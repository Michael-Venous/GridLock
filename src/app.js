import { matchProjects, gapLabel, overlapLabel, opportunityText, savingsEstimate, yardScenario, projectType, sortPairs, milesBetween, SORTS, WEIGHTS, YARD_BASIS, MAX_MILES } from "./match.js";

const $ = id => document.getElementById(id);
const state = {
  data: null, projects: [], allPairs: [], pairs: [], selectedProject: null, selectedPair: null, search: "", distance: 25, year: 2035,
  gap: "all", hidePast: true, includePossible: false, shortlistOnly: false, sort: "score", asOf: null, detailTab: "summary",
  shortlist: new Set(), yard: { acres: 5, months: null, leaseRate: 0.10, surface: "mats", surfacePerAcre: YARD_BASIS.matsPerAcre.value, roadMiles: 0.25 },
};
const FILTER_DEFAULTS = { search: "", distance: 25, year: 2035, gap: "all", hidePast: true, includePossible: false, shortlistOnly: false };
const SHORTLIST_KEY = "gridlock.shortlist";
function loadShortlist() { try { return new Set(JSON.parse(localStorage.getItem(SHORTLIST_KEY) ?? "[]")); } catch { return new Set(); } }
function saveShortlist() { try { localStorage.setItem(SHORTLIST_KEY, JSON.stringify([...state.shortlist])); } catch { /* storage unavailable: shortlist lasts for this visit */ } }
const bounds = [[-85.8, 30.3], [-78.4, 35.3]];
const border = [[-82.7, 31.85], [-80.6, 33.95]];
const COLORS = { desc: "#0a8494", gpc: "#cb6e30" };
// OpenFreeMap: free OpenStreetMap vector tiles, no API key.
const BASEMAP = "https://tiles.openfreemap.org/styles/positron";
const FALLBACK_STYLE = { version: 8, glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf", sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#e5ecec" } }] };
const MILES_PER_DEGREE = 69.09;
let map = null, mapReady = false, styleFailed = false, pendingFocus = null;
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const lngLat = point => [point.lon, point.lat];
const featureCollection = features => ({ type: "FeatureCollection", features });
const pointFeature = (point, properties) => ({ type: "Feature", geometry: { type: "Point", coordinates: lngLat(point) }, properties });
// A circle of the given radius in miles, as a polygon, so it stays true to scale at every zoom.
function ring(center, miles, steps = 64) {
  const dLat = miles / MILES_PER_DEGREE, dLon = dLat / Math.cos(center.lat * Math.PI / 180);
  return Array.from({ length: steps + 1 }, (_, i) => { const t = 2 * Math.PI * i / steps; return [center.lon + dLon * Math.cos(t), center.lat + dLat * Math.sin(t)]; });
}
function geometryFeatures(project) {
  const s = side(project), out = [];
  if (project.center && project.radiusMi) out.push({ type: "Feature", geometry: { type: "Polygon", coordinates: [ring(project.center, project.radiusMi)] }, properties: { kind: "uncertainty", side: s } });
  if (project.route) out.push({ type: "Feature", geometry: { type: "LineString", coordinates: project.route.coords.map(([lat, lon]) => [lon, lat]) }, properties: { kind: "route", side: s } });
  for (const e of project.endpoints) if (e.point) out.push(pointFeature(e.point, { kind: "endpoint", side: s, title: `${e.name} · ${e.method} · ${e.confidence}` }));
  return out;
}

