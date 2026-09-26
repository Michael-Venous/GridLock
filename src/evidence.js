const root = document.getElementById('records');
const node = (tag, text) => { const el = document.createElement(tag); el.textContent = text; return el; };
try {
 const response = await fetch('data/projects.json'); if(!response.ok) throw new Error('Project data unavailable');
 const {projects} = await response.json(); root.replaceChildren();
 for(const p of projects) {
  const article = node('article',''); article.id=p.id;
  article.append(node('h2',`${p.id} · ${p.name}`)); const dl=node('dl','');
  for(const [label,value] of [['Record origin',p.recordOrigin],['Source project ID',p.sourceProjectId || p.id],['Published target',p.inServiceDate],['Previous target',p.previousInServiceDate || 'No revision recorded'],['Filing status',p.documentStatus],['Current status',p.currentStatus],['Location confidence',p.locationConfidence],['Coordinate method',p.coordinateMethod],['Map center',`${p.center.lat}, ${p.center.lon}`],['Location review',p.locationNote]]) dl.append(node('dt',label),node('dd',value));
  article.append(dl);
  for(const e of p.evidence){article.append(node('h3',`${e.title}${e.page ? ' · PDF page '+e.page : ''}`),node('p',e.note));if(e.url.startsWith('https://')){const a=node('a','Open public source');a.href=e.url;a.target='_blank';a.rel='noopener noreferrer';article.append(a);}}
  root.append(article);
 }
 if(location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
} catch(e) {root.textContent=e.message;}
