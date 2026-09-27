"""Parse a Georgia ITS Ten-Year Plan (GA PSC filings; see data/filings.json for each edition).

Table 2 gives zone + sponsor per TEAMS number; each project's detail page gives the title,
start date, need date, description and change-from-last-plan note. We join the two on TEAMS #.
Tables 3 (cancelled) and 4 (completed) are read only so they can be reported, never merged in.
The 2024 plan (2025-2034) and the 2025 plan (2026-2035) share this layout; the older one writes
"10 Year" for "10-Year" and prints redacted cost columns after the sponsor in Table 2.
"""
import re
from collections import Counter

import registry
from common import RAW, BUILD, dump, parse_date, pdf_pages, iso

ZONES = {"215": "Augusta area", "218": "Southeast GA", "219": "Savannah area"}
# a Table 2 row: zone, year and TEAMS #, then the title's first line, the need date and the sponsor, a code (GPC) or a
# name (Dalton). The sponsor ends the row (2025 plan), or redacted cost columns follow it (2024 plan).
ROW = re.compile(r"^\s*(\d{3})\s+(20\d\d)\s+(\d{4,6})\b(.*)$")
TRAILING = re.compile(r"(?:\s+(?:REDACTED|\$?\d[\d,]*(?:\.\d+)?[KMB]?|[*†‡]+))+\s*$")   # cost cells and footnote marks
ROW_NOYEAR = re.compile(r"^\s*(\d{3})\s+(\d{4,6})\s+(.*?)\s{2,}(\d{1,2}/\d{1,2}/\d{4})")
MILES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-\s*)?(?:circuit\s+)?miles?\b", re.I)
SPONSOR_NAMES = {"GPC": "Georgia Power", "SAV": "Georgia Power (Savannah)", "GTC": "Georgia Transmission Corp",
                 "MEAG": "MEAG Power", "DU": "Dalton Utilities", "SPC": "Southern Power"}


def sponsor_code(printed):
    """The code for a sponsor as Table 2 prints it, so every row stores a code: a code stays; a name becomes the code
    whose name in SPONSOR_NAMES it is (compared by registry.company_key), or else the one code whose name holds all its
    words (Dalton: DU). A name with a word company_key drops (Southern Company) is a whole company name, compared only
    whole: the words left (Southern) would name another company (Southern Power). A name that fits no code, or
    several, is kept as printed."""
    if printed in SPONSOR_NAMES:
        return printed
    key = registry.company_key(printed)
    whole = not set(re.findall(r"[a-z0-9]+", printed.lower())) <= set(key.split())
    fits = [c for c, n in SPONSOR_NAMES.items() if registry.company_key(n) == key] or \
           [c for c, n in SPONSOR_NAMES.items() if key and not whole and set(key.split()) <= set(registry.company_key(n).split())]
    return fits[0] if len(fits) == 1 else printed


def last_cell(rest):
    """The last cell printed on a Table 2 row (rest: the row after its TEAMS #), where the sponsor is: cost cells and
    footnote marks after it are dropped. Cells are two or more spaces apart; a cell right after a date is its own
    even one space on (6/1/2026 GPC). '' when the row ends in a date or number (no sponsor printed)."""
    body = TRAILING.sub("", rest).strip()
    words = re.split(r"\s{2,}", body)[-1].split()
    digits = [i for i, w in enumerate(words) if re.search(r"\d", w)]
    return " ".join(words[digits[-1] + 1 if digits else 0:]).rstrip("*†‡").strip()


def table2(lines, members=()):
    """{TEAMS #: {zone, year, sponsor, note}} from Table 2's lines. A row's last cell is its sponsor only when it is a
    known sponsor (sponsor_code gives a code in SPONSOR_NAMES, or it names one of members, the plan's member utilities
    in the registry) or other rows of the table print it there too; else it is title text (a row whose sponsor cell
    is blank or wrapped away), the row keeps its zone and year, its sponsor is left to the title's prefix, and note
    says why."""
    rows = [(m, last_cell(m.group(4))) for m in map(ROW.match, lines) if m]
    printed = Counter(cell for _, cell in rows if cell)
    member_keys = {registry.company_key(n) for n in members} - {""}
    out = {}
    for m, cell in rows:
        code = sponsor_code(cell) if cell else None
        known = bool(cell) and (code in SPONSOR_NAMES or registry.company_key(cell) in member_keys or printed[cell] > 1)
        out[m.group(3)] = {"zone": m.group(1), "year": m.group(2), "sponsor": code if known else None,
                           "note": None if known else ("Table 2 row prints no sponsor" if not cell else
                                                       f"Table 2 row ends in {cell!r}, not a known sponsor and not printed as one on any other row")}
    return out


def section(pages, start_pat, end_pat):
    text = "\f".join(pages)
    a = re.search(start_pat, text)
    b = re.search(end_pat, text[a.end():])
    return text[a.end(): a.end() + b.start()]


