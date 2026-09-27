// A self-contained geographic overview for printed briefs. No map tiles, fonts,
// scripts or external SVG resources are needed when the brief is saved as a PDF.
const WIDTH = 700, HEIGHT = 190, MI_PER_DEGREE = 69.0934;
const COLORS = ["#087f8c", "#b96124"];
const esc = value => String(value ?? "").replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '').replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const short = (value, n = 30) => { const text = String(value ?? ""); return text.length > n ? `${text.slice(0, n - 1)}…` : text; };
const number = value => Number(value.toFixed(2));
const validPoint = p => Boolean(p && Number.isFinite(p.lat) && Number.isFinite(p.lon) && Math.abs(p.lat) <= 90 && Math.abs(p.lon) <= 180);
const radius = project => Number.isFinite(project.radiusMi) && project.radiusMi > 0 ? project.radiusMi : 0;

function svg(content, description) {
  return `<svg xmlns="http://www.w3.org/2000/svg" class="brief-map" viewBox="0 0 ${WIDTH} ${HEIGHT}" width="${WIDTH}" height="${HEIGHT}" role="img" aria-label="Project location overview" style="display:block;width:100%;height:auto;max-width:700px;background:#fff;font-family:Arial,sans-serif"><title>Project location overview</title><desc>${esc(description)}</desc><rect width="700" height="190" fill="#fff" stroke="none"/>${content}</svg>`;
}

