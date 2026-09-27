"""The ingest flow around the agent: trials that leave the registry and data/raw alone, a new edition compared with the
plan's previous one, plans matched on their own names, filing dates and order, guarded downloads, the finder's gates,
the agent loop's handling of bad or cut-off turns, and Bedrock usage kept on every exit. No Bedrock or AWS calls and
no network: sessions, clients, name lookups and downloads are faked, and servers listen on the loopback only."""
import contextlib
import copy
import hashlib
import http.server
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import bedrock  # noqa: E402
import finder  # noqa: E402
import fetch  # noqa: E402
import generated_parser  # noqa: E402
import ingest  # noqa: E402
import parser_agent  # noqa: E402
import registry  # noqa: E402
import sandbox  # noqa: E402
from common import RAW  # noqa: E402
from test_ingest import GOOD, PAGES  # noqa: E402

Session = bedrock.Session

LIVE = registry.PATH
HAVE_DESC = all((RAW / f"desc_{e}.pdf").exists() for e in ("2025-2029", "2026-2030"))
HAVE_GA = all((RAW / f"ga_its_{e}.pdf").exists() for e in ("2025-2034", "2026-2035"))
IDENTITY = {"is_project_list": True, "list_pages": "1-2", "reason": "a project list", "company": "Acme Power", "short_name": "Acme",
            "owner": "Acme Power Company", "aliases": [], "members": [], "state": "SC", "title": "Acme plan", "edition": "2027",
            "published": "2026-05-01", "existing_plan": "", "public": True, "other_companies": []}


class FetchIntegrityTests(unittest.TestCase):
    def test_registered_digest_is_checked_before_a_pdf_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            filing = {"id": "acme-2027", "url": "https://example.org/acme.pdf", "file": "acme.pdf",
                      "sha256": hashlib.sha256(b"%PDF-1.4 expected\n").hexdigest()}
            with mock.patch.object(fetch, "RAW", root), mock.patch.object(finder, "get", return_value=(b"%PDF-1.4 different\n", "application/pdf", filing["url"])), \
                    mock.patch.object(fetch, "get", side_effect=AssertionError("unguarded download")):
                with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
                    fetch.fetch(filing)
                self.assertFalse((root / "acme.pdf").exists())
            (root / "acme.pdf").write_bytes(b"different")
            with mock.patch.object(fetch, "RAW", root), self.assertRaisesRegex(ValueError, "differs from the sha256"):
                fetch.fetch(filing)


_old_unsafe = None


def setUpModule():
    global _old_unsafe
    _old_unsafe = os.environ.get(sandbox.UNISOLATED)
    os.environ[sandbox.UNISOLATED] = "1"  # dry-run tests only


def tearDownModule():
    if _old_unsafe is None:
        os.environ.pop(sandbox.UNISOLATED, None)
    else:
        os.environ[sandbox.UNISOLATED] = _old_unsafe


class ProductionIsolationTests(unittest.TestCase):
    def test_generated_parser_cannot_be_registered_or_built_with_the_unsafe_override(self):
        @contextlib.contextmanager
        def editing():
            yield {"parsers": {}, "filings": [], "plans": {}}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            filing = {"id": "acme-2027", "file": "acme.pdf", "parser": "acme"}
            with mock.patch.object(sandbox, "isolation", return_value=("unshare", [])), \
                    mock.patch.object(registry, "editing", editing), mock.patch.object(ingest, "RAW", root):
                with self.assertRaisesRegex(RuntimeError, "require working bubblewrap"):
                    ingest.register(filing, source, parser=("acme", {"kind": "generated"}))
                self.assertFalse((root / "acme.pdf").exists())
                with self.assertRaisesRegex(RuntimeError, "require working bubblewrap"):
                    generated_parser.parse_filing(filing, {"module": "parsers/acme.py"}, {"name": "Acme"})


