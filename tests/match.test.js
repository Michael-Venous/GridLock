import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { matchProjects, milesBetween, dateGapDays, windowOverlapDays, remainingOverlapDays, certainty, sharedStations, savingsEstimate } from "../src/match.js";

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

test("shared station needs endpoints within half a mile", () => {
  const p = (lat, lon) => ({ name: "X", point: { lat, lon } });
  assert.equal(sharedStations({ endpoints: [p(32.352, -81.175)] }, { endpoints: [p(32.354, -81.178)] }).length, 1);
  assert.equal(sharedStations({ endpoints: [p(32.352, -81.175)] }, { endpoints: [p(32.40, -81.175)] }).length, 0);
});

test("savings estimate needs overlapping timing and a cost on both sides", () => {
  const pair = { a: { cost: { total: 10e6 } }, b: { miles: 5, cost: { total: null } }, overlapDays: 100, gapDays: 10, shared: [] };
  const est = savingsEstimate(pair, { benchmarkPerMile: 2e6, shareRate: 0.04 });
  assert.equal(est.costB, 10e6);
  assert.ok(est.bEstimated);
  assert.equal(Math.round(est.low), 200000);
  assert.equal(savingsEstimate({ ...pair, overlapDays: 0, gapDays: 900 }, { benchmarkPerMile: 2e6 }).high, 0);
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
