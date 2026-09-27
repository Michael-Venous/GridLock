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

// Nearby title endpoints identify leads to investigate, not where construction takes place.
export function nearbyEndpoints(a, b) {
  const out = [];
  for (const x of a.endpoints ?? []) for (const y of b.endpoints ?? []) {
    if (x.point && y.point && milesBetween(x.point, y.point) <= 0.5) out.push({ a: x.name, b: y.name, miles: milesBetween(x.point, y.point) });
  }
  return out;
}

// Ranking score. The challenge makes geography the primary signal and timing a strong secondary one, so the
// full 100 points split proximity 60 / timing 40.
export const WEIGHTS = { proximity: 60, timing: 40 };
export function scorePair(pair) {
  const proximity = WEIGHTS.proximity * Math.max(0, 1 - pair.miles / MAX_MILES);
  let timing = 0;
  const ahead = pair.remainingDays ?? pair.overlapDays;
  if (ahead > 0) timing = WEIGHTS.timing * Math.min(1, 0.5 + ahead / 730);
  else if (pair.gapDays !== null) timing = WEIGHTS.timing / 2 * Math.max(0, 1 - pair.gapDays / 1095);
  const confidence = pair.certainty === "robust" ? 1 : 0.85;
  const total = Math.round((proximity + timing) * confidence);
  return { total, parts: { proximity: Math.round(proximity), timing: Math.round(timing), confidence } };
}

// The plan (one utility's series of filings) a project comes from. Data built before plans existed carries only
// the state: SC was DESC's list, GA the Georgia ITS plan. Such data had no other plan, so a project in any other
// state has none (null) and never pairs.
const LEGACY_PLANS = { SC: "desc", GA: "ga" };
export const planOf = project => project.plan ?? LEGACY_PLANS[project.state] ?? null;

// Projects pair only across plans. `a` is the one whose plan comes first in planOrder; plans it doesn't name
// follow in order of first appearance.
export function matchProjects(projects, cutoff = MAX_MILES, { includePossible = false, asOf = null, planOrder = ["desc", "ga"] } = {}) {
  const groups = new Map(planOrder.map(plan => [plan, []]));
  for (const p of projects) {
    const plan = planOf(p);
    if (!p.center || !plan) continue;
    if (!groups.has(plan)) groups.set(plan, []);
    groups.get(plan).push(p);
  }
  const plans = [...groups.values()], pairs = [];
  for (let i = 0; i < plans.length; i++) for (let j = i + 1; j < plans.length; j++) for (const a of plans[i]) for (const b of plans[j]) {
    const miles = milesBetween(a.center, b.center);
    const level = certainty(miles, a, b, cutoff);
    if (level === "none" || (level === "possible" && !includePossible)) continue;
    const pair = {
      id: `${a.id}__${b.id}`, a, b, miles, certainty: level, qualifies: miles < cutoff,
      gapDays: dateGapDays(a.inServiceDate, b.inServiceDate),
      overlapDays: windowOverlapDays(a.window, b.window),
      remainingDays: remainingOverlapDays(a.window, b.window, asOf),
      bothPast: Boolean(asOf && a.inServiceDate && b.inServiceDate && a.inServiceDate < asOf && b.inServiceDate < asOf),
      nearbyEndpoints: nearbyEndpoints(a, b),
    };
    pair.score = scorePair(pair);
    pairs.push(pair);
  }
  return sortPairs(pairs, "score");
}

// Sort orders offered in the UI. Qualifying pairs always come before possible ones.
export const SORTS = {
  score: { label: "Score", cmp: (x, y) => y.score.total - x.score.total || x.miles - y.miles },
  distance: { label: "Distance", cmp: (x, y) => x.miles - y.miles },
  time: { label: "Closest in time", cmp: (x, y) => (x.gapDays ?? Infinity) - (y.gapDays ?? Infinity) || x.miles - y.miles },
  overlap: { label: "Most overlap ahead", cmp: (x, y) => (y.remainingDays ?? -1) - (x.remainingDays ?? -1) || x.miles - y.miles },
};
export function sortPairs(pairs, key = "score") {
  const cmp = (SORTS[key] ?? SORTS.score).cmp;
  return pairs.sort((x, y) => y.qualifies - x.qualifies || cmp(x, y));
}