class FakeClient:
    """bedrock-runtime's converse, played back from a list of replies (an exception is raised)."""

    class exceptions:
        class AccessDeniedException(Exception):
            pass

        class ValidationException(Exception):
            pass

        class ResourceNotFoundException(Exception):
            pass

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def converse(self, modelId, **kw):
        self.calls.append((modelId, copy.deepcopy(kw)))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def reply(*uses, stop=None):
    """A Converse response calling these (name, input) tools, or saying something when there are none."""
    content = [{"toolUse": {"toolUseId": f"u{i}-{n}", "name": n, "input": a}} for i, (n, a) in enumerate(uses)] or [{"text": "hmm"}]
    return {"output": {"message": {"role": "assistant", "content": content}}, "stopReason": stop or ("tool_use" if uses else "end_turn"),
            "usage": {"inputTokens": 100, "outputTokens": 10, "cacheReadInputTokens": 0, "cacheWriteInputTokens": 0}}


def session(replies, models=("m1", "m2")):
    """A real bedrock.Session over a FakeClient."""
    s = Session.__new__(Session)
    s.models, s.model, s.client = list(models), models[0], FakeClient(replies)
    s.usage = {"inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0, "cacheWriteInputTokens": 0, "calls": 0}
    return s


@contextlib.contextmanager
def scratch_repo(*raw_files):
    """A copy of data/filings.json, a data/raw holding copies of raw_files, and a work directory, in a temporary
    directory the ingest writes to instead of the repo's."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "raw").mkdir()
        for name in raw_files:
            shutil.copy(RAW / name, t / "raw" / name)
        shutil.copy(LIVE, t / "filings.json")
        with mock.patch.object(registry, "PATH", t / "filings.json"), mock.patch.object(registry, "LOCK", t / "registry.lock"), \
                mock.patch.object(registry, "PARSER_DIR", t / "parsers"), mock.patch.object(ingest, "RAW", t / "raw"), \
                mock.patch.object(ingest, "WORK", t / "work"), mock.patch.object(ingest, "log", lambda *a: None):
            yield t


def new_edition(t):
    """DESC 2026-2030's PDF with a comment appended: the same text, a new file."""
    p = t / "desc_new.pdf"
    p.write_bytes((RAW / "desc_2026-2030.pdf").read_bytes() + b"\n%new edition\n")
    return p


class Server:
    """A loopback HTTP server that records the requests it gets."""

    def __init__(self, body=b"%PDF-1.4 internal only\n"):
        hits = self.hits = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append((self.path, self.headers.get("Host")))
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@unittest.skipUnless(HAVE_DESC, "needs data/raw/desc_2025-2029.pdf and desc_2026-2030.pdf (python3 pipeline/fetch.py)")
class TrialTests(unittest.TestCase):
    ARGS = ["--edition", "2027-2031", "--date", "2027-04-01", "--no-build"]

    def test_a_trial_leaves_the_registry_and_data_raw_alone(self):
        live = LIVE.read_bytes()
        with scratch_repo("desc_2025-2029.pdf", "desc_2026-2030.pdf") as t:
            seen, real = [], ingest.outline

            def spy(reg, f):
                seen.append((registry.PATH.read_bytes(), sorted(p.name for p in (t / "raw").iterdir())))
                return real(reg, f)
            with mock.patch.object(ingest, "outline", spy):
                run = ingest.main([str(new_edition(t)), "--dry-run", *self.ARGS])
            self.assertEqual(run["filing"]["parser"], "desc")
            # the registry and data/raw were the same while each parser was tried as after
            self.assertTrue(seen)
            self.assertTrue(all(reg == (t / "filings.json").read_bytes() == live and raw == ["desc_2025-2029.pdf", "desc_2026-2030.pdf"]
                                for reg, raw in seen))

    def test_a_new_edition_is_registered_with_its_sha256(self):
        live = LIVE.read_bytes()
        with scratch_repo("desc_2025-2029.pdf", "desc_2026-2030.pdf") as t:
            pdf = new_edition(t)
            url = "https://www.scrtp.com/assets/pdfs/home/2027-2031-2million-and-above-project-descriptions.pdf"
            with mock.patch.object(finder, "get", lambda u, limit, allow=None: (pdf.read_bytes(), "application/pdf", u)):
                ingest.main([str(pdf), "--url", url, *self.ARGS])
            reg = registry.load_registry()
            f = registry.current(reg)["desc"]
            self.assertEqual((f["id"], f["url"], f["file"]), ("desc-2027-2031", url, "desc_2027-2031.pdf"))
            self.assertEqual(f["sha256"], ingest.sha(pdf.read_bytes()))
            self.assertEqual((t / "raw" / "desc_2027-2031.pdf").read_bytes(), pdf.read_bytes())
            self.assertEqual(registry.render(reg), registry.PATH.read_text())
        self.assertEqual(LIVE.read_bytes(), live)

    def test_a_different_file_already_in_data_raw_is_never_overwritten(self):
        with scratch_repo("desc_2025-2029.pdf", "desc_2026-2030.pdf") as t:
            (t / "raw" / "desc_2027-2031.pdf").write_bytes(b"%PDF-1.4 left from an earlier run\n")
            pdf = new_edition(t)
            with mock.patch.object(finder, "get", lambda u, limit, allow=None: (pdf.read_bytes(), "application/pdf", u)):
                with self.assertRaisesRegex(SystemExit, "already holds a different PDF"):
                    ingest.main([str(pdf), "--url", "https://example.org/x.pdf", *self.ARGS])
            self.assertEqual((t / "raw" / "desc_2027-2031.pdf").read_bytes(), b"%PDF-1.4 left from an earlier run\n")
            self.assertEqual(registry.PATH.read_bytes(), LIVE.read_bytes())
            # and the registration step itself refuses too
            with self.assertRaisesRegex(SystemExit, "holds a different PDF"):
                ingest.register({"id": "x", "file": "desc_2027-2031.pdf"}, pdf)

    def test_a_local_file_must_be_what_its_url_serves(self):
        with scratch_repo("desc_2025-2029.pdf", "desc_2026-2030.pdf") as t:
            pdf = new_edition(t)
            other = (RAW / "desc_2025-2029.pdf").read_bytes()
            with mock.patch.object(finder, "get", lambda u, limit, allow=None: (other, "application/pdf", u)):
                with self.assertRaisesRegex(SystemExit, "serves a different file"):
                    ingest.main([str(pdf), "--url", "https://example.org/x.pdf", *self.ARGS])
            # an address that can't be reached is recorded, with the sha256 standing for the file

            def down(u, limit, allow=None):
                raise OSError("network unreachable")
            with mock.patch.object(finder, "get", down):
                ingest.main([str(pdf), "--url", "https://example.org/x.pdf", *self.ARGS])
            self.assertEqual(registry.current()["desc"]["sha256"], ingest.sha(pdf.read_bytes()))

    def test_skip_plan_is_for_dry_runs_only(self):
        with self.assertRaisesRegex(SystemExit, "--dry-run"):
            ingest.main([str(RAW / "desc_2026-2030.pdf"), "--skip-plan", "desc", "--url", "https://example.org/x.pdf"])


class CompareTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_DESC and HAVE_GA, "needs data/raw (python3 pipeline/fetch.py)")
    def test_consecutive_editions_of_a_list_look_alike(self):
        reg = registry.load_registry()
        by_id = {f["id"]: f for f in registry.filings(reg)}
        for new, old in (("desc-2026-2030", "desc-2025-2029"), ("ga-2026-2035", "ga-2025-2034")):
            a, b = ingest.outline(reg, by_id[new]), ingest.outline(reg, by_id[old])
            self.assertEqual(ingest.shape_problems(a, b, old), [], new)

    @unittest.skipUnless(HAVE_DESC, "needs data/raw/desc_2026-2030.pdf")
    def test_a_renamed_label_that_breaks_the_builtin_parser_is_caught(self):
        import parse_desc
        reg = registry.load_registry()
        f = next(f for f in registry.filings(reg) if f["id"] == "desc-2026-2030")
        base = ingest.outline(reg, f)
        real = parse_desc.pdf_pages
        with mock.patch.object(parse_desc, "pdf_pages", lambda p: [x.replace("Project ID", "Project Number") for x in real(p)]):
            recs = parse_desc.parse(f)
        drift = ingest.shape([(r["name"], r["project_id"], bool(r["isd"])) for r in recs])
        self.assertEqual((drift["records"], drift["dated"], drift["withId"]), (54, 54, 0))
        problems = " ".join(ingest.shape_problems(drift, base, f["id"]))
        self.assertIn("0 of 54 records have a project ID", problems)
        self.assertIn("names run to", problems)

    def test_another_companys_list_is_not_a_new_edition(self):
        ga = {"records": 255, "withId": 255, "dated": 255, "nameMedian": 43}
        other = {"records": 23, "withId": 0, "dated": 17, "nameMedian": 36}
        self.assertIn("23 records against 255", " ".join(ingest.shape_problems(other, ga, "ga-2026-2035")))
        self.assertEqual(ingest.shape_problems(other), [])
        self.assertEqual(ingest.shape_problems({"records": 0, "withId": 0, "dated": 0, "nameMedian": 0}), ["no records"])

    @unittest.skipUnless(HAVE_DESC, "needs data/raw/desc_2026-2030.pdf")
    def test_a_parser_that_fails_the_comparison_goes_to_the_agent_not_the_registry(self):
        base = {"records": 54, "withId": 54, "dated": 54, "nameMedian": 47}
        drift = {"records": 54, "withId": 0, "dated": 54, "nameMedian": 502}
        asked = {}

        def write_new_parser(*a, **kw):
            asked.update(kw)
            return None
        with scratch_repo("desc_2025-2029.pdf", "desc_2026-2030.pdf") as t:
            pdf = new_edition(t)
            with mock.patch.object(ingest, "outline", lambda reg, f: base if f["id"] == "desc-2026-2030" else drift), \
                    mock.patch.object(ingest, "write_new_parser", write_new_parser), mock.patch.object(bedrock, "Session", lambda *a: session([])), \
                    mock.patch.object(finder, "get", lambda u, limit, allow=None: (pdf.read_bytes(), "application/pdf", u)):
                ingest.main([str(pdf), "--url", "https://example.org/x.pdf", *TrialTests.ARGS])
            self.assertEqual(registry.PATH.read_bytes(), LIVE.read_bytes())
            self.assertFalse((t / "raw" / "desc_2027-2031.pdf").exists())
        self.assertIsNone(asked["repair"])   # the built-in isn't repaired; the agent writes a parser for the plan
        drifted = [{"name": "x" * 500, "in_service": "6/1/2027", "page": 1}] * 54
        self.assertIn("project ID", " ".join(asked["compare"](drifted)))


