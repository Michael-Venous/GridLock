"""The ingest agent: a model on Amazon Bedrock identifies a PDF and, when no registered parser reads it, writes one.

identify() says whose project list a PDF is. write_parser() is a tool-use loop: the model reads pages, searches the
text, runs draft parsers in the sandbox and sees what the checks in generated_parser.py make of the output, and ends
by submitting a parser that passes them together with a signature that recognizes this layout. It never sees the
network or the filesystem; the parser it writes never sees the model.
"""
import html
import json
import re
import subprocess
import sys

import generated_parser
from bedrock import CUT_OFF, STOPPED

MAX_TOOL_CHARS = 30000
RESTART_AT_CHARS = 600000
SEARCH_TIMEOUT_S = 10

IDENTIFY = {
    "name": "identify", "description": "Report what this document is and whose projects it lists.",
    "inputSchema": {"json": {"type": "object", "properties": {
        "is_project_list": {"type": "boolean", "description": "true if the document lists individual planned (future) transmission construction projects, one entry per project, in its body or in a section or appendix of a larger document"},
        "list_pages": {"type": "string", "description": "the pages holding that list, e.g. '94-98' or '12-40, 55'; empty if there is none"},
        "reason": {"type": "string", "description": "one sentence: what the document is"},
        "company": {"type": "string", "description": "the utility or planning group whose projects these are, as the document names it"},
        "short_name": {"type": "string", "description": "a short label for it, e.g. 'DESC' or 'Santee Cooper'"},
        "owner": {"type": "string", "description": "its full company name"},
        "aliases": {"type": "array", "items": {"type": "string"}, "description": "other names of this same company the document uses (abbreviations, former names); not its parent, subsidiaries or member utilities"},
        "members": {"type": "array", "items": {"type": "string"}, "description": "the member utilities of a joint list or planning group, and the company's parent company, as the document names them; empty if none"},
        "state": {"type": "string", "description": "two-letter code of the state most of the projects are in"},
        "title": {"type": "string", "description": "the document's title as printed"},
        "edition": {"type": "string", "description": "the planning years it covers as printed, e.g. '2026-2030'; else its year"},
        "published": {"type": "string", "description": "publication or filing date as YYYY-MM-DD, only if printed in the document; else empty"},
        "existing_plan": {"type": "string", "description": "id of a registered plan this list belongs to (the same company's or group's series of lists), else empty; a member utility's or parent's own list is not its group's plan"},
        "public": {"type": "boolean", "description": "false if the project content is marked confidential or CEII; a PUBLIC or redacted edition is public"},
        "other_companies": {"type": "array", "items": {"type": "string"}, "description": "other utilities whose projects the document lists in a separate list or section of their own (not members of one joint list); empty if none"},
    }, "required": ["is_project_list", "list_pages", "reason", "company", "short_name", "owner", "aliases", "members", "state", "title", "edition", "published", "existing_plan", "public", "other_companies"]}},
}

