import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { matchProjects, milesBetween, dateGapDays } from "../src/match.js";

const projects = JSON.parse(readFileSync(new URL("../data/projects.json", import.meta.url))).projects;

test("starter data produces six qualifying cross-utility pairs", () => {
  const pairs = matchProjects(projects);
  assert.equal(pairs.length, 6);
  assert.deepEqual(pairs.map(pair => pair.id).sort(), [
    "DESC_1__GPC_1", "DESC_2__GPC_1", "DESC_3__GPC_2",
    "DESC_3__GPC_3", "DESC_5__GPC_2", "DESC_5__GPC_3",
  ]);
  assert.ok(pairs.every(pair => pair.a.utility === "DESC" && pair.b.utility === "GPC" && pair.miles < 25));
});

test("a project can have several partners", () => {
  assert.equal(matchProjects(projects).filter(pair => pair.a.id === "DESC_3").length, 2);
});

test("25 mile threshold is strict", () => {
  const a = { lat: 0, lon: 0 };
  const b = { lat: 0, lon: 25 / 69.172 };
  const distance = milesBetween(a, b);
  assert.ok(distance > 24 && distance < 26);
  assert.equal(matchProjects([{ id: "A", utility: "DESC", center: a }, { id: "B", utility: "GPC", center: b }], distance).length, 0);
  assert.equal(matchProjects([{ id: "A", utility: "DESC", center: a }, { id: "B", utility: "GPC", center: b }], distance + 0.001).length, 1);
});

test("date gap is absolute days, not claimed construction overlap", () => {
  assert.equal(dateGapDays("2025-12-31", "2026-06-01"), 152);
  assert.equal(dateGapDays("2026-06-01", "2025-12-31"), 152);
  assert.equal(dateGapDays(null, "2025-12-31"), null);
});
