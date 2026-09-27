// The Changes tab: what each new filing changed, on a map, filtered to an area the planner draws.
import { KINDS, itemsIn, changeItems, countByKind, inArea, boundsRing, closeRing, daysLabel, pairAppId, dateBasisText } from "./changes.js";
import { planOf } from "./match.js";

const AREA_KEY = "gridlock.area";
const KIND_TAG = { added: "New", removed: "Dropped", date: "Rescheduled", cost: "Re-costed", name: "Renamed", pairNew: "New pair", pairGone: "Pair gone", pairTiming: "Timing changed" };
const readStore = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key) ?? "null") ?? fallback; } catch { return fallback; } };
const writeStore = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage unavailable: lasts for this visit */ } };

export function createChangesView(ctx) {
  const { h, link, formatDate, money, colors, basemap, fallbackStyle, openProject, openPair, pairExists, side: sideOf, sideName, planName, loadLog } = ctx;
  const saved = readStore(AREA_KEY, {});
  const view = { data: null, event: null, area: saved.area ?? null, bufferMi: saved.bufferMi ?? 0, kinds: null, drawing: null, map: null, mapReady: false };
  let areaOpen = Boolean(saved.area);
  const side = document.getElementById("changes-side");
  const drawBar = document.getElementById("draw-bar");

  async function open() {
    if (!view.data) {
      side.replaceChildren(h("p", "muted-note", "Loading the change log…"));
      try {
        view.data = await loadLog(); view.event = view.data.events[0]?.id ?? null;
      } catch (error) { side.replaceChildren(h("p", "muted-note", `Could not load the change log: ${error.message}.`)); return; }
    }
    initMap();
    render();
    view.map?.resize();
  }

  const event = () => view.data.events.find(e => e.id === view.event);
  const shortTitle = e => ({ desc: "DESC list", ga: "Georgia plan" })[planOf(e)] ?? `${e.utility} list`;
  const editionOf = id => (view.data.filings.find(f => f.id === id)?.edition ?? id.split("-").slice(1).join("-")).replaceAll("-", "–");
  const eventTitle = e => `${shortTitle(e)} ${editionOf(e.id)}`;
  // Today's DESC × Georgia pairs read "DESC 0139 M,N × TEAMS 21275"; other pairs name both plans.
  const pairLabel = p => p.a.plan === "desc" && p.b.plan === "ga" ? `DESC ${p.a.projectId} × ${p.b.projectId}` : `${planName(p.a.plan)} ${p.a.projectId} × ${planName(p.b.plan)} ${p.b.projectId}`;
  const saveArea = () => writeStore(AREA_KEY, { area: view.area, bufferMi: view.bufferMi });

  // ---------- side panel ----------
  function render() {
    side.replaceChildren();
    side.append(h("h1", "", "What changed between filings"),
      h("p", "doc-lead", "Each new edition is compared with the one before it: projects added, dropped, rescheduled or re-costed, and qualifying pairs that appeared, went away or changed timing. Select a filing to review its changes."));
    const area = h("details", "area-disclosure"); area.open = areaOpen || Boolean(view.drawing);
    area.append(h("summary", "", `Filter to an area${view.area ? " · active" : ""}`), areaBlock());
    area.addEventListener("toggle", () => { areaOpen = area.open; });
    side.append(eventsBlock(), area);
    const e = event();
    if (e) side.append(detailBlock(e));
    renderMap();
  }

  function areaBlock() {
    const box = h("section", "area-block");
    const head = h("div", "area-head");
    head.append(h("h2", "", "Your area"), h("span", "area-state", view.area ? `Drawn area, ${view.area.length - 1} corners` : "Whole region"));
    const actions = h("div", "area-actions");
    const btn = (text, fn, cls = "button") => { const b = h("button", cls, text); b.type = "button"; b.addEventListener("click", fn); actions.append(b); return b; };
    btn(view.drawing ? "Drawing…" : "Draw on the map", startDrawing).disabled = Boolean(view.drawing);
    btn("Use the map view", () => { if (!view.map) return; const b = view.map.getBounds(); setArea(boundsRing([[b.getWest(), b.getSouth()], [b.getEast(), b.getNorth()]])); });
    btn("Clear", () => setArea(null), "link-button").disabled = !view.area;
    const range = h("label", "range");
    range.append(document.createTextNode("Also count projects up to "), h("strong", "", `${view.bufferMi} mi`), document.createTextNode(" outside it"));
    const input = h("input"); Object.assign(input, { type: "range", min: 0, max: 25, step: 1, value: String(view.bufferMi) }); input.disabled = !view.area;
    input.addEventListener("input", () => { view.bufferMi = Number(input.value); range.querySelector("strong").textContent = `${view.bufferMi} mi`; saveArea(); renderCountsOnly(); });
    input.addEventListener("change", () => render());
    range.append(input);
    box.append(head, actions, range);
    return box;
  }

  function eventsBlock() {
    const box = h("section", "events-block");
    box.append(h("h2", "", "Filings"));
    const list = h("div", "event-list"); list.setAttribute("role", "listbox"); list.setAttribute("aria-label", "Filings");
    for (const e of view.data.events) {
      const n = itemsIn(e, view.area, view.bufferMi).length;
      const b = h("button", `event-card ${sideOf(e)}${e.id === view.event ? " active" : ""}`);
      b.type = "button"; b.setAttribute("role", "option"); b.setAttribute("aria-selected", String(e.id === view.event));
      b.append(h("strong", "", eventTitle(e)), h("span", "event-date", `${formatDate(e.date)} · replaced the ${editionOf(e.previous.id)} ${planOf(e) === "ga" ? "plan" : "list"}`),
        h("span", "event-count", `${n} ${n === 1 ? "change" : "changes"}${view.area ? " in your area" : ""}`));
      b.dataset.event = e.id;
      b.addEventListener("click", () => { view.event = e.id; view.kinds = null; render(); fitEvent(); });
      list.append(b);
    }
    box.append(list);
    const oldest = view.data.filings[0], dates = dateBasisText(view.data.filings, planName);
    box.append(h("p", "muted-note", `${dates ? `A filing's date is ${dates}. ` : ""}The log starts with ${oldest.title} (${formatDate(oldest.date)}).`));
    return box;
  }

  function renderCountsOnly() {
    side.querySelectorAll(".event-card").forEach(b => {
      const e = view.data.events.find(x => x.id === b.dataset.event);
      const n = itemsIn(e, view.area, view.bufferMi).length;
      b.querySelector(".event-count").textContent = `${n} ${n === 1 ? "change" : "changes"}${view.area ? " in your area" : ""}`;
    });
  }

  function detailBlock(e) {
    const box = h("section", "event-detail");
    const all = itemsIn(e, view.area, view.bufferMi);
    const counts = countByKind(all);
    const head = h("div", "event-head");
    head.append(h("h2", "", `${eventTitle(e)}: ${all.length} ${all.length === 1 ? "change" : "changes"}${view.area ? " in your area" : ""}`));
    const src = h("p", "muted-note"); src.append(link(e.url, e.title), document.createTextNode(" compared with "), link(e.previous.url, e.previous.title), document.createTextNode("."));
    head.append(src);
    box.append(head);
    const chips = h("div", "kind-chips"); chips.setAttribute("aria-label", "Show these kinds of change");
    for (const [k, label] of Object.entries(KINDS)) {
      if (!counts[k]) continue;
      const on = !view.kinds || view.kinds.includes(k);
      const c = h("button", `kind-chip${on ? " on" : ""}`, `${label} ${counts[k]}`); c.type = "button"; c.setAttribute("aria-pressed", String(on));
      c.addEventListener("click", () => {
        const cur = view.kinds ?? Object.keys(KINDS).filter(x => counts[x]);
        view.kinds = cur.includes(k) ? cur.filter(x => x !== k) : [...cur, k];
        if (view.kinds.length === Object.keys(KINDS).filter(x => counts[x]).length) view.kinds = null;
        render();
      });
      chips.append(c);
    }
    box.append(chips);
    const items = view.kinds ? all.filter(i => i.kinds.some(k => view.kinds.includes(k))) : all;
    const ul = h("ul", "change-list");
    const order = ["pairNew", "pairGone", "added", "removed", "date", "pairTiming", "name", "cost"];
    items.sort((a, b) => Math.min(...a.kinds.map(k => order.indexOf(k))) - Math.min(...b.kinds.map(k => order.indexOf(k))));
    items.forEach(i => ul.append(i.type === "pair" ? pairRow(i.change) : projectRow(i.change, i.kinds)));
    if (!items.length) ul.append(h("li", "empty", view.area ? "Nothing in this filing touches your area. Widen the distance or pick another filing." : "No changes of these kinds."));
    box.append(ul);
    return box;
  }

  function projectRow(c, kinds) {
    const li = h("li", `change-row ${sideOf(c)}`);
    const tags = h("span", "change-tags"); kinds.forEach(k => tags.append(h("span", `ctag ${k}`, KIND_TAG[k])));
    const title = h("span", "change-title"); title.append(h("b", "", `${sideName(c)} ${c.projectId}`), document.createTextNode(` ${c.name}`));
    li.append(tags, title);
    const facts = [];
    if (c.kind === "added") facts.push(`In service ${formatDate(c.isd)}`);
    if (c.kind === "removed") facts.push(`Was due ${formatDate(c.isd)}. ${c.reason}`);
    if (kinds.includes("date")) facts.push(`In service ${formatDate(c.oldIsd)} → ${formatDate(c.isd)} (${daysLabel(c.days)})`);
    if (kinds.includes("cost")) facts.push(`Estimate ${money(c.oldCost)} → ${money(c.cost)}`);
    if (kinds.includes("name")) facts.push(`Was “${c.oldName}”`);
    if (c.note && !/^no change/i.test(c.note)) facts.push(`Filing's note: ${c.note}`);
    facts.forEach(f => li.append(h("span", "change-fact", f)));
    const links = h("span", "change-links");
    if (c.appId) { const b = h("button", "link-button", "Open project"); b.type = "button"; b.addEventListener("click", () => openProject(c.appId)); links.append(b); }
    links.append(link(`${c.source.url}#page=${c.source.page}`, `${c.kind === "removed" ? "Previous filing" : "Filing"}, p. ${c.source.page}`));
    li.append(links);
    li.addEventListener("mouseenter", () => highlight(c.points)); li.addEventListener("mouseleave", () => highlight(null));
    return li;
  }

  function pairRow(p) {
    const li = h("li", "change-row pair");
    const kind = { new: "pairNew", gone: "pairGone", timing: "pairTiming" }[p.kind];
    const tags = h("span", "change-tags"); tags.append(h("span", `ctag ${kind}`, KIND_TAG[kind]));
    const title = h("span", "change-title"); title.append(h("b", "", pairLabel(p)), document.createTextNode(` ${p.a.name} × ${p.b.name}`));
    li.append(tags, title);
    const gap = p.gapDays == null ? "in-service dates unknown" : `in service ${p.gapDays} days apart`;
    li.append(h("span", "change-fact", p.kind === "timing" ? `${p.miles.toFixed(1)} mi apart; in-service gap ${p.oldGapDays} → ${p.gapDays} days` : `${p.miles.toFixed(1)} mi apart, ${gap}. ${p.reason[0].toUpperCase()}${p.reason.slice(1)}.`));
    const id = pairAppId(p);
    if (p.kind !== "gone" && id && pairExists(id)) { const links = h("span", "change-links"); const b = h("button", "link-button", "Open pair"); b.type = "button"; b.addEventListener("click", () => openPair(id)); links.append(b); li.append(links); }
    li.addEventListener("mouseenter", () => highlight(p.points)); li.addEventListener("mouseleave", () => highlight(null));
    return li;
  }

  // ---------- map ----------
  function initMap() {
    if (view.map || !window.maplibregl) return;
    try {
      view.map = new maplibregl.Map({ container: "changes-map", style: basemap, bounds: [[-83.3, 31.4], [-79.8, 34.5]], fitBoundsOptions: { padding: 20 }, dragRotate: false, pitchWithRotate: false, attributionControl: { compact: window.innerWidth < 760 } });
    } catch { document.getElementById("changes-map").append(h("p", "map-error", "The map needs WebGL, which this browser has turned off.")); return; }
    const map = view.map;
    map.touchZoomRotate.disableRotation();
    let failed = false;
    map.on("error", ev => { if (!view.mapReady && !map.isStyleLoaded() && !failed) { failed = true; console.warn("Basemap unavailable:", ev.error?.message); map.setStyle(fallbackStyle); } });
    map.on("load", () => {
      const empty = { type: "FeatureCollection", features: [] };
      for (const id of ["area", "draft", "changes", "hl"]) map.addSource(id, { type: "geojson", data: empty });
      map.addLayer({ id: "area-fill", type: "fill", source: "area", paint: { "fill-color": "#10222a", "fill-opacity": 0.06 } });
      map.addLayer({ id: "area-line", type: "line", source: "area", paint: { "line-color": "#10222a", "line-width": 2 } });
      map.addLayer({ id: "draft-line", type: "line", source: "draft", filter: ["==", ["geometry-type"], "LineString"], paint: { "line-color": "#10222a", "line-width": 2, "line-dasharray": [2, 1.5] } });
      map.addLayer({ id: "draft-pts", type: "circle", source: "draft", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": 4, "circle-color": "#fff", "circle-stroke-color": "#10222a", "circle-stroke-width": 2 } });
      const pairPaint = { "line-color": "#4d6670", "line-width": 1.6, "line-opacity": ["case", ["get", "inside"], 0.8, 0.2] };
      map.addLayer({ id: "pair-lines", type: "line", source: "changes", filter: ["all", ["==", ["get", "type"], "pair"], ["!=", ["get", "kind"], "pairGone"]], paint: pairPaint });
      map.addLayer({ id: "pair-gone", type: "line", source: "changes", filter: ["all", ["==", ["get", "type"], "pair"], ["==", ["get", "kind"], "pairGone"]], paint: { ...pairPaint, "line-dasharray": [2, 2] } });
      const bySide = ["match", ["get", "side"], ...Object.entries(colors).flat(), colors.desc];
      map.addLayer({ id: "change-pts", type: "circle", source: "changes", filter: ["==", ["get", "type"], "project"],
        paint: { "circle-radius": 6, "circle-color": ["match", ["get", "kind"], "removed", "#fff", bySide], "circle-stroke-color": bySide, "circle-stroke-width": 2,
          "circle-opacity": ["case", ["get", "inside"], 1, 0.25], "circle-stroke-opacity": ["case", ["get", "inside"], 1, 0.25] } });
      map.addLayer({ id: "change-glyph", type: "symbol", source: "changes", filter: ["all", ["==", ["get", "type"], "project"], ["get", "inside"]],
        layout: { "text-field": ["match", ["get", "kind"], "added", "+", "removed", "−", "date", "›", "cost", "$", "name", "a", ""], "text-font": ["Noto Sans Bold"], "text-size": 11, "text-allow-overlap": true, "text-ignore-placement": true },
        paint: { "text-color": ["match", ["get", "kind"], "removed", "#10222a", "#fff"] } });
      map.addLayer({ id: "hl", type: "circle", source: "hl", paint: { "circle-radius": 13, "circle-opacity": 0, "circle-stroke-color": "#10222a", "circle-stroke-width": 2.5 } });
      view.mapReady = true;
      renderMap();
    });
    const tip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, className: "map-tip", offset: 10, maxWidth: "280px" });
    map.on("mousemove", ev => {
      if (!view.mapReady || view.drawing) return;
      const f = map.queryRenderedFeatures(ev.point, { layers: ["change-pts", "pair-lines", "pair-gone"] })[0];
      map.getCanvas().style.cursor = f ? "pointer" : "";
      if (f) tip.setLngLat(ev.lngLat).setText(f.properties.title).addTo(map); else tip.remove();
    });
    map.on("click", ev => {
      if (!view.drawing) return;
      const pt = [ev.lngLat.lng, ev.lngLat.lat];
      const first = view.drawing[0];
      if (first && view.drawing.length >= 3) {
        const a = map.project(first), b = ev.point;
        if (Math.hypot(a.x - b.x, a.y - b.y) < 12) { finishDrawing(); return; }
      }
      view.drawing.push(pt); renderDraft();
    });
    map.on("dblclick", ev => { if (view.drawing) { ev.preventDefault(); finishDrawing(); } });
    map.on("mousemove", ev => { if (view.drawing) renderDraft([ev.lngLat.lng, ev.lngLat.lat]); });
  }

  function renderMap() {
    if (!view.mapReady) return;
    const map = view.map;
    map.getSource("area").setData({ type: "FeatureCollection", features: view.area ? [{ type: "Feature", geometry: { type: "Polygon", coordinates: [view.area] }, properties: {} }] : [] });
    const e = event();
    const feats = [];
    if (e) for (const i of changeItems(e)) {
      const inside = inArea(i.points, view.area, view.bufferMi) && (!view.kinds || i.kinds.some(k => view.kinds.includes(k)));
      const c = i.change;
      if (i.type === "pair") {
        if (!c.a.center || !c.b.center) continue;
        feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: [[c.a.center.lon, c.a.center.lat], [c.b.center.lon, c.b.center.lat]] },
          properties: { type: "pair", kind: i.kinds[0], inside, title: `${KIND_TAG[i.kinds[0]]}: ${pairLabel(c)}, ${c.miles.toFixed(1)} mi` } });
      } else if (c.center) {
        feats.push({ type: "Feature", geometry: { type: "Point", coordinates: [c.center.lon, c.center.lat] },
          properties: { type: "project", kind: i.kinds[0], side: sideOf(c), inside, title: `${i.kinds.map(k => KIND_TAG[k]).join(", ")}: ${c.projectId} ${c.name}` } });
      }
    }
    map.getSource("changes").setData({ type: "FeatureCollection", features: feats });
  }

  function highlight(points) {
    if (!view.mapReady) return;
    view.map.getSource("hl").setData({ type: "FeatureCollection", features: (points ?? []).slice(-1).map(p => ({ type: "Feature", geometry: { type: "Point", coordinates: p }, properties: {} })) });
  }

  function fitEvent() {
    if (!view.mapReady) return;
    const pts = view.area ?? itemsIn(event(), null).flatMap(i => i.points);
    if (!pts.length) return;
    const lons = pts.map(p => p[0]), lats = pts.map(p => p[1]);
    view.map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 40, maxZoom: 11, duration: 0 });
  }

  // ---------- drawing ----------
  function startDrawing() {
    if (!view.mapReady) return;
    view.drawing = [];
    view.map.doubleClickZoom.disable();
    view.map.getCanvas().style.cursor = "crosshair";
    drawBar.hidden = false;
    drawBar.replaceChildren(h("span", "", "Click to add corners. Click the first corner, double-click or press Enter to finish. Esc cancels."));
    const done = h("button", "button primary", "Finish"); done.type = "button"; done.addEventListener("click", finishDrawing);
    const cancel = h("button", "button", "Cancel"); cancel.type = "button"; cancel.addEventListener("click", cancelDrawing);
    drawBar.append(done, cancel);
    document.addEventListener("keydown", drawKeys);
    render();
  }

  function drawKeys(ev) { if (ev.key === "Escape") cancelDrawing(); else if (ev.key === "Enter") finishDrawing(); }

  function renderDraft(cursor) {
    const pts = cursor ? [...view.drawing, cursor] : view.drawing;
    const feats = pts.map(p => ({ type: "Feature", geometry: { type: "Point", coordinates: p }, properties: {} }));
    if (pts.length > 1) feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: pts }, properties: {} });
    view.map.getSource("draft").setData({ type: "FeatureCollection", features: feats });
  }

  function endDrawing() {
    view.drawing = null;
    view.map.doubleClickZoom.enable();
    view.map.getCanvas().style.cursor = "";
    view.map.getSource("draft").setData({ type: "FeatureCollection", features: [] });
    drawBar.hidden = true;
    document.removeEventListener("keydown", drawKeys);
  }

  function finishDrawing() {
    const ring = closeRing(view.drawing ?? []);
    endDrawing();
    if (ring) setArea(ring); else render();
  }

  function cancelDrawing() { endDrawing(); render(); }

  function setArea(ring) {
    view.area = ring ? ring.map(([x, y]) => [Number(x.toFixed(5)), Number(y.toFixed(5))]) : null;
    if (!ring) view.bufferMi = 0;
    saveArea();
    render();
  }

  return { open, resize: () => view.map?.resize() };
}
