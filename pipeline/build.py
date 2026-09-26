"""Build data/projects.json for the app from the public source documents.

    python3 pipeline/build.py            # uses cached OSM/Nominatim lookups, fetches only what's missing
    python3 pipeline/build.py --offline  # never touches the network
"""
import datetime as dt
import difflib
import re
import statistics
import sys

import fetch
import parse_desc
import parse_ga
from common import ROOT, dump, haversine_mi, load, midpoint, norm_name
from geocode import OSMIndex, geocode_project
from lines import Grid

sys.setrecursionlimit(20000)
TODAY = dt.date(2026, 9, 26)
DEFAULT_HALF_LINE_MI = 10.0
_ov = load(ROOT / "data" / "overrides.json")
RADIUS_OVERRIDES = _ov.get("radius", {})
ENDPOINT_NOTES = _ov.get("endpoint_notes", {})   # uncertainty when only one end of a line could be located and no length is stated


def sponsor_points():
    pts, issues = {}, []
    for p in load(ROOT / "data" / "starter_projects.json")["projects"]:
        state = p["state"]
        for e in p["endpoints"]:
            if not e["point"]:
                continue
            k = f"{state}:{norm_name(e['name'])}"
            if k in pts and haversine_mi(pts[k]["lat"], pts[k]["lon"], e["point"]["lat"], e["point"]["lon"]) > 0.05:
                d = haversine_mi(pts[k]["lat"], pts[k]["lon"], e["point"]["lat"], e["point"]["lon"])
                issues.append(f"Starter workbook gives two coordinates for {e['name']!r} ({d:.2f} mi apart); the first is used")
                continue
            pts.setdefault(k, dict(e["point"]))
    return pts, issues


def slip_history(current, editions):
    """Match each current DESC project to earlier editions on Project ID *and* a similar name."""
    for r in current:
        hist = []
        for ed, recs in editions.items():
            best = None
            for o in recs:
                same_id = o["key"] == r["key"] or o["key"].lstrip("0") == r["key"].lstrip("0")
                sim = difflib.SequenceMatcher(None, norm_name(o["name"]), norm_name(r["name"])).ratio()
                if same_id and sim >= 0.5 and (best is None or sim > best[0]):
                    best = (sim, o)
            if best:
                hist.append({"edition": ed, "isd": best[1]["isd"], "name": best[1]["name"]})
        r["history"] = hist
        first = next((h for h in hist if h["isd"]), None)
        if first and r["isd"] and first["isd"] != r["isd"]:
            r["slip_days"] = (dt.date.fromisoformat(r["isd"]) - dt.date.fromisoformat(first["isd"])).days
        else:
            r["slip_days"] = 0 if first else None


def id_collisions(recs):
    out = []
    seen = {}
    for r in recs:
        seen.setdefault(r["key"], []).append(r)
    for k, rs in seen.items():
        if len(rs) > 1:
            out.append(f"Project ID {rs[0]['project_id']!r} is used by {len(rs)} different projects: " + "; ".join(x["name"] for x in rs))
            for x in rs:
                x["issues"].append({"level": "warn", "msg": f"Project ID {x['project_id']!r} is shared with another project in the same list"})
    for a in recs:
        for b in recs:
            if a is not b and a["key"] != b["key"] and a["key"].lstrip("0") and b["key"].lstrip("0").startswith(a["key"].lstrip("0")) and len(a["key"].lstrip("0")) >= 4:
                out.append(f"Project ID {a['project_id']!r} ({a['name']}) is a prefix of {b['project_id']!r} ({b['name']}); matching on ID alone would merge them")
    return out


def locate(recs, osm, spts, grid, anchors=None, offline=False):
    for r in recs:
        anchor = (anchors or {}).get(r.get("zone"))
        eps = geocode_project(r, osm, spts, anchor=anchor, allow_network=not offline)
        pts = [(e["point"]["lat"], e["point"]["lon"]) for e in eps if e["point"]]
        c = midpoint(pts)
        r["endpoints"] = eps
        r["center"] = {"lat": round(c[0], 6), "lon": round(c[1], 6)} if c else None
        located = [e for e in eps if e["point"]]
        if not located:
            r["radiusMi"] = None
            r["locationConfidence"] = "none"
        else:
            rad = max(e["radiusMi"] for e in located)
            if len(located) < len(eps):
                rad += (r["miles"] / 2) if r.get("miles") else DEFAULT_HALF_LINE_MI
            r["radiusMi"] = round(max(rad, RADIUS_OVERRIDES.get(r["uid"].rsplit(":", 1)[0] if r["state"] == "SC" else r["uid"], 0)), 2)
            order = ["none", "low", "ambiguous", "medium", "high"]
            r["locationConfidence"] = min((e["confidence"] for e in eps), key=order.index)
        r["route"] = None
        if len(located) >= 2 and grid:
            legs = [grid.route(a["point"], b["point"]) for a, b in zip(located, located[1:])]
            if all(legs):
                coords = [c for leg in legs for c in leg["coords"]]
                r["route"] = {"miles": round(sum(l["miles"] for l in legs), 2), "coords": coords,
                              "source": "shortest path along OpenStreetMap power=line ways between the endpoints"}
                if r.get("miles") and abs(r["route"]["miles"] - r["miles"]) / r["miles"] > 0.35:
                    r["issues"].append({"level": "info", "msg": f"mapped route is {r['route']['miles']} mi but the source states {r['miles']} mi"})


