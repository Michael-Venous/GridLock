import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { matchProjects, planOf, milesBetween, dateGapDays, windowOverlapDays, remainingOverlapDays, certainty, nearbyEndpoints, opportunityText, scenarioAvailability, savingsEstimate, scorePair, sortPairs, projectType, yardScenario, landValue, landCitation, extraPlanColors, textShade, contrastOnWhite, colorDistance, TEXT_CONTRAST, WEIGHTS, YARD_BASIS } from "../src/match.js";
import { PLAN_COLORS, PLAN_TEXT_COLORS, PLAN_PRINT_COLORS, ENV_COLORS } from "../src/palette.js";

const starter = JSON.parse(readFileSync(new URL("../data/starter_projects.json", import.meta.url))).projects;

// The sponsor's own answer key: Projects_Overlaps.xlsx, sheet "overlaps".
const SPONSOR_ROWS = [
  ["DESC_2", "GPC_1", 4.09, 3074], ["DESC_3", "GPC_2", 5.65, 152], ["DESC_3", "GPC_3", 7.55, 517],
  ["DESC_1", "GPC_1", 8.01, 3074], ["DESC_5", "GPC_2", 14.34, 365], ["DESC_5", "GPC_3", 14.81, 730],
];

test("reproduces every row of the sponsor's overlap sheet exactly", () => {
  const pairs = matchProjects(starter);
  assert.equal(pairs.length, SPONSOR_ROWS.length);
  for (const [a, b, miles, gap] of SPONSOR_ROWS) {
    const pair = pairs.find(p => p.a.id === a && p.b.id === b);
    assert.ok(pair, `${a} x ${b} missing`);
    assert.equal(pair.miles.toFixed(2), miles.toFixed(2), `${a} x ${b} distance`);
    assert.equal(pair.gapDays, gap, `${a} x ${b} gap`);
  }
});

test("a project can have several partners", () => {
  assert.equal(matchProjects(starter).filter(pair => pair.a.id === "DESC_3").length, 2);
});

test("25 mile threshold is strict", () => {
  const a = { lat: 0, lon: 0 };
  const b = { lat: 0, lon: 25 / 69.172 };
  const distance = milesBetween(a, b);
  assert.ok(distance > 24 && distance < 26);
  const pair = (cut) => matchProjects([{ id: "A", state: "SC", center: a }, { id: "B", state: "GA", center: b }], cut);
  assert.equal(pair(distance).length, 0);
  assert.equal(pair(distance + 0.001).length, 1);
});

// Four projects a few miles apart along the river: two DESC, one Georgia, one from a third plan listed in SC.
const river = [
  { id: "D1", plan: "desc", state: "SC", center: { lat: 33.40, lon: -81.90 } },
  { id: "D2", plan: "desc", state: "SC", center: { lat: 33.42, lon: -81.92 } },
  { id: "G1", plan: "ga", state: "GA", center: { lat: 33.45, lon: -82.00 } },
  { id: "X1", plan: "third", state: "SC", center: { lat: 33.38, lon: -81.95 } },
];

test("three plans: every cross-plan pair in range, none within a plan", () => {
  const ids = matchProjects(river).map(p => p.id).sort();
  assert.deepEqual(ids, ["D1__G1", "D1__X1", "D2__G1", "D2__X1", "G1__X1"]);
  for (const p of matchProjects(river)) assert.notEqual(planOf(p.a), planOf(p.b));
});

test("projects in the same plan never pair, whatever their state", () => {
  const same = [{ id: "A", plan: "third", state: "SC", center: { lat: 33.4, lon: -81.9 } }, { id: "B", plan: "third", state: "GA", center: { lat: 33.4, lon: -81.91 } }];
  assert.equal(matchProjects(same).length, 0);
  assert.equal(matchProjects([...same, { id: "C", plan: "ga", state: "GA", center: { lat: 33.4, lon: -81.92 } }]).length, 2);
});

