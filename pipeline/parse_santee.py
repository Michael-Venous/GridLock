"""Parse Santee Cooper's project lists from SCRTP stakeholder decks (see data/filings.json).

Each first-quarter SCRTP deck has a "Transmission Projects YYYY-YYYY" slide listing Santee Cooper's projects by
title and in-service date, followed by a slide for each major project (description, need, status and planned
in-service month) and its map. The list has no project IDs and no costs, so a project is known by its title.
Rows are joined to their own slides by title. When the list and a project's slide, or the deck's other table of
committed facilities, give different dates, the list's date is used and the disagreement is reported.
"""
import calendar
import difflib
import re

from common import RAW, BUILD, dump, filings, iso, norm_name, parse_date, pdf_pages

EDITIONS = {f["edition"]: f for f in filings("santee")}
LIST_NOTES = {}   # filing id -> slide-level parsing notes, shown in data quality for the current edition
ROW = re.compile(r"^\s*(\S.*?\S)\s{2,}(\d{1,2}/\d{1,2}/\d{2,4}|TBD)\s*$")
VOLT = re.compile(r"\b\d{2,3}(?:\s*[-/]\s*\d{1,3})*\s*kv\b", re.I)
LEAD = re.compile(r"^(?:reconductor|rebuild|construct|upgrade|replace|install(?:\s+\d+(?:st|nd|rd|th))?)\s+", re.I)
TRAIL = re.compile(r"\s+(?:station|substation|switching|switchyard|improvements?|upgrades?|transformers?|xfmr|tap|lines?|tie|"
                   r"series|bus|breakers?|rebuild|limiting|elements|reconductor)\b.*$", re.I)
MILES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-\s*)?mi(?:les?)?\b", re.I)
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
SAME_TITLE = 0.75   # title similarity at which a project slide is taken to describe a list row


def title_norm(title):
    """A title with voltages and circuit numbers removed, normalized like station names."""
    t = VOLT.sub(" ", title)
    t = re.sub(r"#\s*\d+", " ", t)
    return norm_name(t)


def key_of(title):
    return re.sub(r"[^A-Z0-9]+", "-", title_norm(title).upper()).strip("-")


def endpoint_names(title):
    """Station names in a title: "Reconductor Purrysburg - Mcintosh 230 kV tie lines" -> [Purrysburg, Mcintosh]."""
    t = re.sub(r"\([^)]*\)", " ", title).strip()
    t = LEAD.sub("", t)
    t = re.split(r"\s+as\s+a\s+|:|\s+at\s+", t, maxsplit=1, flags=re.I)[0]
    t = re.sub(r"#\s*\d+(?:\s*(?:and|&)\s*#\s*\d+)?", " ", t)
    m = VOLT.search(t)
    if m:
        t = t[:m.start()]
    names = []
    for part in re.split(r"\s*[-–]\s*|\s+to\s+", t):
        part = TRAIL.sub("", " " + part.strip()).strip()
        if len(part) > 1:
            names.append(part)
    return names


def table_rows(page):
    """(title, date) rows of a 'Project Title / In-service Date' table, plus any line that isn't a row."""
    text = page.split("In-service Date", 1)[1] if "In-service Date" in page else ""
    rows, stray = [], []
    for ln in text.split("\n"):
        if not ln.strip() or re.fullmatch(r"\s*\d+\s*", ln):
            continue
        m = ROW.match(ln)
        if m:
            rows.append((re.sub(r"\s+", " ", m.group(1)), m.group(2)))
        else:
            stray.append(ln.strip())
    return rows, stray