function h(tag, className = "", text = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}
function link(href, text) { const a = h("a", "", text); a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer"; return a; }
const side = project => project.state === "SC" ? "desc" : "gpc";
const sideName = project => project.state === "SC" ? "DESC" : project.utility === "SAV" ? "GPC · Savannah" : project.utility;
function projectLabel(project) { return `${project.projectId} · ${project.name}`; }
function formatDate(value) { return value ? new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`)) : "Unknown"; }
const money = n => n == null ? "—" : n >= 1e6 ? `$${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M` : `$${Math.round(n / 1000)}k`;
function datePassed(value) { return Boolean(value && value < state.asOf); }
function pdfLink(source) { return `${source.url}#page=${source.page}`; }
function includesSearch(pair, q = state.search) {
  return !q || [pair.a, pair.b].some(p => `${p.id} ${p.projectId} ${p.name} ${p.utility} ${p.endpoints.map(e => e.name).join(" ")}`.toLowerCase().includes(q));
}
function bySelectedYear(project, year = state.year) { return !project.inServiceDate || Number(project.inServiceDate.slice(0, 4)) <= year; }
const certaintyText = {
  robust: "Inside 25 miles even at the edges of both location estimates.",
  sensitive: "Inside 25 miles at the best-estimate locations, but location uncertainty could push it past 25.",
  possible: "Outside 25 miles at the best-estimate locations, but within reach of the location uncertainty. Not a qualifying pair.",
};

const GAP_LABELS = { ahead: "Build overlap from today on", overlap: "Build overlap (any time)", 365: "In-service within 1 year", 730: "In-service within 2 years" };

// The filter predicate, parameterized so the empty state can ask "what if this one filter were off?".
function filterPairs(f) {
  return state.allPairs.filter(pair =>
    (f.includePossible || pair.qualifies) && bySelectedYear(pair.a, f.year) && bySelectedYear(pair.b, f.year) && pair.miles < f.distance + (pair.qualifies ? 0 : 99) &&
    (!f.hidePast || !pair.bothPast) &&
    (f.gap === "all" || (f.gap === "ahead" ? pair.remainingDays > 0 : f.gap === "overlap" ? pair.overlapDays > 0 : pair.gapDays !== null && pair.gapDays <= Number(f.gap))) &&
    (!f.shortlistOnly || state.shortlist.has(pair.id)) &&
    includesSearch(pair, f.search) && (!f.selectedProject || pair.a.id === f.selectedProject || pair.b.id === f.selectedProject));
}

// Each non-default filter, with a label and a way to turn it off. Drives the chips and the empty state.
function activeFilters() {
  const out = [];
  if (state.search) out.push({ key: "search", label: `Search: “${state.search}”` });
  if (state.year < FILTER_DEFAULTS.year) out.push({ key: "year", label: `In service by ${state.year}` });
  if (state.distance < FILTER_DEFAULTS.distance) out.push({ key: "distance", label: `Within ${state.distance} mi` });
  if (state.gap !== "all") out.push({ key: "gap", label: GAP_LABELS[state.gap] });
  if (!state.hidePast) out.push({ key: "hidePast", label: "Including pairs already past both dates" });
  if (state.includePossible) out.push({ key: "includePossible", label: "Including possible (non-qualifying) pairs" });
  if (state.shortlistOnly) out.push({ key: "shortlistOnly", label: "Shortlist only" });
  if (state.selectedProject) out.push({ key: "selectedProject", label: `Project: ${state.projects.find(p => p.id === state.selectedProject)?.projectId}` });
  return out;
}

function clearFilter(key) {
  if (key === "selectedProject") state.selectedProject = null;
  else state[key] = FILTER_DEFAULTS[key];
  syncControls();
}

function syncControls() {
  $("search").value = state.search; $("distance").value = String(state.distance); $("year").value = String(state.year);
  $("gap").value = state.gap; $("hide-past").checked = state.hidePast; $("possible").checked = state.includePossible;
  $("shortlist-only").checked = state.shortlistOnly; $("sort").value = state.sort;
  applyFilters();
}

function applyFilters() {
  if (state.selectedProject && !bySelectedYear(state.projects.find(project => project.id === state.selectedProject))) state.selectedProject = null;
  state.pairs = sortPairs(filterPairs(state), state.sort);
  if (state.selectedPair && !state.pairs.some(p => p.id === state.selectedPair)) state.selectedPair = null;
  $("distance-value").textContent = `${state.distance} mi`;
  $("year-value").textContent = String(state.year);
  const q = state.pairs.filter(p => p.qualifies).length;
  $("result-count").textContent = `${q} qualifying ${q === 1 ? "pair" : "pairs"}${state.pairs.length > q ? ` + ${state.pairs.length - q} possible` : ""}`;
  $("queue-title").textContent = state.selectedProject ? `Pairs for ${state.projects.find(p => p.id === state.selectedProject)?.projectId}` : "Ranked pairs";
  $("clear-selection").disabled = !state.selectedPair && !state.selectedProject;
  const filterCount = activeFilters().filter(f => !["search", "selectedProject"].includes(f.key)).length;
  $("reset-filters").disabled = !filterCount;
  $("filter-count").hidden = !filterCount; $("filter-count").textContent = String(filterCount);
  $("shortlist-count").textContent = String(state.shortlist.size);
  $("shortlist-export").disabled = !state.shortlist.size;
  renderChips(); renderList(); renderMap(); renderDetail();
}

function renderChips() {
  const box = $("chips"); box.replaceChildren();
  const active = activeFilters();
  box.hidden = !active.length;
  for (const f of active) {
    const chip = h("button", "chip", f.label); chip.type = "button"; chip.title = "Remove this filter";
    chip.append(h("span", "chip-x", "×")); chip.addEventListener("click", () => clearFilter(f.key)); box.append(chip);
  }
}

function overviewStats() {
  const located = state.projects.filter(p => p.center);
  const qualifying = state.allPairs.filter(p => p.qualifies);
  const paired = new Set(qualifying.flatMap(p => [p.a.id, p.b.id]));
  const cards = [[String(state.projects.length), "projects parsed"], [`${located.length}`, "located on the map"], [String(qualifying.length), "qualifying pairs"],
    [String(qualifying.filter(p => p.remainingDays > 0).length), "building at the same time"], [String(located.filter(p => !paired.has(p.id)).length), "with no partner in range"]];
  const row = h("div", "stat-row");
  row.append(...cards.map(([value, label]) => { const card = h("div", "stat"); card.append(h("strong", "", value), h("span", "", label)); return card; }));
  return row;
}

function badge(text, kind = "") { return h("span", `badge ${kind}`, text); }

// Say which filter emptied the list, and how many pairs removing it would bring back.
function emptyState() {
  const box = h("div", "empty-state");
  const project = state.projects.find(p => p.id === state.selectedProject);
  const lonely = project && !state.allPairs.some(p => p.qualifies && (p.a.id === project.id || p.b.id === project.id));
  box.append(h("strong", "", !project?.center && project ? "Project not located" : lonely ? "No partner across the river" : "No pairs match these filters"));
  if (project && !project.center) { box.append(h("p", "", `${project.projectId} could not be located, so it can't be paired. The Data quality tab lists why.`)); return box; }
  if (lonely) {
    const n = nearestPartner(project);
    box.append(h("p", "", `${project.projectId} has no project across the river within ${MAX_MILES} miles.${n ? ` The nearest is ${n.p.projectId} (${n.p.name}), ${n.miles.toFixed(1)} mi away.` : ""} Most projects in both plans have no match; that is expected.`));
    return box;
  }
  const hints = activeFilters().map(f => ({ f, n: filterPairs({ ...state, [f.key]: f.key === "selectedProject" ? null : FILTER_DEFAULTS[f.key] }).length })).filter(x => x.n > 0);
  if (!hints.length) { box.append(h("p", "", "No single filter explains it; try Reset filters.")); return box; }
  const ul = h("ul", "empty-hints");
  hints.sort((x, y) => y.n - x.n).forEach(({ f, n }) => {
    const li = h("li"); const btn = h("button", "link-button", `Remove “${f.label}”`); btn.type = "button"; btn.addEventListener("click", () => clearFilter(f.key));
    li.append(btn, document.createTextNode(` → ${n} ${n === 1 ? "pair" : "pairs"}`)); ul.append(li);
  });
  box.append(ul);
  return box;
}

function renderList() {
  const list = $("match-list"); list.replaceChildren();
  if (!state.pairs.length) { list.append(emptyState()); return; }
  let rank = 0;
  state.pairs.forEach(pair => {
    const button = h("button", `pair-row${pair.id === state.selectedPair ? " active" : ""}${pair.qualifies ? "" : " possible"}`);
    button.type = "button"; button.setAttribute("aria-pressed", String(pair.id === state.selectedPair));
    button.setAttribute("aria-label", `${pair.qualifies ? `Rank ${rank + 1}, score ${pair.score.total}` : "Possible pair, not qualifying"}: ${pair.a.name} and ${pair.b.name}, ${pair.miles.toFixed(1)} miles`);
    button.append(h("span", "pr-rank", pair.qualifies ? String(++rank) : "?"));
    const body = h("span", "pr-body");
    for (const p of [pair.a, pair.b]) { const n = h("span", `pr-name ${side(p)}`, p.name); n.title = projectLabel(p); body.append(n); }
    const tags = h("span", "pr-tags");
    const tag = (text, kind = "") => tags.append(h("span", `tag ${kind}`, text));
    if (state.shortlist.has(pair.id)) tag("Shortlisted", "star");
    if (!pair.qualifies) tag("Could qualify", "muted");
    if (pair.shared.length) tag(`Shares ${pair.shared[0].a}`, "good");
    if (pair.remainingDays > 0) tag("Building together", "good");
    else if (pair.overlapDays > 0) tag("Overlap past", "muted");
    if (pair.certainty === "sensitive") tag("Location-sensitive", "warn");
    if (pair.bothPast) tag("Both dates passed", "muted");
    else if (datePassed(pair.a.inServiceDate) || datePassed(pair.b.inServiceDate)) tag("A date has passed", "warn");
    if (tags.childElementCount) body.append(tags);
    const fig = h("span", "pr-fig"); const mi = h("strong", "", pair.miles.toFixed(1)); mi.append(h("small", "", " mi"));
    fig.append(mi, h("span", "", pair.gapDays === null ? "date unknown" : `${gapShort(pair.gapDays)} apart`));
    button.append(body, fig);
    button.addEventListener("click", () => selectPair(pair));
    list.append(button);
  });
}

function gapShort(days) { return days < 60 ? `${days} d` : days < 730 ? `${Math.round(days / 30.44)} mo` : `${(days / 365.25).toFixed(1)} yr`; }

function selectPair(pair) { state.selectedPair = pair.id; applyFilters(); focusPair(pair); }

function nearestPartner(project) {
  if (!project.center) return null;
  let best = null;
  for (const p of state.projects) if (p.state !== project.state && p.center) {
    const miles = milesBetween(project.center, p.center);
    if (!best || miles < best.miles) best = { p, miles };
  }
  return best;
}

function initMap() {
  if (!window.maplibregl) { $("map").append(h("p", "map-error", "The map library could not load. Check the internet connection and reload; the list and details still work.")); return; }
  try {
    map = new maplibregl.Map({ container: "map", style: BASEMAP, bounds: border, fitBoundsOptions: { padding: 20 }, attributionControl: { compact: false }, dragRotate: false, pitchWithRotate: false, touchPitch: false });
  } catch (error) { $("map").append(h("p", "map-error", `The map needs WebGL, which this browser has turned off (${error.message}). The list and details still work.`)); return; }
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
  // If the basemap style can't be fetched, fall back to a plain background so the project layers still draw.
  map.on("error", event => { if (!mapReady && !map.isStyleLoaded() && !styleFailed) { styleFailed = true; console.warn("Basemap unavailable:", event.error?.message); map.setStyle(FALLBACK_STYLE); } });
  map.on("load", () => {
    addLayers();
    mapReady = true;
    renderMap();
    if (pendingFocus) { focusPair(pendingFocus); pendingFocus = null; }
  });
  const tip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: "map-tip", offset: 12, maxWidth: "280px" });
  let hovered = null;
  const setHover = next => {
    if (hovered && (!next || hovered.id !== next.id || hovered.source !== next.source)) map.setFeatureState(hovered, { hover: false });
    if (next) map.setFeatureState(next, { hover: true });
    hovered = next;
  };
  const hitAt = point => mapReady ? map.queryRenderedFeatures(point, { layers: ["project-hit", "links-hit", "endpoints"] })[0] : null;
  map.on("mousemove", event => {
    const f = hitAt(event.point);
    setHover(f && f.layer.id !== "endpoints" ? { source: f.source, id: f.id } : null);
    map.getCanvas().style.cursor = f && f.layer.id !== "endpoints" ? "pointer" : "";
    if (f) tip.setLngLat(event.lngLat).setText(f.properties.title).addTo(map); else tip.remove();
  });
  map.getCanvas().addEventListener("mouseleave", () => { setHover(null); tip.remove(); });
  // MapLibre fires click only for a press that didn't turn into a pan, so a click on empty map deselects.
  map.on("click", event => {
    if (!mapReady) return;
    const hits = map.queryRenderedFeatures(event.point, { layers: ["project-hit", "links-hit"] });
    const project = hits.find(f => f.layer.id === "project-hit");
    const link = hits.find(f => f.layer.id === "links-hit");
    if (project) { state.selectedProject = project.properties.id; state.selectedPair = null; applyFilters(); }
    else if (link) selectPair(state.pairs.find(p => p.id === link.properties.id));
    else if (state.selectedPair || state.selectedProject) { state.selectedPair = null; state.selectedProject = null; applyFilters(); }
  });
}

function addLayers() {
  map.addSource("geometry", { type: "geojson", data: featureCollection([]) });
  map.addSource("links", { type: "geojson", data: featureCollection([]), promoteId: "id" });
  map.addSource("projects", { type: "geojson", data: featureCollection([]), promoteId: "id" });
  const bySide = ["match", ["get", "side"], "desc", COLORS.desc, COLORS.gpc];
  const kind = k => ["==", ["get", "kind"], k];
  const hover = ["boolean", ["feature-state", "hover"], false];
  const linkState = ["get", "state"];
  map.addLayer({ id: "uncertainty-fill", type: "fill", source: "geometry", filter: kind("uncertainty"), paint: { "fill-color": bySide, "fill-opacity": 0.08 } });
  map.addLayer({ id: "uncertainty-line", type: "line", source: "geometry", filter: kind("uncertainty"), paint: { "line-color": bySide, "line-width": 1.2, "line-dasharray": [4, 3] } });
  map.addLayer({ id: "routes", type: "line", source: "geometry", filter: kind("route"), layout: { "line-join": "round", "line-cap": "round" }, paint: { "line-color": bySide, "line-width": 3.5 } });
  const linkPaint = {
    "line-color": ["case", ["any", hover, ["==", linkState, "selected"]], "#10262e", "#4d6670"],
    "line-width": ["case", ["==", linkState, "selected"], 4.5, hover, 3.5, 1.6],
  };
  map.addLayer({ id: "links", type: "line", source: "links", filter: ["!", ["get", "possible"]], layout: { "line-sort-key": ["match", linkState, "selected", 2, "normal", 1, 0], "line-cap": "round" },
    paint: { ...linkPaint, "line-opacity": ["case", hover, 1, ["==", linkState, "selected"], 1, ["==", linkState, "dim"], 0.07, 0.6] } });
  map.addLayer({ id: "links-possible", type: "line", source: "links", filter: ["get", "possible"],
    paint: { ...linkPaint, "line-dasharray": [2, 2], "line-opacity": ["case", hover, 1, ["==", linkState, "selected"], 1, ["==", linkState, "dim"], 0.1, 0.4] } });
  map.addLayer({ id: "links-hit", type: "line", source: "links", paint: { "line-color": "#000", "line-width": 14, "line-opacity": 0 } });
  map.addLayer({ id: "endpoints", type: "circle", source: "geometry", filter: kind("endpoint"), paint: { "circle-radius": 3.5, "circle-color": "#fff", "circle-stroke-color": bySide, "circle-stroke-width": 1.5 } });
  const focus = ["get", "focus"];
  map.addLayer({ id: "project-halo", type: "circle", source: "projects", filter: ["any", ["==", focus, "chosen"], ["get", "matched"]],
    paint: { "circle-radius": 14, "circle-color": bySide, "circle-opacity": ["case", ["==", focus, "chosen"], 0.2, hover, 0.2, 0], "circle-stroke-color": bySide, "circle-stroke-width": ["case", ["==", focus, "chosen"], 2.5, hover, 2, 0] } });
  map.addLayer({ id: "project-core", type: "circle", source: "projects", layout: { "circle-sort-key": ["get", "order"] },
    paint: {
      "circle-radius": ["case", ["==", focus, "chosen"], 8, ["get", "matched"], 6, 3.5],
      "circle-color": bySide,
      "circle-stroke-color": ["case", ["==", focus, "chosen"], "#13232a", "#fff"],
      "circle-stroke-width": ["case", ["get", "matched"], 2.5, ["==", focus, "chosen"], 2.5, 1.5],
      "circle-opacity": ["case", ["==", focus, "faded"], 0.35, ["get", "matched"], 1, ["case", hover, 1, 0.55]],
      "circle-stroke-opacity": ["case", ["==", focus, "faded"], 0.35, 1],
    } });
  map.addLayer({ id: "project-hit", type: "circle", source: "projects", layout: { "circle-sort-key": ["get", "order"] }, paint: { "circle-radius": 12, "circle-color": "#000", "circle-opacity": 0 } });
  map.addLayer({ id: "project-labels", type: "symbol", source: "projects", filter: ["==", focus, "chosen"],
    layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Bold"], "text-size": 12, "text-anchor": "left", "text-offset": [1.35, 0], "text-allow-overlap": true, "text-ignore-placement": true },
    paint: { "text-color": ["match", ["get", "side"], "desc", "#0b5f6b", "#9a4a17"], "text-halo-color": "#fff", "text-halo-width": 2.5 } });
}

function renderMap() {
  const pair = state.allPairs.find(p => p.id === state.selectedPair);
  const project = state.projects.find(p => p.id === state.selectedProject);
  const activeIds = new Set(state.pairs.flatMap(p => [p.a.id, p.b.id]));
  const focusIds = new Set(pair ? [pair.a.id, pair.b.id] : project ? [project.id] : []);
  const visibleProjects = state.projects.filter(p => p.center && bySelectedYear(p));
  $("desc-count").textContent = String(visibleProjects.filter(p => p.state === "SC").length);
  $("gpc-count").textContent = String(visibleProjects.filter(p => p.state === "GA").length);
  if (!mapReady) return;
  map.getSource("geometry").setData(featureCollection((pair ? [pair.a, pair.b] : project ? [project] : []).flatMap(geometryFeatures)));
  map.getSource("links").setData(featureCollection(state.pairs.map(p => ({
    type: "Feature", geometry: { type: "LineString", coordinates: [lngLat(p.a.center), lngLat(p.b.center)] },
    properties: { id: p.id, possible: !p.qualifies, state: p.id === state.selectedPair ? "selected" : state.selectedPair ? "dim" : "normal", title: `${p.a.projectId} ↔ ${p.b.projectId}: ${p.miles.toFixed(1)} miles` },
  }))));
  map.getSource("projects").setData(featureCollection(visibleProjects.map(p => pointFeature(p.center, {
    id: p.id, side: side(p), matched: activeIds.has(p.id), focus: focusIds.has(p.id) ? "chosen" : pair ? "faded" : "normal", label: p.projectId,
    order: (activeIds.has(p.id) ? 1 : 0) + (focusIds.has(p.id) ? 2 : 0),
    title: `${projectLabel(p)}\n${sideName(p)} · in service ${formatDate(p.inServiceDate)}`,
  }))));
}

function focusPair(pair) {
  if (!mapReady) { pendingFocus = pair; return; }
  const lats = [pair.a.center.lat, pair.b.center.lat], lons = [pair.a.center.lon, pair.b.center.lon];
  const pad = 0.04 + Math.max(pair.a.radiusMi ?? 0, pair.b.radiusMi ?? 0) / MILES_PER_DEGREE;
  map.fitBounds([[Math.min(...lons) - pad, Math.min(...lats) - pad], [Math.max(...lons) + pad, Math.max(...lats) + pad]], { padding: 30, duration: reducedMotion() ? 0 : 700 });
}

function infoRow(label, value) { const row = h("div", "info-row"); row.append(h("span", "", label), value instanceof Node ? value : h("strong", "", value)); return row; }

function timeline(a, b) {
  const wins = [a, b].map(p => p.window).filter(w => w?.start && w?.end);
  const box = h("div", "timeline");
  if (wins.length < 2) { box.append(h("p", "muted-note", "Build window unknown for at least one project.")); return box; }
  const t = s => Date.parse(`${s}T00:00:00Z`);
  const lo = Math.min(...wins.map(w => t(w.start)), t(state.asOf)), hi = Math.max(...wins.map(w => t(w.end)), t(state.asOf));
  const pct = s => `${((t(s) - lo) / (hi - lo)) * 100}%`;
  for (const p of [a, b]) {
    const row = h("div", "tl-row");
    const bar = h("div", `tl-bar ${side(p)}`);
    bar.style.left = pct(p.window.start); bar.style.width = `calc(${pct(p.window.end)} - ${pct(p.window.start)})`;
    bar.title = `${p.window.start} → ${p.window.end} (${p.window.basis})`;
    const track = h("div", "tl-track"); track.append(bar);
    row.append(h("span", "tl-label", sideName(p)), track);
    box.append(row);
  }
  const today = h("div", "tl-today"); today.style.left = pct(state.asOf); today.title = `Today (${state.asOf})`;
  const axis = h("div", "tl-axis"); const tlabel = h("span", "tl-now", "today"); tlabel.style.left = pct(state.asOf);
  axis.append(h("span", "", String(new Date(lo).getUTCFullYear())), tlabel, h("span", "", String(new Date(hi).getUTCFullYear())));
  box.querySelectorAll(".tl-track").forEach(tr => tr.append(today.cloneNode()));
  box.append(axis);
  return box;
}

function issuesList(project) {
  if (!project.issues.length) return h("p", "muted-note", "No validation issues.");
  const ul = h("ul", "issue-list");
  for (const i of project.issues) { const li = h("li", `issue ${i.level}`); li.append(h("b", "", i.level), document.createTextNode(` ${i.msg}`)); ul.append(li); }
  return ul;
}

const located = p => p.endpoints.filter(e => e.point);
function centerMethod(p) {
  const n = located(p).length, total = p.endpoints.length;
  if (!n) return "Not located";
  if (n >= 2) return `Midpoint of ${n} located endpoints`;
  if (total > 1) return `Single known endpoint (${located(p)[0].name}); the other end is not located, so the radius is widened`;
  return `Single site (${located(p)[0].name})`;
}
function sourceLabel(src) { return src.url.includes("psc.ga.gov") ? `${src.doc}, PDF p. ${src.page} (link downloads the PSC zip)` : `${src.doc}, p. ${src.page}`; }
function sourceLink(p) { return link(pdfLink(p.source), sourceLabel(p.source)); }
function targetDate(p) { return `${formatDate(p.inServiceDate)}${p.issues.some(i => /date/.test(i.msg) && /repaired|impossible|last day/.test(i.msg)) || (p.inServiceRaw && !p.inServiceRaw.includes(p.inServiceDate?.slice(0, 4))) ? ` (printed “${p.inServiceRaw}”)` : ""}`; }
function statusText(p) {
  const passed = datePassed(p.inServiceDate);
  return passed ? `Filing says “${p.status}”, but the target date has passed. Current status not confirmed.` : `“${p.status}” in the filing. Not independently confirmed.`;
}

// Only station-level placements get imagery links; a town guess would open an unrelated street.
const sitePlaced = e => e.point && ["high", "medium"].includes(e.confidence) && e.method !== "town";
// Street View links (map_action=pano) open a black "no imagery" screen at most stations, which sit back from the road; a pin keeps Street View one drag of the pegman away.
const mapPinUrl = pt => `https://www.google.com/maps/search/?api=1&query=${pt.lat.toFixed(5)},${pt.lon.toFixed(5)}`;
const satelliteUrl = pt => `https://www.google.com/maps/@?api=1&map_action=map&center=${pt.lat.toFixed(5)},${pt.lon.toFixed(5)}&zoom=18&basemap=satellite`;

function projectBlock(p, full = false, heading = true) {
  const block = h("section", "project-block");
  if (heading) block.append(h("span", `utility-label ${side(p)}`, `${sideName(p)}, ${p.state}${p.zoneName ? `, ${p.zoneName}` : ""}`), h("h3", "", p.name));
  block.append(infoRow("Project ID", p.projectId), infoRow("Type", projectType(p)));
  block.append(infoRow("Target in service (published)", targetDate(p)));
  const st = infoRow("Current status", statusText(p)); if (datePassed(p.inServiceDate)) st.classList.add("warn-row"); block.append(st);
  if (p.window?.start) block.append(infoRow("Build window", `${formatDate(p.window.start)} → ${formatDate(p.window.end)} (${p.window.basis})`));
  block.append(infoRow("Cost", p.cost?.total ? `${money(p.cost.total)} (DESC estimate)` : p.state === "GA" ? "Redacted in the public filing" : "—"));
  if (p.miles || p.route) block.append(infoRow("Length", `${p.miles ? `${p.miles} mi stated` : "not stated"}${p.route ? ` · ${p.route.miles} mi traced on OSM` : ""}`));
  if (p.slipDays) block.append(infoRow("Schedule history", `${p.slipDays > 0 ? "slipped" : "advanced"} ${Math.abs(Math.round(p.slipDays / 30.44))} months since ${p.history[0].edition}`));
  else if (p.change && p.state === "GA") block.append(infoRow("Change vs last plan", p.change));
  block.append(infoRow("Map point", centerMethod(p)), infoRow("Location confidence", `${p.locationConfidence} · ±${p.radiusMi ?? "?"} mi`));
  const src = h("div", "source-line"); src.append(document.createTextNode("Source: "), sourceLink(p), document.createTextNode(` (${p.source.item})`));
  block.append(src);
  if (full) {
    if (p.description) block.append(h("p", "desc-text", p.description));
    const endpoints = h("div", "endpoint-box"); endpoints.append(h("h4", "", "Endpoints and how each was located"));
    for (const e of p.endpoints) {
      const row = h("p", "", `${e.name}: ${e.point ? `${e.point.lat.toFixed(4)}, ${e.point.lon.toFixed(4)} · ${e.method} · ${e.confidence} (±${e.radiusMi} mi)` : "not located"}`);
      if (sitePlaced(e)) { const views = h("span", "site-views"); views.append(link(satelliteUrl(e.point), "Satellite"), link(mapPinUrl(e.point), "Google Maps")); row.append(views); }
      if (e.evidence) row.append(h("span", "evidence", e.evidence));
      endpoints.append(row);
    }
    if (p.locationNote) endpoints.append(h("p", "evidence", p.locationNote));
    block.append(endpoints, h("h4", "", "Validation"), issuesList(p));
  }
  return block;
}

// ---- What a planner needs from a pair, shared by the panel and the brief ----
function whyQualifies(pair) {
  const out = [`Centers are ${pair.miles.toFixed(2)} mi apart, ${pair.qualifies ? `under the challenge's ${MAX_MILES}-mile rule` : `outside the ${MAX_MILES}-mile rule`}. ${certaintyText[pair.certainty]}`];
  if (pair.shared.length) out.push(`They share a station: ${pair.shared.map(s => s.a === s.b ? s.a : `${s.a} / ${s.b}`).join(", ")} (endpoints within 0.5 mi).`);
  if (pair.remainingDays > 0) out.push(`Their build windows overlap for ${Math.round(pair.remainingDays / 30.44)} months from today.`);
  else if (pair.overlapDays > 0) out.push("Their build windows overlapped, but that overlap is in the past.");
  else if (pair.gapDays !== null) out.push(`Their build windows don't overlap; in-service dates are ${pair.gapDays} days apart.`);
  return out;
}

function toConfirm(pair) {
  const out = [];
  for (const p of [pair.a, pair.b]) {
    if (datePassed(p.inServiceDate)) out.push(`${sideName(p)} ${p.projectId}: target date ${formatDate(p.inServiceDate)} has passed while the filing still says “${p.status}”. Is it built, delayed or dropped?`);
    if (p.slipDays > 0) out.push(`${sideName(p)} ${p.projectId} has slipped ${Math.round(p.slipDays / 30.44)} months since ${p.history[0].edition}. Is the current date firm?`);
    for (const e of p.endpoints) {
      if (!e.point) out.push(`${sideName(p)} ${p.projectId}: endpoint “${e.name}” is not located.`);
      else if (!["high"].includes(e.confidence)) out.push(`${sideName(p)} ${p.projectId}: “${e.name}” placed by ${e.method} (${e.confidence}, ±${e.radiusMi} mi). Confirm the site.`);
    }
  }
  if (pair.certainty === "sensitive") out.push(`The pair is inside ${MAX_MILES} mi only at best-estimate locations (±${pair.a.radiusMi} and ±${pair.b.radiusMi} mi).`);
  out.push(`Build windows are inferred (${pair.a.window?.basis ?? "unknown"}; ${pair.b.window?.basis ?? "unknown"}). Confirm construction and outage months.`);
  out.push("Proximity alone doesn't show that land, yard space or equipment can be shared. Confirm site access, ownership and each utility's contracting rules.");
  return out;
}

function sharedResources(pair) {
  const out = [];
  if (pair.shared.length) out.push(`Work at ${pair.shared[0].a}: one outage plan, one mobilization to the site, shared yard space at the station.`);
  if (pair.remainingDays > 0) out.push("One staging/laydown yard between the two sites instead of two (see scenario).", "Crane, mat and specialty-crew mobilizations scheduled back to back.");
  if (pair.approachMiles !== null && pair.approachMiles < 2) out.push(`Access roads and crossings: the two ${pair.a.route && pair.b.route ? "traced lines" : "project sites"} come within ${pair.approachMiles.toFixed(1)} mi.`);
  if (projectType(pair.a) === projectType(pair.b)) out.push(`Same kind of work (${projectType(pair.a).toLowerCase()}): joint procurement, shared spares or one specialist contractor.`);
  if (!out.length) out.push("Crew and contractor scheduling across the river; no site-level sharing is indicated.");
  return out;
}

function questions(pair) {
  const other = sideName(pair.b);
  return [
    `Is ${pair.b.projectId} still scheduled for ${formatDate(pair.b.inServiceDate)}? Which months need outages or heavy construction?`,
    "Where will your staging/laydown yard be, how large, and is there room for a second project's material?",
    pair.shared.length ? `Can the work at ${pair.shared[0].b} be done under one outage? Who controls yard space inside that station?` : "Which contractors, mat suppliers and crane vendors are you planning to use?",
    `Would ${other} share a site lease or access road if the schedules line up? What approvals would that need?`,
    "Who is the right planning contact for follow-up?",
  ];
}

// ---- Staging-yard scenario ----
function yardInputs() { const y = state.yard; return { acres: y.acres, months: y.months, leaseRate: y.leaseRate, surfacePerAcre: y.surface === "mats" ? YARD_BASIS.matsPerAcre.value : y.surfacePerAcre, roadMiles: y.roadMiles }; }
function verified(pair) { return pair.qualifies && pair.certainty === "robust" && pair.remainingDays > 0; }
function scenarioLines(pair, sc) {
  return [
    ["Yard surface", `${sc.acres} ac × ${money(sc.surfacePerAcre)}/ac = ${money(sc.parts.surface)}`, state.yard.surface === "mats" ? "cited: MISO timber-mat rate" : "your assumption"],
    ["Land lease", `${sc.acres} ac × ${money(sc.landPerAcre)}/ac × ${Math.round(sc.leaseRate * 100)}%/yr × ${sc.months} mo = ${money(sc.parts.lease)}`, "land value cited (USDA, GA/SC average); lease rate is an assumption"],
    ["Access road", `${sc.roadMiles} mi × ${money(YARD_BASIS.roadPerMile.value)}/mi = ${money(sc.parts.road)}`, "cited: MISO access-road rate; length is an assumption"],
  ];
}

function yardCard(pair) {
  const card = h("section", "cost-card");
  card.append(h("h3", "", "One shared staging yard instead of two"));
  if (!pair.qualifies) { card.append(h("p", "", "Only modeled for qualifying pairs.")); return card; }
  card.append(h("p", "", verified(pair) ? `Verified pair: robust location and ${Math.round(pair.remainingDays / 30.44)} months of build overlap still ahead.` : `Not a verified pair: ${pair.remainingDays > 0 ? "location uncertainty could move it past 25 mi" : "no build overlap from today on, so no yard would be shared"}. Treat the figure below with extra caution.`));
  const out = h("div", "scenario-out");
  const form = h("div", "scenario-form");
  const field = (label, key, attrs, hint) => {
    const l = h("label", "", label); const i = h("input"); Object.assign(i, { type: "number", ...attrs }); i.value = String(key === "months" ? (state.yard.months ?? Math.max(1, Math.round((pair.remainingDays ?? 0) / 30.44))) : key === "leaseRate" ? Math.round(state.yard.leaseRate * 100) : state.yard[key]);
    i.addEventListener("input", () => { const v = Number(i.value); if (!Number.isFinite(v) || v < 0) return; state.yard[key] = key === "leaseRate" ? v / 100 : v; update(); });
    l.append(i); if (hint) l.append(h("span", "hint", hint)); form.append(l); return i;
  };
  field("Yard size (acres)", "acres", { min: 0, step: 0.5 }, "assumption");
  field("Months shared", "months", { min: 0, step: 1 }, "default: overlap still ahead");
  const sl = h("label", "", "Yard surface"); const sel = h("select");
  [["mats", `Timber mats, floodplain (${money(YARD_BASIS.matsPerAcre.value)}/ac, MISO)`], ["custom", "Other surface: enter $/acre"]].forEach(([v, t]) => { const o = h("option", "", t); o.value = v; sel.append(o); });
  sel.value = state.yard.surface; sl.append(sel); form.append(sl);
  const custom = field("Surface cost ($/acre)", "surfacePerAcre", { min: 0, step: 1000 }, "your assumption (e.g. a gravel pad)");
  const toggle = () => { custom.closest("label").hidden = state.yard.surface === "mats"; };
  sel.addEventListener("change", () => { state.yard.surface = sel.value; toggle(); update(); }); toggle();
  field("Lease (% of land value per year)", "leaseRate", { min: 0, max: 100, step: 1 }, "assumption");
  field("Shared access road (miles)", "roadMiles", { min: 0, step: 0.05 }, "assumption");
  function update() {
    const sc = yardScenario(pair, yardInputs());
    out.replaceChildren(h("div", "cost-big", sc.active ? `${money(sc.low)} – ${money(sc.high)}` : "$0"));
    const ul = h("ul", "cost-basis"); scenarioLines(pair, sc).forEach(([k, v, why]) => { const li = h("li"); li.append(h("b", "", `${k}: `), document.createTextNode(v), h("span", "why", ` · ${why}`)); ul.append(li); });
    ul.append(h("li", "", `One yard ≈ ${money(sc.oneYard)}. A combined yard is assumed to be 1.0–1.5× one project's yard, so sharing avoids 0.5–1.0 of a yard.`));
    const est = savingsEstimate(pair, { benchmarkPerMile: state.data.costBenchmark.perMile });
    if (est && sc.active) ul.append(h("li", "", `For scale: the smaller project costs about ${money(Math.min(est.costA, est.costB))}${est.aEstimated || est.bEstimated ? " (Georgia side estimated from DESC's median $/mi)" : " (published)"}; the high end is ${((sc.high / Math.min(est.costA, est.costB)) * 100).toFixed(1)}% of it.`));
    out.append(ul);
    if (!sc.active) out.append(h("p", "", "No overlap ahead: each project would need its own yard, so nothing is saved."));
  }
  update();
  card.append(form, out);
  const src = h("p", "muted-note"); src.append(document.createTextNode("Cost basis: "), link(YARD_BASIS.matsPerAcre.url, "MISO MTEP24 cost guide, p. 19 (mats)"), document.createTextNode(" · "), link(YARD_BASIS.roadPerMile.url, "p. 23 (access road)"), document.createTextNode(" · "), link(YARD_BASIS.landPerAcre.url, `USDA Land Values 2026, p. 15 (pasture: GA ${money(YARD_BASIS.landPerAcre.GA)}, SC ${money(YARD_BASIS.landPerAcre.SC)}/ac)`));
  card.append(src, h("p", "muted-note", "Proximity alone can't establish that land or equipment can be shared. This assumes a usable site between the projects, both schedules holding, and both utilities agreeing. It is a reason to make a call, not a budget."));
  return card;
}

function compareTable(pair) {
  const t = h("table", "compare");
  const head = h("tr"); head.append(h("th", "", ""), h("th", `desc`, sideName(pair.a)), h("th", "gpc", sideName(pair.b))); t.append(head);
  const rows = [
    ["Type", p => projectType(p)],
    ["Target in service", p => targetDate(p)],
    ["Status", p => datePassed(p.inServiceDate) ? `“${p.status}” in filing; date passed, unconfirmed` : `“${p.status}” in filing`],
    ["Build window", p => p.window?.start ? `${formatDate(p.window.start)} → ${formatDate(p.window.end)}` : "unknown"],
    ["Location", p => `${p.locationConfidence}, ±${p.radiusMi} mi · ${centerMethod(p).split(" (")[0].split(";")[0]}`],
    ["Source", p => sourceLink(p)],
  ];
  for (const [k, f] of rows) { const tr = h("tr"); tr.append(h("th", "", k)); for (const p of [pair.a, pair.b]) { const v = f(p); const td = h("td"); td.append(v instanceof Node ? v : document.createTextNode(v)); if (k === "Status" && datePassed(p.inServiceDate)) td.className = "warn-cell"; tr.append(td); } t.append(tr); }
  return t;
}

function listBox(title, items, cls = "") { const box = h("section", `list-box ${cls}`); box.append(h("h3", "", title)); const ul = h("ul"); items.forEach(i => ul.append(h("li", "", i))); box.append(ul); return box; }

function toggleShortlist(pair) {
  if (state.shortlist.has(pair.id)) state.shortlist.delete(pair.id); else state.shortlist.add(pair.id);
  saveShortlist(); applyFilters();
}

const DETAIL_TABS = [["summary", "Summary"], ["scenario", "Cost scenario"], ["records", "Records"]];
let lastDetailKey = null;

function spanHead(pair) {
  const head = h("header", "span-head");
  const row = h("div", "span-row");
  const end = p => { const e = h("div", `span-end ${side(p)}`); e.append(h("span", "", sideName(p)), h("strong", "", p.projectId)); return e; };
  const line = h("div", "span-line"); line.append(h("i"), h("b", "", `${pair.miles.toFixed(2)} mi`));
  row.append(end(pair.a), line, end(pair.b));
  const names = h("div", "span-names"); names.append(h("p", "", pair.a.name), h("p", "", pair.b.name));
  head.append(row, names);
  if (!pair.qualifies) head.append(h("p", "notice", `Does not qualify: centers are over ${MAX_MILES} mi apart, but location uncertainty could bring them under.`));
  const actions = h("div", "detail-actions");
  const on = state.shortlist.has(pair.id);
  const star = h("button", `button${on ? " on" : ""}`, on ? "★ Shortlisted" : "☆ Shortlist"); star.type = "button"; star.setAttribute("aria-pressed", String(on)); star.addEventListener("click", () => toggleShortlist(pair));
  const brief = h("button", "button", "Open brief"); brief.type = "button"; brief.addEventListener("click", () => openBriefs([pair]));
  const copy = h("button", "button", "Copy as text"); copy.type = "button"; copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(briefText(pair)); copy.textContent = "Copied"; } catch { copy.textContent = "Copy failed"; } });
  actions.append(star, brief, copy); head.append(actions);
  return head;
}

