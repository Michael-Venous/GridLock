import test from 'node:test';
import assert from 'node:assert/strict';
import { applyLocationEdits, validateLocation } from '../src/location-edits.js';
import { matchProjects } from '../src/match.js';
const project = { id: 'a', state: 'SC', endpoints: [{ name: 'Station', point: null }], center: null, issues: [], radiusMi: null, route: { coords: [] }, environment: { checked: true } };
const edit = { name: 'Station', lat: 33, lon: -81, radiusMi: 0.5, note: 'Site records' };
test('a previously unplaced project can match; edits leave the source untouched and clear stale screening', () => {
  const [p] = applyLocationEdits([project], { a: { 0: edit } });
  assert.equal(project.center, null);
  assert.deepEqual(p.center, { lat: 33, lon: -81 });
  assert.equal(p.environment, null); assert.equal(p.route, null);
  assert.equal(p.userLocation, true);
  const other = { id: 'b', state: 'GA', center: { lat: 33.01, lon: -81 }, endpoints: [], radiusMi: 0.5 };
  assert.equal(matchProjects([p, other]).length, 1);
  assert.equal(applyLocationEdits([project], {})[0], project);
});
test('invalid or outdated endpoint edits do not override source records', () => {
  for (const v of [{ ...edit, lat: 91 }, { ...edit, lon: Infinity }, { ...edit, radiusMi: 0 }, { ...edit, note: ' ' }]) {
    assert.equal(Boolean(validateLocation(v)), false);
    assert.equal(applyLocationEdits([project], { a: { 0: v } })[0], project);
  }
  assert.equal(applyLocationEdits([project], { a: { 0: { ...edit, name: 'Other station' } } })[0], project);
});
test('partial endpoint edits retain uncertainty about the unlocated end', () => {
  const source = { ...project, endpoints: [...project.endpoints, { name: 'Unknown', point: null }], miles: 20 };
  const [p] = applyLocationEdits([source], { a: { 0: edit } });
  assert.equal(p.radiusMi, 10.5); assert.equal(p.locationConfidence, 'low');
});
