"""What federal maps show at each exactly placed site and along each traced line. Standard library only.

Checked only where the location is good enough to mean something: endpoints placed at a station (not a town
guess), and lines traced between two such stations. Each site is checked within SITE_RADIUS_MI; each line
along its traced path. Results describe what is mapped, never what a permit will require.

Every service answer is clipped to the checked area and cached in data/cache/environment.json, so an
--offline build reproduces the same results. Regional layers for the map go to data/env/.
"""
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import time
import urllib.parse
import urllib.request

from common import CACHE, ROOT, dump, haversine_mi, xy_mi

SITE_RADIUS_MI = 0.25
GENERALIZE_DEG = 0.00003   # about 3 m: FEMA's flood outlines come back with millions of vertices otherwise
STEP_MI = 0.015          # sampling step along lines and across site circles (about 24 m)
REGION = (-83.3, 31.4, -80.0, 34.4)   # lon/lat box around the river for the map layers
UA = {"User-Agent": "Gridlock-ShellHacks-2026/0.1 (hackathon prototype)"}

NWI = "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer"
NFHL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer"
FWS_CH = "https://services.arcgis.com/QVENGdaPbd4LUkLV/arcgis/rest/services/USFWS_Critical_Habitat/FeatureServer"
NMFS_CH = "https://maps.fisheries.noaa.gov/server/rest/services/All_NMFS_Critical_Habitat/MapServer"
PADUS = "https://services.arcgis.com/v01gqwM5QqNysAAi/arcgis/rest/services/Manager_Type_PADUS/FeatureServer"

LAYERS = {
    "wetlands": {"url": f"{NWI}/0", "fields": ["Wetlands.WETLAND_TYPE", "Wetlands.ATTRIBUTE"]},
    "flood": {"url": f"{NFHL}/28", "fields": ["FLD_ZONE", "ZONE_SUBTY", "SFHA_TF"]},
    "habitat_fws": {"url": f"{FWS_CH}/0", "fields": ["comname", "sciname", "listing_status"]},
    "habitat_fws_proposed": {"url": f"{FWS_CH}/2", "fields": ["comname", "sciname", "listing_status"]},
    "habitat_nmfs": {"url": f"{NMFS_CH}/226", "fields": ["COMNAME", "LISTENTITY", "LISTSTATUS", "CHSTATUS", "UNIT"]},
    "habitat_nmfs_rivers": {"url": f"{NMFS_CH}/2", "fields": ["COMNAME", "LISTENTITY", "LISTSTATUS", "CHSTATUS", "UNIT"]},
    "protected": {"url": f"{PADUS}/0", "fields": ["Unit_Nm", "Mang_Name", "Mang_Type", "Category", "Des_Tp", "Pub_Access"],
                  "where": "Category IN ('Fee','Easement','Designation')"},
}
SOURCES = [
    {"id": "nwi", "title": "USFWS National Wetlands Inventory", "url": "https://www.fws.gov/program/national-wetlands-inventory", "service": NWI},
    {"id": "nfhl", "title": "FEMA National Flood Hazard Layer (effective flood maps)", "url": "https://www.fema.gov/flood-maps/national-flood-hazard-layer", "service": NFHL},
    {"id": "fws-ch", "title": "USFWS critical habitat, final and proposed", "url": "https://ecos.fws.gov/ecp/report/table/critical-habitat.html", "service": FWS_CH},
    {"id": "nmfs-ch", "title": "NOAA Fisheries critical habitat", "url": "https://www.fisheries.noaa.gov/national/endangered-species-conservation/critical-habitat", "service": NMFS_CH},
    {"id": "padus", "title": "USGS Protected Areas Database of the US (PAD-US 4.1), manager type", "url": "https://www.usgs.gov/programs/gap-analysis-project/science/pad-us-data-overview", "service": PADUS},
]
# NWI types that are open water rather than vegetated wetland; reported separately.
WATER = {"Lake", "Riverine", "Freshwater Pond", "Estuarine and Marine Deepwater"}

_cache_path = CACHE / "environment.json"
_cache = json.loads(_cache_path.read_text()) if _cache_path.exists() else {}


