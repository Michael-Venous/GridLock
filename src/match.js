export const MAX_MILES = 25;

export function milesBetween(a, b) {
  const radians = Math.PI / 180;
  const dLat = (b.lat - a.lat) * radians;
  const dLon = (b.lon - a.lon) * radians;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * radians) * Math.cos(b.lat * radians) * Math.sin(dLon / 2) ** 2;
  return 3958.7613 * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
}

export function dateGapDays(a, b) {
  if (!a || !b) return null;
  return Math.round(Math.abs(Date.parse(`${a}T00:00:00Z`) - Date.parse(`${b}T00:00:00Z`)) / 86400000);
}

export function matchProjects(projects, cutoff = MAX_MILES) {
  const left = projects.filter(project => project.utility === "DESC" && project.center);
  const right = projects.filter(project => project.utility === "GPC" && project.center);
  return left.flatMap(a => right.map(b => ({
    id: `${a.id}__${b.id}`,
    a,
    b,
    miles: milesBetween(a.center, b.center),
    gapDays: dateGapDays(a.inServiceDate, b.inServiceDate),
  }))).filter(pair => pair.miles < cutoff).sort((first, second) => first.miles - second.miles || (first.gapDays ?? Infinity) - (second.gapDays ?? Infinity));
}

export function gapLabel(days) {
  if (days === null) return "Timing unknown";
  if (days < 365) return `${days} days apart`;
  return `${(days / 365.25).toFixed(1)} years apart`;
}

export function opportunityText(pair) {
  if (pair.miles < 1) return "Very close sites: investigate shared access, staging, and equipment.";
  if (pair.miles < 5) return "Nearby work: investigate staging space, deliveries, and specialist equipment.";
  return "Within regional coordination range: investigate crews and equipment scheduling.";
}
