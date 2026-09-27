"""Turn project names into endpoint names, then endpoint names into coordinates with provenance.

Order of trust (first hit wins):
  1. data/overrides.json "points"   - hand-sited, each with a written reason
  2. reference coordinates          - Projects_Overlaps.xlsx, the challenge's own trusted starter workbook
  3. OpenStreetMap substation/plant - exact, then token-subset name match, on the correct side of the state line
  4. Nominatim place (town)         - coarse fallback, wide uncertainty. The result's own name must be the
                                      station name, and it is searched near the project when anything locates it
Each located endpoint carries method, confidence and an uncertainty radius in miles.

Separately, described_stations() finds existing stations the filing's description names (a line the project
loops in, a station it connects to). They locate the project's neighborhood, never its endpoints.
"""
import difflib
import json
import math
import re
import time
import urllib.parse
import urllib.request

from common import CACHE, ROOT, haversine_mi, load, norm_name, xy_mi

OVERRIDES = load(ROOT / "data" / "overrides.json")
RADIUS = {"manual": 0.5, "reference": 0.5, "osm-exact": 0.5, "osm-partial": 1.5, "town": 6.0}
CONF = {"manual": "high", "reference": "high", "osm-exact": "high", "osm-partial": "medium", "town": "low"}

# Savannah River, coarse polyline north -> south. Used only to keep SC names on the SC side and GA names in GA.
RIVER = [(35.00, -83.11), (34.48, -82.85), (34.07, -82.64), (33.66, -82.20), (33.45, -81.97),
         (33.10, -81.60), (32.70, -81.40), (32.35, -81.15), (32.03, -80.88), (31.90, -80.70)]
SLACK_MI = 3.0
FAR_MI = 80.0      # no single project here spans more than this; farther endpoints are a wrong same-name match
NOT_GRID = re.compile(r"solar|wastewater|treatment|landfill|farm|customer|school|hospital|university|mill\b", re.I)

VOLT = re.compile(r"\b\d{2,3}(?:\s*[-/]\s*\d{1,3})?\s*kv\b", re.I)
STOP = re.compile(r"\b(rebuild|rebuilds|reconductor|construct|construction|upgrade|upgrades|replace|replacement|new|line|lines|"
                  r"tie|tap|loop|sub|substation|switching station|fold-in|fold in|network improvements|improvements|"
                  r"relay|modernization|breaker|breakers|auto transformer|autotransformer|transformer|statcom|cap bank|"
                  r"series reactor|reactors?|conversion|strategic|solution|project|area|bus|jumper|spdc|second|add|"
                  r"overstressed|network|switch|transmission|terminal|equipment|protection|capacitor)\b", re.I)


def side_of_river(lat, lon):
    """Positive = SC side, negative = GA side, magnitude = distance in miles to the coarse river line."""
    best = None
    lat0 = 33.0
    p = xy_mi(lat, lon, lat0)
    for (la1, lo1), (la2, lo2) in zip(RIVER, RIVER[1:]):
        a, b = xy_mi(la1, lo1, lat0), xy_mi(la2, lo2, lat0)
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
        cx, cy = a[0] + t * dx, a[1] + t * dy
        d = math.hypot(p[0] - cx, p[1] - cy)
        if best is None or d < best[0]:
            cross = dx * (p[1] - a[1]) - dy * (p[0] - a[0])   # >0 left of the southbound river = east = SC
            best = (d, 1 if cross > 0 else -1)
    return best[0] * best[1]


# Coarse state outlines (lat, lon); the shared edge is the RIVER line above.
SC_POLY = [(35.215, -83.11), (35.18, -82.30), (35.20, -81.04), (35.15, -80.93), (34.82, -80.80), (34.80, -79.67),
           (33.85, -78.54), (32.00, -80.60)] + RIVER[::-1][1:]
GA_POLY = [(35.00, -85.61), (35.00, -83.11)] + RIVER[1:] + [(30.70, -81.45), (30.70, -82.00), (30.36, -82.04),
           (30.60, -84.86), (31.00, -85.00), (32.00, -85.06), (33.00, -85.18)]
COVERED = ("SC", "GA")   # the only states outlined here: a project anywhere else can't be placed