# ---- geometry (lon/lat throughout this module, like the services) ----

def bbox_around(points, pad_mi):
    lat0 = sum(p[1] for p in points) / len(points)
    dlat = pad_mi / 69.09
    dlon = dlat / math.cos(math.radians(lat0))
    return (min(p[0] for p in points) - dlon, min(p[1] for p in points) - dlat,
            max(p[0] for p in points) + dlon, max(p[1] for p in points) + dlat)


def clip_ring(ring, box):
    """Sutherland-Hodgman against an axis-aligned box. Rings are [[lon, lat], ...]."""
    x0, y0, x1, y1 = box
    edges = [(lambda p: p[0] >= x0, lambda a, b: _at_x(a, b, x0)), (lambda p: p[0] <= x1, lambda a, b: _at_x(a, b, x1)),
             (lambda p: p[1] >= y0, lambda a, b: _at_y(a, b, y0)), (lambda p: p[1] <= y1, lambda a, b: _at_y(a, b, y1))]
    out = ring
    for inside, cut in edges:
        if not out:
            break
        src, out = out, []
        for i, cur in enumerate(src):
            prev = src[i - 1]
            if inside(cur):
                if not inside(prev):
                    out.append(cut(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cut(prev, cur))
    if len(out) >= 3 and out[0] != out[-1]:
        out.append(out[0])
    return out if len(out) >= 4 else []


def _at_x(a, b, x):
    t = (x - a[0]) / (b[0] - a[0])
    return [x, a[1] + t * (b[1] - a[1])]


def _at_y(a, b, y):
    t = (y - a[1]) / (b[1] - a[1])
    return [a[0] + t * (b[0] - a[0]), y]


def clip_path(path, box):
    """Keep the parts of a polyline inside the box (Liang-Barsky per segment)."""
    x0, y0, x1, y1 = box
    parts, cur = [], []
    for a, b in zip(path, path[1:]):
        seg = _clip_seg(a, b, x0, y0, x1, y1)
        if not seg:
            if len(cur) > 1:
                parts.append(cur)
            cur = []
            continue
        if cur and cur[-1] == seg[0]:
            cur.append(seg[1])
        else:
            if len(cur) > 1:
                parts.append(cur)
            cur = [seg[0], seg[1]]
    if len(cur) > 1:
        parts.append(cur)
    return parts


def _clip_seg(a, b, x0, y0, x1, y1):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return [[a[0] + t0 * dx, a[1] + t0 * dy], [a[0] + t1 * dx, a[1] + t1 * dy]]


def inside_rings(pt, rings):
    """Even-odd over every ring, so holes subtract without needing ring orientation."""
    x, y = pt
    hit = False
    for ring in rings:
        for (xa, ya), (xb, yb) in zip(ring, ring[1:]):
            if (ya > y) != (yb > y) and x < xa + (y - ya) * (xb - xa) / (yb - ya):
                hit = not hit
    return hit


def ring_area(r):
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(r, r[1:])) / 2


def multipolygon(rings):
    """Esri rings (outer clockwise, holes counterclockwise) -> GeoJSON MultiPolygon coordinates."""
    outers = [r for r in rings if ring_area(r) < 0] or rings
    polys = [[o] for o in outers]
    for h in rings:
        if any(h is o for o in outers):
            continue
        home = next((p for p in polys if inside_rings(h[0], [p[0]])), None)
        if home:
            home.append(h)
    return polys


def ring_box(rings):
    xs = [p[0] for r in rings for p in r]; ys = [p[1] for r in rings for p in r]
    return (min(xs), min(ys), max(xs), max(ys))


def in_box(pt, box):
    return box[0] <= pt[0] <= box[2] and box[1] <= pt[1] <= box[3]


def seg_point_mi(pt, a, b, lat0):
    p, pa, pb = xy_mi(pt[1], pt[0], lat0), xy_mi(a[1], a[0], lat0), xy_mi(b[1], b[0], lat0)
    dx, dy = pb[0] - pa[0], pb[1] - pa[1]
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((p[0] - pa[0]) * dx + (p[1] - pa[1]) * dy) / L))
    return math.hypot(p[0] - pa[0] - t * dx, p[1] - pa[1] - t * dy)


