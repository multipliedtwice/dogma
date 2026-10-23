const app=document.querySelector('#app');
const nav=document.querySelector('#familyNav');
const saveState=document.querySelector('#saveState');
let data,review={schema:1,narrator_id:'vlad-rimsky',registry_version:'vlad-rimsky-v1',decisions:{}};
let activeFamily=null,noteTimer=null,pendingNote=null,saveQueue=Promise.resolve();
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=n=>n==null?'—':Number(n).toFixed(3);
const options=(values,current)=>values.map(([value,label])=>`<option value="${value}" ${value===current?'selected':''}>${label}</option>`).join('');

async function boot(){
  [data,review]=await Promise.all([
    fetch('review-data.json',{cache:'no-store'}).then(r=>r.json()),
    fetch('review',{cache:'no-store'}).then(r=>r.ok?r.json():review).catch(()=>review)
  ]);
  nav.innerHTML=data.families.map(f=>`<option value="${f.id}">${esc(f.label)}</option>`).join('');
  nav.onchange=()=>show(nav.value,true);
  const wanted=new URL(location.href).searchParams.get('family');
  show(data.families.some(f=>f.id===wanted)?wanted:data.families[0].id,false);
  saveState.textContent='Saved automatically';
}

function show(id,push){
  flushNotes();
  activeFamily=id;nav.value=id;
  if(push){const u=new URL(location.href);u.searchParams.set('family',id);history.pushState({},'',u)}
  const family=data.families.find(f=>f.id===id);
  const saved=review.decisions?.[id]||{};
  const desc={...(saved.descriptors||{})};
  const med=family.book1_metrics||{};
  const grouped=Object.groupBy?Object.groupBy(family.examples,e=>e.character_id):family.examples.reduce((a,e)=>((a[e.character_id]??=[]).push(e),a),{});
  const examples=Object.entries(grouped).map(([,items])=>items.map((e,index)=>`<article class="example"><h3>${esc(e.character_name)}${items.length>1?` · ${index+1}`:''}</h3><blockquote>${esc(e.text)}</blockquote><audio controls preload="metadata" src="${esc(e.audio_url)}"></audio></article>`).join('')).join('');
  const comparisons=family.comparisons.length?`<table><thead><tr><th>Compared with</th><th>Unseen chapters<br>AUC / balanced</th><th>Unseen characters<br>AUC / balanced</th></tr></thead><tbody>${family.comparisons.map(c=>`<tr><td>${esc(c.other_family_label)}</td><td>${fmt(c.heldout_chapters.roc_auc)} / ${fmt(c.heldout_chapters.balanced_accuracy)}</td><td>${fmt(c.heldout_characters.roc_auc)} / ${fmt(c.heldout_characters.balanced_accuracy)}</td></tr>`).join('')}</tbody></table>`:'<p class="empty">No reliable held-out comparison yet.</p>';
  const verdict=saved.reuse_verdict||'';
  app.innerHTML=`
    <h1>${esc(family.label)}</h1>
    <p class="lede">Compare Vlad’s performance across different characters. Character names are shown as listening aids; the saved registry key remains <b>${esc(family.id)}</b>.</p>
    ${family.priority_reason?`<div class="priority">${esc(family.priority_reason)}</div>`:''}
    <section class="summary">
      <div class="metric"><b>${family.usage.measured_clips}</b><span>measured clips across books</span></div>
      <div class="metric"><b>${med.median_pitch_hz==null?'—':Number(med.median_pitch_hz).toFixed(1)+' Hz'}</b><span>median pitch</span></div>
      <div class="metric"><b>${med.envelope_peaks_per_second==null?'—':Number(med.envelope_peaks_per_second).toFixed(2)}</b><span>tempo estimate · sound peaks/s</span></div>
      <div class="metric"><b>${med.pitch_range_semitones==null?'—':Number(med.pitch_range_semitones).toFixed(1)+' st'}</b><span>median pitch range</span></div>
    </section>
    ${family.sample_size_note?`<p class="limited">${esc(family.sample_size_note)}</p>`:''}
    <h2>Listen across characters</h2>
    <section class="examples">${examples||'<p class="empty">No Book 1 examples available.</p>'}</section>
    <h2>Does the distinction survive new characters?</h2>
    <p class="help">AUC is the main distinctness score. Balanced accuracy shows whether the current automatic cut-off is usable. Around 0.5 means no useful separation.</p>
    ${comparisons}
    <section class="decision">
      <h2>Your conclusion</h2>
      <div class="choice-row" id="verdicts">
        ${choice('reusable','One reusable family',verdict)}
        ${choice('character_voice','Character-specific voice',verdict)}
        ${choice('split_needed','Several voices — split family',verdict)}
        ${choice('unsure','Unsure',verdict)}
      </div>
      <h2>How this voice sounds</h2>
      <div class="fields">
        ${selectField('perceived_gender','Gender',[['masculine','Masculine'],['feminine','Feminine'],['neutral','Neutral / unclear']],desc.perceived_gender)}
        ${selectField('perceived_age','Age',[['young','Young'],['adult','Adult'],['mature','Mature'],['elderly','Elderly'],['unclear','Unclear']],desc.perceived_age)}
        ${selectField('voice_weight','Weight',[['light','Light'],['medium','Medium'],['heavy','Heavy'],['unclear','Unclear']],desc.voice_weight)}
        ${selectField('voice_depth','Depth',[['low','Low'],['medium','Medium'],['high','High'],['unclear','Unclear']],desc.voice_depth)}
        ${selectField('brightness','Brightness',[['dark','Dark'],['balanced','Balanced'],['bright','Bright'],['unclear','Unclear']],desc.brightness)}
        ${selectField('roughness','Roughness',[['smooth','Smooth'],['slightly_rough','Slightly rough'],['rough','Rough'],['unclear','Unclear']],desc.roughness)}
        ${selectField('voice_quality','Voice quality',[['normal','Normal'],['bright','Bright'],['dark','Dark'],['soft','Soft'],['rough','Rough'],['nasal','Nasal'],['mechanical','Mechanical'],['other','Other']],desc.voice_quality)}
        ${selectField('otherness','Type',[['human','Human'],['non_human','Non-human'],['mechanical','Mechanical'],['unclear','Unclear']],desc.otherness)}
      </div>
      <textarea id="notes" placeholder="Optional correction or distinction…">${esc(saved.notes||'')}</textarea>
      <p class="help">Every change is saved automatically. Clip labels are never modified here.</p>
    </section>
    <div class="next"><button id="nextFamily">Next family</button></div>`;
  document.querySelectorAll('[data-verdict]').forEach(button=>button.onclick=()=>{document.querySelectorAll('[data-verdict]').forEach(b=>b.classList.toggle('selected',b===button));save({reuse_verdict:button.dataset.verdict})});
  document.querySelectorAll('[data-descriptor]').forEach(select=>select.onchange=()=>save({descriptors:readDescriptors()}));
  document.querySelector('#notes').oninput=()=>{clearTimeout(noteTimer);pendingNote={id,notes:document.querySelector('#notes').value};noteTimer=setTimeout(flushNotes,550)};
  document.querySelector('#nextFamily').onclick=()=>{const i=data.families.findIndex(f=>f.id===id);show(data.families[(i+1)%data.families.length].id,true);scrollTo({top:0,behavior:'smooth'})};
}

