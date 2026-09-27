"""Add a public PDF to GridLock. If a registered parser recognizes it (its signature, e.g. a DESC $2M+ list), that
parser reads it. Otherwise the agent on Amazon Bedrock says whose list it is: a company GridLock already reads gets
its existing parser tried first, and when no parser can read the PDF the agent writes one. Any parser's output on a
new edition must also look like the same list as the plan's previous edition (count, IDs, dates, names), and a parser
the agent wrote is registered only if its output passes every check in generated_parser.py. Then the build runs, and
the new projects join the map and the pairs like any others.

    python3 pipeline/ingest.py URL                              # a public PDF, or a zip holding one (--zip-member)
    python3 pipeline/ingest.py FILE.pdf --url URL               # a local copy of a public PDF (URL must serve the same bytes)
    python3 pipeline/ingest.py FILE.pdf --dry-run               # route and parse only; nothing is registered
    python3 pipeline/ingest.py --find "Santee Cooper"           # find the company's public list first, then as above

--company NAME says whose list to read when one PDF lists several utilities' projects (a regional deck, say);
--skip-plan ID acts as if that plan weren't registered (with --dry-run only: to test the agent against a parser we
already have); --title/--edition/--date set what can't be read from the PDF (--date is needed when neither the
document nor its metadata gives one); --no-build skips the rebuild. Parsers are tried on a new PDF in a separate
process that reads the registry from memory; data/filings.json and data/raw change only when a filing is registered.
Bedrock calls and tokens are logged at the end of every run and kept in data/build/ingest/usage.jsonl.
The agent needs boto3 (requirements-agent.txt) and AWS credentials with Bedrock access, e.g. AWS_PROFILE=gridlock.
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import registry
from common import BUILD, RAW, ROOT, dump, haversine_mi, load, pdf_pages, read_date

WORK = BUILD / "ingest"
TRIAL_TIMEOUT_S = 600


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def obtain(source, zip_member=None, local=True):
    """(pdf bytes, public url or None, zip member or None) from a URL or, if local, a file. A URL is downloaded the
    finder's way: public addresses only (every redirect too), and a size cap on the download and on a zip member."""
    import finder
    if re.match(r"https?://", source):
        try:
            data, url = finder.get(source, finder.MAX_PDF)[0], source
        except Exception as e:
            raise SystemExit(f"Could not download {source}: {e}")
    elif not local:
        raise SystemExit(f"{source} is not a web address")
    else:
        data, url = Path(source).read_bytes(), None
    try:
        data, member = finder.pdf_from(data, zip_member)
    except ValueError as e:
        raise SystemExit(f"{source}: {e}".replace("name one as its zip member", "pick one with --zip-member"))
    return data, url, member


def confirm_url(data, url, zip_member):
    """A local file's --url must serve the same bytes, when it can be reached; otherwise a fresh clone would build from
    whatever the address serves under this filing's name."""
    try:
        served, _, _ = obtain(url, zip_member, local=False)
    except SystemExit as e:
        log(f"Couldn't check that {url} serves this file ({e}); recording its sha256.")
        return
    if sha(served) != sha(data):
        raise SystemExit(f"{url} serves a different file than {sha(data)[:12]} (it serves {sha(served)[:12]}); pass the address of this PDF.")


def pdfinfo(path):
    out = subprocess.run(["pdfinfo", "-isodates", str(path)], capture_output=True, text=True, timeout=60).stdout
    return {k.strip(): v.strip() for k, v in (ln.split(":", 1) for ln in out.splitlines() if ":" in ln)}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def records_sha(records):
    """Stable digest of a built-in parser's reviewed record fields."""
    return sha(json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode())


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def iso_date(s):
    """--date as YYYY-MM-DD (filings are ordered and compared as ISO text)."""
    return dt.date.fromisoformat(s).isoformat()


def edition_of(pages, source, since):
    """The planning years a list covers, as "2026-2030": a year range in the file name or address, else the range printed
    most often in its first pages, else the longest run of consecutive year columns. A list of future projects covers
    years from its publication on, so a range ending before `since` (the year the PDF was made) is some other period."""
    ok = lambda a, b: int(a) < int(b) and int(b) >= since
    for a, b in re.findall(r"(20\d\d)\s*[-–_]\s*(20\d\d)", source):
        if ok(a, b):
            return f"{a}-{b}"
    seen = Counter((a, b) for p in pages[:30] for a, b in re.findall(r"\b(20\d\d)\s*[-–]\s*(20\d\d)\b", p) if ok(a, b))
    if seen:
        return "-".join(seen.most_common(1)[0][0])
    best = []
    for ln in "\n".join(pages[:5]).splitlines():
        run = []
        for y in (int(y) for y in re.findall(r"\b20\d\d\b", ln)):
            run = run + [y] if run and y == run[-1] + 1 else [y]
            best = run if len(run) > len(best) else best
    return f"{best[0]}-{best[-1]}" if len(best) >= 3 and best[-1] >= since else None