def in_poly(lat, lon, poly):
    inside = False
    for (y1, x1), (y2, x2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def on_state_side(state, lat, lon):
    """In the named state, or within SLACK_MI of the river (stations sit on both banks, and on dams)."""
    own, other = (SC_POLY, GA_POLY) if state == "SC" else (GA_POLY, SC_POLY)
    if in_poly(lat, lon, own):
        return True
    return in_poly(lat, lon, other) and abs(side_of_river(lat, lon)) <= SLACK_MI


def ov_key(r):
    """The key a project goes by in overrides.json: its uid's plan prefix and printed ID."""
    return f"{r['uid'].split(':')[0]}:{r['key']}"


def endpoint_names(rec):
    ov = OVERRIDES["endpoints"].get(ov_key(rec))
    if ov is not None:
        return ov
    if rec.get("endpoint_names") is not None:   # a parser that knows its filing's title format names the stations
        return list(rec["endpoint_names"])
    name = rec["name"]
    if rec.get("plan") == "ga":   # Georgia ITS title prefixes are not conventions of every GA plan
        name = re.sub(r"^(SAV|GTC|MEAG|DU|SPC|GRID)\s*[:\-]\s*", "", name, flags=re.I)
        name = re.sub(r"^CC\s*[:\-–]\s*", "", name, flags=re.I)
        name = re.sub(r"^.*\bAT\s+", "", name, flags=re.I)
    name = name.split(":")[0]
    name = re.sub(r"\([^)]*\)", " ", name)
    name = re.sub(r"#\s*\d+", " ", name)
    m = VOLT.search(name)
    if m:
        name = name[:m.start()]
    parts = []
    for part in re.split(r"\s*[-–&]\s*", name):
        part = part.strip(" ,/")
        # Line Creek, New Hampton and Project Speedway are names. A descriptor
        # can end a name only after at least one name word has been retained.
        name_prefixes = {"line", "new", "project", "switch"}
        stop = next((m for m in STOP.finditer(part) if m.start() > 0 or m.group().lower() not in name_prefixes), None)
        part = part[:stop.start()].strip() if stop else part
        if len(part) > 1:
            parts.append(part)
    return parts[:3]


# A South Carolina town can have a DESC and a Santee Cooper station of the same name (Bluffton has both). A station
# OSM tags with another South Carolina owner is kept, but at medium confidence, since it may be that owner's yard.
SC_OWNERS = {"DESC": re.compile(r"dominion|sce&g|south carolina (?:electric|gas|generating)|scana", re.I),
             "SCPSA": re.compile(r"santee cooper|public service authority|scpsa", re.I)}
SC_UTILITY = re.compile(r"dominion|sce&g|south carolina|scana|santee|public service authority|duke|progress", re.I)


def other_sc_owner(rec, cand):
    """The OSM operator tag when it names a different South Carolina utility than the project's, else None."""
    own, op = SC_OWNERS.get(rec.get("utility")), cand.get("operator") or ""
    if rec["state"] != "SC" or not own or not op or own.search(op) or not SC_UTILITY.search(op):
        return None
    return op


def compatible_directions(a, b):
    """Do not turn East Villa Rica into West Villa Rica via a fuzzy name match."""
    aa, bb = set(a.split()), set(b.split())
    return not any(left in aa and right in bb or right in aa and left in bb
                   for left, right in (("east", "west"), ("north", "south")))


class OSMIndex:
    def __init__(self):
        els = load(CACHE / "osm_substations_ga_sc.json")["elements"]
        self.items = []
        for e in els:
            t = e.get("tags", {})
            names = {t[k] for k in ("name", "alt_name", "old_name", "short_name") if k in t}
            c = e.get("center") or ({"lat": e["lat"], "lon": e["lon"]} if "lat" in e else None)
            if not names or not c:
                continue
            for n in names:
                self.items.append({"norm": norm_name(n), "name": n, "lat": c["lat"], "lon": c["lon"],
                                   "osm": f"{e['type']}/{e['id']}", "power": t.get("power"), "operator": t.get("operator", "")})

    def find(self, query, state):
        q = norm_name(query)
        if not q:
            return [], None
        exact = [i for i in self.items if i["norm"] == q and on_state_side(state, i["lat"], i["lon"])]
        if exact:
            return exact, "osm-exact"
        # "Wadley Primary" and "West Point Dam" are mapped as "Wadley" and "West Point"
        base = re.sub(r" (primary|dam)$", "", q)
        suffix = [i for i in self.items if base != q and i["norm"] == base and not NOT_GRID.search(i["name"])
                  and on_state_side(state, i["lat"], i["lon"])]
        if suffix:
            return suffix, "osm-partial"
        qt = set(q.split())
        part = [i for i in self.items if qt and qt <= set(i["norm"].split()) and len(set(i["norm"].split()) - qt) <= 1
                and not NOT_GRID.search(i["name"]) and on_state_side(state, i["lat"], i["lon"])]
        if part:
            return part, "osm-partial"
        close = [i for i in self.items if len(q) > 5 and compatible_directions(q, i["norm"])
                 and not NOT_GRID.search(i["name"]) and difflib.SequenceMatcher(None, q, i["norm"]).ratio() >= 0.9
                 and on_state_side(state, i["lat"], i["lon"])]
        return close, ("osm-partial" if close else None)


def cluster(cands, within=1.0):
    """Collapse candidates that describe the same place (a substation and its plant, duplicate nodes)."""
    groups = []
    for c in cands:
        for g in groups:
            if haversine_mi(g[0]["lat"], g[0]["lon"], c["lat"], c["lon"]) <= within:
                g.append(c)
                break
        else:
            groups.append([c])
    return groups


_nom_cache_path = CACHE / "nominatim.json"
_nom = json.loads(_nom_cache_path.read_text()) if _nom_cache_path.exists() else {}


NEAR_DEG = 0.6   # half-width of the search box around a project's known location (~40 mi)
PLACE_CLASSES = {"place", "boundary", "highway", "waterway", "landuse", "power"}   # not peaks, wetlands, churches, shops
_ROAD = {"dr": "drive", "ave": "avenue", "av": "avenue", "hwy": "highway", "pkwy": "parkway", "blvd": "boulevard",
         "ln": "lane", "co": "county", "cty": "county"}


def place_key(s):
    return " ".join(_ROAD.get(w, w) for w in norm_name(s).split())


def names_place(result, query):
    """A town, road or creek stands in for a station only when it carries the station's name. This keeps
    "South Griffin" off "Alvin Griffin Irrigation Pond Dam", a courthouse, church or park off any station, and a
    region ("North Georgia") off a street that happens to share it."""
    name, q = place_key(result.get("name") or result["display_name"].split(",")[0]), place_key(query)
    # a name of two or more words may carry a leading qualifier: Truman Parkway is "Harry Truman Parkway"
    named = name == q or (len(q.split()) >= 2 and name.endswith(" " + q))
    return result.get("class") in PLACE_CLASSES and named and not re.search(r"\b(georgia|carolina)\b", q)


def nominatim(query, state, network=True, near=None):
    key = f"{query}|{state}" + (f"|near {near['lat']:.2f},{near['lon']:.2f}" if near else "")
    if key not in _nom and not network:
        return []
    if key not in _nom:
        params = {"q": f"{query}, {'South Carolina' if state == 'SC' else 'Georgia'}", "format": "json", "limit": 3, "countrycodes": "us"}
        if near:
            la, lo = round(near["lat"], 2), round(near["lon"], 2)
            params.update(viewbox=f"{lo - NEAR_DEG},{la + NEAR_DEG},{lo + NEAR_DEG},{la - NEAR_DEG}", bounded=1)
        url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "GridLock-ShellHacks-2026/0.1 (hackathon prototype)"})
        try:
            _nom[key] = json.loads(urllib.request.urlopen(req, timeout=20).read())
        except Exception as e:  # offline: record the miss, don't crash the build
            print("  nominatim failed", query, e)
            return None
        time.sleep(1.1)
        _nom_cache_path.write_text(json.dumps(_nom, indent=1))
    return [{"lat": float(r["lat"]), "lon": float(r["lon"]), "label": f"{r.get('class')}/{r.get('type')}: {r['display_name']}"}
            for r in _nom[key] if on_state_side(state, float(r["lat"]), float(r["lon"])) and names_place(r, query)
            and not (near and haversine_mi(float(r["lat"]), float(r["lon"]), near["lat"], near["lon"]) > FAR_MI)]