def zone_anchors(ga):
    by = {}
    for r in ga:
        if r.get("zone") and r.get("center") and r["locationConfidence"] == "high":
            by.setdefault(r["zone"], []).append(r["center"])
    return {z: {"lat": statistics.median(p["lat"] for p in ps), "lon": statistics.median(p["lon"] for p in ps)}
            for z, ps in by.items() if len(ps) >= 3}


def to_app(r):
    uid = r["uid"].replace(":", "-")
    if r["state"] == "SC":
        uid = "DESC-" + r["key"]
    return {
        "id": uid, "utility": r["utility"], "owner": r["owner"], "state": r["state"], "name": r["name"],
        "projectId": r["project_id"], "status": r["status"], "zone": r.get("zone"), "zoneName": r.get("zone_name"),
        "description": r["description"],
        "endpoints": [{"name": e["name"], "point": e["point"], "method": e["method"], "confidence": e["confidence"],
                       "radiusMi": e["radiusMi"], "evidence": e["evidence"]} for e in r["endpoints"]],
        "center": r["center"], "radiusMi": r["radiusMi"], "locationConfidence": r["locationConfidence"],
        "inServiceDate": r["isd"], "inServiceRaw": r["isd_raw"],
        "window": r["window"], "cost": r["cost"], "miles": r.get("miles"), "route": r.get("route"),
        "change": r.get("change"), "slipYears": r.get("slip_years"),
        "history": r.get("history"), "slipDays": r.get("slip_days"),
        "source": r["source"], "issues": r["issues"],
        "locationNote": ENDPOINT_NOTES.get(r["uid"].rsplit(":", 1)[0] if r["state"] == "SC" else r["uid"]),
    }


def main():
    offline = "--offline" in sys.argv
    if not offline:
        fetch.main()
    desc_eds = parse_desc.main()
    desc = desc_eds["2026-2030"]
    ga = parse_ga.main()
    older = {k: v for k, v in desc_eds.items() if k != "2026-2030"}
    slip_history(desc, dict(sorted(older.items())))
    dq_global = id_collisions(desc)

    osm, grid = OSMIndex(), Grid()
    spts, sp_issues = sponsor_points()
    dq_global += sp_issues
    locate(desc, osm, spts, grid, offline=offline)
    locate(ga, osm, spts, grid, offline=offline)
    anchors = zone_anchors(ga)
    locate(ga, osm, spts, grid, anchors=anchors, offline=offline)   # second pass: zone-aware disambiguation

    for r in desc + ga:
        if r["isd"] and dt.date.fromisoformat(r["isd"]) < TODAY:
            r["issues"].append({"level": "info", "msg": f"in-service date {r['isd']} has passed; status in source is {r['status']!r}"})
        for e in r["endpoints"]:
            if not e["point"]:
                r["issues"].append({"level": "warn", "msg": f"endpoint {e['name']!r} could not be located"})
            elif e["confidence"] in ("ambiguous", "low"):
                r["issues"].append({"level": "info", "msg": f"endpoint {e['name']!r} located with {e['confidence']} confidence ({e['method']})"})

    removed = load(ROOT / "data" / "build" / "ga_removed.json")
    projects = [to_app(r) for r in desc + ga]
    out = {
        "generated": TODAY.isoformat(),
        "sources": [
            {"id": "desc", "title": "DESC Planned Transmission Projects $2M and above, 2026-2030", "url": parse_desc.EDITIONS["2026-2030"], "projects": len(desc)},
            {"id": "ga", "title": parse_ga.DOC, "url": parse_ga.URL, "projects": len(ga)},
            {"id": "desc-old", "title": "DESC lists 2024-2028 (challenge zip) and 2025-2029, used only for schedule history", "url": parse_desc.EDITIONS["2025-2029"]},
            {"id": "osm", "title": "OpenStreetMap substations, plants and power lines (Overpass, 2026-09-26)", "url": "https://www.openstreetmap.org/copyright"},
        ],
        "zoneAnchors": anchors,
        "removedGeorgia": removed,
        "dataQuality": dq_global,
        "projects": projects,
    }
    dump(out, ROOT / "data" / "projects.json")
    loc = sum(1 for p in projects if p["center"])
    print(f"wrote data/projects.json: {len(projects)} projects, {loc} located, "
          f"{sum(1 for p in projects if p['route'])} with traced routes, {len(dq_global)} list-level data issues")


if __name__ == "__main__":
    main()
