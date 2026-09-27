import { matchProjects, gapLabel, overlapLabel, opportunityText, savingsEstimate, yardScenario, projectType, sortPairs, milesBetween, SORTS, WEIGHTS, YARD_BASIS, MAX_MILES } from "./match.js";
import { pairGround, groundMatches, groundLines, groundShort, groundCostNote, floodZoneText } from "./environment.js";
import { createChangesView } from "./changes-view.js";
import { matchesProject, withinDistance, encodeView, decodeView } from "./view-state.js";
import { briefMapSvg } from "./brief-map.js";
import { PLANNING_FORUMS, PLANNING_TRANSITION } from "./planning-forums.js";

const $ = id => document.getElementById(id);
const state = {
  data: null, projects: [], allPairs: [], pairs: [], selectedProject: null, selectedPair: null, search: "", distance: 25, year: 2035,
  gap: "all", ground: "all", scUtility: "all", hidePast: true, includePossible: false, shortlistOnly: false, sort: "score", asOf: null, detailTab: "summary",
  shortlist: new Set(), yard: { acres: 5, months: null, leaseRate: 0.10, surface: "mats", surfacePerAcre: YARD_BASIS.matsPerAcre.value, roadMiles: 0.25 },
};
const FILTER_DEFAULTS = { search: "", distance: 25, year: 2035, gap: "all", ground: "all", scUtility: "all", hidePast: true, includePossible: false, shortlistOnly: false };
const SHORTLIST_KEY = "gridlock.shortlist";
const LAYERS_KEY = "gridlock.layers";
function loadShortlist() { try { return new Set(JSON.parse(localStorage.getItem(SHORTLIST_KEY) ?? "[]")); } catch { return new Set(); } }
function saveShortlist() { try { localStorage.setItem(SHORTLIST_KEY, JSON.stringify([...state.shortlist])); } catch { /* storage unavailable: shortlist lasts for this visit */ } }
function loadLayers() { try { return new Set(JSON.parse(localStorage.getItem(LAYERS_KEY) ?? "[]")); } catch { return new Set(); } }
function saveLayers() { try { localStorage.setItem(LAYERS_KEY, JSON.stringify([...envOn])); } catch { /* storage unavailable: layer choice lasts for this visit */ } }
const SATELLITE_KEY = "gridlock.satellite";
function loadSatellite() { try { return localStorage.getItem(SATELLITE_KEY) === "1"; } catch { return false; } }
function saveSatellite() { try { localStorage.setItem(SATELLITE_KEY, satelliteOn ? "1" : "0"); } catch { /* storage unavailable: choice lasts for this visit */ } }
const bounds = [[-85.8, 30.3], [-78.4, 35.3]];
const border = [[-82.7, 31.85], [-80.6, 33.95]];
const COLORS = { desc: "#0a8494", gpc: "#cb6e30" };
// OpenFreeMap: free OpenStreetMap vector tiles, no API key.
const BASEMAP = "https://tiles.openfreemap.org/styles/positron";
// USGS National Map orthoimagery (USDA NAIP): public domain, no API key. Cached to zoom 16; closer in, MapLibre enlarges those tiles.
const SATELLITE_TILES = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}";
const FALLBACK_STYLE = { version: 8, glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf", sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#e5ecec" } }] };
const MILES_PER_DEGREE = 69.09;
let map = null, mapReady = false, styleFailed = false, pendingFocus = null, changesView = null;

// Ground layers. Around checked sites we draw the cached outlines behind each result (data/env/); zoomed in close,
// the full federal maps take over as live images, drawn in the agencies' own colors.
const NWI_EXPORT = "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/export";
const NFHL_EXPORT = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/export";
const exportTiles = (url, layer) => `${url}?bbox={bbox-epsg-3857}&bboxSR=3857&imageSR=3857&size=512,512&format=png32&transparent=true&layers=show:${layer}&f=image`;
const ENV_COLORS = { wetland: "#1f8a3c", water: "#4f78c4", flood: "#3fb6d0", flood02: "#9fdbe6", habitat: "#b0397a", protected: "#8a7a2c" };
const LIVE_ZOOM = { wetlands: 12, flood: 14 };
const ENV_LAYERS = {
  wetlands: ["env-wetland-fill", "env-water-fill", "env-nwi"],
  flood: ["env-flood-fill", "env-flood-line", "env-nfhl"],
  habitat: ["env-habitat-fill", "env-habitat-line"],
  protected: ["env-protected-fill", "env-protected-line"],
};
const ENV_DATA = { evidence: "data/env/evidence.geojson", habitat: "data/env/habitat.geojson", protected: "data/env/protected.geojson" };
const envOn = loadLayers();
const envLoaded = new Set();
let satelliteOn = loadSatellite();
let endpointTip = null;

