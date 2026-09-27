import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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

test("a plan's current edition comes from its filing, not from its title", () => {
  const filings = [
    { plan: "desc", edition: "2025-2029", date: "2025-03-03", url: "u/2025" },
    { plan: "desc", edition: "2026-2030", date: "2026-04-28", url: "u/2026" },
    { plan: "third", edition: "2027", date: "2026-06-01", url: "u/third" },
  ];
  assert.equal(currentEdition({ id: "desc", source: { title: "DESC list, 2026-2030", url: "u/2026" } }, filings), "2026–2030");
  assert.equal(currentEdition({ id: "desc", source: { title: "DESC list", url: "elsewhere" } }, filings), "2026–2030");   // newest filing
  assert.equal(currentEdition({ id: "desc", source: { title: "DESC list", url: "u/2025" } }, filings), "2025–2029");      // the filing at its URL
  assert.equal(currentEdition({ id: "third", source: { title: "Third Utility Planned Projects", url: "u/third" } }, filings), "2027");
  // Without the change log: a year range in the title, else nothing.
  assert.equal(currentEdition({ id: "ga", source: { title: "2025 GA ITS Ten-Year Plan (2026-2035), GA PSC docket" } }), "2026–2035");
  assert.equal(currentEdition({ id: "third", source: { title: "Third Utility Planned Projects" } }), null);
});

test("filing dates are explained from each filing's own date basis", () => {
  const f = (plan, dateBasis) => ({ plan, dateBasis });
  const names = { desc: "DESC", ga: "Georgia ITS", third: "Third", fourth: "Fourth" };
  assert.equal(dateBasisText([f("desc", "PDF creation date"), f("ga", "GA PSC filed date"), f("desc", "PDF creation date")], id => names[id]),
    "the PDF creation date for DESC and the GA PSC filed date for Georgia ITS");
  assert.equal(dateBasisText([f("desc", "PDF creation date"), f("third", "PDF creation date"), f("fourth", "posting date"), f("ga", undefined)], id => names[id]),
    "the PDF creation date for DESC and Third and the posting date for Fourth");
  assert.equal(dateBasisText([f("ga", "GA PSC filed date"), f("ga", "PDF creation date")], id => names[id]), "the GA PSC filed date or PDF creation date for Georgia ITS");
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
  // Every current plan's edition comes from its filing; every filing says what its date is.
  const data = JSON.parse(readFileSync(new URL("../data/projects.json", import.meta.url)));
  assert.deepEqual(data.plans.map(p => currentEdition(p, log.filings)), data.plans.map(p => log.filings.filter(f => f.plan === p.id).at(-1).edition.replaceAll("-", "–")));
  assert.ok(log.filings.every(f => f.dateBasis), "dateBasis");
  // Georgia says why every project left its plan: Table 3 (cancelled) or Table 4 (completed).
  const ga = log.events.find(e => e.id === "ga-2026-2035");
  assert.ok(ga.projects.filter(p => p.kind === "removed").every(p => /Table [34]/.test(p.reason)));
});
