import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import generated_parser  # noqa: E402
import parser_agent  # noqa: E402
import registry  # noqa: E402
import sandbox  # noqa: E402
from common import RAW, pdf_pages, read_date  # noqa: E402

PAGES = [
    "Acme Power Company\nPlanned Transmission Projects\n\nProject A-1  Foo 115 kV Line Rebuild\n  In service: June 2027\n  Cost: $4,200,000\n",
    "Project A-2  Bar Substation Expansion\n  In service: 12/1/2028\n  Start: 2026\n\nProject A-3  Baz Tap\n  In service: 2029\n",
]
GOOD = r'''
import re
def parse(pages):
    out = []
    for i, p in enumerate(pages, 1):
        for m in re.finditer(r"Project (\S+)\s+(.+)\n\s+In service: (.+)\n(?:\s+Cost: (\S+)\n)?(?:\s+Start: (\S+)\n)?", p):
            r = {"project_id": m.group(1), "name": m.group(2).strip(), "in_service": m.group(3).strip(), "page": i, "item": m.group(1)}
            if m.group(4):
                r["cost_raw"] = m.group(4)
                r["cost_total"] = int(m.group(4).lstrip("$").replace(",", ""))
            if m.group(5):
                r["start"] = m.group(5)
            out.append(r)
    return out
'''
FILING = {"id": "acme-2027", "plan": "acme", "title": "Acme planned projects", "url": "https://example.org/acme.pdf", "parser": "acme", "edition": "2027"}
PLAN = {"name": "Acme", "owner": "Acme Power Company", "state": "SC"}


_old_unsafe = None


def setUpModule():
    global _old_unsafe
    _old_unsafe = os.environ.get(sandbox.UNISOLATED)
    os.environ[sandbox.UNISOLATED] = "1"  # draft parsers in tests; registration is checked separately


def tearDownModule():
    if _old_unsafe is None:
        os.environ.pop(sandbox.UNISOLATED, None)
    else:
        os.environ[sandbox.UNISOLATED] = _old_unsafe


class SandboxTests(unittest.TestCase):
    def test_weak_isolation_requires_explicit_dry_run_override(self):
        with mock.patch.object(sandbox, "isolation", return_value=("unshare", [])):
            with mock.patch.dict(os.environ, {sandbox.UNISOLATED: ""}):
                self.assertIn("working bubblewrap", sandbox.run(GOOD, PAGES)[1])
            with mock.patch.dict(os.environ, {sandbox.UNISOLATED: "1"}):
                with self.assertRaisesRegex(RuntimeError, "require working bubblewrap"):
                    sandbox.require_bubblewrap()

    def test_a_parser_runs_and_returns_its_records(self):
        recs, err = sandbox.run(GOOD, PAGES)
        self.assertIsNone(err)
        self.assertEqual([r["project_id"] for r in recs], ["A-1", "A-2", "A-3"])

    def test_code_that_could_escape_is_rejected_before_running(self):
        for code in ("import os\ndef parse(pages): return os.listdir('/')",
                     "import subprocess\ndef parse(pages): return []",
                     "def parse(pages): return open('/etc/passwd').read()",
                     "def parse(pages): return ().__class__.__bases__[0].__subclasses__()",
                     "def parse(pages):\n    g = (x for x in [1])\n    return g.gi_frame.f_back",
                     "def parse(pages): return getattr(pages, 'pop')()",
                     "def parse(pages): return eval('1')",
                     # a format field walks attributes from inside a string
                     "def parse(pages): return ['{0.__globals__[__builtins__]}'.format(parse)]",
                     "def parse(pages): return ['{p.__class__}'.format_map({'p': pages})]",
                     "from . import x\ndef parse(pages): return []",
                     "x = 1"):
            recs, err = sandbox.run(code, PAGES)
            self.assertIsNone(recs, code)
            self.assertIn("rejected", err, code)

    def test_runaway_and_failing_code_is_stopped(self):
        self.assertIn("longer than", sandbox.run("def parse(pages):\n    while True: pass", PAGES, timeout=3)[1])
        self.assertIn("raised an error", sandbox.run("def parse(pages): return 1/0", PAGES)[1])

    def test_no_network_or_home_directory_when_isolated(self):
        mode, _ = sandbox.isolation()
        if mode != "bubblewrap":
            self.skipTest("bubblewrap not available")
        # the checks would reject these imports, so run the isolation layer directly
        import subprocess
        _, cmd = sandbox.isolation()
        probe = ("import socket, os\ntry:\n    socket.create_connection(('1.1.1.1', 80), timeout=2); print('net')\nexcept OSError: print('no-net')\n"
                 "print('home' if os.path.exists(os.path.expanduser('~nobody')) or os.path.exists('/home') else 'no-home')")
        out = subprocess.run(cmd + ["-I", "-c", probe], capture_output=True, text=True, env={}).stdout.split()
        self.assertEqual(out, ["no-net", "no-home"])