function factsRow(pair) {
  const figs = h("dl", "facts");
  [[pair.gapDays === null ? "Unknown" : `${pair.gapDays} days`, "In-service gap"], [pair.remainingDays > 0 ? `${Math.round(pair.remainingDays / 30.44)} mo` : "None", "Overlap ahead"], [pair.certainty[0].toUpperCase() + pair.certainty.slice(1), "Location"], [pair.qualifies ? `${pair.score.total}` : "–", "Score"]]
    .forEach(([v, l]) => { const f = h("div"); f.append(h("dt", "", l), h("dd", "", v)); figs.append(f); });
  return figs;
}

function detailTabs(pair) {
  const nav = h("div", "dtabs"); nav.setAttribute("role", "tablist"); nav.setAttribute("aria-label", "Pair details");
  for (const [key, label] of DETAIL_TABS) {
    const t = h("button", `dtab${state.detailTab === key ? " active" : ""}`, label); t.type = "button";
    t.setAttribute("role", "tab"); t.setAttribute("aria-selected", String(state.detailTab === key));
    t.addEventListener("click", () => { state.detailTab = key; renderDetail(); $("detail").querySelector(".dtab.active")?.focus(); });
    nav.append(t);
  }
  const panel = h("div", "dpanel"); panel.setAttribute("role", "tabpanel");
  if (state.detailTab === "scenario") panel.append(yardCard(pair));
  else if (state.detailTab === "records") { panel.append(h("p", "section-note", "Every field as parsed, how each endpoint was located, and what validation flagged.")); for (const p of [pair.a, pair.b]) panel.append(projectBlock(p, true)); }
  else {
    panel.append(compareTable(pair));
    panel.append(listBox(pair.qualifies ? "Why it qualifies" : "Why it might qualify", whyQualifies(pair)), listBox("Still to confirm", toConfirm(pair), "confirm"), listBox("Possible shared resources", sharedResources(pair)));
    const tl = h("section", "list-box"); tl.append(h("h3", "", "Build windows"), timeline(pair.a, pair.b)); panel.append(tl);
    panel.append(scoreBlock(pair));
  }
  return [nav, panel];
}