TOOLS = [
    {"name": "read_pages", "description": "Text of pages first..last (1-based, at most 6 pages), as pdftotext -layout gives it; your parser gets exactly this text.",
     "inputSchema": {"json": {"type": "object", "properties": {"first": {"type": "integer"}, "last": {"type": "integer"}}, "required": ["first", "last"]}}},
    {"name": "search", "description": "Lines matching a Python regex across all pages, with page numbers, and the total count.",
     "inputSchema": {"json": {"type": "object", "properties": {"pattern": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["pattern"]}}},
    {"name": "run_parser", "description": "Run a draft parser on this PDF in the sandbox and get the checks' report: record count, problems, a sample.",
     "inputSchema": {"json": {"type": "object", "properties": {
         "code": {"type": "string", "description": "complete Python source defining parse(pages)"},
         "count_pattern": {"type": "string", "description": "optional regex matching each project's own ID, number or title line once; never a field label"},
         "count_scope": {"type": "string", "description": "optional regex selecting pages that contain this project's list when the marker also appears in other lists; must match every page with returned records"}},
         "required": ["code"]}}},
    {"name": "submit", "description": "Submit the finished parser. It is accepted only if it passes every check; otherwise you get the reasons.",
     "inputSchema": {"json": {"type": "object", "properties": {
         "code": {"type": "string"},
         "signature": {"type": "array", "items": {"type": "string"}, "description": "2-4 regexes of fixed text every edition of this layout carries and other utilities' documents don't (publisher, list title, entry marker). No edition years."},
         "count_pattern": {"type": "string", "description": "regex matching each project's own ID, number or title line once, so every future run can check the count"},
         "count_scope": {"type": "string", "description": "optional regex selecting only this list's pages when count_pattern also matches elsewhere; every record's page must match"},
         "no_count_reason": {"type": "string", "description": "only if no such regex exists: why"},
         "notes": {"type": "string", "description": "what the layout is and how the parser reads it, for the file header"}},
         "required": ["code", "signature", "notes"]}}},
]

SYSTEM = """You write Python parsers for GridLock, a tool that finds planned electric transmission projects of different utilities that are near each other in place and time.

Your parser reads one utility's published list of planned (future) transmission projects from its PDF text and returns one record per project. It will also be run, unchanged, on that utility's future editions of the same list, so read the layout's structure (entry markers, headings, column positions, labels), never this edition's particular values, page numbers or counts.

Contract:
- Define `parse(pages)`; pages[i] is the text of page i+1 from `pdftotext -layout`. Return a list of dicts.
- Standard library only, and only these modules: {allowed}. No files, network, eval/exec, getattr, type(), or underscore attributes; code breaking these rules is rejected before it runs.
- Deterministic and fast (under a few seconds).

Record fields (omit a field, or use null, when the document doesn't print it; never invent, infer or compute a value):
- name (required): the project's title as printed.
- page (required): 1-based page where the project's entry starts.
- in_service: the in-service / completion / need date text exactly as printed (e.g. "6/1/2027", "June 2027", "2028"), or just the phrase holding it ("scheduled to be completed in 2027"). GridLock reads the dates itself. Omit it for a project the document gives no date; list such projects anyway.
- project_id: the ID the document prints for the project.
- item: how the document labels the entry, e.g. "Project 3 of 54" or "Table 2, row 14".
- description, need (why it is needed), status, change (change from the previous edition, if the document says).
- start: construction/start date text as printed.
- cost_raw: the total cost as printed (e.g. "$12,500,000" or "12.5"); cost_total: that amount in dollars as a number (apply the table's stated units, e.g. "$ in thousands"). Only if printed.
- cost_by_year: {"2026": dollars, ...} when the document prints yearly amounts ("Previous" for prior spend).
- zone, sponsor (the member utility, when one list covers several), state (two-letter code, only if printed per project).
- also_printed: when the document prints this project's in_service, start or cost_raw a second time elsewhere (a summary table row and the project's detail page, say), the other printings as [{"field": ..., "value": verbatim, "page": ...}]. The field itself takes the more precise printing (a full date over a month). GridLock flags printings that disagree; don't choose between them silently.
Use values verbatim from the text (collapsing whitespace and joining wrapped lines is fine). Every word of name, project_id, status, in_service, start, cost_raw, zone and sponsor, and nearly every word of the prose fields, must appear on the entry's start page or the next page, or on a line that carries the project's project_id or its whole name (a value joined from a summary table row for the project, such as a zone, sponsor or date, is fine and worth including), or the record fails the checks.

Later editions of the same list will differ in small ways: footnote marks on labels (an asterisk after a column name), more or fewer year columns, titles wrapping differently, an extra table. Match labels and columns loosely enough to survive that.

Include only projects the document lists as planned or under way. Leave out projects it lists as completed, cancelled or withdrawn (separate tables or statuses). Leave out summary rows, totals and headers.

Work method: first look at the structure with read_pages and search; then write the parser and use run_parser until it returns every project with no problems. A count_pattern must match each project's own ID, number or title line once, including records without dates or costs. It cannot be a field label. Each match must belong to one returned record's entry; the checker rejects unmatched markers and records without a marker. If the same marker also appears in another table, pass count_scope to select only pages of this list; choose a heading or other stable page text present on every page with records, without hard-coded page numbers or edition years. If there is no per-project marker, explain why in no_count_reason. Check a sample of records against the pages, then submit a signature and the count rule. Be concise in your messages."""


TYPES = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict}


def input_problems(tool, args):
    """Why a tool call's input doesn't fit the tool's schema (Converse doesn't enforce it): missing or unknown fields,
    or a value of another type. An optional field may be null."""
    if tool is None:
        return ["no such tool"]
    schema = tool["inputSchema"]["json"]
    if not isinstance(args, dict):
        return ["the input must be an object"]
    out = [f"missing {k}" for k in schema.get("required", []) if k not in args]
    for k, v in args.items():
        spec = schema["properties"].get(k)
        if spec is None:
            out.append(f"unknown field {k!r}")
        elif v is None and k not in schema.get("required", []):
            continue
        elif not isinstance(v, TYPES[spec["type"]]) or (spec["type"] == "integer" and isinstance(v, bool)):
            out.append(f"{k} must be {'an' if spec['type'][0] in 'aeiou' else 'a'} {spec['type']}, got {type(v).__name__}")
        elif spec["type"] == "array" and not all(isinstance(x, TYPES[spec["items"]["type"]]) for x in v):
            out.append(f"{k} must be a list of {spec['items']['type']}s")
    return out


