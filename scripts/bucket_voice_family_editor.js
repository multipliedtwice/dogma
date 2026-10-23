// Global voice-family descriptions. This never reads or writes clip decisions.
(() => {
  const ENDPOINT='/voice-family-registry/vlad-rimsky-v1/';
  const descriptorFields=[
    ['perceived_gender','Perceived gender',[['masculine','Masculine'],['feminine','Feminine'],['neutral','Neutral'],['unclear','Unclear']]],
    ['perceived_age','Perceived age',[['child','Child'],['young','Young adult'],['adult','Adult'],['mature','Mature'],['elderly','Elderly'],['unclear','Unclear']]],
    ['voice_weight','Weight',[['light','Light'],['medium','Medium'],['heavy','Heavy'],['unclear','Unclear']]],
    ['voice_depth','Depth',[['low','Low'],['medium','Medium'],['high','High'],['unclear','Unclear']]],
    ['brightness','Brightness',[['dark','Dark'],['balanced','Balanced'],['bright','Bright'],['unclear','Unclear']]],
    ['roughness','Roughness',[['smooth','Smooth'],['slightly_rough','Slightly rough'],['rough','Rough'],['unclear','Unclear']]],
    ['voice_quality','Quality',[['normal','Normal'],['bright','Bright'],['dark','Dark'],['soft','Soft'],['rough','Rough'],['nasal','Nasal'],['mechanical','Mechanical'],['other','Other'],['unclear','Unclear']]],
    ['otherness','Type',[['human','Human'],['non_human','Non-human'],['mechanical','Mechanical'],['unclear','Unclear']]],
  ];
  const textFields=[
    ['casting_description','Natural-language sound description','Describe the voice as it sounds, without naming a mood or character.'],
    ['character_fit','Best character fit','What kind of character is this voice best suited to?'],
    ['avoid_for','Avoid / reserve / distinguish guidance','Say “X only” to reserve this family for X; otherwise say when to avoid it or which nearby family to distinguish it from.'],
  ];
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const state={data:null,review:null,active:'',bucketFamily:'',drafts:new Map(),loading:null};
  let host;

  function previewId(value){
    const pairs={'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'ts','ч':'ch','ш':'sh','щ':'shch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya'};
    const transliterated=[...String(value||'').toLocaleLowerCase('ru')].map(char=>pairs[char]??char).join('');
    const slug=transliterated.normalize('NFKD').replace(/[\u0300-\u036f]/g,'').replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,'').replace(/_+/g,'_').slice(0,56).replace(/_+$/,'');
    return slug?'custom_'+slug:'';
  }
  const familyLabel=family=>family.label+(family.synthesis_status==='requires_pack_voice'?' (label only until added to a Higgs pack)':'');

  function styles(){
    if(document.querySelector('#voice-family-editor-style'))return;
    const style=document.createElement('style');style.id='voice-family-editor-style';style.textContent=`
      .voice-family-editor{margin-top:14px;border-top:1px solid #cad7ce;padding-top:12px}.voice-family-editor summary{cursor:pointer;font-weight:650;color:#245b3b}.voice-family-editor .family-editor-summary-status{margin-left:10px;font-weight:500;color:#586b60}.voice-family-editor .family-editor-summary-status.error{color:#9a3c2b}
      .voice-family-editor .family-editor-head{display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin:14px 0}.voice-family-editor .family-editor-head label{flex:1 1 300px}
      .voice-family-editor .family-editor-fields{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:10px}.voice-family-editor .family-editor-text{display:grid;grid-template-columns:repeat(3,minmax(220px,1fr));gap:10px;margin-top:12px}
      .voice-family-editor textarea{min-height:92px}.voice-family-editor .family-editor-actions{display:flex;align-items:center;gap:12px;margin-top:12px}.voice-family-editor .family-editor-status{font-size:.9rem;color:#586b60}.voice-family-editor .family-editor-status.error{color:#9a3c2b}
      .voice-family-editor .create-family{margin:12px 0;padding:10px;border:1px solid #d5ddd8;border-radius:8px}.voice-family-editor .create-family-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.voice-family-editor code{overflow-wrap:anywhere}
      @media(max-width:800px){.voice-family-editor .family-editor-fields{grid-template-columns:1fr 1fr}.voice-family-editor .family-editor-text{grid-template-columns:1fr}}
    `;document.head.append(style);
  }
  async function load(){
    if(state.loading)return state.loading;
    state.loading=Promise.all([
      fetch(ENDPOINT+'data',{cache:'no-store'}).then(async response=>{if(!response.ok)throw Error((await response.json()).error||'Could not load voice taxonomy');return response.json()}),
      fetch(ENDPOINT+'review',{cache:'no-store'}).then(async response=>{if(!response.ok)throw Error((await response.json()).error||'Could not load voice descriptions');return response.json()}),
    ]).then(([data,review])=>{state.data=data;state.review=review;return state;});
    return state.loading;
  }
  function saved(id){return state.review?.decisions?.[id]||{}}
  function capture(){
    if(!state.active||!host?.querySelector('[data-family-editor-form]'))return;
    const descriptors=Object.fromEntries(descriptorFields.map(([name])=>[name,host.querySelector(`[name=${name}]`).value]).filter(([,value])=>value));
    const draft={descriptors};
    for(const [name] of textFields)draft[name]=host.querySelector(`[name=${name}]`).value;
    state.drafts.set(state.active,draft);
  }
  function decision(id){
    const base=saved(id),draft=state.drafts.get(id);
    return draft?{...base,...draft,descriptors:draft.descriptors}:{...base,descriptors:{...(base.descriptors||{})}};
  }
  function option(value,label,current){return `<option value="${esc(value)}"${value===current?' selected':''}>${esc(label)}</option>`}
  function render(id){
    if(!state.data)return;
    const valid=state.data.families.some(family=>family.id===id);state.active=valid?id:'';
    const current=valid?decision(id):{descriptors:{}};
    const familyOptions=state.data.families.map(family=>option(family.id,familyLabel(family),state.active)).join('');
    const groups=[...new Set(state.data.families.map(family=>family.group))].sort().map(group=>`<option value="${esc(group)}">${esc(group)}</option>`).join('');
    const savedStatus=saved(id)._revision?'Saved revision '+saved(id)._revision:'Not described yet';
    host.innerHTML=`<details class="voice-family-editor" open><summary>Describe this voice family for future casting <span class="family-editor-summary-status" data-family-summary-status>${esc(savedStatus)}</span></summary>
      <p class="small">This global description edits the voice family, not the selected mood or any clip label. ${state.data.families.length} canonical families are available.</p>
      <div class="family-editor-head"><label>Voice family<select data-family-selector><option value="">Choose a taxonomy family</option>${familyOptions}</select></label></div>
      <details class="create-family"><summary>Add a genuinely different voice family</summary>
        <p class="small">Creation only extends the global labeling taxonomy. It does not change clip labels and is not synthesizable until a reviewed performance is added to a Higgs pack.</p>
        <div class="create-family-grid"><label>Display name<input data-new-family-name maxlength="120" placeholder="Distinct audible voice"></label><label>Canonical group<select data-new-family-group><option value="">Choose a group</option>${groups}</select></label></div>
        <p class="small">Stable ID: <code data-new-family-id>Enter a display name</code> (preview; the server is authoritative).</p>
        <div class="family-editor-actions"><button type="button" data-create-family>Create family</button><span class="family-editor-status" data-create-status role="status"></span></div>
      </details>
      <div data-family-editor-form ${valid?'':'hidden'}>
        <div class="family-editor-fields">${descriptorFields.map(([name,label,values])=>`<label>${esc(label)}<select name="${name}"><option value="">Not described</option>${values.map(([value,text])=>option(value,text,current.descriptors?.[name]||'')).join('')}</select></label>`).join('')}</div>
        <div class="family-editor-text">${textFields.map(([name,label,placeholder])=>`<label>${esc(label)}<textarea name="${name}" placeholder="${esc(placeholder)}">${esc(current[name]||'')}</textarea></label>`).join('')}</div>
        <div class="family-editor-actions"><button type="button" class="primary" data-save-family>Save family description</button><span class="family-editor-status" role="status">${esc(savedStatus)}</span></div>
      </div></details>`;
    host.querySelector('[data-family-selector]').onchange=event=>{capture();render(event.target.value)};
    host.querySelector('[data-new-family-name]').oninput=event=>host.querySelector('[data-new-family-id]').textContent=previewId(event.target.value)||'Cannot generate an ID from this name';
    host.querySelector('[data-create-family]').onclick=createFamily;
    host.querySelectorAll('select[name],textarea[name]').forEach(control=>control.oninput=()=>{
      capture();const status=host.querySelector('.family-editor-status'),summary=host.querySelector('[data-family-summary-status]');status.textContent='Unsaved changes kept in this tab';status.classList.remove('error');summary.textContent='Unsaved changes';summary.classList.remove('error');
    });
    host.querySelector('[data-save-family]')?.addEventListener('click',save);
  }
  async function createFamily(){
    capture();
    const name=host.querySelector('[data-new-family-name]'),group=host.querySelector('[data-new-family-group]');
    const button=host.querySelector('[data-create-family]'),status=host.querySelector('[data-create-status]');
    const idPreview=previewId(name.value);button.disabled=true;status.textContent='Creating…';status.classList.remove('error');
    try{
      const response=await fetch(ENDPOINT+'create-family',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        schema:1,narrator_id:state.data.narrator_id,registry_version:state.data.registry_version,
        display_name:name.value,group:group.value,id_preview:idPreview,
        before_revision:state.data.revision,before_families_sha256:state.data.families_sha256,
      })});
      const body=await response.json();if(!response.ok)throw Error(body.error||'Family creation failed');
      state.data=body.taxonomy;state.bucketFamily=body.created.id;
      document.dispatchEvent(new CustomEvent('voice-family-taxonomy-changed',{detail:body.taxonomy}));
      render(body.created.id);
      host.querySelector('.family-editor-status').textContent=`Family created as ${body.created.id}. Describe it, then save.`;
    }catch(error){status.textContent='Not created · '+error.message;status.classList.add('error');button.disabled=false}
  }
  async function save(){
    capture();const id=state.active,button=host.querySelector('[data-save-family]'),status=host.querySelector('.family-editor-status');
    const summary=host.querySelector('[data-family-summary-status]');
    if(!id)return;button.disabled=true;status.textContent='Saving…';status.classList.remove('error');summary.textContent='Saving…';summary.classList.remove('error');
    try{
      const before=saved(id),response=await fetch(ENDPOINT+'save-decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        schema:1,narrator_id:state.data.narrator_id,registry_version:state.data.registry_version,
        family_id:id,before_revision:before._revision||0,decision:decision(id),
      })});
      const body=await response.json();if(!response.ok)throw Error(body.error||'Save failed');
      state.review=body.review;state.drafts.delete(id);render(id);
      host.querySelector('.family-editor-status').textContent='Saved revision '+saved(id)._revision;
    }catch(error){status.textContent='Not saved · '+error.message;status.classList.add('error');summary.textContent='Not saved';summary.classList.add('error');button.disabled=false}
  }
  async function show(bucketFamily){
    host=host||document.querySelector('#voice-family-editor');if(!host)return;
    styles();
    try{
      await load();
      if(bucketFamily!==state.bucketFamily){capture();state.bucketFamily=bucketFamily;render(bucketFamily)}
      else if(!host.children.length)render(bucketFamily);
    }catch(error){host.innerHTML=`<p class="status error">Voice-family editor unavailable: ${esc(error.message)}</p>`}
  }
  function selectedBucketFamily(){return document.querySelector('#bucket')?.value.split('|')[0]||''}
  function boot(){
    host=document.querySelector('#voice-family-editor');const bucket=document.querySelector('#bucket'),summary=document.querySelector('#bucket-summary');
    if(!host||!bucket)return;
    bucket.addEventListener('change',()=>show(selectedBucketFamily()));
    if(summary)new MutationObserver(()=>show(selectedBucketFamily())).observe(summary,{childList:true,subtree:true,characterData:true});
    show(selectedBucketFamily());
  }
  window.voiceFamilyDescriptionEditor={show};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