def printed(edition, text):
    """The agent's reading of the edition ("2026-2030", "2025 Update"), if the document prints it (spacing and dashes
    aside), with an ASCII hyphen between years; None if it doesn't, so the edition is read from the PDF instead."""
    flat = lambda s: re.sub(r"[\s\-–—]+", " ", s).strip().lower()
    if not edition or flat(edition) not in flat(text):
        return None
    return re.sub(r"\s*[-–—]\s*", "-", " ".join(edition.split()))


def title_of(plan, edition):
    """A plan's list title for an edition, from the plan's titleTemplate."""
    return plan["titleTemplate"].replace("{edition}", edition) if plan.get("titleTemplate") else None


def describe(pages, meta, plan, args):
    """Edition, title and date for a filing read without the agent: flags first, then the PDF and the plan."""
    date, basis = filing_date(meta, None, args)
    edition = args.edition or edition_of(pages, f"{args.source} {args.url or ''}", int(date[:4]))
    title = args.title or title_of(plan, edition or "") or meta.get("Title") or " ".join(ln.strip() for ln in pages[0].splitlines() if ln.strip())[:120]
    return edition, title, date, basis


def filing_date(meta, printed, args):
    """The filing's date and where it came from: --date, the date the document prints, the PDF's creation date. Never
    today's date for a filing that is registered: it would order the filing, and pick its plan's current list, by
    when it happened to be ingested."""
    if args.date:
        return args.date, "given at ingest"
    if printed and read_date(printed)[0]:
        return read_date(printed)[0].isoformat(), "date printed in the document"
    d = read_date(meta.get("CreationDate", ""))[0]
    if d:
        return d.isoformat(), "PDF creation date"
    if not args.dry_run:
        raise SystemExit("Neither the document nor its PDF metadata gives a date; pass --date YYYY-MM-DD (when it was published or filed).")
    log("No date in the document or its PDF metadata; this dry run uses today's (registering needs --date).")
    return dt.date.today().isoformat(), "date ingested (dry run)"


def raw_file(f):
    """A registered filing's PDF in data/raw: downloaded from its url when missing (a fresh clone; data/raw isn't
    committed), and checked against the sha256 it was registered with."""
    p = RAW / f["file"]
    fetched = not p.exists()
    if fetched:
        import fetch
        log(f"Downloading {f['id']}'s PDF (not in data/raw) ...")
        try:
            fetch.fetch(f)
        except Exception as e:
            raise SystemExit(f"{f['id']}'s PDF is not in data/raw and could not be downloaded ({e}); run python3 pipeline/fetch.py.")
    if f.get("sha256") and sha(p.read_bytes()) != f["sha256"]:
        if fetched:
            p.unlink()
        raise SystemExit(f"data/raw/{f['file']} is not the PDF {f['id']} was registered from (sha256 {f['sha256'][:12]}); "
                         f"{'its url now serves another file' if fetched else 'replace it with the right one'}.")
    return p


class Texts:
    """Page text of registered filings, read once, for checking a new signature against them."""

    def __init__(self, reg):
        self.reg, self.cache = reg, {}

    def of(self, f):
        if f["id"] not in self.cache:
            self.cache[f["id"]] = "\f".join(pdf_pages(raw_file(f)))
        return self.cache[f["id"]]


def signature_checker(text, reg, texts, skip, plan_id=None):
    """Problems with a proposed signature: it must occur in this PDF and not in any other plan's registered filings,
    and no other plan's parser may claim this PDF. The same plan's filings may match (a plan's later layout)."""
    def problems(sig):
        out = []
        if not isinstance(sig, list) or not 1 <= len(sig) <= 6:
            return ["signature must be a list of 1-6 regexes"]
        for s in sig:
            try:
                if not registry.signature_hit(s, text):
                    out.append(f"signature pattern {s!r} does not occur in this document")
            except ValueError as e:
                out.append(f"signature pattern {s!r} cannot be checked: {e}")
        if out:
            return out
        for f in registry.filings(reg):
            if f["plan"] not in skip and f["plan"] != plan_id:
                try:
                    if all(registry.signature_hit(s, texts.of(f)) for s in sig):
                        out.append(f"signature also matches registered filing {f['id']} ({f['title']}); make it specific to this layout")
                except ValueError as e:
                    out.append(f"signature cannot be checked against registered filing {f['id']}: {e}")
        for pid, p in registry.parsers(reg).items():
            if p["plan"] not in skip and p["plan"] != plan_id and p.get("signature"):
                try:
                    if all(registry.signature_hit(s, text) for s in p["signature"]):
                        out.append(f"registered parser {pid}'s signature also matches this document")
                except ValueError as e:
                    out.append(f"registered parser {pid}'s signature cannot be checked: {e}")
        return out
    return problems


def rows_of(f, reg, include_records=False):
    """(name, project ID, dated) per record, as the filing's parser reads it on the build's own path (an agent-written
    parser that fails its checks fails here). For an agent-written parser, the IDs it printed: the build fills missing
    ones from item labels."""
    recs, _ = registry.parse_filing(f, reg)
    p = registry.parsers(reg)[f["parser"]]
    if p["kind"] == "generated":
        import sandbox
        raw, err = sandbox.run((ROOT / "pipeline" / p["module"]).read_text(), pdf_pages(RAW / f["file"]))
        if err:
            raise SystemExit(err)
        rows = raw_rows(raw)
        details = recs
    else:
        rows = [(r["name"], r.get("project_id"), bool(r.get("isd"))) for r in recs]
        details = recs
    return (rows, details) if include_records else rows