function scoreBlock(pair) {
  const score = h("section", "list-box score-block");
  const head = h("h3", "", "Ranking score "); head.append(h("span", "", `${pair.score.total} of 100, geography first`)); score.append(head);
  const parts = h("div", "score-parts");
  [["Proximity", pair.score.parts.proximity, WEIGHTS.proximity], ["Shared station", pair.score.parts.shared, WEIGHTS.shared], ["Line proximity", pair.score.parts.corridor, WEIGHTS.corridor], ["Timing", pair.score.parts.timing, WEIGHTS.timing]].forEach(([k, v, max]) => {
    const row = h("div", "score-part"); const bar = h("i", ""); bar.style.width = `${(v / max) * 100}%`; const track = h("span", "score-track"); track.append(bar);
    row.append(h("span", "", k), track, h("b", "", `${v}/${max}`)); parts.append(row);
  });
  if (pair.score.parts.confidence < 1) parts.append(h("p", "muted-note", "× 0.85 because location uncertainty could move this pair past 25 miles."));
  score.append(parts);
  return score;
}

function landing() {
  const box = h("div", "landing");
  box.append(h("h2", "", "Savannah River transmission coordination"),
    h("p", "lead", `Every planned project in DESC’s 2026–2030 list and Georgia ITS’s 2026–2035 plan, paired across the river when their centers are under ${MAX_MILES} miles apart.`),
    overviewStats());
  const steps = h("ol", "guide-steps");
  [["Find", "Pairs are ranked geography first, timing second. Change the sort or open Filters to explore."], ["Check", "Every number links to the filing page it came from, and every location states its method and confidence."], ["Call", "Shortlist a pair and export a one-page brief with evidence, shared resources, a cost scenario and questions."]]
    .forEach(([title, body]) => { const li = h("li"); li.append(h("strong", "", title), h("p", "", body)); steps.append(li); });
  box.append(steps, h("p", "fine", "Pick a pair in the list or a link on the map to compare both projects. Pick a dot to see one project. Click empty map to deselect. Built from public filings and OpenStreetMap only, with no CEII. A planning aid, not a field plan."));
  return box;
}