class DateTests(unittest.TestCase):
    def test_partial_dates_stand_for_their_period_and_say_so(self):
        self.assertEqual(read_date("6/1/2027"), (read_date("2027-06-01")[0], None))
        self.assertEqual(read_date("June 2027")[0].isoformat(), "2027-06-30")
        self.assertEqual(read_date("June 2027", end=False)[0].isoformat(), "2027-06-01")
        self.assertEqual(read_date("Q2 2028")[0].isoformat(), "2028-06-30")
        self.assertEqual(read_date("Summer 2027")[0].isoformat(), "2027-08-31")
        d, issue = read_date("2029")
        self.assertEqual(d.isoformat(), "2029-12-31")
        self.assertIn("a year", issue)
        self.assertEqual(read_date("12/1/2026; 6/1/2028")[0].isoformat(), "2028-06-01")
        self.assertIsNone(read_date("TBD")[0])

    def test_of_several_dates_in_any_form_the_latest_ends_and_the_earliest_starts(self):
        for s, last, first in (("Phase 1: 2027; Phase 2: 2029", "2029-12-31", "2027-01-01"),
                               ("June 2027 (Phase 1), December 2029 (Phase 2)", "2029-12-31", "2027-06-01"),
                               ("delayed from 2025 to 2027", "2027-12-31", "2025-01-01"),
                               ("6/1/2027 (Phase 1), 2029 (Phase 2)", "2029-12-31", "2027-06-01"),
                               ("June 1, 2027 and December 1, 2029", "2029-12-01", "2027-06-01")):
            d, issue = read_date(s)
            self.assertEqual((d.isoformat(), read_date(s, end=False)[0].isoformat()), (last, first), s)
            self.assertIn("several dates", issue, s)
        # the month in a full date and the year in a quarter are not dates of their own
        self.assertEqual(read_date("6/1/2027"), (read_date("June 1, 2027")[0], None))
        self.assertNotIn("several", read_date("Q2 2027")[1])

    def test_words_that_start_like_months_are_not_months(self):
        self.assertIn("a year", read_date("Marion 2027")[1])