class PlanMatchTests(unittest.TestCase):
    reg = registry.load_registry()

    def plan(self, **kw):
        return ingest.plan_of(dict(IDENTITY, **kw), self.reg, set())

    def test_a_plan_is_matched_on_its_own_names_in_its_own_state(self):
        self.assertEqual(self.plan(company="Georgia Integrated Transmission System", owner="", short_name="Georgia ITS", state="GA"), "ga")
        self.assertIsNone(self.plan())
        with self.assertRaisesRegex(SystemExit, "places its projects in 'AL'"):
            self.plan(company="Georgia ITS", state="AL")
        with self.assertRaisesRegex(SystemExit, "says it is plan 'desc'"):
            self.plan(company="Georgia ITS", state="GA", existing_plan="desc")

    def test_a_member_utilitys_or_parents_own_list_is_not_the_joint_plans(self):
        for company in ("Georgia Transmission Corp.", "MEAG Power", "Southern Company"):
            with self.assertRaisesRegex(SystemExit, "member or parent of Georgia ITS"):
                self.plan(company=company, owner=company, short_name=company, state="GA")
        # the agent's say-so alone doesn't file a list under a plan
        with self.assertRaisesRegex(SystemExit, "none of its names"):
            self.plan(company="Acme Power", existing_plan="ga", state="GA")