def segments_cross(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (o(c, d, a) > 0) != (o(c, d, b) > 0) and (o(a, b, c) > 0) != (o(a, b, d) > 0)


def circle_samples(center, radius_mi):
    """Grid points inside a circle, STEP_MI apart. center is (lon, lat)."""
    lat0 = center[1]
    dlat = STEP_MI / 69.09
    dlon = dlat / math.cos(math.radians(lat0))
    n = int(radius_mi / STEP_MI)
    out = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            if (i * i + j * j) * STEP_MI * STEP_MI <= radius_mi * radius_mi:
                out.append((center[0] + j * dlon, center[1] + i * dlat))
    return out


def path_samples(path):
    """Points every STEP_MI along a [(lon, lat), ...] path, each standing for STEP_MI of line."""
    out = []
    for a, b in zip(path, path[1:]):
        d = haversine_mi(a[1], a[0], b[1], b[0])
        n = max(1, round(d / STEP_MI))
        for k in range(n):
            t = (k + 0.5) / n
            out.append(((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])), d / n))
    return out


CORRIDOR_MI = 0.1   # drawn width either side of a traced line; the numbers are read on the line itself


def clip_convex(ring, clip):
    """Sutherland-Hodgman against a convex, counterclockwise clip polygon (open list of [lon, lat])."""
    out = ring[:-1] if ring and ring[0] == ring[-1] else ring
    for a, b in zip(clip, clip[1:] + clip[:1]):
        if not out:
            break
        side = lambda p: (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= 0
        def cut(p, q):
            d1 = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
            d2 = (b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0])
            t = d1 / (d1 - d2)
            return [p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])]
        src, out = out, []
        for i, cur in enumerate(src):
            prev = src[i - 1]
            if side(cur):
                if not side(prev):
                    out.append(cut(prev, cur))
                out.append(cur)
            elif side(prev):
                out.append(cut(prev, cur))
    if len(out) < 3:
        return []
    return [[round(x, 5), round(y, 5)] for x, y in out] + [[round(out[0][0], 5), round(out[0][1], 5)]]


def circle(center, radius_mi, steps=40):
    dlat = radius_mi / 69.09
    dlon = dlat / math.cos(math.radians(center[1]))
    return [[center[0] + dlon * math.cos(2 * math.pi * i / steps), center[1] + dlat * math.sin(2 * math.pi * i / steps)] for i in range(steps)]


def simplify(path, tol_mi):
    """Douglas-Peucker on [lon, lat] points."""
    if len(path) < 3:
        return path
    lat0 = path[0][1]
    best, idx = 0, 0
    for i in range(1, len(path) - 1):
        d = seg_point_mi(path[i], path[0], path[-1], lat0)
        if d > best:
            best, idx = d, i
    if best <= tol_mi:
        return [path[0], path[-1]]
    return simplify(path[:idx + 1], tol_mi)[:-1] + simplify(path[idx:], tol_mi)


def corridor(path, half_mi):
    """Convex quads covering a band along the path, joined on the angle bisectors so they don't overlap,
    plus the band's outline. Works in a local mile grid, returns lon/lat."""
    pts = simplify(path, 0.01)
    lat0 = pts[0][1]
    k = math.cos(math.radians(lat0))
    xy = [(x * 69.09 * k, y * 69.09) for x, y in pts]
    back = lambda q: [q[0] / (69.09 * k), q[1] / 69.09]
    normals = []
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        L = math.hypot(x2 - x1, y2 - y1) or 1e-9
        normals.append((-(y2 - y1) / L, (x2 - x1) / L))
    offs = []
    for i in range(len(xy)):
        if i == 0 or i == len(xy) - 1:
            n = normals[0] if i == 0 else normals[-1]
            m = 1.0
        else:
            a, b = normals[i - 1], normals[i]
            n = (a[0] + b[0], a[1] + b[1]); L = math.hypot(*n) or 1e-9; n = (n[0] / L, n[1] / L)
            m = min(1 / max(n[0] * a[0] + n[1] * a[1], 0.25), 4)   # miter length, capped at sharp turns
        offs.append(((xy[i][0] + n[0] * half_mi * m, xy[i][1] + n[1] * half_mi * m), (xy[i][0] - n[0] * half_mi * m, xy[i][1] - n[1] * half_mi * m)))
    quads = []
    for i in range(len(xy) - 1):
        (l1, r1), (l2, r2) = offs[i], offs[i + 1]
        quad = [r1, r2, l2, l1]
        if ring_area([*quad, quad[0]]) < 0:
            quad = quad[::-1]
        quads.append([back(q) for q in quad])
    outline = [back(l) for l, _ in offs] + [back(r) for _, r in offs[::-1]]
    return quads, outline + [outline[0]]


