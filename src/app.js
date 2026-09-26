import { matchProjects, gapLabel, opportunityText } from "./match.js";

import { sortPairs, impactScenario, money } from "./review.js";

const $ = id => document.getElementById(id);
const svgNS = "http://www.w3.org/2000/svg";
const state = { projects: [], allPairs: [], pairs: [], selectedProject: null, selectedPair: null, search: "", distance: 25, year: 2033, gap: "all", view: { x: 0, y: 0, w: 900, h: 650 } };
state.sort = 'distance'; state.onlyShortlisted = false; state.scenarios = {};
state.shortlist = new Set();
try { state.shortlist = new Set(JSON.parse(localStorage.getItem('gridlock-shortlist') || '[]')); } catch {}
function saveShortlist() { try { localStorage.setItem('gridlock-shortlist', JSON.stringify([...state.shortlist])); } catch {} }
const bounds = { west: -84.5, east: -79.5, south: 31.0, north: 34.15 };
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

function fittedView() {
  const rect = $("map").getBoundingClientRect();
  const ratio = rect.width / Math.max(rect.height, 1);
  const w = Math.max(900, 650 * ratio);
  const h = Math.max(650, 900 / ratio);
  return { x: 450 - w / 2, y: 325 - h / 2, w, h };
}

function renderTiles() {
  const tiles = $("map").querySelector(".basemap");
  if (!tiles) return;
  const view = state.view;
  const screenScale = scaleX * $("map").getBoundingClientRect().width / view.w;
  const zoomLevel = Math.max(3, Math.min(14, Math.round(tileZoom + Math.log2(Math.max(screenScale, 0.01)))));
  const tileFactor = 2 ** (zoomLevel - tileZoom);
  if (tiles.dataset.zoom !== String(zoomLevel)) { tiles.replaceChildren(); loadedTiles.clear(); tiles.dataset.zoom = String(zoomLevel); }
  const minX = Math.floor((northwest.x + view.x / scaleX) * tileFactor / tileSize);
  const maxX = Math.floor((northwest.x + (view.x + view.w) / scaleX) * tileFactor / tileSize);
  const minY = Math.floor((northwest.y + view.y / scaleY) * tileFactor / tileSize);
  const maxY = Math.floor((northwest.y + (view.y + view.h) / scaleY) * tileFactor / tileSize);
  for (let x = minX; x <= maxX; x++) {
    for (let y = minY; y <= maxY; y++) {
      const key = `${x}/${y}`;
      if (loadedTiles.has(key)) continue;
      loadedTiles.add(key);
      tiles.append(el("image", {
        x: (x * tileSize / tileFactor - northwest.x) * scaleX,
        y: (y * tileSize / tileFactor - northwest.y) * scaleY,
        width: tileSize / tileFactor * scaleX + 0.5,
        height: tileSize / tileFactor * scaleY + 0.5,
        href: `https://tile.openstreetmap.org/${zoomLevel}/${x}/${y}.png`,
        class: "basemap-tile",
        "preserveAspectRatio": "none",
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
function position(point) {
  const world = worldPoint(point);
  return {
    x: (world.x - northwest.x) / (southeast.x - northwest.x) * 900,
    y: (world.y - northwest.y) / (southeast.y - northwest.y) * 650,
  };
}
function projectLabel(project) { return `${project.id} · ${project.name}`; }
function formatDate(value) { return value ? new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`)) : "Unknown"; }
function datePassed(value) { return Boolean(value && value < new Date().toISOString().slice(0, 10)); }
function includesSearch(pair) { const q = state.search; return !q || [pair.a, pair.b].some(p => `${p.id} ${p.name} ${p.utility}`.toLowerCase().includes(q)); }
function bySelectedYear(project) { return project.inServiceDate && Number(project.inServiceDate.slice(0, 4)) <= state.year; }

function applyFilters() {
  if (state.selectedProject && !bySelectedYear(state.projects.find(project => project.id === state.selectedProject))) state.selectedProject = null;
  state.pairs = state.allPairs.filter(pair => bySelectedYear(pair.a) && bySelectedYear(pair.b) && pair.miles < state.distance && (state.gap === "all" || (pair.gapDays !== null && pair.gapDays <= Number(state.gap))) && includesSearch(pair) && (!state.selectedProject || pair.a.id === state.selectedProject || pair.b.id === state.selectedProject));
  state.pairs = sortPairs(state.pairs.filter(pair => !state.onlyShortlisted || state.shortlist.has(pair.id)), state.sort);
  if (state.selectedPair && !state.pairs.some(p => p.id === state.selectedPair)) state.selectedPair = null;
  $("sort-description").textContent = state.sort === 'timing' ? 'Date gap first' : 'Closest first';
  $("filter-summary").textContent = `Under ${state.distance} mi · through ${state.year} · ${state.gap === 'all' ? 'any date gap' : 'gap ≤ ' + state.gap + ' days'}${state.search ? ' · search: ' + state.search : ''}${state.onlyShortlisted ? ' · shortlist' : ''}. Ranking does not confirm feasibility.`;
  $("distance-value").textContent = `${state.distance} mi`;
  $("year-value").textContent = String(state.year);
  $("result-count").textContent = `${state.pairs.length} qualifying ${state.pairs.length === 1 ? "pair" : "pairs"}`;
  $("queue-title").textContent = state.selectedProject ? `${state.selectedProject} pairs` : "Candidate pairs";
  renderStats(); renderList(); renderMap(); renderDetail();
}

function renderStats() {
  const cards = [[String(state.projects.length), "source projects"], [String(state.allPairs.length), "nearby pairs"], ["2", "utilities compared"]];
  $("stats").replaceChildren(...cards.map(([value, label]) => { const card = h("div", "stat"); card.append(h("strong", "", value), h("span", "", label)); return card; }));
}

function renderList() {
  const list = $("match-list"); list.replaceChildren();
  if (!state.pairs.length) { const empty = h("div", "empty-state"); empty.append(h("strong", "", "No qualifying pairs"), h("p", "", state.onlyShortlisted ? "No saved pairs meet these filters. Turn off shortlist-only or reset filters." : "No cross-utility candidate meets the current filters. Reset filters or clear the selected project. The challenge limit remains under 25 miles.")); list.append(empty); return; }
  state.pairs.forEach((pair, index) => {
    const button = h("button", `match-card${pair.id === state.selectedPair ? " active" : ""}`);
    button.type = "button"; button.setAttribute("aria-label", `View match ${index + 1}: ${pair.a.name} and ${pair.b.name}`);
    const top = h("div", "card-top"); top.append(h("span", "rank", `PAIR ${String(index + 1).padStart(2, "0")}`), h("strong", "distance-pill", `${pair.miles.toFixed(1)} mi`));
    button.append(top, h("strong", "pair-title", `${pair.a.name} × ${pair.b.name}`));
    const meta = h("div", "card-meta"); meta.append(h("span", "", datePassed(pair.a.inServiceDate) || datePassed(pair.b.inServiceDate) ? "Past target date · status unverified" : "DESC ↔ GPC"), h("span", "", gapLabel(pair.gapDays))); button.append(meta);
    button.addEventListener("click", () => { selectPair(pair); });
    list.append(button);
  });
}

function mapBase(svg) {
  svg.append(el("rect", { x: -600, y: -600, width: 2100, height: 1850, fill: "#e5ecec" }));
  const fallback = el("g", { class: "map-grid" });
  for (let lon = -84; lon <= -80; lon++) {
    const p = position({ lat: 32, lon });
    fallback.append(el("line", { x1: p.x, y1: -600, x2: p.x, y2: 1250, class: "grid-line" }), el("text", { x: p.x + 5, y: 18, class: "grid-label" }, `${Math.abs(lon)}°W`));
  }
  for (let lat = 31; lat <= 34; lat++) {
    const p = position({ lat, lon: -84 });
    fallback.append(el("line", { x1: -600, y1: p.y, x2: 1500, y2: p.y, class: "grid-line" }), el("text", { x: 8, y: p.y - 7, class: "grid-label" }, `${lat}°N`));
  }
  svg.append(fallback);

  svg.append(el("g", { class: "basemap" }), el("rect", { x: -600, y: -600, width: 2100, height: 1850, class: "basemap-shade" }), el("g", { id: "map-overlay" }));
  renderTiles();
}

function sizeMarkers() {
  const svg = $("map");
  const pixelsToMapUnits = state.view.w / Math.max(svg.getBoundingClientRect().width, 1);
  svg.querySelectorAll(".marker-core").forEach(marker => marker.setAttribute("r", String(6 * pixelsToMapUnits)));
  svg.querySelectorAll(".marker-halo").forEach(marker => marker.setAttribute("r", String(14 * pixelsToMapUnits)));
}

function renderMap() {
  const svg = $("map");
  if (!svg.querySelector("#map-overlay")) mapBase(svg);
  const overlay = svg.querySelector("#map-overlay");
  overlay.replaceChildren();
  svg.classList.toggle("has-selection", Boolean(state.selectedPair || state.selectedProject));
  const activeIds = new Set(state.pairs.flatMap(pair => [pair.a.id, pair.b.id]));
  state.pairs.forEach(pair => {
    const a = position(pair.a.center), b = position(pair.b.center);
    const line = el("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: `pair-link${pair.id === state.selectedPair ? " selected" : ""}` });
    line.append(el("title", {}, `${pair.a.id} ↔ ${pair.b.id}: ${pair.miles.toFixed(1)} miles`));
    line.addEventListener("click", event => { event.stopPropagation(); selectPair(pair); });
    overlay.append(line);
  });
  const visibleProjects = state.projects.filter(bySelectedYear);
  const selectedPair = state.pairs.find(pair => pair.id === state.selectedPair);
  visibleProjects.forEach(project => {
    const p = position(project.center);
    const group = el("g", { class: `project-marker ${project.utility.toLowerCase()}${activeIds.has(project.id) ? " matched" : ""}${(project.id === state.selectedProject || selectedPair?.a.id === project.id || selectedPair?.b.id === project.id) ? " chosen" : ""}`, tabindex: "0", role: "button", "aria-label": `View ${projectLabel(project)}` });
    group.append(el("circle", { cx: p.x, cy: p.y, r: 13, class: "marker-halo" }), el("circle", { cx: p.x, cy: p.y, r: 6.5, class: "marker-core" }), el("title", {}, projectLabel(project)));
    const select = event => { event.stopPropagation(); state.selectedProject = project.id; state.selectedPair = null; applyFilters(); };
    group.addEventListener("click", select);
    group.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(event); } });
    overlay.append(group);
  });
  svg.setAttribute("viewBox", `${state.view.x} ${state.view.y} ${state.view.w} ${state.view.h}`);
  sizeMarkers();
  $("desc-count").textContent = String(visibleProjects.filter(project => project.utility === "DESC").length);
  $("gpc-count").textContent = String(visibleProjects.filter(project => project.utility === "GPC").length);
}

function linkTo(label, url) {
  const a = h('a', 'evidence-link', label); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
}
function projectEvidence(project) {
  const box = h('details', 'evidence-block'); box.append(h('summary', '', 'Sources · ' + project.locationConfidence));
  box.append(h('span', 'confidence', project.locationConfidence), infoRow('Project type', project.projectType), infoRow('Source project ID', project.sourceProjectId || project.id), h('p', '', project.coordinateMethod), h('p', '', project.locationNote), h('p', '', project.documentStatus));
  if (project.previousInServiceDate) box.append(h('p', 'revision', `Target revised: ${formatDate(project.previousInServiceDate)} → ${formatDate(project.inServiceDate)}. Earlier target preserved from the workbook.`));
  for (const source of project.evidence) box.append(linkTo(`${source.title}${source.page ? ' · PDF p. ' + source.page : ''}`, source.url), h('p', '', source.note));
  box.append(linkTo('Review record evidence', `evidence.html#${project.id}`));
  return box;
}
function selectPair(pair) {
  state.selectedPair = pair.id; state.selectedProject = null;
  const a = position(pair.a.center), b = position(pair.b.center), rect = $('map').getBoundingClientRect();
  const ratio = rect.width / Math.max(rect.height, 1);
  const w = Math.max(180, Math.abs(a.x - b.x) + 120, (Math.abs(a.y - b.y) + 120) * ratio);
  state.view = { x: (a.x + b.x) / 2 - w / 2, y: (a.y + b.y) / 2 - w / ratio / 2, w, h: w / ratio };
  applyFilters(); renderTiles(); $('detail').scrollTop = 0;
}
function pairActions(pair) {
  const box = h('div', 'pair-actions');
  const save = h('button', 'ghost-button', state.shortlist.has(pair.id) ? 'Remove from shortlist' : 'Shortlist pair');
  save.type = 'button'; save.setAttribute('aria-pressed', String(state.shortlist.has(pair.id)));
  save.addEventListener('click', () => { state.shortlist.has(pair.id) ? state.shortlist.delete(pair.id) : state.shortlist.add(pair.id); saveShortlist(); applyFilters(); });
  const brief = h('button', 'ghost-button', 'Open printable brief'); brief.type = 'button';
  brief.addEventListener('click', () => {
    const values = state.scenarios[pair.id] || { share: 1, low: 10, high: 30, extra: 0 };
    if (!impactScenario(pair.a.publishedBudget, values.share, values.low, values.high, values.extra)) return;
    const query = new URLSearchParams({ pair: pair.id, ...values });
    window.location.assign(`brief.html?${query}`);
  });
  box.append(save, brief); return box;
}
function scenarioPanel(pair) {
  const box = h('section', 'scenario'); box.append(h('h3', '', 'What could coordination be worth?'), h('p', '', 'Illustrative DESC-side budget scenario. Feasibility and construction overlap are unconfirmed. These assumptions are not sourced savings rates.'));
  const budget = pair.a.publishedBudget;
  if (!budget) { box.append(h('p', '', 'No published cost basis available for this pair.')); return box; }
  box.append(infoRow('Published DESC total estimate', money(budget)), linkTo('Budget source', pair.a.evidence[0].url), h('p', '', 'Assume a portion of this budget covers shareable logistics, then estimate how much of that portion coordination could avoid. GPC costs are excluded.'));
  const values = state.scenarios[pair.id] ||= { share: 1, low: 10, high: 30, extra: 0 };
  const output = h('p', 'scenario-result'); output.setAttribute('aria-live', 'polite');
  const inputs = [];
  const update = () => {
    const result = impactScenario(budget, values.share, values.low, values.high, values.extra);
    output.textContent = result ? `${money(result.low)} to ${money(result.high)} potential net change. Shareable portion: ${money(result.shareable)}. Negative values mean added cost.` : 'Enter valid nonnegative values; percentages ≤ 100 and low ≤ high.';
    const button = [...$('detail').querySelectorAll('button')].find(b => b.textContent === 'Open printable brief');
    if (button) button.disabled = !result;
  };
  for (const [key, text] of [['share','Assumed shareable budget (%)'],['low','Assumed avoidance, low (%)'],['high','Assumed avoidance, high (%)'],['extra','Additional coordination cost ($)']]) {
    const label = h('label', '', text), input = document.createElement('input'); input.type = 'number'; input.min = 0; if(key !== 'extra') input.max = 100; input.step = 'any'; input.value = values[key];
    input.addEventListener('input', () => { values[key] = input.value === '' ? NaN : Number(input.value); update(); }); label.append(input); box.append(label); inputs.push(input);
  }
  box.append(output, h('p', 'filter-hint', 'Formula: published budget × shareable % × avoidance % − additional cost. Defaults are illustrative; no actual savings have been established.')); update(); return box;
}
function reviewQuestions() {
  const box = h('section', 'review-questions'); box.append(h('h3', '', 'Before contacting the other team'));
  const list = h('ul');
  for (const text of ['Confirm site coordinates and the current project phase.', 'Ask for actual construction dates and outage constraints.', 'Check whether crews, equipment or staging can be shared.', 'Validate budget assumptions and split responsibility for added costs.']) list.append(h('li', '', text));
  box.append(list); return box;
}

function infoRow(label, value) { const row = h("div", "info-row"); row.append(h("span", "", label), h("strong", "", value)); return row; }
function sourceBox() { const box = h("div", "source-box"); box.append(h("strong", "", "Data provenance"), h("p", "", "Ten starter records plus projects researched in public filings. Each project shows its source, date revisions and location confidence. Approximate locations remain subject to verification. Use Evidence register to review the full audit.")); return box; }
function renderDetail() {
  const detail = $("detail"); detail.replaceChildren();
  const pair = state.allPairs.find(p => p.id === state.selectedPair);
  const project = state.projects.find(p => p.id === state.selectedProject);
  if (pair) {
    detail.append(h("p", "eyebrow", "Opportunity detail"), h("h2", "", "Why this pair?"), h("p", "detail-lead", opportunityText(pair)));
    detail.append(pairActions(pair));
    const score = h("div", "score-block"); score.append(h("strong", "", `${pair.miles.toFixed(2)} miles`), h("span", "", "Center-to-center distance · straight line")); detail.append(score);
    const timing = h("div", "timing-callout"); timing.append(h("strong", "", `${pair.gapDays ?? "Unknown"} days apart`), h("span", "", "between in-service dates. This does not confirm that construction windows overlap."));
    if (datePassed(pair.a.inServiceDate) || datePassed(pair.b.inServiceDate)) timing.append(h("span", "", "At least one published in-service date has passed; current project status is unverified."));
    detail.append(timing);
    for (const p of [pair.a, pair.b]) {
      const block = h("div", "project-block"); block.append(h("span", `utility-label ${p.utility.toLowerCase()}`, `${p.utility} · ${p.state}`), h("h3", "", p.name), infoRow("In service", formatDate(p.inServiceDate)), infoRow("Map center", `${p.center.lat.toFixed(4)}, ${p.center.lon.toFixed(4)}`)); block.append(projectEvidence(p)); detail.append(block);
    }
    detail.append(scenarioPanel(pair), reviewQuestions());
  } else if (project) {
    detail.append(h("p", "eyebrow", "Selected project"), h("h2", "", project.name), h("span", `utility-label ${project.utility.toLowerCase()}`, `${project.utility} · ${project.state}`));
    const count = state.pairs.length; detail.append(h("p", "detail-lead", `${count} qualifying ${count === 1 ? "project" : "projects"} from the other utility under the current filters. Select a result in the opportunity queue for details.`));
    detail.append(infoRow("In service", formatDate(project.inServiceDate)), infoRow("Map center", `${project.center.lat.toFixed(4)}, ${project.center.lon.toFixed(4)}`));
    const endpoints = h("div", "endpoint-box"); endpoints.append(h("strong", "", "Named endpoints"));
    for (const endpoint of project.endpoints) endpoints.append(h("p", "", `${endpoint.name || "Unnamed"}${endpoint.point ? ` · ${endpoint.point.lat.toFixed(4)}, ${endpoint.point.lon.toFixed(4)}` : " · coordinates unavailable"}`));
    detail.append(endpoints, projectEvidence(project));
    const shared = state.projects.filter(p => p.id !== project.id && p.center.lat === project.center.lat && p.center.lon === project.center.lon);
    if (shared.length) {
      detail.append(h('h3', '', 'Other records at this map point'));
      for (const p of shared) { const button = h('button', 'ghost-button', p.name); button.addEventListener('click', () => {state.selectedProject=p.id; state.selectedPair=null; applyFilters();}); detail.append(button); }
    }
  } else {
    detail.append(h("p", "eyebrow", "SELECTION"), h("h2", "", "Select a project or pair"), h("p", "detail-lead", "Select a project marker to review its candidate pairs. Select a pair in the list to compare distance, timing, and source details."));
    const steps = h("div", "guide-steps"); [["01", "Find", "Look for nearby work across utilities."], ["02", "Compare", "Review distance and in-service date gap."], ["03", "Discuss", "Confirm schedules and possible shared resources with project teams."]].forEach(([n, title, body]) => { const item = h("div", "guide-step"); item.append(h("span", "", n), h("div", "", "")); item.lastChild.append(h("strong", "", title), h("p", "", body)); steps.append(item); });
    detail.append(steps, sourceBox(), linkTo("Evidence register", "evidence.html"));
  }
}

function zoom(factor, x = state.view.x + state.view.w / 2, y = state.view.y + state.view.h / 2) {
  const nextW = Math.max(160, Math.min(fittedView().w, state.view.w * factor));
  const nextH = state.view.h * nextW / state.view.w;
  state.view.x = x - (x - state.view.x) * nextW / state.view.w;
  state.view.y = y - (y - state.view.y) * nextH / state.view.h;
  state.view.w = nextW; state.view.h = nextH;
  $("map").setAttribute("viewBox", `${state.view.x} ${state.view.y} ${nextW} ${nextH}`);
  sizeMarkers();
  renderTiles();
}

function exportCsv() {
  const header = ["rank", "project_a", "project_b", "distance_miles", "date_gap_days", "in_service_a", "in_service_b", "name_a", "name_b", "confidence_a", "confidence_b", "source_a", "source_b"];
  const lines = [header, ...state.pairs.map((p, i) => [i + 1, p.a.id, p.b.id, p.miles.toFixed(3), p.gapDays ?? "", p.a.inServiceDate ?? "", p.b.inServiceDate ?? "", p.a.name, p.b.name, p.a.locationConfidence, p.b.locationConfidence, p.a.evidence[0].title, p.b.evidence[0].title])];
  const csv = lines.map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\r\n");
  const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" })); link.download = "gridlock-matches.csv"; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

$("sort").addEventListener('change', event => { state.sort = event.target.value; applyFilters(); });
$("shortlisted").addEventListener('change', event => { state.onlyShortlisted = event.target.checked; applyFilters(); });
$("reset-filters").addEventListener('click', () => {
  state.search = ''; state.distance = 25; state.gap = 'all'; state.year = Number($("year").max); state.sort = 'distance'; state.onlyShortlisted = false;
  $("search").value = ''; $("distance").value = 25; $("gap").value = 'all'; $("year").value = state.year; $("sort").value = 'distance'; $("shortlisted").checked = false;
  applyFilters();
});
$("distance").addEventListener("input", event => { state.distance = Number(event.target.value); applyFilters(); });
$("year").addEventListener("input", event => { state.year = Number(event.target.value); applyFilters(); });
$("gap").addEventListener("change", event => { state.gap = event.target.value; applyFilters(); });
$("search").addEventListener("input", event => { state.search = event.target.value.trim().toLowerCase(); applyFilters(); });
$("reset").addEventListener("click", () => { state.selectedProject = null; state.selectedPair = null; applyFilters(); });
$("export").addEventListener("click", exportCsv);
$("zoom-in").addEventListener("click", () => zoom(0.7));
$("zoom-out").addEventListener("click", () => zoom(1 / 0.7));
$("fit").addEventListener("click", () => { state.view = fittedView(); renderMap(); renderTiles(); });
$("map").addEventListener("wheel", event => { event.preventDefault(); const rect = $("map").getBoundingClientRect(); const x = state.view.x + (event.clientX - rect.left) / rect.width * state.view.w; const y = state.view.y + (event.clientY - rect.top) / rect.height * state.view.h; zoom(event.deltaY > 0 ? 1.15 : 0.87, x, y); }, { passive: false });
let dragging = null;
$("map").addEventListener("pointerdown", event => { if (event.target.closest(".project-marker, .pair-link")) return; dragging = { x: event.clientX, y: event.clientY, view: { ...state.view } }; $("map").setPointerCapture(event.pointerId); });
$("map").addEventListener("pointermove", event => { if (!dragging) return; const rect = $("map").getBoundingClientRect(); state.view.x = dragging.view.x - (event.clientX - dragging.x) / rect.width * state.view.w; state.view.y = dragging.view.y - (event.clientY - dragging.y) / rect.height * state.view.h; $("map").setAttribute("viewBox", `${state.view.x} ${state.view.y} ${state.view.w} ${state.view.h}`); renderTiles(); });
$("map").addEventListener("pointerup", () => { dragging = null; });
$("map").addEventListener("pointercancel", () => { dragging = null; });

try {
  const response = await fetch("data/projects.json");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json(); state.projects = data.projects; state.allPairs = matchProjects(state.projects);
  const years = state.projects.map(project => Number(project.inServiceDate?.slice(0, 4))).filter(Number.isFinite);
  $("year").min = String(Math.min(...years)); $("year").max = String(Math.max(...years)); state.year = Math.max(...years); $("year").value = String(state.year);
  state.view = fittedView();
  applyFilters();
  const linkedPair = state.allPairs.find(pair => pair.id === new URLSearchParams(location.search).get('pair'));
  if (linkedPair) {
    const query = new URLSearchParams(location.search);
    const values = Object.fromEntries(['share','low','high','extra'].map((key, i) => [key, query.has(key) ? Number(query.get(key)) : [1,10,30,0][i]]));
    if (impactScenario(linkedPair.a.publishedBudget, values.share, values.low, values.high, values.extra)) state.scenarios[linkedPair.id] = values;
    selectPair(linkedPair);
  }
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
} catch (error) { $("match-list").textContent = `Could not load project data: ${error.message}. Run the local server described in README.md.`; }