class DateTests(unittest.TestCase):
    def args(self, **kw):
        import argparse
        return argparse.Namespace(**{"date": None, "dry_run": False, **kw})

    def test_a_filing_is_never_dated_today_silently(self):
        with self.assertRaisesRegex(SystemExit, "pass --date"):
            ingest.filing_date({}, None, self.args())
        with mock.patch.object(ingest, "log", lambda *a: None):
            self.assertEqual(ingest.filing_date({}, None, self.args(dry_run=True))[1], "date ingested (dry run)")
        self.assertEqual(ingest.filing_date({"CreationDate": "2026-04-28T10:00:00"}, None, self.args()), ("2026-04-28", "PDF creation date"))
        self.assertEqual(ingest.filing_date({}, "March 3, 2026", self.args()), ("2026-03-03", "date printed in the document"))

    def test_date_must_be_iso(self):
        self.assertEqual(ingest.iso_date("2026-03-05"), "2026-03-05")
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            ingest.main(["x.pdf", "--date", "3/5/2026"])
        self.assertIn("invalid iso_date value", err.getvalue())


def zipped(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


class DownloadTests(unittest.TestCase):
    def test_a_download_goes_only_to_public_addresses(self):
        srv = Server()
        try:
            with self.assertRaisesRegex(SystemExit, "not a public internet address"):
                ingest.obtain(f"http://127.0.0.1:{srv.port}/plan.pdf")
            self.assertEqual(srv.hits, [])
        finally:
            srv.close()

    def test_a_proposed_address_is_never_read_as_a_local_file(self):
        with self.assertRaisesRegex(SystemExit, "not a web address"):
            ingest.obtain("/etc/passwd", local=False)

    def test_zip_members_are_chosen_and_bounded(self):
        pdf = b"%PDF-1.4 " + b"x" * 5000
        self.assertEqual(finder.pdf_from(zipped({"a/Plan.pdf": pdf, "readme.txt": b"hi"})), (pdf, "a/Plan.pdf"))
        cases = {"holding no PDF": (zipped({"readme.txt": b"hi"}), None), "several PDFs": (zipped({"a.pdf": pdf, "b.pdf": pdf}), None),
                 "holds no PDF named 'a/plan.pdf'": (zipped({"a/Plan.pdf": pdf}), "a/plan.pdf"),
                 "is not a PDF": (zipped({"x.pdf": b"<html>login</html>"}), None), "not a readable zip": (b"PK\x03\x04junk", None)}
        for msg, (data, member) in cases.items():
            with self.assertRaisesRegex(ValueError, msg):
                finder.pdf_from(data, member)
        with self.assertRaisesRegex(ValueError, "unpacks to"):
            finder.pdf_from(zipped({"big.pdf": pdf}), limit=1000)
        # the command line's wording for choosing a member
        with tempfile.NamedTemporaryFile(suffix=".zip") as f:
            f.write(zipped({"a.pdf": pdf, "b.pdf": pdf}))
            f.flush()
            with self.assertRaisesRegex(SystemExit, "pick one with --zip-member"):
                ingest.obtain(f.name)

    def test_a_missing_or_changed_registered_pdf_is_not_checked_as_empty(self):
        f = {"id": "desc-x", "file": "desc_x.pdf", "url": "https://example.org/x.pdf"}
        with scratch_repo() as t:
            import fetch

            def unreachable(f):
                raise OSError("offline")
            with mock.patch.object(fetch, "fetch", unreachable):
                with self.assertRaisesRegex(SystemExit, "could not be downloaded"):
                    ingest.Texts(None).of(f)
            (t / "raw" / "desc_x.pdf").write_bytes(b"%PDF-1.4 other\n")
            with self.assertRaisesRegex(SystemExit, "not the PDF desc-x was registered from"):
                ingest.raw_file(dict(f, sha256="0" * 64))


class FinderGuardTests(unittest.TestCase):
    def test_a_name_that_rebinds_to_the_loopback_is_not_connected_to(self):
        srv, real, answers = Server(), socket.getaddrinfo, []

        def lookup(host, port, *a, **kw):
            if host != "rebind.example":
                return real(host, port, *a, **kw)
            answers.append(host)
            ip = "93.184.216.34" if len(answers) == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]
        try:
            with mock.patch.object(socket, "getaddrinfo", lookup), self.assertRaisesRegex(Exception, "not a public internet address"):
                finder.get(f"http://rebind.example:{srv.port}/x.pdf", 1000)
            self.assertEqual(srv.hits, [])
            self.assertEqual(len(answers), 2)   # the check, then the connection, which checks what it connects to
        finally:
            srv.close()

    def test_the_connection_goes_to_the_vetted_address_under_the_hosts_name(self):
        srv = Server()
        try:
            with mock.patch.object(finder, "resolve", lambda host, port: ("127.0.0.1", srv.port)):
                data, ctype, final = finder.get(f"http://plans.example:{srv.port}/x.pdf", 1000)
            self.assertEqual((data[:5], srv.hits), (b"%PDF-", [("/x.pdf", f"plans.example:{srv.port}")]))
        finally:
            srv.close()

    def test_a_typed_address_may_not_redirect_to_a_query(self):
        r = finder._Redirects(allow=finder.typed_ok)
        req = finder.urllib.request.Request("https://utility.example/plans")
        with mock.patch.object(finder, "public", lambda u: True):
            with self.assertRaisesRegex(Exception, "not a plain page address"):
                r.redirect_request(req, None, 302, "Found", {}, "https://search.example/?q=utility")
            self.assertIsNotNone(finder._Redirects().redirect_request(req, None, 302, "Found", {}, "https://cdn.example/a.pdf?v=2"))

    def find(self, replies, **patches):
        s = session(replies)
        with contextlib.ExitStack() as stack:
            for k, v in patches.items():
                stack.enter_context(mock.patch.object(finder, k, v))
            try:
                return s, finder.find("Utility", s, log=lambda *a: None, max_turns=len(replies))
            except SystemExit as e:
                return s, e

    @staticmethod
    def results(s, call):
        """The tool results the model got back before its call-th turn."""
        return [b["toolResult"]["content"][0]["text"] for b in s.client.calls[call][1]["messages"][-1]["content"] if "toolResult" in b]

    def test_only_a_checked_pdf_may_be_proposed_and_bad_calls_dont_end_the_search(self):
        doc = "https://utility.example/plan.pdf"

        def check_pdf(url, zip_member=None, allow=None, checked=None):
            checked.setdefault(url, set()).add(None)
            return "a PDF"
        s, found = self.find([reply(("propose", {"url": doc, "why": "its list"})),
                              reply(("fetch", {}), ("propose", {"url": doc}), ("search", {"q": "x"})),
                              reply(("check_pdf", {"url": doc}), stop="max_tokens"),
                              reply(("check_pdf", {"url": doc})),
                              reply(("propose", {"url": doc, "why": "its list"}))],
                             check_pdf=check_pdf, typed_ok=lambda u: True)
        self.assertEqual(found["url"], doc)
        self.assertIn("check it first", self.results(s, 1)[0])
        self.assertEqual([r.split(":")[0] for r in self.results(s, 2)], ["bad tool call"] * 3)
        self.assertIn("cut off", self.results(s, 3)[0])
        # one moving cache point, on the newest turn
        self.assertEqual(sum(1 for m in s.client.calls[-1][1]["messages"] for b in m["content"] if "cachePoint" in b), 1)

    def test_a_tool_that_fails_is_reported_and_giving_up_ends_the_search(self):
        def broken(url, zip_member=None, allow=None, checked=None):
            raise zipfile.BadZipFile("File is not a zip file")
        s, out = self.find([reply(("check_pdf", {"url": "https://utility.example/a.zip"})), reply(("give_up", {"why": "nothing public"}))],
                           check_pdf=broken, typed_ok=lambda u: True)
        self.assertIn("BadZipFile", self.results(s, 1)[0])
        self.assertIsInstance(out, SystemExit)

    def test_a_pages_title_and_links_are_bounded(self):
        page = f"<title>{'t' * 100000}</title>".encode() + b"".join(f'<a href="https://u.example/{"p" * 5000}{i}">x</a>'.encode() for i in range(200))
        with mock.patch.object(finder, "get", lambda u, limit, allow=None: (page, "text/html", u)):
            out = finder.fetch("https://u.example/")
        self.assertLess(len(out), finder.MAX_RESULT + 200)