def stop_problem(resp):
    """What to tell the model about a turn that ended without finishing its tool calls, or None. A turn cut off at the
    token limit or not well formed may carry truncated input, so its calls aren't run. A turn the service stopped
    (content filter, guardrail, context window) ends the conversation."""
    stop = resp.get("stopReason")
    if stop in STOPPED:
        raise RuntimeError(f"the model's turn was stopped: {stop}")
    if stop in CUT_OFF:
        return (f"not run: your turn ended with {stop}, so its tool calls may be cut off. Send them again, shorter (a more "
                "compact parser, fewer calls per turn).")
    return None


def page_index(pages, width=110):
    """First two non-empty lines of every page, so the model sees the document's shape."""
    out = []
    for i, p in enumerate(pages, 1):
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in p.splitlines() if ln.strip()][:2]
        out.append(f"p{i}: " + " | ".join(ln[:width] for ln in lines))
    return "\n".join(out)


def clip(s, n=MAX_TOOL_CHARS):
    return s if len(s) <= n else s[:n] + f"\n[... {len(s) - n} more characters cut; ask for less]"


DATED = re.compile(r"in[- ]service|need date|scheduled to be (?:completed|in service)|completion date|energiz", re.I)


def dated_pages(pages):
    """Pages that talk about in-service or completion dates, with counts: where a project list usually is."""
    hits = [(i, len(DATED.findall(p))) for i, p in enumerate(pages, 1)]
    return ", ".join(f"p{i} ({n})" for i, n in hits if n) or "none"


def identify(pages, meta, plans, session, log=print, company=None):
    """What the PDF is and whose list it holds. company: whose list the user asked for, when a document may hold several."""
    known = "\n".join(f"- {pid}: {p['name']} ({p['owner']}, {p['state']}); also called {', '.join(p.get('aliases', []))}"
                      f"{'; members or parent (their own lists are not this plan): ' + ', '.join(p['members']) if p.get('members') else ''}"
                      for pid, p in plans.items())
    text = (f"PDF metadata: {json.dumps(meta)}\nPages: {len(pages)}\n\nRegistered plans (utilities GridLock already reads):\n{known or '(none)'}\n\n"
            f"Pages mentioning in-service or completion dates (count): {dated_pages(pages)}\n\n"
            f"Page index:\n{clip(page_index(pages), 40000)}\n\nFirst pages:\n" +
            clip("\n".join(f"=== page {i} ===\n{p}" for i, p in enumerate(pages[:3], 1)), 20000))
    if company:
        text += (f"\n\nThe user asked for {company}'s list. If the document lists several utilities' projects in separate lists or sections, "
                 f"answer every field about {company}'s own list (list_pages are its pages) and name the others in other_companies. "
                 f"If it holds no list of {company}'s projects, is_project_list is false.")
    system = ("You identify documents for GridLock, which reads utilities' public lists of planned transmission projects. "
              "Answer only from the document. A 'PUBLIC DISCLOSURE' or redacted edition is public even if it carries a CEII banner template.")
    # fields of the wrong type are asked again, never guessed: a "false" string for public would pass the CEII gate
    out = session.force_tool(system, text, IDENTIFY, check=lambda o: input_problems(IDENTIFY, o))
    out = {k: html.unescape(v) if isinstance(v, str) else [html.unescape(x) for x in v] if isinstance(v, list) else v for k, v in out.items()}
    others = ", ".join(out.get("other_companies") or [])
    log(f"identified: {out.get('company')!r}, {out.get('title')!r}, edition {out.get('edition')!r}; project list: {out.get('is_project_list')}"
        f"{' on pages ' + out['list_pages'] if out.get('list_pages') else ''}{'; also lists projects of ' + others if others else ''}")
    return out


SEARCH = """import json, re, sys
d = json.load(sys.stdin)
rx = re.compile(d["pattern"])
hits = [f"p{i}: {ln.rstrip()}" for i, p in enumerate(d["pages"], 1) for ln in p.splitlines() if rx.search(ln)]
print(json.dumps([len(hits), hits[:d["n"]]]))
"""