function renderDetail() {
  const detail = $("detail"); detail.replaceChildren();
  const pair = state.allPairs.find(p => p.id === state.selectedPair);
  const project = state.projects.find(p => p.id === state.selectedProject);
  const key = pair ? `pair:${pair.id}` : project ? `project:${project.id}` : "none";
  const changed = key !== lastDetailKey; lastDetailKey = key;
  detail.classList.toggle("draw", changed && Boolean(pair));
  if (changed) detail.scrollTop = 0;
  if (pair) {
    detail.append(spanHead(pair), factsRow(pair), ...detailTabs(pair));
  } else if (project) {
    const head = h("header", "project-head");
    head.append(h("span", `utility-label ${side(project)}`, `${sideName(project)}, ${project.state}${project.zoneName ? `, ${project.zoneName}` : ""}`), h("h2", "", project.name));
    const count = state.pairs.length;
    let lead;
    if (!project.center) lead = "This project could not be located, so it can't be matched. The Data quality tab lists why.";
    else if (!state.allPairs.some(p => p.qualifies && (p.a.id === project.id || p.b.id === project.id))) {
      const n = nearestPartner(project);
      lead = `No project across the river within ${MAX_MILES} miles.${n ? ` The nearest is ${n.p.projectId}, ${n.p.name}, ${n.miles.toFixed(1)} mi away.` : ""}`;
    } else lead = `${count} ${count === 1 ? "pair" : "pairs"} with the other state under the current filters. Pick one in the list to compare.`;
    head.append(h("p", "lead", lead));
    detail.append(head, projectBlock(project, true, false));
  } else detail.append(landing());
}