/** Return escaped, standalone SVG markup suitable for a pair's printable brief. */
export function briefMapSvg(pair) {
  const projects = [pair?.a, pair?.b];
  if (!projects.every(p => validPoint(p?.center))) {
    return svg('<rect x="1" y="1" width="698" height="188" rx="5" fill="#f5f8f8" stroke="#d7e0e3"/><text x="20" y="40" fill="#405661" stroke="none" font-size="13">Location overview unavailable: both project centers are needed.</text>', 'One or both project center locations are unavailable.');
  }

  const lat0 = (projects[0].center.lat + projects[1].center.lat) / 2;
  const lon0 = projects[0].center.lon;
  const cos = Math.max(0.01, Math.cos(lat0 * Math.PI / 180));
  const xy = point => [(point.lon - lon0) * MI_PER_DEGREE * cos, (point.lat - lat0) * MI_PER_DEGREE];
  const rows = projects.map((p, i) => ({
    project: p, color: COLORS[i], letter: i ? "B" : "A", center: xy(p.center), radius: radius(p),
    endpoints: (p.endpoints ?? []).filter(e => validPoint(e.point)).map(e => ({ ...e, xy: xy(e.point) })),
  }));
  const extent = rows.flatMap(r => [
    [r.center[0] - r.radius, r.center[1] - r.radius], [r.center[0] + r.radius, r.center[1] + r.radius],
    ...r.endpoints.map(e => e.xy),
  ]);
  const xs = extent.map(p => p[0]), ys = extent.map(p => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const rangeX = Math.max(2, maxX - minX), rangeY = Math.max(2, maxY - minY);
  // Space for the north arrow, markers and labels is outside the fitted bounds.
  const area = { x: 32, y: 43, width: 384, height: 94 };
  const scale = Math.min(area.width / rangeX, area.height / rangeY);
  const midX = (minX + maxX) / 2, midY = (minY + maxY) / 2;
  const point = p => [number(area.x + area.width / 2 + (p[0] - midX) * scale), number(area.y + area.height / 2 - (p[1] - midY) * scale)];
  const text = (x, y, value, size = 10, attrs = '') => `<text x="${number(x)}" y="${number(y)}" font-size="${size}" fill="#29434e" stroke="none" ${attrs}>${esc(value)}</text>`;
  const circle = (x, y, r, attrs) => `<circle cx="${number(x)}" cy="${number(y)}" r="${number(r)}" ${attrs}/>`;
  const title = value => `<title>${esc(value)}</title>`;
  const centers = rows.map(r => point(r.center));
  const close = Math.hypot(centers[0][0] - centers[1][0], centers[0][1] - centers[1][1]) < 24;
  let out = text(14, 17, 'PROJECT LOCATIONS', 10, 'font-weight="700" letter-spacing=".7"');
  out += '<rect x="14" y="27" width="442" height="147" rx="4" fill="#f8faf9" stroke="#d7e0e3"/>';

  for (const r of rows) {
    const [x, y] = point(r.center);
    if (r.radius) out += circle(x, y, r.radius * scale, `class="uncertainty-ring" fill="${r.color}" fill-opacity=".06" stroke="${r.color}" stroke-width="1" stroke-dasharray="3 3"`);
  }
  out += `<path class="center-link" d="M${centers[0].join(' ')} L${centers[1].join(' ')}" fill="none" stroke="#4f6570" stroke-width="1.3" stroke-dasharray="5 4">${title('Straight distance between centers; not a transmission route')}</path>`;
  for (const r of rows) for (const e of r.endpoints) {
    const [x, y] = point(e.xy);
    out += `<g class="located-endpoint">${title(`${r.letter} endpoint: ${e.name ?? 'Unnamed'}; ${e.confidence ?? 'confidence unknown'}`)}${circle(x, y, 3, `fill="#fff" stroke="${r.color}" stroke-width="1.5"`)}</g>`;
  }
  rows.forEach((r, i) => {
    const [x, y] = centers[i];
    out += `<g class="project-center">${title(`${r.letter}: ${r.project.projectId ?? r.project.id ?? 'Project'}; ${r.project.center.lat.toFixed(4)}, ${r.project.center.lon.toFixed(4)}; uncertainty ±${r.radius} mi`)}`;
    if (!i) out += circle(x, y, 6, `fill="${r.color}" stroke="#fff" stroke-width="1.5"`);
    else out += `<path d="M${x} ${number(y - 5)} l5 5 -5 5 -5 -5 Z" fill="${r.color}" stroke="#fff" stroke-width="1.2"/>`;
    out += text(x + 9, y + (close && i ? 13 : -8), r.letter, 11, 'font-weight="700"') + '</g>';
  });

  // A "nice" scale length fits within 85 pixels and remains in the map footer.
  const limitMi = 85 / scale, magnitude = 10 ** Math.floor(Math.log10(limitMi));
  const distance = [5, 2, 1].map(n => n * magnitude).find(n => n <= limitMi) ?? magnitude / 2;
  const scaleWidth = number(distance * scale), scaleText = `${Number(distance.toPrecision(3))} mi`;
  out += `<path class="scale-bar" d="M28 152 v5 h${scaleWidth} v-5" fill="none" stroke="#29434e" stroke-width="1.3"/>`;
  out += text(28 + scaleWidth / 2, 169, scaleText, 9, 'text-anchor="middle"');
  out += '<path d="M440 62 V42 M436 47 l4 -6 4 6" fill="none" stroke="#29434e" stroke-width="1.3"/>' + text(440, 38, 'N', 9, 'text-anchor="middle"');
  rows.forEach((r, i) => {
    const y = 37 + i * 28;
    const utility = r.project.utility === 'SAV' ? 'GPC (Sav)' : r.project.utility === 'SCPSA' ? 'Santee Coop' : r.project.utility ?? r.project.state ?? 'Project';
    out += text(476, y, `${r.letter}  ${short(utility, 11)} · ${short(r.project.projectId ?? r.project.id, 19)}`, 11, `font-weight="700" style="fill:${r.color}"`);
    out += text(476, y + 12, `${r.project.center.lat.toFixed(4)}, ${r.project.center.lon.toFixed(4)} · ±${r.radius} mi`, 9);
  });
  out += circle(481, 101, 3, 'fill="#fff" stroke="#4f6570" stroke-width="1.3"') + text(493, 104, 'Located endpoint (confidence in records)', 9);
  out += circle(481, 119, 5, 'fill="none" stroke="#4f6570" stroke-width="1" stroke-dasharray="2 2"') + text(493, 122, 'Stated location uncertainty', 9);
  out += '<path d="M476 137 h14" fill="none" stroke="#4f6570" stroke-width="1.3" stroke-dasharray="4 3"/>' + text(496, 140, 'Center link — not a route', 9);
  out += text(14, 187, 'Relative geographic positions; no street basemap. Endpoint markers are estimates, not verified worksites.', 9);
  return svg(out, `${rows.map(r => `${r.letter}: ${r.project.name ?? r.project.projectId}; uncertainty ${r.radius} miles`).join('. ')}. North is up. Dashed link joins project centers, not a transmission route.`);
}
