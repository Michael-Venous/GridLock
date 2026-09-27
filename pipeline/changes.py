"""What changed each time a utility published a new edition: projects and qualifying pairs. Standard library only.

Filings (data/filings.json) are replayed in edition order within each plan. Before each new edition, the state is the latest
edition of each plan; after it, the new edition replaces its plan's old one. The difference is an event:
projects added, dropped (with the reason the filing gives, if any), rescheduled, renamed or re-costed, and
qualifying pairs (the challenge's rule: centers under 25 miles, one project from each of two plans in different states) that
appeared, went away or changed timing.

Projects are matched across editions on their ID. A plan that reuses IDs (DESC) needs a similar name too;
an ID kept with a different name, and nothing else carrying it, is reported as renamed. A plan whose IDs are
unique (Georgia's TEAMS numbers) matches on the ID alone.
"""
import datetime as dt
import difflib
import math

from common import ROOT, dump, norm_name

MAX_MILES = 25
R_MI = 3958.7613          # same Earth radius as src/match.js, so pairs agree with the app
TIMING_DAYS = 30          # a qualifying pair's in-service gap must move this much to count as a timing change


def miles(a, b):
    r = math.pi / 180
    dlat, dlon = (b["lat"] - a["lat"]) * r, (b["lon"] - a["lon"]) * r
    x = math.sin(dlat / 2) ** 2 + math.cos(a["lat"] * r) * math.cos(b["lat"] * r) * math.sin(dlon / 2) ** 2
    return R_MI * 2 * math.atan2(math.sqrt(x), math.sqrt(1 - x))


def sim(a, b):
    return difflib.SequenceMatcher(None, norm_name(a["name"]), norm_name(b["name"])).ratio()


def match(old, new, reuses_ids):
    """[(old_rec or None, new_rec or None, how)] for one plan's two editions. reuses_ids: the plan prints one ID for
    different projects (DESC), so IDs compare without leading zeros and a match needs a similar name too."""
    out, used_old = [], set()
    kf = (lambda r: r["key"].lstrip("0")) if reuses_ids else (lambda r: r["key"])
    by_key = {}
    for i, o in enumerate(old):
        by_key.setdefault(kf(o), []).append(i)
    pending = []
    for n in new:
        k = kf(n)
        cands = [i for i in by_key.get(k, []) if i not in used_old]
        if not cands:
            out.append((None, n, "added"))
            continue
        best = max(cands, key=lambda i: sim(old[i], n))
        if not reuses_ids or sim(old[best], n) >= 0.5:
            used_old.add(best)
            out.append((old[best], n, "same"))
        else:
            pending.append((n, k))
    for n, k in pending:
        cands = [i for i in by_key.get(k, []) if i not in used_old]
        if len(cands) == 1 and len(by_key[k]) == 1:
            used_old.add(cands[0])
            out.append((old[cands[0]], n, "renamed"))
        else:
            out.append((None, n, "added"))
    out += [(o, None, "removed") for i, o in enumerate(old) if i not in used_old]
    return out


def points(r):
    pts = [[e["point"]["lon"], e["point"]["lat"]] for e in r.get("endpoints") or [] if e.get("point")]
    if r.get("center"):
        pts.append([r["center"]["lon"], r["center"]["lat"]])
    return pts


def brief(r):
    return {"lineage": r["lineage"], "uid": r["uid"], "key": r["key"], "projectId": r["project_id"], "name": r["name"], "state": r["state"], "utility": r["utility"],
            "plan": r["plan"], "isd": r["isd"], "center": r.get("center"), "radiusMi": r.get("radiusMi"), "points": points(r), "source": r["source"]}