class CheckTests(unittest.TestCase):
    def evaluate(self, code, pattern=r"Project \S+"):
        return generated_parser.evaluate(code, PAGES, pattern)

    def test_a_faithful_parser_is_accepted(self):
        rep, raw, notes = self.evaluate(GOOD)
        self.assertTrue(rep["accepted"], rep)
        self.assertEqual(rep["records"], 3)

    def test_values_not_printed_on_the_page_are_rejected(self):
        rep, _, _ = self.evaluate(GOOD.replace('"name": m.group(2).strip()', '"name": m.group(2).strip() + " Plantation"'))
        self.assertFalse(rep["accepted"])
        self.assertIn("plantation", " ".join(rep["blocking"]))

    def test_a_cost_must_match_the_printed_amount(self):
        rep, _, _ = self.evaluate(GOOD.replace('int(m.group(4).lstrip("$").replace(",", ""))', "4300000"))
        self.assertFalse(rep["accepted"])
        self.assertIn("cost_total", " ".join(rep["blocking"]))

    def test_the_record_count_must_match_the_documents_own_markers(self):
        rep, _, _ = self.evaluate(GOOD.replace("out.append(r)", "out.append(r) if r['project_id'] != 'A-3' else None"))
        self.assertFalse(rep["accepted"])
        self.assertIn("count_pattern matches 3", " ".join(rep["blocking"]))

    def test_unknown_fields_and_missing_dates_are_rejected(self):
        rep, _, _ = self.evaluate(GOOD.replace('"item": m.group(1)}', '"item": m.group(1), "lat": 33.0}'))
        self.assertIn("unknown field 'lat'", " ".join(rep["blocking"]))
        rep, raw, notes = self.evaluate(GOOD.replace('"in_service": m.group(3).strip(), ', ""))
        self.assertIn("no record has a readable in-service date", " ".join(rep["blocking"]))
        # each entry prints its date, so leaving it out is the parser's mistake, not a fact about the filing
        self.assertIn("no in_service, but its entry prints one: 'In service: June 2027'", " ".join(rep["blocking"]))
        # a project whose entry prints no date is kept, with a note
        problems, notes = generated_parser.check([dict(raw[0], page=1)], ["Project A-1  Foo 115 kV Line Rebuild\n  Cost: $4,200,000\n"])
        self.assertEqual(problems, [])
        self.assertIn("no in-service date", notes[0][0]["msg"])

    def test_records_normalize_to_the_pipeline_shape(self):
        rep, raw, notes = self.evaluate(GOOD)
        recs = generated_parser.normalize(raw, notes, FILING, {}, PLAN)
        a1, a2, a3 = recs
        self.assertEqual((a1["uid"], a1["key"], a1["plan"], a1["state"], a1["utility"]), ("ACME:A-1", "A-1", "acme", "SC", "Acme"))
        self.assertEqual((a1["isd"], a1["cost"]["total"]), ("2027-06-30", 4200000))
        self.assertTrue(any("a month" in i["msg"] for i in a1["issues"]))
        self.assertEqual(a2["window"], {"start": "2026-01-01", "end": "2028-12-01", "basis": "Start date in the filing → in-service date"})
        self.assertEqual(a3["window"]["start"], None)
        self.assertEqual(a3["cost"], {"total": None, "by_year": {}, "basis": "not published in the filing"})
        self.assertEqual(a1["source"], {"doc": "Acme planned projects", "url": "https://example.org/acme.pdf", "page": 1, "item": "A-1"})

    def test_a_value_joined_from_the_row_carrying_the_id_is_grounded(self):
        pages = ["Summary\nID     Zone   Sponsor\nA-1    215    GPC\nA-2    219    SAV\n",
                 "Project A-1  Foo Line\n  In service: 2027\n", "Project A-2  Bar Sub\n  In service: 2028\n"]
        rec = {"project_id": "A-1", "name": "Foo Line", "in_service": "2027", "page": 2, "zone": "215", "sponsor": "GPC"}
        self.assertEqual(generated_parser.check([rec], pages)[0], [])
        # the next row's zone belongs to another project
        problems = generated_parser.check([dict(rec, zone="219")], pages)[0]
        self.assertIn("zone", " ".join(problems))

    def test_a_date_printed_twice_is_checked_against_itself(self):
        pages = ["Transmission Projects 2026-2030\nProject Title                        In-service Date\nPurrysburg - McIntosh tie lines      5/1/2026\n"
                 "Indian Field 230 kV Substation      12/1/2026\n",
                 "Purrysburg-McIntosh Tie Lines\nIn-service: December 2026\n", "",
                 "Indian Field 230 kV Substation\nIn-service: December 2026\n", ""]
        tie = {"name": "Purrysburg-McIntosh Tie Lines", "page": 2, "in_service": "5/1/2026",
               "also_printed": [{"field": "in_service", "value": "December 2026", "page": 2}]}
        sub = {"name": "Indian Field 230 kV Substation", "page": 4, "in_service": "12/1/2026",
               "also_printed": [{"field": "in_service", "value": "December 2026", "page": 4}]}
        # the table's date is joined from the row carrying the project's name; printings that disagree are kept as a warning
        problems, notes = generated_parser.check([tie, sub], pages)
        self.assertEqual(problems, [])
        self.assertEqual([n["level"] for n in notes[0]], ["warn"])
        self.assertIn("'5/1/2026' and 'December 2026' (page 2)", notes[0][0]["msg"])
        self.assertEqual(notes[1], [])
        # the less precise printing can't stand in for the more precise one
        vague = dict(sub, in_service="December 2026", also_printed=[{"field": "in_service", "value": "12/1/2026", "page": 1}])
        self.assertIn("less precise", " ".join(generated_parser.check([vague], pages)[0]))
        # ...even when the two disagree
        vague = dict(tie, in_service="December 2026", also_printed=[{"field": "in_service", "value": "5/1/2026", "page": 1}])
        self.assertIn("less precise", " ".join(generated_parser.check([vague], pages)[0]))
        # a second printing must be on the page it names
        stray = dict(sub, also_printed=[{"field": "in_service", "value": "June 2027", "page": 4}])
        self.assertIn("not printed on page 4", " ".join(generated_parser.check([stray], pages)[0]))

    def test_a_reused_id_keeps_records_apart(self):
        raw = [{"project_id": "A 1", "name": "Foo", "in_service": "2027", "page": 1}, {"project_id": "A1", "name": "Bar", "in_service": "2028", "page": 2}]
        recs = generated_parser.normalize(raw, [[], []], FILING, {}, PLAN)
        self.assertEqual([r["uid"] for r in recs], ["ACME:A1:1", "ACME:A1:2"])


