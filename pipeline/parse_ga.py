"""Parse the '2025 GA ITS Ten-Year Plan (2026-2035)' (GA PSC docket 56002, filing #225600).

Table 2 gives zone + sponsor per TEAMS number; each project's detail page gives the title,
start date, need date, description and change-from-last-plan note. We join the two on TEAMS #.
Tables 3 (cancelled) and 4 (completed) are read only so they can be reported, never merged in.
"""
import re

from common import RAW, BUILD, dump, parse_date, pdf_pages, iso

URL = "https://services.psc.ga.gov/api/v1/External/Public/Get/Document/DownloadFile/225600/106866"
DOC = "2025 GA ITS Ten-Year Plan (2026-2035), GA PSC docket 56002 #225600"
ZONES = {"215": "Augusta area", "218": "Southeast GA", "219": "Savannah area"}
ROW = re.compile(r"^\s*(\d{3})\s+(20\d\d)\s+(\d{4,6})\b.*?\s(GPC|GTC|MEAG|DU|SAV|SPC)\s*$")
ROW_NOYEAR = re.compile(r"^\s*(\d{3})\s+(\d{4,6})\s+(.*?)\s{2,}(\d{1,2}/\d{1,2}/\d{4})")
MILES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-\s*)?(?:circuit\s+)?miles?\b", re.I)
SPONSOR_NAMES = {"GPC": "Georgia Power", "SAV": "Georgia Power (Savannah)", "GTC": "Georgia Transmission Corp",
                 "MEAG": "MEAG Power", "DU": "Dalton Utilities", "SPC": "Southern Power"}


def section(pages, start_pat, end_pat):
    text = "\f".join(pages)
    a = re.search(start_pat, text)
    b = re.search(end_pat, text[a.end():])
    return text[a.end(): a.end() + b.start()]


def main():
    pages = pdf_pages(RAW / "ga_its_2026-2035.pdf")

    # Table 2 -> zone, year, sponsor
    t2 = {}
    for ln in section(pages, r"Table 2 Georgia ITS 10-Year Plan Project List\s*\n", r"Table 3 Cancelled").split("\n"):
        m = ROW.match(ln)
        if m:
            t2[m.group(3)] = {"zone": m.group(1), "year": m.group(2), "sponsor": m.group(4)}

    removed = {}
    for label, a, b in (("cancelled", r"Table 3 Cancelled Projects[^\n]*\n[^\n]*Table 3[^\n]*\n", r"Table 4 Completed"),
                        ("completed", r"Table 4 Completed Projects[^\n]*\n[^\n]*\n[^\n]*Table 4[^\n]*\n", r"Table 5 ")):
        for ln in section(pages, a, b).split("\n"):
            m = ROW_NOYEAR.match(ln)
            if m:
                removed[m.group(2)] = {"status": label, "zone": m.group(1), "name": m.group(3).strip(), "last_need": m.group(4)}

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
        chg = re.search(r"Change From Previous Ten-Year Plan\s*\n\s*(.*?)\n\s*\n", page, re.S)
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
        miles = [float(x) for x in MILES.findall(desc)]
        slip = _slip(chg)
        out.append({
            "uid": f"GA:{teams}", "key": teams, "utility": sponsor or "GA-ITS",
            "owner": SPONSOR_NAMES.get(sponsor, "Georgia ITS member"), "state": "GA",
            "name": title, "project_id": f"TEAMS {teams}", "status": "Planned",
            "zone": (row or {}).get("zone"), "zone_name": ZONES.get((row or {}).get("zone")),
            "description": desc, "need": support,
            "isd": iso(need), "isd_raw": dm.group(1) if dm else None,
            "window": {"start": iso(start), "end": iso(need), "basis": "detail page Start Date → Need Date"},
            "cost": {"total": None, "by_year": {}, "basis": "redacted in public filing"},
            "miles": max(miles) if miles else None,
            "change": chg, "slip_years": slip,
            "source": {"doc": DOC, "url": URL, "page": pno, "item": f"Teams # {teams}"},
            "issues": issues,
        })
    missing = sorted(set(t2) - {r["key"] for r in out})
    dump(out, BUILD / "ga_2026-2035.json")
    dump(removed, BUILD / "ga_removed.json")
    print(f"GA ITS 2026-2035: {len(out)} detail pages, {len(t2)} Table 2 rows, {len(missing)} Table 2 rows without detail page,"
          f" {len(removed)} cancelled/completed, {sum(len(r['issues']) for r in out)} issues")
    return out


def _sponsor_from_title(t):
    m = re.match(r"(SAV|GTC|MEAG|DU|SPC):", t)
    return m.group(1) if m else None


def _slip(chg):
    m = re.search(r"(Delayed|Advanced) from (\d{4}) to (\d{4})", chg, re.I)
    if not m:
        return None
    return int(m.group(3)) - int(m.group(2))


if __name__ == "__main__":
    main()
