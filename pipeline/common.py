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


def iso(d):
    return d.isoformat() if d else None


def pdf_pages(pdf):
    """pdftotext -layout, split on form feeds -> list of page texts (index 0 = page 1)."""
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    return out.split("\f")


VOLT_RE = re.compile(r"\b\d{2,3}(?:\s*[-/]\s*\d{1,3}(?:\.\d)?)?\s*kv\b.*$", re.I)


def norm_name(s):
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\((sav|usa|aug)\)", " ", s)
    s = re.sub(r"\b(st|ft|mt)\.?\s", lambda m: {"st": "saint ", "ft": "fort ", "mt": "mount "}[m.group(1)], s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(substation|sub|switching station|switchyard|station|generating plant|power plant|plant|"
               r"tap|jct|junction|kv|the|of|inc|scana|sce g|desc|gpc|georgia power|dominion energy)\b", " ", s)
    s = re.sub(r"\brd\b", "road", s)
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