# ---- services ----

def _post(url, params, tries=3):
    body = urllib.parse.urlencode(params).encode()
    for attempt in range(tries):
        req = urllib.request.Request(url, data=body, headers={**UA, "Content-Type": "application/x-www-form-urlencoded"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=120).read())
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def cache_key(layer, geometry, box, clips=None):
    return hashlib.sha1(json.dumps([layer, LAYERS[layer]["url"], geometry, [round(v, 6) for v in box], GENERALIZE_DEG,
                                    [[[round(x, 6), round(y, 6)] for x, y in c] for c in clips or []]], sort_keys=True).encode()).hexdigest()


def fetch(layer, geometry, geometry_type, box, network, clips=None):
    """All features of one layer touching the geometry, clipped to box and then to each convex area in clips
    (one stored feature per polygon and area). Cached by layer, geometry and areas."""
    spec = LAYERS[layer]
    key = cache_key(layer, geometry, box, clips)
    if key in _cache:
        return _cache[key]["features"]
    if not network:
        return None
    feats, offset = [], 0
    while True:
        params = {"geometry": json.dumps(geometry), "geometryType": geometry_type, "inSR": 4326, "outSR": 4326,
                  "spatialRel": "esriSpatialRelIntersects", "outFields": ",".join(spec["fields"]), "returnGeometry": "true",
                  "where": spec.get("where", "1=1"), "maxAllowableOffset": GENERALIZE_DEG, "geometryPrecision": 6,
                  "resultOffset": offset, "resultRecordCount": 1000, "f": "json"}
        d = _post(spec["url"] + "/query", params)
        if "error" in d:
            raise RuntimeError(f"{layer}: {d['error']}")
        page = d.get("features", [])
        for f in page:
            attrs = {k.split(".")[-1]: v for k, v in f["attributes"].items()}
            g = f.get("geometry") or {}
            if "rings" in g:
                rings = [r for r in (clip_ring(ring, box) for ring in g["rings"]) if r]
                for area in (clips or [None]):
                    part = rings if area is None else [r for r in (clip_convex(ring, area) for ring in rings) if r]
                    if part:
                        feats.append({"a": attrs, "rings": [[[round(x, 5), round(y, 5)] for x, y in r] for r in part]})
            elif "paths" in g:
                paths = [p for path in g["paths"] for p in clip_path(path, box)]
                if paths:
                    feats.append({"a": attrs, "paths": [[[round(x, 5), round(y, 5)] for x, y in p] for p in paths]})
        offset += len(page)
        if not page or not d.get("exceededTransferLimit"):
            break
    _cache[key] = {"fetched": dt.date.today().isoformat(), "layer": layer, "features": feats}
    return feats


def fetch_all(jobs, network):
    """jobs: {name: (layer, geometry, geometry_type, box)} -> {name: features or None}. Runs a few at once."""
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch, *job[:4], network, job[4]): name for name, job in jobs.items()}
        for f in concurrent.futures.as_completed(futs):
            name = futs[f]
            try:
                out[name] = f.result()
            except Exception as e:   # a service being down leaves that check empty, never crashes the build
                print("  environment query failed", name, e)
                out[name] = None
    return out


# ---- reading the results ----