def raw_rows(raw):
    return [(r.get("name"), r.get("project_id"), bool(read_date(r.get("in_service"))[0]) if isinstance(r.get("in_service"), str) else False)
            for r in raw if isinstance(r, dict)]


def shape(rows):
    """What editions of one list are compared on: records, how many carry a project ID and a readable in-service
    date, and the median name length."""
    names = sorted(len(n) if isinstance(n, str) else 0 for n, _, _ in rows)
    return {"records": len(rows), "withId": sum(1 for _, i, _ in rows if isinstance(i, str) and i.strip()),
            "dated": sum(1 for *_, d in rows if d), "nameMedian": names[len(names) // 2] if names else 0}


def shape_problems(new, old=None, old_id=None):
    """Why a parser's output doesn't look like a new edition of the same list as old, the plan's closest registered
    edition: no records or no dates; a count outside half to twice the old one; project IDs or in-service dates on
    less than half the share of records the old edition had them on; names over twice as long (a label no longer
    found, so the name runs on into the text)."""
    if not new["records"]:
        return ["no records"]
    out = [] if new["dated"] else ["no record has a readable in-service date"]
    if old and old["records"]:
        n, m = new["records"], old["records"]
        if not m / 2 <= n <= 2 * m:
            out.append(f"{n} records against {m} in {old_id}; a new edition of the same list should have between half and twice as many")
        for k, what in (("withId", "a project ID"), ("dated", "an in-service date")):
            if new[k] / n < old[k] / m / 2:
                out.append(f"{new[k]} of {n} records have {what}, against {old[k]} of {m} in {old_id}")
        # short names vary a lot relative to their length, so the growth must also be more than 20 characters
        if new["nameMedian"] > 2 * old["nameMedian"] and new["nameMedian"] - old["nameMedian"] > 20:
            out.append(f"names run to {new['nameMedian']} characters (median) against {old['nameMedian']} in {old_id}; "
                       "a label may no longer be found, so names run on into the text")
    return out


def outline(reg, f):
    """shape() of what f's parser makes of it, or {"error"}. Runs in a fresh process with the registry passed in memory
    (the live registry and data/raw are never written for a trial), so a parser that crashes or hangs stays there."""
    code = "import sys; sys.path.insert(0, 'pipeline'); import ingest; ingest.outline_main()"
    try:
        p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, input=json.dumps({"reg": reg, "filing": f}),
                           capture_output=True, text=True, timeout=TRIAL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"error": f"it ran longer than {TRIAL_TIMEOUT_S} s"}
    if p.returncode != 0:
        last = p.stderr.strip().splitlines()[-1] if p.stderr.strip() else f"exit {p.returncode}"
        return {"error": last[:300] + ("..." if len(last) > 300 else "")}
    return json.loads(p.stdout.strip().splitlines()[-1])


def outline_main():
    import common
    import tempfile
    d = json.load(sys.stdin)
    with tempfile.TemporaryDirectory(prefix="gridlock-trial-") as tmp:
        # parsers dump what they read to data/build; a trial's dumps go to a scratch directory instead (the parser
        # modules are imported after this, so they pick it up)
        common.BUILD = Path(tmp)
        rows, details = rows_of(d["filing"], d["reg"], include_records=True)
        samples = [{"projectId": project_id, "name": name, "hasInServiceDate": dated}
                   for name, project_id, dated in rows[:5]]
        print(json.dumps({**shape(rows), "samples": samples, "reviewRecords": details}))


def closest(reg, filing):
    """The plan's registered edition just before this one in its series, else just after it (a backfilled older edition)."""
    fs = [f for f in registry.filings(_with(json.loads(json.dumps(reg)), filing=filing), plan=filing["plan"])]
    i = next(i for i, f in enumerate(fs) if f["id"] == filing["id"])
    return fs[i - 1] if i else (fs[1] if len(fs) > 1 else None)


def plan_of(identity, reg, skip):
    """The registered plan a document the agent identified belongs to: one whose own names (name, owner, aliases) are
    the company's, in the same state, that the agent's existing_plan doesn't contradict; None for a new company. A
    member utility's or parent's own list is not its plan's, and a separate plan for it would repeat projects the
    joint list holds, so that stops, as does an existing_plan no own name supports."""
    names = [identity["company"], identity["owner"], identity["short_name"], *identity["aliases"]]
    plans, said = registry.plans(reg), identity.get("existing_plan") or ""
    plan_id = registry.plan_for_company(names, reg, skip)
    if plan_id:
        if identity["state"].upper() != plans[plan_id]["state"]:
            raise SystemExit(f"It names {identity['company']}, which is plan {plan_id!r} ({plans[plan_id]['name']}, {plans[plan_id]['state']}), "
                             f"but the agent places its projects in {identity['state']!r}; nothing registered.")
        if said and said != plan_id:
            raise SystemExit(f"Its company names match plan {plan_id!r}, but the agent says it is plan {said!r}'s list; nothing registered.")
        return plan_id
    member = registry.plan_with_member(names, reg, skip)
    if member:
        raise SystemExit(f"{identity['company']} is a member or parent of {plans[member]['name']} (plan {member!r}), whose joint list GridLock "
                         "reads. Its own list isn't filed under that plan, and a plan of its own would repeat the joint list's projects; nothing registered.")
    if said in plans and said not in skip:
        raise SystemExit(f"The agent says this is plan {said!r}'s list, but none of its names ({', '.join(filter(None, names))}) is one of that "
                         f"plan's own names. If it is, add the name to the plan's aliases in data/filings.json and run again; nothing registered.")
    return None


def agent(args, state):
    """The run's Bedrock session, started on first use."""
    if not state.get("session"):
        from bedrock import Session
        state["session"] = Session(args.model)
    return state["session"]


def ingest(args):
    """Route, parse and register one PDF. Whatever way the run ends, its Bedrock usage is logged and kept."""
    if args.skip_plan and not args.dry_run:
        raise SystemExit("--skip-plan is for testing the agent against a parser GridLock already has; use it with --dry-run.")
    run, state = {"started": dt.datetime.now().isoformat(timespec="seconds"), "dryRun": args.dry_run}, {}
    try:
        return _ingest(args, run, state)
    except BaseException as e:
        run["exit"] = str(e.code if isinstance(e, SystemExit) else f"{type(e).__name__}: {e}")
        raise
    finally:
        record(run, state)


def record(run, state):
    """Log the run's Bedrock calls and tokens and keep them: in its run.json (rewritten when the same PDF is ingested
    again) and appended to data/build/ingest/usage.jsonl, one line per run."""
    s = state.get("session")
    if s:
        run["usage"], run["model"] = dict(s.usage), s.model
        u = {k: s.usage.get(k, 0) for k in ("calls", "inputTokens", "cacheReadInputTokens", "cacheWriteInputTokens", "outputTokens")}
        log(f"Bedrock: {u['calls']} calls, {u['inputTokens']:,} input + {u['cacheReadInputTokens']:,} cached "
            f"+ {u['cacheWriteInputTokens']:,} cache-write + {u['outputTokens']:,} output tokens")
        WORK.mkdir(parents=True, exist_ok=True)
        with open(WORK / "usage.jsonl", "a") as fh:
            fh.write(json.dumps({k: run.get(k) for k in ("started", "source", "sha256", "route", "dryRun", "model", "usage", "exit")}) + "\n")
    if state.get("work"):
        dump(run, state["work"] / "run.json")
        if run.get("dryRun") and run.get("reviewedParserSha256"):
            dump(run, state["work"] / "review.json")


def _ingest(args, run, state):
    reviewed_sha = getattr(args, "reviewed_parser_sha256", None)
    reviewed_records_sha = getattr(args, "reviewed_records_sha256", None)
    if reviewed_sha and args.dry_run:
        raise SystemExit("A reviewed parser is for registration, not a dry run.")
    found = None
    if args.find:
        from finder import find
        session = agent(args, state)
        run["source"] = f"--find {args.find}"
        log(f"Looking for {args.find}'s public list of planned transmission projects ...")
        found = find(args.find, session, log=log)
        run["found"] = found
        args.source, args.zip_member = found["url"], found.get("zip_member")
    elif not args.source:
        raise SystemExit("Give a PDF (URL or file) or --find COMPANY.")
    run["source"] = args.source
    if args.url and not re.match(r"https?://", args.url):
        raise SystemExit(f"--url must be the PDF's public web address, not {args.url!r}.")
    # a proposed address is downloaded like any other (guarded), never read as a local path
    data, url, member = obtain(args.source, args.zip_member, local=not found)
    if args.url and args.url != url:
        confirm_url(data, args.url, args.zip_member)
        # A reviewed ZIP is registered from its cached, extracted PDF. Preserve
        # the archive member so a fresh clone can fetch the same document.
        member = member or args.zip_member
    url = args.url or url
    if not url and not args.dry_run:
        raise SystemExit("GridLock uses public filings only and records where each came from: pass --url with this PDF's public address (or use --dry-run).")
    reg = registry.load_registry()
    skip = set(args.skip_plan or [])
    # whose list to read, when one PDF holds several utilities' lists (a member's name picks its joint plan)
    focus = args.find or args.company
    focus_plan = (registry.plan_for_company([focus], reg, skip) or registry.plan_with_member([focus], reg, skip)) if focus else None
    digest = sha(data)
    run["sha256"] = digest
    for f in registry.filings(reg):
        if f["plan"] not in skip and (not focus or f["plan"] == focus_plan) and (
                f.get("sha256") == digest or (RAW / f["file"]).exists() and sha((RAW / f["file"]).read_bytes()) == digest):
            log(f"Already registered as {f['id']} ({f['title']}); nothing to do.")
            return None
    work = state["work"] = WORK / digest[:12]
    work.mkdir(parents=True, exist_ok=True)
    pdf = work / "source.pdf"
    pdf.write_bytes(data)
    reviewed = None
    if reviewed_sha:
        review_file, parser_file = work / "review.json", work / "parser.py"
        if not re.fullmatch(r"[0-9a-f]{64}", reviewed_sha) or not review_file.is_file() or not parser_file.is_file():
            raise SystemExit("The reviewed parser is missing; preview this PDF again.")
        reviewed = load(review_file)
        if (reviewed.get("sha256") != digest or not reviewed.get("dryRun") or not reviewed.get("agent") or
                reviewed.get("reviewedParserSha256") != reviewed_sha or
                sha(parser_file.read_bytes()) != reviewed_sha):
            raise SystemExit("The reviewed parser or PDF changed; preview this PDF again.")
        run["reviewedParserSha256"] = reviewed_sha
    pages = pdf_pages(pdf)
    text = "\f".join(pages)
    if len(text.strip()) < 500:
        raise SystemExit("The PDF has no text layer (a scan?); GridLock reads text, so it can't use it.")
    meta = pdfinfo(pdf)
    run.update({"url": url, "zipMember": member, "pages": len(pages)})
    log(f"{args.source}: {len(pages)} pages")

    plans = {k: v for k, v in registry.plans(reg).items() if k not in skip}
    # newest first: a plan's later layout is registered after its earlier one
    hits = [h for h in registry.signature_matches(text, reg, skip_plans=skip)[::-1] if not focus or registry.parsers(reg)[h]["plan"] == focus_plan]
    if len({registry.parsers(reg)[h]["plan"] for h in hits}) > 1:
        raise SystemExit(f"Parsers of different plans recognize this PDF ({', '.join(hits)}); their signatures need to be more specific.")
    identity, candidates = None, hits
    if hits:
        plan_id = registry.parsers(reg)[hits[0]]["plan"]
        run["route"] = f"signature of parser {', '.join(hits)}"
        log(f"Recognized by the signature of the {', '.join(repr(h) for h in hits)} parser ({plans[plan_id]['name']}).")
        edition, title, date, basis = describe(pages, meta, plans[plan_id], args)
        identity = {"title": title, "edition": edition, "reason": f"a new edition of {plans[plan_id]['name']}'s list, recognized by its parser's signature"}
    else:
        from parser_agent import identify
        if reviewed:
            identity = reviewed.get("identity")
            if not isinstance(identity, dict):
                raise SystemExit("The reviewed filing identity is missing; preview this PDF again.")
            log("Reusing the filing identity reviewed in the dry run.")
        else:
            session = agent(args, state)
            log(f"No registered parser recognizes it; asking {session.model} what it is.")
            try:
                identity = identify(pages, meta, plans, session, log=log, company=focus)
            except RuntimeError as e:
                raise SystemExit(f"The agent couldn't identify the PDF: {e}")
        run["identity"] = identity
        if not identity["is_project_list"]:
            whose = f"{focus}'s " if focus else ""
            raise SystemExit(f"Not a list of {whose}planned transmission projects: {identity['reason']}")
        if identity.get("other_companies") and not focus:
            raise SystemExit(f"It lists the projects of {identity['company']} and also of {', '.join(identity['other_companies'])}; "
                             "pass --company NAME to choose whose list to read.")
        if not identity["public"]:
            raise SystemExit("The agent reads this as confidential (CEII) content; GridLock uses public data only.")
        names = [identity["company"], identity["owner"], identity["short_name"], *identity["aliases"], *identity.get("members", [])]
        if focus and not any(set(registry.company_key(focus).split()) <= set(registry.company_key(n).split()) for n in names):
            raise SystemExit(f"The PDF is {identity['company']}'s list, not {focus}'s; nothing registered.")
        plan_id = plan_of(identity, reg, skip)
        date, basis = filing_date(meta, identity.get("published"), args)
        edition = args.edition or printed(identity["edition"], text) or edition_of(pages, f"{args.source} {url or ''}", int(date[:4]))
        title = args.title or identity["title"]
        if plan_id:
            candidates = [pid for pid, p in registry.parsers(reg).items() if p["plan"] == plan_id][::-1]
            run["route"] = f"company match: plan {plan_id!r}"
            log(f"It is {plans[plan_id]['name']}'s, a company GridLock already reads; trying its parsers: {', '.join(candidates)}")
        else:
            run["route"] = "new company"
            log(f"New company: {identity['company']}.")
    if not edition:
        raise SystemExit("Couldn't read the edition (the planning years it covers) from the PDF; pass --edition, e.g. --edition 2027-2031.")

    new_plan = None
    if not plan_id:
        plan_id = slug(identity["short_name"] or identity["company"])
        while plan_id in registry.plans(reg):
            plan_id += "-2"
        state_code = identity["state"].upper()
        if not re.fullmatch(r"[A-Z]{2}", state_code):
            raise SystemExit(f"The agent couldn't tell which state the projects are in (it said {identity['state']!r}).")
        template = title.replace(edition, "{edition}") if edition in title else f"{title}, {{edition}}"
        new_plan = {"name": identity["short_name"], "owner": identity["owner"], "state": state_code, "reusesIds": True, "titleTemplate": template,
                    "aliases": list(dict.fromkeys(a for a in [identity["company"], identity["owner"], *identity["aliases"]] if a)),
                    **({"members": list(dict.fromkeys(identity["members"]))} if identity.get("members") else {})}
    plan = new_plan or registry.plans(reg)[plan_id]
    fid = f"{plan_id}-{slug(edition)}"
    if any(f["id"] == fid for f in registry.filings(reg)):
        raise SystemExit(f"{fid} is already registered from a different file. If this is a corrected re-issue, replace that filing's entry by hand.")
    filing = {"id": fid, "plan": plan_id, "utility": plan["name"], "state": plan["state"], "edition": edition, "parser": None,
              "title": title, "date": date, "dateBasis": basis, "url": url, **({"zipMember": member} if member else {}),
              "file": f"{plan_id}_{slug(edition)}.pdf", "sha256": digest}
    if reviewed:
        former = reviewed.get("filing") or {}
        keys = ("id", "plan", "utility", "state", "edition", "title", "date", "url", "zipMember", "file", "sha256")
        if any(former.get(k) != filing.get(k) for k in keys):
            raise SystemExit("The filing metadata changed since preview; preview it again.")
    stale = RAW / filing["file"]
    if stale.exists() and sha(stale.read_bytes()) != digest and not args.dry_run:
        raise SystemExit(f"data/raw/{filing['file']} already holds a different PDF with no filing of its own (left from an earlier run?); "
                         "remove it and run again.")

    # the plan's closest registered edition, which a new edition's records are compared with
    prev = closest(reg, filing) if not new_plan else None
    base = None
    if prev:
        base = outline(reg, dict(prev, file=str(raw_file(prev))))
        if base.get("error"):
            raise SystemExit(f"Can't read {prev['id']}, the edition to compare with: {base['error']}")
    parser_id = None
    for pid in candidates:
        filing["parser"] = pid
        log(f"Trying the {pid!r} parser ...")
        got = outline(reg, dict(filing, file=str(pdf)))   # data/raw is left alone: the trial reads the work copy
        why = [got["error"]] if got.get("error") else shape_problems(got, base, prev and prev["id"])
        if not why:
            log(f"  {got['records']} records, {got['dated']} with in-service dates, {got['withId']} with project IDs")
            output_sha = records_sha(got.get("reviewRecords", []))
            if reviewed_records_sha and output_sha != reviewed_records_sha:
                raise SystemExit("The extracted records changed since preview; preview this filing again.")
            run["shape"] = got
            run["reviewRecordsSha256"] = output_sha
            parser_id = pid
            break
        log("  it can't: " + "; ".join(why))
    run["filing"] = filing
    if parser_id and not args.dry_run:
        register(filing, pdf)
        log(f"Registered {fid}; read by the existing {parser_id!r} parser.")
    elif parser_id:
        log(f"Dry run: the existing {parser_id!r} parser reads it; nothing registered.")
    else:
        if reviewed_records_sha:
            raise SystemExit("The parser can no longer read the reviewed records; preview this filing again.")
        # the plan's own agent-written parser recognized the PDF but can't read it: the layout drifted, so repair that parser
        broken = next((h for h in hits if registry.parsers(reg)[h]["kind"] == "generated"), None)
        compare = (lambda raw: shape_problems(shape(raw_rows(raw)), base, prev["id"])) if base else None
        session = SimpleNamespace(model=reviewed["agent"]["parser"]["model"]) if reviewed else agent(args, state)
        parser_id = write_new_parser(args, reg, pages, text, identity, plan_id, plan, new_plan, filing, session, skip, work, run,
                                     repair=broken, compare=compare, reviewed=reviewed)
        if parser_id is None:
            return run
    run["parser"] = parser_id
    if not args.dry_run and not args.no_build:
        log("Rebuilding data/projects.json ...")
        subprocess.run([sys.executable, "pipeline/build.py"], cwd=ROOT, check=True)
        report(plan_id)
    return run


def register(filing, pdf, plan=None, parser=None, code=None):
    """Copy the PDF into data/raw, write an agent-written parser's code, and add the filing (and a new plan, or a new or
    repaired parser) to the registry, all under the registry lock. Never copies over a different file, and refuses a
    filing, plan or new parser id another run registered meanwhile."""
    RAW.mkdir(parents=True, exist_ok=True)
    raw = RAW / filing["file"]
    with registry.editing() as reg:
        kind = parser[1]["kind"] if parser else registry.parsers(reg).get(filing.get("parser"), {}).get("kind")
        if kind == "generated":
            import sandbox
            sandbox.require_bubblewrap()
        if raw.exists() and raw.read_bytes() != pdf.read_bytes():
            raise SystemExit(f"data/raw/{filing['file']} holds a different PDF; nothing registered.")
        taken = ([f"filing {filing['id']}"] if any(f["id"] == filing["id"] for f in reg["filings"]) else []) + \
                ([f"plan {plan[0]}"] if plan and plan[0] in reg["plans"] else []) + \
                ([f"parser {parser[0]}"] if parser and "repaired" not in parser[1] and parser[0] in reg["parsers"] else [])
        if taken:
            raise SystemExit(f"{', '.join(taken)} was registered by another run meanwhile; nothing registered.")
        shutil.copy(pdf, raw)
        if code:
            registry.PARSER_DIR.mkdir(exist_ok=True)
            (registry.PARSER_DIR / f"{parser[0]}.py").write_text(code)
        _with(reg, plan=plan, parser=parser, filing=filing)


def write_new_parser(args, reg, pages, text, identity, plan_id, plan, new_plan, filing, session, skip, work, run,
                     repair=None, compare=None, reviewed=None):
    """Have the agent write a parser for this PDF (or, with repair, fix the registered parser that no longer reads it).
    compare(raw records) gives problems with its output against the plan's previous edition."""
    from parser_agent import write_parser
    import generated_parser
    # every registered filing the new signature is checked against, present before the agent starts
    for f in registry.filings(reg):
        if f["plan"] not in skip and f["plan"] != plan_id:
            raw_file(f)
    start, others = None, []
    if repair:
        old = registry.parsers(reg)[repair]
        code = (ROOT / "pipeline" / old["module"]).read_text()
        rep, _, _ = generated_parser.evaluate(code, pages, old.get("countPattern"), old.get("countScope"))
        start = (code, json.dumps({k: rep.get(k) for k in ("error", "records", "blocking") if rep.get(k) is not None}, indent=1))
        others = [(f["id"], pdf_pages(raw_file(f))) for f in registry.filings(reg, parser=repair)]
        parser_id = repair
        log(f"Repairing parser {repair!r} with {session.model}; it must still read {', '.join(l for l, _ in others)} ...")
    else:
        parser_id = plan_id if plan_id not in registry.parsers(reg) else next(f"{plan_id}-{n}" for n in range(2, 99) if f"{plan_id}-{n}" not in registry.parsers(reg))
        log(f"Writing a parser with {session.model} (parser id {parser_id!r}) ...")
    if reviewed:
        earlier = reviewed["agent"]["parser"]
        if (earlier.get("plan") != plan_id or earlier.get("kind") != "generated" or
                earlier.get("module") != f"parsers/{parser_id}.py" or
                earlier.get("model") != session.model):
            raise SystemExit("The parser registry changed since preview; preview this filing again.")
        code = (work / "parser.py").read_text()
        count_pattern, count_scope = earlier.get("countPattern"), earlier.get("countScope")
        rep, raw, notes = generated_parser.evaluate(code, pages, count_pattern, count_scope)
        issues = list(rep.get("blocking") or []) + ([rep["error"]] if rep.get("error") else [])
        issues += signature_checker(text, reg, Texts(reg), skip, plan_id)(earlier.get("signature"))
        if compare and raw:
            issues += compare(raw)
        for label, old_pages in others:
            old_rep, _, _ = generated_parser.evaluate(code, old_pages, count_pattern, count_scope)
            if not old_rep.get("accepted"):
                issues.append(f"The reviewed parser no longer reads {label}.")
        if issues or not rep.get("accepted"):
            raise SystemExit("The reviewed parser no longer passes its checks: " + "; ".join(issues[:5]))
        current_records = generated_parser.normalize(raw, notes, filing, earlier, plan)
        try:
            preview_records = load(work / "records.json")
        except (OSError, ValueError):
            raise SystemExit("The reviewed records are missing or unreadable; preview this filing again.") from None
        if current_records != preview_records:
            raise SystemExit("The extracted records changed since preview; preview this filing again.")
        result = {"code": code, "signature": earlier["signature"], "count_pattern": count_pattern,
                  "count_scope": count_scope, "no_count_reason": earlier.get("noCountReason"),
                  "notes": reviewed["agent"]["notes"], "report": rep}
        log("The reviewed parser and its extracted records passed fresh checks without another model call.")
    else:
        transcript = []
        try:
            result = write_parser(pages, identity, plan["name"], session, signature_checker(text, reg, Texts(reg), skip, plan_id),
                                  max_turns=args.max_turns, log=log, transcript=transcript, start=start, others=others, compare=compare)
        except RuntimeError as e:
            raise SystemExit(f"The agent's parser was not accepted: {e}")
        finally:
            dump(transcript, work / "transcript.json")
        done = f"{'Repaired' if repair else 'Written'} by the GridLock ingest agent ({session.model} on Amazon Bedrock) on {dt.date.today().isoformat()} for {filing['id']}"
        about = (f'Parser for {plan["owner"]}: {filing["title"]}.\n\n{done}; it passed the checks in generated_parser.py\n'
                 f'({result["report"]["records"]} records{", and still reads " + ", ".join(l for l, _ in others) if others else ""}). '
                 f'Runs in the sandbox (pipeline/sandbox.py).\n\n{result["notes"].strip()}\n')
        header = '"""' + about.replace("\\", "\\\\").replace('"""', "'''") + '"""\n'
        code = header + strip_docstring(result["code"])
        if not generated_parser.evaluate(code, pages, result["count_pattern"], result["count_scope"])[0]["accepted"]:
            code = result["code"]
    filing["parser"] = parser_id
    entry = {"plan": plan_id, "kind": "generated", "module": f"parsers/{parser_id}.py", "signature": result["signature"],
             **({"countPattern": result["count_pattern"],
                 **({"countScope": result["count_scope"]} if result["count_scope"] else {})}
                if result["count_pattern"] else {"noCountReason": result["no_count_reason"]}),
             "model": session.model, "created": registry.parsers(reg)[repair]["created"] if repair else dt.date.today().isoformat(),
             "writtenFor": registry.parsers(reg)[repair]["writtenFor"] if repair else filing["id"],
             **({"repaired": dt.date.today().isoformat(), "repairedFor": filing["id"]} if repair else {}),
             "checked": {"records": result["report"]["records"], "withInServiceDate": result["report"]["with_in_service_date"]}}
    if reviewed:
        entry = reviewed["agent"]["parser"]
    run["agent"] = {"parser": entry, "report": result["report"], "notes": result["notes"]}
    if args.dry_run:
        (work / "parser.py").write_text(code)
        rep, raw, notes = generated_parser.evaluate(code, pages, result["count_pattern"], result["count_scope"])
        dump(generated_parser.normalize(raw, notes, filing, entry, plan), work / "records.json")
        run["reviewedParserSha256"] = sha((work / "parser.py").read_bytes())
        run["reviewRecordsSha256"] = sha((work / "records.json").read_bytes())
        log(f"Dry run: parser, records and report in {work.relative_to(ROOT)}/; nothing registered.")
        return None
    register(filing, work / "source.pdf", plan=(plan_id, new_plan) if new_plan else None, parser=(parser_id, entry), code=code)
    log(f"Registered {'plan ' + plan_id + ', ' if new_plan else ''}parser {parser_id} (pipeline/parsers/{parser_id}.py) and filing {filing['id']}.")
    return parser_id


def strip_docstring(code):
    """The code without its module docstring (a repaired parser gets a fresh header)."""
    import ast
    body = ast.parse(code).body
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
        return "\n".join(code.splitlines()[body[0].end_lineno:]).lstrip() + "\n"
    return code.lstrip()


def _with(reg, plan=None, parser=None, filing=None):
    if plan:
        reg["plans"][plan[0]] = plan[1]
    if parser:
        reg["parsers"][parser[0]] = parser[1]
    if filing:
        # kept in the order they are replayed in (registry.filings)
        reg["filings"] = registry.filings({**reg, "filings": [f for f in reg["filings"] if f["id"] != filing["id"]] + [filing]})
    return reg


def report(plan_id):
    """How the plan's projects came out in the build: placed, and near other plans' projects."""
    d = load(ROOT / "data" / "projects.json")
    mine = [p for p in d["projects"] if p.get("plan") == plan_id]
    others = [p for p in d["projects"] if p.get("plan") != plan_id and p["center"]]
    near = sorted({(p["projectId"], o["projectId"], round(haversine_mi(p["center"]["lat"], p["center"]["lon"], o["center"]["lat"], o["center"]["lon"]), 1))
                   for p in mine if p["center"] for o in others
                   if haversine_mi(p["center"]["lat"], p["center"]["lon"], o["center"]["lat"], o["center"]["lon"]) < 25}, key=lambda x: x[2])
    log(f"{plan_id}: {len(mine)} projects, {sum(1 for p in mine if p['center'])} placed on the map, {len(near)} pairs under 25 mi with other plans")
    for a, b, mi in near[:10]:
        log(f"  {a} × {b}: {mi} mi")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="URL or local path of the PDF (or of a zip holding it)")
    ap.add_argument("--find", metavar="COMPANY", help="find the company's public project list first (Bedrock agent browsing its websites)")
    ap.add_argument("--company", help="whose list to read when the PDF lists several utilities' projects (--find sets it)")
    ap.add_argument("--url", help="public address of a local file (checked to serve the same bytes when reachable)")
    ap.add_argument("--zip-member", help="which PDF in a zip")
    ap.add_argument("--title"), ap.add_argument("--edition")
    ap.add_argument("--date", type=iso_date, help="filing date, YYYY-MM-DD; needed when neither the document nor its metadata gives one")
    ap.add_argument("--skip-plan", action="append", help="act as if this plan weren't registered (with --dry-run only)")
    ap.add_argument("--dry-run", action="store_true", help="route and parse only; register nothing")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--model", help="Bedrock model id (default GRIDLOCK_BEDROCK_MODEL or GPT-6 Sol)")
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--reviewed-parser-sha256", help=argparse.SUPPRESS)
    ap.add_argument("--reviewed-records-sha256", help=argparse.SUPPRESS)
    return ingest(ap.parse_args(argv))


if __name__ == "__main__":
    main()
