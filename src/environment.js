// What federal maps show at a project's station-level sites and along its traced line (pipeline/environment.py).
// Facts only: what is mapped, never what a permit will require.

const pct = x => x >= 0.995 ? "all" : x < 0.005 ? "none" : `${Math.max(1, Math.round(x * 100))}%`;
const mi = x => `${x < 0.1 ? x.toFixed(2) : x.toFixed(1)} mi`;
const SFHA_ZONES = /^(A|AE|AH|AO|AR|A99|V|VE)$/;

export function floodZoneText(zone, subtype) {
  if (!zone) return null;
  if (/^V/.test(zone)) return "coastal high-hazard area, 1% annual chance";
  if (SFHA_ZONES.test(zone)) return "1% annual-chance flood area";
  if (zone === "X" && /0\.2/.test(subtype ?? "")) return "0.2% annual-chance flood area";
  if (zone === "X") return "minimal flood hazard";
  if (zone === "D") return "flood hazard undetermined";
  if (zone === "OPEN WATER") return "open water";
  return null;
}

const habitatLabel = h => `${h.species}${h.stage === "proposed" ? " (proposed)" : ""}`;
const areaLabel = a => `${a.name}${a.manager ? `, ${a.manager}` : ""}${a.category === "Easement" ? " (easement)" : ""}`;

// The strongest mapped facts, for list tags and the Ground filter.
export function groundFlags(project) {
  const env = project.environment;
  const flags = { checked: Boolean(env?.checked), flood: false, habitat: [], protected: [] };
  if (!flags.checked) return flags;
  const habitat = new Map(), prot = new Map();
  for (const s of env.sites) {
    if (s.flood?.sfha) flags.flood = true;
    for (const h of s.habitat ?? []) habitat.set(habitatLabel(h), h);
    for (const a of s.protected ?? []) prot.set(areaLabel(a), a);
  }
  const r = env.route;
  if (r) {
    if ((r.flood?.sfhaMiles ?? 0) >= 0.1) flags.flood = true;
    for (const h of r.habitat ?? []) habitat.set(habitatLabel(h), h);
    for (const a of r.protected ?? []) prot.set(areaLabel(a), a);
  }
  flags.habitat = [...habitat.keys()];
  flags.protected = [...prot.keys()];
  return flags;
}

export function pairGround(pair) {
  const a = groundFlags(pair.a), b = groundFlags(pair.b);
  return {
    checked: a.checked || b.checked, both: a.checked && b.checked,
    flood: a.flood || b.flood,
    habitat: [...new Set([...a.habitat, ...b.habitat])],
    protected: [...new Set([...a.protected, ...b.protected])],
  };
}

// The Ground filter. "flood": a station in the 1% flood area or a traced line through it (0.1 mi or more).
export function groundMatches(pair, key) {
  const g = pairGround(pair);
  const any = g.flood || g.habitat.length > 0 || g.protected.length > 0;
  if (key === "unchecked") return !g.checked;
  if (key === "clear") return g.checked && !any;
  if (key === "flood") return g.flood;
  if (key === "habitat") return g.habitat.length > 0;
  if (key === "protected") return g.protected.length > 0;
  return true;
}

