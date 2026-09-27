"""Parse DESC 'Planned Transmission Projects $2M and above' PDFs (SCRTP) into records + validation issues."""
import datetime as dt
import re

import registry
from common import RAW, BUILD, dump, parse_date, pdf_pages, iso

HEADER = re.compile(r"Dominion Energy South Carolina\s*\n\s*Planned Transmission Projects \$2M and above Total\s*\n\s*5 Year Budget")
MONEY = re.compile(r"(?<![\w$,])\$?\d[\d,]*")
MILES = re.compile(r"(?:approx\.?\s*|~)?(\d+(?:\.\d+)?)\s*(?:-\s*)?mi(?:les?)?\b", re.I)


def field(body, label, nxt):
    m = re.search(rf"{label}\s*\n(.*?)\n\s*(?:{nxt})", body, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def parse(filing):
    pages = pdf_pages(RAW / filing["file"])
    out = []
    for pno, page in enumerate(pages, 1):
        for m in re.finditer(r"Project (\d+) of (\d+)", page):
            body = HEADER.sub("", page[m.end():])
            nxt = re.search(r"Project \d+ of \d+", body)
            if nxt:
                body = body[:nxt.start()]
            # a project can run onto the next page
            if "Estimated Project Cost" not in body and pno < len(pages):
                body += "\n" + HEADER.sub("", pages[pno])
            out.append(record(int(m.group(1)), int(m.group(2)), body, filing, pno))
    return out


def record(n, total, body, filing, page):
    name = re.sub(r"\s+", " ", body.split("Project ID")[0]).strip()
    pid = field(body, "Project ID", "Project Description")
    desc = field(body, "Project Description", "Project Need")
    need = field(body, "Project Need", "Project Status")
    status = field(body, "Project Status", "Planned In-Service Date")
    date_raw = field(body, "Planned In-Service Date", "Estimated Project Cost")
    hdr = re.search(r"(Previous.*?Total\*?)\s*\n\s*(.+)", body)
    cols = hdr.group(1).replace("*", "").split() if hdr else []
    raw = MONEY.findall(hdr.group(2)) if hdr else []
    issues = []

    # dates: phased projects list several; the last phase is the project's in-service date
    all_dates = re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", date_raw)
    if len(all_dates) > 1:
        issues.append(("info", f"multiple in-service dates {date_raw!r}; using the last phase"))
    isd, err = parse_date(all_dates[-1] if all_dates else date_raw)
    if err:
        issues.append(("error", err))

    # costs: well-formed, the row sums to its total, and the total clears the list's $2M threshold
    spend = {}
    total_cost = None
    bad = [x for x in raw if not re.fullmatch(r"\$\d{1,3}(,\d{3})*|0", x)]
    if bad:
        issues.append(("error", f"malformed money {bad}"))
    vals = [int(x.lstrip("$").replace(",", "")) for x in raw]
    if len(vals) == len(cols) and len(vals) >= 3:
        spend = dict(zip(cols[:-1], vals[:-1]))
        total_cost = vals[-1]
        if sum(vals[:-1]) != total_cost:
            issues.append(("error", f"cost row sums to ${sum(vals[:-1]):,} but Total says ${total_cost:,}"))
        if total_cost < 2_000_000:
            issues.append(("warn", f"total ${total_cost:,} is below the list's own $2M threshold"))
    else:
        m = re.search(r"Estimated cost of (\$[\d,]+) is to be financed by the interconnection customer", re.sub(r"\s+", " ", body))
        if m:
            total_cost = int(m.group(1)[1:].replace(",", ""))
            issues.append(("info", f"no yearly budget: {m.group(1)} is financed by the interconnection customer and reimbursed later"))
        else:
            issues.append(("error", f"cost row has {len(vals)} values for {len(cols)} columns"))

    # build window: first year with spend -> in-service date. 'Previous' spend means work began earlier.
    years = [int(c) for c in cols if c.isdigit()]
    start = None
    if spend:
        if spend.get("Previous", 0) > 0:
            start = dt.date(years[0] - 1, 1, 1)
        else:
            spent = [y for y in years if spend.get(str(y), 0) > 0]
            if spent:
                start = dt.date(spent[0], 1, 1)
    if isd and start and start > isd:
        issues.append(("warn", f"spending starts {start.year} but in-service date is {isd}"))
        start = dt.date(isd.year, 1, 1)
    if isd and years and spend and isd.year < years[0] and any(spend.get(str(y), 0) for y in years):
        issues.append(("warn", f"in-service {isd} is before budget years that still carry spend"))

    miles = [float(x) for x in MILES.findall(f"{name} {desc}")]
    return {
        "uid": f"DESC:{re.sub(r'\s', '', pid).upper()}:{n}",
        "key": re.sub(r"\s", "", pid).upper(), "plan": filing["plan"],
        "utility": "DESC", "owner": "Dominion Energy South Carolina", "state": "SC",
        "name": name, "project_id": pid, "status": status, "description": desc, "need": need,
        "isd": iso(isd), "isd_raw": date_raw,
        "window": {"start": iso(start), "end": iso(isd), "basis": "first budget year with spend → in-service date"},
        "cost": {"total": total_cost, "by_year": spend, "basis": "DESC published estimate"},
        "miles": max(miles) if miles else None,
        "source": {"doc": f"DESC $2M+ list {filing['edition']}", "url": filing["url"], "page": page, "item": f"Project {n} of {total}"},
        "issues": [{"level": lv, "msg": m} for lv, m in issues],
    }


def parse_filing(filing):
    """(records, removed) for one filing. The list prints no table of dropped projects, so removed is {}."""
    recs = parse(filing)
    dump(recs, BUILD / f"{filing['parser']}_{filing['edition']}.json")
    print(f"DESC {filing['edition']}: {len(recs)} projects, {sum(len(r['issues']) for r in recs)} issues")
    return recs, {}


def main():
    """Every edition in the registry, newest first: {edition: records}."""
    return {f["edition"]: parse_filing(f)[0] for f in reversed(registry.filings(parser="desc"))}


if __name__ == "__main__":
    main()
