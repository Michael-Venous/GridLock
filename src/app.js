import { matchProjects, gapLabel, opportunityText } from "./match.js";

const $ = id => document.getElementById(id);
const svgNS = "http://www.w3.org/2000/svg";
const state = { projects: [], allPairs: [], pairs: [], selectedProject: null, selectedPair: null, search: "", distance: 25, year: 2033, gap: "all", view: { x: 0, y: 0, w: 900, h: 650 } };
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
  const minX = Math.floor((northwest.x + view.x / scaleX) / tileSize);
  const maxX = Math.floor((northwest.x + (view.x + view.w) / scaleX) / tileSize);
  const minY = Math.floor((northwest.y + view.y / scaleY) / tileSize);
  const maxY = Math.floor((northwest.y + (view.y + view.h) / scaleY) / tileSize);
  for (let x = minX; x <= maxX; x++) {
    for (let y = minY; y <= maxY; y++) {
      const key = `${x}/${y}`;
      if (loadedTiles.has(key)) continue;
      loadedTiles.add(key);
      tiles.append(el("image", {
        x: (x * tileSize - northwest.x) * scaleX,
        y: (y * tileSize - northwest.y) * scaleY,
        width: tileSize * scaleX + 0.5,
        height: tileSize * scaleY + 0.5,
        href: `https://tile.openstreetmap.org/${tileZoom}/${x}/${y}.png`,
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
  if (state.selectedPair && !state.pairs.some(p => p.id === state.selectedPair)) state.selectedPair = null;
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
  if (!state.pairs.length) { const empty = h("div", "empty-state"); empty.append(h("strong", "", "No qualifying pairs"), h("p", "", "Try a wider distance or timing filter. You can also select a different project on the map.")); list.append(empty); return; }
  state.pairs.forEach((pair, index) => {
    const button = h("button", `match-card${pair.id === state.selectedPair ? " active" : ""}`);
    button.type = "button"; button.setAttribute("aria-label", `View match ${index + 1}: ${pair.a.name} and ${pair.b.name}`);
    const top = h("div", "card-top"); top.append(h("span", "rank", `PAIR ${String(index + 1).padStart(2, "0")}`), h("strong", "distance-pill", `${pair.miles.toFixed(1)} mi`));
    button.append(top, h("strong", "pair-title", `${pair.a.name} × ${pair.b.name}`));
    const meta = h("div", "card-meta"); meta.append(h("span", "", datePassed(pair.a.inServiceDate) || datePassed(pair.b.inServiceDate) ? "Past target date · status unverified" : "DESC ↔ GPC"), h("span", "", gapLabel(pair.gapDays))); button.append(meta);
    button.addEventListener("click", () => { state.selectedPair = pair.id; state.selectedProject = null; applyFilters(); });
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
  const activeIds = new Set(state.pairs.flatMap(pair => [pair.a.id, pair.b.id]));
  state.pairs.forEach(pair => {
    const a = position(pair.a.center), b = position(pair.b.center);
    const line = el("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: `pair-link${pair.id === state.selectedPair ? " selected" : ""}` });
    line.append(el("title", {}, `${pair.a.id} ↔ ${pair.b.id}: ${pair.miles.toFixed(1)} miles`));
    line.addEventListener("click", event => { event.stopPropagation(); state.selectedPair = pair.id; state.selectedProject = null; applyFilters(); });
    overlay.append(line);
  });
  const visibleProjects = state.projects.filter(bySelectedYear);
  visibleProjects.forEach(project => {
    const p = position(project.center);
    const group = el("g", { class: `project-marker ${project.utility.toLowerCase()}${activeIds.has(project.id) ? " matched" : ""}${project.id === state.selectedProject ? " chosen" : ""}`, tabindex: "0", role: "button", "aria-label": `View ${projectLabel(project)}` });
    group.append(el("circle", { cx: p.x, cy: p.y, r: 13, class: "marker-halo" }), el("circle", { cx: p.x, cy: p.y, r: 6.5, class: "marker-core" }), el("title", {}, projectLabel(project)));
    const select = event => { event.stopPropagation(); state.selectedProject = project.id; state.selectedPair = null; applyFilters(); };
    group.addEventListener("click", select);
    group.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") select(event); });
    overlay.append(group);
  });
  svg.setAttribute("viewBox", `${state.view.x} ${state.view.y} ${state.view.w} ${state.view.h}`);
  sizeMarkers();
  $("desc-count").textContent = String(visibleProjects.filter(project => project.utility === "DESC").length);
  $("gpc-count").textContent = String(visibleProjects.filter(project => project.utility === "GPC").length);
}

function infoRow(label, value) { const row = h("div", "info-row"); row.append(h("span", "", label), h("strong", "", value)); return row; }
function sourceBox() { const box = h("div", "source-box"); box.append(h("strong", "", "Data provenance"), h("p", "", "Project names, starter coordinates and in-service dates come from the supplied Projects_Overlaps.xlsx workbook, compiled from the DESC and Georgia Power public planning PDFs. Locations have not been independently verified in this prototype.")); return box; }
function renderDetail() {
  const detail = $("detail"); detail.replaceChildren();
  const pair = state.allPairs.find(p => p.id === state.selectedPair);
  const project = state.projects.find(p => p.id === state.selectedProject);
  if (pair) {
    detail.append(h("p", "eyebrow", "Opportunity detail"), h("h2", "", "Why this pair?"), h("p", "detail-lead", opportunityText(pair)));
    const score = h("div", "score-block"); score.append(h("strong", "", `${pair.miles.toFixed(2)} miles`), h("span", "", "Center-to-center distance · straight line")); detail.append(score);
    const timing = h("div", "timing-callout"); timing.append(h("strong", "", gapLabel(pair.gapDays)), h("span", "", "between in-service dates. This does not confirm that construction windows overlap."));
    if (datePassed(pair.a.inServiceDate) || datePassed(pair.b.inServiceDate)) timing.append(h("span", "", "At least one published in-service date has passed; current project status is unverified."));
    detail.append(timing);
    for (const p of [pair.a, pair.b]) {
      const block = h("div", "project-block"); block.append(h("span", `utility-label ${p.utility.toLowerCase()}`, `${p.utility} · ${p.state}`), h("h3", "", p.name), infoRow("In service", formatDate(p.inServiceDate)), infoRow("Map center", `${p.center.lat.toFixed(4)}, ${p.center.lon.toFixed(4)}`)); detail.append(block);
    }
    detail.append(sourceBox());
  } else if (project) {
    detail.append(h("p", "eyebrow", "Selected project"), h("h2", "", project.name), h("span", `utility-label ${project.utility.toLowerCase()}`, `${project.utility} · ${project.state}`));
    const count = state.pairs.length; detail.append(h("p", "detail-lead", `${count} qualifying ${count === 1 ? "project" : "projects"} from the other utility under the current filters. Select a result in the opportunity queue for details.`));
    detail.append(infoRow("In service", formatDate(project.inServiceDate)), infoRow("Map center", `${project.center.lat.toFixed(4)}, ${project.center.lon.toFixed(4)}`));
    const endpoints = h("div", "endpoint-box"); endpoints.append(h("strong", "", "Named endpoints"));
    for (const endpoint of project.endpoints) endpoints.append(h("p", "", `${endpoint.name || "Unnamed"}${endpoint.point ? ` · ${endpoint.point.lat.toFixed(4)}, ${endpoint.point.lon.toFixed(4)}` : " · coordinates unavailable"}`));
    detail.append(endpoints, sourceBox());
  } else {
    detail.append(h("p", "eyebrow", "SELECTION"), h("h2", "", "Select a project or pair"), h("p", "detail-lead", "Select a project marker to review its candidate pairs. Select a pair in the list to compare distance, timing, and source details."));
    const steps = h("div", "guide-steps"); [["01", "Find", "Look for nearby work across utilities."], ["02", "Compare", "Review distance and in-service date gap."], ["03", "Discuss", "Confirm schedules and possible shared resources with project teams."]].forEach(([n, title, body]) => { const item = h("div", "guide-step"); item.append(h("span", "", n), h("div", "", "")); item.lastChild.append(h("strong", "", title), h("p", "", body)); steps.append(item); });
    detail.append(steps, sourceBox());
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
  const header = ["rank", "project_a", "project_b", "distance_miles", "date_gap_days", "in_service_a", "in_service_b"];
  const lines = [header, ...state.pairs.map((p, i) => [i + 1, p.a.id, p.b.id, p.miles.toFixed(3), p.gapDays ?? "", p.a.inServiceDate ?? "", p.b.inServiceDate ?? ""])];
  const csv = lines.map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\r\n");
  const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" })); link.download = "gridlock-matches.csv"; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

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
