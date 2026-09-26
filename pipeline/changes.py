"""What changed each time a utility published a new edition: projects and qualifying pairs. Standard library only.

Filings (data/filings.json) are replayed in date order. Before each new edition, the state is the latest
edition of each utility; after it, the new edition replaces its utility's old one. The difference is an event:
projects added, dropped (with the reason the filing gives, if any), rescheduled, renamed or re-costed, and
qualifying pairs (the challenge's rule: centers under 25 miles) that appeared, went away or changed timing.

Projects are matched across editions on their ID. DESC reuses IDs, so a DESC match also needs a similar name;
an ID kept with a different name, and nothing else carrying it, is reported as renamed. Georgia's TEAMS
numbers are unique, so a Georgia match is on the number alone.
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


def match(old, new, state):
    """[(old_rec or None, new_rec or None, how)] for one utility's two editions."""
    out, used_old = [], set()
    by_key = {}
    for i, o in enumerate(old):
        by_key.setdefault(o["key"].lstrip("0") if state == "SC" else o["key"], []).append(i)
    pending = []
    for n in new:
        k = n["key"].lstrip("0") if state == "SC" else n["key"]
        cands = [i for i in by_key.get(k, []) if i not in used_old]
        if not cands:
            out.append((None, n, "added"))
            continue
        best = max(cands, key=lambda i: sim(old[i], n))
        if state == "GA" or sim(old[best], n) >= 0.5:
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


def app_id(r):
    return f"DESC-{r['key']}" if r["state"] == "SC" else f"GA-{r['key']}"


def brief(r):
    return {"lineage": r["lineage"], "key": r["key"], "projectId": r["project_id"], "name": r["name"], "state": r["state"], "utility": r["utility"],
            "isd": r["isd"], "center": r.get("center"), "radiusMi": r.get("radiusMi"), "points": points(r), "source": r["source"]}


def project_changes(old, new, state, filing, removed_tables):
    out = []
    for o, n, how in match(old, new, state):
        if how == "added":
            c = {"kind": "added", **brief(n), "appId": None}
            if state == "GA" and n.get("change"):
                c["note"] = n["change"]
            if state == "SC" and any(n["key"].lstrip("0") == x["key"].lstrip("0") for x in old):
                c["note"] = f"Reuses Project ID {n['project_id']}, which the previous list gave to a different project."
            out.append(c)
        elif how == "removed":
            c = {"kind": "removed", **brief(o)}
            gone = removed_tables.get(o["key"]) if state == "GA" else None
            if gone:
                c["reason"] = f"{gone['status'].capitalize()}: listed in Table {3 if gone['status'] == 'cancelled' else 4} of the new plan"
            elif state == "SC" and o["isd"] and o["isd"] < filing["date"]:
                c["reason"] = f"No longer listed. Its in-service date ({o['isd']}) was before the new list came out; the list doesn't say whether it was finished or dropped."
            else:
                c["reason"] = "No longer listed; the filing doesn't say why."
            out.append(c)
        else:
            what = []
            if how == "renamed" or (state == "GA" and sim(o, n) < 0.5):
                what.append("name")
            if o["isd"] != n["isd"]:
                what.append("date")
            if state == "SC" and o["cost"]["total"] and n["cost"]["total"] and o["cost"]["total"] != n["cost"]["total"]:
                what.append("cost")
            if not what:
                continue
            c = {"kind": "changed", "what": what, **brief(n), "oldName": o["name"], "oldIsd": o["isd"], "oldSource": o["source"]}
            if o["isd"] and n["isd"]:
                c["days"] = (dt.date.fromisoformat(n["isd"]) - dt.date.fromisoformat(o["isd"])).days
            if "cost" in what:
                c["cost"], c["oldCost"] = n["cost"]["total"], o["cost"]["total"]
            if state == "GA" and n.get("change"):
                c["note"] = n["change"]
            out.append(c)
    return out


def assign_lineage(editions_in_order, state):
    """Give every record the same "lineage" id as its match in the edition before, so a project keeps one
    identity through renames. editions_in_order: [records] oldest first, one utility."""
    prev = None
    for recs in editions_in_order:
        for r in recs:
            r["lineage"] = f"{state}:{r['key']}:{norm_name(r['name'])}"
        if prev is not None:
            for o, n, how in match(prev, recs, state):
                if o and n:
                    n["lineage"] = o["lineage"]
        prev = recs