class RegistryTests(unittest.TestCase):
    def test_the_registry_file_round_trips_in_its_layout(self):
        self.assertEqual(registry.render(registry.load_registry()), registry.PATH.read_text())

    def test_company_names_match_their_plan(self):
        self.assertEqual(registry.plan_for_company(["Dominion Energy South Carolina, Inc."]), "desc")
        self.assertEqual(registry.plan_for_company(["Georgia ITS"]), "ga")
        self.assertEqual(registry.plan_for_company(["Santee Cooper"]), "santee")
        # a member utility or parent is not the joint plan itself
        self.assertIsNone(registry.plan_for_company(["Georgia Power Company"]))
        self.assertEqual(registry.plan_with_member(["Georgia Power Company"]), "ga")
        self.assertEqual(registry.plan_with_member(["Southern Company"]), "ga")
        self.assertIsNone(registry.plan_with_member(["Georgia Power"], skip_plans={"ga"}))

    def test_every_registered_filing_is_recognized_by_its_own_plans_parsers_only(self):
        # a plan's later layout may get a parser whose signature also fits the plan's earlier filings
        parsers = registry.parsers()
        for f in registry.filings():
            if not (RAW / f["file"]).exists():
                self.skipTest("source PDFs not downloaded (python3 pipeline/fetch.py)")
            hits = registry.signature_matches("\f".join(pdf_pages(RAW / f["file"])))
            self.assertIn(f["parser"], hits, f["id"])
            self.assertEqual({parsers[h]["plan"] for h in hits}, {f["plan"]}, f["id"])

    def test_a_plans_filings_go_by_edition_and_the_newest_edition_is_current(self):
        reg = copy.deepcopy(registry.load_registry())
        # a backfilled older edition, dated today, is neither current nor replayed after the newer ones
        desc = next(f for f in reg["filings"] if f["id"] == "desc-2024-2028")
        reg["filings"].append(dict(desc, id="desc-2023-2027", edition="2023-2027", date="2026-09-27"))
        self.assertEqual([f["id"] for f in registry.filings(reg, plan="desc")], ["desc-2023-2027", "desc-2024-2028", "desc-2025-2029", "desc-2026-2030"])
        self.assertEqual(registry.current(reg)["desc"]["id"], "desc-2026-2030")
        # plans interleave by date, each keeping its own order
        order = [f["id"] for f in registry.filings(reg)]
        self.assertLess(order.index("ga-2026-2035"), order.index("desc-2023-2027"))
        self.assertLess(order.index("desc-2023-2027"), order.index("desc-2026-2030"))