def species(a, source):
    if source == "NOAA Fisheries":
        name = a.get("COMNAME") or ""
        if "," in name:
            head, tail = name.split(",", 1)
            name = f"{tail.strip()} {head.strip().lower()}"
        return {"species": name, "status": a.get("LISTSTATUS"), "stage": (a.get("CHSTATUS") or "").lower() or None,
                "unit": a.get("UNIT"), "source": source}
    return {"species": a.get("comname"), "status": a.get("listing_status"), "stage": None, "unit": None, "source": source}


def domains(layer, network=True):
    """Coded-value names for a layer's fields (PAD-US stores manager and designation as codes). Cached."""
    key = f"domains:{layer}"
    if key not in _cache:
        if not network:
            return {}
        req = urllib.request.Request(LAYERS[layer]["url"] + "?f=json", headers=UA)
        d = json.loads(urllib.request.urlopen(req, timeout=60).read())
        _cache[key] = {"fetched": dt.date.today().isoformat(), "layer": layer,
                       "features": {f["name"]: {c["code"]: c["name"] for c in f["domain"]["codedValues"]} for f in d["fields"] if (f.get("domain") or {}).get("codedValues")}}
    return _cache[key]["features"]


def protected_area(a):
    names = domains("protected", network=False)
    code = lambda field: names.get(field, {}).get(a.get(field), a.get(field))
    return {"name": a.get("Unit_Nm"), "manager": code("Mang_Name"), "managerType": code("Mang_Type"),
            "category": a.get("Category"), "designation": code("Des_Tp"), "access": code("Pub_Access")}


def _polys(feats):
    return [(f, ring_box(f["rings"])) for f in feats or [] if "rings" in f]


def _hits(pt, polys):
    return [f for f, box in polys if in_box(pt, box) and inside_rings(pt, f["rings"])]


def read_site(center, got):
    """What is mapped within SITE_RADIUS_MI of a station. center is (lon, lat)."""
    samples = circle_samples(center, SITE_RADIUS_MI)
    out = {"radiusMi": SITE_RADIUS_MI}
    if got["wetlands"] is None:
        out["wetlands"] = None
    else:
        w = _polys(got["wetlands"])
        types = {}
        for pt in samples:
            h = _hits(pt, w)
            if h:
                t = h[0]["a"].get("WETLAND_TYPE") or "Wetland"
                types[t] = types.get(t, 0) + 1
        n = len(samples)
        out["wetlands"] = {"share": round(sum(v for k, v in types.items() if k not in WATER) / n, 3),
                           "waterShare": round(sum(v for k, v in types.items() if k in WATER) / n, 3),
                           "types": {k: round(v / n, 3) for k, v in sorted(types.items(), key=lambda kv: -kv[1])},
                           "atStation": [f["a"].get("WETLAND_TYPE") for f in _hits(center, w)]}
    fl = _polys(got["flood"])
    if got["flood"] is None:
        out["flood"] = None
    else:
        at = _hits(center, fl)
        sfha = sum(1 for s in samples if any(f["a"].get("SFHA_TF") == "T" for f in _hits(s, fl)))
        out["flood"] = {"zone": at[0]["a"].get("FLD_ZONE") if at else None,
                        "subtype": at[0]["a"].get("ZONE_SUBTY") if at else None,
                        "sfha": bool(at) and at[0]["a"].get("SFHA_TF") == "T",
                        "mapped": bool(got["flood"]),
                        "sfhaShare": round(sfha / len(samples), 3)}
    out["habitat"] = _habitat_near(samples, center, got)
    out["protected"] = _protected_near(samples, got)
    return out


def _habitat_near(samples, center, got):
    found = {}
    for layer, source in (("habitat_fws", "USFWS"), ("habitat_fws_proposed", "USFWS (proposed)"), ("habitat_nmfs", "NOAA Fisheries")):
        polys = _polys(got[layer])
        for f, box in polys:
            if any(in_box(s, box) and inside_rings(s, f["rings"]) for s in samples):
                sp = species(f["a"], "NOAA Fisheries" if "nmfs" in layer else "USFWS")
                if "proposed" in layer:
                    sp["stage"] = "proposed"
                found[(sp["species"], sp["unit"])] = sp
    for f in got["habitat_nmfs_rivers"] or []:
        lat0 = center[1]
        if any(seg_point_mi(center, a, b, lat0) <= SITE_RADIUS_MI for p in f["paths"] for a, b in zip(p, p[1:])):
            sp = species(f["a"], "NOAA Fisheries")
            found[(sp["species"], sp["unit"])] = sp
    return list(found.values()) if any(got[k] is not None for k in ("habitat_fws", "habitat_nmfs", "habitat_nmfs_rivers")) else None