def qualifying(desc, ga):
    pairs = {}
    for a in desc:
        if not a.get("center"):
            continue
        for b in ga:
            if not b.get("center"):
                continue
            d = miles(a["center"], b["center"])
            if d < MAX_MILES:
                gap = abs((dt.date.fromisoformat(a["isd"]) - dt.date.fromisoformat(b["isd"])).days) if a["isd"] and b["isd"] else None
                pairs[(a["lineage"], b["lineage"])] = {"desc": a, "ga": b, "miles": d, "gap": gap}
    return pairs


def pair_side(r):
    return {"key": r["key"], "projectId": r["project_id"], "name": r["name"], "isd": r["isd"], "center": r["center"]}


def pair_changes(before, after, changed_projects):
    why = {c["lineage"]: c for c in changed_projects}
    out = []
    for k, p in after.items():
        q = before.get(k)
        entry = {"desc": pair_side(p["desc"]), "ga": pair_side(p["ga"]), "miles": round(p["miles"], 2), "gapDays": p["gap"],
                 "points": points(p["desc"]) + points(p["ga"])}
        if not q:
            src = [x for x in (why.get(p["desc"]["lineage"]), why.get(p["ga"]["lineage"])) if x and x["kind"] == "added"]
            entry["reason"] = f"{src[0]['projectId']} is new" if src else "a project's date or location changed"
            out.append({"kind": "new", **entry})
        elif q["gap"] is not None and p["gap"] is not None and abs(q["gap"] - p["gap"]) >= TIMING_DAYS:
            out.append({"kind": "timing", **entry, "oldGapDays": q["gap"]})
    for k, q in before.items():
        if k in after:
            continue
        entry = {"desc": pair_side(q["desc"]), "ga": pair_side(q["ga"]), "miles": round(q["miles"], 2), "gapDays": q["gap"],
                 "points": points(q["desc"]) + points(q["ga"])}
        gone = [x for x in (why.get(q["desc"]["lineage"]), why.get(q["ga"]["lineage"])) if x and x["kind"] == "removed"]
        entry["reason"] = f"{gone[0]['projectId']} was dropped" if gone else "a project's name, date or location changed"
        out.append({"kind": "gone", **entry})
    return out


def events(filings, editions, removed_tables):
    """editions: {filing id: located records}; removed_tables: {filing id: Table 3/4 dict} (Georgia only)."""
    for st in ("SC", "GA"):
        assign_lineage([editions[f["id"]] for f in filings if f["state"] == st], st)
    state, out = {}, []
    for f in filings:
        prev = state.get(f["state"])
        before = dict(state)
        state[f["state"]] = f
        if not prev:
            continue
        changed = project_changes(editions[prev["id"]], editions[f["id"]], f["state"], f, removed_tables.get(f["id"], {}))
        pairs = {}
        if before.get("SC") and before.get("GA"):
            b = qualifying(editions[before["SC"]["id"]], editions[before["GA"]["id"]])
            a = qualifying(editions[state["SC"]["id"]], editions[state["GA"]["id"]])
            pairs = pair_changes(b, a, changed)
        counts = {"added": sum(1 for c in changed if c["kind"] == "added"), "removed": sum(1 for c in changed if c["kind"] == "removed"),
                  "rescheduled": sum(1 for c in changed if c["kind"] == "changed" and "date" in c["what"]),
                  "renamed": sum(1 for c in changed if c["kind"] == "changed" and "name" in c["what"]),
                  "recosted": sum(1 for c in changed if c["kind"] == "changed" and "cost" in c["what"]),
                  "pairsNew": sum(1 for p in pairs if p["kind"] == "new"), "pairsGone": sum(1 for p in pairs if p["kind"] == "gone"),
                  "pairsTiming": sum(1 for p in pairs if p["kind"] == "timing")}
        out.append({"id": f["id"], "date": f["date"], "dateBasis": f["dateBasis"], "state": f["state"], "utility": f["utility"],
                    "title": f["title"], "url": f["url"], "previous": {"id": prev["id"], "title": prev["title"], "date": prev["date"], "url": prev["url"]},
                    "counts": counts, "projects": changed, "pairs": pairs})
    return out


def write(filings, editions, removed_tables, current_ids):
    evs = events(filings, editions, removed_tables)
    for ev in evs:
        for c in ev["projects"]:
            if c["kind"] != "removed" and app_id(c) in current_ids:
                c["appId"] = app_id(c)
    out = {"generated": None, "filings": [{k: f[k] for k in ("id", "utility", "state", "edition", "title", "date", "dateBasis", "url")} for f in filings],
           "events": evs[::-1]}
    dump(out, ROOT / "data" / "changes.json")
    for ev in evs:
        print(f"  {ev['id']} vs {ev['previous']['id']}: {ev['counts']}")
    return out