test("orientation follows planOrder: a is the project whose plan comes first", () => {
  const byDefault = matchProjects(river).find(p => p.id === "G1__X1");
  assert.equal(byDefault.a.id, "G1");   // desc, ga, then plans in order of first appearance
  const flipped = matchProjects(river, 25, { planOrder: ["third", "ga", "desc"] });
  assert.deepEqual(flipped.map(p => p.id).sort(), ["G1__D1", "G1__D2", "X1__D1", "X1__D2", "X1__G1"]);
  for (const p of flipped) assert.equal(p.a.plan === "third" || (p.a.plan === "ga" && p.b.plan === "desc"), true, p.id);
  const partial = matchProjects(river, 25, { planOrder: ["third"] });
  assert.ok(partial.every(p => p.a.plan === "third" || (p.a.plan === "desc" && p.b.plan === "ga")));
});

test("data without a plan field falls back to the state: SC is DESC, GA is Georgia ITS", () => {
  assert.equal(planOf({ state: "SC" }), "desc");
  assert.equal(planOf({ state: "GA" }), "ga");
  assert.equal(planOf({ state: "SC", plan: "third" }), "third");
  const legacy = river.slice(0, 3).map(({ plan, ...p }) => p);
  assert.deepEqual(matchProjects(legacy).map(p => p.id), matchProjects(river.slice(0, 3)).map(p => p.id));
  assert.deepEqual(matchProjects(legacy, 25, { planOrder: ["ga", "desc"] }).map(p => p.a.id).sort(), ["G1", "G1"]);
});

test("data without a plan field in any other state has no plan and pairs with nothing", () => {
  assert.equal(planOf({ state: "NC" }), null);
  assert.equal(planOf({}), null);
  const nc = { id: "N1", state: "NC", center: { lat: 33.41, lon: -81.91 } };   // a mile from D1 and D2
  const legacy = [...river.slice(0, 3).map(({ plan, ...p }) => p), nc];
  assert.deepEqual(matchProjects(legacy).map(p => p.id).sort(), ["D1__G1", "D2__G1"]);
  assert.equal(matchProjects([nc, { ...nc, id: "N2" }]).length, 0);
  assert.equal(matchProjects([{ ...nc, plan: "third" }, river[0]]).length, 1);   // a plan field still counts
});

test("date gap is absolute days", () => {
  assert.equal(dateGapDays("2025-12-31", "2026-06-01"), 152);
  assert.equal(dateGapDays("2026-06-01", "2025-12-31"), 152);
  assert.equal(dateGapDays(null, "2025-12-31"), null);
});

test("build-window overlap counts shared days, zero when apart, null when unknown", () => {
  assert.equal(windowOverlapDays({ start: "2026-01-01", end: "2026-12-31" }, { start: "2026-12-01", end: "2027-06-01" }), 30);
  assert.equal(windowOverlapDays({ start: "2026-01-01", end: "2026-06-01" }, { start: "2027-01-01", end: "2027-06-01" }), 0);
  assert.equal(windowOverlapDays({ start: null, end: "2026-06-01" }, { start: "2027-01-01", end: "2027-06-01" }), null);
});

test("only the part of an overlap still ahead counts toward ranking", () => {
  const a = { start: "2025-01-01", end: "2026-12-31" }, b = { start: "2025-06-01", end: "2027-06-01" };
  assert.equal(remainingOverlapDays(a, b, "2026-09-26"), 96);
  assert.equal(remainingOverlapDays(a, { start: "2025-01-01", end: "2026-06-01" }, "2026-09-26"), 0);
});

test("location uncertainty separates robust from sensitive and possible pairs", () => {
  assert.equal(certainty(20, { radiusMi: 1 }, { radiusMi: 1 }), "robust");
  assert.equal(certainty(20, { radiusMi: 4 }, { radiusMi: 2 }), "sensitive");
  assert.equal(certainty(27, { radiusMi: 4 }, { radiusMi: 2 }), "possible");
  assert.equal(certainty(40, { radiusMi: 4 }, { radiusMi: 2 }), "none");
  const far = [{ id: "A", state: "SC", center: { lat: 0, lon: 0 }, radiusMi: 5 }, { id: "B", state: "GA", center: { lat: 0, lon: 27 / 69.172 }, radiusMi: 0 }];
  assert.equal(matchProjects(far).length, 0);
  assert.equal(matchProjects(far, 25, { includePossible: true })[0].qualifies, false);
});

