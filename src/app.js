import { matchProjects, gapLabel, overlapLabel, opportunityText, savingsEstimate, MAX_MILES } from "./match.js";

const $ = id => document.getElementById(id);
const svgNS = "http://www.w3.org/2000/svg";
const state = {
  data: null, projects: [], allPairs: [], pairs: [], selectedProject: null, selectedPair: null, search: "", distance: 25, year: 2035,
  gap: "all", hidePast: true, includePossible: false, shareRate: 0.04, asOf: null, view: { x: 0, y: 0, w: 900, h: 650 },
};
const bounds = { west: -85.8, east: -78.4, south: 30.3, north: 35.3 };
const border = { west: -82.7, east: -80.6, south: 31.85, north: 33.95 };
const tileZoom = 8;
const tileSize = 256;
const worldSize = tileSize * 2 ** tileZoom;

function worldPoint(point) {
  const latitude = Math.max(-85.0511, Math.min(85.0511, point.lat)) * Math.PI / 180;
  return {
    x: (point.lon + 180) / 360 * worldSize,
    y: (1 - Math.log(Math.tan(latitude) + 1 / Math.cos(latitude)) / Math.PI) / 2 * worldSize,
  };
}
const northwest = worldPoint({ lat: bounds.north, lon: bounds.west });
const southeast = worldPoint({ lat: bounds.south, lon: bounds.east });
const scaleX = 900 / (southeast.x - northwest.x);
const scaleY = 650 / (southeast.y - northwest.y);
const loadedTiles = new Set();

function viewFor(box) {
  const rect = $("map").getBoundingClientRect();
  const ratio = rect.width / Math.max(rect.height, 1);
  const a = position({ lat: box.north, lon: box.west }), b = position({ lat: box.south, lon: box.east });
  let w = b.x - a.x, h = b.y - a.y;
  if (w / h < ratio) w = h * ratio; else h = w / ratio;
  return { x: (a.x + b.x) / 2 - w / 2, y: (a.y + b.y) / 2 - h / 2, w, h };
}
const fittedView = () => viewFor(border);

// Pick the OSM zoom level whose tiles land near 256 screen pixels, and redraw when it changes.
let tileLevel = null;
function renderTiles() {
  const tiles = $("map").querySelector(".basemap");
  if (!tiles) return;
  const view = state.view;
  const pxPerUnit = Math.max($("map").getBoundingClientRect().width, 1) / view.w;
  const z = Math.max(5, Math.min(13, Math.round(tileZoom + Math.log2(pxPerUnit * scaleX))));   // screen px per level-8 world px
  if (z !== tileLevel) { tiles.replaceChildren(); loadedTiles.clear(); tileLevel = z; }
  const f = 2 ** (z - tileZoom);            // tiles at level z are this many times denser than level 8
  const size = tileSize / f;                // one level-z tile in level-8 world pixels
  const minX = Math.floor((northwest.x + view.x / scaleX) / size);
  const maxX = Math.floor((northwest.x + (view.x + view.w) / scaleX) / size);
  const minY = Math.floor((northwest.y + view.y / scaleY) / size);
  const maxY = Math.floor((northwest.y + (view.y + view.h) / scaleY) / size);
  if ((maxX - minX + 1) * (maxY - minY + 1) > 400) return;
  for (let x = minX; x <= maxX; x++) {
    for (let y = minY; y <= maxY; y++) {
      const key = `${z}/${x}/${y}`;
      if (loadedTiles.has(key)) continue;
      loadedTiles.add(key);
      tiles.append(el("image", {
        x: (x * size - northwest.x) * scaleX, y: (y * size - northwest.y) * scaleY,
        width: size * scaleX + 0.02 * size * scaleX, height: size * scaleY + 0.02 * size * scaleY,
        href: `https://tile.openstreetmap.org/${z}/${x}/${y}.png`, class: "basemap-tile", preserveAspectRatio: "none",
      }));
    }
  }
}