class FakeSession:
    """Plays back scripted model turns; records what the loop sent."""

    def __init__(self, turns):
        self.turns, self.sent, self.model = list(turns), [], "fake"
        self.usage = {"calls": 0}

    def converse(self, system, messages, tools=None, **kw):
        self.sent.append(copy.deepcopy(messages))
        content = [{"toolUse": {"toolUseId": f"t{len(self.sent)}-{i}", "name": n, "input": a}} for i, (n, a) in enumerate(self.turns.pop(0))]
        return {"output": {"message": {"role": "assistant", "content": content or [{"text": "thinking"}]}}, "stopReason": "tool_use" if content else "end_turn"}


class AgentLoopTests(unittest.TestCase):
    identity = {"title": "Acme planned projects", "edition": "2027", "reason": "a project list"}

    def test_the_loop_runs_tools_and_ends_on_an_accepted_submission(self):
        s = FakeSession([
            [("search", {"pattern": r"^Project"})],
            [("run_parser", {"code": GOOD.replace("out.append(r)", "pass"), "count_pattern": r"Project \S+"})],
            [("submit", {"code": GOOD, "signature": ["Acme Power Company"], "notes": "n"})],
            [("submit", {"code": GOOD, "signature": ["Acme Power Company"], "count_pattern": r"Project \S+", "notes": "blocks per project"})],
        ])
        out = parser_agent.write_parser(PAGES, self.identity, "Acme", s, lambda sig: [], log=lambda *a: None)
        self.assertEqual(out["report"]["records"], 3)
        self.assertEqual(out["count_pattern"], r"Project \S+")
        # the model saw the empty run's problem and the count rule before its last try
        results = [b["toolResult"]["content"][0]["text"] for m in s.sent[-1] if m["role"] == "user" for b in m["content"] if "toolResult" in b]
        self.assertIn("no records", results[1])
        self.assertIn("count_pattern", results[2])
        # one moving cache point besides the first message's
        self.assertEqual(sum(1 for m in s.sent[-1][1:] for b in m["content"] if "cachePoint" in b), 1)

    def test_a_scope_for_repeated_project_markers_is_used_by_the_agent(self):
        pages = [PAGES[0], "Other projects\nProject A-1\nProject A-2\nProject A-3\n",
                 "Planned Transmission Projects\n" + PAGES[1]]
        args = {"code": GOOD, "signature": ["Acme Power Company"], "notes": "one entry per project",
                "count_pattern": r"Project A-\d", "count_scope": "Planned Transmission Projects"}
        session = FakeSession([[("run_parser", {"code": GOOD, "count_pattern": args["count_pattern"]})],
                               [("submit", args)]])
        result = parser_agent.write_parser(pages, self.identity, "Acme", session, lambda sig: [], log=lambda *a: None)
        self.assertEqual(result["count_scope"], args["count_scope"])
        self.assertEqual(result["report"]["count_pattern_matches"], 3)
        prior = [b["toolResult"]["content"][0]["text"] for b in session.sent[-1][-1]["content"] if "toolResult" in b]
        self.assertIn("count_pattern matches 6 times", prior[0])

    def test_a_signature_problem_blocks_acceptance(self):
        s = FakeSession([[("submit", {"code": GOOD, "signature": ["Project"], "count_pattern": r"Project \S+", "notes": "n"})]] * 2)
        with self.assertRaises(RuntimeError):
            parser_agent.write_parser(PAGES, self.identity, "Acme", s, lambda sig: ["signature also matches registered filing x"], max_turns=2, log=lambda *a: None)