// Rough project type from the filing's own title and description, for side-by-side comparison.
const TYPES = [
  ["Reactive device", /\b(reactors?|capacitors?|capacitor banks?|statcom|svc|synchronous condensers?)\b/i],
  ["Protection & control", /\b(relays?|protection|scada|rtu|sw(itch)? house)\b/i],
  ["Line rebuild / reconductor", /\b(rebuild|reconductor|uprate|re-?rate)\b/i],
  ["Line relocation / structures", /\b(move line|relocat\w*|river crossing|structures|angles|dead ends)\b/i],
  ["New line or tap", /\b(new|construct\w*|build|add)\b.*\b(line|tie|tap|spdc)\b|\b(line|tie|tap)\b.*\bconstruct/i],
  ["Terminal equipment / limiting element", /\b(jumpers?|switch(es)?|line traps?|trap|terminal equipment|limiting elements?|equipment upgrade|buses)\b/i],
  ["Substation / switching station", /\b(substation|switching station|switchyard|transformers?|bus|breakers?|sub)\b/i],
];
export function projectType(project) {
  const text = `${project.name ?? ""} ${project.description ?? ""}`;
  const title = project.name ?? "";
  for (const [label, re] of TYPES) if (re.test(title)) return label;
  for (const [label, re] of TYPES) if (re.test(text)) return label;
  return "Other / unspecified";
}

export function gapLabel(days) {
  if (days === null) return "Timing unknown";
  if (days < 365) return `${days} days apart`;
  return `${(days / 365.25).toFixed(1)} years apart`;
}

export function overlapLabel(days) {
  if (days === null) return "Planning window unknown";
  if (days === 0) return "Planning windows don't overlap";
  if (days < 60) return `Planning windows overlap ${days} days`;
  return `Planning windows overlap ${(days / 30.44).toFixed(0)} months`;
}

// Illustrative savings from sharing mobilization, staging and access work. Inputs are shown and adjustable.
export function savingsEstimate(pair, { benchmarkPerMile, shareRate = 0.04 }) {
  if (!scenarioAvailability(pair).available) return null;
  const cost = p => p.cost?.total ?? (p.miles && benchmarkPerMile ? p.miles * benchmarkPerMile : null);
  const ca = cost(pair.a), cb = cost(pair.b);
  if (ca === null || cb === null) return null;
  return { costA: ca, costB: cb, aEstimated: pair.a.cost?.total == null, bEstimated: pair.b.cost?.total == null, shareRate,
    low: Math.min(ca, cb) * shareRate * 0.5, high: Math.min(ca, cb) * shareRate * 1.5 };
}

// Cited unit costs for the staging-yard scenario. Everything else in the scenario is a user-editable assumption.
export const YARD_BASIS = {
  landPerAcre: { GA: 5100, SC: 4500, source: "USDA NASS, Land Values 2026 Summary (July 2026), p. 15: pasture average value per acre", url: "https://www.nass.usda.gov/Publications/Todays_Reports/reports/land0726.pdf#page=15" },
  matsPerAcre: { value: 69975, source: "MISO Transmission Cost Estimation Guide for MTEP24 (May 2024), Table 2.2-9, p. 19: wetland matting and construction difficulties, per acre", url: "https://cdn.misoenergy.org/20240501%20PSC%20Item%2004%20MISO%20Transmission%20Cost%20Estimation%20Guide%20for%20MTEP24632680.pdf#page=19" },
  roadPerMile: { value: 593636, source: "MISO Transmission Cost Estimation Guide for MTEP24 (May 2024), p. 23: access road, per mile", url: "https://cdn.misoenergy.org/20240501%20PSC%20Item%2004%20MISO%20Transmission%20Cost%20Estimation%20Guide%20for%20MTEP24632680.pdf#page=23" },
};

