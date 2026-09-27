"""The registry in data/filings.json: plans, the parsers that read them, and every filing. Standard library only.

A plan is one utility's (or group's) series of published project lists: DESC's $2M+ list, the Georgia ITS
Ten-Year Plan. Projects pair only across plans. A plan's aliases are its own other names; its members are the
member utilities of a joint plan and parent companies, whose own documents are not the plan's. A parser reads one
layout of a plan's PDFs and names the text every PDF of that layout carries (its signature), which is how a new PDF
is recognized. Built-in parsers are modules in pipeline/; parsers the ingest agent writes live in pipeline/parsers/
and run in the sandbox.
"""
import contextlib
import fcntl
import importlib
import json
import os
import re

from common import BUILD, ROOT, load

PATH = ROOT / "data" / "filings.json"
PARSER_DIR = ROOT / "pipeline" / "parsers"
LOCK = BUILD / "registry.lock"
# filing keys that share a line in data/filings.json, in order; any other key gets a line of its own
FILING_LINES = (("id", "plan", "utility", "state", "edition", "parser"), ("title",), ("date", "dateBasis"), ("url",),
                ("zipMember",), ("docket", "document"), ("file",))


def load_registry():
    return load(PATH)


def save_registry(reg):
    """Written to a temporary file and moved into place, so no reader sees half a registry."""
    tmp = PATH.with_name(f".{PATH.name}.{os.getpid()}.tmp")
    tmp.write_text(render(reg))
    os.replace(tmp, PATH)


@contextlib.contextmanager
def editing():
    """The registry to change, saved when the block ends without an error. Other ingests wait meanwhile, so an edit
    made between another's load and save isn't lost."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        reg = load_registry()
        yield reg
        save_registry(reg)


def render(reg):
    """The registry in its hand-edited layout: one line per plan, parser and watch entry, grouped lines per filing."""
    j = lambda v: json.dumps(v, ensure_ascii=False)
    out = ["{"]
    blocks = []
    for key, val in reg.items():
        if key == "filings":
            items = []
            for f in val:
                rows, done = [], set()
                for group in FILING_LINES:
                    keys = [k for k in group if k in f]
                    if keys:
                        rows.append(", ".join(f"{j(k)}: {j(f[k])}" for k in keys))
                        done.update(keys)
                rows += [f"{j(k)}: {j(v)}" for k, v in f.items() if k not in done]
                items.append("    {\n" + ",\n".join("      " + r for r in rows) + "\n    }")
            blocks.append(f"  {j(key)}: [\n" + ",\n".join(items) + "\n  ]")
        elif isinstance(val, dict):
            blocks.append(f"  {j(key)}: {{\n" + ",\n".join(f"    {j(k)}: {j(v)}" for k, v in val.items()) + "\n  }")
        else:
            blocks.append(f"  {j(key)}: {j(val)}")
    out.append(",\n".join(blocks))
    out.append("}")
    return "\n".join(out) + "\n"


def plans(reg=None):
    return (reg or load_registry())["plans"]


def parsers(reg=None):
    return (reg or load_registry())["parsers"]


def edition_key(f):
    """Where a filing sits in its plan's series: by the years its edition names (first, then last), then its date.
    An edition that names no year counts as its date's year."""
    years = [int(y) for y in re.findall(r"\b(?:19|20)\d\d\b", f.get("edition") or "")] or [int(f["date"][:4])]
    return min(years), max(years), f["date"]


def filings(reg=None, plan=None, parser=None):
    """Filings oldest first, optionally only one plan's or one parser's. Each plan's filings are in edition order, and
    the plans' series are interleaved by date: an older edition registered late (a backfill, or a PDF whose date is
    later than its edition) never becomes its plan's newest."""
    series = {}
    for f in sorted((reg or load_registry())["filings"], key=edition_key):
        series.setdefault(f["plan"], []).append(f)
    heads, fs = list(series.values()), []
    while heads:
        s = min(heads, key=lambda s: s[0]["date"])
        fs.append(s.pop(0))
        heads = [s for s in heads if s]
    return [f for f in fs if (plan is None or f["plan"] == plan) and (parser is None or f["parser"] == parser)]


def current(reg=None):
    """{plan id: its newest filing}, in registry order; plans with no filing yet are left out."""
    reg = reg or load_registry()
    out = {}
    for p in plans(reg):
        fs = filings(reg, plan=p)
        if fs:
            out[p] = fs[-1]
    return out


def signature_matches(text, reg=None, skip_plans=()):
    """Parsers whose every signature pattern occurs in the text."""
    hits = []
    for pid, p in parsers(reg).items():
        if p["plan"] in skip_plans or not p.get("signature"):
            continue
        try:
            if all(signature_hit(s, text) for s in p["signature"]):
                hits.append(pid)
        except ValueError as e:
            raise ValueError(f"parser {pid!r} has an unusable signature: {e}") from e
    return hits


def signature_hit(pattern, text):
    """Check even model-written regexes in a time-limited subprocess, never in the ingest process."""
    import sandbox
    spans, error = sandbox.find(pattern, [text])
    if error:
        raise ValueError(error)
    return bool(spans)


def company_key(name):
    """A company name reduced for comparison: case, punctuation and corporate suffixes dropped."""
    s = re.sub(r"[^a-z0-9& ]", " ", (name or "").lower()).replace("&", " and ")
    s = re.sub(r"\b(inc|llc|corp|corporation|company|co|the)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _plan_named(names, reg, skip_plans, fields):
    keys = {company_key(n) for n in names if n} - {""}
    for pid, p in plans(reg).items():
        if pid not in skip_plans and keys & {company_key(a) for f in fields for a in ([p[f]] if isinstance(p.get(f), str) else p.get(f, []))}:
            return pid
    return None


def plan_for_company(names, reg=None, skip_plans=()):
    """The plan one of these company names is (by its own name, owner or an alias), or None."""
    return _plan_named(names, reg, skip_plans, ("name", "owner", "aliases"))


def plan_with_member(names, reg=None, skip_plans=()):
    """The plan that lists one of these companies as a member utility or parent, or None."""
    return _plan_named(names, reg, skip_plans, ("members",))


def parse_filing(f, reg=None):
    """(records, removed) for one filing, from the parser the registry names for it. removed is the filing's own
    list of cancelled and completed projects when its parser reads one (Georgia's Tables 3 and 4), else {}."""
    reg = reg or load_registry()
    p = parsers(reg)[f["parser"]]
    if p["kind"] == "builtin":
        return importlib.import_module(p["module"]).parse_filing(f)
    import generated_parser
    return generated_parser.parse_filing(f, p, plans(reg)[p["plan"]]), {}
