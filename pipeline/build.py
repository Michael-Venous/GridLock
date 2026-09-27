"""Build data/projects.json for the app from the public source documents.

    python3 pipeline/build.py            # uses cached OSM/Nominatim lookups, fetches only what's missing
    python3 pipeline/build.py --offline  # never touches the network
"""
import datetime as dt
import difflib
import os
import re
import statistics
import sys
from collections import Counter

import changes
import environment
import fetch
import parse_desc
import parse_ga
from common import ROOT, dump, filings, haversine_mi, load, midpoint, norm_name
from geocode import DESC_RADIUS_MIN, OSMIndex, described_stations, geocode_project
from lines import Grid

sys.setrecursionlimit(20000)
# Fixed so local rebuilds reproduce; set GRIDLOCK_TODAY to rebuild as of another day (see issue #7).
TODAY = dt.date.fromisoformat(os.environ.get("GRIDLOCK_TODAY", "2026-09-26"))
DEFAULT_HALF_LINE_MI = 10.0
DESC_REACH_MAX_MI = 25.0   # stations named in a description farther apart than this don't mark one neighborhood
_ov = load(ROOT / "data" / "overrides.json")
RADIUS_OVERRIDES = _ov.get("radius", {})
ENDPOINT_NOTES = _ov.get("endpoint_notes", {})   # uncertainty when only one end of a line could be located and no length is stated
HOST_LINE = _ov.get("host_line", {})   # projects placed on the line they tap: its stations aren't the project's own ends, so no line is traced or drawn


def ov_key(r):
    """The key a project goes by in overrides.json: DESC:<ProjectID without spaces> or GA:<TEAMS>."""
    return r["uid"].rsplit(":", 1)[0] if r["state"] == "SC" else r["uid"]


def reference_points():
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


def slip_history(current, editions, need_name=True):
    """Match each current project to earlier editions on its ID and, for DESC (which reuses IDs), a similar name."""
    for r in current:
        hist = []
        for ed, recs in editions.items():
            best = None
            for o in recs:
                same_id = o["key"] == r["key"] or o["key"].lstrip("0") == r["key"].lstrip("0")
                sim = difflib.SequenceMatcher(None, norm_name(o["name"]), norm_name(r["name"])).ratio()
                if same_id and (sim >= 0.5 or not need_name) and (best is None or sim > best[0]):
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


PLACEMENT_NOTES = ("mapped route is", "route rejected:", "no station names could be extracted", "the stations named in the description",
                   "rebuilds ", "stated length (")