// Shared eligibility for the on-screen scenario and every exported representation.
export function scenarioAvailability(pair, { months = null } = {}) {
  const remainingDays = pair.remainingDays ?? pair.overlapDays ?? 0;
  const duration = months ?? (remainingDays > 0 ? Math.max(1, Math.round(remainingDays / 30.44)) : 0);
  let reason = null;
  if (pair.qualifies !== true) reason = "Only modeled for pairs that meet the center-distance qualification rule.";
  else if (!(remainingDays > 0)) reason = "No overlapping planning window remains; no shared-yard savings are modeled.";
  else if (!Number.isFinite(duration) || duration <= 0) reason = "Enter a positive number of shared months to model a shared yard.";
  return { available: reason === null, reason, months: duration, remainingDays };
}

// Land value for a pair's yard: the average of the USDA figures for the pair's own states. A state with no figure
// is named in `missing`; the other state's figure stands in for it, and with no figure for either state the
// average of every cited state does (`standIn`).
export function landValue(pair) {
  const cited = Object.keys(YARD_BASIS.landPerAcre).filter(k => typeof YARD_BASIS.landPerAcre[k] === "number");
  const states = [...new Set([pair.a?.state, pair.b?.state].filter(Boolean))];
  const have = cited.filter(s => states.includes(s)), used = have.length ? have : cited;
  const perAcre = used.reduce((sum, s) => sum + YARD_BASIS.landPerAcre[s], 0) / used.length;
  return { perAcre, states: used, missing: states.filter(s => !cited.includes(s)), standIn: !have.length };
}

// One shared staging yard instead of two. A combined yard is assumed to be 1.0-1.5x the size of one project's
// yard, so the avoided cost is 0.5-1.0 of one yard. No overlap ahead means no shared yard and no saving.
export function yardScenario(pair, { acres = 5, months = null, leaseRate = 0.10, surfacePerAcre = YARD_BASIS.matsPerAcre.value, roadMiles = 0.25 } = {}) {
  const land = landValue(pair), landPerAcre = land.perAcre;
  const availability = scenarioAvailability(pair, { months });
  const m = availability.months;
  const leaseMonths = Number.isFinite(m) && m > 0 ? m : 0;
  const parts = {
    surface: acres * surfacePerAcre,
    lease: acres * landPerAcre * leaseRate * leaseMonths / 12,
    road: roadMiles * YARD_BASIS.roadPerMile.value,
  };
  const oneYard = parts.surface + parts.lease + parts.road;
  const active = availability.available;
  return { acres, months: m, leaseRate, surfacePerAcre, roadMiles, landPerAcre, land, parts, oneYard, active, reason: availability.reason,
    low: active ? oneYard * 0.5 : 0, high: active ? oneYard : 0 };
}

export function opportunityText(pair) {
  if (!pair.qualifies) return "Outside the center-distance rule: verify locations before considering a coordination scenario.";
  if (pair.nearbyEndpoints?.length) return "Near common network endpoints; confirm the actual worksites before proposing shared access, outages or staging.";
  if ((pair.remainingDays ?? pair.overlapDays) > 0 && pair.miles < 10) return "Nearby projects with overlapping planning windows: investigate staging, deliveries and specialist crews.";
  if ((pair.remainingDays ?? pair.overlapDays) > 0) return "Overlapping planning windows in the same region: investigate crews, equipment and procurement.";
  if (pair.miles < 5) return "Nearby work: investigate staging space, deliveries, and specialist equipment.";
  return "Within regional coordination range: investigate crews and equipment scheduling.";
}