def parse_filing(filing):
    """(records, removed) for one filing; removed is its Tables 3 and 4: {teams: {status, zone, name, last_need}}."""
    edition = filing["edition"]
    pages = pdf_pages(RAW / filing["file"])

    # Table 2 -> zone, year, sponsor
    t2 = table2(section(pages, r"Table 2 Georgia ITS 10[- ]Year Plan Project List\s*\n", r"Table 3 Cancelled").split("\n"),
                registry.plans().get(filing["plan"], {}).get("members", []))

    removed = {}
    for label, a, b in (("cancelled", r"Table 3 Cancelled Projects[^\n]*\n[^\n]*Table 3[^\n]*\n", r"Table 4 Completed"),
                        ("completed", r"Table 4 Completed Projects[^\n]*\n[^\n]*\n[^\n]*Table 4[^\n]*\n", r"Table 5 ")):
        lines = section(pages, a, b).split("\n")
        frag = lambda ln: ln.strip() if ln.strip() and not ROW_NOYEAR.match(ln) and not re.search(r"Zone|TEAMS|Sponsor|Estimated|Assigned|Need Date|Table|CRITICAL|PUBLIC|disclos|policy|employees|notification|REDACTED|Year|Date", ln) else ""
        for i, ln in enumerate(lines):
            m = ROW_NOYEAR.match(ln)
            if m:
                # names wrap onto the lines just above and below the row's anchor line
                name = " ".join(x for x in (frag(lines[i - 1]) if i else "", m.group(3).strip(), frag(lines[i + 1]) if i + 1 < len(lines) else "") if x)
                removed[m.group(2)] = {"status": label, "zone": m.group(1), "name": name, "last_need": m.group(4)}

    # detail pages
    out = []
    for pno, page in enumerate(pages, 1):
        m = re.search(r"Teams # (\d+)", page)
        if not m or "Need Date" not in page:
            continue
        teams = m.group(1)
        head = page[:m.start()]
        head = head.split("employees.")[-1] if "employees." in head else head
        title = re.sub(r"\s+", " ", head).strip()
        dm = re.search(r"Need Date (\S+)\s+Start Date (\S+)", page)
        desc = re.search(r"\nDescription\s*\n(.*?)\n\s*Supporting Statement", page, re.S)
        desc = re.sub(r"\s+", " ", desc.group(1)).strip() if desc else ""
        support = re.search(r"Supporting Statement\s*\n(.*?)\n\s*Change From Previous", page, re.S)
        support = re.sub(r"\s+", " ", support.group(1)).strip() if support else ""
        chg = re.search(r"Change From Previous Ten[- ]Year Plan\s*\n\s*(.*?)\n\s*\n", page, re.S)
        chg = re.sub(r"\s+", " ", chg.group(1)).strip() if chg else ""
        issues = []
        need, e1 = parse_date(dm.group(1)) if dm else (None, "no need date on detail page")
        start, e2 = parse_date(dm.group(2)) if dm else (None, "no start date on detail page")
        for e in (e1, e2):
            if e:
                issues.append({"level": "error", "msg": e})
        if need and start and start > need:
            issues.append({"level": "warn", "msg": f"start date {start} is after need date {need}"})
        row = t2.get(teams)
        if not row:
            issues.append({"level": "warn", "msg": "detail page has no matching Table 2 row (zone/sponsor unknown)"})
        elif need and int(row["year"]) != need.year:
            issues.append({"level": "warn", "msg": f"Table 2 year {row['year']} != detail need date {need}"})
        if teams in removed:
            issues.append({"level": "warn", "msg": f"also listed as {removed[teams]['status']} in Table 3/4"})
        sponsor = (row or {}).get("sponsor") or _sponsor_from_title(title)
        if row and row["note"]:
            issues.append({"level": "warn", "msg": row["note"] + (f"; sponsor {sponsor} read from the title" if sponsor else "; sponsor unknown")})
        miles = [float(x) for x in MILES.findall(desc)]
        slip = _slip(chg)
        out.append({
            "uid": f"GA:{teams}", "key": teams, "plan": filing["plan"], "utility": sponsor or "GA-ITS",
            "owner": SPONSOR_NAMES.get(sponsor, "Georgia ITS member"), "state": "GA",
            "name": title, "project_id": f"TEAMS {teams}", "status": "Planned",
            "zone": (row or {}).get("zone"), "zone_name": ZONES.get((row or {}).get("zone")),
            "description": desc, "need": support,
            "isd": iso(need), "isd_raw": dm.group(1) if dm else None,
            "window": {"start": iso(start), "end": iso(need), "basis": "detail page Start Date → Need Date"},
            "cost": {"total": None, "by_year": {}, "basis": "redacted in public filing"},
            "miles": max(miles) if miles else None,
            "change": chg, "slip_years": slip,
            "source": {"doc": filing["title"], "url": filing["url"], "page": pno, "item": f"Teams # {teams}"},
            "issues": issues,
        })
    missing = sorted(set(t2) - {r["key"] for r in out})
    dump(out, BUILD / f"{filing['parser']}_{edition}.json")
    dump(removed, BUILD / f"{filing['parser']}_removed_{edition}.json")
    print(f"GA ITS {edition}: {len(out)} detail pages, {len(t2)} Table 2 rows, {len(missing)} Table 2 rows without detail page,"
          f" {len(removed)} cancelled/completed, {sum(len(r['issues']) for r in out)} issues")
    return out, removed


def main():
    """Every edition in the registry, newest first: {edition: (records, removed)}."""
    return {f["edition"]: parse_filing(f) for f in reversed(registry.filings(parser="ga"))}


def _sponsor_from_title(t):
    """A sponsor code the title opens with ("DU: EAST DALTON - ...")."""
    m = re.match(rf"({'|'.join(map(re.escape, SPONSOR_NAMES))}):", t)
    return m.group(1) if m else None


def _slip(chg):
    m = re.search(r"(Delayed|Advanced) from (\d{4}) to (\d{4})", chg, re.I)
    if not m:
        return None
    return int(m.group(3)) - int(m.group(2))


if __name__ == "__main__":
    main()