def project_changes(old, new, reuses_ids, filing, removed_tables):
    """removed_tables: the new filing's own list of dropped projects ({key: {status, ...}}), or {} if it prints none."""
    out = []
    for o, n, how in match(old, new, reuses_ids):
        if how == "added":
            c = {"kind": "added", **brief(n), "appId": None}
            if n.get("change"):
                c["note"] = n["change"]
            if reuses_ids and any(n["key"].lstrip("0") == x["key"].lstrip("0") for x in old):
                c["note"] = f"Reuses Project ID {n['project_id']}, which the previous list gave to a different project."
            out.append(c)
        elif how == "removed":
            c = {"kind": "removed", **brief(o)}
            gone = removed_tables.get(o["key"])
            if gone:
                c["reason"] = f"{gone['status'].capitalize()}: listed in Table {3 if gone['status'] == 'cancelled' else 4} of the new plan"
            elif not removed_tables and o["isd"] and o["isd"] < filing["date"]:
                c["reason"] = f"No longer listed. Its in-service date ({o['isd']}) was before the new list came out; the list doesn't say whether it was finished or dropped."
            else:
                c["reason"] = "No longer listed; the filing doesn't say why."
            out.append(c)
        else:
            what = []
            # a plan with unique IDs matches on the ID alone, so its matches can carry a different name
            if how == "renamed" or sim(o, n) < 0.5:
                what.append("name")
            if o["isd"] != n["isd"]:
                what.append("date")
            if o["cost"]["total"] and n["cost"]["total"] and o["cost"]["total"] != n["cost"]["total"]:
                what.append("cost")
            if not what:
                continue
            c = {"kind": "changed", "what": what, **brief(n), "oldName": o["name"], "oldIsd": o["isd"], "oldSource": o["source"]}
            if o["isd"] and n["isd"]:
                c["days"] = (dt.date.fromisoformat(n["isd"]) - dt.date.fromisoformat(o["isd"])).days
            if "cost" in what:
                c["cost"], c["oldCost"] = n["cost"]["total"], o["cost"]["total"]
            if n.get("change"):
                c["note"] = n["change"]
            out.append(c)
    return out


def assign_lineage(editions_in_order, reuses_ids):
    """Give every record the same "lineage" id as its match in the edition before, so a project keeps one
    identity through renames. editions_in_order: [records] oldest first, one plan. A lineage is unique only
    within its plan, so it is always looked up together with the plan.

    A project new in its edition is named by its state, key and name. A filing can print one key and name twice
    (a parser derives the key from the name when there is no printed ID), and a project matched to the edition
    before can already carry that lineage; the repeat then takes the next free ordinal (:2, :3, ...) in print
    order, so no two projects of one edition share a lineage."""
    prev = None
    for recs in editions_in_order:
        for r in recs:
            r["lineage"] = None
        if prev is not None:
            for o, n, how in match(prev, recs, reuses_ids):
                if o and n:
                    n["lineage"] = o["lineage"]
        taken = {r["lineage"] for r in recs} - {None}
        for r in recs:
            if r["lineage"] is None:
                base = r["lineage"] = f"{r['state']}:{r['key']}:{norm_name(r['name'])}"
                k = 1
                while r["lineage"] in taken:
                    k += 1
                    r["lineage"] = f"{base}:{k}"
                taken.add(r["lineage"])
        prev = recs


def qualifying(a_recs, b_recs):
    """Cross-state pairs under MAX_MILES between two plans, keyed by plan and lineage."""
    pairs = {}
    for a in a_recs:
        if not a.get("center"):
            continue
        for b in b_recs:
            if a["state"] == b["state"] or not b.get("center"):
                continue
            d = miles(a["center"], b["center"])
            if d < MAX_MILES:
                gap = abs((dt.date.fromisoformat(a["isd"]) - dt.date.fromisoformat(b["isd"])).days) if a["isd"] and b["isd"] else None
                pairs[(a["plan"], a["lineage"], b["plan"], b["lineage"])] = {"a": a, "b": b, "miles": d, "gap": gap}
    return pairs


def all_pairs(current, editions, order):
    """Qualifying pairs across every two plans in current ({plan: its filing}); a is the plan that comes first in order."""
    ps = [p for p in order if p in current]
    out = {}
    for i, pa in enumerate(ps):
        for pb in ps[i + 1:]:
            out.update(qualifying(editions[current[pa]["id"]], editions[current[pb]["id"]]))
    return out


def pair_side(r, app_ids):
    return {"key": r["key"], "projectId": r["project_id"], "name": r["name"], "isd": r["isd"], "center": r["center"],
            "plan": r["plan"], "state": r["state"], "utility": r["utility"], "appId": app_ids.get((r["plan"], r["lineage"]))}


def label(c):
    """Use the utility name with Santee's row number, since its lists have no project IDs."""
    return f"Santee Cooper {c['projectId'].lower()}" if c.get("utility") == "SCPSA" else c["projectId"]


