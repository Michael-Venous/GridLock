// Shared, testable rules for project discovery, combined filters and shareable views.
const normalized = value => String(value ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');
export function matchesProject(project, query) {
  const q = normalized(query);
  return !q || [project.id, project.projectId, project.name, project.utility, ...(project.endpoints ?? []).map(e => e.name)].some(value => normalized(value).includes(q));
}
export function withinDistance(pair, distance) {
  const minDistance = pair.qualifies ? pair.miles : Math.max(0, pair.miles - (pair.a.radiusMi ?? 0) - (pair.b.radiusMi ?? 0));
  return minDistance < distance;
}
export function encodeView(state, defaults) {
  const p = new URLSearchParams();
  if (state.search) p.set('q', state.search);
  if (state.distance !== defaults.distance) p.set('distance', state.distance);
  if (state.year !== defaults.year) p.set('year', state.year);
  if (state.gap !== defaults.gap) p.set('timing', state.gap);
  if (!state.hidePast) p.set('past', '1');
  if (state.includePossible) p.set('possible', '1');
  if (state.sort !== 'score') p.set('sort', state.sort);
  if (state.selectedProject) p.set('project', state.selectedProject);
  if (state.selectedPair) p.set('pair', state.selectedPair);
  if (state.detailTab !== 'summary') p.set('detail', state.detailTab);
  return p.toString();
}
export function decodeView(hash, {defaults, minYear, maxYear, sortKeys}) {
  const p = new URLSearchParams(hash.replace(/^#/, ''));
  const bounded = (key, lo, hi, fallback) => {
    if (!p.has(key)) return fallback;
    const n = Number(p.get(key));
    return Number.isInteger(n) && n >= lo && n <= hi ? n : fallback;
  };
  return {...defaults,
    search: (p.get('q') ?? '').trim().toLowerCase().slice(0, 200),
    distance: bounded('distance', 1, 25, defaults.distance), year: bounded('year', minYear, maxYear, defaults.year),
    gap: ['all','ahead','overlap','365','730'].includes(p.get('timing')) ? p.get('timing') : defaults.gap,
    hidePast: p.get('past') !== '1', includePossible: p.get('possible') === '1',
    sort: sortKeys.includes(p.get('sort')) ? p.get('sort') : 'score',
    selectedProject: p.get('project'), selectedPair: p.get('pair'),
    detailTab: ['summary','scenario','records'].includes(p.get('detail')) ? p.get('detail') : 'summary',
  };
}