function choice(value,label,current){return `<button class="choice ${value===current?'selected':''}" data-verdict="${value}">${label}</button>`}
function selectField(name,label,values,current){return `<div class="field"><label for="${name}">${label}</label><select id="${name}" data-descriptor>${options([['','Not described'],...values],current||'')}</select></div>`}
function readDescriptors(){return Object.fromEntries([...document.querySelectorAll('[data-descriptor]')].filter(x=>x.value).map(x=>[x.id,x.value]))}

function flushNotes(){clearTimeout(noteTimer);if(pendingNote){const note=pendingNote;pendingNote=null;save({notes:note.notes},note.id)}}
function save(change,familyId=activeFamily){
  saveQueue=saveQueue.then(async()=>{
  const before=review.decisions?.[familyId]||{};
  const after={...before,...change};
  if(change.descriptors)after.descriptors=change.descriptors;
  if(activeFamily===familyId)saveState.textContent='Saving…';
  try{
    const response=await fetch('save-decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({schema:1,narrator_id:data.narrator_id,registry_version:data.registry_version,family_id:familyId,before_revision:before._revision||0,decision:after})});
    const body=await response.json();
    if(!response.ok)throw new Error(body.error||'Save failed');
    review=body.review;if(activeFamily===familyId)saveState.textContent='Saved';
  }catch(error){saveState.textContent='Not saved · '+error.message}
  });return saveQueue;
}
window.onpopstate=()=>{const wanted=new URL(location.href).searchParams.get('family');if(data?.families.some(f=>f.id===wanted))show(wanted,false)};
boot().catch(error=>{saveState.textContent='Could not load';app.innerHTML=`<p>${esc(error.message)}</p>`});