def _protected_near(samples, got):
    if got["protected"] is None:
        return None
    found = {}
    for f, box in _polys(got["protected"]):
        if any(in_box(s, box) and inside_rings(s, f["rings"]) for s in samples):
            pa = protected_area(f["a"])
            found[(pa["name"], pa["category"])] = pa
    return list(found.values())


def read_route(path, got):
    """Miles of a traced line inside mapped wetland, special flood hazard area, habitat and protected land."""
    samples = path_samples(path)
    total = sum(d for _, d in samples)
    out = {"miles": round(total, 2)}
    if got["wetlands"] is None:
        out["wetlands"] = None
    else:
        w = _polys(got["wetlands"])
        types = {}
        for s, d in samples:
            h = _hits(s, w)
            if h:
                t = h[0]["a"].get("WETLAND_TYPE") or "Wetland"
                types[t] = types.get(t, 0) + d
        out["wetlands"] = {"miles": round(sum(v for k, v in types.items() if k not in WATER), 2),
                           "waterMiles": round(sum(v for k, v in types.items() if k in WATER), 2),
                           "types": {k: round(v, 2) for k, v in sorted(types.items(), key=lambda kv: -kv[1])}}
    if got["flood"] is None:
        out["flood"] = None
    else:
        fl = _polys(got["flood"])
        mapped = sum(d for s, d in samples if _hits(s, fl))
        sfha = sum(d for s, d in samples if any(f["a"].get("SFHA_TF") == "T" for f in _hits(s, fl)))
        out["flood"] = {"sfhaMiles": round(sfha, 2), "mappedMiles": round(mapped, 2)}
    found = {}
    for layer in ("habitat_fws", "habitat_fws_proposed", "habitat_nmfs"):
        for f, box in _polys(got[layer]):
            miles = sum(d for s, d in samples if in_box(s, box) and inside_rings(s, f["rings"]))
            if miles:
                sp = species(f["a"], "NOAA Fisheries" if "nmfs" in layer else "USFWS")
                if "proposed" in layer:
                    sp["stage"] = "proposed"
                found[(sp["species"], sp["unit"])] = {**sp, "miles": round(miles, 2)}
    for f in got["habitat_nmfs_rivers"] or []:
        if any(segments_cross(a, b, c, d) for p in f["paths"] for c, d in zip(p, p[1:]) for a, b in zip(path, path[1:])):
            sp = species(f["a"], "NOAA Fisheries")
            found[(sp["species"], sp["unit"])] = {**sp, "crosses": True}
    out["habitat"] = list(found.values()) if any(got[k] is not None for k in ("habitat_fws", "habitat_nmfs", "habitat_nmfs_rivers")) else None
    if got["protected"] is None:
        out["protected"] = None
    else:
        pas = {}
        for f, box in _polys(got["protected"]):
            miles = sum(d for s, d in samples if in_box(s, box) and inside_rings(s, f["rings"]))
            if miles:
                pa = protected_area(f["a"])
                k = (pa["name"], pa["category"])
                pas[k] = {**pa, "miles": round(pas.get(k, {}).get("miles", 0) + miles, 2)}
        out["protected"] = list(pas.values())
    return out


# ---- which projects to check ----

def site_placed(e):
    return bool(e["point"]) and e["confidence"] in ("high", "medium") and e["method"] != "town"


UNPLACED = "not placed on the map, so its distance to other utilities' projects can't be measured"