# Capitalized words that open a sentence or an instruction in the filings, not a station name
LEAD = {"at", "the", "a", "an", "and", "or", "to", "from", "in", "on", "near", "with", "for", "of", "this", "also", "both",
        "existing", "new", "build", "building", "rebuild", "rebuilding", "construct", "constructing", "install", "installing",
        "replace", "replacing", "upgrade", "upgrading", "reconductor", "add", "loop", "convert", "make", "remove", "expand",
        "approximately", "retire", "disconnect", "terminate", "connect", "fold", "split", "relocate", "extend", "move",
        "complete", "energize", "cut", "reconfigure", "gpc", "gtc", "meag", "desc", "apc", "sav"}
DESC_RADIUS_MIN = 5.0   # a new station connected to a named one can sit a few miles from it


def described_stations(rec, osm, anchor=None):
    """Existing stations the filing's own description names, each matched to exactly one OSM substation or plant.

    A candidate is a whole run of capitalized words in the description ("Bonaire Primary", "Little Ogeechee").
    Only leading verbs and articles are dropped; a run is never cut to a shorter name inside it, so
    "Big South Griffin" cannot become Griffin. The name must equal an OSM name exactly (after norm_name),
    on the right side of the state line, form a single place, and lie within FAR_MI of the planning zone.
    """
    text = rec.get("description") or ""
    found = {}
    for m in re.finditer(r"[A-Z][A-Za-z'’]*\.?(?:[ \t]+[A-Z][A-Za-z'’]*\.?)*", text):
        words = m.group().rstrip(".").split()
        while words and words[0].lower().rstrip(".") in LEAD:
            words = words[1:]
        name = " ".join(words)
        key = norm_name(name)
        if len(key) < 4 or key in found:
            continue
        cands = [i for i in osm.items if i["norm"] == key and not NOT_GRID.search(i["name"]) and on_state_side(rec["state"], i["lat"], i["lon"])]
        if anchor:
            cands = [i for i in cands if haversine_mi(i["lat"], i["lon"], anchor["lat"], anchor["lon"]) <= FAR_MI]
        groups = cluster(cands)
        if len(groups) != 1:
            continue
        g = groups[0]
        start = max(0, text.rfind(".", 0, m.start()) + 1)
        end = text.find(".", m.end())
        quote = text[start:end + 1 if end >= 0 else len(text)].strip()
        found[key] = {"name": name, "point": {"lat": round(g[0]["lat"], 6), "lon": round(g[0]["lon"], 6)},
                      "evidence": f"Named in the filing description (“{quote}”), matched to OSM {g[0]['osm']} {g[0]['name']!r}"}
    return list(found.values())