def search_pages(pages, pattern, max_results=80):
    """Lines matching the model's regex, with page numbers. The regex runs in a child process with a time limit: a
    pattern that backtracks without end (nested quantifiers) is stopped rather than hanging the ingest."""
    try:
        re.compile(pattern)
    except re.error as e:
        return f"invalid regex: {e}"
    n = max(1, min(int(max_results), 300))
    try:
        p = subprocess.run([sys.executable, "-I", "-c", SEARCH], input=json.dumps({"pattern": pattern, "pages": pages, "n": n}),
                           capture_output=True, text=True, timeout=SEARCH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return (f"search stopped after {SEARCH_TIMEOUT_S} s: the pattern backtracks too much (nested quantifiers such as "
                r"(\w+\s?)+); simplify it")
    if p.returncode != 0:
        return f"search failed: {(p.stderr.strip().splitlines() or ['?'])[-1][:300]}"
    total, hits = json.loads(p.stdout)
    return clip(f"{total} matching lines\n" + "\n".join(hits))


def write_parser(pages, identity, plan_name, session, signature_problems, max_turns=40, log=print, transcript=None, start=None, others=(),
                 compare=None):
    """Loop until the model submits a parser that passes the checks. Returns the submission and its report, or raises.
    To repair a registered parser that a new edition broke, start is (its code, its failure on this edition) and others
    is [(filing id, pages)] of the editions it already reads, which the repaired parser must still pass. compare(raw
    records) gives problems with the output against the plan's previous edition (a count far off, IDs gone)."""
    tool_log, latest = [], {}

    def read_pages(first, last):
        first, last = max(1, int(first)), min(len(pages), int(last))
        if last < first:
            return f"no pages in {first}..{last}; the document has {len(pages)}"
        last = min(last, first + 5)
        return clip("\n".join(f"=== page {i} ===\n{pages[i - 1]}" for i in range(first, last + 1)))

    def shorten(rep):
        rep = dict(rep)
        rep["sample"] = [{k: (v[:240] + "…" if isinstance(v, str) and len(v) > 240 else v) for k, v in r.items()} if isinstance(r, dict) else r
                         for r in rep.get("sample", [])]
        return clip(json.dumps(rep, indent=1, default=str))

    def evaluate(code, count_pattern, count_scope):
        rep, raw, _ = generated_parser.evaluate(code, pages, count_pattern, count_scope)
        for label, opages in others:
            o, _, _ = generated_parser.evaluate(code, opages, count_pattern, count_scope)
            rep.setdefault("earlier_editions", {})[label] = {"records": o.get("records"), "accepted": o["accepted"],
                                                            "problems": (o.get("blocking") or [o.get("error")])[:5]}
            if not o["accepted"]:
                rep["accepted"] = False
                rep.setdefault("blocking", []).append(f"no longer reads earlier edition {label}: {(o.get('blocking') or [o.get('error')])[0]}")
        if compare and isinstance(raw, list) and raw:
            for problem in compare(raw):
                rep["accepted"] = False
                rep.setdefault("blocking", []).append(f"against the previous edition: {problem}")
        latest.update(code=code, count_pattern=count_pattern, count_scope=count_scope, report=shorten(rep))
        return rep, raw

    def run_parser(code, count_pattern=None, count_scope=None):
        if count_scope and not count_pattern:
            return "rejected: count_scope requires count_pattern"
        rep, _ = evaluate(code, count_pattern, count_scope)
        tool_log.append(("run_parser", rep.get("records"), len(rep.get("blocking", [])), rep.get("error", "")[:80]))
        return latest["report"]

    def submit(code, signature, notes, count_pattern=None, count_scope=None, no_count_reason=None):
        if not count_pattern and not (no_count_reason or "").strip():
            return "rejected: give a count_pattern, or no_count_reason if the document has no per-project marker"
        if count_scope and not count_pattern:
            return "rejected: count_scope requires count_pattern"
        rep, raw = evaluate(code, count_pattern, count_scope)
        why = list(rep.get("blocking", [])) + ([rep["error"]] if rep.get("error") else []) + signature_problems(signature)
        tool_log.append(("submit", rep.get("records"), len(why), ""))
        if why:
            return "rejected:\n" + "\n".join(why[:30])
        done["result"] = {"code": code, "signature": signature, "count_pattern": count_pattern, "count_scope": count_scope,
                          "no_count_reason": no_count_reason,
                          "notes": notes, "report": rep}
        return "accepted"

    done = {}
    handlers = {"read_pages": read_pages, "search": lambda pattern, max_results=80: search_pages(pages, pattern, max_results),
                "run_parser": run_parser, "submit": submit}
    tools = {t["name"]: t for t in TOOLS}
    first = (f"Document: {identity['title']} ({plan_name}; edition {identity['edition']}), {len(pages)} pages.\n"
             f"Identified as: {identity['reason']}\n" +
             (f"The project list is on pages {identity['list_pages']}; read those first. Records must come only from the list.\n" if identity.get("list_pages") else "") +
             (f"The document also lists projects of {', '.join(identity['other_companies'])}. Return only {identity['company']}'s, finding its "
              "section by its headings or labels, not by page numbers.\n" if identity.get("other_companies") else "") +
             f"\nPage index:\n{clip(page_index(pages), 30000)}\n\nFirst pages:\n" +
             clip("\n".join(f"=== page {i} ===\n{p}" for i, p in enumerate(pages[:2], 1)), 15000))
    if start:
        first += (f"\n\nGridLock's registered parser for this layout fails on this edition:\n{start[1]}\n\nIts code:\n```python\n{start[0]}\n```\n\n"
                  f"Repair it so it reads this edition and still reads the earlier ones ({', '.join(l for l, _ in others)}); run_parser and "
                  "submit check all of them. Keep its signature unless it no longer fits.")
    else:
        first += "\n\nWrite the parser for this document."
    messages = [{"role": "user", "content": [{"text": first}, {"cachePoint": {"type": "default"}}]}]
    earlier = []   # conversations given up by a restart, for the transcript
    system = SYSTEM.replace("{allowed}", ", ".join(sorted(generated_parser.sandbox.ALLOWED)))
    for turn in range(1, max_turns + 1):
        if latest and sum(len(json.dumps(m)) for m in messages) > RESTART_AT_CHARS:
            # A long conversation starts over from the newest draft instead of having earlier turns edited: an edited
            # turn invalidates the thinking blocks after it (preserved thinking), and the drafts are most of the size.
            earlier += messages
            messages = [{"role": "user", "content": [{"text": first + restarted(latest)}, {"cachePoint": {"type": "default"}}]}]
            log(f"  turn {turn}: conversation restarted from the latest draft to save space")
        resp = session.converse(system, messages, tools=TOOLS)
        msg = resp["output"]["message"]
        messages.append(msg)
        cut = stop_problem(resp)
        uses = [b["toolUse"] for b in msg["content"] if "toolUse" in b]
        results, logged = [], len(tool_log)
        for u in uses:
            bad = [cut] if cut else input_problems(tools.get(u["name"]), u["input"])
            if bad:
                out, status = (cut or f"bad tool call: {'; '.join(bad)}"), "error"
            else:
                try:
                    out, status = handlers[u["name"]](**u["input"]), "success"
                except Exception as e:
                    blame = " (most likely a value of the wrong type in your parser's output)" if u["name"] in ("run_parser", "submit") else ""
                    out, status = f"{u['name']} failed{blame}: {type(e).__name__}: {e}", "error"
            results.append({"toolResult": {"toolUseId": u["toolUseId"], "content": [{"text": out}], "status": status}})
        summary = ", ".join(f"{t[0]}→{t[1]} records, {t[2]} problems{(' ' + t[3]) if t[3] else ''}" for t in tool_log[logged:])
        log(f"  turn {turn}: {', '.join(u['name'] for u in uses) or resp['stopReason']}{'; ' + summary if summary else ''}")
        if transcript is not None:
            transcript[:] = earlier + messages
        if done:
            return done["result"]
        if not uses:
            results = [{"text": cut or "Continue: use the tools, and call submit when the parser passes."}]
        # keep one moving cache point, on the newest turn (moving a cache point leaves thinking blocks valid)
        for m in messages[1:]:
            m["content"] = [b for b in m["content"] if "cachePoint" not in b]
        messages.append({"role": "user", "content": results + [{"cachePoint": {"type": "default"}}]})
    raise RuntimeError(f"no accepted parser after {max_turns} turns")


def restarted(latest):
    """The note that starts a restarted conversation: the newest draft and what the checks made of it."""
    return (f"\n\nYou have already worked on this; the conversation was restarted to save space, so earlier page reads are "
            f"gone (read again what you need). Your latest draft{' (count_pattern ' + repr(latest['count_pattern']) + ')' if latest.get('count_pattern') else ''}"
            f"{' (count_scope ' + repr(latest['count_scope']) + ')' if latest.get('count_scope') else ''}:\n"
            f"```python\n{latest['code']}\n```\n\nWhat the checks said of it:\n{latest['report']}")


def log_err(*a):
    print(*a, file=sys.stderr, flush=True)