// ---- Brief: plain text for pasting, and a printable one-page document ----
function briefText(pair) {
  const sc = yardScenario(pair, yardInputs());
  const bullets = xs => xs.map(x => `  - ${x}`);
  return [
    `GridLock coordination brief · ${state.asOf}`, "",
    `${pair.a.name} (${sideName(pair.a)} ${pair.a.projectId}, ${projectType(pair.a)})`, `  × ${pair.b.name} (${sideName(pair.b)} ${pair.b.projectId}, ${projectType(pair.b)})`, "",
    `Distance: ${pair.miles.toFixed(2)} mi center to center (${pair.certainty}) · In-service gap: ${pair.gapDays ?? "unknown"} days (${pair.a.inServiceDate} vs ${pair.b.inServiceDate})`,
    "", "Why it qualifies:", ...bullets(whyQualifies(pair)),
    "", "Possible shared resources:", ...bullets(sharedResources(pair)),
    "", `Staging-yard scenario: ${sc.active ? `${money(sc.low)} – ${money(sc.high)}` : "$0 (no overlap ahead)"}`, ...bullets(scenarioLines(pair, sc).map(([k, v, why]) => `${k}: ${v} (${why})`)),
    "", "Still to confirm:", ...bullets(toConfirm(pair)),
    "", `Questions for ${sideName(pair.b)}:`, ...questions(pair).map((q, i) => `  ${i + 1}. ${q}`),
    "", "Sources:", `  ${sourceLabel(pair.a.source)}: ${pdfLink(pair.a.source)}`, `  ${sourceLabel(pair.b.source)}: ${pdfLink(pair.b.source)}`,
    `  ${YARD_BASIS.matsPerAcre.source}`, `  ${YARD_BASIS.roadPerMile.source}`, `  ${YARD_BASIS.landPerAcre.source}`,
    "Built from public filings and OpenStreetMap only; no CEII. Locations are estimates.",
  ].join("\n");
}

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
function briefHtml(pair) {
  const sc = yardScenario(pair, yardInputs());
  const li = xs => xs.map(x => `<li>${esc(x)}</li>`).join("");
  const a = pair.a, b = pair.b;
  const row = (k, f) => `<tr><th>${k}</th><td>${f(a)}</td><td>${f(b)}</td></tr>`;
  const src = p => `<a href="${esc(pdfLink(p.source))}">${esc(sourceLabel(p.source))}</a>`;
  const eps = p => p.endpoints.map(e => `${esc(e.name)}: ${e.point ? `${e.point.lat.toFixed(4)}, ${e.point.lon.toFixed(4)} · ${esc(e.method)}, ${esc(e.confidence)} ±${e.radiusMi} mi` : "not located"}${sitePlaced(e) ? ` · <a href="${esc(satelliteUrl(e.point))}">Satellite</a> · <a href="${esc(mapPinUrl(e.point))}">Google Maps</a>` : ""}`).join("<br>");
  return `<section class="page">
  <header><div><p class="eyebrow">GridLock coordination brief · ${esc(state.asOf)}</p><h1>${esc(a.name)} <span>×</span> ${esc(b.name)}</h1></div>
  <div class="figs"><div><b>${pair.miles.toFixed(2)} mi</b>center to center</div><div><b>${pair.gapDays ?? "?"} days</b>in-service gap</div><div><b>${esc(pair.certainty)}</b>location</div></div></header>
  <table class="cmp"><tr><th></th><th>${esc(sideName(a))}</th><th>${esc(sideName(b))}</th></tr>
  ${row("Project", p => `${esc(p.projectId)} · ${esc(p.name)}`)}${row("Type", p => esc(projectType(p)))}${row("Target in service", p => esc(targetDate(p)))}
  ${row("Status", p => esc(datePassed(p.inServiceDate) ? `“${p.status}” in filing; date passed, unconfirmed` : `“${p.status}” in filing`))}
  ${row("Build window", p => p.window?.start ? `${esc(p.window.start)} → ${esc(p.window.end)}` : "unknown")}${row("Cost", p => p.cost?.total ? money(p.cost.total) : "redacted")}
  ${row("Location", p => `${esc(p.locationConfidence)}, ±${p.radiusMi} mi · ${esc(centerMethod(p))}`)}${row("Endpoints", eps)}${row("Source", src)}</table>
  <div class="cols"><div><h2>Why it qualifies</h2><ul>${li(whyQualifies(pair))}</ul><h2>Possible shared resources</h2><ul>${li(sharedResources(pair))}</ul>
  <h2>Staging-yard scenario: ${sc.active ? `${money(sc.low)} – ${money(sc.high)}` : "$0"}</h2><ul>${scenarioLines(pair, sc).map(([k, v, why]) => `<li><b>${k}:</b> ${esc(v)} <i>(${esc(why)})</i></li>`).join("")}<li>Combined yard assumed 1.0–1.5× one yard, so sharing avoids 0.5–1.0 of a yard (${money(sc.oneYard)}). Proximity alone does not prove the land or equipment can be shared.</li></ul></div>
  <div><h2>Still to confirm</h2><ul>${li(toConfirm(pair))}</ul><h2>Questions for ${esc(sideName(b))}</h2><ol>${li(questions(pair))}</ol></div></div>
  <footer>Cost basis: ${esc(YARD_BASIS.matsPerAcre.source)}; ${esc(YARD_BASIS.roadPerMile.source)}; ${esc(YARD_BASIS.landPerAcre.source)}. Built from public filings and OpenStreetMap only; no CEII. Locations are estimates with stated uncertainty.</footer>
</section>`;
}