class BedrockTests(unittest.TestCase):
    tool = {"name": "answer", "description": "d", "inputSchema": {"json": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}}

    def test_a_structured_answer_is_asked_for_without_forcing_the_tool_and_retried(self):
        s = session([reply(), reply(("answer", {"ok": "false"})), reply(("answer", {"ok": False}))])
        out = s.force_tool("sys", "question", self.tool, check=lambda o: parser_agent.input_problems(self.tool, o))
        self.assertEqual(out, {"ok": False})
        self.assertTrue(all("toolChoice" not in kw["toolConfig"] for _, kw in s.client.calls))
        self.assertIn("Answer by calling the answer tool", s.client.calls[0][1]["messages"][0]["content"][0]["text"])
        self.assertIn("ok must be a boolean", s.client.calls[2][1]["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"])
        with self.assertRaises(RuntimeError):
            session([reply()] * 3).force_tool("sys", "question", self.tool)

    def test_a_model_this_account_cant_use_falls_back_to_the_next(self):
        ex = FakeClient.exceptions
        s = session([ex.ValidationException('tool_choice: type "tool" and "any" are not supported for this model.'), reply()])
        s.converse("sys", [])
        self.assertEqual([m for m, _ in s.client.calls], ["m1", "m2"])
        s = session([ex.ResourceNotFoundException("The provided model identifier is invalid."), reply()])
        s.converse("sys", [])
        self.assertEqual(s.model, "m2")
        # an error about the request itself is not a reason to switch
        with self.assertRaises(ex.ValidationException):
            session([ex.ValidationException("messages.1: roles must alternate")]).converse("sys", [])

    def test_identify_rejects_values_of_the_wrong_type(self):
        bad = dict(IDENTITY, public="false", aliases="Acme Electric")
        s = session([reply(("identify", bad)), reply(("identify", IDENTITY))])
        out = parser_agent.identify(PAGES, {}, {}, s, log=lambda *a: None)
        self.assertIs(out["public"], True)
        text = s.client.calls[1][1]["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"]
        self.assertIn("public must be a boolean", text)
        self.assertIn("aliases must be an array", text)
        with self.assertRaises(RuntimeError):
            parser_agent.identify(PAGES, {}, {}, session([reply(("identify", bad))] * 3), log=lambda *a: None)


