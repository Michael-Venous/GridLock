export const MAX_MILES = 25;
const DAY = 86400000;

export function milesBetween(a, b) {
  const radians = Math.PI / 180;
  const dLat = (b.lat - a.lat) * radians;
  const dLon = (b.lon - a.lon) * radians;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * radians) * Math.cos(b.lat * radians) * Math.sin(dLon / 2) ** 2;
  return 3958.7613 * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
}

const toTime = value => Date.parse(`${value}T00:00:00Z`);

export function dateGapDays(a, b) {
  if (!a || !b) return null;
  return Math.round(Math.abs(toTime(a) - toTime(b)) / DAY);
}

// Days both build windows are open at once (0 when they don't touch), or null when either window is unknown.
export function windowOverlapDays(a, b) {
  if (!a?.start || !a?.end || !b?.start || !b?.end) return null;
  return Math.max(0, Math.round((Math.min(toTime(a.end), toTime(b.end)) - Math.max(toTime(a.start), toTime(b.start))) / DAY));
}

// Overlap still ahead of us: both windows clipped to start no earlier than `asOf`.
export function remainingOverlapDays(a, b, asOf) {
  if (!asOf) return windowOverlapDays(a, b);
  const clip = w => w && w.start && w.end ? { start: w.start > asOf ? w.start : asOf, end: w.end } : w;
  const ca = clip(a), cb = clip(b);
  if (ca?.end && ca.end < asOf || cb?.end && cb.end < asOf) return 0;
  return windowOverlapDays(ca, cb);
}

// Is the pair inside 25 miles no matter where, within their uncertainty, the two centers really are?
export function certainty(miles, a, b, cutoff = MAX_MILES) {
  const slack = (a.radiusMi ?? 0) + (b.radiusMi ?? 0);
  if (miles + slack < cutoff) return "robust";
  if (miles < cutoff) return "sensitive";
  if (miles - slack < cutoff) return "possible";
  return "none";
}

// Two projects touching the same station (endpoints within half a mile) share a site, not just a region.
export function sharedStations(a, b) {
  const out = [];
  for (const x of a.endpoints ?? []) for (const y of b.endpoints ?? []) {
    if (x.point && y.point && milesBetween(x.point, y.point) <= 0.5) out.push({ a: x.name, b: y.name, miles: milesBetween(x.point, y.point) });
  }
  return out;
}

function shape(project) {
  if (project.route?.coords?.length) return project.route.coords.map(([lat, lon]) => ({ lat, lon }));
  const points = (project.endpoints ?? []).filter(e => e.point).map(e => e.point);
  return points.length ? points : project.center ? [project.center] : [];
}

function xy(p, lat0) { const r = Math.PI / 180; return [p.lon * r * 3958.7613 * Math.cos(lat0 * r), p.lat * r * 3958.7613]; }
function segDist(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], L = dx * dx + dy * dy;
  const t = L ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L)) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}

// Closest approach between the two projects' mapped lines (or endpoints when no line was traced).
export function closestApproachMiles(a, b) {
  const A = shape(a), B = shape(b);
  if (!A.length || !B.length) return null;
  const lat0 = (A[0].lat + B[0].lat) / 2;
  const pa = A.map(p => xy(p, lat0)), pb = B.map(p => xy(p, lat0));
  let best = Infinity;
  const segs = pts => pts.length > 1 ? pts.slice(1).map((p, i) => [pts[i], p]) : [[pts[0], pts[0]]];
  for (const p of pa) for (const [s, e] of segs(pb)) best = Math.min(best, segDist(p, s, e));
  for (const p of pb) for (const [s, e] of segs(pa)) best = Math.min(best, segDist(p, s, e));
  return best;
}