def locate(recs, osm, refs, grid, anchors=None, offline=False):
    for r in recs:
        # Georgia is located twice (the second pass uses zone anchors); drop the first pass's placement notes
        r["issues"] = [i for i in r["issues"] if not i["msg"].startswith(PLACEMENT_NOTES)]
        anchor = (anchors or {}).get(r.get("zone"))
        described = described_stations(r, osm, anchor) if osm else []
        dmid = midpoint([(d["point"]["lat"], d["point"]["lon"]) for d in described])
        # stations the filing names place the project more tightly than its planning zone's median
        near = {"lat": dmid[0], "lon": dmid[1]} if dmid else anchor
        eps = geocode_project(r, osm, refs, anchor=near, allow_network=not offline)
        pts = [(e["point"]["lat"], e["point"]["lon"]) for e in eps if e["point"]]
        c = midpoint(pts)
        r["endpoints"] = eps
        r["describedStations"] = described
        r["locatedBy"] = "endpoints" if c else None
        r["center"] = {"lat": round(c[0], 6), "lon": round(c[1], 6)} if c else None
        located = [e for e in eps if e["point"]]
        r["locationCompleteness"] = {"located": len(located), "total": len(eps)}
        reach = max((haversine_mi(*dmid, d["point"]["lat"], d["point"]["lon"]) for d in described), default=0)
        if not located and dmid and reach > DESC_REACH_MAX_MI:
            r["issues"].append({"level": "info", "msg": f"the stations named in the description span {2 * reach:.0f} mi, too wide to place the project"})
        if not located and dmid and reach <= DESC_REACH_MAX_MI:
            # None of the project's own stations can be placed (often a new one), but its description names
            # existing stations it connects to. The project is somewhere among them.
            r["locatedBy"] = "description"
            r["center"] = {"lat": round(dmid[0], 6), "lon": round(dmid[1], 6)}
            r["radiusMi"] = round(max(DESC_RADIUS_MIN, reach + 1.0, RADIUS_OVERRIDES.get(ov_key(r), 0)), 2)
            r["locationConfidence"] = "low"
        elif not located:
            r["radiusMi"] = None
            r["locationConfidence"] = "none"
        else:
            rad = max(e["radiusMi"] for e in located)
            if len(located) < len(eps):
                rad += (r["miles"] / 2) if r.get("miles") else DEFAULT_HALF_LINE_MI
            elif len(located) >= 2 and r.get("miles"):
                # A rebuild/reconductor often covers only part of the line between two stations; the
                # filing rarely says which part. Widen the radius so the true midpoint (anywhere on the
                # unstated section) still falls within it, rather than pinning it to the whole line's midpoint.
                span = haversine_mi(located[0]["point"]["lat"], located[0]["point"]["lon"],
                                     located[-1]["point"]["lat"], located[-1]["point"]["lon"])
                gap = span - r["miles"]
                if gap > 0:
                    rad = max(rad, gap / 2)
                    r["issues"].append({"level": "info", "msg": f"rebuilds {r['miles']} mi of a {span:.2f} mi station-to-station span; section not stated"})
                    if span > r["miles"] * 10:
                        r["issues"].append({"level": "warn", "msg": f"stated length ({r['miles']} mi) is far shorter than the {span:.2f} mi between its stations; check whether a station is placed correctly"})
            r["radiusMi"] = round(max(rad, RADIUS_OVERRIDES.get(ov_key(r), 0)), 2)
            order = ["none", "low", "ambiguous", "medium", "high"]
            r["locationConfidence"] = min((e["confidence"] for e in located), key=order.index)
            if len(located) < len(eps):
                r["locationConfidence"] = "low"
        r["route"] = None
        if not eps:
            r["issues"].append({"level": "warn", "msg": "no station names could be extracted; project requires a reviewed endpoint override"})
        station_methods = {"manual", "reference", "osm-exact", "osm-partial"}
        if len(located) >= 2 and len(located) == len(eps) and grid and ov_key(r) not in HOST_LINE and all(
                e["method"] in station_methods and e["confidence"] in ("medium", "high") for e in located):
            legs = [grid.route(a["point"], b["point"]) for a, b in zip(located, located[1:])]
            if all(legs):
                coords = [c for leg in legs for c in leg["coords"]]
                route_miles = round(sum(l["miles"] for l in legs), 2)
                if r.get("miles") and (route_miles > 3 * r["miles"] or route_miles < r["miles"] / 3):
                    r["issues"].append({"level": "warn", "msg": f"route rejected: {route_miles} mi traced vs {r['miles']} mi stated (more than 3× difference)"})
                    continue
                r["route"] = {"miles": route_miles, "coords": coords,
                              "verified": False, "method": "osm-shortest-path",
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


ZONE_OUTLIER_MI = 150   # farther than this from the rest of its zone, a placement is a name collision, not a site


def unplace_zone_outliers(ga):
    """A Georgia project placed far from every other project in its planning zone matched a same-named site elsewhere
    (zone 206 is metro Atlanta; its "Boulevard" and "Virginia Avenue" matched Savannah names). Leave it unplaced."""
    by = {}
    for r in ga:
        if r.get("zone") and r.get("center"):
            by.setdefault(r["zone"], []).append(r)
    for z, rs in by.items():
        if len(rs) < 5:
            continue
        mid = (statistics.median(r["center"]["lat"] for r in rs), statistics.median(r["center"]["lon"] for r in rs))
        for r in rs:
            d = haversine_mi(*mid, r["center"]["lat"], r["center"]["lon"])
            if d <= ZONE_OUTLIER_MI:
                continue
            r["issues"].append({"level": "warn", "msg": f"placed at {r['center']['lat']:.4f}, {r['center']['lon']:.4f}, {d:.0f} mi from the median of "
                                f"zone {z}'s {len(rs)} located projects; the station names likely matched a different site, so it is left unplaced"})
            for e in r["endpoints"]:
                e["point"], e["confidence"], e["radiusMi"] = None, "none", None
            r["center"], r["radiusMi"], r["locationConfidence"], r["route"], r["locatedBy"] = None, None, "none", None, None
            r["locationCompleteness"]["located"] = 0
            r["issues"] = [i for i in r["issues"] if not i["msg"].startswith("mapped route is")]


def cost_benchmark(desc):
    """$/mile from DESC's own list: line projects that state a length and whose cost row checks out."""
    rows = []
    for r in desc:
        errs = [i for i in r["issues"] if i["level"] == "error"]
        if r.get("miles") and r["miles"] >= 2 and r["cost"]["total"] and not errs and re.search(r"rebuild|construct|line", r["name"], re.I):
            rows.append({"id": r["project_id"], "name": r["name"], "miles": r["miles"], "cost": r["cost"]["total"],
                         "perMile": round(r["cost"]["total"] / r["miles"])})
    per = sorted(x["perMile"] for x in rows)
    return {"perMile": round(statistics.median(per)) if per else None, "low": per[len(per) // 4] if per else None,
            "high": per[(3 * len(per)) // 4] if per else None, "n": len(rows), "projects": rows,
            "basis": "median cost per stated mile of DESC 2026-2030 line projects with a stated length and a consistent cost row"}


def starter_status(desc, ga, removed):
    """What became of each project in the sponsor's 10-project sample, in the current filings."""
    out = []
    for p in load(ROOT / "data" / "starter_projects.json")["projects"]:
        pool = desc if p["state"] == "SC" else ga
        best = max(pool, key=lambda r: difflib.SequenceMatcher(None, norm_name(r["name"]), norm_name(p["name"])).ratio())
        sim = difflib.SequenceMatcher(None, norm_name(best["name"]), norm_name(p["name"])).ratio()
        gone = None
        if p["state"] == "GA" and removed:
            k, v = max(removed.items(), key=lambda kv: difflib.SequenceMatcher(None, norm_name(kv[1]["name"]), norm_name(p["name"])).ratio())
            if difflib.SequenceMatcher(None, norm_name(v["name"]), norm_name(p["name"])).ratio() > 0.5:
                gone = f"{v['status']} per Table {3 if v['status'] == 'cancelled' else 4} of the 2026-2035 plan (Teams {k}, was due {v['last_need']})"
        if sim >= 0.8:
            status = f"still planned as {best['project_id']}, in service {best['isd']}" + (f" (was {p['inServiceDate']})" if best["isd"] != p["inServiceDate"] else "")
        elif gone:
            status = gone
        else:
            status = "not in the 2026-2030 list (finished, dropped or renamed; the list does not say which)" if p["state"] == "SC" else "not in the 2026-2035 plan"
        out.append({"id": p["id"], "name": p["name"], "starterDate": p["inServiceDate"], "status": status})
    return out


def assign_app_ids(recs):
    """Keep existing links for unique printed IDs; disambiguate reused source IDs.

    The parsed DESC UID includes the source item number, so two independent
    records with a reused printed ID remain separately selectable. legacyId is
    only present on changed records; ambiguous old saved links must not guess.
    """
    legacy = ["DESC-" + r["key"] if r["state"] == "SC" else r["uid"].replace(":", "-") for r in recs]
    counts = Counter(legacy)
    for r, old in zip(recs, legacy):
        r["app_id"] = r["uid"].replace(":", "-") if counts[old] > 1 else old
        if counts[old] > 1:
            r["legacy_id"] = old
    ids = [r["app_id"] for r in recs]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate internal project IDs; source records cannot be distinguished safely")


def to_app(r):
    return {
        "id": r["app_id"], **({"legacyId": r["legacy_id"]} if r.get("legacy_id") else {}),
        "utility": r["utility"], "owner": r["owner"], "state": r["state"], "name": r["name"],
        "projectId": r["project_id"], "status": r["status"], "zone": r.get("zone"), "zoneName": r.get("zone_name"),
        "description": r["description"],
        "endpoints": [{"name": e["name"], "point": e["point"], "method": e["method"], "confidence": e["confidence"],
                       "radiusMi": e["radiusMi"], "evidence": e["evidence"]} for e in r["endpoints"]],
        "describedStations": r.get("describedStations", []), "locatedBy": r.get("locatedBy"),
        "center": r["center"], "radiusMi": r["radiusMi"], "locationConfidence": r["locationConfidence"],
        "locationCompleteness": r["locationCompleteness"],
        "inServiceDate": r["isd"], "inServiceRaw": r["isd_raw"],
        "window": r["window"], "cost": r["cost"], "miles": r.get("miles"), "route": r.get("route"),
        "change": r.get("change"), "slipYears": r.get("slip_years"),
        "history": r.get("history"), "slipDays": r.get("slip_days"),
        "source": r["source"], "issues": r["issues"],
        "locationNote": ENDPOINT_NOTES.get(ov_key(r)),
        "hostLine": ov_key(r) in HOST_LINE,
        "environment": r.get("environment"),
    }


def main():
    offline = "--offline" in sys.argv
    if not offline:
        fetch.main()
    registry = filings()
    cur_desc, cur_ga = filings("desc")[-1], filings("ga")[-1]
    desc_eds = parse_desc.main()
    ga_eds = parse_ga.main()
    desc = desc_eds[cur_desc["edition"]]
    ga, removed = ga_eds[cur_ga["edition"]]
    slip_history(desc, {k: v for k, v in sorted(desc_eds.items()) if k != cur_desc["edition"]})
    slip_history(ga, {k: v[0] for k, v in sorted(ga_eds.items()) if k != cur_ga["edition"]}, need_name=False)
    dq_global = id_collisions(desc)

    osm, grid = OSMIndex(), Grid()
    refs, ref_issues = reference_points()
    dq_global += ref_issues
    locate(desc, osm, refs, grid, offline=offline)
    locate(ga, osm, refs, grid, offline=offline)
    anchors = zone_anchors(ga)
    locate(ga, osm, refs, grid, anchors=anchors, offline=offline)   # second pass: zone-aware disambiguation
    unplace_zone_outliers(ga)

    for r in desc + ga:
        if r["isd"] and dt.date.fromisoformat(r["isd"]) < TODAY:
            r["issues"].append({"level": "info", "msg": f"in-service date {r['isd']} has passed; status in source is {r['status']!r}"})
        for e in r["endpoints"]:
            if not e["point"]:
                r["issues"].append({"level": "warn", "msg": f"endpoint {e['name']!r} could not be located"})
            elif e["confidence"] in ("ambiguous", "low"):
                r["issues"].append({"level": "info", "msg": f"endpoint {e['name']!r} located with {e['confidence']} confidence ({e['method']})"})

    assign_app_ids(desc + ga)
    environment.check(desc + ga, offline=offline)
    environment.regional_layers(offline=offline)

    # Older editions, for the change log: reuse each project's current location, place only the ones that are gone.
    editions = {f["id"]: (desc_eds[f["edition"]] if f["parser"] == "desc" else ga_eds[f["edition"]][0]) for f in registry}
    for st, parser in (("SC", "desc"), ("GA", "ga")):
        changes.assign_lineage([editions[f["id"]] for f in filings(parser)], st)
    here = {r["lineage"]: r for r in desc + ga}
    for f in registry:
        recs = editions[f["id"]]
        if recs is desc or recs is ga:
            continue
        gone = []
        for r in recs:
            cur = here.get(r["lineage"])
            if cur:
                for k in ("endpoints", "describedStations", "locatedBy", "center", "radiusMi", "locationConfidence"):
                    r[k] = cur[k]
            else:
                gone.append(r)
        locate(gone, osm, refs, None, anchors=anchors if f["parser"] == "ga" else None, offline=offline)
        if f["parser"] == "ga":
            unplace_zone_outliers(gone)
    changes.write(registry, editions, {f["id"]: ga_eds[f["edition"]][1] for f in filings("ga")}, {to_app(r)["id"] for r in desc + ga})

    projects = [to_app(r) for r in desc + ga]
    out = {
        "generated": TODAY.isoformat(),
        "sources": [
            {"id": "desc", "title": cur_desc["title"], "url": cur_desc["url"], "projects": len(desc)},
            {"id": "ga", "title": cur_ga["title"], "url": cur_ga["url"], "projects": len(ga)},
            *[{"id": f["id"], "title": f"{f['title']} (earlier edition: schedule history and the change log)", "url": f["url"]}
              for f in registry if f not in (cur_desc, cur_ga)],
            {"id": "osm", "title": "OpenStreetMap substations, plants and power lines (Overpass, 2026-09-26)", "url": "https://www.openstreetmap.org/copyright"},
        ],
        "environmentSources": environment.SOURCES,
        "environmentRadiusMi": environment.SITE_RADIUS_MI,
        "zoneAnchors": anchors,
        "costBenchmark": cost_benchmark(desc),
        "starterStatus": starter_status(desc, ga, removed),
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
