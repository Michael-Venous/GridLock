import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { matchProjects, milesBetween, dateGapDays, windowOverlapDays, remainingOverlapDays, certainty, sharedStations, nearbyEndpoints, closestApproachMiles, opportunityText, scenarioAvailability, savingsEstimate, scorePair, sortPairs, projectType, yardScenario, WEIGHTS, YARD_BASIS } from "../src/match.js";

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
  assert.equal(sharedStations(a, b).length, 0);
  assert.equal(sharedStations(a, a).length, 0);
  assert.equal(nearbyEndpoints(a, { endpoints: [p("X", 32.40, -81.175)] }).length, 0);
});

test("shared worksite bonus requires a common site identity and sourced scope on both projects", () => {
  const site = { siteId: "station-1", name: "Station One", verified: true, evidence: { source: { url: "https://example.com/plan.pdf", page: 2 }, quote: "Replace breakers at Station One." } };
  const a = { id: "SC", state: "SC", center: { lat: 33, lon: -81 }, worksites: [site] };
  const b = { id: "GA", state: "GA", center: a.center, worksites: [{ ...site, name: "Station 1" }] };
  const pair = matchProjects([a, b])[0];
  assert.equal(pair.shared.length, 1);
  assert.equal(pair.shared[0].siteId, "station-1");
  assert.equal(pair.shared[0].evidence.a.quote, site.evidence.quote);
  assert.equal(pair.score.parts.shared, WEIGHTS.shared);
  for (const unconfirmed of [{ ...site, verified: false }, { ...site, evidence: {} }, { ...site, evidence: { source: "https://example.com/plan.pdf" } }, { ...site, siteId: "nearby-station" }]) {
    assert.equal(matchProjects([a, { ...b, worksites: [unconfirmed] }])[0].score.parts.shared, 0);
  }
});

test("current McIntosh leads remain geographic candidates without claiming a common worksite", () => {
  const real = JSON.parse(readFileSync(new URL("../data/projects.json", import.meta.url))).projects;
  const pairs = matchProjects(real, 25, { asOf: "2026-09-26" });
  for (const id of ["GA-21275", "GA-21257", "GA-20065"]) {
    const pair = pairs.find(p => p.a.id === "DESC-6888" && p.b.id === id);
    assert.ok(pair?.qualifies, `DESC-6888 × ${id} remains a geographic candidate`);
    assert.ok(pair.nearbyEndpoints.length > 0);
    assert.equal(pair.shared.length, 0);
    assert.equal(pair.score.parts.shared, 0);
    assert.match(opportunityText(pair), /confirm the actual worksites/);
    assert.match(pair.score.notes.join(" "), /no shared-worksite bonus/);
  }
});

test("corridor bonuses require source-backed verified routes for both projects", () => {
  const evidence = { source: "https://example.com/circuit-plan.pdf", quote: "Construction follows the reviewed circuit route." };
  const route = { coords: [[33, -81], [33, -80.99]], verified: true, evidence };
  const a = { id: "A", state: "SC", center: { lat: 33, lon: -81 }, route };
  const b = { id: "B", state: "GA", center: a.center, route };
  assert.equal(matchProjects([a, b])[0].score.parts.corridor, WEIGHTS.corridor);
  for (const unconfirmed of [undefined, { ...route, verified: false }, { coords: route.coords }, { ...route, evidence: undefined }]) {
    const pair = matchProjects([a, { ...b, route: unconfirmed }])[0];
    assert.equal(pair.approachMiles, 0);
    assert.equal(pair.corridorVerified, false);
    assert.equal(pair.score.parts.corridor, 0);
    assert.match(pair.score.notes.join(" "), /no corridor bonus/);
  }
});

test("closest approach detects crossing, touching and collinear line segments", () => {
  const route = coords => ({ route: { coords } });
  assert.equal(closestApproachMiles(route([[32, -81], [34, -81]]), route([[33, -82], [33, -80]])), 0);
  assert.equal(closestApproachMiles(route([[33, -81], [33, -80]]), route([[33, -80], [34, -80]])), 0);
  assert.equal(closestApproachMiles(route([[33, -81], [33, -80]]), route([[33, -80.5], [33, -79.5]])), 0);
  assert.ok(closestApproachMiles(route([[33, -81], [33, -80]]), route([[33, -79], [33, -78]])) > 50);
});

test("closest approach handles parallel lines, point-to-line, point-to-point and missing geometry", () => {
  const route = coords => ({ route: { coords } });
  const parallel = closestApproachMiles(route([[32, -81], [34, -81]]), route([[32, -80], [34, -80]]));
  assert.ok(parallel > 50 && parallel < 60);
  assert.equal(closestApproachMiles({ center: { lat: 33, lon: -81 } }, route([[32, -81], [34, -81]])), 0);
  const a = { lat: 33, lon: -81 }, b = { lat: 33, lon: -80.99 };
  assert.ok(Math.abs(closestApproachMiles({ center: a }, { center: b }) - milesBetween(a, b)) < 0.00001);
  assert.equal(closestApproachMiles({}, { center: a }), null);
});

test("savings estimate needs overlapping timing and a cost on both sides", () => {
  const pair = { qualifies: true, a: { cost: { total: 10e6 } }, b: { miles: 5, cost: { total: null } }, overlapDays: 100, gapDays: 10, shared: [] };
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

test("geography outweighs timing in the score, as the challenge asks", () => {
  assert.equal(WEIGHTS.proximity + WEIGHTS.timing, 100, "shared/corridor are a bonus on top of 100, not part of the core split");
  assert.ok(WEIGHTS.proximity > WEIGHTS.timing);
  const base = { shared: [], approachMiles: null, certainty: "robust" };
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
  const pair = { qualifies: true, remainingDays: 365 };
  const sc = yardScenario(pair, { acres: 4, months: 12, leaseRate: 0.1, surfacePerAcre: 10000, roadMiles: 0.5 });
  const land = (YARD_BASIS.landPerAcre.GA + YARD_BASIS.landPerAcre.SC) / 2;
  assert.equal(sc.oneYard, 4 * 10000 + 4 * land * 0.1 + 0.5 * YARD_BASIS.roadPerMile.value);
  assert.equal(sc.low, sc.oneYard / 2);
  assert.equal(sc.high, sc.oneYard);
  assert.equal(yardScenario({ qualifies: true, remainingDays: 0 }).high, 0);
  assert.equal(yardScenario(pair).months, 12);
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