// ---- Plan colors beyond the fixed, hand-checked ones ----
// A further plan gets, of the colors that read as text on white (4.5:1), no darker than the fixed palette's darkest
// and with OKLCH chroma from 0.06 (never a grey) to 0.2 (the fixed palette peaks at 0.15), the one farthest from
// every color in use. "Far" is measured twice, each relative to the fixed palette's own closest pair: as normal vision sees it,
// and as whichever of full protanopia, deuteranopia or tritanopia (Machado et al. 2009) sees it closest; the smaller
// ratio counts. Deterministic, so a plan keeps its color.
const lin = c => c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
const enc = c => c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
const mul = (m, [x, y, z]) => [0, 3, 6].map(i => m[i] * x + m[i + 1] * y + m[i + 2] * z);
const clamp = rgb => rgb.map(c => Math.min(1, Math.max(0, c)));
const hexToRgb = hex => [1, 3, 5].map(i => lin(parseInt(hex.slice(i, i + 2), 16) / 255));   // linear sRGB
const rgbToHex = rgb => `#${clamp(rgb).map(c => Math.round(enc(c) * 255).toString(16).padStart(2, "0")).join("")}`;
const TO_LMS = [0.4122214708, 0.5363325363, 0.0514459929, 0.2119034982, 0.6806995451, 0.1073969566, 0.0883024619, 0.2817188376, 0.6299787005];
const TO_LAB = [0.2104542553, 0.7936177850, -0.0040720468, 1.9779984951, -2.4285922050, 0.4505937099, 0.0259040371, 0.7827717662, -0.8086757660];
const FROM_LAB = [1, 0.3963377774, 0.2158037573, 1, -0.1055613458, -0.0638541728, 1, -0.0894841775, -1.2914855480];
const FROM_LMS = [4.0767416621, -3.3077115913, 0.2309699292, -1.2684380046, 2.6097574011, -0.3413193965, -0.0041960863, -0.7034186147, 1.7076147010];
const oklab = rgb => mul(TO_LAB, mul(TO_LMS, rgb).map(Math.cbrt));
const fromOklab = lab => mul(FROM_LMS, mul(FROM_LAB, lab).map(c => c ** 3));
const VISION = [
  [1, 0, 0, 0, 1, 0, 0, 0, 1],
  [0.152286, 1.052583, -0.204868, 0.114503, 0.786281, 0.099216, -0.003882, -0.048116, 1.051998],   // protanopia
  [0.367322, 0.860646, -0.227968, 0.280085, 0.672501, 0.047413, -0.011820, 0.042940, 0.968881],    // deuteranopia
  [1.255528, -0.076749, -0.178779, -0.078411, 0.930809, 0.147602, 0.004733, 0.691367, 0.303900],   // tritanopia
];
const luminance = rgb => 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
const seen = hex => VISION.map(m => oklab(clamp(mul(m, hexToRgb(hex)))));
const gap = (x, y, i) => Math.hypot(...x[i].map((c, k) => c - y[i][k]));
const normalGap = (x, y) => gap(x, y, 0);
const worstGap = (x, y) => Math.min(...x.map((_, i) => gap(x, y, i)));
export const contrastOnWhite = hex => 1.05 / (luminance(hexToRgb(hex)) + 0.05);
// OKLab distance between two colors, as normal vision sees it and as the dichromat who sees them closest does
// (about 0.02 is just noticeable).
export const colorDistance = (x, y) => { const a = seen(x), b = seen(y); return { normal: normalGap(a, b), worst: worstGap(a, b) }; };

function readableColors(darkest) {
  const target = 1.05 / 4.6 - 0.05, out = [];   // luminance for 4.6:1 on white, leaving room for rounding to 8-bit
  for (let hue = 0; hue < 360; hue += 5) for (let L = darkest; L < 1; L += 0.02) for (let C = 0.06; C <= 0.2001; C += 0.02) {
    const h = hue * Math.PI / 180, rgb = fromOklab([L, C * Math.cos(h), C * Math.sin(h)]);
    if (rgb.every(c => c >= -1e-4 && c <= 1 + 1e-4) && luminance(clamp(rgb)) <= target) out.push(rgbToHex(rgb));
  }
  return out;
}

// `count` colors for further plans, each as far as possible from `fixed` and from the ones picked before it.
export function extraPlanColors(fixed, count) {
  if (count <= 0) return [];
  const used = fixed.map(seen), pairs = used.flatMap((x, i) => used.slice(i + 1).map(y => [x, y]));
  const floorNormal = Math.min(...pairs.map(([x, y]) => normalGap(x, y))), floorWorst = Math.min(...pairs.map(([x, y]) => worstGap(x, y)));
  const score = (c, u) => Math.min(normalGap(c, u) / floorNormal, worstGap(c, u) / floorWorst);
  const candidates = readableColors(Math.min(...used.map(u => u[0][0]))).map(hex => ({ hex, seen: seen(hex) }));
  const out = [];
  while (out.length < count) {
    let best = null, bestScore = -1;
    for (const c of candidates) { const sc = Math.min(...used.map(u => score(c.seen, u))); if (sc > bestScore) { bestScore = sc; best = c; } }
    used.push(best.seen); out.push(best.hex);
  }
  return out;
}