def field(text, label, nxt):
    m = re.search(rf"{label}\s*(.*?)\s*(?:{nxt}|\Z)", text, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def project_slides(pages, first, last):
    """Each project slide between the list slide and the end of Santee Cooper's section."""
    out = []
    for i in range(first, last):
        page = pages[i]
        if "Project Description" not in page:
            continue
        head = page.split("Project Description", 1)[0]
        body = re.sub(r"\n\s*\d+\s*$", "", page.strip())   # slide number
        out.append({
            "page": i + 1, "title": re.sub(r"\s+", " ", head).strip(),
            "description": field(body, "Project Description", "Project Need"),
            "need": field(body, "Project Need", "Project Status"),
            "status": field(body, "Project Status", "Planned In-Service Date"),
            "planned": field(body, "Planned In-Service Date", r"\Z"),
        })
    return out


def title_alternatives(title):
    """A slide can cover two rows ("Conway 230 kV Switching Station and Marion-Conway 230 kV Line")."""
    parts = [p for p in re.split(r"\s+and\s+", title) if re.search(r"[A-Za-z]{3}", p)]
    return [title] + (parts if len(parts) > 1 else [])


def own_slide(title, slides):
    best, score = None, 0.0
    for s in slides:
        for alt in title_alternatives(s["title"]):
            r = difflib.SequenceMatcher(None, title_norm(title), title_norm(alt)).ratio()
            if r > score:
                best, score = s, r
    return best if score >= SAME_TITLE else None


def planned_month(text):
    """'December 2026' -> (2026, 12); anything else -> None."""
    m = re.search(r"([A-Za-z]+)\s+(\d{4})", text or "")
    if m and m.group(1).lower() in MONTHS:
        return int(m.group(2)), MONTHS[m.group(1).lower()]
    return None


def cased(name, text):
    """The spelling a project slide uses for a station name, when it names it ("Mcintosh" -> "McIntosh")."""
    m = re.search(re.escape(name), text or "", re.I)
    return m.group(0) if m else name


def parse(edition, filing=None):
    filing = filing or EDITIONS[edition]
    pages = pdf_pages(RAW / filing["file"])
    heading = re.compile(r"Transmission Projects " + edition.replace("-", r"\s*-\s*"))
    lists = [i for i, p in enumerate(pages) if heading.search(p) and "In-service Date" in p]
    if len(lists) != 1:
        raise ValueError(f"Santee Cooper {edition}: expected one 'Transmission Projects {edition}' slide, found {len(lists)}")
    at = lists[0]
    end = next((i for i in range(at + 1, len(pages)) if "Questions?" in pages[i]), len(pages))
    rows, stray = table_rows(pages[at])
    slides = project_slides(pages, at + 1, end)
    notes = [f"Santee Cooper {edition} list (slide {at + 1}): could not read the line {s!r}" for s in stray]
    # The deck's other table of the same projects ("Committed Transmission Facilities"), for a cross-check.
    others = {}
    for i, p in enumerate(pages):
        if i != at and "Project Title" in p and "In-service Date" in p:
            for title, date in table_rows(p)[0]:
                others.setdefault(key_of(title), (i + 1, date))

    out, used, seen = [], set(), {}
    for n, (title, date_raw) in enumerate(rows, 1):
        issues = []
        key = key_of(title)
        if key in seen:
            issues.append(("warn", f"same title as row {seen[key]} of the list"))
            key = f"{key}-{n}"
        seen.setdefault(key, n)
        slide = own_slide(title, slides)
        if slide:
            used.add(slide["page"])
        isd = None
        if date_raw == "TBD":
            issues.append(("warn", "in-service date is TBD in the list"))
        else:
            isd, err = parse_date(date_raw)
            if err:
                issues.append(("error", err))
        if slide and isd:
            month = planned_month(slide["planned"])
            if month and month != (isd.year, isd.month):
                issues.append(("warn", f"the list (slide {at + 1}) gives {date_raw}, but the project's own slide {slide['page']} says {slide['planned']}"))
            elif not month:
                issues.append(("warn", f"the list (slide {at + 1}) gives {date_raw}, but the project's own slide {slide['page']} says {slide['planned'] or 'nothing'}"))
        other = others.get(key_of(title))
        if other and other[1] != date_raw:
            issues.append(("warn", f"slide {other[0]}'s table gives {other[1]} where the list (slide {at + 1}) gives {date_raw}"))
        if not slide:
            issues.append(("info", "no project slide in the deck: description, need and status not published"))
        desc = slide["description"] if slide else ""
        miles = [float(x) for x in MILES.findall(desc)] if re.search(r"\blines?\b", title, re.I) else []
        page = slide["page"] if slide else at + 1
        out.append({
            "uid": f"SCPSA:{key}", "key": key, "plan": "santee", "utility": "SCPSA", "owner": "Santee Cooper", "state": "SC",
            "name": title, "project_id": f"Row {n}", "status": slide["status"] if slide and slide["status"] else None,
            "description": desc, "need": slide["need"] if slide else "",
            "endpoint_names": [cased(e, slide["title"] if slide else "") for e in endpoint_names(title)],
            "isd": iso(isd), "isd_raw": date_raw,
            "window": {"start": None, "end": iso(isd), "basis": "in-service date only; Santee Cooper publishes no start date"},
            "cost": {"total": None, "by_year": {}, "basis": "not published in Santee Cooper's list"},
            "miles": max(miles) if miles else None,
            "source": {"doc": f"Santee Cooper list {edition} (SCRTP meeting {filing['date']})", "url": filing["url"], "page": page,
                       "item": f"row {n} of {len(rows)} on slide {at + 1}"},
            "issues": [{"level": lv, "msg": m} for lv, m in issues],
        })
    for s in slides:
        if s["page"] not in used:
            notes.append(f"Santee Cooper {edition}: project slide {s['page']} ({s['title']!r}) matches no row of the list on slide {at + 1}")
    dump(out, BUILD / f"santee_{edition}.json")
    print(f"Santee Cooper {edition}: {len(out)} projects, {len(slides)} project slides, {sum(len(r['issues']) for r in out)} issues")
    return out, notes


def parse_filing(filing):
    """The registry's built-in parser interface: (records, removed projects)."""
    records, notes = parse(filing["edition"], filing)
    LIST_NOTES[filing["id"]] = notes
    return records, {}


def main():
    """Every edition in the registry, newest first: {edition: (records, list-level notes)}."""
    return {ed: parse(ed) for ed in sorted(EDITIONS, reverse=True)}


if __name__ == "__main__":
    main()
