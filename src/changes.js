// Area matching and summaries for the change log (data/changes.json). Pure functions, so the app and the tests
// agree on what "in your area" means.

export const KINDS = {
  added: "New projects", removed: "Dropped projects", date: "Rescheduled", cost: "Re-costed", name: "Renamed",
  pairNew: "New pairs", pairGone: "Pairs gone", pairTiming: "Pair timing changed",
};

const R_MI = 3958.7613;
const toXY = ([lon, lat], lat0) => [lon * Math.PI / 180 * R_MI * Math.cos(lat0 * Math.PI / 180), lat * Math.PI / 180 * R_MI];

export function pointInPolygon([x, y], ring) {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < xi + (y - yi) * (xj - xi) / (yj - yi)) hit = !hit;
  }
  return hit;
}

// Miles from a point to the polygon's edge (0 when inside).
export function milesToPolygon(pt, ring) {
  if (pointInPolygon(pt, ring)) return 0;
  const lat0 = pt[1], p = toXY(pt, lat0);
  let best = Infinity;
  for (let i = 0; i < ring.length - 1; i++) {
    const a = toXY(ring[i], lat0), b = toXY(ring[i + 1], lat0);
    const dx = b[0] - a[0], dy = b[1] - a[1], L = dx * dx + dy * dy;
    const t = L ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L)) : 0;
    best = Math.min(best, Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy));
  }
  return best;
}

// area: a closed [[lon, lat], ...] ring, or null for the whole region.
export function inArea(points, area, bufferMi = 0) {
  if (!area) return true;
  return (points ?? []).some(pt => bufferMi > 0 ? milesToPolygon(pt, area) <= bufferMi : pointInPolygon(pt, area));
}

// One row per thing that changed, tagged with the kinds it counts under.
export function changeItems(event) {
  const items = [];
  for (const c of event.projects) {
    const kinds = c.kind === "changed" ? c.what : [c.kind];
    items.push({ type: "project", kinds, change: c, points: c.points });
  }
  for (const p of event.pairs) items.push({ type: "pair", kinds: [{ new: "pairNew", gone: "pairGone", timing: "pairTiming" }[p.kind]], change: p, points: p.points });
  return items;
}

// The Pairs view's id for a changed pair, from both projects' current app ids; null when either left the data.
export function pairAppId(pair) {
  return pair.a?.appId && pair.b?.appId ? `${pair.a.appId}__${pair.b.appId}` : null;
}

export function itemsIn(event, area, bufferMi = 0, kinds = null) {
  return changeItems(event).filter(i => inArea(i.points, area, bufferMi) && (!kinds || i.kinds.some(k => kinds.includes(k))));
}

export function countByKind(items) {
  const out = Object.fromEntries(Object.keys(KINDS).map(k => [k, 0]));
  for (const i of items) for (const k of i.kinds) out[k] += 1;
  return out;
}

// A closed ring from map bounds [[west, south], [east, north]].
export function boundsRing([[w, s], [e, n]]) {
  return [[w, s], [e, s], [e, n], [w, n], [w, s]];
}

export function closeRing(points) {
  if (points.length < 3) return null;
  const [a, b] = [points[0], points[points.length - 1]];
  return a[0] === b[0] && a[1] === b[1] ? points : [...points, points[0]];
}

// A plan's current edition ("2026–2030"), from the change log's filings: the one at the plan's source URL, else the
// plan's newest. Without either, a year range in the source title; null when nothing says.
export function currentEdition(plan, filings = []) {
  const own = filings.filter(f => f.plan === plan.id);
  const filing = own.find(f => f.url && f.url === plan.source?.url) ?? own.reduce((a, f) => !a || f.date >= a.date ? f : a, null);
  const edition = filing?.edition ?? plan.source?.title?.match(/\d{4}\s*[-–]\s*\d{4}/)?.[0];
  return edition ? edition.replace(/\s*[-–]\s*/g, "–") : null;
}

// What each plan's filing dates are, from the filings' own dateBasis: "the PDF creation date for DESC and the GA PSC
// filed date for Georgia ITS". Plans in order of first filing; null when no filing says.
export function dateBasisText(filings, planName = id => id) {
  const byPlan = new Map();
  for (const f of filings) if (f.dateBasis) byPlan.set(f.plan, new Set([...(byPlan.get(f.plan) ?? []), f.dateBasis]));
  const byBasis = new Map();
  for (const [plan, bases] of byPlan) { const k = [...bases].join(" or "); byBasis.set(k, [...(byBasis.get(k) ?? []), planName(plan)]); }
  const and = xs => xs.length < 3 ? xs.join(" and ") : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`;
  return byBasis.size ? and([...byBasis].map(([basis, names]) => `the ${basis} for ${and(names)}`)) : null;
}

export function daysLabel(days) {
  if (days == null) return "";
  const m = Math.round(Math.abs(days) / 30.44);
  const span = m >= 1 ? `${m} mo` : `${Math.abs(days)} d`;
  return days > 0 ? `${span} later` : days < 0 ? `${span} earlier` : "same day";
}

// Include initial filings without pretending their projects are changes.
export function filingEntries(log) {
  const events = new Map(log.events.map(e => [e.id, e]));
  return log.filings.map(f => events.get(f.id) ?? { ...f, baseline: true, baselineProjects: log.baselines?.[f.id] ?? [], projects: [], pairs: [] })
    .sort((a, b) => (b.date ?? "").localeCompare(a.date ?? "") || b.id.localeCompare(a.id));
}
