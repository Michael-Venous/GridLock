import test from "node:test";
import assert from "node:assert/strict";
import { floodZoneText, groundFlags, pairGround, groundMatches, groundLines, groundShort, groundCostNote } from "../src/environment.js";

const site = (name, over = {}) => ({
  name, radiusMi: 0.25,
  flood: { zone: "X", subtype: null, sfha: false, mapped: true, sfhaShare: 0 },
  wetlands: { share: 0, waterShare: 0, types: {}, atStation: [] },
  habitat: [], protected: [], ...over,
});
const project = (environment, state = "SC") => ({ state, environment });

const floodSite = site("Okatie", { flood: { zone: "AE", subtype: null, sfha: true, mapped: true, sfhaShare: 0.91 }, wetlands: { share: 0.131, waterShare: 0, types: {}, atStation: [] } });
const uplandSite = site("McIntosh", { wetlands: { share: 0.031, waterShare: 0.038, types: {}, atStation: [] } });
const sturgeon = { species: "Atlantic sturgeon", status: "Endangered", stage: "final", unit: "Unit 3 Savannah River, Back River", source: "NOAA Fisheries", crosses: true };
const route = { miles: 10.56, wetlands: { miles: 1.12, waterMiles: 0.3, types: {} }, flood: { sfhaMiles: 6.57, mappedMiles: 10.5 }, habitat: [sturgeon], protected: [] };

test("FEMA zones read as plain language", () => {
  assert.equal(floodZoneText("AE"), "1% annual-chance flood area");
  assert.equal(floodZoneText("VE"), "coastal high-hazard area, 1% annual chance");
  assert.equal(floodZoneText("X", "0.2 PCT ANNUAL CHANCE FLOOD HAZARD"), "0.2% annual-chance flood area");
  assert.equal(floodZoneText("X"), "minimal flood hazard");
  assert.equal(floodZoneText(null), null);
});

test("flags come only from mapped facts at checked sites and lines", () => {
  const p = project({ checked: true, sites: [floodSite, uplandSite], route });
  const f = groundFlags(p);
  assert.equal(f.flood, true);
  assert.deepEqual(f.habitat, ["Atlantic sturgeon"]);
  assert.deepEqual(f.protected, []);
  assert.deepEqual(groundFlags(project({ checked: false, reason: "x" })), { checked: false, flood: false, habitat: [], protected: [] });
});

test("a line barely touching the flood area does not flag it", () => {
  const p = project({ checked: true, sites: [uplandSite], route: { ...route, habitat: [], flood: { sfhaMiles: 0.05, mappedMiles: 10 } } });
  assert.equal(groundFlags(p).flood, false);
});

test("pairs are classed for the Ground filter", () => {
  const checkedClear = project({ checked: true, sites: [uplandSite], route: null });
  const unchecked = project({ checked: false, reason: "no endpoint is placed at a station" }, "GA");
  const constrained = project({ checked: true, sites: [floodSite], route: null }, "GA");
  assert.equal(groundMatches({ a: checkedClear, b: unchecked }, "clear"), true);
  assert.equal(groundMatches({ a: unchecked, b: unchecked }, "unchecked"), true);
  assert.equal(groundMatches({ a: unchecked, b: unchecked }, "clear"), false);
  assert.equal(groundMatches({ a: checkedClear, b: constrained }, "flood"), true);
  assert.equal(groundMatches({ a: checkedClear, b: constrained }, "habitat"), false);
  assert.equal(groundMatches({ a: checkedClear, b: constrained }, "all"), true);
  assert.equal(pairGround({ a: checkedClear, b: unchecked }).both, false);
});

test("sentences state what is mapped and where", () => {
  const lines = groundLines(project({ checked: true, sites: [floodSite, uplandSite], route }));
  assert.equal(lines[0], "Okatie: FEMA zone AE at the station (1% annual-chance flood area); 91% of the land within 0.25 mi is in the 1% flood area; mapped wetland 13% within 0.25 mi.");
  assert.match(lines[1], /^McIntosh: FEMA zone X at the station \(minimal flood hazard\); mapped wetland 3%, open water 4% within 0.25 mi\.$/);
  assert.equal(lines[2], "Traced line (10.6 mi): 6.6 mi in the 1% flood area, 1.1 mi mapped wetland, 0.3 mi open water.");
  assert.equal(lines[3], "Traced line crosses critical habitat for Atlantic sturgeon (endangered, NOAA Fisheries, Savannah River).");
  assert.deepEqual(groundLines(project({ checked: false, reason: "no endpoint is placed at a station, so the ground can't be checked" })),
    ["Not checked: no endpoint is placed at a station, so the ground can't be checked."]);
});

test("a site with no digital flood map says so instead of implying no risk", () => {
  const s = site("Hooks", { flood: { zone: null, subtype: null, sfha: false, mapped: false, sfhaShare: 0 } });
  assert.match(groundLines(project({ checked: true, sites: [s], route: null }))[0], /no digital FEMA flood map here/);
});

test("CSV cell and cost note", () => {
  const p = project({ checked: true, sites: [floodSite], route: null });
  const q = project({ checked: true, sites: [uplandSite], route: null }, "GA");
  assert.equal(groundShort(p), "flood zones: Okatie AE");
  assert.equal(groundShort(project({ checked: false, reason: "x" })), "not checked");
  assert.match(groundCostNote({ a: p, b: q }), /Okatie in the 1% flood area; McIntosh outside it/);
  assert.match(groundCostNote({ a: q, b: q }), /may overstate the surface cost/);
  assert.match(groundCostNote({ a: p, b: p }), /fits the floodplain mat rate/);
  assert.equal(groundCostNote({ a: project({ checked: false, reason: "x" }), b: project({ checked: false, reason: "x" }) }), null);
});
