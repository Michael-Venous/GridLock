"""Which changes a subscriber gets, and the email that says so. Standard library only.

The area rules mirror src/changes.js (inArea, changeItems) so the email lists exactly what the Changes tab
shows for the same area. tests/test_alerts.py checks the two against the same fixtures.
"""
import datetime as dt
import math
import re

R_MI = 3958.7613
KINDS = {"added": "New projects", "removed": "Dropped projects", "date": "Rescheduled", "cost": "Re-costed", "name": "Renamed",
         "pairNew": "New pairs", "pairGone": "Pairs gone", "pairTiming": "Pair timing changed"}
ALERT_KINDS = ["added", "removed", "date", "pairNew", "pairGone"]
REGION = (-86.0, 30.0, -78.0, 36.0)       # lon/lat box an area has to sit in (GA and SC)
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")


def point_in_polygon(pt, ring):
    x, y = pt
    hit = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < xi + (y - yi) * (xj - xi) / (yj - yi):
            hit = not hit
        j = i
    return hit


def _xy(pt, lat0):
    return (math.radians(pt[0]) * R_MI * math.cos(math.radians(lat0)), math.radians(pt[1]) * R_MI)


def miles_to_polygon(pt, ring):
    if point_in_polygon(pt, ring):
        return 0.0
    lat0 = pt[1]
    p = _xy(pt, lat0)
    best = math.inf
    for a, b in zip(ring, ring[1:]):
        a, b = _xy(a, lat0), _xy(b, lat0)
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = dx * dx + dy * dy
        t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L)) if L else 0
        best = min(best, math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy))
    return best


def in_area(points, area, buffer_mi=0):
    if not area:
        return True
    return any((miles_to_polygon(p, area) <= buffer_mi) if buffer_mi > 0 else point_in_polygon(p, area) for p in points or [])


def change_items(event):
    items = [{"type": "project", "kinds": c["what"] if c["kind"] == "changed" else [c["kind"]], "change": c, "points": c["points"]} for c in event["projects"]]
    items += [{"type": "pair", "kinds": [{"new": "pairNew", "gone": "pairGone", "timing": "pairTiming"}[p["kind"]]], "change": p, "points": p["points"]}
              for p in event["pairs"]]
    return items


def items_for(event, sub):
    kinds = set(sub.get("kinds") or ALERT_KINDS)
    return [i for i in change_items(event) if in_area(i["points"], sub.get("area"), float(sub.get("bufferMi") or 0)) and kinds & set(i["kinds"])]


# ---- validating what a browser sends ----

class Invalid(ValueError):
    pass


def clean_subscription(body):
    email = str(body.get("email") or "").strip()
    if len(email) > 254 or not EMAIL.match(email):
        raise Invalid("that doesn't look like an email address")
    area = body.get("area")
    if area is not None:
        if not isinstance(area, list) or not 4 <= len(area) <= 200:
            raise Invalid("the area needs between 3 and 199 corners")
        try:
            area = [[round(float(x), 5), round(float(y), 5)] for x, y in area]
        except (TypeError, ValueError):
            raise Invalid("the area's corners must be longitude, latitude pairs")
        if any(not (REGION[0] <= x <= REGION[2] and REGION[1] <= y <= REGION[3]) for x, y in area):
            raise Invalid("the area has to be in Georgia or South Carolina")
        if area[0] != area[-1]:
            area.append(area[0])
    try:
        buffer_mi = float(body.get("bufferMi") or 0)
    except (TypeError, ValueError):
        raise Invalid("the distance must be a number of miles")
    if not 0 <= buffer_mi <= 25:
        raise Invalid("the distance must be between 0 and 25 miles")
    kinds = body.get("kinds") or ALERT_KINDS
    if not isinstance(kinds, list) or not kinds or any(k not in KINDS for k in kinds):
        raise Invalid("unknown kind of change")
    return {"email": email, "area": area, "bufferMi": buffer_mi, "kinds": sorted(set(kinds))}


# ---- the email ----

def _date(s):
    if not s:
        return "unknown"
    d = dt.date.fromisoformat(s)
    return f"{d:%b} {d.day}, {d.year}"


def _money(n):
    if n is None:
        return "unknown"
    return f"${n / 1e6:.1f}M" if n >= 1e6 else f"${round(n / 1000)}k"


