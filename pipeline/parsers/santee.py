"""Parser for South Carolina Public Service Authority: South Carolina Regional Transmission Planning Stakeholder Meeting.

Written by the GridLock ingest agent (us.anthropic.claude-opus-5 on Amazon Bedrock) on 2026-09-27 for santee-2026-2030; it passed the checks in generated_parser.py
(12 records). Runs in the sandbox (pipeline/sandbox.py).

Santee Cooper slide section of the SCRTP stakeholder deck. Its list starts with a summary slide headed "Transmission Projects <yr>-<yr>" whose two columns are "Project Title" and "In-service Date" (the same table also appears earlier as "Committed Transmission Facilities" in the assumptions section, so the summary slide is located by its heading plus a nearby "Santee Cooper" slide). Per-project slides follow, each with its title as the first line and labelled blocks "Project Description", "Project Need", "Project Status", "Planned In-Service Date" (label and value sometimes on one line), plus a map slide repeating the title; the section ends at the "Questions?" slide.

parse() reads the summary rows (title + date, header/total lines skipped) and the detail slides (title, label blocks), then matches each detail slide to a summary row by fuzzy normalized title (titles differ in hyphens/en dashes/case/an extra word). A matched record starts on its detail slide: name and in_service come verbatim from the summary row (full m/d/yyyy, the more precise printing), the slide's month-year in-service printing is kept in also_printed, and description/need/status (plus cost/start labels if a later edition adds them) come from the slide. Rows without a detail slide are returned from the summary slide with just name, in_service and item. item records the table heading and row position. count_pattern matches the summary table's "title .... m/d/yyyy" rows, once per project; count_scope keeps it to this list's pages.
"""
import re, difflib

LABELS = r"(Project Description|Project Need|Project Status|Project Justification|Planned In[- ]?Service Date|In[- ]?Service Date|Estimated (?:Project )?Cost|Project Cost|Estimated Start|Construction Start)"


def _norm(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def _clean(s):
    return re.sub(r'\s+', ' ', s).strip()


def _find_summary(pages):
    """Page index of Santee Cooper's 'Transmission Projects <yr>-<yr>' summary table."""
    cands = []
    for i, t in enumerate(pages):
        if (re.search(r'Transmission Projects\s*\d{4}\s*[-\u2010-\u2015]\s*\d{4}', t)
                and re.search(r'Project\s+Title', t, re.I)
                and re.search(r'In\s*-?\s*service\s+Date', t, re.I)):
            cands.append(i)
    for i in cands:
        for j in range(max(0, i - 5), i + 1):
            if 'Santee Cooper' in pages[j]:
                return i
    return cands[0] if cands else None


def _rows(text):
    """Summary table rows: 'Project Title .... In-service Date'."""
    rows = []
    row_re = re.compile(
        r'^\s*(?P<title>\S.*?)\s{2,}(?P<date>\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}/\d{2,4}|'
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}|\d{4})\s*$')
    for line in text.split('\n'):
        if re.search(r'Project\s+Title', line, re.I):
            continue
        m = row_re.match(line)
        if not m:
            continue
        title = _clean(m.group('title'))
        if not re.search(r'[A-Za-z]', title):
            continue
        if re.match(r'^(total|sub\s*-?total)\b', title, re.I):
            continue
        rows.append((title, _clean(m.group('date'))))
    return rows


def _detail(text):
    """Per-project slide: title line plus labelled blocks."""
    title = None
    for l in text.split('\n'):
        s = _clean(l)
        if not s or re.fullmatch(r'\d{1,3}', s):
            continue
        if re.match(r'^' + LABELS, s):
            break
        title = s
        break
    parts = [(m.group(0), m.start(), m.end()) for m in re.finditer(LABELS, text)]
    fields = {}
    for k, (label, st, en) in enumerate(parts):
        stop = parts[k + 1][1] if k + 1 < len(parts) else len(text)
        val = text[en:stop]
        val = re.sub(r'\n\s*\d{1,3}\s*$', '', val.rstrip())
        val = _clean(val)
        lab = label.lower()
        if 'description' in lab:
            key = 'description'
        elif 'need' in lab or 'justification' in lab:
            key = 'need'
        elif 'status' in lab:
            key = 'status'
        elif 'service' in lab:
            key = 'in_service'
        elif 'cost' in lab:
            key = 'cost_raw'
        elif 'start' in lab:
            key = 'start'
        else:
            continue
        if val and key not in fields:
            fields[key] = val
    return title, fields


def parse(pages):
    si = _find_summary(pages)
    if si is None:
        return []
    heading = ''
    m = re.search(r'Transmission Projects\s*\d{4}\s*[-\u2010-\u2015]\s*\d{4}', pages[si])
    if m:
        heading = _clean(m.group(0))
    rows = _rows(pages[si])

    # detail slides follow the summary table until the section's closing 'Questions?' slide
    details = []
    for j in range(si + 1, len(pages)):
        t = pages[j]
        if re.search(r'Questions\s*\?', t):
            break
        if not re.search(r'Project\s+Description', t, re.I):
            continue
        title, fields = _detail(t)
        if title:
            details.append((j + 1, title, fields))

    # greedy one-to-one match of detail slides to summary rows by fuzzy title
    pairs = []
    for di, (pg, dtitle, fields) in enumerate(details):
        for ri, (rtitle, rdate) in enumerate(rows):
            r = difflib.SequenceMatcher(None, _norm(dtitle), _norm(rtitle)).ratio()
            pairs.append((r, di, ri))
    pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
    used_d, used_r, d2r = set(), set(), {}
    for r, di, ri in pairs:
        if r < 0.8 or di in used_d or ri in used_r:
            continue
        used_d.add(di)
        used_r.add(ri)
        d2r[ri] = di

    out = []
    n = len(rows)
    for ri, (title, date) in enumerate(rows):
        rec = {'name': title, 'page': si + 1, 'in_service': date}
        if heading:
            rec['item'] = '%s, row %d of %d' % (heading, ri + 1, n)
        if ri in d2r:
            pg, dtitle, fields = details[d2r[ri]]
            rec['page'] = pg
            for k in ('description', 'need', 'status', 'start', 'cost_raw'):
                if k in fields:
                    rec[k] = fields[k]
            if 'in_service' in fields:
                rec.setdefault('also_printed', []).append(
                    {'field': 'in_service', 'value': fields['in_service'], 'page': pg})
        out.append(rec)
    out.sort(key=lambda r: r['page'])
    return out
