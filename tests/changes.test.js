import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { pointInPolygon, milesToPolygon, inArea, changeItems, itemsIn, countByKind, boundsRing, closeRing, daysLabel, pairAppId, currentEdition, dateBasisText } from "../src/changes.js";

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
  pairs: [
    { kind: "gone", a: { plan: "desc", appId: "DESC-1" }, b: { plan: "ga", appId: null }, points: [[-81.2, 32.2], [-81.0, 32.3]] },
    { kind: "timing", a: { plan: "ga", appId: "GA-2" }, b: { plan: "third", appId: "THIRD-A-7" }, points: [[-82.0, 33.4]] },
  ],
};

test("items carry the kinds they count under, and filter by area and kind", () => {
  const items = changeItems(event);
  assert.deepEqual(items.map(i => i.kinds), [["added"], ["removed"], ["date", "cost"], ["pairGone"], ["pairTiming"]]);
  assert.equal(itemsIn(event, box).length, 3);
  assert.equal(itemsIn(event, box, 0, ["cost"]).length, 1);
  const counts = countByKind(itemsIn(event, null));
  assert.equal(counts.date, 1); assert.equal(counts.cost, 1); assert.equal(counts.pairTiming, 1); assert.equal(counts.name, 0);
});

test("a changed pair links to the Pairs view only when both projects are in the current data", () => {
  assert.equal(pairAppId(event.pairs[0]), null);
  assert.equal(pairAppId(event.pairs[1]), "GA-2__THIRD-A-7");
  assert.equal(pairAppId({ a: {}, b: { appId: "GA-2" } }), null);
});

test("rings close, and day shifts read in months", () => {
  assert.equal(closeRing([[0, 0], [1, 0]]), null);
  assert.deepEqual(closeRing([[0, 0], [1, 0], [1, 1]]).at(-1), [0, 0]);
  assert.equal(daysLabel(120), "4 mo later");
  assert.equal(daysLabel(-730), "24 mo earlier");
  assert.equal(daysLabel(10), "10 d later");
});

test("a plan's current edition is the one the build recorded, not the title's or the first filing at its URL", () => {
  // A plan that posts every edition at one URL: the recorded edition is its current one.
  const third = { id: "third", edition: "2026-2030", source: { title: "Third Utility plan 2025-2029", url: "u/third" } };
  assert.equal(currentEdition(third), "2026–2030");
  assert.equal(currentEdition({ id: "third", edition: "2027", source: { title: "Third Utility Planned Projects" } }), "2027");
  // Data built without plans[].edition: a year range in the title, else nothing.
  assert.equal(currentEdition({ id: "ga", source: { title: "2025 GA ITS Ten-Year Plan (2026-2035), GA PSC docket" } }), "2026–2035");
  assert.equal(currentEdition({ id: "third", source: { title: "Third Utility Planned Projects" } }), null);
});

test("filing dates are explained from each filing's own date basis", () => {
  const f = (plan, dateBasis) => ({ plan, dateBasis });
  const names = { desc: "DESC", ga: "Georgia ITS", third: "Third", fourth: "Fourth" };
  assert.equal(dateBasisText([f("desc", "PDF creation date"), f("ga", "GA PSC filed date"), f("desc", "PDF creation date")], id => names[id]),
    "DESC: PDF creation date; Georgia ITS: GA PSC filed date");
  assert.equal(dateBasisText([f("desc", "PDF creation date"), f("third", "PDF creation date"), f("fourth", "posting date"), f("ga", undefined)], id => names[id]),
    "DESC and Third: PDF creation date; Fourth: posting date");
  assert.equal(dateBasisText([f("ga", "GA PSC filed date"), f("ga", "PDF creation date")], id => names[id]), "Georgia ITS: GA PSC filed date or PDF creation date");
  // Whatever the ingest writes reads as the plan's date basis, not as a noun after "the".
  assert.equal(dateBasisText([f("third", "given at ingest"), f("fourth", "date ingested (dry run)")], id => names[id]),
    "Third: given at ingest; Fourth: date ingested (dry run)");
  assert.equal(dateBasisText([f("ga")]), null);
});

test("the shipped change log has the three known filings and consistent counts", () => {
  const log = JSON.parse(readFileSync(new URL("../data/changes.json", import.meta.url)));
  assert.deepEqual(log.events.map(e => e.id), ["desc-2026-2030", "ga-2026-2035", "desc-2025-2029"]);
  for (const e of log.events) {
    const c = countByKind(changeItems(e));
    assert.equal(c.added, e.counts.added, e.id);
    assert.equal(c.removed, e.counts.removed, e.id);
    assert.equal(c.pairNew, e.counts.pairsNew, e.id);
    assert.equal(c.pairGone, e.counts.pairsGone, e.id);
  }
  // Pair sides are a/b from two different plans, a the earlier one in registry order.
  const order = Object.keys(JSON.parse(readFileSync(new URL("../data/filings.json", import.meta.url))).plans);
  for (const p of log.events.flatMap(e => e.pairs)) assert.ok(order.indexOf(p.a.plan) >= 0 && order.indexOf(p.a.plan) < order.indexOf(p.b.plan), `${p.a.key} × ${p.b.key}`);
  // Every current plan's edition is the registry's own current filing's (pipeline/registry.py current()); every
  // filing says what its date is.
  const data = JSON.parse(readFileSync(new URL("../data/projects.json", import.meta.url)));
  const current = JSON.parse(execFileSync("python3", ["-c", "import json, registry; print(json.dumps({p: f['edition'] for p, f in registry.current().items()}))"],
    { cwd: new URL("../pipeline/", import.meta.url), encoding: "utf8" }));
  assert.deepEqual(Object.fromEntries(data.plans.map(p => [p.id, currentEdition(p)])), Object.fromEntries(Object.entries(current).map(([p, e]) => [p, e.replaceAll("-", "–")])));
  assert.ok(data.plans.every(p => p.edition), "plans[].edition");
  assert.ok(log.filings.every(f => f.dateBasis), "dateBasis");
  // Georgia says why every project left its plan: Table 3 (cancelled) or Table 4 (completed).
  const ga = log.events.find(e => e.id === "ga-2026-2035");
  assert.ok(ga.projects.filter(p => p.kind === "removed").every(p => /Table [34]/.test(p.reason)));
});
