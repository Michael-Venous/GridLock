"""Parsers the ingest agent writes: run them in the sandbox, check what they return against the PDF, and turn it into
pipeline records (the same shape parse_desc and parse_ga produce). Standard library only.

A generated parser returns raw fields as the filing prints them (RAW_FIELDS). A record's entry starts at its name (or
ID) on its page and runs to where the next entry starts, onto the next page only when it reaches the end of its own. A
short value (an ID, a date, a cost, a zone) must be printed there as one phrase, or fill whole cells of a table row keyed
by the project's ID or name; prose must be printed there word for word (hyphenation may split a few words, never a
number). So a parser that misreads the layout, or gives a project its neighbour's values, fails the checks instead of
publishing a wrong name, date or cost. The record count is checked against a marker the document prints once per
project, each match in its own record's entry; a newer edition must not read far fewer records or dates than the one
the parser was checked on. Dates and costs are read here, by one set of rules for every utility.
"""
import datetime as dt
import difflib
import math
import re
from bisect import bisect_left
from collections import namedtuple

import sandbox
from common import BUILD, RAW, ROOT, dump, filings, iso, pdf_pages, read_date

# field: (types, how it is found) — "phrase": one phrase in the entry, or whole cells of the project's table row;
# "prose": its words in the entry (a few may be split by hyphenation, never a number); None: checked another way
RAW_FIELDS = {
    "project_id": ((str,), "phrase"), "name": ((str,), "phrase"), "description": ((str,), "prose"), "need": ((str,), "prose"),
    "status": ((str,), "phrase"), "in_service": ((str,), "phrase"), "start": ((str,), "phrase"), "cost_raw": ((str,), "phrase"),
    "cost_total": ((int, float), None), "cost_by_year": ((dict,), None), "zone": ((str,), "phrase"), "sponsor": ((str,), "phrase"),
    "state": ((str,), None), "change": ((str,), "prose"), "page": ((int,), None), "item": ((str,), None),
    "also_printed": ((list,), None),
}
# fields a filing may print twice for one project (a summary table row and a detail page), checked against each other
ALSO = {"in_service": "in-service date", "start": "start date", "cost_raw": "cost"}
REQUIRED = ("name", "page")
STATES = {"AL": "Alabama", "FL": "Florida", "GA": "Georgia", "NC": "North Carolina", "SC": "South Carolina", "TN": "Tennessee",
          "VA": "Virginia", "MS": "Mississippi", "KY": "Kentucky", "WV": "West Virginia", "MD": "Maryland", "DE": "Delaware",
          "LA": "Louisiana", "AR": "Arkansas", "TX": "Texas", "OK": "Oklahoma", "MO": "Missouri", "IN": "Indiana", "OH": "Ohio",
          "IL": "Illinois", "PA": "Pennsylvania", "NY": "New York", "NJ": "New Jersey"}
MILES = re.compile(r"(?:approx\.?\s*|~)?(\d+(?:\.\d+)?)\s*(?:-\s*)?(?:circuit\s+)?mi(?:les?)?\b", re.I)
MONEY = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|m|mm|million|thousand|b|billion)?\b", re.I)
# words and numbers; a number keeps its separators, so '4,200' is not part of '4,200,000' nor '1/12/2027' of '12/1/2027'
TOKEN = re.compile(r"[A-Za-z0-9]+(?:[.,/][0-9]+)*")
# pdftotext -layout sets table cells apart by two or more spaces
CELL = re.compile(r"\S+(?: \S+)*")
# a unit the document states for its amounts; '$2M and above' is an amount, not a unit
THOUSANDS = re.compile(r"\(\s*\$?\s*000'?s?\s*\)|\$\s*000'?s?\b(?!,000)|\bin\s+(?:\$\s*)?(?:thousands|000'?s)\b|\bthousands\s+of\s+dollars"
                       r"|\(\s*\$?\s*(?:in\s+)?thousands\s*\)|\$\s*thousands\b|\(\s*\$\s*k\s*\)|\$\s*k\b(?![\d.])", re.I)
MILLIONS = re.compile(r"\bin\s+(?:\$\s*)?millions\b|\bmillions\s+of\s+dollars|\(\s*\$?\s*(?:in\s+)?millions?\s*\)|\$\s*millions?\b"
                      r"|\(\s*\$\s*mm?\s*\)|\$\s*mm?\b(?![\d.])|\$\s*000,000\b", re.I)
DATE_TEXT = (r"\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}/\d{4}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2},?\s+)?\d{4}"
             r"|q[1-4]\s*\d{4}|(?:19|20)\d\d")
DATE_LABEL = (r"in[- ]?service|need\s+date|(?:scheduled|expected|planned|anticipated)\s+to\s+be\s+(?:completed|placed\s+in\s+service|in\s+service|energized)"
              r"|to\s+be\s+completed|completion|energiz")
# an in-service date as an entry prints it: a label, then a date later in the sentence
PRINTED_DATE = re.compile(rf"(?:{DATE_LABEL})[^.]{{0,60}}?(?<![\d/])(?:{DATE_TEXT})(?![\d/])", re.I)
# a status or list heading for projects that are no longer planned
STATUS_DONE = re.compile(r"^\W*(?:project\s+)?(?:cancel+(?:ed|ation)|withdrawn|completed?\b(?!\s+(?:by|in|date))|removed|retired"
                         r"|(?:in[- ]service|energized)\W*$)", re.I)