class AgentLoopTests(unittest.TestCase):
    """The loop's own handling of turns; the checks on a parser's output are generated_parser's (faked here)."""
    identity = {"title": "Acme plan", "edition": "2027", "reason": "a project list"}

    def setUp(self):
        self.runs = []

        def evaluate(code, pages, count_pattern=None, *a, **kw):
            self.runs.append(code)
            return {"records": 3, "with_in_service_date": 3, "accepted": True, "blocking": [], "sample": []}, [{"name": "Foo", "page": 1}] * 3, []
        patcher = mock.patch.object(parser_agent.generated_parser, "evaluate", evaluate)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, replies, **kw):
        s = session(replies)
        return s, parser_agent.write_parser(PAGES, self.identity, "Acme", s, lambda sig: [], log=lambda *a: None, max_turns=len(replies), **kw)

    @staticmethod
    def results(s, call):
        return [b["toolResult"]["content"][0]["text"] for b in s.client.calls[call][1]["messages"][-1]["content"] if "toolResult" in b]

    def test_bad_input_and_cut_off_turns_are_answered_not_run(self):
        good = {"code": GOOD, "signature": ["Acme Power Company"], "count_pattern": r"Project \S+", "notes": "blocks"}
        s, out = self.write([reply(("submit", dict(good, count_pattern=None, no_count_reason=7)), ("submit", dict(good, notes=["n"]))),
                             reply(("run_parser", {"code": GOOD[:40]}), stop="max_tokens"),
                             reply(("submit", good))])
        self.assertEqual(out["report"]["records"], 3)
        self.assertEqual(self.results(s, 1), ["bad tool call: no_count_reason must be a string, got int", "bad tool call: notes must be a string, got list"])
        self.assertIn("max_tokens", self.results(s, 2)[0])
        self.assertEqual(self.runs, [GOOD])   # only the whole submission ran
        with self.assertRaisesRegex(RuntimeError, "content_filtered"):
            self.write([reply(("run_parser", {"code": GOOD}), stop="content_filtered")])

    def test_output_the_checks_choke_on_is_blamed_on_the_parser(self):
        def choke(*a, **kw):
            raise TypeError("unhashable type: 'list'")
        s = session([reply(("run_parser", {"code": GOOD}))] * 2)
        with mock.patch.object(parser_agent.generated_parser, "evaluate", choke), self.assertRaises(RuntimeError):
            parser_agent.write_parser(PAGES, self.identity, "Acme", s, lambda sig: [], log=lambda *a: None, max_turns=2)
        self.assertIn("wrong type in your parser's output", self.results(s, 1)[0])

    def test_the_previous_editions_comparison_blocks_a_submission(self):
        good = {"code": GOOD, "signature": ["Acme Power Company"], "count_pattern": r"Project \S+", "notes": "blocks"}
        s = session([reply(("submit", good))] * 2)
        with self.assertRaises(RuntimeError):
            parser_agent.write_parser(PAGES, self.identity, "Acme", s, lambda sig: [], log=lambda *a: None, max_turns=2,
                                      compare=lambda raw: [f"{len(raw)} records against 255 in ga-2026-2035"])
        self.assertIn("against the previous edition: 3 records against 255", self.results(s, 1)[0])

    def test_a_backtracking_search_is_stopped(self):
        with mock.patch.object(parser_agent, "SEARCH_TIMEOUT_S", 1):
            out = parser_agent.search_pages(["a" * 40 + "b"], r"(a+)+$")
        self.assertIn("backtracks", out)
        self.assertTrue(parser_agent.search_pages(PAGES, r"^Project").startswith("3 matching lines"))

    def test_history_is_only_appended_to_and_a_long_run_restarts_from_the_latest_draft(self):
        drafts = [GOOD.replace("out = []", f"out = []  # draft {i}") for i in range(5)]
        s = session([reply(("run_parser", {"code": d})) for d in drafts] + [reply(("read_pages", {"first": 1, "last": 1}))])
        with mock.patch.object(parser_agent, "RESTART_AT_CHARS", 4000), self.assertRaises(RuntimeError):
            parser_agent.write_parser(PAGES, self.identity, "Acme", s, lambda sig: [], log=lambda *a: None, max_turns=6)
        strip = lambda ms: [{**m, "content": [b for b in m["content"] if "cachePoint" not in b]} for m in ms]
        sent = [strip(kw["messages"]) for _, kw in s.client.calls]
        restarts = [i for i in range(1, len(sent)) if sent[i][:len(sent[i - 1])] != sent[i - 1]]
        self.assertTrue(restarts)
        for i in range(1, len(sent)):
            if i not in restarts:
                self.assertEqual(sent[i][:len(sent[i - 1])], sent[i - 1], f"request {i} edited earlier turns")
        first = sent[restarts[0]][0]["content"][0]["text"]
        self.assertEqual(len(sent[restarts[0]]), 1)
        self.assertIn(f"# draft {restarts[0] - 1}", first)