function openBriefs(pairs) {
  const w = window.open("", "_blank");
  if (!w) return;
  w.document.write(`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>GridLock brief${pairs.length > 1 ? "s" : ""}</title><style>
  body { margin: 0; background: #e9eef0; color: #22343c; font: 11px/1.45 "IBM Plex Sans", Arial, sans-serif; }
  .bar { position: sticky; top: 0; display: flex; gap: 10px; align-items: center; padding: 10px 16px; background: #183541; color: #fff; }
  .bar button { font: inherit; font-weight: 700; padding: 6px 12px; border: 0; border-radius: 4px; background: #fff; color: #183541; cursor: pointer; }
  .page { box-sizing: border-box; width: min(8.5in, 100%); min-height: 11in; margin: 16px auto; padding: .45in .5in; background: #fff; }
  header { display: flex; justify-content: space-between; gap: 16px; border-bottom: 2px solid #205a66; padding-bottom: 8px; }
  .eyebrow { margin: 0; color: #5b7580; font-size: 9px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }
  h1 { margin: 4px 0 0; font-size: 16px; line-height: 1.25; } h1 span { color: #8aa; }
  .figs { display: flex; flex: none; gap: 14px; text-align: right; } .figs div { font-size: 9px; color: #647780; white-space: nowrap; } .figs b { display: block; font-size: 14px; color: #183541; }
  h2 { font-size: 11px; margin: 10px 0 3px; color: #205a66; text-transform: uppercase; letter-spacing: .04em; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; } th, td { text-align: left; vertical-align: top; padding: 3px 6px; border-bottom: 1px solid #e3e9eb; }
  .cmp th:first-child { width: 17%; color: #647780; font-weight: 600; } .cmp tr:first-child th { color: #183541; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; } ul, ol { margin: 0; padding-left: 16px; } li { margin: 2px 0; } i { color: #6b7d84; }
  footer { margin-top: 12px; padding-top: 6px; border-top: 1px solid #d9e0e3; color: #6b7d84; font-size: 8.5px; }
  a { color: #1b6472; }
  @media (max-width: 700px) { .cols { grid-template-columns: 1fr; } header { flex-direction: column; } .page { padding: 16px; min-height: 0; } }
  @media print { body { background: #fff; } .bar { display: none; } .page { margin: 0; width: auto; min-height: 0; padding: 0; page-break-after: always; } @page { size: letter; margin: .45in; } }
  </style></head><body><div class="bar"><strong>${pairs.length} coordination brief${pairs.length > 1 ? "s" : ""}</strong><button onclick="print()">Print / save as PDF</button></div>${pairs.map(briefHtml).join("")}</body></html>`);
  w.document.close();
}

function renderQuality() {
  const v = $("view-quality"); v.replaceChildren();
  const d = state.data;
  const wrap = h("div", "doc");
  wrap.append(h("h1", "", "What the filings get wrong, and what we couldn't place"),
    h("p", "doc-lead", "Every record is validated as it is parsed. Nothing here was silently fixed: impossible dates are read as the month's last day and flagged, cost rows that don't add up keep the printed total and are flagged."));
  const all = state.projects.flatMap(p => p.issues.map(i => ({ ...i, p })));
  const counts = { error: 0, warn: 0, info: 0 }; all.forEach(i => counts[i.level]++);
  const sum = h("div", "stat-row"); [[counts.error, "errors"], [counts.warn, "warnings"], [counts.info, "notes"], [state.projects.filter(p => !p.center).length, "projects not located"]].forEach(([n, l]) => { const c = h("div", "stat"); c.append(h("strong", "", String(n)), h("span", "", l)); sum.append(c); });
  wrap.append(sum);

  wrap.append(h("h2", "", "The sponsor's sample, checked against today's filings"));
  const t0 = h("table", "dq-table"); t0.innerHTML = "<thead><tr><th>Sample ID</th><th>Project</th><th>Sample date</th><th>Status in the current filing</th></tr></thead>";
  const b0 = h("tbody"); d.starterStatus.forEach(s => { const tr = h("tr"); [s.id, s.name, s.starterDate, s.status].forEach((x, i) => tr.append(h("td", i === 2 ? "nowrap" : "", x ?? ""))); b0.append(tr); }); t0.append(b0); wrap.append(t0);
  wrap.append(h("p", "muted-note", "Both Augusta-area sample pairs are gone: DESC's Hooks–Thurmond rebuild is no longer listed and Georgia Power cancelled Evans Primary–Thurmond Dam #5 and #6 (Table 3). The McIntosh–Purrysburg reactors are complete (Table 4)."));

  wrap.append(h("h2", "", "List-level problems"));
  const ul = h("ul", "issue-list"); d.dataQuality.forEach(msg => ul.append(h("li", "issue warn", msg))); wrap.append(ul);

  const table = (rows, caption) => {
    const t = h("table", "dq-table"); t.innerHTML = "<thead><tr><th>Level</th><th>Project</th><th>Issue</th><th>Source</th></tr></thead>";
    const body = h("tbody");
    rows.forEach(i => {
      const tr = h("tr", `lvl-${i.level}`);
      const nameCell = h("td"); const btn = h("button", "link-button", `${i.p.projectId} · ${i.p.name}`); btn.type = "button";
      btn.addEventListener("click", () => { state.selectedProject = i.p.id; state.selectedPair = null; setView("explore"); applyFilters(); });
      nameCell.append(btn);
      const srcCell = h("td"); srcCell.append(link(pdfLink(i.p.source), `p. ${i.p.source.page}`));
      tr.append(h("td", "", i.level), nameCell, h("td", "", i.msg), srcCell); body.append(tr);
    });
    t.append(body);
    return t;
  };
  const order = { error: 0, warn: 1 };
  wrap.append(h("h2", "", "Errors and warnings in the filings and in our matching"));
  wrap.append(table(all.filter(i => i.level !== "info").sort((a, b) => order[a.level] - order[b.level])));
  const notes = all.filter(i => i.level === "info" && !/has passed/.test(i.msg));
  const more = h("details", "dq-more"); more.append(h("summary", "", `${notes.length} notes: phased dates, customer-funded costs, low-confidence locations, route vs stated length`), table(notes));
  wrap.append(more);
  const passed = all.filter(i => /has passed/.test(i.msg)).length;
  wrap.append(h("p", "muted-note", `${passed} further notes flag in-service dates that have already passed while the filing still lists the project as planned or in progress.`));
  v.append(wrap);
}

