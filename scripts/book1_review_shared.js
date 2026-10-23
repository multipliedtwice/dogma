// Shared labels and persistence for the reusable bucket-labeling boards.
const MOOD_GROUPS = {
  'Calm or gentle': ['neutral','calm','soft','whisper','explaining','reflective','reminiscent','solemn','warm','gentle','reassuring','relieved'],
  'Happy or lively': ['cheerful','excited','energetic','triumphant','playful','curious','confident','encouraging','awe'],
  'Questions or surprise': ['questioning','perplexed','hesitant','worried','anxious','surprised','shocked','stunned','embarrassed'],
  'Sad, scared, hurt, or tired': ['afraid','panicked','sad','disappointed','grieving','distressed','injured','tired'],
  'Strong or urgent': ['fast_action','shouting','angry','irritated','indignant','threatening','suspicious','sarcastic','mocking','contemptuous','disgusted','warning','urgent','firm','commanding']
};
const MOODS = Object.values(MOOD_GROUPS).flat();
const MOOD_LABELS = {
  neutral:'Normal',calm:'Calm',soft:'Soft / quiet',whisper:'Whisper',explaining:'Explaining',reflective:'Thinking',reminiscent:'Remembering the past',
  solemn:'Serious',warm:'Friendly',gentle:'Gentle / kind',reassuring:'Comforting',relieved:'Relieved',
  cheerful:'Happy',excited:'Excited',energetic:'Energetic',triumphant:'Proud after winning',
  playful:'Playful',curious:'Curious',confident:'Confident',encouraging:'Encouraging',awe:'Amazed',questioning:'Asking a question',
  perplexed:'Confused',hesitant:'Hesitating',worried:'Worried',anxious:'Nervous / tense',
  surprised:'Surprised',shocked:'Shocked',stunned:'Quiet shock',embarrassed:'Embarrassed',afraid:'Scared',panicked:'Panicking',
  sad:'Sad',disappointed:'Disappointed',grieving:'Deeply sad',distressed:'Upset',injured:'Injured / in pain',tired:'Tired',angry:'Angry',
  irritated:'Annoyed',indignant:'Offended',threatening:'Threatening',suspicious:'Does not trust someone',
  sarcastic:'Sarcastic',mocking:'Making fun',contemptuous:'Looking down on someone',disgusted:'Disgusted',warning:'Warning / alarm',urgent:'In a hurry',
  fast_action:'Fast action',shouting:'Shouting',firm:'Firm',commanding:'Giving orders'
};
const canonicalMood = value => ({exhausted:'tired',wistful:'reminiscent',uncertain:'hesitant','cheerful, excited':'excited'})[value] || value;
const reviewEqual = (a,b) => JSON.stringify(reviewSorted(a))===JSON.stringify(reviewSorted(b));
function reviewSorted(value) {
  if(Array.isArray(value))return value.map(reviewSorted);
  if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(key=>[key,reviewSorted(value[key])]));
  return value;
}
const reviewClone = value => structuredClone(value);

