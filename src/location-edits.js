import { milesBetween } from './match.js';

export function validateLocation(value) {
  return value && Number.isFinite(value.lat) && value.lat >= -90 && value.lat <= 90
    && Number.isFinite(value.lon) && value.lon >= -180 && value.lon <= 180
    && Number.isFinite(value.radiusMi) && value.radiusMi > 0 && value.radiusMi <= 100
    && typeof value.note === 'string' && value.note.trim().length > 0;
}

// Work on a copy: the published snapshot remains available for reset and historical comparisons.
export function applyLocationEdits(projects, edits) {
  return projects.map(original => {
    const entries = edits?.[original.id];
    if (!entries || typeof entries !== 'object') return original;
    const p = structuredClone(original);
    let changed = false;
    p.endpoints.forEach((e, index) => {
      const v = entries[index];
      if (!validateLocation(v) || v.name !== e.name) return;
      Object.assign(e, { point: { lat: v.lat, lon: v.lon }, radiusMi: v.radiusMi,
        method: 'user supplied', confidence: 'medium', evidence: `User supplied, not independently verified: ${v.note.trim()}` });
      changed = true;
    });
    if (!changed) return original;
    const located = p.endpoints.filter(e => e.point);
    p.center = { lat: located.reduce((s, e) => s + e.point.lat, 0) / located.length,
      lon: located.reduce((s, e) => s + e.point.lon, 0) / located.length };
    p.radiusMi = Math.max(...located.map(e => e.radiusMi ?? 0));
    if (located.length < p.endpoints.length) p.radiusMi += p.miles ? p.miles / 2 : 10;
    if (located.length >= 2 && p.miles) p.radiusMi = Math.max(p.radiusMi,
      (milesBetween(located[0].point, located.at(-1).point) - p.miles) / 2);
    // Preserve published scope uncertainty even if a station is placed more precisely.
    p.radiusMi = Math.max(p.radiusMi, original.radiusMi ?? 0);
    p.locationConfidence = located.some(e => ['low', 'ambiguous'].includes(e.confidence)) || located.length < p.endpoints.length ? 'low' : 'medium';
    p.locatedBy = 'endpoints';
    p.locationCompleteness = { located: located.length, total: p.endpoints.length };
    p.userLocation = true;
    p.route = null;
    p.environment = null;
    p.locationNote = 'Browser location edit. Ground screening and inferred routes are unavailable for this edited project. Published validation notes below refer to the original snapshot.';
    return p;
  });
}
