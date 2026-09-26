import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { briefMapSvg } from '../src/brief-map.js';
import { matchProjects } from '../src/match.js';

const project = (id, lat, lon, radiusMi = 1) => ({id, projectId:id, name:id, utility:id, center:{lat,lon}, radiusMi, endpoints:[]});
const pair = () => ({a:project('DESC-A',32.3,-81.1),b:project('GA-B',32.4,-81.2)});

test('brief map is standalone, identifies geometry and escapes project text', () => {
  const p = pair();
  p.a.projectId = '<script>alert("x")</script>';
  p.a.name = 'Station </desc><image href="https://bad.example/x"/>';
  p.a.endpoints = [{name:'<path onload="alert(1)">', point:{lat:32.32,lon:-81.13}}];
  const svg = briefMapSvg(p);
  assert.match(svg,/viewBox="0 0 700 190"/);
  assert.match(svg,/role="img"/);
  assert.match(svg,/class="uncertainty-ring"/);
  assert.match(svg,/class="located-endpoint"/);
  assert.match(svg,/class="scale-bar"/);
  assert.match(svg,/Center link — not a route/);
  assert.match(svg,/&lt;script&gt;/);
  assert.doesNotMatch(svg,/<script|<image|\s(?:onload|href)="/);
  assert.equal(svg,briefMapSvg(p));
});

test('brief map omits inferred routes and includes only explicitly verified valid routes', () => {
  const p=pair();
  p.a.route={coords:[[32.3,-81.1],[32.4,-81.2]]};
  assert.doesNotMatch(briefMapSvg(p),/class="verified-route"/);
  p.a.route.verified=true;
  assert.doesNotMatch(briefMapSvg(p),/class="verified-route"/);
  p.a.route.evidence={source:"https://example.org/public-plan",quote:"The project follows this circuit."};
  assert.match(briefMapSvg(p),/class="verified-route"/);
  p.a.route.coords[1]=[NaN,-81.2];
  assert.doesNotMatch(briefMapSvg(p),/class="verified-route"/);
});

test('brief map handles missing, invalid and coincident centers without nonfinite coordinates', () => {
  assert.match(briefMapSvg(null),/Location overview unavailable/);
  const invalid=pair(); invalid.a.center.lat=NaN;
  assert.match(briefMapSvg(invalid),/Location overview unavailable/);
  const p=pair();p.b.center={...p.a.center};p.a.radiusMi=p.b.radiusMi=0;
  const svg=briefMapSvg(p);
  assert.doesNotMatch(svg,/NaN|Infinity/);
  assert.equal((svg.match(/class="project-center"/g)??[]).length,2);
});

test('all current pair markers and uncertainty rings fit inside the printed map', () => {
  const projects=JSON.parse(readFileSync(new URL('../data/projects.json',import.meta.url))).projects;
  const pairs=matchProjects(projects,25,{includePossible:true,asOf:'2026-09-26'});
  assert.ok(pairs.length>0);
  for(const p of pairs){
    const svg=briefMapSvg(p);
    assert.doesNotMatch(svg,/NaN|Infinity/,p.id);
    for(const match of svg.matchAll(/<circle cx="([\d.-]+)" cy="([\d.-]+)" r="([\d.-]+)"/g)){
      const [,cx,cy,r]=match.map(Number);
      assert.ok(cx-r>=0&&cx+r<=700&&cy-r>=0&&cy+r<=190,`${p.id}: circle clips viewBox`);
    }
  }
});
