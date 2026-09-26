import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {impactScenario,sortPairs} from '../src/review.js';
import {matchProjects,dateGapDays} from '../src/match.js';
const projects=JSON.parse(readFileSync(new URL('../data/projects.json',import.meta.url))).projects;
test('impact is limited to shareable budget and allows net added cost',()=>{
 assert.deepEqual(impactScenario(100000,10,10,30,2000),{shareable:10000,low:-1000,high:1000});
 for(const args of [[100,101,10,20,0],[100,10,30,20,0],[100,10,10,20,-1],[100,NaN,10,20,0]]) assert.equal(impactScenario(...args),null);
});
test('timing sort changes priority but not geographic eligibility',()=>{
 const pairs=matchProjects(projects),timed=sortPairs(pairs,'timing');
 assert.deepEqual(new Set(timed.map(p=>p.id)),new Set(pairs.map(p=>p.id)));
 assert.ok(timed.every((p,i)=>!i || p.gapDays>=timed[i-1].gapDays));
 assert.ok(timed.every(p=>p.miles<25));
});
test('updated filing preserves previous target and changes the date gap',()=>{
 const p=projects.find(p=>p.id==='DESC_3');
 assert.equal(p.previousInServiceDate,'2025-12-31');assert.equal(p.inServiceDate,'2026-12-01');
 assert.equal(dateGapDays(p.inServiceDate,'2027-06-01'),182);
 assert.ok(p.evidence[0].url.endsWith('#page=12'));
});
test('every mapped record is traceable and uncertainty remains explicit',()=>{
 for(const p of projects){assert.ok(p.evidence.length);assert.ok(p.coordinateMethod);assert.ok(p.locationConfidence);assert.ok(Number.isFinite(p.center.lat));}
 const participating=new Set(matchProjects(projects).flatMap(p=>[p.a.id,p.b.id]));
 assert.ok(projects.some(p=>!participating.has(p.id)));
 assert.equal(matchProjects(projects).length,9);
});