function renderMethod() {
  const v = $("view-method"); v.replaceChildren();
  const d = state.data, bm = d.costBenchmark;
  const wrap = h("div", "doc");
  wrap.append(h("h1", "", "How GridLock decides"));
  const sec = (title, ...paras) => { wrap.append(h("h2", "", title)); paras.forEach(p => wrap.append(typeof p === "string" ? h("p", "", p) : p)); };
  const srcList = h("ul", "src-list"); d.sources.forEach(s => { const li = h("li"); li.append(link(s.url, s.title), document.createTextNode(s.projects ? ` — ${s.projects} projects` : "")); srcList.append(li); });
  sec("Sources", srcList, "The challenge zip's DESC list (2024–2028) and Georgia plan (2025 IRP) are superseded; both newer editions are public and are used here. The older DESC lists are kept only to measure schedule slip.");
  sec("The qualifying rule (unchanged from the challenge)", `A DESC project and a Georgia project form a pair when their centers are less than ${MAX_MILES} miles apart by great-circle (haversine) distance. A project's center is the midpoint of its located endpoints, or the single located endpoint. The time gap is the absolute difference between in-service dates. Our tests reproduce the sponsor's six example rows to the hundredth of a mile and the day.`);
  sec("What we add on top (ranking only — never changes which pairs qualify)",
    "Build windows: DESC's first budget year with spend through its in-service date; Georgia's detail-page Start Date through Need Date. Overlap still ahead of today counts; overlap already in the past does not.",
    "Certainty: each location carries an uncertainty radius. A pair is robust if it stays under 25 miles at the edges of both radii, sensitive if it only does at the best estimate, and possible (shown only on request) if it could qualify.",
    "Shared station: endpoints within half a mile. Line proximity: closest approach of lines traced along OpenStreetMap power lines, where both ends could be placed.",
    `Score = proximity (${WEIGHTS.proximity}) + shared station (${WEIGHTS.shared}) + line proximity (${WEIGHTS.corridor}) + timing (${WEIGHTS.timing}), × 0.85 when location-sensitive. The challenge makes geography the primary signal and timing a strong secondary one, so the geographic parts add up to ${WEIGHTS.proximity + WEIGHTS.shared + WEIGHTS.corridor} of 100. In practice distance sets most of the order and timing reorders pairs at similar distances. To explore alternatives, sort by distance, by closest in-service dates, or by build overlap still ahead.`);
  sec("How locations are found", "In order of trust: hand-sited points with a written reason (data/overrides.json); coordinates from the sponsor's starter workbook; OpenStreetMap substations and plants by exact then partial name, restricted to the right state; and last, a town-level match (±6 mi). When a name fits several places (there are two Goshens 87 miles apart), the one nearest the project's other endpoint and its planning zone wins. Matches far from the rest of the project are rejected rather than kept.");
  const basis = h("ul", "src-list"); [YARD_BASIS.matsPerAcre, YARD_BASIS.roadPerMile, YARD_BASIS.landPerAcre].forEach(b => { const li = h("li"); li.append(link(b.url, b.source)); basis.append(li); });
  sec("Impact scenario (bonus): one shared staging yard",
    "For a pair building at the same time, we model what one shared staging/laydown yard would avoid compared with two separate yards. One yard = surface (acres × $/acre) + land lease (acres × land value × lease rate × months) + a short access road (miles × $/mile). A combined yard is assumed to be 1.0–1.5× the size of one project's yard, so sharing avoids 0.5–1.0 of a yard. With no build overlap from today on, the saving is $0.",
    "Cited unit costs:", basis,
    "Yard size, months, lease rate, road length and any non-mat surface cost are assumptions, marked as such and editable in the panel. Proximity alone can't establish that land or equipment can be shared; the scenario is a reason to make the call, not a budget.",
    `For scale, the panel compares the result with the smaller project's cost. DESC publishes costs; Georgia's are redacted, so for a Georgia line with a stated length we apply DESC's own median of ${money(bm.perMile)} per mile (${bm.n} line projects).`);
  sec("What this does not use", "No CEII, no non-public data and no paid APIs. Georgia filings carry a CEII banner even in their public-disclosure versions; we use only what the Commission published, and we do not reconstruct redacted costs.");
  sec("Reproduce", Object.assign(h("pre", "code"), { textContent: "python3 pipeline/build.py        # fetch filings, parse, geocode, trace lines -> data/projects.json\npython3 pipeline/build.py --offline\nnode --test tests/*.test.js\npython3 -m http.server 8000" }));
  v.append(wrap);
}

function setView(name) {
  document.querySelectorAll(".tab").forEach(t => { t.classList.toggle("active", t.dataset.view === name); if (t.dataset.view === name) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current"); });
  document.querySelectorAll(".view").forEach(v => { v.hidden = v.id !== `view-${name}`; });
  window.scrollTo(0, 0);
  document.querySelectorAll(".doc-view").forEach(v => { v.scrollTop = 0; });
  if (name === "explore") map?.resize();
}

function exportCsv() {
  const header = ["rank", "qualifies", "score", "certainty", "desc_project", "desc_id", "desc_type", "desc_status", "ga_project", "ga_teams", "ga_type", "ga_status", "distance_miles", "in_service_gap_days", "build_overlap_days", "overlap_days_ahead", "shared_station", "closest_approach_miles", "in_service_desc", "in_service_ga", "source_desc", "source_ga"];
  const lines = [header, ...state.pairs.map((p, i) => [i + 1, p.qualifies, p.score.total, p.certainty, p.a.name, p.a.projectId, projectType(p.a), p.a.status, p.b.name, p.b.projectId, projectType(p.b), p.b.status, p.miles.toFixed(3), p.gapDays ?? "", p.overlapDays ?? "", p.remainingDays ?? "", p.shared.map(s => s.a).join("; "), p.approachMiles?.toFixed(2) ?? "", p.a.inServiceDate ?? "", p.b.inServiceDate ?? "", pdfLink(p.a.source), pdfLink(p.b.source)])];
  const csv = lines.map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\r\n");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" })); a.download = "gridlock-opportunities.csv"; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

$("distance").addEventListener("input", event => { state.distance = Number(event.target.value); applyFilters(); });
$("year").addEventListener("input", event => { state.year = Number(event.target.value); applyFilters(); });
$("gap").addEventListener("change", event => { state.gap = event.target.value; applyFilters(); });
$("hide-past").addEventListener("change", event => { state.hidePast = event.target.checked; applyFilters(); });
$("possible").addEventListener("change", event => { state.includePossible = event.target.checked; applyFilters(); });
$("search").addEventListener("input", event => { state.search = event.target.value.trim().toLowerCase(); applyFilters(); });
$("reset-filters").addEventListener("click", () => { Object.assign(state, FILTER_DEFAULTS); syncControls(); });
$("clear-selection").addEventListener("click", () => { state.selectedProject = null; state.selectedPair = null; applyFilters(); });
$("sort").addEventListener("change", event => { state.sort = event.target.value; applyFilters(); });
$("shortlist-only").addEventListener("change", event => { state.shortlistOnly = event.target.checked; applyFilters(); });
$("shortlist-export").addEventListener("click", () => { const pairs = state.allPairs.filter(p => state.shortlist.has(p.id)); if (pairs.length) openBriefs(pairs); });
$("export").addEventListener("click", exportCsv);
$("filter-toggle").addEventListener("click", () => { const open = $("filters").hidden; $("filters").hidden = !open; $("filter-toggle").setAttribute("aria-expanded", String(open)); });
$("zoom-in").addEventListener("click", () => map?.zoomIn());
$("zoom-out").addEventListener("click", () => map?.zoomOut());
$("fit").addEventListener("click", () => map?.fitBounds(border, { padding: 20 }));
$("fit-all").addEventListener("click", () => map?.fitBounds(bounds, { padding: 20 }));
document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => setView(t.dataset.view)));

try {
  const response = await fetch("data/projects.json");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  state.data = await response.json();
  state.projects = state.data.projects;
  state.asOf = new Date().toISOString().slice(0, 10);
  state.allPairs = matchProjects(state.projects, MAX_MILES, { includePossible: true, asOf: state.asOf });
  const years = state.projects.map(project => Number(project.inServiceDate?.slice(0, 4))).filter(Number.isFinite);
  $("year").min = String(Math.min(...years)); $("year").max = String(Math.max(...years)); state.year = FILTER_DEFAULTS.year = Math.max(...years); $("year").value = String(state.year);
  state.shortlist = new Set([...loadShortlist()].filter(id => state.allPairs.some(p => p.id === id)));
  for (const [key, s] of Object.entries(SORTS)) { const o = h("option", "", s.label); o.value = key; $("sort").append(o); }
  $("sort-hint").textContent = `Score: geography ${WEIGHTS.proximity + WEIGHTS.shared + WEIGHTS.corridor} pts (distance ${WEIGHTS.proximity}, shared station ${WEIGHTS.shared}, line proximity ${WEIGHTS.corridor}) + timing ${WEIGHTS.timing}. Distance sets most of the order.`;
  const issueTotal = state.projects.reduce((n, p) => n + p.issues.filter(i => i.level !== "info").length, 0);
  $("issue-count").textContent = String(issueTotal);
  initMap();
  renderQuality(); renderMethod();
  applyFilters();
} catch (error) { $("match-list").textContent = `Could not load project data: ${error.message}. Run the local server described in README.md.`; console.error(error); }