def in_pairing_range(recs):
    """Projects that could appear in a cross-state pair: within 25 mi plus both uncertainty radii."""
    located = [r for r in recs if r.get("center")]
    keep = set()
    for a in located:
        for b in located:
            if a["plan"] == b["plan"] or a.get("state") == b.get("state"):
                continue
            d = haversine_mi(a["center"]["lat"], a["center"]["lon"], b["center"]["lat"], b["center"]["lon"])
            if d < 25 + (a["radiusMi"] or 0) + (b["radiusMi"] or 0):
                keep.add(a["uid"])
                break
    return keep


def check(recs, offline=False):
    """Set r["environment"] on every record; query services for the ones in pairing range."""
    network = not offline
    try:
        domains("protected", network)
    except OSError as e:
        print("  PAD-US field names unavailable; codes kept", e)
    targets = in_pairing_range(recs)
    jobs, plans = {}, {}
    for r in recs:
        if r["uid"] not in targets:
            r["environment"] = {"checked": False, "reason": "not near any project in the other state" if r.get("center") else UNPLACED}
            continue
        sites = [e for e in r["endpoints"] if site_placed(e)]
        route_ok = bool(r.get("route")) and all(site_placed(e) for e in r["endpoints"] if e["point"])
        if not sites:
            r["environment"] = {"checked": False, "reason": "no endpoint is placed at a station, so the ground can't be checked"}
            continue
        plan = {"sites": [], "route": None}
        for e in sites:
            c = (e["point"]["lon"], e["point"]["lat"])
            box = bbox_around([c], SITE_RADIUS_MI)
            geom = {"xmin": box[0], "ymin": box[1], "xmax": box[2], "ymax": box[3], "spatialReference": {"wkid": 4326}}
            k = f"site:{c[0]:.5f},{c[1]:.5f}"
            area = [circle(c, SITE_RADIUS_MI * 1.05)]
            for layer in LAYERS:
                jobs[(k, layer)] = (layer, geom, "esriGeometryEnvelope", box, area)
            plan["sites"].append((e["name"], c, k))
        if route_ok:
            path = [[lon, lat] for lat, lon in r["route"]["coords"]]
            box = bbox_around([tuple(p) for p in path], 0.1)
            geom = {"paths": [[[round(x, 6), round(y, 6)] for x, y in path]], "spatialReference": {"wkid": 4326}}
            k = f"route:{r['uid']}"
            quads, _ = corridor(path, CORRIDOR_MI)
            for layer in LAYERS:
                jobs[(k, layer)] = (layer, geom, "esriGeometryPolyline", box, quads)
            plan["route"] = (path, k)
        plans[r["uid"]] = plan
    print(f"  environment: {len(plans)} projects to check, {len(jobs)} layer queries ({sum(1 for layer, geom, _, box, clips in jobs.values() if cache_key(layer, geom, box, clips) not in _cache)} not cached)")
    got = fetch_all(jobs, network)
    for r in recs:
        plan = plans.get(r["uid"])
        if not plan:
            continue
        env = {"checked": True, "sites": [], "route": None}
        for name, c, k in plan["sites"]:
            res = {layer: got[(k, layer)] for layer in LAYERS}
            env["sites"].append({"name": name, **read_site(c, res)})
        if plan["route"]:
            path, k = plan["route"]
            env["route"] = read_route(path, {layer: got[(k, layer)] for layer in LAYERS})
        keys = {k for _, _, k in plan["sites"]} | ({plan["route"][1]} if plan["route"] else set())
        missing = sorted({layer for (k, layer), v in got.items() if v is None and k in keys})
        if missing:
            env["missing"] = missing
        r["environment"] = env
    _cache_path.write_text(json.dumps(_cache, separators=(",", ":"), sort_keys=True))
    write_evidence(plans, got)


