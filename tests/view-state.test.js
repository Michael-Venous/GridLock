import test from 'node:test';
import assert from 'node:assert/strict';
import {matchesProject, withinDistance, encodeView, decodeView} from '../src/view-state.js';
const defaults={search:'',distance:25,year:2035,gap:'all',hidePast:true,includePossible:false,shortlistOnly:false};
const config={defaults,minYear:2025,maxYear:2035,sortKeys:['score','distance','time','overlap']};
test('project discovery includes unmatched and unlocated records and formatted IDs',()=>{
  const p={name:'Wagener 115kV Tap: Construct Tap',projectId:'06371 D',endpoints:[],center:null};
  assert.ok(matchesProject(p,'Wagener'));assert.ok(matchesProject(p,'06371D'));assert.ok(!matchesProject(p,'McIntosh'));
});
test('possible candidates obey the selected uncertainty-aware distance',()=>{
  const p={miles:28.44,qualifies:false,a:{radiusMi:6},b:{radiusMi:0.5}};
  assert.ok(withinDistance(p,25));assert.ok(!withinDistance(p,5));
  assert.ok(!withinDistance({...p,qualifies:true,miles:5},5));
});
test('URL round trip restores decision but excludes local shortlist',()=>{
  const s={...defaults,search:'Wagener',distance:7,year:2030,gap:'ahead',includePossible:true,hidePast:false,sort:'distance',selectedProject:'DESC-A',selectedPair:'DESC-A|GA-1',detailTab:'records',shortlistOnly:true,shortlist:new Set(['private'])};
  const hash=encodeView(s,defaults);assert.ok(!hash.includes('shortlist'));assert.ok(!hash.includes('private'));
  const restored=decodeView(hash,config);for(const k of ['distance','year','gap','includePossible','hidePast','sort','selectedProject','selectedPair','detailTab']) assert.deepEqual(restored[k],s[k]);
  assert.equal(restored.search,'wagener');assert.equal(restored.shortlistOnly,false);
});
test('invalid incoming URL values use safe defaults',()=>{
  const s=decodeView('#distance=NaN&year=1900&timing=bogus&sort=invalid&detail=bad',config);
  assert.equal(s.distance,25);assert.equal(s.year,2035);assert.equal(s.gap,'all');assert.equal(s.sort,'score');assert.equal(s.detailTab,'summary');
});
