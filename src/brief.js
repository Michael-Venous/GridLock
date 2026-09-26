import {matchProjects} from './match.js';
import {impactScenario,money} from './review.js';
const root=document.getElementById('brief');
const node=(tag,text)=>{const e=document.createElement(tag);e.textContent=text;return e;};
try {
 const query=new URLSearchParams(location.search);
 const response=await fetch('data/projects.json');if(!response.ok)throw new Error('Project data unavailable');
 const {projects}=await response.json(),pair=matchProjects(projects).find(p=>p.id===query.get('pair'));
 if (pair) document.querySelector('header > a').href = 'index.html?' + query.toString();
 if(!pair) throw new Error('No qualifying pair selected. Open a brief from the workspace.');
 const values=['share','low','high','extra'].map((key,i)=>query.has(key)?Number(query.get(key)):[1,10,30,0][i]);
 const result=impactScenario(pair.a.publishedBudget,...values);if(!result)throw new Error('Invalid impact assumptions. Return to the workspace and review them.');
 root.replaceChildren(node('h2',`${pair.a.name} × ${pair.b.name}`),node('p',`Prepared ${new Date().toISOString().slice(0,10)} · ${pair.miles.toFixed(2)} miles between approximate centers · ${pair.gapDays} days between published targets.`));
 const notice=node('p','Research candidate, not an agreed coordination plan. Site locations and current project status require confirmation. In-service date proximity does not establish overlapping construction windows.');notice.className='notice';root.append(notice);
 for(const p of [pair.a,pair.b]){
  const article=node('article','');article.append(node('h3',`${p.utility} · ${p.name}`),node('p',`Target: ${p.inServiceDate}${p.previousInServiceDate?' (previously '+p.previousInServiceDate+')':''}. ${p.documentStatus}.`),node('p',`${p.locationConfidence}. ${p.coordinateMethod}. ${p.locationNote}`));
  for(const e of p.evidence){const line=node('p',`${e.title}${e.page?' · PDF p. '+e.page:''}. ${e.note} `);const a=node('a','Source');a.href=e.url;line.append(a);article.append(line);}root.append(article);
 }
 root.append(node('h2','Illustrative impact scenario'),node('p',`Cost basis: ${money(pair.a.publishedBudget)}, the published DESC project total. GPC costs are excluded. Assumed shareable portion: ${values[0]}%; assumed avoided cost within that portion: ${values[1]}–${values[2]}%; additional coordination cost: ${money(values[3])}. These percentages are user assumptions, not measured savings rates.`));
 const estimate=node('p',`${money(result.low)} to ${money(result.high)} potential net change`);estimate.className='estimate';root.append(estimate,node('p','Formula: DESC budget × shareable fraction × avoidance fraction − added coordination cost. Negative results indicate added expense. No savings or shareable site have been confirmed.'));
 root.append(node('h2','Proposed next discussion'));
 const list=node('ol','');for(const text of ['Confirm the named sites and exact project phases with both teams.','Compare construction dates and outage windows; verify whether past target dates reflect completed work.','Assess shared staging, crew or equipment availability and access constraints.','Replace scenario assumptions with quotes and agree how costs and responsibilities would be split.'])list.append(node('li',text));root.append(list);
 document.getElementById('print').disabled=false;document.getElementById('download').disabled=false;
 document.getElementById('print').onclick=()=>window.print();
 document.getElementById('download').onclick=()=>{
  const sources=[pair.a,pair.b].flatMap(p=>p.evidence.map(e=>`${e.title}${e.page?' p. '+e.page:''}: ${new URL(e.url,location.href).href}`)).join('\n');
  const text=root.innerText+'\n\nSources\n'+sources;
  document.getElementById('text-export').hidden=false;document.getElementById('brief-text').value=text;
  const blob=new Blob([text],{type:'text/plain;charset=utf-8'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`gridlock-${pair.id}-brief.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
 };
} catch(e) {root.textContent=e.message;}