def write_evidence(plans, got):
    """The wetland and flood outlines behind each result, clipped to the area actually read (0.25 mi around each
    station, a 0.1 mi band along each traced line), plus that area's outline, for the map."""
    feats, seen = [], set()
    for plan in plans.values():
        areas = [(k, [circle(c, SITE_RADIUS_MI) + [circle(c, SITE_RADIUS_MI)[0]]]) for _, c, k in plan["sites"]]
        if plan["route"]:
            path, k = plan["route"]
            areas.append((k, [corridor(path, CORRIDOR_MI)[1]]))
        for k, outlines in areas:
            if k in seen:
                continue
            seen.add(k)
            for ring in outlines:
                feats.append({"type": "Feature", "properties": {"layer": "footprint"}, "geometry": {"type": "LineString", "coordinates": [[round(x, 5), round(y, 5)] for x, y in ring]}})
            for layer in ("wetlands", "flood"):
                for f in got.get((k, layer)) or []:
                    if "rings" not in f:
                        continue
                    a = f["a"]
                    props = {"layer": layer}
                    if layer == "wetlands":
                        props["type"] = a.get("WETLAND_TYPE")
                        props["water"] = props["type"] in WATER
                    else:
                        props["zone"] = a.get("FLD_ZONE")
                        props["sfha"] = a.get("SFHA_TF") == "T"
                        props["subtype"] = a.get("ZONE_SUBTY")
                        if a.get("FLD_ZONE") == "X" and not props["sfha"] and "0.2" not in (a.get("ZONE_SUBTY") or ""):
                            continue   # minimal flood hazard: not worth drawing
                    feats.append({"type": "Feature", "properties": props, "geometry": {"type": "MultiPolygon", "coordinates": multipolygon(f["rings"])}})
    dump_geojson(feats, ROOT / "data" / "env" / "evidence.geojson")


def dump_geojson(feats, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))


def regional_layers(offline=False):
    """Protected lands and critical habitat around the river, generalized for drawing. Refetched only when missing."""
    out = ROOT / "data" / "env"
    if not offline:
        domains("protected", True)
    x0, y0, x1, y1 = REGION
    env = {"xmin": x0, "ymin": y0, "xmax": x1, "ymax": y1, "spatialReference": {"wkid": 4326}}
    for name, layers in (("protected", ["protected"]), ("habitat", ["habitat_fws", "habitat_fws_proposed", "habitat_nmfs", "habitat_nmfs_rivers"])):
        path = out / f"{name}.geojson"
        if path.exists() or offline:
            continue
        feats = []
        for layer in layers:
            spec = LAYERS[layer]
            offset = 0
            while True:
                d = _post(spec["url"] + "/query", {"geometry": json.dumps(env), "geometryType": "esriGeometryEnvelope", "inSR": 4326, "outSR": 4326,
                                                   "spatialRel": "esriSpatialRelIntersects", "outFields": ",".join(spec["fields"]), "where": spec.get("where", "1=1"),
                                                   "returnGeometry": "true", "maxAllowableOffset": 0.0003, "geometryPrecision": 5,
                                                   "resultOffset": offset, "resultRecordCount": 1000, "f": "json"})
                page = d.get("features", [])
                for f in page:
                    a = {k.split(".")[-1]: v for k, v in f["attributes"].items()}
                    g = f.get("geometry") or {}
                    if name == "protected":
                        props = {"layer": "protected", **{k: v for k, v in protected_area(a).items() if v}}
                    else:
                        props = {"layer": "habitat", **{k: v for k, v in species(a, "NOAA Fisheries" if "nmfs" in layer else "USFWS").items() if v}}
                        if "proposed" in layer:
                            props["stage"] = "proposed"
                    if "rings" in g:
                        rings = [[[round(x, 5), round(y, 5)] for x, y in r] for r in (clip_ring(ring, REGION) for ring in g["rings"]) if r]
                        if rings:
                            feats.append({"type": "Feature", "properties": props, "geometry": {"type": "MultiPolygon", "coordinates": multipolygon(rings)}})
                    elif "paths" in g:
                        paths = [[[round(x, 5), round(y, 5)] for x, y in p] for path in g["paths"] for p in clip_path(path, REGION)]
                        if paths:
                            feats.append({"type": "Feature", "properties": props, "geometry": {"type": "MultiLineString", "coordinates": paths}})
                offset += len(page)
                if not page or not d.get("exceededTransferLimit"):
                    break
        dump_geojson(feats, path)
        print(f"  wrote data/env/{name}.geojson: {len(feats)} features")
