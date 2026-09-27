import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { matchProjects, planOf, milesBetween, dateGapDays, windowOverlapDays, remainingOverlapDays, certainty, nearbyEndpoints, opportunityText, scenarioAvailability, savingsEstimate, scorePair, sortPairs, projectType, yardScenario, landValue, extraPlanColors, contrastOnWhite, colorDistance, WEIGHTS, YARD_BASIS } from "../src/match.js";

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

test("three plans: pairs cross both plans and state lines", () => {
  const ids = matchProjects(river).map(p => p.id).sort();
  assert.deepEqual(ids, ["D1__G1", "D2__G1", "G1__X1"]);
  for (const p of matchProjects(river)) { assert.notEqual(planOf(p.a), planOf(p.b)); assert.notEqual(p.a.state, p.b.state); }
});

test("projects in the same plan never pair, whatever their state", () => {
  const same = [{ id: "A", plan: "third", state: "SC", center: { lat: 33.4, lon: -81.9 } }, { id: "B", plan: "third", state: "GA", center: { lat: 33.4, lon: -81.91 } }];
  assert.equal(matchProjects(same).length, 0);
  assert.equal(matchProjects([...same, { id: "C", plan: "ga", state: "GA", center: { lat: 33.4, lon: -81.92 } }]).length, 1);
});

test("orientation follows planOrder: a is the project whose plan comes first", () => {
  const byDefault = matchProjects(river).find(p => p.id === "G1__X1");
  assert.equal(byDefault.a.id, "G1");   // desc, ga, then plans in order of first appearance
  const flipped = matchProjects(river, 25, { planOrder: ["third", "ga", "desc"] });
  assert.deepEqual(flipped.map(p => p.id).sort(), ["G1__D1", "G1__D2", "X1__G1"]);
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

test("real data: every project names one of the registered plans", () => {
  const data = JSON.parse(readFileSync(new URL("../data/projects.json", import.meta.url)));
  const ids = data.plans.map(p => p.id);
  const registry = JSON.parse(readFileSync(new URL("../data/filings.json", import.meta.url)));
  assert.deepEqual([...ids].sort(), [...new Set(registry.filings.map(f => f.plan))].sort());
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
  const land = (YARD_BASIS.landPerAcre.GA + YARD_BASIS.landPerAcre.SC) / 2;
  assert.equal(sc.oneYard, 4 * 10000 + 4 * land * 0.1 + 0.5 * YARD_BASIS.roadPerMile.value);
  assert.equal(sc.low, sc.oneYard / 2);
  assert.equal(sc.high, sc.oneYard);
  assert.equal(yardScenario({ qualifies: true, remainingDays: 0 }).high, 0);
  assert.equal(yardScenario(pair).months, 12);
});

test("yard land value comes from the pair's own states, and says which state has no figure", () => {
  const { GA, SC } = YARD_BASIS.landPerAcre;
  const pair = (a, b) => ({ qualifies: true, remainingDays: 365, a: { state: a }, b: { state: b } });
  assert.deepEqual(landValue(pair("SC", "GA")), { perAcre: (GA + SC) / 2, states: ["GA", "SC"], missing: [], standIn: false });
  assert.deepEqual(landValue(pair("SC", "SC")), { perAcre: SC, states: ["SC"], missing: [], standIn: false });
  assert.deepEqual(landValue(pair("NC", "GA")), { perAcre: GA, states: ["GA"], missing: ["NC"], standIn: false });
  assert.deepEqual(landValue(pair("NC", "TN")), { perAcre: (GA + SC) / 2, states: ["GA", "SC"], missing: ["NC", "TN"], standIn: true });
  const sc = yardScenario(pair("SC", "SC"), { acres: 4, months: 12, leaseRate: 0.1 });
  assert.equal(sc.parts.lease, 4 * SC * 0.1);
  assert.equal(sc.land.states[0], "SC");
});

test("plan colors: generated ones for plans past the fixed five are readable, distinct and stable", () => {
  const fixed = ["#0a8494", "#cb6e30", "#7b4fb0", "#8b5a2b", "#2a3f7a"];   // styles.css and src/app.js
  assert.deepEqual(extraPlanColors(fixed, 0), []);
  const seven = [...fixed, ...extraPlanColors(fixed, 2)];
  assert.equal(new Set(seven).size, 7);
  assert.deepEqual(extraPlanColors(fixed, 4).slice(0, 2), seven.slice(5));   // adding a plan keeps the others' colors
  // With seven plans, each generated color reads as text on white and sits at least as far from every other plan's
  // color as the fixed palette's closest pair does, both to normal vision and to the dichromat who sees them closest.
  const pairs = fixed.flatMap((x, i) => fixed.slice(i + 1).map(y => colorDistance(x, y)));
  const floor = { normal: Math.min(...pairs.map(d => d.normal)), worst: Math.min(...pairs.map(d => d.worst)) };
  for (const c of seven.slice(5)) {
    assert.ok(contrastOnWhite(c) >= 4.5, `${c} contrast ${contrastOnWhite(c)}`);
    for (const other of seven) if (other !== c) {
      const d = colorDistance(c, other);
      assert.ok(d.normal >= floor.normal && d.worst >= floor.worst, `${c} vs ${other}: ${JSON.stringify(d)}, floor ${JSON.stringify(floor)}`);
    }
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

test("an unpublished build window says so instead of claiming no overlap remains", () => {
  const unknown = scenarioAvailability({ qualifies: true, remainingDays: null, overlapDays: null });
  assert.equal(unknown.available, false);
  assert.match(unknown.reason, /build window isn't published/);
  assert.match(scenarioAvailability({ qualifies: true, remainingDays: 0, overlapDays: 0 }).reason, /No overlapping planning window remains/);
});

test("Santee Cooper projects pair with Georgia like DESC's, and never with each other", () => {
  const sc = { id: "SCPSA-X", state: "SC", utility: "SCPSA", center: { lat: 32.35, lon: -81.17 } };
  const desc = { id: "DESC-Y", state: "SC", utility: "DESC", center: { lat: 32.35, lon: -81.17 } };
  const ga = { id: "GA-1", state: "GA", utility: "SAV", center: { lat: 32.36, lon: -81.18 } };
  assert.deepEqual(matchProjects([sc, desc, ga]).map(p => p.id).sort(), ["DESC-Y__GA-1", "SCPSA-X__GA-1"]);
});