def geocode_project(rec, osm, reference_points, anchor=None, allow_network=True):
    names = endpoint_names(rec)
    resolved = []
    for n in names:
        key = norm_name(n)
        pt = OVERRIDES["points"].get(f"{rec['state']}:{key}")
        if pt:
            confidence = pt.get("confidence", CONF["manual"])
            radius = pt.get("radiusMi", RADIUS["manual"])
            if confidence not in ("low", "medium", "high") or not isinstance(radius, (int, float)) or not math.isfinite(radius) or radius <= 0:
                raise ValueError(f"Invalid confidence/radiusMi for point override {rec['state']}:{key}")
            resolved.append({"name": n, "cands": [[{"lat": pt["lat"], "lon": pt["lon"]}]], "method": "manual",
                             "confidence": confidence, "radiusMi": radius, "evidence": pt["why"]})
            continue
        sp = reference_points.get(f"{rec['state']}:{key}")
        if sp and anchor and haversine_mi(sp["lat"], sp["lon"], anchor["lat"], anchor["lon"]) > FAR_MI:
            sp = None   # same name, different place (e.g. the Augusta-area Goshen vs Goshen (SAV))
        if sp:
            ref = {"name": n, "cands": [[sp]], "method": "reference", "evidence": "Projects_Overlaps.xlsx (reference dataset)"}
            owner = sp.get("utility")
            if rec["state"] == "SC" and owner and rec.get("utility") and owner != rec["utility"]:
                # the workbook's South Carolina stations are DESC's; another owner's station of the same name may differ
                ref.update(confidence="medium", radiusMi=RADIUS["osm-partial"],
                           evidence=f"Projects_Overlaps.xlsx (reference dataset) gives this point for {owner}'s station of this name; it may be another utility's station")
            resolved.append(ref)
            continue
        cands, method = osm.find(n, rec["state"])
        if cands:
            resolved.append({"name": n, "cands": cluster(cands), "method": method,
                             "evidence": None})
            continue
        resolved.append({"name": n, "cands": [], "method": None, "evidence": "no match in overrides, reference dataset, OSM or Nominatim"})

    # Town fallback, searched near what already locates the project: the zone or described stations, else its
    # other stations when each matched a single place. A statewide search finds the wrong "Garrett Road".
    fixed = [r["cands"][0][0] for r in resolved if len(r["cands"]) == 1]
    near = anchor or ({"lat": sum(p["lat"] for p in fixed) / len(fixed), "lon": sum(p["lon"] for p in fixed) / len(fixed)} if fixed else None)
    for r in resolved:
        if r["method"] or len(norm_name(r["name"])) <= 2:
            continue
        towns = nominatim(r["name"], rec["state"], network=allow_network, near=near) if near else None
        if not towns:   # statewide, but still no farther than FAR_MI from what locates the project
            towns = [t for t in nominatim(r["name"], rec["state"], network=allow_network) or []
                     if not near or haversine_mi(t["lat"], t["lon"], near["lat"], near["lon"]) <= FAR_MI]
        if towns:
            r.update(cands=[[t] for t in towns], method="town", evidence=None)

    # joint resolution: when a name matches several places, pick the combination that keeps the
    # endpoints closest to each other and to the planning-zone anchor (if the source gives a zone)
    import itertools
    opts = [range(len(r["cands"])) if r["cands"] else [None] for r in resolved]
    def spread(combo):
        pts = [resolved[i]["cands"][k][0] for i, k in enumerate(combo) if k is not None]
        if anchor:
            pts = pts + [anchor]
        return sum(haversine_mi(a["lat"], a["lon"], b["lat"], b["lon"]) for a, b in itertools.combinations(pts, 2))
    combos = list(itertools.islice(itertools.product(*opts), 5000))
    choice = list(min(combos, key=spread)) if combos else []
    disambiguated = bool(anchor) or sum(1 for r in resolved if r["cands"]) > 1
    # drop endpoints that land implausibly far from the rest of the project (a same-name place elsewhere).
    # The weaker evidence loses; with a zone anchor as the only reference, the endpoint loses.
    rank = {"manual": 4, "reference": 4, "osm-exact": 3, "osm-partial": 2, "town": 1}
    for i, r in enumerate(resolved):
        if not r["cands"]:
            continue
        me = r["cands"][choice[i]][0]
        peers = [j for j in range(len(resolved)) if j != i and resolved[j]["cands"]]
        refs = [(resolved[j]["cands"][choice[j]][0], rank[resolved[j]["method"]]) for j in peers] or ([(anchor, 5)] if anchor else [])
        if refs and all(haversine_mi(me["lat"], me["lon"], o["lat"], o["lon"]) > FAR_MI for o, _ in refs) \
                and rank[r["method"]] <= min(k for _, k in refs):
            d = min(haversine_mi(me["lat"], me["lon"], o["lat"], o["lon"]) for o, _ in refs)
            r["evidence"] = f"rejected a {r['method']} match {d:.0f} mi from the rest of the project ({me.get('label', 'same name, different place')})"
            r["cands"] = []

    endpoints = []
    for i, r in enumerate(resolved):
        if not r["cands"]:
            endpoints.append({"name": r["name"], "point": None, "method": None, "confidence": "none", "radiusMi": None, "evidence": r["evidence"]})
            continue
        g = r["cands"][choice[i]]
        c = g[0]
        conf, method = r.get("confidence", CONF[r["method"]]), r["method"]
        radius = r.get("radiusMi", RADIUS[method])
        note = r["evidence"] or ("Nominatim: " + c["label"] if method == "town" else
                                f"OSM {', '.join(sorted({x['osm'] + ' ' + repr(x['name']) for x in g})[:2])}")
        other = other_sc_owner(rec, c) if method.startswith("osm") else None
        if other:
            conf = "medium" if conf == "high" else conf
            radius = max(radius, RADIUS["osm-partial"])
            note += f"; OSM tags its operator {other!r}, so it may be another utility's station of the same name"
        if len(r["cands"]) > 1:
            alts = [x[0] for k, x in enumerate(r["cands"]) if k != choice[i]]
            far = max(haversine_mi(c["lat"], c["lon"], a["lat"], a["lon"]) for a in alts)
            if not disambiguated:
                conf = "ambiguous"
            note += f"; {len(r['cands'])} places share this name (next one {far:.0f} mi away)" + (
                "; picked the one nearest the other endpoint(s)" + (" and the planning zone" if anchor else "") if disambiguated else "")
        endpoints.append({"name": r["name"], "point": {"lat": round(c["lat"], 6), "lon": round(c["lon"], 6)}, "method": method,
                          "confidence": conf, "radiusMi": radius, "evidence": note})
    return endpoints