class RepairTests(unittest.TestCase):
    def test_a_repair_must_still_read_the_editions_the_parser_already_reads(self):
        older = ["Acme Power Company\nProject B-1  Old Line\n  In service: 2026\n"]
        breaks_older = GOOD.replace(r"Project (\S+)", r"Project (A-\S+)")
        s = FakeSession([[("submit", {"code": breaks_older, "signature": ["Acme Power Company"], "count_pattern": r"Project \S+", "notes": "n"})],
                         [("submit", {"code": GOOD, "signature": ["Acme Power Company"], "count_pattern": r"Project \S+", "notes": "n"})]])
        out = parser_agent.write_parser(PAGES, AgentLoopTests.identity, "Acme", s, lambda sig: [], log=lambda *a: None,
                                        start=(breaks_older, "failed"), others=[("acme-2026", older)])
        self.assertTrue(out["report"]["earlier_editions"]["acme-2026"]["accepted"])
        first = s.sent[0][0]["content"][0]["text"]
        self.assertIn("Repair it", first)
        rejected = [b["toolResult"]["content"][0]["text"] for b in s.sent[1][-1]["content"] if "toolResult" in b][0]
        self.assertIn("no longer reads earlier edition acme-2026", rejected)


class SignatureCheckTests(unittest.TestCase):
    def test_a_signature_must_occur_here_and_nowhere_registered(self):
        import ingest
        reg = registry.load_registry()

        class Texts:
            def of(self, f):
                return "Dominion Energy South Carolina Project 1 of 3" if f["plan"] == "desc" else "Georgia ITS"
        check = ingest.signature_checker("Acme Power Company\nProject 1 of 3", reg, Texts(), set())
        self.assertEqual(check(["Acme Power Company"]), [])
        self.assertTrue(check(["Nowhere Electric"]))
        self.assertTrue(any("also matches registered filing desc-" in p for p in check([r"Project \d+ of \d+"])))
        self.assertEqual(ingest.signature_checker("Acme\nProject 1 of 3", reg, Texts(), {"desc"})([r"Project \d+ of \d+"]), [])


class EditionTests(unittest.TestCase):
    def test_the_agents_edition_is_used_only_as_printed(self):
        import ingest
        text = "Integrated Resource Plan\n2025 Update\n...\nPlanned Projects 2026 –  2030\nload forecast 2034-2040"
        self.assertEqual(ingest.printed("2025 Update", text), "2025 Update")
        self.assertEqual(ingest.printed("2026–2030", text), "2026-2030")
        self.assertIsNone(ingest.printed("2027-2031", text))
        self.assertIsNone(ingest.printed("", text))


@unittest.skipUnless((RAW / "desc_2026-2030.pdf").exists(), "needs data/raw/desc_2026-2030.pdf")
class SeveralCompaniesTests(unittest.TestCase):
    identity = {"is_project_list": True, "list_pages": "1-54", "reason": "a regional deck", "company": "Acme Power", "short_name": "Acme",
                "owner": "Acme Power Company", "aliases": [], "state": "SC", "title": "Regional plan", "edition": "2026-2030", "published": "",
                "existing_plan": "", "public": True, "other_companies": ["Beta Electric"]}

    def ingest_as(self, company=None):
        """Run ingest on a PDF no registered parser claims, with the identify step answering self.identity."""
        import argparse
        import tempfile
        import bedrock
        import ingest
        asked = []
        args = argparse.Namespace(source=str(RAW / "desc_2026-2030.pdf"), find=None, company=company, url=None, zip_member=None, title=None,
                                  edition=None, date=None, skip_plan=["desc"], dry_run=True, no_build=True, model=None, max_turns=1)
        real = bedrock.Session, parser_agent.identify, ingest.log, ingest.WORK
        tmp = tempfile.TemporaryDirectory()
        bedrock.Session, ingest.log, ingest.WORK = (lambda *a: FakeSession([])), (lambda *a: None), Path(tmp.name)
        parser_agent.identify = lambda *a, **kw: asked.append(kw.get("company")) or dict(self.identity)
        try:
            ingest.ingest(args)
        finally:
            bedrock.Session, parser_agent.identify, ingest.log, ingest.WORK = real
            tmp.cleanup()
            self.assertEqual(asked, [company])

    def test_a_document_with_several_utilities_lists_needs_a_company(self):
        with self.assertRaisesRegex(SystemExit, "also of Beta Electric;.*Utility to import.*--company"):
            self.ingest_as()

    def test_the_named_company_must_be_whose_list_was_read(self):
        with self.assertRaisesRegex(SystemExit, "Acme Power's list, not Beta Electric's"):
            self.ingest_as("Beta Electric")