test("nearby or identically named endpoints do not establish shared construction", () => {
  const p = (name, lat, lon) => ({ name, point: { lat, lon } });
  const a = { endpoints: [p("McIntosh", 32.352, -81.175)] };
  const b = { endpoints: [p("West McIntosh", 32.354, -81.178)] };
  assert.equal(nearbyEndpoints(a, b).length, 1);
  assert.equal(nearbyEndpoints(a, { endpoints: [p("X", 32.40, -81.175)] }).length, 0);
});

test("current McIntosh leads remain geographic candidates without claiming a common worksite", () => {
  const real = JSON.parse(readFileSync(new URL("../data/projects.json", import.meta.url))).projects;
  const pairs = matchProjects(real, 25, { asOf: "2026-09-26" });
  for (const id of ["GA-21275", "GA-21257", "GA-20065"]) {
    const pair = pairs.find(p => p.a.id === "DESC-6888" && p.b.id === id);
    assert.ok(pair?.qualifies, `DESC-6888 × ${id} remains a geographic candidate`);
    assert.ok(pair.nearbyEndpoints.length > 0);
    assert.match(opportunityText(pair), /confirm the actual worksites/);
  }
});

test("savings estimate needs overlapping timing and a cost on both sides", () => {
  const pair = { qualifies: true, a: { cost: { total: 10e6 } }, b: { miles: 5, cost: { total: null } }, overlapDays: 100, gapDays: 10 };
  const est = savingsEstimate(pair, { benchmarkPerMile: 2e6, shareRate: 0.04 });
  assert.equal(est.costB, 10e6);
  assert.ok(est.bEstimated);
  assert.equal(Math.round(est.low), 200000);
  assert.equal(savingsEstimate({ ...pair, overlapDays: 0, gapDays: 10 }, { benchmarkPerMile: 2e6 }), null);
  assert.equal(savingsEstimate({ ...pair, b: { cost: {} } }, { benchmarkPerMile: 2e6 }), null);
});

test("real data: every project cites a source page and every located project has an uncertainty radius", () => {
  const real = JSON.parse(readFileSync(new URL("../data/projects.json", import.meta.url))).projects;
  assert.ok(real.length > 300);
  for (const p of real) {
    assert.ok(p.source?.url && p.source?.page, `${p.id} lacks a source page`);
    if (p.center) assert.ok(p.radiusMi > 0, `${p.id} lacks radius`);
  }
});

test("real data: every project names one of the listed plans, DESC and Georgia ITS first", () => {
  const data = JSON.parse(readFileSync(new URL("../data/projects.json", import.meta.url)));
  const ids = data.plans.map(p => p.id);
  assert.deepEqual(ids.slice(0, 2), ["desc", "ga"]);
  for (const p of data.projects) assert.ok(ids.includes(p.plan), p.id);
  for (const plan of data.plans) assert.equal(plan.projects, data.projects.filter(p => p.plan === plan.id).length, plan.id);
});

test("geography outweighs timing in the score, as the challenge asks", () => {
  assert.equal(WEIGHTS.proximity + WEIGHTS.timing, 100);
  assert.ok(WEIGHTS.proximity > WEIGHTS.timing);
  const base = { certainty: "robust" };
  const nearNoOverlap = scorePair({ ...base, miles: 2, remainingDays: 0, overlapDays: 0, gapDays: 1000 });
  const farFullOverlap = scorePair({ ...base, miles: 22, remainingDays: 730, overlapDays: 730, gapDays: 0 });
  assert.ok(nearNoOverlap.total > farFullOverlap.total);
});

