import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { pointInPolygon, milesToPolygon, inArea, changeItems, itemsIn, countByKind, boundsRing, closeRing, daysLabel } from "../src/changes.js";

const box = boundsRing([[-81.3, 32.0], [-80.8, 32.4]]);   // around Savannah and Okatie

test("point in polygon and distance to it", () => {
  assert.equal(pointInPolygon([-81.1, 32.1], box), true);
  assert.equal(pointInPolygon([-81.5, 32.1], box), false);
  assert.equal(milesToPolygon([-81.1, 32.1], box), 0);
  const d = milesToPolygon([-81.4, 32.2], box);          // 0.1 degree of longitude west of the edge
  assert.ok(d > 5.7 && d < 6.0, `got ${d}`);
});

test("a change is in the area when any of its points is, or within the buffer", () => {
  assert.equal(inArea([[-81.5, 32.1], [-81.1, 32.1]], box), true);
  assert.equal(inArea([[-81.4, 32.2]], box), false);
  assert.equal(inArea([[-81.4, 32.2]], box, 6), true);
  assert.equal(inArea([[-81.4, 32.2]], null), true);
  assert.equal(inArea([], box), false);
});

const event = {
  projects: [
    { kind: "added", points: [[-81.1, 32.1]] },
    { kind: "removed", points: [[-82.0, 33.4]] },
    { kind: "changed", what: ["date", "cost"], points: [[-80.9, 32.3]] },
  ],
  pairs: [{ kind: "gone", points: [[-81.2, 32.2], [-81.0, 32.3]] }, { kind: "timing", points: [[-82.0, 33.4]] }],
};

test("items carry the kinds they count under, and filter by area and kind", () => {
  const items = changeItems(event);
  assert.deepEqual(items.map(i => i.kinds), [["added"], ["removed"], ["date", "cost"], ["pairGone"], ["pairTiming"]]);
  assert.equal(itemsIn(event, box).length, 3);
  assert.equal(itemsIn(event, box, 0, ["cost"]).length, 1);
  const counts = countByKind(itemsIn(event, null));
  assert.equal(counts.date, 1); assert.equal(counts.cost, 1); assert.equal(counts.pairTiming, 1); assert.equal(counts.name, 0);
});

test("rings close, and day shifts read in months", () => {
  assert.equal(closeRing([[0, 0], [1, 0]]), null);
  assert.deepEqual(closeRing([[0, 0], [1, 0], [1, 1]]).at(-1), [0, 0]);
  assert.equal(daysLabel(120), "4 mo later");
  assert.equal(daysLabel(-730), "24 mo earlier");
  assert.equal(daysLabel(10), "10 d later");
});

test("the shipped change log has the five known new editions and consistent counts", () => {
  const log = JSON.parse(readFileSync(new URL("../data/changes.json", import.meta.url)));
  // Each utility's first edition is a baseline; Santee Cooper's lists are compared with Santee Cooper's.
  assert.deepEqual(log.events.map(e => e.id), ["desc-2026-2030", "santee-2026-2030", "ga-2026-2035", "santee-2025-2029", "desc-2025-2029"]);
  for (const e of log.events) assert.equal(e.previous.id.split("-")[0], e.id.split("-")[0], e.id);
  for (const e of log.events) {
    const c = countByKind(changeItems(e));
    assert.equal(c.added, e.counts.added, e.id);
    assert.equal(c.removed, e.counts.removed, e.id);
    assert.equal(c.pairNew, e.counts.pairsNew, e.id);
    assert.equal(c.pairGone, e.counts.pairsGone, e.id);
  }
  // Georgia says why every project left its plan: Table 3 (cancelled) or Table 4 (completed).
  const ga = log.events.find(e => e.id === "ga-2026-2035");
  assert.ok(ga.projects.filter(p => p.kind === "removed").every(p => /Table [34]/.test(p.reason)));
});
