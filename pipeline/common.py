"""Shared helpers: paths, geometry, dates, text normalization. Standard library only."""
import calendar
import datetime as dt
import json
import math
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
CACHE = ROOT / "data" / "cache"
OVERRIDES = ROOT / "data" / "overrides"
BUILD = ROOT / "data" / "build"
SITE = ROOT / "site"

RADIUS_MI = 25.0          # challenge rule: centers within 25 miles
EARTH_R_MI = 3958.8


def haversine_mi(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_MI * math.asin(math.sqrt(a))


def midpoint(points):
    """Challenge rule: arithmetic mean of the located endpoints (one point if only one found)."""
    pts = [p for p in points if p is not None]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def xy_mi(lat, lon, lat0):
    """Local equirectangular projection in miles; fine for the ~100 mi scale we compare at."""
    return (math.radians(lon) * EARTH_R_MI * math.cos(math.radians(lat0)), math.radians(lat) * EARTH_R_MI)


def seg_dist(p, a, b):
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def polyline_distance_mi(A, B):
    """Closest approach between two [(lat, lon), ...] polylines, in miles (0 if they cross)."""
    lat0 = (A[0][0] + B[0][0]) / 2
    a = [xy_mi(la, lo, lat0) for la, lo in A]
    b = [xy_mi(la, lo, lat0) for la, lo in B]
    for i in range(len(a) - 1):
        for j in range(len(b) - 1):
            if _cross(a[i], a[i + 1], b[j], b[j + 1]):
                return 0.0
    best = math.inf
    for p in a:
        for j in range(len(b) - 1):
            best = min(best, seg_dist(p, b[j], b[j + 1]))
    for p in b:
        for i in range(len(a) - 1):
            best = min(best, seg_dist(p, a[i], a[i + 1]))
    if len(a) == 1 and len(b) == 1:
        best = math.hypot(a[0][0] - b[0][0], a[0][1] - b[0][1])
    return best


def _cross(p1, p2, p3, p4):
    def o(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2, d3, d4 = o(p3, p4, p1), o(p3, p4, p2), o(p1, p2, p3), o(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and 0 not in (d1, d2, d3, d4)


DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")


def parse_date(s):
    """Parse m/d/yy or m/d/yyyy. Returns (date, issue). Impossible days (04/31) are repaired
    to the month's last day and reported, never silently fixed."""
    m = DATE_RE.search(s or "")
    if not m:
        return None, f"unparseable date {s!r}"
    mo, d, y = map(int, m.groups())
    y += 2000 if y < 100 else 0
    try:
        return dt.date(y, mo, d), None
    except ValueError:
        if 1 <= mo <= 12:
            last = calendar.monthrange(y, mo)[1]
            return dt.date(y, mo, last), f"impossible date {m.group(0)!r} read as {mo}/{last}/{y}"
        return None, f"invalid date {m.group(0)!r}"


MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?"
SEASONS = {"spring": (3, 5), "summer": (6, 8), "fall": (9, 11), "autumn": (9, 11), "winter": (12, 12)}


def _month_period(y, a, b):
    return dt.date(y, a, 1), dt.date(y, b, calendar.monthrange(y, b)[1])


def _full(mo, day, y):
    """(first day, last day, issue) of a full date, repaired as parse_date does; None if it is no date."""
    d, err = parse_date(f"{mo}/{day}/{y}")
    return (d, d, err) if d else None


# every form a date takes, most specific first: (pattern, what it stands for, groups -> (first day, last day, issue) or None)
DATE_FORMS = (
    (DATE_RE, None, lambda g: _full(*g)),
    (re.compile(r"\b(20\d\d|19\d\d)-(\d{1,2})-(\d{1,2})(?!\d)"), None, lambda g: _full(g[1], g[2], g[0])),
    (re.compile(rf"\b{MONTH_RE}\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I), None, lambda g: _full(MONTHS[g[0][:3].lower()], g[1], g[2])),
    (re.compile(rf"\b(\d{{1,2}})\s+{MONTH_RE},?\s+(\d{{4}})\b", re.I), None, lambda g: _full(MONTHS[g[1][:3].lower()], g[0], g[2])),
    (re.compile(rf"\b{MONTH_RE}\s*[-'’ ,]\s*(\d{{4}}|\d{{2}})\b", re.I), "a month",
     lambda g: _month_period(int(g[1]) + (2000 if len(g[1]) == 2 else 0), MONTHS[g[0][:3].lower()], MONTHS[g[0][:3].lower()])),
    (re.compile(r"\b(\d{1,2})/(\d{4})\b"), "a month", lambda g: _month_period(int(g[1]), int(g[0]), int(g[0])) if 1 <= int(g[0]) <= 12 else None),
    (re.compile(r"\bq([1-4])\s*[-' ]?\s*(\d{4})\b|\b(\d{4})\s*[-' ]?\s*q([1-4])\b", re.I), "a quarter",
     lambda g: _month_period(int(g[1] or g[2]), 3 * int(g[0] or g[3]) - 2, 3 * int(g[0] or g[3]))),
    (re.compile(r"\b(spring|summer|fall|autumn|winter)\s*[-' ]?\s*(\d{4})\b", re.I), "a season",
     lambda g: _month_period(int(g[1]), *SEASONS[g[0].lower()])),
    (re.compile(r"\b(19\d\d|20\d\d)\b"), "a year", lambda g: _month_period(int(g[0]), 1, 12)),
)


def read_date(s, end=True):
    """Any date a utility prints: m/d/y, yyyy-mm-dd, 'June 1, 2027', 'June 2027', 'Jun-27', '06/2027', 'Q2 2027',
    'Summer 2027' or a bare year. A date short of a day stands for its whole period: its last day when end is True (in
    service by), else its first. Of several (phases, or a slip 'from 2025 to 2027'), the latest is read when end is
    True, else the earliest. Returns (date, issue); the issue says how a partial, repaired or chosen date was read, and
    is None for a plain full date."""
    s = s or ""
    found, taken, bad = [], [], None
    for rx, what, period in DATE_FORMS:
        for m in rx.finditer(s):
            # '1/2027' inside '6/1/2027' or the year inside 'Q2 2027' is part of a date already read
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            taken.append(m.span())
            p = period(m.groups())
            if p is None:
                bad = bad or f"invalid date {m.group(0)!r}"
                continue
            found.append((m.start(), p[0], p[1], p[2] if what is None else None, what))
    if not found:
        return None, bad or f"unparseable date {s!r}"
    _, first, last, err, what = max(found, key=lambda f: f[2]) if end else min(found, key=lambda f: f[1])
    d = last if end else first
    issue = err or (f"date given as {what} ({s.strip()!r}); read as {d.isoformat()}" if what else None)
    if len({(f[1], f[2]) for f in found}) > 1:
        issue = f"several dates in {s.strip()!r}; read the {'latest' if end else 'earliest'}, {d.isoformat()}" + (f" ({issue})" if err else "")
    return d, issue


def iso(d):
    return d.isoformat() if d else None


def pdf_pages(pdf):
    """pdftotext -layout, split on form feeds -> list of page texts (index 0 = page 1)."""
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    return out.split("\f")


VOLT_RE = re.compile(r"\b\d{2,3}(?:\s*[-/]\s*\d{1,3}(?:\.\d)?)?\s*kv\b.*$", re.I)


def norm_name(s):
    s = s.lower().replace("&", " and ").replace("'", "").replace("’", "")
    s = re.sub(r"\bhydroelectric\b", "hydro", s)
    s = re.sub(r"\((sav|usa|aug)\)", " ", s)
    s = re.sub(r"\b(st|ft|mt)\.?\s", lambda m: {"st": "saint ", "ft": "fort ", "mt": "mount "}[m.group(1)], s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(substation|sub|ss|switching station|switchyard|station|generating plant|power plant|plant|"
               r"tap|jct|junction|kv|the|of|inc|scana|sce g|desc|gpc|georgia power|dominion energy)\b", " ", s)
    s = re.sub(r"\brd\b", "road", s)
    s = re.sub(r"\b[nsew]\b", lambda m: {"n": "north", "s": "south", "e": "east", "w": "west"}[m.group()], s)
    s = re.sub(r"\bpri\b", "primary", s)
    s = re.sub(r"\b\d+\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def dump(obj, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=str))


def load(path):
    return json.loads(Path(path).read_text())


def filings(parser=None):
    """The filing registry (data/filings.json), oldest first, optionally only one parser's filings."""
    fs = sorted(load(ROOT / "data" / "filings.json")["filings"], key=lambda f: f["date"])
    return [f for f in fs if parser is None or f["parser"] == parser]