test("sorts: closest in time orders by in-service gap and keeps qualifying pairs first", () => {
  const mk = (id, miles, gapDays, qualifies = true) => ({ id, miles, gapDays, qualifies, remainingDays: 0, score: { total: 100 - miles } });
  const pairs = [mk("a", 3, 900), mk("b", 20, 10), mk("c", 26, 0, false), mk("d", 10, null)];
  assert.deepEqual(sortPairs([...pairs], "time").map(p => p.id), ["b", "a", "d", "c"]);
  assert.deepEqual(sortPairs([...pairs], "distance").map(p => p.id), ["a", "d", "b", "c"]);
});

test("project type comes from the filing's own title", () => {
  assert.equal(projectType({ name: "Okatie – McIntosh 115kV Tie: Add Series Reactor" }), "Reactive device");
  assert.equal(projectType({ name: "SAV: MCINTOSH 230 kV BREAKER CONTROL RELAY UPGRADES" }), "Protection & control");
  assert.equal(projectType({ name: "Modoc – McCormick 115/46 kV Rebuild" }), "Line rebuild / reconductor");
  assert.equal(projectType({ name: "Jasper – Okatie 230 kV #2: Construct", description: "Construct a 230 kV line with B1272 ACSR from Jasper to Okatie." }), "New line or tap");
});

test("staging-yard scenario: cited unit costs, half to one avoided yard, nothing without overlap ahead", () => {
  const pair = { qualifies: true, remainingDays: 365, a: { state: "SC" }, b: { state: "GA" } };
  const sc = yardScenario(pair, { acres: 4, months: 12, leaseRate: 0.1, surfacePerAcre: 10000, roadMiles: 0.5 });
  const land = (YARD_BASIS.landPerAcre.byState.GA.value + YARD_BASIS.landPerAcre.byState.SC.value) / 2;
  assert.equal(sc.oneYard, 4 * 10000 + 4 * land * 0.1 + 0.5 * YARD_BASIS.roadPerMile.value);
  assert.equal(sc.low, sc.oneYard / 2);
  assert.equal(sc.high, sc.oneYard);
  assert.equal(yardScenario({ qualifies: true, remainingDays: 0 }).high, 0);
  assert.equal(yardScenario(pair).months, 12);
});