// Ranking score, 0-100. Every part is shown to the user; none of it changes which pairs qualify.
export function scorePair(pair) {
  const proximity = 35 * Math.max(0, 1 - pair.miles / MAX_MILES);
  let timing = 0;
  const ahead = pair.remainingDays ?? pair.overlapDays;
  if (ahead > 0) timing = 35 * Math.min(1, 0.5 + ahead / 730);
  else if (pair.gapDays !== null) timing = 17.5 * Math.max(0, 1 - pair.gapDays / 1095);
  const shared = pair.shared.length ? 20 : 0;
  const corridor = pair.approachMiles === null ? 0 : 10 * Math.max(0, 1 - pair.approachMiles / 5);
  const confidence = pair.certainty === "robust" ? 1 : 0.85;
  const total = Math.round((proximity + timing + shared + corridor) * confidence);
  return { total, parts: { proximity: Math.round(proximity), timing: Math.round(timing), shared, corridor: Math.round(corridor), confidence } };
}

export function matchProjects(projects, cutoff = MAX_MILES, { includePossible = false, asOf = null } = {}) {
  const left = projects.filter(project => project.state === "SC" && project.center);
  const right = projects.filter(project => project.state === "GA" && project.center);
  const pairs = [];
  for (const a of left) for (const b of right) {
    const miles = milesBetween(a.center, b.center);
    const level = certainty(miles, a, b, cutoff);
    if (level === "none" || (level === "possible" && !includePossible)) continue;
    const pair = {
      id: `${a.id}__${b.id}`, a, b, miles, certainty: level, qualifies: miles < cutoff,
      gapDays: dateGapDays(a.inServiceDate, b.inServiceDate),
      overlapDays: windowOverlapDays(a.window, b.window),
      remainingDays: remainingOverlapDays(a.window, b.window, asOf),
      bothPast: Boolean(asOf && a.inServiceDate && b.inServiceDate && a.inServiceDate < asOf && b.inServiceDate < asOf),
      shared: sharedStations(a, b),
      approachMiles: closestApproachMiles(a, b),
    };
    pair.score = scorePair(pair);
    pairs.push(pair);
  }
  return pairs.sort((x, y) => y.qualifies - x.qualifies || y.score.total - x.score.total || x.miles - y.miles);
}

export function gapLabel(days) {
  if (days === null) return "Timing unknown";
  if (days < 365) return `${days} days apart`;
  return `${(days / 365.25).toFixed(1)} years apart`;
}

export function overlapLabel(days) {
  if (days === null) return "Build window unknown";
  if (days === 0) return "Build windows don't overlap";
  if (days < 60) return `Build windows overlap ${days} days`;
  return `Build windows overlap ${(days / 30.44).toFixed(0)} months`;
}

// Illustrative savings from sharing mobilization, staging and access work. Inputs are shown and adjustable.
export function savingsEstimate(pair, { benchmarkPerMile, shareRate = 0.04 }) {
  const cost = p => p.cost?.total ?? (p.miles && benchmarkPerMile ? p.miles * benchmarkPerMile : null);
  const ca = cost(pair.a), cb = cost(pair.b);
  if (ca === null || cb === null) return null;
  const timing = (pair.remainingDays ?? pair.overlapDays) > 0 ? 1 : pair.gapDays !== null && pair.gapDays <= 365 ? 0.5 : 0;
  const site = pair.shared.length ? 1.5 : 1;
  return { costA: ca, costB: cb, aEstimated: pair.a.cost?.total == null, bEstimated: pair.b.cost?.total == null, timing, site, shareRate,
    low: Math.min(ca, cb) * shareRate * 0.5 * timing * site, high: Math.min(ca, cb) * shareRate * 1.5 * timing * site };
}

export function opportunityText(pair) {
  if (pair.shared.length) return `Both projects work at ${pair.shared[0].b}: one outage plan, one set of mats and one laydown yard could serve both.`;
  if (pair.overlapDays > 0 && pair.miles < 10) return "Close by and building at the same time: share staging, deliveries and specialist crews.";
  if (pair.overlapDays > 0) return "Building at the same time in the same region: coordinate crews, equipment and procurement.";
  if (pair.miles < 5) return "Nearby work: investigate staging space, deliveries, and specialist equipment.";
  return "Within regional coordination range: investigate crews and equipment scheduling.";
}