function el(tag, attrs = {}, text = "") {
  const node = document.createElementNS(svgNS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text) node.textContent = text;
  return node;
}
function h(tag, className = "", text = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}
function link(href, text) { const a = h("a", "", text); a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer"; return a; }
function position(point) {
  const world = worldPoint(point);
  return { x: (world.x - northwest.x) / (southeast.x - northwest.x) * 900, y: (world.y - northwest.y) / (southeast.y - northwest.y) * 650 };
}
const side = project => project.state === "SC" ? "desc" : "gpc";
const sideName = project => project.state === "SC" ? "DESC" : project.utility === "SAV" ? "GPC · Savannah" : project.utility;
function projectLabel(project) { return `${project.projectId} · ${project.name}`; }
function formatDate(value) { return value ? new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`)) : "Unknown"; }
const money = n => n == null ? "—" : n >= 1e6 ? `$${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M` : `$${Math.round(n / 1000)}k`;
function datePassed(value) { return Boolean(value && value < state.asOf); }
function pdfLink(source) { return `${source.url}#page=${source.page}`; }
function includesSearch(pair) {
  const q = state.search;
  return !q || [pair.a, pair.b].some(p => `${p.id} ${p.projectId} ${p.name} ${p.utility} ${p.endpoints.map(e => e.name).join(" ")}`.toLowerCase().includes(q));
}
function bySelectedYear(project) { return !project.inServiceDate || Number(project.inServiceDate.slice(0, 4)) <= state.year; }
const certaintyText = {
  robust: "Inside 25 miles even at the edges of both location estimates.",
  sensitive: "Inside 25 miles at the best-estimate locations, but location uncertainty could push it past 25.",
  possible: "Outside 25 miles at the best-estimate locations, but within reach of the location uncertainty. Not a qualifying pair.",
};

function applyFilters() {
  if (state.selectedProject && !bySelectedYear(state.projects.find(project => project.id === state.selectedProject))) state.selectedProject = null;
  state.pairs = state.allPairs.filter(pair =>
    (state.includePossible || pair.qualifies) && bySelectedYear(pair.a) && bySelectedYear(pair.b) && pair.miles < state.distance + (pair.qualifies ? 0 : 99) &&
    (!state.hidePast || !pair.bothPast) &&
    (state.gap === "all" || (state.gap === "ahead" ? pair.remainingDays > 0 : state.gap === "overlap" ? pair.overlapDays > 0 : pair.gapDays !== null && pair.gapDays <= Number(state.gap))) &&
    includesSearch(pair) && (!state.selectedProject || pair.a.id === state.selectedProject || pair.b.id === state.selectedProject));
  if (state.selectedPair && !state.pairs.some(p => p.id === state.selectedPair)) state.selectedPair = null;
  $("distance-value").textContent = `${state.distance} mi`;
  $("year-value").textContent = String(state.year);
  const q = state.pairs.filter(p => p.qualifies).length;
  $("result-count").textContent = `${q} qualifying ${q === 1 ? "pair" : "pairs"}${state.pairs.length > q ? ` + ${state.pairs.length - q} possible` : ""}`;
  $("queue-title").textContent = state.selectedProject ? `Pairs for ${state.projects.find(p => p.id === state.selectedProject)?.projectId}` : "Ranked opportunities";
  renderStats(); renderList(); renderMap(); renderDetail();
}

function renderStats() {
  const located = state.projects.filter(p => p.center).length;
  const qualifying = state.allPairs.filter(p => p.qualifies);
  const cards = [[String(state.projects.length), "projects parsed"], [`${located}`, "located on the map"], [String(qualifying.length), "qualifying pairs"],
    [String(qualifying.filter(p => p.remainingDays > 0).length), "still building together"]];
  $("stats").replaceChildren(...cards.map(([value, label]) => { const card = h("div", "stat"); card.append(h("strong", "", value), h("span", "", label)); return card; }));
}

function badge(text, kind = "") { return h("span", `badge ${kind}`, text); }

function renderList() {
  const list = $("match-list"); list.replaceChildren();
  if (!state.pairs.length) { const empty = h("div", "empty-state"); empty.append(h("strong", "", "No pairs match these filters"), h("p", "", "Widen the distance or timing filter, show past pairs, or select a different project on the map.")); list.append(empty); return; }
  state.pairs.forEach((pair, index) => {
    const button = h("button", `match-card${pair.id === state.selectedPair ? " active" : ""}${pair.qualifies ? "" : " possible"}`);
    button.type = "button"; button.setAttribute("aria-label", `View match ${index + 1}: ${pair.a.name} and ${pair.b.name}`);
    const top = h("div", "card-top"); top.append(h("span", "rank", pair.qualifies ? `#${String(index + 1).padStart(2, "0")} · SCORE ${pair.score.total}` : "POSSIBLE · NOT QUALIFYING"), h("strong", "distance-pill", `${pair.miles.toFixed(1)} mi`));
    button.append(top, h("strong", "pair-title", `${pair.a.name} × ${pair.b.name}`));
    const tags = h("div", "badges");
    if (pair.shared.length) tags.append(badge(`Shared station: ${pair.shared[0].a}`, "good"));
    if (pair.remainingDays > 0) tags.append(badge("Building at the same time", "good"));
    else if (pair.overlapDays > 0) tags.append(badge("Overlap already past", "muted"));
    if (pair.certainty === "sensitive") tags.append(badge("Location-sensitive", "warn"));
    if (pair.bothPast) tags.append(badge("Both past in-service date", "muted"));
    if (tags.childElementCount) button.append(tags);
    const meta = h("div", "card-meta"); meta.append(h("span", "", `${sideName(pair.a)} ↔ ${sideName(pair.b)}`), h("span", "", gapLabel(pair.gapDays))); button.append(meta);
    button.addEventListener("click", () => { state.selectedPair = pair.id; applyFilters(); focusPair(pair); });
    list.append(button);
  });
}

function mapBase(svg) {
  svg.append(el("rect", { x: -2000, y: -2000, width: 5000, height: 5000, fill: "#e5ecec" }));
  const fallback = el("g", { class: "map-grid" });
  for (let lon = -85; lon <= -79; lon++) {
    const p = position({ lat: 32, lon });
    fallback.append(el("line", { x1: p.x, y1: -2000, x2: p.x, y2: 3000, class: "grid-line" }), el("text", { x: p.x + 5, y: 18, class: "grid-label" }, `${Math.abs(lon)}°W`));
  }
  for (let lat = 31; lat <= 35; lat++) {
    const p = position({ lat, lon: -85 });
    fallback.append(el("line", { x1: -2000, y1: p.y, x2: 3000, y2: p.y, class: "grid-line" }), el("text", { x: 8, y: p.y - 7, class: "grid-label" }, `${lat}°N`));
  }
  svg.append(fallback);
  svg.append(el("g", { class: "basemap" }), el("rect", { x: -2000, y: -2000, width: 5000, height: 5000, class: "basemap-shade" }), el("g", { id: "map-overlay" }));
  renderTiles();
}

function sizeMarkers() {
  const svg = $("map");
  const px = state.view.w / Math.max(svg.getBoundingClientRect().width, 1);
  svg.querySelectorAll(".marker-core").forEach(marker => marker.setAttribute("r", String((marker.classList.contains("small") ? 3.5 : 6) * px)));
  svg.querySelectorAll(".marker-halo").forEach(marker => marker.setAttribute("r", String(14 * px)));
  svg.querySelectorAll(".endpoint").forEach(dot => { const [cx, cy] = [Number(dot.dataset.x), Number(dot.dataset.y)]; dot.setAttribute("x", cx - 3 * px); dot.setAttribute("y", cy - 3 * px); dot.setAttribute("width", 6 * px); dot.setAttribute("height", 6 * px); });
}

function milesToUnits(point, miles) {
  const a = position(point), b = position({ lat: point.lat + miles / 69.0, lon: point.lon });
  return Math.abs(a.y - b.y);
}

function drawProjectGeometry(overlay, project) {
  if (project.center && project.radiusMi) {
    const p = position(project.center);
    overlay.append(el("circle", { cx: p.x, cy: p.y, r: milesToUnits(project.center, project.radiusMi), class: `uncertainty ${side(project)}` }));
  }
  if (project.route) {
    overlay.append(el("polyline", { points: project.route.coords.map(([lat, lon]) => { const q = position({ lat, lon }); return `${q.x},${q.y}`; }).join(" "), class: `route ${side(project)}` }));
  }
  for (const e of project.endpoints) {
    if (!e.point) continue;
    const q = position(e.point);
    const dot = el("rect", { x: q.x - 2, y: q.y - 2, width: 4, height: 4, "data-x": q.x, "data-y": q.y, class: `endpoint ${side(project)}` });
    dot.append(el("title", {}, `${e.name} · ${e.method} · ${e.confidence}`));
    overlay.append(dot);
  }
}

function renderMap() {
  const svg = $("map");
  if (!svg.querySelector("#map-overlay")) mapBase(svg);
  const overlay = svg.querySelector("#map-overlay");
  overlay.replaceChildren();
  const pair = state.allPairs.find(p => p.id === state.selectedPair);
  const project = state.projects.find(p => p.id === state.selectedProject);
  if (pair) { drawProjectGeometry(overlay, pair.a); drawProjectGeometry(overlay, pair.b); }
  if (project) drawProjectGeometry(overlay, project);
  const activeIds = new Set(state.pairs.flatMap(p => [p.a.id, p.b.id]));
  state.pairs.forEach(p => {
    const a = position(p.a.center), b = position(p.b.center);
    const line = el("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: `pair-link${p.id === state.selectedPair ? " selected" : state.selectedPair ? " dim" : ""}${p.qualifies ? "" : " possible"}` });
    line.append(el("title", {}, `${p.a.projectId} ↔ ${p.b.projectId}: ${p.miles.toFixed(1)} miles`));
    line.addEventListener("click", event => { event.stopPropagation(); state.selectedPair = p.id; applyFilters(); });
    overlay.append(line);
  });
  const visibleProjects = state.projects.filter(p => p.center && bySelectedYear(p));
  visibleProjects.sort((x, y) => activeIds.has(x.id) - activeIds.has(y.id)).forEach(p => {
    const q = position(p.center);
    const matched = activeIds.has(p.id);
    const group = el("g", { class: `project-marker ${side(p)}${matched ? " matched" : ""}${p.id === state.selectedProject ? " chosen" : ""}`, tabindex: "0", role: "button", "aria-label": `View ${projectLabel(p)}` });
    group.append(el("circle", { cx: q.x, cy: q.y, r: 13, class: "marker-halo" }), el("circle", { cx: q.x, cy: q.y, r: 6.5, class: `marker-core${matched ? "" : " small"}` }), el("title", {}, `${projectLabel(p)}\n${sideName(p)} · in service ${formatDate(p.inServiceDate)}`));
    const select = event => { event.stopPropagation(); state.selectedProject = p.id; state.selectedPair = null; applyFilters(); };
    group.addEventListener("click", select);
    group.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") select(event); });
    overlay.append(group);
  });
  svg.setAttribute("viewBox", `${state.view.x} ${state.view.y} ${state.view.w} ${state.view.h}`);
  sizeMarkers();
  $("desc-count").textContent = String(visibleProjects.filter(p => p.state === "SC").length);
  $("gpc-count").textContent = String(visibleProjects.filter(p => p.state === "GA").length);
}

function focusPair(pair) {
  const lats = [pair.a.center.lat, pair.b.center.lat], lons = [pair.a.center.lon, pair.b.center.lon];
  const pad = 0.12 + Math.max(pair.a.radiusMi ?? 0, pair.b.radiusMi ?? 0) / 69;
  state.view = viewFor({ north: Math.max(...lats) + pad, south: Math.min(...lats) - pad, west: Math.min(...lons) - pad, east: Math.max(...lons) + pad });
  renderMap(); renderTiles();
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

function projectBlock(p, full = false) {
  const block = h("div", "project-block");
  block.append(h("span", `utility-label ${side(p)}`, `${sideName(p)} · ${p.state}${p.zoneName ? ` · ${p.zoneName}` : ""}`), h("h3", "", p.name));
  block.append(infoRow("Project ID", p.projectId), infoRow("In service", `${formatDate(p.inServiceDate)}${p.issues.some(i => /date/.test(i.msg)) ? ` (printed: ${p.inServiceRaw})` : ""}`));
  if (p.window?.start) block.append(infoRow("Build window", `${formatDate(p.window.start)} → ${formatDate(p.window.end)}`));
  block.append(infoRow("Cost", p.cost?.total ? `${money(p.cost.total)} (DESC estimate)` : p.state === "GA" ? "Redacted in the public filing" : "—"));
  if (p.miles || p.route) block.append(infoRow("Length", `${p.miles ? `${p.miles} mi stated` : "not stated"}${p.route ? ` · ${p.route.miles} mi traced on OSM` : ""}`));
  if (p.slipDays) block.append(infoRow("Schedule history", `${p.slipDays > 0 ? "slipped" : "advanced"} ${Math.abs(Math.round(p.slipDays / 30.44))} months since ${p.history[0].edition}`));
  else if (p.change && p.state === "GA") block.append(infoRow("Change vs last plan", p.change));
  block.append(infoRow("Location", `${p.locationConfidence} confidence · ±${p.radiusMi ?? "?"} mi`));
  const src = h("div", "source-line"); src.append(document.createTextNode("Source: "), link(pdfLink(p.source), `${p.source.doc}, p. ${p.source.page}`), document.createTextNode(` (${p.source.item})`));
  block.append(src);
  if (full) {
    if (p.description) block.append(h("p", "desc-text", p.description));
    const endpoints = h("div", "endpoint-box"); endpoints.append(h("strong", "", "Endpoints and how each was located"));
    for (const e of p.endpoints) {
      const row = h("p", "", `${e.name}: ${e.point ? `${e.point.lat.toFixed(4)}, ${e.point.lon.toFixed(4)} · ${e.method} · ${e.confidence} (±${e.radiusMi} mi)` : "not located"}`);
      if (e.evidence) row.append(h("span", "evidence", e.evidence));
      endpoints.append(row);
    }
    if (p.locationNote) endpoints.append(h("p", "evidence", p.locationNote));
    block.append(endpoints, h("strong", "mini-head", "Validation"), issuesList(p));
  }
  return block;
}

function costCard(pair) {
  const card = h("div", "cost-card");
  const bm = state.data.costBenchmark;
  const est = savingsEstimate(pair, { benchmarkPerMile: bm.perMile, shareRate: state.shareRate });
  card.append(h("strong", "", "Coordination value (illustrative)"));
  if (!est) {
    card.append(h("p", "", "No cost basis: Georgia costs are redacted and this project states no length to apply the benchmark to. The pair still has scheduling value."));
    return card;
  }
  const range = est.high > 0 ? `${money(est.low)} – ${money(est.high)}` : "$0";
  card.append(h("div", "cost-big", range));
  const basis = [
    `${sideName(pair.a)}: ${money(est.costA)}${est.aEstimated ? " (estimated)" : " (published)"}`,
    `${sideName(pair.b)}: ${money(est.costB)}${est.bEstimated ? ` (estimated: ${pair.b.miles} mi × ${money(bm.perMile)}/mi DESC benchmark)` : " (published)"}`,
    `Shareable share of the smaller project: ${(state.shareRate * 100).toFixed(0)}% ± half (mobilization, access mats, laydown yard, traffic control)`,
    `Timing factor ${est.timing}${est.timing === 1 ? " (building at the same time)" : est.timing === 0.5 ? " (within a year)" : " (no overlap: nothing shared)"}${est.site > 1 ? " · shared-station factor 1.5" : ""}`,
  ];
  const ul = h("ul", "cost-basis"); basis.forEach(b => ul.append(h("li", "", b))); card.append(ul);
  const slider = h("label", "cost-slider"); slider.append(document.createTextNode("Shareable share "));
  const input = h("input"); input.type = "range"; input.min = "1"; input.max = "10"; input.value = String(Math.round(state.shareRate * 100));
  input.addEventListener("input", () => { state.shareRate = Number(input.value) / 100; renderDetail(); });
  slider.append(input); card.append(slider);
  card.append(h("p", "muted-note", `Benchmark: median of ${bm.n} DESC line projects in the 2026–2030 list with a stated length and a cost row that adds up (middle half ${money(bm.low)}–${money(bm.high)} per mile). An order-of-magnitude aid for a first call, not an estimate either utility has made.`));
  return card;
}

function briefText(pair) {
  const lines = [
    `GridLock coordination brief · ${state.asOf}`, "",
    `${pair.a.name} (${sideName(pair.a)} ${pair.a.projectId})`, `  × ${pair.b.name} (${sideName(pair.b)} ${pair.b.projectId})`, "",
    `Why: ${opportunityText(pair)}`,
    `Distance: ${pair.miles.toFixed(2)} mi center to center (${pair.certainty}: ${certaintyText[pair.certainty]})`,
    pair.approachMiles !== null ? `Closest approach of the two lines/sites: ${pair.approachMiles.toFixed(1)} mi` : "",
    pair.shared.length ? `Shared station: ${pair.shared.map(s => s.a).join(", ")}` : "",
    `Timing: ${overlapLabel(pair.overlapDays)}; in-service dates ${gapLabel(pair.gapDays)} (${pair.a.inServiceDate} vs ${pair.b.inServiceDate})`,
    `Score: ${pair.score.total}/100 (proximity ${pair.score.parts.proximity}, timing ${pair.score.parts.timing}, shared station ${pair.score.parts.shared}, corridor ${pair.score.parts.corridor}, confidence ×${pair.score.parts.confidence})`, "",
    "Questions for the first call:",
    "  1. Are both build windows still current? Which months need outages?",
    "  2. Could one laydown yard, access-mat route or crane mobilization serve both jobs?",
    pair.shared.length ? "  3. Can the work at the shared station be done under one outage?" : "  3. Are crews or contractors already shared across the river?",
    "", "Sources:",
    `  ${pair.a.source.doc}, p. ${pair.a.source.page}: ${pdfLink(pair.a.source)}`,
    `  ${pair.b.source.doc}, p. ${pair.b.source.page}: ${pdfLink(pair.b.source)}`,
    "Built from public filings and OpenStreetMap only; no CEII. Locations are estimates (see uncertainty radius).",
  ];
  return lines.filter(l => l !== "").join("\n");
}

function renderDetail() {
  const detail = $("detail"); detail.replaceChildren();
  const pair = state.allPairs.find(p => p.id === state.selectedPair);
  const project = state.projects.find(p => p.id === state.selectedProject);
  if (pair) {
    detail.append(h("p", "eyebrow", pair.qualifies ? "Opportunity detail" : "Possible pair — does not qualify"), h("h2", "", "Why this pair?"), h("p", "detail-lead", opportunityText(pair)));
    const score = h("div", "score-block"); score.append(h("strong", "", `${pair.score.total} / 100`), h("span", "", "Ranking score"));
    const parts = h("div", "score-parts");
    [["Proximity", pair.score.parts.proximity, 35], ["Timing", pair.score.parts.timing, 35], ["Shared station", pair.score.parts.shared, 20], ["Line proximity", pair.score.parts.corridor, 10]].forEach(([k, v, max]) => {
      const row = h("div", "score-part"); const bar = h("i", ""); bar.style.width = `${(v / max) * 100}%`; const track = h("span", "score-track"); track.append(bar);
      row.append(h("span", "", k), track, h("b", "", `${v}/${max}`)); parts.append(row);
    });
    if (pair.score.parts.confidence < 1) parts.append(h("p", "muted-note", "× 0.85 because location uncertainty could move this pair past 25 miles."));
    score.append(parts); detail.append(score);
    const dist = h("div", "timing-callout"); dist.append(h("strong", "", `${pair.miles.toFixed(2)} mi center to center · ${pair.certainty}`), h("span", "", certaintyText[pair.certainty]));
    if (pair.approachMiles !== null) dist.append(h("span", "", `Closest approach of the two ${pair.a.route && pair.b.route ? "traced lines" : "lines/sites"}: ${pair.approachMiles.toFixed(1)} mi.`));
    if (pair.shared.length) dist.append(h("span", "", `Shared station: ${pair.shared.map(s => `${s.a} / ${s.b}`).join(", ")}.`));
    detail.append(dist);
    const timing = h("div", "timing-callout"); timing.append(h("strong", "", overlapLabel(pair.overlapDays)), h("span", "", `In-service dates ${gapLabel(pair.gapDays)}.${pair.overlapDays > 0 && !(pair.remainingDays > 0) ? " The overlap is already in the past." : pair.remainingDays > 0 ? ` ${Math.round(pair.remainingDays / 30.44)} months of overlap are still ahead.` : ""}`));
    detail.append(timing, timeline(pair.a, pair.b), costCard(pair));
    const actions = h("div", "actions");
    const brief = h("button", "ghost-button", "Open coordination brief"); brief.type = "button"; brief.addEventListener("click", () => openBrief(pair));
    const copy = h("button", "ghost-button", "Copy brief"); copy.type = "button"; copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(briefText(pair)); copy.textContent = "Copied"; } catch { copy.textContent = "Copy failed"; } });
    actions.append(brief, copy); detail.append(actions);
    for (const p of [pair.a, pair.b]) detail.append(projectBlock(p, true));
  } else if (project) {
    detail.append(h("p", "eyebrow", "Selected project"), projectBlock(project, true));
    const count = state.pairs.length;
    detail.append(h("p", "detail-lead", project.center ? `${count} ${count === 1 ? "pair" : "pairs"} with the other state under the current filters.` : "This project could not be located, so it can't be matched. See the Data quality tab."));
  } else {
    detail.append(h("p", "eyebrow", "SELECTION"), h("h2", "", "Select a project or pair"), h("p", "detail-lead", "Pick a ranked pair to see why it scored, both build windows against today, the source pages, and a first-call brief. Pick a dot to see a single project and how it was located."));
    const steps = h("div", "guide-steps"); [["01", "Find", "Pairs under 25 miles, ranked by what makes coordination worth a call."], ["02", "Check", "Every number links to the filing page it came from; every location states its confidence."], ["03", "Call", "Open the brief: it lists the overlap, the shared assets and the questions to ask."]].forEach(([n, title, body]) => { const item = h("div", "guide-step"); item.append(h("span", "", n), h("div", "", "")); item.lastChild.append(h("strong", "", title), h("p", "", body)); steps.append(item); });
    detail.append(steps);
  }
}

function openBrief(pair) {
  const w = window.open("", "_blank");
  if (!w) return;
  w.document.title = "GridLock brief";
  const pre = w.document.createElement("pre");
  pre.textContent = briefText(pair);
  pre.style.cssText = "font: 13px/1.5 ui-monospace, Menlo, monospace; white-space: pre-wrap; max-width: 820px; margin: 32px auto; padding: 0 16px;";
  w.document.body.append(pre);
}

function renderQuality() {
  const v = $("view-quality"); v.replaceChildren();
  const d = state.data;
  const wrap = h("div", "doc");
  wrap.append(h("p", "eyebrow", "DATA QUALITY"), h("h1", "", "What the filings get wrong, and what we couldn't place"),
    h("p", "doc-lead", "Every record is validated as it is parsed. Nothing here was silently fixed: impossible dates are read as the month's last day and flagged, cost rows that don't add up keep the printed total and are flagged."));
  const all = state.projects.flatMap(p => p.issues.map(i => ({ ...i, p })));
  const counts = { error: 0, warn: 0, info: 0 }; all.forEach(i => counts[i.level]++);
  const sum = h("div", "stat-row"); [[counts.error, "errors"], [counts.warn, "warnings"], [counts.info, "notes"], [state.projects.filter(p => !p.center).length, "projects not located"]].forEach(([n, l]) => { const c = h("div", "stat"); c.append(h("strong", "", String(n)), h("span", "", l)); sum.append(c); });
  wrap.append(sum);

  wrap.append(h("h2", "", "The sponsor's sample, checked against today's filings"));
  const t0 = h("table", "dq-table"); t0.innerHTML = "<thead><tr><th>Sample ID</th><th>Project</th><th>Sample date</th><th>Status in the current filing</th></tr></thead>";
  const b0 = h("tbody"); d.starterStatus.forEach(s => { const tr = h("tr"); [s.id, s.name, s.starterDate, s.status].forEach(x => tr.append(h("td", "", x ?? ""))); b0.append(tr); }); t0.append(b0); wrap.append(t0);
  wrap.append(h("p", "muted-note", "Both Augusta-area sample pairs are gone: DESC's Hooks–Thurmond rebuild is no longer listed and Georgia Power cancelled Evans Primary–Thurmond Dam #5 and #6 (Table 3). The McIntosh–Purrysburg reactors are complete (Table 4)."));

  wrap.append(h("h2", "", "List-level problems"));
  const ul = h("ul", "issue-list"); d.dataQuality.forEach(msg => ul.append(h("li", "issue warn", msg))); wrap.append(ul);

  const table = (rows, caption) => {
    const t = h("table", "dq-table"); t.innerHTML = "<thead><tr><th>Level</th><th>Project</th><th>Issue</th><th>Source</th></tr></thead>";
    const body = h("tbody");
    rows.forEach(i => {
      const tr = h("tr", `lvl-${i.level}`);
      const nameCell = h("td"); const btn = h("button", "text-button", `${i.p.projectId} · ${i.p.name}`); btn.type = "button";
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
  wrap.append(h("p", "eyebrow", "METHOD & SOURCES"), h("h1", "", "How GridLock decides"));
  const sec = (title, ...paras) => { wrap.append(h("h2", "", title)); paras.forEach(p => wrap.append(typeof p === "string" ? h("p", "", p) : p)); };
  const srcList = h("ul", "src-list"); d.sources.forEach(s => { const li = h("li"); li.append(link(s.url, s.title), document.createTextNode(s.projects ? ` — ${s.projects} projects` : "")); srcList.append(li); });
  sec("Sources", srcList, "The challenge zip's DESC list (2024–2028) and Georgia plan (2025 IRP) are superseded; both newer editions are public and are used here. The older DESC lists are kept only to measure schedule slip.");
  sec("The qualifying rule (unchanged from the challenge)", `A DESC project and a Georgia project form a pair when their centers are less than ${MAX_MILES} miles apart by great-circle (haversine) distance. A project's center is the midpoint of its located endpoints, or the single located endpoint. The time gap is the absolute difference between in-service dates. Our tests reproduce the sponsor's six example rows to the hundredth of a mile and the day.`);
  sec("What we add on top (ranking only — never changes which pairs qualify)",
    "Build windows: DESC's first budget year with spend through its in-service date; Georgia's detail-page Start Date through Need Date. Overlap still ahead of today counts; overlap already in the past does not.",
    "Certainty: each location carries an uncertainty radius. A pair is robust if it stays under 25 miles at the edges of both radii, sensitive if it only does at the best estimate, and possible (shown only on request) if it could qualify.",
    "Shared station: endpoints within half a mile. Line proximity: closest approach of lines traced along OpenStreetMap power lines, where both ends could be placed.",
    "Score = proximity (35) + timing (35) + shared station (20) + line proximity (10), × 0.85 when location-sensitive.");
  sec("How locations are found", "In order of trust: hand-sited points with a written reason (data/overrides.json); coordinates from the sponsor's starter workbook; OpenStreetMap substations and plants by exact then partial name, restricted to the right state; and last, a town-level match (±6 mi). When a name fits several places (there are two Goshens 87 miles apart), the one nearest the project's other endpoint and its planning zone wins. Matches far from the rest of the project are rejected rather than kept.");
  sec("Cost and impact (bonus)", `DESC publishes costs; Georgia's are redacted. For a Georgia line with a stated length we apply a benchmark from DESC's own list: ${money(bm.perMile)} per mile (median of ${bm.n} line projects). Savings are shown as a range on the smaller project's cost: a shareable share (default 4%, adjustable) covering mobilization, access mats, a laydown yard and traffic control, only when build windows overlap. This is for deciding whether to make a call, not a budget.`);
  sec("What this does not use", "No CEII, no non-public data and no paid APIs. Georgia filings carry a CEII banner even in their public-disclosure versions; we use only what the Commission published, and we do not reconstruct redacted costs.");
  sec("Reproduce", Object.assign(h("pre", "code"), { textContent: "python3 pipeline/build.py        # fetch filings, parse, geocode, trace lines -> data/projects.json\npython3 pipeline/build.py --offline\nnode --test tests/*.test.js\npython3 -m http.server 8000" }));
  v.append(wrap);
}

function setView(name) {
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.view === name));
  document.querySelectorAll(".view").forEach(v => { v.hidden = v.id !== `view-${name}`; });
  window.scrollTo(0, 0);
  document.querySelectorAll(".doc-view").forEach(v => { v.scrollTop = 0; });
  if (name === "explore") { renderMap(); renderTiles(); }
}

function zoom(factor, x = state.view.x + state.view.w / 2, y = state.view.y + state.view.h / 2) {
  const nextW = Math.max(12, Math.min(2400, state.view.w * factor));
  const nextH = state.view.h * nextW / state.view.w;
  state.view.x = x - (x - state.view.x) * nextW / state.view.w;
  state.view.y = y - (y - state.view.y) * nextH / state.view.h;
  state.view.w = nextW; state.view.h = nextH;
  $("map").setAttribute("viewBox", `${state.view.x} ${state.view.y} ${nextW} ${nextH}`);
  sizeMarkers();
  renderTiles();
}

function exportCsv() {
  const header = ["rank", "qualifies", "score", "certainty", "desc_project", "desc_id", "ga_project", "ga_teams", "distance_miles", "in_service_gap_days", "build_overlap_days", "overlap_days_ahead", "shared_station", "closest_approach_miles", "in_service_desc", "in_service_ga", "source_desc", "source_ga"];
  const lines = [header, ...state.pairs.map((p, i) => [i + 1, p.qualifies, p.score.total, p.certainty, p.a.name, p.a.projectId, p.b.name, p.b.projectId, p.miles.toFixed(3), p.gapDays ?? "", p.overlapDays ?? "", p.remainingDays ?? "", p.shared.map(s => s.a).join("; "), p.approachMiles?.toFixed(2) ?? "", p.a.inServiceDate ?? "", p.b.inServiceDate ?? "", pdfLink(p.a.source), pdfLink(p.b.source)])];
  const csv = lines.map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\r\n");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" })); a.download = "gridlock-opportunities.csv"; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

$("distance").addEventListener("input", event => { state.distance = Number(event.target.value); applyFilters(); });
$("year").addEventListener("input", event => { state.year = Number(event.target.value); applyFilters(); });
$("gap").addEventListener("change", event => { state.gap = event.target.value; applyFilters(); });
$("hide-past").addEventListener("change", event => { state.hidePast = event.target.checked; applyFilters(); });
$("possible").addEventListener("change", event => { state.includePossible = event.target.checked; applyFilters(); });
$("search").addEventListener("input", event => { state.search = event.target.value.trim().toLowerCase(); applyFilters(); });
$("reset").addEventListener("click", () => { state.selectedProject = null; state.selectedPair = null; applyFilters(); });
$("export").addEventListener("click", exportCsv);
$("zoom-in").addEventListener("click", () => zoom(0.7));
$("zoom-out").addEventListener("click", () => zoom(1 / 0.7));
$("fit").addEventListener("click", () => { state.view = fittedView(); renderMap(); renderTiles(); });
$("fit-all").addEventListener("click", () => { state.view = viewFor(bounds); renderMap(); renderTiles(); });
document.querySelectorAll(".tab").forEach(t => t.addEventListener("click", () => setView(t.dataset.view)));
$("map").addEventListener("wheel", event => { event.preventDefault(); const rect = $("map").getBoundingClientRect(); const x = state.view.x + (event.clientX - rect.left) / rect.width * state.view.w; const y = state.view.y + (event.clientY - rect.top) / rect.height * state.view.h; zoom(event.deltaY > 0 ? 1.15 : 0.87, x, y); }, { passive: false });
let dragging = null;
$("map").addEventListener("pointerdown", event => { if (event.target.closest(".project-marker, .pair-link")) return; dragging = { x: event.clientX, y: event.clientY, view: { ...state.view } }; $("map").setPointerCapture(event.pointerId); });
$("map").addEventListener("pointermove", event => { if (!dragging) return; const rect = $("map").getBoundingClientRect(); state.view.x = dragging.view.x - (event.clientX - dragging.x) / rect.width * state.view.w; state.view.y = dragging.view.y - (event.clientY - dragging.y) / rect.height * state.view.h; $("map").setAttribute("viewBox", `${state.view.x} ${state.view.y} ${state.view.w} ${state.view.h}`); renderTiles(); });
$("map").addEventListener("pointerup", () => { dragging = null; });
$("map").addEventListener("pointercancel", () => { dragging = null; });

try {
  const response = await fetch("data/projects.json");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  state.data = await response.json();
  state.projects = state.data.projects;
  state.asOf = new Date().toISOString().slice(0, 10);
  state.allPairs = matchProjects(state.projects, MAX_MILES, { includePossible: true, asOf: state.asOf });
  const years = state.projects.map(project => Number(project.inServiceDate?.slice(0, 4))).filter(Number.isFinite);
  $("year").min = String(Math.min(...years)); $("year").max = String(Math.max(...years)); state.year = Math.max(...years); $("year").value = String(state.year);
  const issueTotal = state.projects.reduce((n, p) => n + p.issues.filter(i => i.level !== "info").length, 0);
  $("issue-count").textContent = String(issueTotal);
  state.view = fittedView();
  renderQuality(); renderMethod();
  applyFilters();
  new ResizeObserver(() => {
    const svg = $("map");
    const rect = svg.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const centerY = state.view.y + state.view.h / 2;
    state.view.h = state.view.w * rect.height / rect.width;
    state.view.y = centerY - state.view.h / 2;
    svg.setAttribute("viewBox", `${state.view.x} ${state.view.y} ${state.view.w} ${state.view.h}`);
    sizeMarkers();
    renderTiles();
  }).observe($("map"));
} catch (error) { $("match-list").textContent = `Could not load project data: ${error.message}. Run the local server described in README.md.`; console.error(error); }