test("yard land value: USDA's pasture figure for every state its table lists, each cited to its page", () => {
  const { byState, unitedStates } = YARD_BASIS.landPerAcre;
  // Land Values 2026 Summary, "Pasture Average Value per Acre", 2026 column: 48 states (not Alaska or Hawaii).
  assert.equal(Object.keys(byState).length, 48);
  assert.ok(!byState.AK && !byState.HI);
  assert.deepEqual(byState.GA, { value: 5100, page: 15 });
  assert.deepEqual(byState.SC, { value: 4500, page: 15 });
  assert.deepEqual(byState.AL, { value: 3500, page: 15 });
  assert.deepEqual(byState.FL, { value: 7650, page: 15 });
  assert.deepEqual(byState.NC, { value: 6380, page: 14 });
  assert.deepEqual(byState.TN, { value: 5910, page: 14 });
  assert.deepEqual(byState.VA, { value: 5430, page: 14 });
  assert.deepEqual(unitedStates, { value: 2000, page: 15 });
  assert.equal(landCitation([15]).source, "USDA NASS, Land Values 2026 Summary (July 2026), p. 15: pasture average value per acre");
  assert.match(landCitation([14, 15]).url, /land0726\.pdf#page=14$/);
  assert.equal(landCitation([14, 15]).where, "pp. 14–15");
});

test("yard land value comes from the pair's own states, and says which state has no figure", () => {
  const { byState, unitedStates } = YARD_BASIS.landPerAcre;
  const GA = byState.GA.value, SC = byState.SC.value, NC = byState.NC.value, TN = byState.TN.value;
  const pair = (a, b) => ({ qualifies: true, remainingDays: 365, a: { state: a }, b: { state: b } });
  const brief = land => ({ perAcre: land.perAcre, states: land.states, pages: land.pages, missing: land.missing, unstated: land.unstated, standIn: land.standIn });
  assert.deepEqual(brief(landValue(pair("SC", "GA"))), { perAcre: (GA + SC) / 2, states: ["GA", "SC"], pages: [15], missing: [], unstated: 0, standIn: false });
  assert.deepEqual(brief(landValue(pair("SC", "SC"))), { perAcre: SC, states: ["SC"], pages: [15], missing: [], unstated: 0, standIn: false });
  assert.deepEqual(brief(landValue(pair("NC", "GA"))), { perAcre: (GA + NC) / 2, states: ["GA", "NC"], pages: [14, 15], missing: [], unstated: 0, standIn: false });
  assert.deepEqual(brief(landValue(pair("NC", "TN"))), { perAcre: (NC + TN) / 2, states: ["NC", "TN"], pages: [14], missing: [], unstated: 0, standIn: false });
  // A state the report doesn't list, or no state at all: the other project's state stands in, else the US average.
  assert.deepEqual(brief(landValue(pair("AK", "GA"))), { perAcre: GA, states: ["GA"], pages: [15], missing: ["AK"], unstated: 0, standIn: false });
  assert.deepEqual(brief(landValue(pair("AK", "HI"))), { perAcre: unitedStates.value, states: [], pages: [15], missing: ["AK", "HI"], unstated: 0, standIn: true });
  assert.deepEqual(brief(landValue(pair(null, "GA"))), { perAcre: GA, states: ["GA"], pages: [15], missing: [], unstated: 1, standIn: false });
  assert.deepEqual(brief(landValue(pair(undefined, null))), { perAcre: unitedStates.value, states: [], pages: [15], missing: [], unstated: 2, standIn: true });
  assert.deepEqual(landValue(pair("SC", "GA")).figures, [{ name: "GA", value: GA, page: 15 }, { name: "SC", value: SC, page: 15 }]);
  assert.deepEqual(landValue(pair(null, null)).figures, [{ name: "United States", ...unitedStates }]);
  const sc = yardScenario(pair("SC", "SC"), { acres: 4, months: 12, leaseRate: 0.1 });
  assert.equal(sc.parts.lease, 4 * SC * 0.1);
  assert.equal(sc.land.states[0], "SC");
});

// Every color the app draws in: palette.js, plus styles.css's own text shades (which the app reads from the page).
const cssColors = Object.fromEntries([...readFileSync(new URL("../styles.css", import.meta.url), "utf8").matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})\b/gi)].map(m => [m[1], m[2].toLowerCase()]));
const JND = 0.02;   // OKLab distance that is just noticeable

test("styles.css repeats the palette's plan fills and ground-layer colors", () => {
  for (const [side, fill] of Object.entries(PLAN_COLORS)) assert.equal(cssColors[side], fill, side);
  for (const [layer, color] of Object.entries(ENV_COLORS)) if (cssColors[`env-${layer}`]) assert.equal(cssColors[`env-${layer}`], color, layer);
});

test("plan colors: a generated plan's text shade is made the way the fixed ones are", () => {
  for (const side of ["desc", "plan-x1", "plan-x2", "plan-x3"]) {
    const d = colorDistance(textShade(PLAN_COLORS[side]), PLAN_TEXT_COLORS[side]);
    assert.ok(d.normal < JND, `${side}: ${textShade(PLAN_COLORS[side])} vs ${PLAN_TEXT_COLORS[side]}`);
  }
  assert.equal(textShade(PLAN_COLORS["plan-x3"]), PLAN_COLORS["plan-x3"]);   // navy already reads at 7:1
  for (const hex of ["#d4326b", "#0a8494", "#cb6e30", "#3fb6d0"]) assert.ok(contrastOnWhite(textShade(hex)) >= TEXT_CONTRAST, hex);
});