HEADING_DONE = re.compile(r"\b(?:cancel+ed|withdrawn|(?<!to be )completed|removed|retired|placed\s+in[- ]service)\b", re.I)
# a line that heads a list: a numbered table or lettered section, or a short line naming projects
HEADING = re.compile(r"^\s*(?:Table\s+\d+\b|[A-Z]\.\s+[A-Z]|[IVX]+\.\s+[A-Z])")
# field labels: a count_pattern built on one counts only the projects that print that field
FIELD_LABEL = re.compile(r"in[- ]?service|need\s+date|complet|energiz|start\s+date|\bcost|budget|estimat|\$", re.I)

MIN_DATED_SHARE = 0.5     # at least half the records need a readable in-service date
MAX_DATED_DROP = 0.25     # a later edition may lose at most this share of dated records against the edition checked
MIN_COUNT_RATIO = 0.5     # ...and must keep at least half its record count


def tokens(s):
    return [t.lower() for t in TOKEN.findall(s or "")]


def money(raw):
    """Dollars in a printed amount: '$1,234,567', '$12.5M', '3.2 million'. None if it holds no number."""
    m = MONEY.search(raw or "")
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    return v * {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}.get(unit, 1)


def units(text):
    """Multipliers the text states for its amounts: {1e3} for '($000)' or '$ in thousands', {1e6} for '$M' or 'millions'."""
    return {u for u, rx in ((1e3, THOUSANDS), (1e6, MILLIONS)) if rx.search(text)}


# a phrase found on a page: first and last token, the lines it covers, whether it starts a table cell (lead) and fills
# whole cells (whole)
Occ = namedtuple("Occ", "start end lines lead whole")


class Page:
    """A page's text as one stream of tokens, with each token's line, columns and table cell."""

    def __init__(self, text):
        self.text, self.lines = text, text.split("\n")
        self.tok, self.line, self.col, self.off, self.cell = [], [], [], [], []
        self.cells, self.line_cells, self.index = [], [], {}   # cell: (line, first col, end col, first token, last token)
        off = 0
        for li, ln in enumerate(self.lines):
            ids = []
            for m in CELL.finditer(ln):
                first = len(self.tok)
                for t in TOKEN.finditer(ln, m.start(), m.end()):
                    self.index.setdefault(t.group().lower(), []).append(len(self.tok))
                    self.tok.append(t.group().lower())
                    self.line.append(li)
                    self.col.append((t.start(), t.end()))
                    self.off.append(off + t.start())
                    self.cell.append(len(self.cells))
                if len(self.tok) > first:
                    ids.append(len(self.cells))
                    self.cells.append((li, m.start(), m.end(), first, len(self.tok) - 1))
            self.line_cells.append(ids)
            off += len(ln) + 1

    def find(self, vt, wrapped=True):
        """Occurrences of the token sequence vt: contiguous in reading order (across line ends, as prose wraps), or with
        wrapped, a table cell continued in the same column on the next line or the one after."""
        out, n = set(), len(vt)
        for s in self.index.get(vt[0], ()) if n else ():
            stack = [(0, s, (self.line[s],))]
            while stack:
                k, p, lines = stack.pop()
                if k == n - 1:
                    lead = self.cells[self.cell[s]][3] == s
                    out.add(Occ(s, p, frozenset(lines), lead, lead and self.cells[self.cell[p]][4] == p))
                    continue
                if p + 1 < len(self.tok) and self.tok[p + 1] == vt[k + 1]:
                    stack.append((k + 1, p + 1, lines + (self.line[p + 1],)))
                c = self.cells[self.cell[p]]
                if wrapped and c[4] == p:
                    for li in (c[0] + 1, c[0] + 2):
                        for d in (self.cells[x] for x in (self.line_cells[li] if li < len(self.lines) else ())):
                            if d[1] < c[2] and c[1] < d[2] and d[3] != p + 1 and self.tok[d[3]] == vt[k + 1]:
                                stack.append((k + 1, d[3], lines + (li,)))
        return sorted(out)

    def bounded(self, o):
        """o is a whole code, not part of a longer one ('5392 A' inside '5392 A-C')."""
        a, b = self.col[o.start][0], self.col[o.end][1]
        ln, lm = self.lines[self.line[o.start]], self.lines[self.line[o.end]]
        return not (a >= 2 and ln[a - 1] in "-–/." and ln[a - 2].isalnum()) and not (b + 1 < len(lm) and lm[b] in "-–/." and lm[b + 1].isalnum())

    def pos(self, char):
        """Index of the first token at or after a character offset."""
        return bisect_left(self.off, char)

    def text_of(self, lo, hi):
        return self.text[self.off[lo] if lo < len(self.off) else len(self.text):self.off[hi] if hi < len(self.off) else len(self.text)]