@unittest.skipUnless(HAVE_DESC, "needs data/raw/desc_2026-2030.pdf")
class UsageTests(unittest.TestCase):
    def test_usage_is_kept_when_the_agent_stops_the_run(self):
        with scratch_repo() as t:
            s = session([reply(("identify", dict(IDENTITY, is_project_list=False, reason="a rate case")))])
            with mock.patch.object(bedrock, "Session", lambda *a: s), self.assertRaisesRegex(SystemExit, "a rate case"):
                ingest.main([str(RAW / "desc_2026-2030.pdf"), "--dry-run", "--skip-plan", "desc"])
            line = json.loads((t / "work" / "usage.jsonl").read_text().splitlines()[-1])
            self.assertEqual((line["usage"]["calls"], line["dryRun"]), (1, True))
            self.assertIn("a rate case", line["exit"])
            run = json.loads(next((t / "work").glob("*/run.json")).read_text())
            self.assertEqual(run["usage"]["calls"], 1)

    def test_usage_is_kept_when_the_search_gives_up(self):
        with scratch_repo() as t:
            s = session([reply(("give_up", {"why": "nothing public"}))])
            with mock.patch.object(bedrock, "Session", lambda *a: s), self.assertRaisesRegex(SystemExit, "nothing public"):
                ingest.main(["--find", "Acme Power"])
            line = json.loads((t / "work" / "usage.jsonl").read_text())
            self.assertEqual((line["usage"]["calls"], line["source"]), (1, "--find Acme Power"))


if __name__ == "__main__":
    unittest.main()