test("plan colors: generated ones for plans past the fixed five are readable, stable, and apart from every color in use", () => {
  const fixed = Object.values(PLAN_COLORS);
  const inUse = [...Object.values(PLAN_TEXT_COLORS), ...Object.values(PLAN_PRINT_COLORS), ...Object.values(ENV_COLORS),
    ...Object.keys(PLAN_COLORS).map(side => cssColors[`${side}-text`])];
  assert.ok(inUse.every(Boolean));
  assert.deepEqual(extraPlanColors(fixed, 0, inUse), []);
  const six = extraPlanColors(fixed, 6, inUse);   // eleven plans
  assert.deepEqual(extraPlanColors(fixed, 2, inUse), six.slice(0, 2));   // adding a plan keeps the others' colors
  assert.equal(new Set(six.map(c => c.fill)).size, 6);
  const pairs = fixed.flatMap((x, i) => fixed.slice(i + 1).map(y => colorDistance(x, y)));
  const floor = { normal: Math.min(...pairs.map(d => d.normal)), worst: Math.min(...pairs.map(d => d.worst)) };
  six.forEach(({ fill, text }, i) => {
    assert.ok(contrastOnWhite(fill) >= 4.5, `${fill} contrast ${contrastOnWhite(fill)}`);
    assert.equal(text, textShade(fill));
    assert.ok(contrastOnWhite(text) >= TEXT_CONTRAST, `${text} contrast ${contrastOnWhite(text)}`);
    // Its fill and its text shade are noticeably apart from every other color on the page, to normal vision and to
    // the dichromat who sees them closest: other plans' fills and text shades, print colors and ground layers.
    const others = [...fixed, ...inUse, ...six.filter((_, j) => j !== i).flatMap(c => [c.fill, c.text])];
    for (const own of new Set([fill, text])) for (const other of others) {
      const d = colorDistance(own, other);
      assert.ok(d.normal > JND && d.worst > JND, `plan ${i + 6} ${own} vs ${other}: ${JSON.stringify(d)}`);
    }
  });
  // The sixth plan's color is as far from every color in use as the fixed palette's closest pair of fills are apart.
  for (const own of new Set([six[0].fill, six[0].text])) for (const other of [...fixed, ...inUse]) {
    const d = colorDistance(own, other);
    assert.ok(d.normal >= floor.normal && d.worst >= floor.worst, `${own} vs ${other}: ${JSON.stringify(d)}, floor ${JSON.stringify(floor)}`);
  }
});

test("nonqualifying pairs cannot receive modeled savings through either public cost helper", () => {
  const real = JSON.parse(readFileSync(new URL("../data/projects.json", import.meta.url))).projects;
  const pair = matchProjects(real, 25, { includePossible: true, asOf: "2026-09-26" }).find(p => p.a.id === "DESC-6808L" && p.b.id === "GA-21275");
  assert.ok(pair && !pair.qualifies && pair.remainingDays > 0);
  const available = scenarioAvailability(pair);
  assert.equal(available.available, false);
  assert.match(available.reason, /qualification rule/);
  const sc = yardScenario(pair, { months: 12 });
  assert.equal(sc.active, false);
  assert.equal(sc.low, 0);
  assert.equal(sc.high, 0);
  assert.equal(savingsEstimate(pair, { benchmarkPerMile: 2e6 }), null);
  assert.equal(scenarioAvailability({ remainingDays: 365 }).available, false);
});

test("zero or invalid sharing months cannot produce positive shared-yard savings", () => {
  const pair = { qualifies: true, remainingDays: 365 };
  for (const months of [0, -1, NaN, Infinity]) {
    const sc = yardScenario(pair, { months });
    assert.equal(sc.active, false);
    assert.equal(sc.low, 0);
    assert.equal(sc.high, 0);
    assert.ok(Number.isFinite(sc.oneYard));
    assert.match(sc.reason, /positive number of shared months/);
  }
  assert.equal(scenarioAvailability({ ...pair, remainingDays: 0, overlapDays: 365 }).available, false);
  assert.equal(yardScenario({ ...pair, remainingDays: 1 }).active, true);
});