def pair_changes(before, after, changed_projects, app_ids):
    why = {(c["plan"], c["lineage"]): c for c in changed_projects}
    causes = lambda p, kind: [x for x in (why.get((p[s]["plan"], p[s]["lineage"])) for s in "ab") if x and x["kind"] == kind]
    entry = lambda p: {"a": pair_side(p["a"], app_ids), "b": pair_side(p["b"], app_ids), "miles": round(p["miles"], 2), "gapDays": p["gap"],
                       "points": points(p["a"]) + points(p["b"])}
    out = []
    for k, p in after.items():
        q = before.get(k)
        if not q:
            src = causes(p, "added")
            out.append({"kind": "new", **entry(p), "reason": f"{label(src[0])} is new" if src else "a project's date or location changed"})
        elif q["gap"] is not None and p["gap"] is not None and abs(q["gap"] - p["gap"]) >= TIMING_DAYS:
            out.append({"kind": "timing", **entry(p), "oldGapDays": q["gap"]})
    for k, q in before.items():
        if k in after:
            continue
        gone = causes(q, "removed")
        out.append({"kind": "gone", **entry(q), "reason": f"{label(gone[0])} was dropped" if gone else "a project's name, date or location changed"})
    return out


def events(filings, editions, removed_tables, plans, app_ids=None):
    """filings: oldest first. editions: {filing id: located records}. removed_tables: {filing id: the filing's own
    list of dropped projects} (Georgia's Tables 3 and 4). plans: the registry's plans, in registry order.
    app_ids: {(plan, lineage): app id} of the projects in the current data, so entries can link to them."""
    app_ids = app_ids or {}
    for p in plans:
        assign_lineage([editions[f["id"]] for f in filings if f["plan"] == p], plans[p]["reusesIds"])
    state, out = {}, []
    for f in filings:
        prev = state.get(f["plan"])
        before = dict(state)
        state[f["plan"]] = f
        if not prev:
            continue
        changed = project_changes(editions[prev["id"]], editions[f["id"]], plans[f["plan"]]["reusesIds"], f, removed_tables.get(f["id"], {}))
        for c in changed:
            if c["kind"] != "removed" and (c["plan"], c["lineage"]) in app_ids:
                c["appId"] = app_ids[(c["plan"], c["lineage"])]
        pairs = pair_changes(all_pairs(before, editions, plans), all_pairs(state, editions, plans), changed, app_ids)
        counts = {"added": sum(1 for c in changed if c["kind"] == "added"), "removed": sum(1 for c in changed if c["kind"] == "removed"),
                  "rescheduled": sum(1 for c in changed if c["kind"] == "changed" and "date" in c["what"]),
                  "renamed": sum(1 for c in changed if c["kind"] == "changed" and "name" in c["what"]),
                  "recosted": sum(1 for c in changed if c["kind"] == "changed" and "cost" in c["what"]),
                  "pairsNew": sum(1 for p in pairs if p["kind"] == "new"), "pairsGone": sum(1 for p in pairs if p["kind"] == "gone"),
                  "pairsTiming": sum(1 for p in pairs if p["kind"] == "timing")}
        out.append({"id": f["id"], "date": f["date"], "dateBasis": f["dateBasis"], "plan": f["plan"], "state": f["state"], "utility": f["utility"],
                    "title": f["title"], "url": f["url"], "previous": {"id": prev["id"], "title": prev["title"], "date": prev["date"], "url": prev["url"]},
                    "counts": counts, "projects": changed, "pairs": pairs})
    return out


def write(filings, editions, removed_tables, plans, app_ids):
    evs = events(filings, editions, removed_tables, plans, app_ids)
    first = {}
    for f in filings:
        first.setdefault(f["plan"], f)
    baselines = {f["id"]: [dict(brief(r), appId=app_ids.get((r["plan"], r["lineage"])))
                           for r in editions[f["id"]]] for f in first.values()}
    out = {"baselines": baselines, "generated": None, "filings": [{k: f[k] for k in ("id", "plan", "utility", "state", "edition", "title", "date", "dateBasis", "url")} for f in filings],
           "events": evs[::-1]}
    dump(out, ROOT / "data" / "changes.json")
    for ev in evs:
        print(f"  {ev['id']} vs {ev['previous']['id']}: {ev['counts']}")
    return out