def _last_heading(lines):
    for ln in reversed(lines):
        n = len(tokens(ln))
        if (HEADING.match(ln) and n <= 16) or (re.search(r"\bprojects\b", ln, re.I) and n <= 8):
            return " ".join(ln.split())
    return None


class Layout:
    """Where each record's entry is. Its anchor is its name on its page (or its ID there, when an ID printed just before
    the name, or instead of it, starts the entry). The entry runs from the anchor to the next record's anchor on that
    page, or to a cell that holds another record's whole name or ID (the table row of a project listed elsewhere), and
    onto the next page only when it runs to the end of its own. Elsewhere, a line that starts a cell with the record's ID
    or whole name, and no other record's, is its table row, whose other whole cells may be joined in."""

    def __init__(self, raw, pages):
        self.pages = [Page(p) for p in pages]
        self.keys = {}
        for i, r in enumerate(raw if isinstance(raw, list) else []):
            if isinstance(r, dict) and isinstance(r.get("page"), int) and not isinstance(r["page"], bool) and 1 <= r["page"] <= len(pages):
                name, pid = (tokens(r.get(k)) if isinstance(r.get(k), str) else [] for k in ("name", "project_id"))
                if name or pid:
                    self.keys[i] = (r["page"] - 1, name, pid)
        # every place a record's name or ID is printed: page -> [(occurrence, record, kind)]
        self.occ = [[] for _ in pages]
        for i, (_, name, pid) in self.keys.items():
            for P, pg in enumerate(self.pages):
                self.occ[P] += [(o, i, "name") for o in pg.find(name, False)] if name else []
                self.occ[P] += [(o, i, "id") for o in pg.find(pid, False) if pg.bounded(o)] if pid else []
        self.anchor = {i: a for i in self.keys if (a := self._anchor(i))}
        self.region = {i: self._region(i) for i in self.anchor}
        self.rows = self._rows()
        self._words, self._headings = {}, {}

    def _same(self, i, j, kind):
        """Record j's name or ID (kind) is one of record i's own keys (the same project printed twice, or i itself)."""
        k = self.keys[j][1 if kind == "name" else 2]
        return k in (self.keys[i][1], self.keys[i][2])

    def _anchor(self, i):
        P, name, pid = self.keys[i]
        pg = self.pages[P]
        # a heading or cell holding just the name beats one it starts, which beats a mention in prose
        rank = lambda o: (not o.whole, not o.lead, o.start)
        names = sorted(pg.find(name), key=rank) if name else []
        ids = sorted((o for o in pg.find(pid, False) if pg.bounded(o)), key=rank) if pid else []
        if names:
            o = names[0]
            before = [d.start for d in ids if d.start < o.start and pg.line[o.start] - pg.line[d.start] <= 2]
            return (max(before) if before else o.start), o.end, "name"
        if ids:
            return ids[0].start, ids[0].end, "id"
        c = self._title(i, pg, name)
        return (c[3], c[4], "title") if c else None

    def _title(self, i, pg, name):
        """A cell on the page that titles the entry a little differently than the name the parser took from a summary
        table: a word or two (one in five) added or dropped, never a number, and not another record's name. A name of
        fewer than four words is too short to tell a retitling from another project."""
        if len(name) < 4:
            return None
        others = {tuple(n) for j, (_, n, _) in self.keys.items() if j != i}
        for c in pg.cells:
            ct = pg.tok[c[3]:c[4] + 1]
            if tuple(ct) in others:
                continue
            ops = difflib.SequenceMatcher(None, name, ct, autojunk=False).get_opcodes()
            odd = [t for op, a, b, x, y in ops if op != "equal" for t in name[a:b] + ct[x:y]]
            if len(odd) <= max(1, len(name) // 5) and not any(re.search(r"\d", t) for t in odd):
                return c
        return None

    def _starts(self, P):
        return [a[0] for i, a in self.anchor.items() if self.keys[i][0] == P]

    def _cuts(self, i, P, after):
        """Where another record's whole name or ID fills a cell on page P, after token `after`."""
        return [o.start for o, j, kind in self.occ[P] if o.whole and o.start > after and not self._same(i, j, kind) and self._distinct(j, kind)]

    def _distinct(self, j, kind):
        """A key specific enough to mark a row: several words, or one of three or more characters (not a bare '7')."""
        k = self.keys[j][1 if kind == "name" else 2]
        return len(k) > 1 or len(k[0]) > 2

    def _region(self, i):
        """[(page, first token, end token)]: the record's own entry."""
        P = self.keys[i][0]
        a, e, _ = self.anchor[i]
        ends = [s for s in self._starts(P) if s > a] + self._cuts(i, P, e)
        if ends or P + 1 == len(self.pages):
            return [(P, a, min(ends, default=len(self.pages[P].tok)))]
        # it runs to the end of its page: it continues on the next, up to the first entry there
        ends = self._starts(P + 1) + self._cuts(i, P + 1, -1)
        return [(P, a, len(self.pages[P].tok)), (P + 1, 0, min(ends, default=len(self.pages[P + 1].tok)))]

    def _rows(self):
        """{record: [(page, line, key occurrence, key kind)]}: table rows keyed by each record."""
        keyed = {}
        for P, occ in enumerate(self.occ):
            for o, j, kind in occ:
                if o.lead and len(o.lines) == 1:
                    keyed.setdefault((P, min(o.lines)), []).append((o, j, kind))
        out = {}
        for (P, li), here in keyed.items():
            if len(self.pages[P].line_cells[li]) < 2:
                continue
            for o, i, kind in here:
                # a row carries one project's key at the start of a cell (another's may sit inside a cell: 'replaces 21319')
                if not any(not self._same(i, j, k) and not (o.start <= o2.start and o2.end <= o.end) for o2, j, k in here):
                    out.setdefault(i, []).append((P, li, o, kind))
        # a key that starts that many lines is too common to mark a row
        return {i: rows for i, rows in out.items() if len({(P, li) for P, li, _, _ in rows}) <= 20}

    def phrase(self, i, value, page=None, heading=False):
        """The page (0-based) where record i's value is printed as one phrase: in its entry, as whole cells of its table
        row, or (heading) filling a cell above its anchor on its page, as a section heading does. None if nowhere."""
        vt = tokens(value)
        for P, lo, hi in self.region.get(i, ()):
            if page in (None, P) and any(lo <= o.start and o.end < hi for o in self.pages[P].find(vt)):
                return P
        for P, li, key, _ in self.rows.get(i, ()):
            if page in (None, P) and any(o.whole and min(o.lines) <= li <= max(o.lines) and (o.end < key.start or o.start > key.end)
                                         for o in self.pages[P].find(vt)):
                return P
        if heading and i in self.anchor:
            P = self.keys[i][0]
            if page in (None, P) and any(o.whole and o.end < self.anchor[i][0] for o in self.pages[P].find(vt)):
                return P
        return None

    def heading(self, i):
        """The heading the record is listed under: the nearest list heading above its anchor, on its page or before."""
        if i not in self.anchor:
            return None
        P = self.keys[i][0]
        pg = self.pages[P]
        return _last_heading(pg.lines[:pg.line[self.anchor[i][0]]]) or next(
            (h for h in (self._page_heading(Q) for Q in range(P - 1, -1, -1)) if h), None)

    def _page_heading(self, Q):
        if Q not in self._headings:
            self._headings[Q] = _last_heading(self.pages[Q].lines)
        return self._headings[Q]

    def words(self, i):
        """Tokens of the record's entry and of its table rows."""
        if i not in self._words:
            w = {t for P, lo, hi in self.region.get(i, ()) for t in self.pages[P].tok[lo:hi]}
            for P, li, _, _ in self.rows.get(i, ()):
                pg = self.pages[P]
                w |= {pg.tok[t] for c in pg.line_cells[li] for t in range(pg.cells[c][3], pg.cells[c][4] + 1)}
            self._words[i] = w
        return self._words[i]

    def text(self, i, rows=False):
        parts = [self.pages[P].text_of(lo, hi) for P, lo, hi in self.region.get(i, ())]
        return "\n".join(parts + ([self.pages[P].lines[li] for P, li, _, _ in self.rows.get(i, ())] if rows else []))

    def describe(self, i):
        (P, lo, hi), *more = self.region[i]
        return f"page {P + 1} from its name or ID{' onto page ' + str(P + 2) if more else ' to the next entry' if hi < len(self.pages[P].tok) else ''}"

    def tie(self, spans):
        """Pair count_pattern matches with records: a match belongs to the record whose entry holds it, whose anchor it
        precedes, or whose name or ID is on its line. Returns (unpaired matches, unpaired records)."""
        cands = []
        for P, a, b in spans:
            pg, t = self.pages[P], self.pages[P].pos(a)
            lines = set(range(pg.text.count("\n", 0, a), pg.text.count("\n", 0, max(a, b - 1)) + 1))
            nxt = sorted((an[0], i) for i, an in self.anchor.items() if self.keys[i][0] == P and an[0] >= t)[:1]
            c = [i for _, i in nxt] + [i for i, reg in self.region.items() for Q, lo, hi in reg if Q == P and lo <= t < hi]
            c += [j for o, j, _ in self.occ[P] if o.lines & lines]
            cands.append(list(dict.fromkeys(c)))
        owner, got = {}, {}   # record -> match, match -> record
        for m in range(len(spans)):
            prev, queue, found = {}, [m], None
            while queue and found is None:
                x = queue.pop(0)
                for r in cands[x]:
                    if r in prev:
                        continue
                    prev[r] = x
                    if r not in owner:
                        found = r
                        break
                    queue.append(owner[r])
            while found is not None:
                x = prev[found]
                was, owner[found], got[x] = got.get(x), x, found
                found = was
        return [m for m in range(len(spans)) if m not in got], [i for i in self.keys if i not in owner]


def check(raw, pages, lay=None):
    """(problems, notes): problems are defects in the parser's output, one line each; notes are per-record issues that
    are facts about the filing (an unparseable or partial date), kept on the records."""
    if not isinstance(raw, list):
        return [f"parse() must return a list, got {type(raw).__name__}"], []
    lay = lay or Layout(raw, pages)
    problems, notes, seen = [], [], {}
    first_page = min((P for P, _, _ in lay.keys.values()), default=0)
    for i, r in enumerate(raw):
        where = f"record {i}"
        notes.append([])
        if not isinstance(r, dict):
            problems.append(f"{where}: not an object")
            continue
        where = f"record {i} ({str(r.get('name'))[:60]!r}, page {r.get('page')})"
        for k in r:
            if k not in RAW_FIELDS:
                problems.append(f"{where}: unknown field {k!r}")
        for k in REQUIRED:
            if r.get(k) in (None, ""):
                problems.append(f"{where}: missing {k}")
        for k, (types, _) in RAW_FIELDS.items():
            v = r.get(k)
            if v is not None and (not isinstance(v, types) or (isinstance(v, bool) and bool not in types)):
                problems.append(f"{where}: {k} should be {' or '.join(t.__name__ for t in types)}, got {type(v).__name__}")
        page = r.get("page")
        if not isinstance(page, int) or not 1 <= page <= len(pages):
            problems.append(f"{where}: page {page!r} is outside 1..{len(pages)}")
            continue
        if isinstance(r.get("name"), str) and r["name"] and not re.search(r"[A-Za-z]", r["name"]):
            problems.append(f"{where}: name {r['name']!r} has no words")
        if isinstance(r.get("project_id"), str) and r["project_id"].strip() and not re.search(r"[A-Za-z0-9]", r["project_id"]):
            problems.append(f"{where}: project_id {r['project_id']!r} has no letters or digits")
        if i not in lay.anchor:
            problems.append(f"{where}: neither its name nor its project_id is printed on page {page}, where its entry should start")
            continue
        span = lay.describe(i)
        for k, (types, rule) in RAW_FIELDS.items():
            v = r.get(k)
            if rule is None or not isinstance(v, str) or not tokens(v):
                continue
            absent = [t for t in tokens(v) if t not in lay.words(i)]
            # a name the page titles a little differently (see Layout._title) must key a table row as printed
            if k == "name" and lay.anchor[i][2] == "title" and any(kind == "name" for *_, kind in lay.rows.get(i, ())):
                continue
            if rule == "phrase" and lay.phrase(i, v, heading=k == "status") is None:
                problems.append(f"{where}: {k} {v[:80]!r} is not printed as one phrase in its entry ({span}), nor as whole cells of a "
                                f"table row keyed by its project_id or name" + (f"; not there at all: {absent[:8]}" if absent else ""))
            # hyphenation at line ends splits a few words, never a number
            elif rule == "prose" and (any(re.search(r"\d", t) for t in absent) or len(absent) > max(1, 0.05 * len(tokens(v)))):
                problems.append(f"{where}: {k} has words not in its entry ({span}) nor on a table row keyed by its project_id or name: {absent[:8]}")
        state = r.get("state")
        if isinstance(state, str) and state:
            if state not in STATES:
                problems.append(f"{where}: state {state!r} is not a two-letter state code")
            # the code as a cell of its own or after a city ('Aiken, SC'), not the word 'in' or 'IN SERVICE'
            elif not re.search(rf"(?m)(?:^\s*|\s\s|,\s?){state}(?=\s\s|\s*$|\s\d{{5}}\b|[,.;)])", lay.text(i, rows=True)) \
                    and lay.phrase(i, STATES[state]) is None:
                problems.append(f"{where}: state {state!r} is not printed in its entry, as a code of its own ('SC', 'Aiken, SC') or the state's name")
        factor = cost_problems(r, i, lay, pages, first_page, where, problems)
        by_year_problems(r, i, lay, pages, first_page, factor, where, problems, notes[-1])
        if isinstance(r.get("in_service"), str) and r["in_service"].strip():
            pass
        elif m := PRINTED_DATE.search(lay.text(i)):
            problems.append(f"{where}: no in_service, but its entry prints one: {' '.join(m.group().split())!r}")
        else:
            notes[-1].append({"level": "warn", "msg": "the filing gives no in-service date for this project"})
        for k, end in (("in_service", True), ("start", False)):
            if isinstance(r.get(k), str) and r[k].strip():
                d, issue = read_date(r[k], end=end)
                if issue:
                    notes[-1].append({"level": "error" if d is None else "info", "msg": f"{'in-service' if end else 'start'} {issue}"})
        also = r.get("also_printed")
        for a in also if isinstance(also, list) else []:
            problem, note = second_printing(r, i, a, lay)
            if problem:
                problems.append(f"{where}: {problem}")
            if note:
                notes[-1].append(note)
        problems += [f"{where}: {p}" for p in no_longer_planned(r, i, lay)]
        if isinstance(r.get("name"), str):
            key = (key_of(r), tuple(tokens(r["name"])))
            if key in seen:
                problems.append(f"{where}: the same project as record {seen[key]} (same {'ID and ' if r.get('project_id') else ''}name); "
                                "each project should appear once, an entry continued on the next page included")
            seen.setdefault(key, i)
    return problems, notes


def stated_units(pages, at, first_page):
    """(multipliers, page) the document states for amounts printed on page `at`: on that page, else on the nearest page
    before it back to the list's first (a table's header may be on its first page only). ({1}, None) if none."""
    for P in range(at, min(at, first_page) - 1, -1):
        if u := units(pages[P]):
            return u, P
    return {1}, None


def cost_problems(r, i, lay, pages, first_page, where, problems):
    """Check cost_total against cost_raw, in the unit the document states where the cost is printed. Returns the
    multiplier used, or None."""
    total, raw = r.get("cost_total"), r.get("cost_raw")
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        return None
    if not math.isfinite(total):
        problems.append(f"{where}: cost_total {total} is not a number")
        return None
    printed = money(raw) if isinstance(raw, str) else None
    if printed is None:
        problems.append(f"{where}: cost_total needs cost_raw, the amount as printed")
        return None
    at = lay.phrase(i, raw)
    stated, said = stated_units(pages, r["page"] - 1 if at is None else at, first_page)
    # an amount that carries its own unit ('12.5M') is in dollars once read; a stated unit applies to bare amounts
    own = bool(MONEY.search(raw).group(2))
    for u in sorted({1} if own else stated):
        if abs(total - printed * u) <= 0.005 * printed * u + 1:
            return u
    why = ("cost_raw carries its own unit" if own else "no unit (such as '$ in thousands' or '$M') is stated where it is printed, "
           "so it is dollars" if said is None else f"page {said + 1} states amounts in " +
           " or ".join({1e3: "thousands", 1e6: "millions"}[u] for u in sorted(stated)))
    problems.append(f"{where}: cost_total {total} doesn't match cost_raw {raw!r}: {why}")
    return None


def by_year_problems(r, i, lay, pages, first_page, factor, where, problems, notes):
    """cost_by_year: every year or label printed in the entry, and every amount printed there (in the cost's unit)."""
    by_year = r.get("cost_by_year")
    if not isinstance(by_year, dict):
        return
    for y, v in by_year.items():
        if not re.fullmatch(r"\d{4}|Previous|Prior|Future", str(y)) or not isinstance(v, (int, float)) or isinstance(v, bool):
            problems.append(f"{where}: cost_by_year entries must be year: number, got {y!r}: {v!r}")
            return
        if not math.isfinite(v):
            problems.append(f"{where}: cost_by_year {y!r} is {v}, not a number")
            return
    shown = {float(t.replace(",", "")) for t in lay.words(i) if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", t)}
    unit = [factor] if factor else sorted(stated_units(pages, r["page"] - 1, first_page)[0])
    for y, v in by_year.items():
        if lay.phrase(i, str(y)) is None:
            problems.append(f"{where}: cost_by_year year {y!r} is not printed in its entry")
        elif v and not any(abs(v - s * u) <= 0.005 * abs(v) + 1 for s in shown for u in unit):
            problems.append(f"{where}: cost_by_year {y!r}: {v} is not an amount printed in its entry")
    # filings don't always add up (DESC prints rows that don't), so a mismatch is kept as a note
    total, spent = r.get("cost_total"), sum(by_year.values())
    if isinstance(total, (int, float)) and math.isfinite(total) and by_year and abs(spent - total) > 0.005 * abs(total) + 1:
        notes.append({"level": "warn", "msg": f"the yearly amounts add up to ${spent:,.0f}, but the total printed is ${total:,.0f}"})


def no_longer_planned(r, i, lay):
    """A record the filing marks as cancelled, completed or withdrawn, by its status or by its list's heading."""
    out = []
    if isinstance(r.get("status"), str) and STATUS_DONE.search(r["status"]):
        out.append(f"status {r['status']!r} marks a project that is no longer planned; leave out cancelled, completed and withdrawn projects")
    head = lay.heading(i)
    if head and HEADING_DONE.search(head):
        out.append(f"it is listed under {head!r}; leave out cancelled, completed and withdrawn projects")
    return out


def period(s):
    """(first day, last day) a printed date stands for: one day for 6/1/2027, the month for June 2027."""
    return read_date(s, end=False)[0], read_date(s, end=True)[0]


def second_printing(r, i, a, lay):
    """(problem, note) for one also_printed entry: it must be printed where it says, in the record's entry or table
    row; a more precise printing must be the record's own value; two printings that disagree are a fact about the
    filing, kept as a warning."""
    if not (isinstance(a, dict) and set(a) == {"field", "value", "page"} and a["field"] in ALSO and isinstance(a["value"], str)
            and isinstance(a["page"], int) and 1 <= a["page"] <= len(lay.pages)):
        return f"also_printed entries are {{field: one of {', '.join(ALSO)}; value: as printed; page}}, got {str(a)[:120]}", None
    k, v = a["field"], a["value"]
    if lay.phrase(i, v, page=a["page"] - 1) is None:
        return f"also_printed {k} {v!r} is not printed on page {a['page']} in its entry or on a table row keyed by its project_id or name", None
    mine = r.get(k)
    if not (isinstance(mine, str) and mine.strip()):
        return f"also_printed gives a {ALSO[k]} ({v!r}) but the record's own {k} is empty; use one printing there", None
    if k == "cost_raw":
        x, y = money(mine), money(v)
        if x and y and not any(abs(x * u - y) <= 0.005 * y or abs(y * u - x) <= 0.005 * x for u in (1, 1e3, 1e6)):
            return None, {"level": "warn", "msg": f"the filing prints two costs for this project: {mine!r} and {v!r} (page {a['page']}); GridLock uses {mine!r}"}
        return None, None
    (a0, a1), (b0, b1) = period(mine), period(v)
    if not (a0 and a1 and b0 and b1):
        return None, None
    if b1 - b0 < a1 - a0:
        return f"{k} {mine!r} is less precise than {v!r} on page {a['page']}; use the more precise one and list the other in also_printed", None
    if b1 < a0 or a1 < b0:
        return None, {"level": "warn", "msg": f"the filing prints two {ALSO[k]}s for this project: {mine!r} and {v!r} (page {a['page']}); GridLock uses {mine!r}"}
    return None, None


def summarize(raw, problems):
    """What the agent sees after a run: counts, checks, and a few records to eyeball."""
    n = len(raw) if isinstance(raw, list) else 0
    recs = raw if isinstance(raw, list) else []
    dated = sum(1 for r in recs if isinstance(r, dict) and isinstance(r.get("in_service"), str) and read_date(r["in_service"])[0])
    out = {"records": n, "with_in_service_date": dated, "problems": len(problems), "first_problems": problems[:25]}
    step = max(1, n // 6)
    out["sample"] = [recs[i] for i in sorted({*range(0, n, step), n - 1}) if 0 <= i < n][:8]
    for k in RAW_FIELDS:
        filled = sum(1 for r in recs if isinstance(r, dict) and r.get(k) not in (None, "", {}))
        if filled:
            out.setdefault("fields_filled", {})[k] = filled
    return out


def count_problems(count_pattern, count_scope, raw, pages, lay, rep):
    """Cross-check the record count against the document's own per-project marker, found in the sandbox. Each match
    must pair with a record (in its entry, just before its anchor, or on a line with its name or ID), and none may be a
    field label, which a project lacking that field wouldn't print."""
    spans, err = sandbox.find(count_pattern, pages, count_scope)
    if err:
        return [f"count_pattern: {err}"]
    n = len(raw) if isinstance(raw, list) else 0
    rep["count_pattern_matches"] = len(spans)
    out = []
    if len(spans) != n:
        rep["count_mismatch"] = f"count_pattern matches {len(spans)} times{' on the pages count_scope selects' if count_scope else ''} but the parser returned {n} records"
        out.append(rep["count_mismatch"])
    said = lambda m: f"{' '.join(pages[spans[m][0]][spans[m][1]:spans[m][2]].split())[:60]!r} (page {spans[m][0] + 1})"
    labels = [m for m in range(len(spans)) if FIELD_LABEL.search(pages[spans[m][0]][spans[m][1]:spans[m][2]])]
    if labels:
        out.append(f"count_pattern matches a field label, e.g. {said(labels[0])}; match each project's own marker (its number, ID "
                   "or title line), which a project prints whether or not it gives that field")
    matches, recs = lay.tie(spans)
    out += [f"count_pattern matches {said(m)} where no record's entry is (a project the parser missed?)" for m in matches[:5]]
    out += [f"record {i} ({str(raw[i].get('name'))[:60]!r}, page {raw[i]['page']}) has no count_pattern match in or just before its entry"
            for i in recs[:5]]
    return out


def evaluate(code, pages, count_pattern=None, count_scope=None, registered=None):
    """Run and check a parser. count_pattern matches once per project (only on pages count_scope matches, if given);
    registered is the parser's `checked` numbers from the edition it was checked on, which a later edition may not fall
    far below. Returns (report, raw records or None, per-record notes)."""
    raw, err = sandbox.run(code, pages)
    if err:
        return {"error": err, "accepted": False}, None, []
    lay = Layout(raw, pages)
    problems, notes = check(raw, pages, lay)
    rep = summarize(raw, problems)
    blocking = list(problems)
    n, dated = rep["records"], rep["with_in_service_date"]
    if isinstance(raw, list) and not raw:
        blocking.append("no records")
    if count_pattern:
        blocking += count_problems(count_pattern, count_scope, raw, pages, lay, rep)
    if n and not dated:
        blocking.append("no record has a readable in-service date; check which field holds it")
    elif dated < MIN_DATED_SHARE * n:
        blocking.append(f"only {dated} of {n} records have a readable in-service date; check which field holds it")
    if registered and registered.get("records"):
        n0, d0 = registered["records"], registered.get("withInServiceDate") or 0
        if n < MIN_COUNT_RATIO * n0:
            blocking.append(f"{n} records, where the edition this parser was checked on had {n0}; has the layout changed?")
        if n and dated / n < d0 / n0 - MAX_DATED_DROP:
            blocking.append(f"{dated} of {n} records have a readable in-service date, where the edition this parser was checked on "
                            f"had {d0} of {n0}; has the layout changed?")
    rep["accepted"] = not blocking
    rep["blocking"] = blocking[:25]
    return rep, raw, notes


FOOTNOTE = re.compile(r"[\s*†‡§¶⁰¹²³⁴⁵⁶⁷⁸⁹]+")


def key_of(r):
    """A project's key across editions: its ID without spacing, case or footnote marks ('A-101*' is A-101), else its name."""
    pid = r.get("project_id") if isinstance(r.get("project_id"), str) else ""
    pid = re.sub(r"^\W+|\W+$", "", FOOTNOTE.sub("", pid)).upper()
    return pid or re.sub(r"[^A-Z0-9]+", "-", r["name"].upper()).strip("-")


def window_start(r, isd, by_year, issues):
    """(start, basis): the filing's start date, else the first budget year with spend. One after the in-service date
    is dropped with a warning, and the next is tried."""
    tried = []
    if r.get("start"):
        tried.append((read_date(r["start"], end=False)[0], "start", "Start date in the filing → in-service date"))
    spent = sorted(int(y) for y, v in by_year.items() if y.isdigit() and v)
    if any(y.isdigit() for y in by_year) and (by_year.get("Previous") or by_year.get("Prior")):
        spent = [min(int(y) for y in by_year if y.isdigit()) - 1]
    if spent:
        tried.append((dt.date(spent[0], 1, 1), "first budget year with spend", "first budget year with spend → in-service date"))
    for d, what, basis in tried:
        if d and isd and d > isd:
            issues.append({"level": "warn", "msg": f"{what} {d} is after in-service date {isd}"})
        elif d:
            return d, basis
    late = [what for d, what, _ in tried if d and isd and d > isd]
    return None, (f"the filing's {' and '.join(late)} fall{'s' if len(late) == 1 else ''} after its in-service date" if late
                  else "no start date or yearly budget in the filing")


def normalize(raw, notes, filing, parser, plan):
    """Pipeline records from checked raw records."""
    prefix = filing["plan"].upper()
    keys = [key_of(r) for r in raw]
    out = []
    for n, (r, issues) in enumerate(zip(raw, notes), 1):
        key = keys[n - 1]
        isd, _ = read_date(r.get("in_service") or "", end=True)
        by_year = {str(k): v for k, v in (r.get("cost_by_year") or {}).items()}
        start, basis = window_start(r, isd, by_year, issues)
        cost = r.get("cost_total")
        miles = [float(x) for x in MILES.findall(f"{r['name']} {r.get('description') or ''}")]
        out.append({
            "uid": f"{prefix}:{key}" + (f":{n}" if keys.count(key) > 1 else ""), "key": key, "plan": filing["plan"],
            "utility": r.get("sponsor") or plan["name"], "owner": plan["owner"], "state": r.get("state") or plan["state"],
            "name": re.sub(r"\s+", " ", r["name"]).strip(), "project_id": r.get("project_id") or r.get("item") or key,
            "status": r.get("status") or "Planned",
            "description": re.sub(r"\s+", " ", r.get("description") or "").strip(), "need": re.sub(r"\s+", " ", r.get("need") or "").strip(),
            "isd": iso(isd), "isd_raw": r.get("in_service"),
            "window": {"start": iso(start), "end": iso(isd), "basis": basis},
            "cost": {"total": round(cost) if isinstance(cost, (int, float)) else None, "by_year": by_year,
                     "basis": f"{plan['name']} published estimate" if cost else "not published in the filing"},
            "miles": max(miles) if miles else None,
            **({"zone": r["zone"], "zone_name": None} if r.get("zone") else {}),
            **({"change": re.sub(r"\s+", " ", r["change"]).strip()} if r.get("change") else {}),
            "source": {"doc": filing["title"], "url": filing["url"], "page": r["page"], "item": r.get("item") or f"entry {n}"},
            "issues": issues,
        })
    return out


def checked_against(filing, parser):
    """The parser's `checked` numbers when this filing is newer than the edition they were taken on; an older edition
    was checked when the parser was written or repaired, and may be smaller."""
    ref = parser.get("repairedFor") or parser.get("writtenFor")
    import registry
    editions = {f["id"]: registry.edition_key(f) for f in filings()}
    if not parser.get("checked") or filing["id"] == ref or (ref in editions and registry.edition_key(filing) <= editions[ref]):
        return None
    return parser["checked"]


def parse_filing(filing, parser, plan):
    """Records for one filing read by an agent-written parser. A parser that no longer passes its checks on a filing
    (or, on a newer edition, reads far fewer records or dates than on the one it was checked on) stops the build rather
    than publishing records it can't vouch for."""
    sandbox.require_bubblewrap()
    code = (ROOT / "pipeline" / parser["module"]).read_text()
    pages = pdf_pages(RAW / filing["file"])
    rep, raw, notes = evaluate(code, pages, parser.get("countPattern"), parser.get("countScope"), checked_against(filing, parser))
    if not rep["accepted"]:
        raise RuntimeError(f"{parser['module']} failed its checks on {filing['file']}: {rep.get('error') or rep['blocking']}")
    recs = normalize(raw, notes, filing, parser, plan)
    dump(recs, BUILD / f"{filing['parser']}_{filing['edition']}.json")
    print(f"{plan['name']} {filing['edition']}: {len(recs)} projects, {sum(len(r['issues']) for r in recs)} issues (agent-written parser)")
    return recs