class Book1ReviewStore {
  constructor(summary,endpointBase='') {
    this.summary=summary;this.endpointBase=endpointBase;this.decisions={};this.base={};
    this.reviewer='local-listener';this.baseReviewer=this.reviewer;this.chain=Promise.resolve();this.conflicts=null;
    this.key=`rb1-finetune-review-${summary.candidates_sha256}`;
    let tabId=sessionStorage.getItem('rb1-review-tab');
    if(!tabId){
      // randomUUID is restricted to secure contexts in mobile browsers. The
      // tab ID only separates local drafts; it is not a security credential.
      tabId=globalThis.crypto?.randomUUID?.()||`tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
      sessionStorage.setItem('rb1-review-tab',tabId);
    }
    this.pendingKey=`${this.key}-pending-${tabId}`;
  }
  valid(value) {
    return value?.candidates_sha256===this.summary.candidates_sha256&&value?.decisions&&typeof value.decisions==='object';
  }
  async latest() {
    const response=await fetch(this.endpointBase+'review-draft.json',{cache:'no-store'});
    if(response.status===404)return {schema:1,candidates_sha256:this.summary.candidates_sha256,reviewer:this.reviewer,decisions:{}};
    if(!response.ok)throw Error(`Could not load saved review: HTTP ${response.status}`);
    const data=await response.json();if(!this.valid(data))throw Error('Saved review does not match these clips');
    return data;
  }
  async load() {
    const server=await this.latest();
    this.base=reviewClone(server.decisions);Object.assign(this.decisions,reviewClone(server.decisions));
    this.reviewer=server.reviewer||this.reviewer;
    this.baseReviewer=this.reviewer;
    let pending=null,legacy=null;
    try{pending=JSON.parse(localStorage.getItem(this.pendingKey)||'null');}catch{}
    try{legacy=JSON.parse(localStorage.getItem(this.key)||'null');}catch{}
    if(pending?.candidates_sha256===this.summary.candidates_sha256&&pending.changes){
      for(const [id,change] of Object.entries(pending.changes)){
        if(change.after==null)delete this.decisions[id];else this.decisions[id]=reviewClone(change.after);
        if(change.before==null)delete this.base[id];else this.base[id]=reviewClone(change.before);
      }
      this.reviewer=pending.reviewer||this.reviewer;
    }else if(this.valid(legacy)&&legacy.storage_schema!==2&&Date.parse(legacy.updated_at||0)>Date.parse(server.updated_at||0)){
      // Preserve unsaved work from the older full-file review page on upgrade.
      let skipped=0;
      for(const [id,answer] of Object.entries(legacy.decisions)){
        if((this.base[id]?._revision||0)>(answer?._revision||0)){skipped++;continue;}
        this.decisions[id]=reviewClone(answer);
      }
      if(skipped)localStorage.setItem(this.key+'-legacy-backup',JSON.stringify(legacy));
      this.reviewer=legacy.reviewer||this.reviewer;
      localStorage.setItem(this.key,JSON.stringify({...server,storage_schema:2}));
    }
    this.stash();return server;
  }
  changes(snapshot=this.decisions) {
    const result={};
    for(const id of new Set([...Object.keys(this.base),...Object.keys(snapshot)]))
      if(!reviewEqual(this.base[id],snapshot[id]))result[id]={before:this.base[id]??null,after:snapshot[id]??null};
    return result;
  }
  stash() {
    const changes=this.changes();
    if(Object.keys(changes).length||this.reviewer!==this.baseReviewer)localStorage.setItem(this.pendingKey,JSON.stringify({
      candidates_sha256:this.summary.candidates_sha256,reviewer:this.reviewer,changes}));
    else localStorage.removeItem(this.pendingKey);
  }
  payload() {
    return {schema:1,candidates_sha256:this.summary.candidates_sha256,reviewer:this.reviewer,
      updated_at:new Date().toISOString(),decisions:reviewClone(this.decisions)};
  }
  async refresh() {
    const server=await this.latest();
    if(this.reviewer===this.baseReviewer)this.reviewer=server.reviewer||this.reviewer;
    this.baseReviewer=server.reviewer||this.baseReviewer;
    for(const id of new Set([...Object.keys(this.base),...Object.keys(server.decisions)])){
      if(reviewEqual(this.base[id],this.decisions[id])){
        if(server.decisions[id]==null){delete this.decisions[id];delete this.base[id];}
        else {this.decisions[id]=reviewClone(server.decisions[id]);this.base[id]=reviewClone(server.decisions[id]);}
      }
    }
    this.stash();return server;
  }
  save() {
    this.chain=this.chain.catch(()=>{}).then(()=>this.savePatch());return this.chain;
  }
  async savePatch() {
    const snapshot=reviewClone(this.decisions),changes=this.changes(snapshot);
    if(!Object.keys(changes).length&&this.reviewer===this.baseReviewer){await this.refresh();return;}
    const response=await fetch(this.endpointBase+'patch-review',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({schema:1,candidates_sha256:this.summary.candidates_sha256,reviewer:this.reviewer,changes})});
    const data=await response.json().catch(()=>({}));
    if(!response.ok){
      this.stash();
      if(response.status===409){this.conflicts=data.conflicts;throw Error('Another tab changed the same clip. Your edits are kept in this tab. Choose which labels to use.');}
      throw Error(data.error||`HTTP ${response.status}. Your edits remain saved in this tab.`);
    }
    if(!this.valid(data.review))throw Error('Save returned an invalid review');
    for(const id of new Set([...Object.keys(snapshot),...Object.keys(data.review.decisions)])){
      if(reviewEqual(this.decisions[id],snapshot[id])){
        if(data.review.decisions[id]==null)delete this.decisions[id];else this.decisions[id]=reviewClone(data.review.decisions[id]);
      }else{
        // Keep edits made during the request, while incorporating other tabs' fields.
        const current=this.decisions[id]||{},sent=snapshot[id]||{},merged=reviewClone(data.review.decisions[id]||{});
        for(const field of new Set([...Object.keys(current),...Object.keys(sent)])){
          if(field==='_revision'||reviewEqual(current[field],sent[field]))continue;
          if(Object.hasOwn(current,field))merged[field]=reviewClone(current[field]);else delete merged[field];
        }
        this.decisions[id]=merged;
      }
    }
    this.base=reviewClone(data.review.decisions);this.conflicts=null;
    this.baseReviewer=data.review.reviewer||this.baseReviewer;
    localStorage.setItem(this.key,JSON.stringify({...data.review,storage_schema:2}));this.stash();
  }
  async resolveConflicts(keepLocal) {
    const server=await this.latest();
    for(const conflict of this.conflicts||[]){
      const id=conflict.id,current=this.decisions[id]||{},before=this.base[id]||{},merged=reviewClone(server.decisions[id]||{});
      for(const field of new Set([...Object.keys(current),...Object.keys(before)])){
        if(field==='_revision'||reviewEqual(current[field],before[field])||!keepLocal&&conflict.fields.includes(field))continue;
        if(Object.hasOwn(current,field))merged[field]=reviewClone(current[field]);else delete merged[field];
      }
      this.decisions[id]=merged;this.base[id]=reviewClone(server.decisions[id]||{});
    }
    this.conflicts=null;this.stash();
  }
}

function reviewConflictControls(store,status,retry) {
  if(!store.conflicts?.length)return;
  for(const [label,keepLocal] of [['Use latest saved labels',false],['Keep my changes',true]]){
    const button=document.createElement('button');button.textContent=label;button.style.margin='8px';
    button.onclick=async()=>{button.disabled=true;try{await store.resolveConflicts(keepLocal);await retry();}catch(error){status.textContent=error.message;}};
    status.append(button);
  }
}

function bucketReviewMatches(decision,voice,mood,audioHash) {
  const check=decision?.bucket_review;
  return check?.verdict==='consistent'&&check.voice_family_id===voice&&canonicalMood(check.mood)===mood&&check.audio_sha256===audioHash;
}