let returnTarget = null, resultScroll = 0;
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
  const line = (coords, kind) => out.push({ type: "Feature", geometry: { type: "LineString", coordinates: coords.map(([lat, lon]) => [lon, lat]) }, properties: { kind, side: s } });
  const stations = project.endpoints.filter(e => e.point).map(e => [e.point.lat, e.point.lon]);
  // An OSM path we traced ourselves is dashed; with no path, a dotted straight line joins the stations.
  // A tap placed on its host line draws nothing: those stations are the host line's, not the project's.
  if (!project.hostLine && project.route?.coords?.length > 1) line(project.route.coords, "route-inferred");
  else if (!project.hostLine && stations.length > 1) line(stations, "stations");
  for (const e of project.endpoints) if (e.point) out.push(pointFeature(e.point, { kind: "endpoint", side: s, town: e.method === "town", title: `${e.name} · ${e.method} · ${e.confidence}` }));
  // Stations the description names mark the neighborhood, not the project's own ends, so they draw faint.
  if (project.locatedBy === "description") for (const d of project.describedStations) out.push(pointFeature(d.point, { kind: "endpoint", side: s, town: true, title: `${d.name} · ${DESCRIBED}` }));
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
// The plans a project would pair with; names them outright since many projects sit far from the river.
const otherPlan = project => project.state === "SC" ? "Georgia ITS" : "South Carolina";
const UTILITY_NAMES = { SCPSA: "Santee Cooper", SAV: "GPC · Savannah" };
const sideName = project => UTILITY_NAMES[project.utility] ?? project.utility;
// South Carolina's two listing utilities, for the filter.
const SC_UTILITIES = { DESC: "DESC", SCPSA: "Santee Cooper" };
const byScUtility = (project, choice = state.scUtility) => choice === "all" || project.state !== "SC" || project.utility === choice;
function projectLabel(project) { return `${project.projectId} · ${project.name}`; }
// Other projects in the same state with the same named end stations (separate filings on one line), which otherwise
// read as duplicate pairs in the list.
function sameStationProjects(projects) {
  // Both stations must be named in the project's own title, so a tap placed on its host line doesn't count.
  const key = p => {
    const names = [...new Set((p.endpoints ?? []).map(e => e.name?.toUpperCase()).filter(Boolean))];
    return names.length >= 2 && names.every(n => p.name.toUpperCase().includes(n)) ? `${p.state}|${names.sort().join("|")}` : null;
  };
  const groups = new Map();
  for (const p of projects) { const k = key(p); if (k) groups.set(k, [...(groups.get(k) ?? []), p]); }
  const out = new Map();
  for (const group of groups.values()) if (group.length > 1) for (const p of group) out.set(p.id, group.filter(q => q !== p));
  return out;
}
const windowText = p => p.window?.start ? `${formatDate(p.window.start)} → ${formatDate(p.window.end)}` : `in service ${formatDate(p.inServiceDate)}`;
function formatDate(value) { return value ? new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`)) : "Unknown"; }
const money = n => n == null ? "—" : n >= 1e6 ? `$${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M` : `$${Math.round(n / 1000)}k`;
function datePassed(value) { return Boolean(value && value < state.asOf); }
function pdfLink(source) { return `${source.url}#page=${source.page}`; }
function includesSearch(pair, q = state.search) {
  return [pair.a, pair.b].some(p => matchesProject(p, q));
}
function bySelectedYear(project, year = state.year) { return !project.inServiceDate || Number(project.inServiceDate.slice(0, 4)) <= year; }
const certaintyText = {
  robust: "Inside 25 miles even at the edges of both location estimates.",
  sensitive: "Inside 25 miles at the best-estimate locations, but location uncertainty could push it past 25.",
  possible: "Outside 25 miles at the best-estimate locations, but within reach of the location uncertainty. Not a qualifying pair.",
};

const GAP_LABELS = { ahead: "Planning overlap after data date", overlap: "Planning overlap (any time)", 365: "In-service within 1 year", 730: "In-service within 2 years" };
const GROUND_LABELS = { flood: "Flood area mapped at a site", habitat: "Critical habitat mapped at a site", protected: "Protected land mapped at a site", clear: "Ground checked, none of those mapped", unchecked: "Ground not checked" };

// The filter predicate, parameterized so the empty state can ask "what if this one filter were off?".
function filterPairs(f) {
  return state.allPairs.filter(pair =>
    (f.includePossible || pair.qualifies) && bySelectedYear(pair.a, f.year) && bySelectedYear(pair.b, f.year) && withinDistance(pair, f.distance) &&
    byScUtility(pair.a, f.scUtility) &&
    (!f.hidePast || !pair.bothPast) &&
    (f.gap === "all" || (f.gap === "ahead" ? pair.remainingDays > 0 : f.gap === "overlap" ? pair.overlapDays > 0 : pair.gapDays !== null && pair.gapDays <= Number(f.gap))) &&
    groundMatches(pair, f.ground) &&
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
  if (state.ground !== "all") out.push({ key: "ground", label: GROUND_LABELS[state.ground] });
  if (state.scUtility !== "all") out.push({ key: "scUtility", label: `South Carolina: ${SC_UTILITIES[state.scUtility]} only` });
  if (!state.hidePast) out.push({ key: "hidePast", label: "Including pairs already past both dates" });
  if (state.includePossible) out.push({ key: "includePossible", label: "Including possible (non-qualifying) pairs" });
  if (state.shortlistOnly) out.push({ key: "shortlistOnly", label: "Shortlist only" });
  if (state.selectedProject) out.push({ key: "selectedProject", label: `Project: ${state.projects.find(p => p.id === state.selectedProject)?.projectId}` });
  return out;
}

function clearFilter(key) {
  if (key === "selectedProject") state.selectedProject = null;
  else state[key] = FILTER_DEFAULTS[key];
  syncControls(); $("search").focus();
}

function syncControls() {
  $("search").value = state.search; $("distance").value = String(state.distance); $("year").value = String(state.year);
  $("gap").value = state.gap; $("ground").value = state.ground; $("sc-utility").value = state.scUtility; $("hide-past").checked = state.hidePast; $("possible").checked = state.includePossible;
  $("shortlist-only").checked = state.shortlistOnly; $("sort").value = state.sort;
  applyFilters();
}

function applyFilters() {
  if (state.selectedProject && !state.projects.some(project => project.id === state.selectedProject)) state.selectedProject = null;
  state.pairs = sortPairs(filterPairs(state), state.sort);
  if (state.selectedPair && !state.pairs.some(p => p.id === state.selectedPair)) state.selectedPair = null;
  $("distance-value").textContent = `${state.distance} mi`;
  $("year-value").textContent = String(state.year);
  const q = state.pairs.filter(p => p.qualifies).length;
  const total = state.allPairs.filter(p => p.qualifies).length;
  const pastHidden = state.hidePast ? state.allPairs.filter(p => p.qualifies && p.bothPast).length : 0;
  $("result-scope").textContent = `${total} qualifying in dataset${pastHidden ? ` · ${pastHidden} past-date pair${pastHidden === 1 ? "" : "s"} hidden` : ""}. As of ${formatDate(state.asOf)}.`;
  $("result-count").textContent = `${q} qualifying ${q === 1 ? "pair" : "pairs"}${state.pairs.length > q ? ` + ${state.pairs.length - q} possible` : ""}`;
  $("queue-title").textContent = state.selectedProject ? `Pairs for ${state.projects.find(p => p.id === state.selectedProject)?.projectId}` : "Ranked pairs";
  $("clear-selection").disabled = !state.selectedPair && !state.selectedProject;
  $("skip-details").disabled = !state.selectedPair && !state.selectedProject;
  const filterCount = activeFilters().filter(f => !["search", "selectedProject"].includes(f.key)).length;
  $("reset-filters").disabled = !filterCount;
  $("filter-count").hidden = !filterCount; $("filter-count").textContent = String(filterCount);
  $("shortlist-count").textContent = String(state.shortlist.size);
  $("shortlist-export").disabled = !state.shortlist.size;
  const scroll = $("match-list").scrollTop;
  renderChips(); renderList(); $("match-list").scrollTop = scroll; renderMap(); renderDetail();
  writeViewUrl();
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
    [String(qualifying.filter(p => p.remainingDays > 0).length), "overlapping planning windows"], [String(located.filter(p => !paired.has(p.id)).length), "with no partner in range"]];
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
  box.append(h("strong", "", !project?.center && project ? "Project not located" : lonely ? `No ${otherPlan(project)} match` : "No pairs match these filters"));
  if (project && !project.center) { box.append(h("p", "", `${project.projectId} could not be located, so it can't be paired. The Data quality tab lists why.`)); return box; }
  if (lonely) {
    const n = nearestPartner(project);
    box.append(h("p", "", `${project.projectId} has no ${otherPlan(project)} project within ${MAX_MILES} miles.${n ? ` The nearest is ${n.p.projectId} (${n.p.name}), ${n.miles.toFixed(1)} mi away.` : ""} Most projects in both plans have no match; that is expected.`));
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
  if (state.search) {
    const hits = state.projects.filter(p => matchesProject(p, state.search));
    const projects = h("section", "project-results");
    projects.append(h("h3", "", `${hits.length} project${hits.length === 1 ? "" : "s"} found (all projects)`));
    for (const p of hits) {
      const button = h("button", "project-result"); button.type = "button"; button.dataset.projectId = p.id;
      const partners = state.allPairs.filter(pair => pair.qualifies && [pair.a.id, pair.b.id].includes(p.id)).length;
      button.append(h("strong", "", projectLabel(p)), h("span", "", !p.center ? "Not located · inspect record" : partners ? `${partners} qualifying partner${partners === 1 ? "" : "s"}` : "No partner within 25 miles · inspect record"));
      button.addEventListener("click", () => selectProject(p)); projects.append(button);
    }
    list.append(projects, h("h3", "results-heading", "Pairs under current filters"));
  }
  if (!state.pairs.length) { list.append(emptyState()); return; }
  let rank = 0;
  state.pairs.forEach(pair => {
    const button = h("button", `pair-row${pair.id === state.selectedPair ? " active" : ""}${pair.qualifies ? "" : " possible"}`);
    button.type = "button"; button.dataset.pairId = pair.id; button.setAttribute("aria-pressed", String(pair.id === state.selectedPair));
    button.setAttribute("aria-label", `${pair.qualifies ? `Rank ${rank + 1}, score ${pair.score.total}` : "Possible pair, not qualifying"}: ${pair.a.name} and ${pair.b.name}, ${pair.miles.toFixed(1)} miles`);
    button.append(h("span", "pr-rank", pair.qualifies ? String(++rank) : "?"));
    const body = h("span", "pr-body");
    for (const p of [pair.a, pair.b]) { const n = h("span", `pr-name ${side(p)}`, p.name); n.title = projectLabel(p); body.append(n); }
    const tags = h("span", "pr-tags");
    const tag = (text, kind = "") => tags.append(h("span", `tag ${kind}`, text));
    if (state.shortlist.has(pair.id)) tag("Shortlisted", "star");
    if (!pair.qualifies) tag("Could qualify", "muted");
    if (pair.nearbyEndpoints?.length) tag("Nearby endpoints · site unconfirmed", "muted");
    if (pair.remainingDays > 0) tag("Planning overlap", "good");
    else if (pair.overlapDays > 0) tag("Overlap past", "muted");
    if (pair.certainty === "sensitive") tag("Location-sensitive", "warn");
    const ground = pairGround(pair);
    const mapped = [ground.flood && "Flood area", ground.habitat.length && "Critical habitat", ground.protected.length && "Protected land"].filter(Boolean);
    if (mapped.length) {
      tag(mapped.length > 1 ? `${mapped[0]} +${mapped.length - 1}` : mapped[0], "env");
      tags.lastChild.title = `Mapped near located endpoints: ${mapped.join(", ").toLowerCase()}`;
    }
    for (const p of [pair.a, pair.b]) {
      const others = state.sameStations.get(p.id);
      if (!others) continue;
      tag(others.length === 1 ? `Same stations as ${others[0].projectId}` : `Same stations as ${others.length} others`, "muted");
      tags.lastChild.title = `${p.projectId} (${windowText(p)}) shares its end stations with ${others.map(q => `${q.projectId} · ${q.name} (${windowText(q)})`).join("; ")}. Separate projects in the filing.`;
    }
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

function rememberResults() {
  resultScroll = $("match-list").scrollTop;
  const node = document.activeElement;
  returnTarget = node?.dataset.pairId ? {kind: "pairId", id: node.dataset.pairId} : node?.dataset.projectId ? {kind: "projectId", id: node.dataset.projectId} : null;
}
function revealDetails() {
  if (!state.selectedPair && !state.selectedProject) { $("search").focus(); return; }
  $("view-explore").classList.add("show-detail");
  const detail = $("detail"); detail.focus({preventScroll: true});
  if (window.matchMedia("(max-width: 1100px)").matches) window.scrollTo({top: 0, behavior: "instant"});
}
function backToResults() {
  $("view-explore").classList.remove("show-detail");
  const list = $("match-list"); list.scrollTop = resultScroll;
  const row = returnTarget && [...list.querySelectorAll("button")].find(n => n.dataset[returnTarget.kind] === returnTarget.id);
  (row ?? $("search")).focus({preventScroll: true});
  if (window.matchMedia("(max-width: 1100px)").matches) window.scrollTo({top: 0, behavior: "instant"});
  requestAnimationFrame(() => map?.resize());
}
// Keeps the selected row in view when the pick came from the map or a link; scrolls only the list, not the page.
function scrollToPairRow(id) {
  const list = $("match-list");
  const row = [...list.querySelectorAll(".pair-row")].find(n => n.dataset.pairId === id);
  if (!row || !list.clientHeight) return;
  const top = row.getBoundingClientRect().top - list.getBoundingClientRect().top + list.scrollTop;
  if (top < list.scrollTop || top + row.offsetHeight > list.scrollTop + list.clientHeight)
    list.scrollTo({top: Math.max(0, top - (list.clientHeight - row.offsetHeight) / 2), behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth"});
}
function selectPair(pair) {
  if (!pair) return;
  rememberResults(); state.selectedPair = pair.id; applyFilters(); focusPair(pair); revealDetails(); scrollToPairRow(pair.id);
}
function selectProject(project) {
  if (!project) return;
  rememberResults(); state.selectedProject = project.id; state.selectedPair = null;
  applyFilters(); revealDetails();
}
function writeViewUrl() {
  if (!state.data) return;
  const hash = encodeView(state, FILTER_DEFAULTS);
  history.replaceState(null, "", `${location.pathname}${location.search}${hash ? `#${hash}` : ""}`);
}
function restoreViewUrl() {
  Object.assign(state, decodeView(location.hash, {defaults: FILTER_DEFAULTS, minYear: Number($("year").min), maxYear: Number($("year").max), sortKeys: Object.keys(SORTS)}));
  if (!state.projects.some(p => p.id === state.selectedProject)) state.selectedProject = null;
  if (!state.allPairs.some(p => p.id === state.selectedPair)) state.selectedPair = null;
}

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
    map = new maplibregl.Map({ container: "map", style: BASEMAP, bounds: border, fitBoundsOptions: { padding: 20 }, attributionControl: { compact: window.innerWidth < 760 }, dragRotate: false, pitchWithRotate: false, touchPitch: false });
  } catch (error) { $("map").append(h("p", "map-error", `The map needs WebGL, which this browser has turned off (${error.message}). The list and details still work.`)); return; }
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
  // If the basemap style can't be fetched, fall back to a plain background so the project layers still draw.
  map.on("error", event => { if (!mapReady && !map.isStyleLoaded() && !styleFailed) { styleFailed = true; console.warn("Basemap unavailable:", event.error?.message); map.setStyle(FALLBACK_STYLE); } });
  map.on("load", () => {
    addEnvLayers();
    addLayers();
    mapReady = true;
    syncEnvLayers();
    syncSatellite();
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
  const envHitAt = point => {
    if (!mapReady || !envOn.size) return null;
    const ids = Object.entries(ENV_LAYERS).filter(([k]) => envOn.has(k)).flatMap(([, ls]) => ls).filter(id => map.getLayer(id) && map.getLayer(id).type !== "raster");
    return ids.length ? map.queryRenderedFeatures(point, { layers: ids })[0] : null;
  };
  map.on("mousemove", event => {
    const f = hitAt(event.point);
    setHover(f && f.layer.id !== "endpoints" ? { source: f.source, id: f.id } : null);
    map.getCanvas().style.cursor = f && f.layer.id !== "endpoints" ? "pointer" : "";
    const text = f ? f.properties.title : envTitle(envHitAt(event.point));
    if (text) tip.setLngLat(event.lngLat).setText(text).addTo(map); else tip.remove();
  });
  map.getCanvas().addEventListener("mouseleave", () => { setHover(null); tip.remove(); });
  // MapLibre fires click only for a press that didn't turn into a pan, so a click on empty map deselects.
  map.on("click", event => {
    if (!mapReady) return;
    const hits = map.queryRenderedFeatures(event.point, { layers: ["project-hit", "links-hit"] });
    const project = hits.find(f => f.layer.id === "project-hit");
    const link = hits.find(f => f.layer.id === "links-hit");
    if (project) selectProject(state.projects.find(p => p.id === project.properties.id));
    else if (link) selectPair(state.pairs.find(p => p.id === link.properties.id));
    else if (state.selectedPair || state.selectedProject) { state.selectedPair = null; state.selectedProject = null; applyFilters(); }
  });
}

// Added before the project layers, so they sit underneath. Sources stay empty until a layer is first turned on.
function addEnvLayers() {
  const hidden = { visibility: "none" };
  const empty = featureCollection([]);
  map.addSource("env-evidence", { type: "geojson", data: empty, attribution: "USFWS NWI, FEMA NFHL" });
  map.addSource("env-habitat", { type: "geojson", data: empty, attribution: "USFWS, NOAA Fisheries" });
  map.addSource("env-protected", { type: "geojson", data: empty, attribution: "USGS PAD-US" });
  map.addSource("env-nwi", { type: "raster", tiles: [exportTiles(NWI_EXPORT, 0)], tileSize: 256, minzoom: LIVE_ZOOM.wetlands, attribution: "USFWS NWI" });
  // Imagery goes under the basemap's place labels, so town and road names stay readable on top of it.
  map.addSource("satellite", { type: "raster", tiles: [SATELLITE_TILES], tileSize: 256, maxzoom: 16, attribution: "Imagery USGS, USDA NAIP" });
  map.addLayer({ id: "satellite", type: "raster", source: "satellite", layout: hidden }, map.getStyle().layers.find(l => l.type === "symbol")?.id);
  map.addSource("env-nfhl", { type: "raster", tiles: [exportTiles(NFHL_EXPORT, 28)], tileSize: 256, minzoom: LIVE_ZOOM.flood, attribution: "FEMA NFHL" });
  const layer = ["get", "layer"];
  map.addLayer({ id: "env-protected-fill", type: "fill", source: "env-protected", layout: hidden, paint: { "fill-color": ENV_COLORS.protected, "fill-opacity": 0.12 } });
  map.addLayer({ id: "env-protected-line", type: "line", source: "env-protected", layout: hidden, paint: { "line-color": ENV_COLORS.protected, "line-width": 0.8, "line-opacity": 0.6 } });
  map.addLayer({ id: "env-flood-fill", type: "fill", source: "env-evidence", maxzoom: LIVE_ZOOM.flood, layout: hidden, filter: ["==", layer, "flood"],
    paint: { "fill-color": ["case", ["get", "sfha"], ENV_COLORS.flood, ENV_COLORS.flood02], "fill-opacity": 0.35 } });
  map.addLayer({ id: "env-flood-line", type: "line", source: "env-evidence", maxzoom: LIVE_ZOOM.flood, layout: hidden, filter: ["==", layer, "flood"], paint: { "line-color": ENV_COLORS.flood, "line-width": 0.6 } });
  map.addLayer({ id: "env-nfhl", type: "raster", source: "env-nfhl", layout: hidden, paint: { "raster-opacity": 0.7 } });
  map.addLayer({ id: "env-wetland-fill", type: "fill", source: "env-evidence", maxzoom: LIVE_ZOOM.wetlands, layout: hidden, filter: ["all", ["==", layer, "wetlands"], ["!", ["get", "water"]]], paint: { "fill-color": ENV_COLORS.wetland, "fill-opacity": 0.45 } });
  map.addLayer({ id: "env-water-fill", type: "fill", source: "env-evidence", maxzoom: LIVE_ZOOM.wetlands, layout: hidden, filter: ["all", ["==", layer, "wetlands"], ["get", "water"]], paint: { "fill-color": ENV_COLORS.water, "fill-opacity": 0.45 } });
  map.addLayer({ id: "env-nwi", type: "raster", source: "env-nwi", layout: hidden, paint: { "raster-opacity": 0.75 } });
  map.addLayer({ id: "env-footprint", type: "line", source: "env-evidence", layout: hidden, filter: ["==", layer, "footprint"],
    paint: { "line-color": "#10222a", "line-width": 1, "line-opacity": 0.55, "line-dasharray": [2, 2] } });
  map.addLayer({ id: "env-habitat-fill", type: "fill", source: "env-habitat", layout: hidden, paint: { "fill-color": ENV_COLORS.habitat, "fill-opacity": 0.14 } });
  map.addLayer({ id: "env-habitat-line", type: "line", source: "env-habitat", layout: hidden, paint: { "line-color": ENV_COLORS.habitat, "line-width": ["case", ["in", ["geometry-type"], ["literal", ["LineString", "MultiLineString"]]], 3, 1], "line-opacity": 0.75 } });
}

function syncSatellite() {
  $("satellite-toggle").setAttribute("aria-pressed", String(satelliteOn));
  document.querySelector(".map-panel").classList.toggle("satellite", satelliteOn);
  if (!mapReady) return;
  const visibility = satelliteOn ? "visible" : "none", linkLook = linkStyle(satelliteOn);
  ["satellite", "links-casing", "geometry-casing"].forEach(id => map.setLayoutProperty(id, "visibility", visibility));
  ["links", "links-possible"].forEach(id => map.setPaintProperty(id, "line-color", linkLook.color));
  map.setPaintProperty("links", "line-opacity", linkLook.opacity);
  map.setPaintProperty("links-possible", "line-opacity", linkLook.possibleOpacity);
}

function syncEnvLayers() {
  document.querySelectorAll("#layers-panel input[data-layer]").forEach(input => { input.checked = envOn.has(input.dataset.layer); });
  $("layers-count").hidden = !envOn.size; $("layers-count").textContent = String(envOn.size);
  if (!mapReady) return;
  for (const [key, ids] of Object.entries(ENV_LAYERS)) {
    const on = envOn.has(key);
    if (on) {
      const src = key === "wetlands" || key === "flood" ? "evidence" : key;
      if (!envLoaded.has(src)) { envLoaded.add(src); map.getSource(`env-${src}`).setData(ENV_DATA[src]); }
    }
    ids.forEach(id => map.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
  }
  map.setLayoutProperty("env-footprint", "visibility", envOn.has("wetlands") || envOn.has("flood") ? "visible" : "none");
}

function envTitle(f) {
  if (!f) return null;
  const p = f.properties;
  if (p.layer === "wetlands") return `${p.water ? "Open water" : "Mapped wetland"}: ${p.type} (USFWS National Wetlands Inventory)`;
  if (p.layer === "flood") return `FEMA flood zone ${p.zone}: ${floodZoneText(p.zone, p.subtype) ?? "see FEMA map"}`;
  if (p.layer === "habitat") return `Critical habitat: ${p.species}${p.stage === "proposed" ? " (proposed)" : ""}, ${String(p.status ?? "listed").toLowerCase()} (${p.source})${p.unit ? `\n${p.unit}` : ""}`;
  if (p.layer === "protected") return `Protected land: ${p.name}${p.manager ? `, ${p.manager}` : ""}${p.category ? ` (${p.category.toLowerCase()})` : ""}\nUSGS PAD-US`;
  return null;
}

// Pair links: slate on the grey basemap, white (over a dark casing) on satellite imagery.
function linkStyle(satellite) {
  const hover = ["boolean", ["feature-state", "hover"], false], linkState = ["get", "state"];
  const opacity = (normal, dim) => ["case", hover, 1, ["==", linkState, "selected"], 1, ["==", linkState, "dim"], dim, normal];
  return {
    color: satellite ? "#fff" : ["case", ["any", hover, ["==", linkState, "selected"]], "#10262e", "#4d6670"],
    opacity: opacity(satellite ? 0.85 : 0.6, satellite ? 0.12 : 0.07),
    possibleOpacity: opacity(satellite ? 0.7 : 0.4, satellite ? 0.15 : 0.1),
  };
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
  map.addLayer({ id: "geometry-casing", type: "line", source: "geometry", filter: ["in", ["get", "kind"], ["literal", ["uncertainty", "route-inferred", "stations"]]], layout: { visibility: "none", "line-join": "round", "line-cap": "round" },
    paint: { "line-color": "#fff", "line-width": ["match", ["get", "kind"], "uncertainty", 3.5, 6.5], "line-opacity": 0.75 } });
  map.addLayer({ id: "uncertainty-line", type: "line", source: "geometry", filter: kind("uncertainty"), paint: { "line-color": bySide, "line-width": 1.2, "line-dasharray": [4, 3] } });
  map.addLayer({ id: "routes-inferred", type: "line", source: "geometry", filter: kind("route-inferred"), layout: { "line-join": "round" }, paint: { "line-color": bySide, "line-width": 3, "line-dasharray": [2.5, 1.5] } });
  map.addLayer({ id: "station-lines", type: "line", source: "geometry", filter: kind("stations"), layout: { "line-cap": "round" }, paint: { "line-color": bySide, "line-width": 2.5, "line-dasharray": [0, 2] } });
  const linkLook = linkStyle(satelliteOn);
  const linkPaint = { "line-color": linkLook.color, "line-width": ["case", ["==", linkState, "selected"], 4.5, hover, 3.5, 1.6] };
  // Casings only show over satellite imagery: a dark edge under the white pair links, a light band under project lines.
  map.addLayer({ id: "links-casing", type: "line", source: "links", filter: ["!", ["get", "possible"]], layout: { visibility: "none", "line-sort-key": ["match", linkState, "selected", 2, "normal", 1, 0], "line-cap": "round" },
    paint: { "line-color": "#0b1a1f", "line-width": ["case", ["==", linkState, "selected"], 7.5, hover, 6.5, 3.8], "line-opacity": linkStyle(true).opacity } });
  map.addLayer({ id: "links", type: "line", source: "links", filter: ["!", ["get", "possible"]], layout: { "line-sort-key": ["match", linkState, "selected", 2, "normal", 1, 0], "line-cap": "round" },
    paint: { ...linkPaint, "line-opacity": linkLook.opacity } });
  map.addLayer({ id: "links-possible", type: "line", source: "links", filter: ["get", "possible"],
    paint: { ...linkPaint, "line-dasharray": [2, 2], "line-opacity": linkLook.possibleOpacity } });
  map.addLayer({ id: "links-hit", type: "line", source: "links", paint: { "line-color": "#000", "line-width": 14, "line-opacity": 0 } });
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
  // Stations draw above project dots: a project centered on the selected one's station would otherwise hide it.
  map.addLayer({ id: "endpoints", type: "circle", source: "geometry", filter: kind("endpoint"), paint: { "circle-radius": 3.5, "circle-color": "#fff", "circle-stroke-color": bySide, "circle-stroke-width": 1.5, "circle-opacity": ["case", ["get", "town"], 0.5, 1], "circle-stroke-opacity": ["case", ["get", "town"], 0.5, 1] } });
  map.addLayer({ id: "project-hit", type: "circle", source: "projects", layout: { "circle-sort-key": ["get", "order"] }, paint: { "circle-radius": 12, "circle-color": "#000", "circle-opacity": 0 } });
  map.addLayer({ id: "project-labels", type: "symbol", source: "projects", filter: ["==", focus, "chosen"],
    layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Bold"], "text-size": 12, "text-anchor": "left", "text-offset": [1.35, 0], "text-allow-overlap": true, "text-ignore-placement": true },
    paint: { "text-color": ["match", ["get", "side"], "desc", "#0b5f6b", "#9a4a17"], "text-halo-color": "#fff", "text-halo-width": 2.5 } });
}

function renderMap() {
  endpointTip?.remove(); endpointTip = null;
  const pair = state.allPairs.find(p => p.id === state.selectedPair);
  const project = state.projects.find(p => p.id === state.selectedProject);
  const activeIds = new Set(state.pairs.flatMap(p => [p.a.id, p.b.id]));
  const focusIds = new Set(pair ? [pair.a.id, pair.b.id] : project ? [project.id] : []);
  const visibleProjects = state.projects.filter(p => p.center && bySelectedYear(p) && byScUtility(p));
  $("desc-count").textContent = String(visibleProjects.filter(p => p.state === "SC").length);
  const scLabel = $("desc-count").previousSibling;   // the legend text before the count names the South Carolina utilities shown
  if (scLabel?.nodeType === Node.TEXT_NODE) scLabel.textContent = state.scUtility === "all" ? "DESC and Santee Cooper, SC" : `${SC_UTILITIES[state.scUtility]}, SC`;
  $("gpc-count").textContent = String(visibleProjects.filter(p => p.state === "GA").length);
  if (!mapReady) return;
  const geometry = (pair ? [pair.a, pair.b] : project ? [project] : []).flatMap(geometryFeatures);
  map.getSource("geometry").setData(featureCollection(geometry));
  // Line-style rows appear only while the selection draws that kind of line.
  for (const row of document.querySelectorAll(".map-legend [data-kind]")) row.hidden = !geometry.some(f => f.properties.kind === row.dataset.kind);
  map.getSource("links").setData(featureCollection(state.pairs.map(p => ({
    type: "Feature", geometry: { type: "LineString", coordinates: [lngLat(p.a.center), lngLat(p.b.center)] },
    properties: { id: p.id, possible: !p.qualifies, state: p.id === state.selectedPair ? "selected" : state.selectedPair ? "dim" : "normal", title: `${p.a.projectId} ↔ ${p.b.projectId}: ${p.miles.toFixed(1)} mi between comparison points` },
  }))));
  map.getSource("projects").setData(featureCollection(visibleProjects.map(p => pointFeature(p.center, {
    id: p.id, side: side(p), matched: activeIds.has(p.id), focus: focusIds.has(p.id) ? "chosen" : pair ? "faded" : "normal", label: p.projectId,
    order: (activeIds.has(p.id) ? 1 : 0) + (focusIds.has(p.id) ? 2 : 0),
    title: `${projectLabel(p)}\n${pointKind(p)} · worksite unverified\n${sideName(p)} · in service ${formatDate(p.inServiceDate)}`,
  }))));
}

// Station-level placements zoom close enough to see the yard; a town-level guess stays at town scale.
function focusEndpoint(e) {
  if (!mapReady) return;
  endpointTip?.remove();
  endpointTip = new maplibregl.Popup({ closeButton: false, className: "map-tip", offset: 10 }).setLngLat(lngLat(e.point)).setText(e.name).addTo(map);
  map.flyTo({ center: lngLat(e.point), zoom: sitePlaced(e) ? 15 : 12, duration: reducedMotion() ? 0 : 900 });
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
  const today = h("div", "tl-today"); today.style.left = pct(state.asOf); today.title = `Data as of ${state.asOf}`;
  const axis = h("div", "tl-axis"); const tlabel = h("span", "tl-now", "as of"); tlabel.style.left = pct(state.asOf);
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
const DESCRIBED = "Station named in filing description";
const describedNames = p => p.describedStations.map(d => d.name).join(", ");
function pointKind(p) {
  const sites = located(p);
  if (p.locatedBy === "description") return "Near stations named in filing description";
  if (!sites.length) return "Not located";
  if (sites.length > 1) return sites.length === 2 ? "Calculated midpoint" : "Calculated average";
  if (sites[0].method === "town") return "Town estimate";
  return p.endpoints.length > 1 ? "Single mapped endpoint" : "Mapped endpoint";
}
function centerMethod(p) {
  const n = located(p).length, total = p.endpoints.length;
  if (p.locatedBy === "description") return `${p.endpoints.length ? `${p.endpoints.map(e => e.name).join(", ")} could not be located. ` : ""}Placed among the existing stations the filing's description names (${describedNames(p)}); an estimate, not the worksite`;
  if (!n) return "Not located";
  if (n >= 2) return `${pointKind(p)} of ${n} mapped endpoints; this is a comparison point, not a verified worksite`;
  if (located(p)[0].method === "town") return `Town estimate for ${located(p)[0].name}; station and worksite unverified`;
  if (total > 1) return `Single mapped endpoint (${located(p)[0].name}); the other end is not located, so the radius is widened`;
  return `Mapped endpoint (${located(p)[0].name}); worksite unverified`;
}
function sourceLabel(src) { return src.url.includes("psc.ga.gov") ? `${src.doc}, PDF p. ${src.page} (link downloads the PSC zip)` : `${src.doc}, p. ${src.page}`; }
function sourceLink(p) { return link(pdfLink(p.source), sourceLabel(p.source)); }
function targetDate(p) { return `${formatDate(p.inServiceDate)}${p.issues.some(i => /date/.test(i.msg) && /repaired|impossible|last day/.test(i.msg)) || (p.inServiceRaw && !p.inServiceRaw.includes(p.inServiceDate?.slice(0, 4))) ? ` (printed “${p.inServiceRaw}”)` : ""}`; }
function statusText(p) {
  const passed = datePassed(p.inServiceDate);
  if (!p.status) return passed ? "The filing gives no status, and the target date has passed. Current status not confirmed." : "The filing gives no status.";
  return passed ? `Filing says “${p.status}”, but the target date has passed. Current status not confirmed.` : `“${p.status}” in the filing. Not independently confirmed.`;
}
const statusShort = p => `${p.status ? `“${p.status}” in filing` : "No status in filing"}${datePassed(p.inServiceDate) ? "; date passed, unconfirmed" : ""}`;

// Only station-level placements get imagery links; a town guess would open an unrelated street.
const sitePlaced = e => e.point && ["high", "medium"].includes(e.confidence) && e.method !== "town";
// Street View links (map_action=pano) open a black "no imagery" screen at most stations, which sit back from the road; a pin keeps Street View one drag of the pegman away.
const mapPinUrl = pt => `https://www.google.com/maps/search/?api=1&query=${pt.lat.toFixed(5)},${pt.lon.toFixed(5)}`;

function projectBlock(p, full = false, heading = true, foldEvidence = false) {
  const block = h("section", "project-block");
  if (heading) block.append(h("span", `utility-label ${side(p)}`, `${sideName(p)}, ${p.state}${p.zoneName ? `, ${p.zoneName}` : ""}`), h("h3", "", p.name));
  block.append(infoRow("Project ID", p.projectId), infoRow("Type", projectType(p)));
  block.append(infoRow("Target in service (published)", targetDate(p)));
  const st = infoRow("Current status", statusText(p)); if (datePassed(p.inServiceDate)) st.classList.add("warn-row"); block.append(st);
  if (p.window?.start) block.append(infoRow("Planning window", `${formatDate(p.window.start)} → ${formatDate(p.window.end)} (${p.window.basis})`));
  block.append(infoRow("Cost", p.cost?.total ? `${money(p.cost.total)} (DESC estimate)` : p.state === "GA" ? "Redacted in the public filing" : p.utility === "SCPSA" ? "Not published in Santee Cooper's list" : "—"));
  if (p.miles || p.route) block.append(infoRow("Length", `${p.miles ? `${p.miles} mi stated` : "not stated"}${p.route ? ` · ${p.route.miles} mi inferred OSM path (circuit unverified)` : ""}`));
  if (p.slipDays) block.append(infoRow("Schedule history", `${p.slipDays > 0 ? "slipped" : "advanced"} ${Math.abs(Math.round(p.slipDays / 30.44))} months since ${p.history[0].edition}`));
  else if (p.change && p.state === "GA") block.append(infoRow("Change vs last plan", p.change));
  const others = state.sameStations?.get(p.id);
  if (others) block.append(infoRow("Same end stations", `${others.map(q => `${q.projectId} · ${q.name} (${windowText(q)})`).join("; ")}. Listed as a separate project in the filing, not a duplicate.`));
  block.append(infoRow("Project dot", centerMethod(p)), infoRow("Location confidence", `${p.locationConfidence} · ±${p.radiusMi ?? "?"} mi`));
  const src = h("div", "source-line"); src.append(document.createTextNode("Source: "), sourceLink(p), document.createTextNode(` (${p.source.item})`));
  block.append(src);
  if (full) {
    if (p.description) block.append(h("p", "desc-text", p.description));
    const endpoints = h("div", "endpoint-box"); endpoints.append(h("h4", "", "Endpoints and how each was located"));
    for (const e of p.endpoints) {
      const row = h("p", "");
      if (e.point) {
        const go = h("button", "endpoint-go", e.name); go.type = "button"; go.title = `Show ${e.name} on the map`;
        go.addEventListener("click", () => focusEndpoint(e));
        row.append(go, `: ${e.point.lat.toFixed(4)}, ${e.point.lon.toFixed(4)} · ${e.method} · ${e.confidence} (±${e.radiusMi} mi)`);
      } else row.append(`${e.name}: not located`);
      if (sitePlaced(e)) { const views = h("span", "site-views"); views.append(link(mapPinUrl(e.point), "Google Maps")); row.append(views); }
      if (e.evidence) row.append(h("span", "evidence", e.evidence));
      endpoints.append(row);
    }
    if (p.locationNote) endpoints.append(h("p", "evidence", p.locationNote));
    if (p.locatedBy === "description") {
      endpoints.append(h("h4", "", "Stations named in the filing description"));
      for (const d of p.describedStations) {
        const row = h("p", ""), go = h("button", "endpoint-go", d.name); go.type = "button"; go.title = `Show ${d.name} on the map`;
        go.addEventListener("click", () => focusEndpoint(d));
        row.append(go, `: ${d.point.lat.toFixed(4)}, ${d.point.lon.toFixed(4)} · ${DESCRIBED.toLowerCase()}`, h("span", "evidence", d.evidence));
        endpoints.append(row);
      }
    }
    if (foldEvidence) {
      const evidence = h("details", "project-evidence detail-fold");
      evidence.append(h("summary", "", "Location evidence and validation"), endpoints, h("h4", "", "Validation"), issuesList(p));
      block.append(evidence);
    } else block.append(endpoints, h("h4", "", "Validation"), issuesList(p));
  }
  return block;
}

// ---- What a planner needs from a pair, shared by the panel and the brief ----
function whyQualifies(pair) {
  const out = [`Centers are ${pair.miles.toFixed(2)} mi apart, ${pair.qualifies ? `under the challenge's ${MAX_MILES}-mile rule` : `outside the ${MAX_MILES}-mile rule`}. ${certaintyText[pair.certainty]}`];
  if (pair.nearbyEndpoints?.length) out.push(`Network endpoints are nearby (${pair.nearbyEndpoints.map(s => s.a === s.b ? s.a : `${s.a} / ${s.b}`).join(", ")}); this does not confirm construction at a shared site.`);
  if (pair.remainingDays > 0) out.push(`Their inferred planning windows overlap for ${Math.round(pair.remainingDays / 30.44)} months after the data date.`);
  else if (pair.overlapDays > 0) out.push("Their inferred planning windows overlapped, but that overlap is in the past.");
  else if (pair.gapDays !== null) out.push(`Their inferred planning windows don't overlap; in-service dates are ${pair.gapDays} days apart.`);
  return out;
}

function summaryRationale(pair) {
  const scope = pair.nearbyEndpoints?.length ? "Nearby network endpoints are a lead to investigate; a shared worksite is unconfirmed." : "Site-level sharing is unconfirmed.";
  return `${pair.qualifies ? "Meets the under-25-mile center-distance rule." : "Outside the center-distance rule; precise locations could change qualification."} ${scope}`;
}

function toConfirm(pair, compact = false) {
  const out = [];
  for (const p of [pair.a, pair.b]) {
    if (datePassed(p.inServiceDate)) out.push(`${sideName(p)} ${p.projectId}: target date ${formatDate(p.inServiceDate)} has passed while the filing still ${p.status ? `says “${p.status}”` : "lists it"}. Is it built, delayed or dropped?`);
    if (p.slipDays > 0) out.push(`${sideName(p)} ${p.projectId} has slipped ${Math.round(p.slipDays / 30.44)} months since ${p.history[0].edition}. Is the current date firm?`);
    if (p.locatedBy === "description") out.push(`${sideName(p)} ${p.projectId}: placed only near the stations its description names (${describedNames(p)}). Where is the project's own station?`);
    for (const e of p.endpoints) {
      if (!e.point) out.push(`${sideName(p)} ${p.projectId}: endpoint “${e.name}” is not located.`);
      else if (!["high"].includes(e.confidence)) out.push(`${sideName(p)} ${p.projectId}: “${e.name}” placed by ${e.method} (${e.confidence}, ±${e.radiusMi} mi). Confirm the site.`);
    }
  }
  if (pair.certainty === "sensitive") out.push(`The pair is inside ${MAX_MILES} mi only at best-estimate locations (±${pair.a.radiusMi} and ±${pair.b.radiusMi} mi).`);
  // The panel shows these two caveats, true of every pair, under the timeline and in the cost scenario.
  if (compact) return out;
  out.push(windowBasis(pair));
  out.push("Proximity alone doesn't show that land, yard space or equipment can be shared. Confirm site access, ownership and each utility's contracting rules.");
  return out;
}

function windowBasis(pair) {
  return `Planning windows are inferred (${pair.a.window?.basis ?? "unknown"}; ${pair.b.window?.basis ?? "unknown"}). Confirm construction and outage months with both utilities.`;
}

// Sharing other than the staging yard the scenario models.
function beyondYard(pair) {
  const out = [];
  if (pair.remainingDays > 0) out.push("Ask whether crane, mat and specialty-crew mobilizations could be coordinated.");
  if (projectType(pair.a) === projectType(pair.b)) out.push(`Same kind of work (${projectType(pair.a).toLowerCase()}): joint procurement, shared spares or one specialist contractor.`);
  if (!out.length) out.push("Crew and contractor scheduling between the two utilities; no site-level sharing is indicated.");
  return out;
}

function sharedResources(pair) {
  if (!pair.qualifies) return ["Verify precise locations and the center-distance rule before proposing shared resources."];
  return [...(pair.remainingDays > 0 ? ["If a usable shared site is confirmed, compare one staging/laydown yard with two (see scenario)."] : []), ...beyondYard(pair)];
}

function planningForumBlock(pair) {
  const box = h("section", "list-box planning-forums"); box.append(h("h3", "", "Where to raise it"));
  for (const p of [pair.a, pair.b]) {
    const forum = PLANNING_FORUMS[p.state]; if (!forum) continue;
    const row = h("p"); row.append(h("strong", "", `${sideName(p)}: `), link(forum.contactUrl, `${forum.shortName} contact page`)); box.append(row);
  }
  const note = h("p", "muted-note", `${PLANNING_TRANSITION.text} Checked ${formatDate(PLANNING_TRANSITION.checkedOn)}. `);
  note.append(link(PLANNING_TRANSITION.sourceUrl, PLANNING_TRANSITION.sourceLabel)); box.append(note); return box;
}
function planningForumText(pair) {
  return [...[pair.a, pair.b].map(p => `${sideName(p)}: ${PLANNING_FORUMS[p.state].shortName} - ${PLANNING_FORUMS[p.state].contactUrl}`), `${PLANNING_TRANSITION.text} Checked ${PLANNING_TRANSITION.checkedOn}: ${PLANNING_TRANSITION.sourceUrl}`];
}

function questions(pair) {
  const other = sideName(pair.b);
  return [
    `Is ${pair.b.projectId} still scheduled for ${formatDate(pair.b.inServiceDate)}? Which months need outages or heavy construction?`,
    "Where will your staging/laydown yard be, how large, and is there room for a second project's material?",
    "Which contractors, mat suppliers and crane vendors are you planning to use?",
    `Would ${other} share a site lease or access road if the schedules line up? What approvals would that need?`,
    "Who is the right planning contact for follow-up?",
  ];
}

// ---- Staging-yard scenario ----
function yardInputs() { const y = state.yard; return { acres: y.acres, months: y.months, leaseRate: y.leaseRate, surfacePerAcre: y.surface === "mats" ? YARD_BASIS.matsPerAcre.value : y.surfacePerAcre, roadMiles: y.roadMiles }; }
function scenarioLines(pair, sc) {
  return [
    ["Yard surface", `${sc.acres} ac × ${money(sc.surfacePerAcre)}/ac = ${money(sc.parts.surface)}`, state.yard.surface === "mats" ? "cited: MISO timber-mat rate" : "your assumption"],
    ["Land lease", `${sc.acres} ac × ${money(sc.landPerAcre)}/ac × ${Math.round(sc.leaseRate * 100)}%/yr × ${sc.months} mo = ${money(sc.parts.lease)}`, "land value cited (USDA, GA/SC average); lease rate is an assumption"],
    ["Access road", `${sc.roadMiles} mi × ${money(YARD_BASIS.roadPerMile.value)}/mi = ${money(sc.parts.road)}`, "cited: MISO access-road rate; length is an assumption"],
  ];
}

// Working separately vs coordinated, for the one thing the cost model covers: staging yards over the shared months.
function sideBySide(pair, sc) {
  const box = h("div", "sbs");
  const col = (title, yards, cost, note) => { const c = h("div", "sbs-col"); c.append(h("h4", "", title), yards, h("div", "sbs-cost", cost), h("p", "", note)); return c; };
  const yard = (cls, text) => { const y = h("span", `yard ${cls}`); y.append(h("span", "", text)); return y; };
  const apart = h("div", "yards"); apart.append(yard(side(pair.a), sideName(pair.a)), yard(side(pair.b), sideName(pair.b)));
  const together = h("div", "yards");
  if (sc.active) together.append(yard("both", "Shared"));
  else together.append(yard(side(pair.a), sideName(pair.a)), yard(side(pair.b), sideName(pair.b)));
  box.append(
    col("Separately", apart, money(2 * sc.oneYard), `Two yards, ${money(sc.oneYard)} each, over ${sc.months} months.`),
    col("Coordinated", together, sc.active ? `${money(sc.oneYard)} – ${money(1.5 * sc.oneYard)}` : money(2 * sc.oneYard),
      sc.active ? `One combined yard, assumed 1.0–1.5× the size of one, for ${sc.months} assumed shared months.` : sc.reason));
  return box;
}

function yardCard(pair) {
  const card = h("section", "cost-card");
  card.append(h("h3", "", "One shared staging yard instead of two"));
  if (!pair.qualifies) { card.append(h("p", "", "Only modeled for qualifying pairs.")); return card; }
  card.append(h("p", "scenario-caution", `${certaintyText[pair.certainty]} Construction timing is inferred; a usable shared yard, access and agreement have not been confirmed. This is an illustrative avoided-cost scenario, not verified savings.`));
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
    out.replaceChildren(sideBySide(pair, sc), h("div", "cost-big", sc.active ? `${money(sc.low)} – ${money(sc.high)}` : "$0"), h("p", "cost-caption", sc.active ? "illustrative avoided cost if sharing is feasible" : sc.reason));
    const ul = h("ul", "cost-basis"); scenarioLines(pair, sc).forEach(([k, v, why]) => { const li = h("li"); li.append(h("b", "", `${k}: `), document.createTextNode(v), h("span", "why", ` · ${why}`)); ul.append(li); });
    ul.append(h("li", "", `One yard ≈ ${money(sc.oneYard)}. A combined yard is assumed to be 1.0–1.5× one project's yard, so sharing avoids 0.5–1.0 of a yard.`));
    const ground = groundCostNote(pair);
    if (ground) ul.append(h("li", "", ground));
    const est = savingsEstimate(pair, { benchmarkPerMile: state.data.costBenchmark.perMile });
    if (est && sc.active) ul.append(h("li", "", `For scale: the smaller project costs about ${money(Math.min(est.costA, est.costB))}${est.aEstimated || est.bEstimated ? ` (${[est.aEstimated && sideName(pair.a), est.bEstimated && sideName(pair.b)].filter(Boolean).join(" and ")} estimated from DESC's median $/mi)` : " (published)"}; the high end is ${((sc.high / Math.min(est.costA, est.costB)) * 100).toFixed(1)}% of it.`));
    out.append(ul);
    if (!sc.active) out.append(h("p", "", `Scenario unavailable: ${sc.reason}`));
  }
  update();
  const assumptions = h("details", "detail-fold scenario-assumptions");
  assumptions.append(h("summary", "", "Adjust assumptions"), form);
  card.append(out, h("p", "", `Beyond the yard: ${beyondYard(pair).join(" ")}`), assumptions);
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
    ["Status", p => statusShort(p)],
    ["Planning window", p => p.window?.start ? `${formatDate(p.window.start)} → ${formatDate(p.window.end)}` : "unknown"],
    ["Location", p => `${p.locationConfidence}, ±${p.radiusMi} mi · ${centerMethod(p).split(" (")[0].split(";")[0]}`],
    ["Source", p => sourceLink(p)],
  ];
  for (const [k, f] of rows) { const tr = h("tr"); tr.append(h("th", "", k)); for (const p of [pair.a, pair.b]) { const v = f(p); const td = h("td"); td.append(v instanceof Node ? v : document.createTextNode(v)); if (k === "Status" && datePassed(p.inServiceDate)) td.className = "warn-cell"; tr.append(td); } t.append(tr); }
  return t;
}

function listBox(title, items, cls = "") { const box = h("section", `list-box ${cls}`); box.append(h("h3", "", title)); const ul = h("ul"); items.forEach(i => ul.append(h("li", "", i))); box.append(ul); return box; }

const GROUND_NOTE = "What federal maps show (FEMA, USFWS, NOAA Fisheries, USGS PAD-US), read within 0.25 mi of each station-level site and along traced lines. A map is not a field survey or a permit decision; each project still needs its own permits.";
function groundBox(pair) {
  const box = h("section", "list-box ground-box");
  box.append(h("h3", "", "Ground near mapped endpoints"));
  for (const p of [pair.a, pair.b]) {
    box.append(h("h4", `ground-for ${side(p)}`, `${sideName(p)} ${p.projectId}`));
    const ul = h("ul"); groundLines(p, state.data.environmentRadiusMi).forEach(t => ul.append(h("li", "", t))); box.append(ul);
  }
  const note = h("p", "muted-note", `${GROUND_NOTE} `);
  const show = h("button", "link-button", "Show on the map"); show.type = "button";
  show.addEventListener("click", () => { if (!envOn.size) { envOn.add("wetlands"); envOn.add("flood"); saveLayers(); syncEnvLayers(); } openLayersPanel(); });
  note.append(show);
  box.append(note);
  return box;
}

function toggleShortlist(pair) {
  if (state.shortlist.has(pair.id)) state.shortlist.delete(pair.id); else state.shortlist.add(pair.id);
  saveShortlist(); applyFilters();
  const action = $("detail").querySelector('[data-action="shortlist"]');
  if (action) action.focus({preventScroll: true});
  else { backToResults(); ($("match-list").querySelector("button") ?? $("search")).focus({preventScroll: true}); }
}

const DETAIL_TABS = [["summary", "Summary"], ["scenario", "Cost scenario"], ["records", "Records"]];
let lastDetailKey = null;
const detailFolds = new Map();

function foldSection(section, key, initiallyOpen = false) {
  const heading = section.querySelector(":scope > h3");
  const fold = h("details", "detail-fold");
  const summary = h("summary");
  while (heading?.firstChild) summary.append(heading.firstChild);
  heading?.remove();
  // Open/closed is remembered per section, not per pair, so switching pairs keeps the same sections open.
  fold.dataset.fold = key;
  fold.open = detailFolds.get(key) ?? initiallyOpen;
  fold.addEventListener("toggle", () => detailFolds.set(key, fold.open));
  fold.append(summary, section);
  return fold;
}

function recordFold(project) {
  const fold = h("details", "detail-fold record-fold");
  const summary = h("summary");
  summary.append(h("span", `utility-label ${side(project)}`, sideName(project)), h("strong", "", `${project.projectId} · ${project.name}`));
  const stateKey = `${state.selectedPair}:record:${project.id}`;
  fold.open = detailFolds.get(stateKey) ?? false;
  fold.addEventListener("toggle", () => detailFolds.set(stateKey, fold.open));
  fold.append(summary, projectBlock(project, true, false));
  return fold;
}

// Endpoint names as map shortcuts; the fold below keeps coordinates and evidence.
function endpointChips(p) {
  const box = h("div", "endpoint-chips");
  for (const e of p.endpoints) {
    if (!e.point) { const miss = h("span", "endpoint-missing", e.name); miss.title = "Not located"; box.append(miss); continue; }
    const go = h("button", `endpoint-chip ${side(p)}${e.method === "town" ? " town" : ""}`, e.name); go.type = "button";
    go.title = `Show ${e.name} on the map${e.method === "town" ? " (town estimate, station not located)" : ""}`;
    go.addEventListener("click", () => focusEndpoint(e));
    box.append(go);
  }
  return box;
}

function spanHead(pair) {
  const head = h("header", "span-head");
  const row = h("div", "span-row");
  const end = p => { const e = h("div", `span-end ${side(p)}`); e.append(h("span", "", sideName(p)), h("strong", "", p.projectId)); return e; };
  const line = h("div", "span-line"); line.append(h("i"), h("b", "", `${pair.miles.toFixed(2)} mi`));
  row.append(end(pair.a), line, end(pair.b));
  const names = h("div", "span-names");
  for (const p of [pair.a, pair.b]) { const col = h("div"); col.append(h("p", "", p.name), endpointChips(p)); names.append(col); }
  head.append(row, names, h("p", "point-basis", `Distance uses project dots: ${sideName(pair.a)} ${pointKind(pair.a).toLowerCase()} · ${sideName(pair.b)} ${pointKind(pair.b).toLowerCase()}. These dots may not be construction sites.`));
  if (!pair.qualifies) head.append(h("p", "notice", `Does not qualify: centers are over ${MAX_MILES} mi apart, but location uncertainty could bring them under.`));
  const actions = h("div", "detail-actions");
  const on = state.shortlist.has(pair.id);
  const star = h("button", `button${on ? " on" : ""}`, on ? "★ Shortlisted" : "☆ Shortlist"); star.type = "button"; star.dataset.action = "shortlist"; star.setAttribute("aria-pressed", String(on)); star.addEventListener("click", () => toggleShortlist(pair));
  const brief = h("button", "button", "Open brief"); brief.type = "button"; brief.addEventListener("click", () => openBriefs([pair]));
  const copy = h("button", "button", "Copy brief text"); copy.type = "button"; copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(briefText(pair)); copy.textContent = "Copied"; } catch { copy.textContent = "Copy failed"; } });
  const share = h("button", "button", "Copy link"); share.type = "button";
  share.addEventListener("click", async () => { writeViewUrl(); try { await navigator.clipboard.writeText(location.href); share.textContent = "Link copied"; } catch { share.textContent = "Use address bar link"; } });
  const sharing = h("details", "action-menu");
  sharing.append(h("summary", "button", "Share pair"));
  const options = h("div", "action-menu-items"); options.append(copy, share); sharing.append(options);
  actions.append(star, brief, sharing); head.append(actions);
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
    t.id = `detail-tab-${key}`; t.tabIndex = state.detailTab === key ? 0 : -1;
    t.setAttribute("role", "tab"); t.setAttribute("aria-selected", String(state.detailTab === key)); t.setAttribute("aria-controls", "detail-content");
    t.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); const i = DETAIL_TABS.findIndex(([k]) => k === key);
      const next = event.key === "Home" ? 0 : event.key === "End" ? DETAIL_TABS.length - 1 : (i + (event.key === "ArrowRight" ? 1 : -1) + DETAIL_TABS.length) % DETAIL_TABS.length;
      state.detailTab = DETAIL_TABS[next][0]; renderDetail(); $("detail").querySelector(".dtab.active")?.focus(); writeViewUrl();
    });
    t.addEventListener("click", () => { state.detailTab = key; renderDetail(); $("detail").querySelector(".dtab.active")?.focus(); writeViewUrl(); });
    nav.append(t);
  }
  const panel = h("div", "dpanel"); panel.id = "detail-content"; panel.tabIndex = 0; panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", `detail-tab-${state.detailTab}`);
  if (state.detailTab === "scenario") panel.append(yardCard(pair));
  else if (state.detailTab === "records") { panel.append(h("p", "section-note", "Published fields, location evidence and validation for each project.")); for (const p of [pair.a, pair.b]) panel.append(recordFold(p)); }
  else {
    panel.append(compareTable(pair), h("p", "pair-rationale", summaryRationale(pair)));
    const tl = h("section", "list-box"); tl.append(h("h3", "", "Planning windows"), timeline(pair.a, pair.b), h("p", "muted-note", windowBasis(pair))); panel.append(foldSection(tl, "timeline"));
    const confirm = toConfirm(pair, true);
    if (confirm.length) panel.append(foldSection(listBox("Still to confirm", confirm, "confirm"), "confirm", true));
    panel.append(foldSection(groundBox(pair), "ground"));
    panel.append(foldSection(scoreBlock(pair), "score"), foldSection(planningForumBlock(pair), "forums"));
  }
  return [nav, panel];
}

function scoreBlock(pair) {
  const score = h("section", "list-box score-block");
  const head = h("h3", "", "Ranking score "); head.append(h("span", "", `${pair.score.total} of 100, geography first`)); score.append(head);
  const parts = h("div", "score-parts");
  [["Proximity", pair.score.parts.proximity, WEIGHTS.proximity], ["Timing", pair.score.parts.timing, WEIGHTS.timing]].forEach(([k, v, max]) => {
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
    h("p", "lead", `Every planned project in the South Carolina lists published through SCRTP (DESC’s and Santee Cooper’s 2026–2030 lists) and Georgia ITS’s 2026–2035 plan, each South Carolina project paired with each Georgia project when their centers are under ${MAX_MILES} miles apart.`),
    overviewStats());
  const steps = h("ol", "guide-steps");
  [["Find", "Pairs are ranked geography first, timing second. Change the sort or open Filters to explore."], ["Check", "Every number links to the filing page it came from, and every location states its method and confidence."], ["Call", "Shortlist a pair and export a coordination brief with evidence, shared resources, a cost scenario and questions."]]
    .forEach(([title, body]) => { const li = h("li"); li.append(h("strong", "", title), h("p", "", body)); steps.append(li); });
  box.append(steps, h("p", "fine", "Pick a pair in the list or a link on the map to compare both projects. Pick a dot to see one project. Click empty map to deselect. Built from public filings and OpenStreetMap only, with no CEII. A planning aid, not a field plan."));
  return box;
}

// The section at the top of the detail panel and how far into it the reader has scrolled.
function detailAnchor(detail) {
  const top = detail.getBoundingClientRect().top;
  const fold = [...detail.querySelectorAll("[data-fold]")].find(f => f.getBoundingClientRect().bottom > top);
  return {scrollTop: detail.scrollTop, key: fold?.dataset.fold, offset: fold ? fold.getBoundingClientRect().top - top : 0};
}
function restoreDetailAnchor(detail, anchor) {
  const fold = anchor.key && detail.querySelector(`[data-fold="${anchor.key}"]`);
  detail.scrollTop = fold ? detail.scrollTop + fold.getBoundingClientRect().top - detail.getBoundingClientRect().top - anchor.offset : anchor.scrollTop;
}

function renderDetail() {
  const detail = $("detail");
  const pair = state.allPairs.find(p => p.id === state.selectedPair);
  const project = state.projects.find(p => p.id === state.selectedProject);
  const key = pair ? `pair:${pair.id}` : project ? `project:${project.id}` : "none";
  const changed = key !== lastDetailKey;
  // Pair to pair, stay on the section being read instead of jumping back to the top.
  const anchor = changed && pair && lastDetailKey?.startsWith("pair:") && detail.scrollTop > 0 ? detailAnchor(detail) : null;
  detail.replaceChildren();
  lastDetailKey = key;
  detail.classList.toggle("draw", changed && Boolean(pair));
  if (changed && !anchor) detail.scrollTop = 0;
  detail.setAttribute("aria-label", pair ? `Selected pair: ${pair.a.projectId} and ${pair.b.projectId}` : project ? `Selected project: ${project.name}` : "How to use GridLock");
  if (pair || project) {
    const back = h("button", "button back-results", "← Back to results"); back.type = "button"; back.addEventListener("click", backToResults); detail.append(back);
  } else $("view-explore").classList.remove("show-detail");
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
      lead = `No ${otherPlan(project)} project within ${MAX_MILES} miles.${n ? ` The nearest is ${n.p.projectId}, ${n.p.name}, ${n.miles.toFixed(1)} mi away.` : ""}`;
    } else lead = `${count} ${count === 1 ? "pair" : "pairs"} with ${otherPlan(project)} under the current filters. Pick one in the list to compare.`;
    head.append(h("p", "lead", lead));
    if (project.endpoints.length) head.append(endpointChips(project));
    detail.append(head, projectBlock(project, true, false, true));
  } else detail.append(landing());
  if (anchor) restoreDetailAnchor(detail, anchor);
}

// ---- Brief: plain text for pasting, and a printable one-page document ----
function briefText(pair) {
  const sc = yardScenario(pair, yardInputs());
  const bullets = xs => xs.map(x => `  - ${x}`);
  return [
    `Gridlock coordination brief · ${state.asOf}`, "",
    `${pair.a.name} (${sideName(pair.a)} ${pair.a.projectId}, ${projectType(pair.a)})`, `  × ${pair.b.name} (${sideName(pair.b)} ${pair.b.projectId}, ${projectType(pair.b)})`, "",
    `Distance: ${pair.miles.toFixed(2)} mi center to center (${pair.certainty}) · In-service gap: ${pair.gapDays ?? "unknown"} days (${pair.a.inServiceDate} vs ${pair.b.inServiceDate})`,
    "", pair.qualifies ? "Why it qualifies:" : "Does not qualify at current locations - why it might qualify:", ...bullets(whyQualifies(pair)),
    "", "Possible shared resources:", ...bullets(sharedResources(pair)),
    "", `Staging-yard scenario: ${sc.active ? `${money(sc.low)} – ${money(sc.high)} (illustrative; feasibility unconfirmed)` : `Unavailable: ${sc.reason}`}`, ...(sc.active ? bullets(scenarioLines(pair, sc).map(([k, v, why]) => `${k}: ${v} (${why})`)) : []),
    "", "Ground near mapped endpoints (not surveyed):", ...[pair.a, pair.b].flatMap(p => [`  ${sideName(p)} ${p.projectId}:`, ...groundLines(p, state.data.environmentRadiusMi).map(x => `    - ${x}`)]),
    "", "Still to confirm:", ...bullets(toConfirm(pair)),
    "", `Questions for ${sideName(pair.b)}:`, ...questions(pair).map((q, i) => `  ${i + 1}. ${q}`),
    "", "Where to raise it:", ...planningForumText(pair),
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
  const eps = p => p.endpoints.map(e => `${esc(e.name)}: ${e.point ? `${e.point.lat.toFixed(4)}, ${e.point.lon.toFixed(4)} · ${esc(e.method)}, ${esc(e.confidence)} ±${e.radiusMi} mi` : "not located"}${sitePlaced(e) ? ` · <a href="${esc(mapPinUrl(e.point))}">Google Maps</a>` : ""}`).join("<br>");
  return `<section class="page">
  <header><div><p class="eyebrow">Gridlock coordination brief · ${esc(state.asOf)}</p><h1>${esc(a.name)} <span>×</span> ${esc(b.name)}</h1></div>
  <div class="figs"><div><b>${pair.miles.toFixed(2)} mi</b>center to center</div><div><b>${pair.gapDays ?? "?"} days</b>in-service gap</div><div><b>${esc(pair.certainty)}</b>location</div></div></header>
  ${!pair.qualifies ? `<p class="qualification-notice">Does not qualify at current locations: outside the ${MAX_MILES}-mile rule.</p>` : ""}
  ${briefMapSvg(pair)}
  <table class="cmp"><tr><th></th><th>${esc(sideName(a))}</th><th>${esc(sideName(b))}</th></tr>
  ${row("Project", p => `${esc(p.projectId)} · ${esc(p.name)}`)}${row("Type", p => esc(projectType(p)))}${row("Target in service", p => esc(targetDate(p)))}
  ${row("Status", p => esc(statusShort(p)))}
  ${row("Planning window", p => p.window?.start ? `${esc(p.window.start)} → ${esc(p.window.end)}` : "unknown")}${row("Cost", p => p.cost?.total ? money(p.cost.total) : p.state === "GA" ? "redacted" : "not published")}
  ${row("Location", p => `${esc(p.locationConfidence)}, ±${p.radiusMi} mi · ${esc(centerMethod(p))}`)}${row("Endpoints", eps)}${row("Source", src)}</table>
  <div class="cols"><div><h2>${pair.qualifies ? "Why it qualifies" : "Why it might qualify"}</h2><ul>${li(whyQualifies(pair))}</ul><h2>Possible shared resources</h2><ul>${li(sharedResources(pair))}</ul>
  <h2>Staging-yard scenario${sc.active ? `: ${money(sc.low)} – ${money(sc.high)}` : " unavailable"}</h2>${sc.active ? `<p>Illustrative avoided cost; sharing feasibility unconfirmed.</p><ul>${scenarioLines(pair, sc).map(([k, v, why]) => `<li><b>${k}:</b> ${esc(v)} <i>(${esc(why)})</i></li>`).join("")}<li>Combined yard assumed 1.0–1.5× one yard. Proximity does not prove land or equipment can be shared.</li></ul>` : `<p>${esc(sc.reason)}</p>`}</div>
  <div><h2>Still to confirm</h2><ul>${li(toConfirm(pair))}</ul><h2>Questions for ${esc(sideName(b))}</h2><ol>${li(questions(pair))}</ol></div></div>
  <h2>Ground near mapped endpoints</h2><div class="cols">${[a, b].map(p => `<div><b>${esc(sideName(p))} ${esc(p.projectId)}</b><ul>${li(groundLines(p, state.data.environmentRadiusMi))}</ul></div>`).join("")}</div>
  <p class="note">${esc(GROUND_NOTE)}</p>
  <section class="forum"><h2>Where to raise it</h2><p>${[a,b].map(p => `${esc(sideName(p))}: <a href="${esc(PLANNING_FORUMS[p.state].contactUrl)}">${esc(PLANNING_FORUMS[p.state].shortName)} contact page</a>`).join(" · ")}</p><p>${esc(PLANNING_TRANSITION.text)} Checked ${esc(PLANNING_TRANSITION.checkedOn)}. <a href="${esc(PLANNING_TRANSITION.sourceUrl)}">Transition notice</a></p></section>
  <footer>Cost basis: ${esc(YARD_BASIS.matsPerAcre.source)}; ${esc(YARD_BASIS.roadPerMile.source)}; ${esc(YARD_BASIS.landPerAcre.source)}. Built from public filings and OpenStreetMap only; no CEII. Locations are estimates with stated uncertainty.</footer>
</section>`;
}

function openBriefs(pairs) {
  const w = window.open("", "_blank");
  if (!w) { $("session-notice").textContent = "The brief window was blocked. Allow pop-ups for this site and try again."; return; }
  w.document.write(`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Gridlock brief${pairs.length > 1 ? "s" : ""}</title><style>
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
  .note { margin: 4px 0 0; color: #6b7d84; font-size: 9px; }
  a { color: #1b6472; }
  .brief-map { width: 100%; height: auto; display: block; margin: 8px 0; }
  .qualification-notice { font-weight: bold; background: #fff3dd; padding: 8px; }
  tr, .brief-map, h2 { break-inside: avoid; } h2 { break-after: avoid; }
  @media (max-width: 700px) { .cols { grid-template-columns: 1fr; } header { flex-direction: column; } .page { padding: 16px; min-height: 0; } }
  @media print {
    body { background: #fff; line-height: 1.25; }
    .bar { display: none; }
    .page { margin: 0; width: auto; min-height: 0; padding: 0; page-break-after: always; }
    header { padding-bottom: 6px; }
    h2 { margin: 7px 0 2px; }
    th, td { padding: 2px 5px; }
    .brief-map { max-height: 155px; margin: 6px 0; }
    .cols { gap: 16px; }
    .qualification-notice { margin: 6px 0; padding: 6px 8px; }
    .forum p { margin: 4px 0; }
    footer { margin-top: 8px; padding-top: 4px; line-height: 1.3; }
    @page { size: letter; margin: .4in; }
  }
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

  const sample = h("details", "dq-more");
  sample.append(h("summary", "", "Original sponsor sample: comparison with current filings"));
  const t0 = h("table", "dq-table"); t0.innerHTML = "<thead><tr><th>Sample ID</th><th>Project</th><th>Sample date</th><th>Status in the current filing</th></tr></thead>";
  const b0 = h("tbody"); d.starterStatus.forEach(s => { const tr = h("tr"); [s.id, s.name, s.starterDate, s.status].forEach((x, i) => tr.append(h("td", i === 2 ? "nowrap" : "", x ?? ""))); b0.append(tr); }); t0.append(b0); sample.append(t0);
  sample.append(h("p", "muted-note", "Both Augusta-area sample pairs are gone: DESC's Hooks–Thurmond rebuild is no longer listed and Georgia Power cancelled Evans Primary–Thurmond Dam #5 and #6 (Table 3). The McIntosh–Purrysburg reactors are complete (Table 4)."));
  const santee = h("p", "muted-note", "Santee Cooper, not DESC, owns the South Carolina side of that tie. Its reconductor of the Purrysburg–McIntosh 230 kV tie lines is row 1 of Santee Cooper's 2026–2030 list and is paired like any other project; its date conflict (5/1/2026 in the list, December 2026 on slide 51) is flagged on the project. Source: ");
  santee.append(link("https://www.scrtp.com/assets/pdfs/meeting-archives/scrtp-meeting-2026-03-11-presentation.pdf#page=50", "SCRTP stakeholder meeting, March 11, 2026"), ".");
  sample.append(santee);

  wrap.append(h("h2", "", "List-level problems"));
  const ul = h("ul", "issue-list"); d.dataQuality.forEach(msg => ul.append(h("li", "issue warn", msg))); wrap.append(ul);

  const table = (rows, caption) => {
    const t = h("table", "dq-table"); t.innerHTML = "<thead><tr><th>Level</th><th>Project</th><th>Issue</th><th>Source</th></tr></thead>";
    const body = h("tbody");
    rows.forEach(i => {
      const tr = h("tr", `lvl-${i.level}`);
      const nameCell = h("td"); const btn = h("button", "link-button", `${i.p.projectId} · ${i.p.name}`); btn.type = "button";
      btn.addEventListener("click", () => { Object.assign(state, FILTER_DEFAULTS); setView("explore"); selectProject(i.p); syncControls(); });
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
  wrap.append(sample);
  v.append(wrap);
}

function renderMethod() {
  const v = $("view-method"); v.replaceChildren();
  const d = state.data, bm = d.costBenchmark;
  const wrap = h("div", "doc");
  wrap.append(h("h1", "", "How Gridlock decides"));
  const sec = (title, ...paras) => { wrap.append(h("h2", "", title)); paras.forEach(p => wrap.append(typeof p === "string" ? h("p", "", p) : p)); };
  const srcList = h("ul", "src-list"); d.sources.forEach(s => { const li = h("li"); li.append(link(s.url, s.title), document.createTextNode(s.projects ? ` — ${s.projects} projects` : "")); srcList.append(li); });
  const sertp = h("p", "", "Checked September 27, 2026 for a newer source, as the brief asks: SERTP's ");
  sertp.append(link("https://www.southeasternrtp.com/docs/general/2026/2026_SERTP_Preliminary_Expansion_Plan_Report_(Non-CEII).pdf", "2026 preliminary expansion plan"), document.createTextNode(" (June 12, 426 projects) lists no DESC or Santee Cooper projects, and its "),
    link("https://www.southeasternrtp.com/docs/general/2026/2026_SERTP_3rd_Quarter_Meeting_Presentation.pdf", "September 22 meeting"), document.createTextNode(" still treats SCRTP as a separate region. The SCRTP lists used here are the newest for both South Carolina utilities."));
  sec("Sources", `Dataset as of ${formatDate(d.generated)}. The first three links are the current public filings; earlier editions support schedule history and the change log.`, srcList, "The challenge zip's DESC list (2024–2028) and Georgia plan (2025 IRP) are superseded; both newer editions are public and are used here. Santee Cooper, which publishes its list through SCRTP alongside DESC, is on the South Carolina side too: its list is a slide in the first SCRTP stakeholder meeting of each year.", sertp);
  sec("The qualifying rule (unchanged from the challenge)", `A South Carolina project (DESC or Santee Cooper) and a Georgia project form a pair when their centers are less than ${MAX_MILES} miles apart by great-circle (haversine) distance. A project's center is the midpoint of its located endpoints, or the single located endpoint. The time gap is the absolute difference between in-service dates. Our tests reproduce the sponsor's six example rows to the hundredth of a mile and the day.`);
  sec("What we add on top (ranking only — never changes which pairs qualify)",
    "Planning windows: DESC's first budget year with spend through its in-service date; Georgia's detail-page Start Date through Need Date. Santee Cooper publishes only in-service dates, so its projects have no window and are timed by the in-service gap alone. Overlap still ahead of the data date counts; overlap already in the past does not.",
    "Certainty: each location carries an uncertainty radius. A pair is robust if it stays under 25 miles at the edges of both radii, sensitive if it only does at the best estimate, and possible (shown only on request) if it could qualify.",
    `Score = proximity (${WEIGHTS.proximity}) + timing (${WEIGHTS.timing}), × 0.85 when location-sensitive — the challenge's own two signals, geography primary and timing a strong secondary one, filling the full 100. Distance sets most of the order; timing reorders pairs at similar distances. To explore alternatives, sort by distance, by closest in-service dates, or by build overlap still ahead.`);
  sec("How locations are found", "In order of trust: hand-sited points with a written reason (data/overrides.json); coordinates from the sponsor's starter workbook; OpenStreetMap substations and plants by exact then partial name, restricted to the right state; and last, a town, road or creek that carries the station's exact name (±6 mi), searched near whatever else locates the project. A project none of whose own stations can be placed, often because they are new, is placed among the existing stations its filing description names, each matched to exactly one OSM station in the planning zone; its radius covers all of them. When a name fits several places (there are two Goshens 87 miles apart), the one nearest the project's other endpoint and its planning zone wins. Matches far from the rest of the project are rejected rather than kept.");
  const basis = h("ul", "src-list"); [YARD_BASIS.matsPerAcre, YARD_BASIS.roadPerMile, YARD_BASIS.landPerAcre].forEach(b => { const li = h("li"); li.append(link(b.url, b.source)); basis.append(li); });
  sec("Impact scenario (bonus): one shared staging yard",
    "For a pair overlapping planning windows, we model what one shared staging/laydown yard would avoid compared with two separate yards. One yard = surface (acres × $/acre) + land lease (acres × land value × lease rate × months) + a short access road (miles × $/mile). A combined yard is assumed to be 1.0–1.5× the size of one project's yard, so sharing avoids 0.5–1.0 of a yard. With no build overlap after the data date, the saving is $0.",
    "Cited unit costs:", basis,
    "Yard size, months, lease rate, road length and any non-mat surface cost are assumptions, marked as such and editable in the panel. Proximity alone can't establish that land or equipment can be shared; the scenario is a reason to make the call, not a budget.",
    `For scale, the panel compares the result with the smaller project's cost. DESC publishes costs; Georgia's are redacted and Santee Cooper's list gives none, so for a line with a stated length we apply DESC's own median of ${money(bm.perMile)} per mile (${bm.n} line projects).`);
  sec("Changes between filings",
    "Every filing we read is listed in data/filings.json. Each new edition is compared with the one before it from the same utility: projects are matched on their ID (DESC reuses IDs, so a DESC match also needs a similar name; an ID kept under a different name is reported as renamed; Santee Cooper publishes no IDs, so its projects are matched on their titles and its “Row” numbers are only positions in the list), then we list what was added, dropped, rescheduled, renamed or re-costed. Georgia says why each project left its plan (Table 3 cancelled, Table 4 completed); the South Carolina lists don't, so a dropped project only says whether its date had already passed.",
    "Pairs are recomputed with the same 25-mile rule before and after each filing, keeping every project's current location, so a pair appears or disappears only because a project was added, dropped or rescheduled. A pair whose in-service gap moves by 30 days or more is listed as a timing change.");
  sec("Adding the next public filing", "Add its public URL, edition, date and parser information to data/filings.json, then run python3 pipeline/build.py. Review parser validation and any uncertain locations before publishing the rebuilt data/projects.json and data/changes.json. If a utility changes its document format, its parser may need an update. The Changes tab then shows additions and revisions against the prior edition. This is a reviewed data update, not an upload of arbitrary points.");
  const envList = h("ul", "src-list"); (d.environmentSources ?? []).forEach(s => { const li = h("li"); li.append(link(s.url, s.title)); envList.append(li); });
  sec("Ground near mapped endpoints (mapped conditions)",
    `Checked only where a location means something: endpoints placed at a station (not a town guess) and lines traced between two such stations, for projects that could appear in a pair. For each station we read what is mapped within ${d.environmentRadiusMi} mi: the FEMA flood zone at the station and the share of land in the 1% annual-chance flood area, the share mapped as wetland or open water, and any critical habitat or protected land. For each traced line we measure the miles inside those areas. Shares and miles come from sampling points about 24 m apart.`,
    envList,
    "Service answers are cached with the date they were read (data/cache/environment.json), so rebuilding offline gives the same results. On the map, the wetland and flood outlines behind each result are drawn inside the checked areas (dashed); zoomed in close, the agencies' own full maps are shown instead.",
    "These are mapped conditions, not a field survey, a wetland delineation or a permit decision. Coordinating two projects doesn't remove either one's permits. Nothing here changes which pairs qualify or how they score.");
  sec("What this does not use", "No CEII, no non-public data and no paid APIs. Georgia filings carry a CEII banner even in their public-disclosure versions; we use only what the Commission published, and we do not reconstruct redacted costs.");
  sec("Reproduce", Object.assign(h("pre", "code"), { textContent: "python3 pipeline/build.py        # fetch filings, parse, geocode, trace lines, read federal maps -> data/\npython3 pipeline/build.py --offline\nnode --test tests/*.test.js\npython3 -m unittest discover tests\npython3 -m http.server 8000" }));
  v.append(wrap);
}

function setView(name) {
  document.querySelectorAll(".tab").forEach(t => { t.classList.toggle("active", t.dataset.view === name); if (t.dataset.view === name) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current"); });
  document.querySelectorAll(".view").forEach(v => { v.hidden = v.id !== `view-${name}`; });
  window.scrollTo(0, 0);
  document.querySelectorAll(".doc-view").forEach(v => { v.scrollTop = 0; });
  $("results-export").hidden = name !== "explore";
  $("shortlist-export").hidden = name !== "explore";
  document.querySelectorAll(".action-menu[open]").forEach(menu => { menu.open = false; });
  if (name === "explore") map?.resize();
  if (name === "changes") changesView?.open();
}

function exportCsv() {
  const header = ["as_of", "rank", "qualifies", "score", "certainty", "sc_utility", "sc_project", "sc_id", "sc_type", "sc_status", "ga_utility", "ga_project", "ga_teams", "ga_type", "ga_status", "distance_miles", "sc_point_type", "ga_point_type", "in_service_gap_days", "build_overlap_days", "overlap_days_ahead", "in_service_sc", "in_service_ga", "ground_sc", "ground_ga", "source_sc", "source_ga"];
  const lines = [header, ...state.pairs.map((p, i) => [state.asOf, i + 1, p.qualifies, p.score.total, p.certainty, sideName(p.a), p.a.name, p.a.projectId, projectType(p.a), p.a.status ?? "", sideName(p.b), p.b.name, p.b.projectId, projectType(p.b), p.b.status ?? "", p.miles.toFixed(3), pointKind(p.a), pointKind(p.b), p.gapDays ?? "", p.overlapDays ?? "", p.remainingDays ?? "", p.a.inServiceDate ?? "", p.b.inServiceDate ?? "", groundShort(p.a), groundShort(p.b), pdfLink(p.a.source), pdfLink(p.b.source)])];
  const csv = lines.map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\r\n");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" })); a.download = "gridlock-opportunities.csv"; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

$("skip-details").addEventListener("click", revealDetails);

$("distance").addEventListener("input", event => { state.distance = Number(event.target.value); applyFilters(); });
$("year").addEventListener("input", event => { state.year = Number(event.target.value); applyFilters(); });
$("gap").addEventListener("change", event => { state.gap = event.target.value; applyFilters(); });
$("ground").addEventListener("change", event => { state.ground = event.target.value; applyFilters(); });
$("sc-utility").addEventListener("change", event => { state.scUtility = event.target.value; applyFilters(); });
function openLayersPanel() { $("layers-panel").hidden = false; $("layers-toggle").setAttribute("aria-expanded", "true"); }
$("layers-toggle").addEventListener("click", () => { const open = $("layers-panel").hidden; $("layers-panel").hidden = !open; $("layers-toggle").setAttribute("aria-expanded", String(open)); });
$("satellite-toggle").addEventListener("click", () => { satelliteOn = !satelliteOn; saveSatellite(); syncSatellite(); });
syncSatellite();
document.querySelectorAll("#layers-panel input[data-layer]").forEach(input => input.addEventListener("change", () => {
  if (input.checked) envOn.add(input.dataset.layer); else envOn.delete(input.dataset.layer);
  saveLayers(); syncEnvLayers();
}));
$("hide-past").addEventListener("change", event => { state.hidePast = event.target.checked; applyFilters(); });
$("possible").addEventListener("change", event => { state.includePossible = event.target.checked; applyFilters(); });
$("search").addEventListener("input", event => { state.search = event.target.value.trim().toLowerCase(); state.selectedProject = null; applyFilters(); });
$("reset-filters").addEventListener("click", () => { Object.assign(state, FILTER_DEFAULTS); syncControls(); });
$("clear-selection").addEventListener("click", () => { state.selectedProject = null; state.selectedPair = null; applyFilters(); });
$("sort").addEventListener("change", event => { state.sort = event.target.value; applyFilters(); });
$("shortlist-only").addEventListener("change", event => { state.shortlistOnly = event.target.checked; applyFilters(); });
$("shortlist-export").addEventListener("click", () => { const pairs = state.allPairs.filter(p => state.shortlist.has(p.id)); if (pairs.length) openBriefs(pairs); });
$("export").addEventListener("click", () => { exportCsv(); $("results-export").open = false; });
document.addEventListener("click", event => {
  document.querySelectorAll(".action-menu[open]").forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
});
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  document.querySelectorAll(".action-menu[open]").forEach(menu => { menu.open = false; menu.querySelector("summary").focus(); });
});
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
  state.sameStations = sameStationProjects(state.projects);
  state.asOf = state.data.generated;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(state.asOf ?? "")) throw new Error("Dataset is missing a valid as-of date");
  $("data-as-of").textContent = `Data as of ${formatDate(state.asOf)}`;
  state.allPairs = matchProjects(state.projects, MAX_MILES, { includePossible: true, asOf: state.asOf });
  const years = state.projects.map(project => Number(project.inServiceDate?.slice(0, 4))).filter(Number.isFinite);
  $("year").min = String(Math.min(...years)); $("year").max = String(Math.max(...years)); state.year = FILTER_DEFAULTS.year = Math.max(...years); $("year").value = String(state.year);
  const saved = loadShortlist();
  state.shortlist = new Set([...saved].filter(id => state.allPairs.some(p => p.id === id)));
  if (saved.size !== state.shortlist.size) { saveShortlist(); $("session-notice").textContent = "Some saved pairs changed or were ambiguous in this data edition and were removed. Please review your shortlist."; }
  for (const [key, s] of Object.entries(SORTS)) { const o = h("option", "", s.label); o.value = key; $("sort").append(o); }
  $("sort-hint").textContent = `Score: distance ${WEIGHTS.proximity} pts + timing ${WEIGHTS.timing} pts. Distance sets most of the order.`;
  initMap();
  syncEnvLayers();
  changesView = createChangesView({
    h, link, formatDate, money, colors: COLORS, basemap: BASEMAP, fallbackStyle: FALLBACK_STYLE,
    pairExists: id => state.allPairs.some(p => p.id === id && p.qualifies),
    openProject: id => { state.selectedProject = id; state.selectedPair = null; setView("explore"); applyFilters(); },
    openPair: id => {
      const pair = state.allPairs.find(p => p.id === id);
      if (!pair) return;
      setView("explore");
      if (!state.pairs.includes(pair)) { Object.assign(state, FILTER_DEFAULTS); state.selectedProject = null; state.hidePast = !pair.bothPast; syncControls(); }
      selectPair(pair);
    },
  });
  new ResizeObserver(() => requestAnimationFrame(() => map?.resize())).observe($("map"));
  renderQuality(); renderMethod();
  restoreViewUrl(); syncControls();
  if (state.selectedPair) { focusPair(state.allPairs.find(p => p.id === state.selectedPair)); revealDetails(); scrollToPairRow(state.selectedPair); }
  else if (state.selectedProject) revealDetails();
  window.addEventListener("hashchange", () => { restoreViewUrl(); syncControls(); if (state.selectedPair || state.selectedProject) revealDetails(); if (state.selectedPair) scrollToPairRow(state.selectedPair); });
} catch (error) { $("match-list").textContent = `Could not load project data: ${error.message}. Run the local server described in README.md.`; console.error(error); }