def _months(days):
    if days is None:
        return ""
    m = round(abs(days) / 30.44)
    span = f"{m} month{'s' if m != 1 else ''}" if m >= 1 else f"{abs(days)} days"
    return f"{span} {'later' if days > 0 else 'earlier'}" if days else "same day"


def _side(c):
    return "DESC" if c["state"] == "SC" else ("GPC Savannah" if c.get("utility") == "SAV" else c.get("utility") or "Georgia ITS")


def _page(src):
    return f"{src['url']}#page={src['page']}"


def item_line(i):
    c = i["change"]
    if i["type"] == "pair":
        head = f"DESC {c['desc']['projectId']} x {c['ga']['projectId']} ({c['desc']['name']} x {c['ga']['name']})"
        if c["kind"] == "timing":
            return f"{head}: {c['miles']:.1f} mi apart; in-service gap {c['oldGapDays']} -> {c['gapDays']} days."
        gap = "in-service dates unknown" if c["gapDays"] is None else f"in service {c['gapDays']} days apart"
        return f"{head}: {c['miles']:.1f} mi apart, {gap}. {c['reason'][0].upper()}{c['reason'][1:]}."
    head = f"{_side(c)} {c['projectId']} {c['name']}"
    facts = []
    if c["kind"] == "added":
        facts.append(f"In service {_date(c['isd'])}.")
    if c["kind"] == "removed":
        facts.append(f"Was due {_date(c['isd'])}. {c['reason']}")
    if "date" in i["kinds"]:
        facts.append(f"In service {_date(c['isd'])} (was {_date(c['oldIsd'])}, {_months(c.get('days'))}).")
    if "cost" in i["kinds"]:
        facts.append(f"Estimate {_money(c.get('cost'))} (was {_money(c.get('oldCost'))}).")
    if "name" in i["kinds"]:
        facts.append(f"Was \"{c['oldName']}\".")
    facts.append(f"{'Previous filing' if c['kind'] == 'removed' else 'Filing'}, p. {c['source']['page']}: {_page(c['source'])}")
    return f"{head}. " + " ".join(facts)


def message(event, items, sub, sample=False, app_url=None):
    """(subject, body) for one subscriber and one filing."""
    who = "DESC" if event["state"] == "SC" else "Georgia ITS"
    edition = event["id"].split("-", 1)[1]
    subject = f"{'[Sample] ' if sample else ''}Gridlock: {who} {edition} filing, {len(items)} change{'s' if len(items) != 1 else ''} in your area"
    lines = []
    if sample:
        lines += ["SAMPLE: this is built from a filing that was already published, so you can see what an alert looks like.", ""]
    when = {"PDF creation date": "dated", "GA PSC filed date": "filed with the Georgia PSC"}.get(event["dateBasis"], "found")
    lines += [f"{event['title']}, {when} {_date(event['date'])}. Compared with {event['previous']['title']}.", event["url"], ""]
    if not items:
        lines += ["Nothing in this filing touches your area.", ""]
    for kind, label in KINDS.items():
        group = [i for i in items if (i["kinds"][0] if i["type"] == "pair" else _primary(i)) == kind]
        if not group:
            continue
        lines.append(f"{label} ({len(group)})")
        lines += [f"- {item_line(i)}" for i in group]
        lines.append("")
    area = "the whole region" if not sub.get("area") else f"an area you drew ({len(sub['area']) - 1} corners)"
    if sub.get("area") and float(sub.get("bufferMi") or 0) > 0:
        area += f" plus {float(sub['bufferMi']):g} mi around it"
    lines += [f"You asked for {', '.join(KINDS[k].lower() for k in sub.get('kinds') or ALERT_KINDS)} in {area}."]
    if app_url:
        lines.append(f"See them on the map, or change your area: {app_url}")
    lines += ["", "Gridlock compares each new public filing with the one before it. Locations are estimates; check the filing before acting on anything here."]
    return subject[:100], "\n".join(lines)


def _primary(i):
    order = ["added", "removed", "date", "name", "cost"]
    return next(k for k in order if k in i["kinds"])


def new_filing_notice(filing):
    """When a new filing can't be compared automatically: say it exists, and link it."""
    subject = f"Gridlock: new {filing['utility']} filing posted"
    body = "\n".join([f"{filing['title']} was posted ({filing['date']}).", filing["url"], "",
                      "Gridlock couldn't compare it with the previous edition automatically yet, so this email only tells you it exists."
                      " A follow-up comes once the comparison is done."])
    return subject[:100], body