class FinderTests(unittest.TestCase):
    def test_typed_addresses_must_be_plain_page_addresses(self):
        import finder
        real = finder._resolves
        # which names exist, without the network
        finder._resolves = lambda name: name in {"santeecooper.com", "www.google.com", "www.scrtp.com"}
        try:
            self.assertTrue(finder.typed_ok("https://www.santeecooper.com/about/integrated-resource-plan/"))
            self.assertTrue(finder.typed_ok("https://www.scrtp.com/assets/pdfs/home/2026-2030-2million-and-above-project-descriptions.pdf"))
            for url in ("https://lite.duckduckgo.com/lite/?q=santee+cooper", "https://search.example.dev/?q=test",
                        "https://r.jina.ai/https://www.scrtp.com/a.pdf", "https://web.archive.org/web/2024/https://www.scrtp.com/",
                        "http://proxy.example/fetch/https%3A%2F%2Fwww.scrtp.com%2F",
                        # an address inside another without its scheme, or with its query escaped
                        "https://web.archive.org/web/2025/santeecooper.com/transmission-plan",
                        "https://r.jina.ai/www.google.com/search%3Fq%3Dsantee+cooper+transmission"):
                self.assertFalse(finder.typed_ok(url), url)
        finally:
            finder._resolves = real

    def test_only_links_found_on_fetched_pages_may_carry_a_query(self):
        import finder
        opened, doc = [], "https://utility.example/docs?id=7"

        def fetch(url, linked=None, allow=None):
            opened.append(url)
            linked.add(doc)
            return "page"

        def check_pdf(url, zip_member=None, allow=None, checked=None):
            opened.append(url)
            checked.setdefault(url, set()).add(None)
            return "pdf"
        real = finder.fetch, finder.check_pdf
        finder.fetch, finder.check_pdf = fetch, check_pdf
        try:
            s = FakeSession([
                [("fetch", {"url": "https://search.example/?q=utility"})],
                [("check_pdf", {"url": doc})],
                [("fetch", {"url": "https://utility.example/"})],
                [("check_pdf", {"url": doc})],
                [("propose", {"url": doc, "why": "its list"})],
            ])
            found = finder.find("Utility", s, log=lambda *a: None)
        finally:
            finder.fetch, finder.check_pdf = real
        self.assertEqual(opened, ["https://utility.example/", doc])
        self.assertEqual(found["url"], doc)
        refusals = [s.sent[i][-1]["content"][0]["toolResult"]["content"][0]["text"] for i in (1, 2)]
        self.assertTrue(all("search engines" in r for r in refusals))


if __name__ == "__main__":
    unittest.main()

class LateEditionTests(unittest.TestCase):
    def test_project_heading_after_page_thirty(self):
        import ingest
        pages = ["Stakeholder meeting March 11 2026"] + ["Forecast 2026-2035"] * 48 + ["Transmission Projects 2026-2030"]
        self.assertEqual(ingest.edition_of(pages, "meeting-2026-03-11.pdf", 2026), "2026-2030")

    def test_conflicting_project_headings_need_explicit_edition(self):
        import ingest
        self.assertIsNone(ingest.edition_of(["Transmission Projects 2025-2029", "Transmission Projects 2026-2030"], "meeting.pdf", 2026))