// Sentences for the pair panel and the brief, one project at a time.
export function groundLines(project, radiusMi = 0.25) {
  const env = project.environment;
  if (!env) return ["Not checked."];
  if (!env.checked) return [`Not checked: ${env.reason}.`];
  const out = [];
  for (const s of env.sites) {
    const parts = [];
    const f = s.flood;
    if (!f) parts.push("flood map unavailable when checked");
    else if (!f.mapped) parts.push("no digital FEMA flood map here");
    else {
      const z = floodZoneText(f.zone, f.subtype);
      parts.push(f.zone ? `FEMA zone ${f.zone} at the station${z ? ` (${z})` : ""}` : "station outside FEMA's mapped zones");
      if (f.sfhaShare > 0) parts.push(`${pct(f.sfhaShare)} of the land within ${radiusMi} mi is in the 1% flood area`);
    }
    const w = s.wetlands;
    if (!w) parts.push("wetland map unavailable when checked");
    else if (w.share > 0 || w.waterShare > 0) parts.push(`mapped wetland ${pct(w.share)}${w.waterShare > 0 ? `, open water ${pct(w.waterShare)}` : ""} within ${radiusMi} mi`);
    else parts.push(`no mapped wetland within ${radiusMi} mi`);
    out.push(`${s.name}: ${parts.join("; ")}.`);
    if (s.habitat?.length) out.push(`${s.name}: critical habitat within ${radiusMi} mi for ${s.habitat.map(h => `${habitatLabel(h)} (${h.status?.toLowerCase() ?? "listed"}, ${h.source})`).join(", ")}.`);
    if (s.protected?.length) out.push(`${s.name}: protected land within ${radiusMi} mi: ${s.protected.map(areaLabel).join("; ")}.`);
  }
  const r = env.route;
  if (r) {
    const parts = [];
    if (r.flood) parts.push(r.flood.mappedMiles ? `${mi(r.flood.sfhaMiles)} in the 1% flood area` : "no digital FEMA flood map along it");
    if (r.wetlands) parts.push(`${mi(r.wetlands.miles)} mapped wetland${r.wetlands.waterMiles ? `, ${mi(r.wetlands.waterMiles)} open water` : ""}`);
    out.push(`Traced line (${mi(r.miles)}): ${parts.join(", ")}.`);
    for (const h of r.habitat ?? []) out.push(`Traced line ${h.crosses ? "crosses" : `runs ${mi(h.miles)} through`} critical habitat for ${habitatLabel(h)} (${h.status?.toLowerCase() ?? "listed"}, ${h.source}${h.unit ? `, ${h.unit.split(",")[0].replace(/^Unit \d+\s*/, "")}` : ""}).`);
    for (const a of r.protected ?? []) out.push(`Traced line runs ${mi(a.miles)} through ${areaLabel(a)}.`);
  }
  return out;
}

// One cell per project for the CSV.
export function groundShort(project) {
  const env = project.environment;
  if (!env?.checked) return "not checked";
  const g = groundFlags(project);
  const zones = env.sites.map(s => `${s.name} ${s.flood?.zone ?? "no zone"}`).join(", ");
  return [`flood zones: ${zones}`, env.route?.flood ? `line in 1% flood area ${env.route.flood.sfhaMiles} mi` : null,
    g.habitat.length ? `critical habitat: ${g.habitat.join(", ")}` : null, g.protected.length ? `protected land: ${g.protected.join(", ")}` : null].filter(Boolean).join("; ");
}

// For the cost scenario: whether the default floodplain mat rate matches what is mapped at the stations.
export function groundCostNote(pair) {
  const sites = [pair.a, pair.b].flatMap(p => p.environment?.checked ? p.environment.sites.map(s => ({ p, s })) : []);
  if (!sites.length) return null;
  const inFlood = sites.filter(x => x.s.flood?.sfha).map(x => x.s.name);
  const upland = sites.filter(x => x.s.flood?.mapped && !x.s.flood.sfha).map(x => x.s.name);
  if (inFlood.length && !upland.length) return `Mapped at the stations: ${inFlood.join(", ")} ${inFlood.length > 1 ? "are" : "is"} in the 1% flood area, which fits the floodplain mat rate.`;
  if (!inFlood.length && upland.length) return `Mapped at the stations: ${upland.join(", ")} ${upland.length > 1 ? "are" : "is"} outside the 1% flood area, so the floodplain mat rate may overstate the surface cost. Choose "Other surface" if the yard would be on upland ground.`;
  if (inFlood.length) return `Mapped at the stations: ${inFlood.join(", ")} in the 1% flood area; ${upland.join(", ")} outside it. The yard's own site decides which surface rate applies.`;
  return null;
}
