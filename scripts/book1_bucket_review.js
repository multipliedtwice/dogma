// Bucket HLS transport hooks v1
// Grouping is a review view; immutable sentence clips remain individual training rows.
const $ = selector=>document.querySelector(selector);
const escapeHTML = value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const audioURL = path=>'/'+path.replaceAll('\\','/').replace(/^training-work\//,'').replace(/^\/+/, '');
const PAGE_SIZE=12, DEFAULT_CLIP_GAP_MS=2000, BASIC_FAILURES=new Set(['missing_or_changed_audio','unexpected_pcm_format','empty_audio','duration_mismatch','clipping','level_outlier']);
let store, rows=[], byId=new Map(), precision={}, quality={}, families=[], aliases={}, buckets=new Map();
let acousticFeatures={},acousticState={stats:new Map(),warnings:new Map()},acousticLoadMessage='Audio comparisons unavailable.';
let selected='', pageIndex=0, pageSnapshot=[], loaded=false, saveTimer, undoMove=null, saveGeneration=0;
let linkedClip='';
let queue=[], queueBucket='', queueFilter='all', queueIndex=-1, sequenceActive=false, gapTimer, playbackEpoch=0;
let playbackPositions={}, cursorTime=0, cursorAudio=null, gapPending=false, queueComplete=false, playPending=false, cancelLoad=null, lastPositionSave=0;
let betweenClipGapMs=DEFAULT_CLIP_GAP_MS;
let singleClipPlayback=false;
const nextClipQueues=new Map();
let popularMoodCounts=new Map();
const listened=new Map(), heardRanges=new Map(), playedEvidence=new WeakMap();
const LABEL_FIELDS=['voice_verdict','confirmed_voice_id','corrected_voice_id','mood_verdict','corrected_mood','mood_scope','speaker_verdict','corrected_character_id','bucket_review'];
const decision=id=>store.decisions[id]||{};
const speakerHeading=row=>decision(row.id).corrected_character_id?.trim()||row.character_display||row.character_id?.replace(/^rb1_/,'').replaceAll('_',' ')||(row.role==='game_ui'?'Game voice':row.role==='narration'?'Narrator':row.role||'Voice');
const voiceLabel=id=>{const family=families.find(f=>f.id===id);return family?
  family.label+(family.synthesis_status==='requires_pack_voice'?' (label only; not in the current Higgs pack)':''):id;};
function mergeGlobalFamilies(taxonomy) {
  if(!Array.isArray(taxonomy?.families))return;
  const canonicalIds=new Set(taxonomy.families.map(family=>family.id));
  families=[...families.filter(family=>!canonicalIds.has(family.id)),...taxonomy.families];
}
const bucketKey=(voice,mood)=>`${voice}|${mood}`;
const bucketLabel=key=>{const [voice,mood]=key.split('|');return `${voiceLabel(voice)} · ${MOOD_LABELS[mood]||mood}`;};
function labels(row) {
  const d=decision(row.id);
  let voice=d.voice_verdict==='yes'?(d.confirmed_voice_id||row.original_voice_id_proposed||row.base_voice_id_proposed):d.voice_verdict==='no'?d.corrected_voice_id?.trim():null;
  voice=aliases[voice]||voice;
  let mood=d.mood_verdict==='yes'?(row.mood_proposed?.join(', ')||'neutral'):d.mood_verdict==='no'?d.corrected_mood?.trim():null;
  mood=canonicalMood(mood);
  return {voice:families.some(f=>f.id===voice)?voice:null,mood:MOODS.includes(mood)?mood:null};
}
function eligible(row) {
  const d=decision(row.id),p=precision[row.id],q=quality[row.id];
  return p?.status==='precision_pass'&&p.audio&&p.audio_sha256&&!d.rejected&&!d.boundary_start&&!d.boundary_end&&row.role!=='mixed'&&
    q?.audio_sha256===row.audio_sha256&&!q.reasons?.some(reason=>BASIC_FAILURES.has(reason));
}
function verified(row) {
  const {voice,mood}=labels(row);return eligible(row)&&bucketReviewMatches(decision(row.id),voice,mood,precision[row.id]?.audio_sha256);
}
function fingerprint(row) {
  const {voice,mood}=labels(row);return `${voice}|${mood}|${precision[row.id]?.audio_sha256}`;
}
function rebuildBuckets() {
  buckets=new Map();let labeled=0,held=0;
  for(const row of rows){const {voice,mood}=labels(row);if(!voice||!mood)continue;labeled++;
    if(!eligible(row)){held++;continue;}const key=bucketKey(voice,mood);
    if(!buckets.has(key))buckets.set(key,[]);buckets.get(key).push(row.id);
  }
  const order=key=>{const [v,m]=key.split('|');return families.findIndex(f=>f.id===v)*100+MOODS.indexOf(m);};
  buckets=new Map([...buckets].sort((a,b)=>b[1].length-a[1].length||order(a[0])-order(b[0])));
  rebuildPopularMoods();
  for(const host of document.querySelectorAll('.mood-suggestions')){
    const row=byId.get(host.closest('article').dataset.id);if(row)host.innerHTML=moodChips(row);
  }
  acousticState=buildBucketAcoustics(buckets,byId,precision,acousticFeatures);
  refreshAcousticWarnings();
  if(!selected)selected=buckets.keys().next().value||'';
  // Keep an emptied source bucket visible while a move is being reviewed.
  const options=[...buckets.keys()];if(selected&&!buckets.has(selected))options.push(selected);
  $('#bucket').innerHTML=options.map(key=>{const flagged=(buckets.get(key)||[]).filter(id=>acousticState.warnings.has(id)).length;
    return `<option value="${escapeHTML(key)}" ${key===selected?'selected':''}>${escapeHTML(bucketLabel(key))} (${buckets.get(key)?.length||0})${flagged?` · ⚠ ${flagged}`:''}</option>`;}).join('');
  $('#summary').textContent=`${labeled-held} labeled clips in ${buckets.size} buckets. ${held} labeled clips are held by audio or boundary checks. ${rows.length-labeled} clips still need labels.`+
    (Object.keys(acousticFeatures).length?` ${acousticState.warnings.size} clips have an audio comparison warning.`:'');
  updateSummary();
}
function filteredIds() {
  // Sort the complete filtered bucket before pagination. Stable sorting keeps
  // source order within each group; existing playback queues keep their cursor.
  return (buckets.get(selected)||[])
    .filter(id=>$('#filter').value==='all'||(verified(byId.get(id))?$('#filter').value==='verified':$('#filter').value==='pending'))
    .sort((a,b)=>Number(acousticState.warnings.has(b))-Number(acousticState.warnings.has(a)));
}
function updateSummary() {
  const ids=buckets.get(selected)||[],count=ids.filter(id=>verified(byId.get(id))).length;
  $('#bucket-summary').textContent=selected?`${bucketLabel(selected)}: ${ids.length} clips · ${count} verified · ${ids.length-count} to check`:'No reviewed clips yet. Refresh after labels are available.';
  const stats=acousticState.stats.get(selected),flagged=ids.filter(id=>acousticState.warnings.has(id)).length;
  $('#acoustic-summary').textContent=Object.keys(acousticFeatures).length?
    stats?.measured>=8&&stats.paragraphs>=7&&stats.measured/Math.max(1,stats.total)>=.5?
      `Audio averages: ${stats.measured} clips of at least 3 seconds. ${flagged} unusual clips to compare. A warning does not mean a wrong label.`:
      'Too few comparable audio clips for a useful bucket average. No automatic warnings for this group.':acousticLoadMessage;
  $('#confirm-listened').disabled=!ids.some(id=>listened.get(id)===fingerprint(byId.get(id))&&!verified(byId.get(id))&&!hasStagedLabels(id));
  updatePlayerControls();
}
function options(current,mood=false) {
  if(mood)return Object.entries(MOOD_GROUPS).map(([name,ids])=>`<optgroup label="${escapeHTML(name)}">${ids.map(id=>`<option value="${id}" ${id===current?'selected':''}>${escapeHTML(MOOD_LABELS[id])}</option>`).join('')}</optgroup>`).join('');
  return families.map(f=>`<option value="${f.id}" ${f.id===current?'selected':''}>${escapeHTML(voiceLabel(f.id))}</option>`).join('');
}
function card(row) {
  const {voice,mood}=labels(row),d=decision(row.id);
  const characterHeading=speakerHeading(row);
  return `<article data-id="${escapeHTML(row.id)}"><div class="clip-heading"><h2>${escapeHTML(characterHeading)}</h2><span class="acoustic-header">${acousticWarningHeader(row.id)}</span></div>`+
    `<div class="acoustic-body">${acousticWarningDetails(row.id)}</div>`+
    `<div class="muted"><span class="tag">${escapeHTML(row.chapter_id)}</span><span class="tag">${escapeHTML(row.presentation_channel_proposed||'Direct voice')}</span><span class="tag">${escapeHTML(row.id)}</span></div>`+
    `<p class="text">${escapeHTML(row.source_text.trim())}</p><audio controls preload="metadata" data-clip="${escapeHTML(row.id)}" data-bucket-source="${escapeHTML(audioURL(precision[row.id].audio))}"></audio>`+
    `<div class="labels"><label>Voice family<select data-field="voice" aria-label="Voice family">${options(voice)}</select></label><label>Mood<select data-field="mood" aria-label="Mood">${options(mood,true)}</select></label></div>`+
    `<div class="mood-suggestions">${moodChips(row)}</div>`+
    `<div class="actions"><button data-action="confirm" class="primary">Fits this bucket</button></div>`+
    `<p class="status clip-status ${verified(row)?'good':''}">${verified(row)?'Verified in this bucket':'Listen and check this clip'}</p>`+
    `<details><summary>Comment (optional)</summary><textarea aria-label="Comment" data-field="comment" placeholder="What sounds different?">${escapeHTML(d.bucket_comment||'')}</textarea></details></article>`;
}
function writeURL(push=false,clip=currentQueueClip()) {
  const url=new URL(location.href);url.searchParams.set('bucket',selected);url.searchParams.set('page',String(pageIndex+1));
  url.searchParams.delete('focus');
  if($('#filter').value!=='all')url.searchParams.set('show',$('#filter').value);else url.searchParams.delete('show');
  if(clip)url.searchParams.set('clip',clip);else url.searchParams.delete('clip');
  history[push?'pushState':'replaceState']({},'',url);
}
function readURL() {
  const params=new URL(location.href).searchParams,requested=params.get('bucket');
  const [voice,mood]=requested?.split('|')||[];
  if(requested&&families.some(f=>f.id===voice)&&MOODS.includes(mood))selected=requested;
  $('#filter').value=['pending','verified'].includes(params.get('show'))?params.get('show'):'all';
  pageIndex=Math.max(0,(parseInt(params.get('page'),10)||1)-1);
  // Report links locate the clip using its latest saved labels and display order.
  // A normal playback bookmark keeps its original queue and bucket semantics.
  linkedClip='';
  const target=params.get('focus')==='1'?byId.get(params.get('clip')):null;
  if(target&&eligible(target)){
    const {voice,mood}=labels(target);
    if(voice&&mood){
      selected=bucketKey(voice,mood);$('#filter').value='all';
      const position=filteredIds().indexOf(target.id);
      if(position>=0){pageIndex=Math.floor(position/PAGE_SIZE);linkedClip=target.id;}
    }
  }
}
function render(push=false,clip=currentQueueClip()) {
  const ids=filteredIds(),pages=Math.max(1,Math.ceil(ids.length/PAGE_SIZE));pageIndex=Math.min(pageIndex,pages-1);
  pageSnapshot=ids.slice(pageIndex*PAGE_SIZE,(pageIndex+1)*PAGE_SIZE);
  $('#cards').innerHTML=pageSnapshot.map(id=>card(byId.get(id))).join('')||'<section>No clips in this view.</section>';
  for(const suffix of ['', '-bottom']){
    $('#page-label'+suffix).textContent=`Page ${pageIndex+1} of ${pages}`;
    $('#prev'+suffix).disabled=pageIndex===0;$('#next'+suffix).disabled=pageIndex>=pages-1;
  }
  $('#bucket').value=selected;updateSummary();writeURL(push,clip);highlightPlaying();
  if(linkedClip){
    const target=clipCard(linkedClip);linkedClip='';
    if(target)requestAnimationFrame(()=>target.scrollIntoView({block:'start',behavior:'instant'}));
  }
}
function rebuildPopularMoods() {
  popularMoodCounts=new Map();
  for(const row of rows){
    const d=decision(row.id);if(d.rejected||!['yes','no'].includes(d.mood_verdict))continue;
    const {voice,mood}=labels(row);
    if(!voice||voice==='voice_pending'||!mood||mood==='mood_pending')continue;
    if(!popularMoodCounts.has(voice))popularMoodCounts.set(voice,new Map());
    const counts=popularMoodCounts.get(voice);counts.set(mood,(counts.get(mood)||0)+1);
  }
}
function moodChips(row) {
  if(row.parent_id)return '';
  const {voice,mood:current}=labels(row);
  const moods=[...new Set([...(row.mood_proposed||[]),...(row.hybrid?.top3||row.prediction?.tasks?.mood?.top3||[]).map(item=>item.label)].map(canonicalMood))]
    .filter(mood=>MOODS.includes(mood)&&mood!=='mood_pending').slice(0,4);
  const popular=[...(popularMoodCounts.get(voice)||[])].sort((a,b)=>b[1]-a[1]||MOODS.indexOf(a[0])-MOODS.indexOf(b[0]))
    .filter(([mood])=>!moods.includes(mood)).slice(0,2);
  const chip=(mood,source,count)=>`<button type="button" data-action="mood" data-mood="${escapeHTML(mood)}" data-source="${source}" aria-pressed="${mood===current}"${count?` title="${count} saved mood choices for ${escapeHTML(voiceLabel(voice))}"`:''}>${escapeHTML(MOOD_LABELS[mood]||mood)}</button>`;
  const chips=moods.map(mood=>chip(mood,'model')).join('')+popular.map(([mood,count])=>chip(mood,'popular',count)).join('');
  return chips?`<div class="mood-chips">${chips}</div>`:'';
}
function clipCard(id){return [...document.querySelectorAll('article')].find(el=>el.dataset.id===id);}
function updateCard(id,message) {
  const row=byId.get(id),el=clipCard(id);if(!el)return;
  const {voice,mood}=labels(row),moved=bucketKey(voice,mood)!==selected;
  el.classList.toggle('moved',moved);el.querySelector('[data-field=voice]').value=voice;el.querySelector('[data-field=mood]').value=mood;
  const status=el.querySelector('.clip-status');status.textContent=message||(verified(row)?'Verified in this bucket':'Listen and check this clip');
  status.classList.toggle('good',verified(row));
  const heading=el.querySelector('.clip-heading h2');if(heading)heading.textContent=speakerHeading(row);
  const chips=el.querySelector('.mood-suggestions');if(chips)chips.innerHTML=moodChips(row);
}
function visibleLabelState() {
  return new Map([...document.querySelectorAll('article')].map(el=>[el.dataset.id,{...labels(byId.get(el.dataset.id)),comment:decision(el.dataset.id).bucket_comment||''}]));
}
function hasStagedLabels(id) {
  const el=clipCard(id),l=labels(byId.get(id));
  return !!el&&['voice','mood'].some(field=>el.querySelector(`[data-field=${field}]`).value!==l[field]);
}
function syncVisibleLabels(before) {
  for(const el of document.querySelectorAll('article')){
    const id=el.dataset.id,row=byId.get(id),old=before.get(id),now=labels(row);
    if(!old)continue;
    // Keep dropdown choices the user has staged but has not moved yet.
    for(const field of ['voice','mood']){
      const control=el.querySelector(`[data-field=${field}]`);
      if(control.value===old[field])control.value=now[field]||'';
    }
    const comment=el.querySelector('[data-field=comment]');if(comment.value===old.comment)comment.value=decision(id).bucket_comment||'';
    if(!eligible(row)){
      el.querySelector('.clip-status').textContent='Held by audio or boundary checks. This clip is excluded from the buckets.';
    }else if(old.voice!==now.voice||old.mood!==now.mood){
      const moved=bucketKey(now.voice,now.mood)!==selected;el.classList.toggle('moved',moved);
      el.querySelector('.clip-status').textContent=`Saved labels changed: ${bucketLabel(bucketKey(now.voice,now.mood))}.`;
    }else if(verified(row)){
      el.querySelector('.clip-status').textContent='Verified in this bucket';
    }else if(el.querySelector('.clip-status').textContent==='Verified in this bucket'){
      el.querySelector('.clip-status').textContent='Listen and check this clip';
    }
    el.querySelector('.clip-status').classList.toggle('good',verified(row));
  }
  updateSummary();
}
function queueSave() {
  store.stash();$('#save-status').textContent='Saving changes…';clearTimeout(saveTimer);saveTimer=setTimeout(saveNow,400);
}
async function saveNow() {
  clearTimeout(saveTimer);if(!loaded)return;
  const before=visibleLabelState(),generation=++saveGeneration;
  $('#save-status').className='status';$('#save-status').textContent='Saving changes…';
  try{await store.save();if(generation===saveGeneration){
      const pending=Object.keys(store.changes()).length||store.reviewer!==store.baseReviewer;
      $('#save-status').className='status '+(pending?'':'good');$('#save-status').textContent=pending?'Saving changes…':'Saved locally';
    }rebuildBuckets();syncVisibleLabels(before);}
  catch(error){if(generation!==saveGeneration)return;$('#save-status').className='status error';$('#save-status').textContent=error.message+' Use Export review JSON for a backup.';
    reviewConflictControls(store,$('#save-status'),async()=>{await saveNow();render();});}
}
function rememberNextClip(id) {
  const prior=nextClipQueues.get(id),filter=$('#filter').value;
  if(prior?.bucket===selected&&prior.filter===filter)return;
  const ids=currentQueueClip()===id?[...queue]:filteredIds(),index=ids.indexOf(id);
  if(index>=0)nextClipQueues.set(id,{queue:[...ids],index,bucket:selected,filter});
}
function playNextClip(id) {
  stopSequence();
  if(currentQueueClip()!==id){
    const saved=nextClipQueues.get(id),filter=$('#filter').value;
    queue=saved?.bucket===selected&&saved.filter===filter?[...saved.queue]:[...filteredIds()];
    queueBucket=selected;queueFilter=filter;queueIndex=queue.indexOf(id);
  }
  if(queueIndex>=0&&queue[queueIndex]===id){rebaseQueueAfterCursor();playQueued(queueIndex+1,0,false);}
}
function moveClip(id,voice,mood,field='') {
  const row=byId.get(id),before=reviewClone(decision(id)),old=labels(row);
  if(!families.some(f=>f.id===voice)||!MOODS.includes(mood))throw Error('Choose a known voice and mood');
  if(old.voice===voice&&old.mood===mood&&!field)return;
  rememberNextClip(id);
  const after={...before,reviewed_at:new Date().toISOString(),review_origin:'bucket_move'};
  if(old.voice!==voice||field==='voice'){after.voice_verdict='no';after.corrected_voice_id=voice;delete after.confirmed_voice_id;}
  if(old.mood!==mood||field==='mood'){after.mood_verdict='no';after.corrected_mood=mood;after.mood_scope='sentence';}
  delete after.bucket_review;delete after.playback_acceptance;delete after.listening_evidence;store.decisions[id]=after;
  undoMove={id,before,after};sessionStorage.setItem(store.key+'-undo',JSON.stringify(undoMove));$('#undo').disabled=false;
  queueSave();rebuildBuckets();updateCard(id,`Moved to ${bucketLabel(bucketKey(voice,mood))}. It stays here until you change page. Undo is available.`);
}
function undoLastMove() {
  if(!undoMove)return;const {id,before,after}=undoMove,current=decision(id);
  if(LABEL_FIELDS.some(field=>!reviewEqual(current[field],after[field]))){$('#save-status').textContent='This clip changed again. Choose its voice and mood directly instead of undoing.';return;}
  const restored={...current};for(const field of LABEL_FIELDS){if(Object.hasOwn(before,field))restored[field]=before[field];else delete restored[field];}
  restored.reviewed_at=new Date().toISOString();restored.review_origin='bucket_undo';store.decisions[id]=restored;
  undoMove=null;sessionStorage.removeItem(store.key+'-undo');$('#undo').disabled=true;queueSave();rebuildBuckets();updateCard(id,'Move undone.');
}
function confirmClip(id) {
  const row=byId.get(id),{voice,mood}=labels(row);if(!eligible(row)||!voice||!mood)return;
  const d=decision(id);store.decisions[id]={...d,bucket_review:{verdict:'consistent',voice_family_id:voice,mood,audio_sha256:precision[id].audio_sha256,checked_at:new Date().toISOString()}};
  queueSave();updateCard(id);updateSummary();
}
function highlightPlaying() {
  const id=currentQueueClip();for(const el of document.querySelectorAll('article'))el.classList.toggle('playing',el.dataset.id===id);
}
function acousticWarningHeader(id) {
  return acousticState.warnings.has(id)?'<span class="acoustic-warning" role="note">⚠ Unusual for this bucket</span>':'';
}
function acousticWarningDetails(id) {
  const warning=acousticState.warnings.get(id),plot=acousticPitchPlot(id,acousticState);
  if(!warning)return plot?'<details class="pitch-details"><summary>Pitch shape</summary>'+plot+'</details>':'';
  return acousticBucketHints(id)+'<details class="acoustic-details"><summary>Why this clip is unusual</summary>'+plot+
    warning.differences.map(d=>`<p>${escapeHTML(acousticDifferenceText(d))}</p>`).join('')+
    `<p>Compared with ${warning.peerClips} clips from other paragraphs in the same voice + mood bucket. Listen before changing the label. A rising pitch does not automatically mean surprised or excited. Pitch can be measured incorrectly; pace is an estimate from the audio.</p></details>`;
}
function acousticBucketHints(id) {
  const hints=acousticState.suggestions?.get(id)||[],current=acousticState.currentReferences?.get(id);
  if(!hints.length&&!current)return '<p class="small acoustic-no-hint">This clip is unusual, but that does not mean its label is wrong. There are not enough measured examples for a useful comparison yet.</p>';
  const preview=hints.map(h=>MOOD_LABELS[h.bucket.split('|')[1]]||h.bucket.split('|')[1]).join(', ');
  return `<details class="acoustic-hints"><summary>${hints.length?'Closest moods to compare: '+escapeHTML(preview):'Compare with the current bucket'}</summary>`+
    `<p class="small">${hints.some(h=>h.clearer)?'There is a closer measured match below. Listen to its example before moving.':'No clear winner. These are the nearest alternatives to listen to; keeping the current label may be right.'} Comparisons stay within the same voice family.</p>`+
    hints.map(h=>{
      const url=new URL(location.href);url.search='';url.searchParams.set('bucket',h.bucket);url.searchParams.set('page','1');
      return `<div class="hint-choice"><b>${escapeHTML(bucketLabel(h.bucket))}</b>`+
        `<p class="small hint-strength">${h.clearer?'Stronger measured match':h.closer?'Slightly closer by measurements; uncertain':'Comparison only: the current bucket is at least as close overall'}</p>`+
        `<p class="small">Compared with ${h.peerClips} clips from other paragraphs in this bucket.</p>`+
        h.reasons.map(d=>`<p class="small">${escapeHTML(d.shape?(d.closer?'Pitch shape is closer to this bucket average.':'Pitch shape is not closer to this bucket average.'):acousticDifferenceText(d))}</p>`).join('')+
        `<div class="actions"><button data-action="hint" data-bucket="${escapeHTML(h.bucket)}">Use these choices</button><a href="${escapeHTML(url.href)}" target="_blank" rel="noopener">Open bucket</a></div>`+
        `<details class="hint-example" data-example="${escapeHTML(h.example)}"><summary>Listen to an example</summary>`+
        `<p class="text">${escapeHTML(byId.get(h.example).source_text.trim())}</p>`+
        `<audio controls preload="none" aria-label="Example from ${escapeHTML(bucketLabel(h.bucket))}" data-bucket-source="${escapeHTML(audioURL(precision[h.example].audio))}"></audio></details></div>`;
    }).join('')+(current?`<div class="hint-choice current-reference"><b>Current bucket: ${escapeHTML(bucketLabel(current.bucket))}</b>`+
      '<p class="small">Listen to a nearby example that has no audio warning. If yours still belongs here, keep its label and use Fits this bucket after listening.</p>'+
      `<details class="hint-example" data-example="${escapeHTML(current.example)}"><summary>Listen to the current bucket</summary>`+
      `<p class="text">${escapeHTML(byId.get(current.example).source_text.trim())}</p>`+
      `<audio controls preload="none" aria-label="Current bucket example" data-bucket-source="${escapeHTML(audioURL(precision[current.example].audio))}"></audio></details></div>`:'')+'</details>';
}
function refreshAcousticWarnings() {
  for(const el of document.querySelectorAll('#cards article')){
    const id=el.dataset.id,header=el.querySelector('.acoustic-header'),body=el.querySelector('.acoustic-body');
    if(header)header.innerHTML=acousticWarningHeader(id);
    if(body){const opened=new Set([...body.querySelectorAll('details[open]')].map(d=>d.dataset.example||d.className));
      body.innerHTML=acousticWarningDetails(id);for(const detail of body.querySelectorAll('details'))if(opened.has(detail.dataset.example||detail.className))detail.open=true;}
  }
}
const playbackKey=()=>store.key+'-bucket-playback';
const playbackView=()=>`${selected}::${$('#filter').value}`;
function currentQueueClip() {
  return queueBucket===selected&&queueFilter===$('#filter').value?queue[queueIndex]||'':'';
}
function updatePlayerControls() {
  const running=sequenceActive||playPending||[...document.querySelectorAll('audio')].some(audio=>!audio.paused);
  const resumable=!!currentQueueClip()&&!queueComplete;
  $('#play-bucket').textContent=queueComplete&&queueBucket===selected?'Play all again':resumable?'Resume all':'Play all';
  $('#play-bucket').disabled=running||(!resumable&&!filteredIds().length);
  $('#stop').disabled=!running;
  $('#next-clip').disabled=!currentQueueClip()||queueComplete;
}
function rememberPosition(force=false) {
  if(!loaded||queueIndex<0||!queue[queueIndex])return;
  if(!force&&Date.now()-lastPositionSave<1000)return;
  const id=queue[queueIndex],audio=cursorAudio;
  if(audio?.dataset.clip===id&&Number.isFinite(audio.currentTime))cursorTime=globalThis.BucketHls?.sourceTime(audio)??audio.currentTime;
  playbackPositions[`${queueBucket}::${queueFilter}`]={queue:[...queue],index:queueIndex,
    clip:id,time:cursorTime,audio_sha256:precision[id]?.audio_sha256,
    ended:gapPending||!!(audio?.dataset.clip===id&&audio.ended),complete:queueComplete};
  // Merge positions from other tabs without modifying any review answers.
  try{const saved=JSON.parse(localStorage.getItem(playbackKey())||'{}');
    localStorage.setItem(playbackKey(),JSON.stringify({...saved,[`${queueBucket}::${queueFilter}`]:playbackPositions[`${queueBucket}::${queueFilter}`]}));
  }catch{}
  lastPositionSave=Date.now();
}
function rebaseQueueAfterCursor() {
  if(queueIndex<0||!queue[queueIndex]||queueBucket!==selected||queueFilter!==$('#filter').value)return;
  const before=queue.slice(0,queueIndex),current=queue[queueIndex],seen=new Set([...before,current]);
  // Keep everything already heard and the current cursor fixed. Rebuild only
  // the remaining queue so new warnings and moved clips use today's ordering.
  queue=[...before,current,...filteredIds().filter(id=>!seen.has(id))];
}
function restorePosition(fromURL=false) {
  queue=[];queueIndex=-1;queueBucket=selected;queueFilter=$('#filter').value;
  cursorTime=0;cursorAudio=null;gapPending=false;queueComplete=false;
  let saved=playbackPositions[playbackView()];
  try{saved=JSON.parse(localStorage.getItem(playbackKey())||'{}')[playbackView()]||saved;}catch{}
  if(saved&&Array.isArray(saved.queue)&&Number.isInteger(saved.index)&&saved.queue[saved.index]===saved.clip&&
      saved.queue.every(id=>typeof id==='string'&&byId.has(id))){
    queue=[...saved.queue];queueIndex=saved.index;cursorTime=Number.isFinite(saved.time)?Math.max(0,saved.time):0;
    if(saved.audio_sha256!==precision[saved.clip]?.audio_sha256)cursorTime=0;
    gapPending=saved.audio_sha256===precision[saved.clip]?.audio_sha256&&saved.ended===true;
    queueComplete=saved.complete===true;
    rebaseQueueAfterCursor();
  }
  const urlClip=fromURL?new URL(location.href).searchParams.get('clip'):null;
  if(urlClip&&byId.has(urlClip)&&urlClip!==queue[queueIndex]){
    const view=filteredIds(),index=view.indexOf(urlClip);
    // A moved clip may still be named in the URL. Its original order tells us
    // where to continue even when no saved queue is available.
    queue=index>=0?view:[urlClip,...view.filter(id=>rows.indexOf(byId.get(id))>rows.indexOf(byId.get(urlClip)))];
    queueIndex=index>=0?index:0;cursorTime=0;gapPending=false;queueComplete=false;
  }
  const id=currentQueueClip();
  $('#sequence-title').textContent=id?`${queueComplete?'Finished':'Paused'} · ${queueIndex+1} of ${queue.length} · ${byId.get(id).source_text.trim()}`:'Choose a bucket, then start listening.';
  $('#sequence-title').title=$('#sequence-title').textContent;
  updatePlayerControls();highlightPlaying();
}
function stopSequence(allAudio=true) {
  rememberPosition(true);playbackEpoch++;sequenceActive=false;playPending=false;clearTimeout(gapTimer);
  cancelLoad?.();cancelLoad=null;
  if(allAudio)for(const audio of document.querySelectorAll('audio'))audio.pause();else $('#sequence').pause();
  rememberPosition(true);updatePlayerControls();highlightPlaying();
}
function queueClipAllowed(id) {
  const row=byId.get(id);if(!row||!eligible(row))return false;
  const l=labels(row);return bucketKey(l.voice,l.mood)===queueBucket&&
    (queueFilter==='all'||(queueFilter==='verified'?verified(row):!verified(row)));
}
function loadQueueAudio(id,time,epoch) {
  const audio=$('#sequence');
  return new Promise(resolve=>{
    let done=false;
    const finish=ready=>{if(done)return;done=true;audio.removeEventListener('loadedmetadata',onReady);audio.removeEventListener('error',onError);if(cancelLoad===cancel)cancelLoad=null;resolve(ready);};
    const cancel=()=>{globalThis.BucketHls?.cancel(audio);finish(false);},onError=()=>finish(false);
    const onReady=()=>{if(epoch!==playbackEpoch)return finish(false);
      if(time>0&&Number.isFinite(audio.duration))audio.currentTime=Math.min(time,Math.max(0,audio.duration-.01));finish(true);};
    cancelLoad=cancel;audio.addEventListener('loadedmetadata',onReady);audio.addEventListener('error',onError);
    audio.preload='metadata';audio.dataset.clip=id;globalThis.BucketHls.load(audio,audioURL(precision[id].audio),{sha256:precision[id].audio_sha256,offset:time}).catch(onError);
  });
}
async function playQueued(index,time=0,continuous=true) {
  rememberPosition(true);cancelLoad?.();const epoch=++playbackEpoch;clearTimeout(gapTimer);
  singleClipPlayback=!continuous;
  const requested=index;
  while(index<queue.length&&!queueClipAllowed(queue[index]))index++;
  if(index>=queue.length){stopSequence();queueComplete=true;gapPending=false;rememberPosition(true);updatePlayerControls();$('#sequence-title').textContent='End of bucket. Confirm the clips that fit, or move any outliers.';return;}
  queueIndex=index;queueComplete=false;gapPending=false;cursorTime=index===requested?time:0;
  cursorAudio=null;sequenceActive=true;playPending=true;const id=queue[index],row=byId.get(id);
  $('#sequence-title').textContent=`${index+1} of ${queue.length} · ${row.source_text.trim()}`;
  $('#sequence-title').title=$('#sequence-title').textContent;updatePlayerControls();rememberPosition(true);
  if($('#follow').checked){
    selected=queueBucket;rebuildBuckets();const position=filteredIds().indexOf(id);
    if(position>=0&&(!pageSnapshot.includes(id)||!clipCard(id))){pageIndex=Math.floor(position/PAGE_SIZE);render(false,id);}
    else writeURL(false,id);
    clipCard(id)?.scrollIntoView({block:'center',behavior:'smooth'});
  }
  if(!await loadQueueAudio(id,cursorTime,epoch)||epoch!==playbackEpoch)return;
  cursorAudio=$('#sequence');
  await resumeSequence(epoch);
}
async function resumeSequence(epoch=playbackEpoch) {
  try{await $('#sequence').play();if(epoch!==playbackEpoch)return;playPending=false;sequenceActive=true;rememberPosition(true);updatePlayerControls();highlightPlaying();}
  catch(error){if(epoch!==playbackEpoch)return;playPending=false;sequenceActive=false;rememberPosition(true);updatePlayerControls();$('#sequence-title').textContent=`Could not play: ${error.message}. Press Resume all to retry.`;}
}
$('#play-bucket').onclick=async()=>{
  if(sequenceActive||playPending)return;
  rememberPosition(true);
  if(currentQueueClip()&&!queueComplete){rebaseQueueAfterCursor();await playQueued(queueIndex+(gapPending?1:0),gapPending?0:cursorTime);return;}
  queue=[...filteredIds()];queueBucket=selected;queueFilter=$('#filter').value;queueIndex=-1;await playQueued(0);
};
$('#next-clip').onclick=()=>{if(currentQueueClip()){rebaseQueueAfterCursor();playQueued(queueIndex+1);}};$('#stop').onclick=()=>stopSequence();
function scheduleNextClip(){clearTimeout(gapTimer);if(!singleClipPlayback)gapTimer=setTimeout(()=>{rebaseQueueAfterCursor();playQueued(queueIndex+1);},betweenClipGapMs);}
$('#sequence').onended=()=>{if(sequenceActive){gapPending=true;if(singleClipPlayback){stopSequence();return;}rememberPosition(true);scheduleNextClip();}};
$('#sequence').onpause=()=>{if(!playPending&&!$('#sequence').ended&&$('#sequence').paused){sequenceActive=false;clearTimeout(gapTimer);rememberPosition(true);updatePlayerControls();}};
$('#sequence').onplay=()=>{if(currentQueueClip())sequenceActive=true;cursorAudio=$('#sequence');updatePlayerControls();};
$('#sequence').onerror=()=>{
  if(!sequenceActive)return;stopSequence();$('#sequence-title').textContent='Could not load this clip. Check the local server and press Resume all to retry.';
};
function setClipGap(seconds,persist=true){
  const value=Math.min(10,Math.max(0,Math.round(Number(seconds)*2)/2));betweenClipGapMs=value*1000;
  $('#clip-gap').value=String(value);$('#clip-gap-value').textContent=value.toFixed(1)+'s';
  if(persist)try{localStorage.setItem('rimskify-clip-gap-seconds',String(value));}catch{}
  if(gapPending&&sequenceActive)scheduleNextClip();
}
try{setClipGap(localStorage.getItem('rimskify-clip-gap-seconds')??2,false);}catch{setClipGap(2,false);}
$('#clip-gap').oninput=()=>setClipGap($('#clip-gap').value);
document.addEventListener('play',event=>{
  if(!(event.target instanceof HTMLAudioElement))return;
  if(event.target!==$('#sequence')){
    stopSequence(false);const audio=event.target,id=audio.dataset.clip;
    if(id&&filteredIds().includes(id)){
      if(!currentQueueClip()||!queue.includes(id)){queue=[...filteredIds()];queueBucket=selected;queueFilter=$('#filter').value;}
      queueIndex=queue.indexOf(id);cursorAudio=audio;cursorTime=globalThis.BucketHls?.sourceTime(audio)??audio.currentTime;gapPending=false;queueComplete=false;
      $('#sequence-title').textContent=`${queueIndex+1} of ${queue.length} · ${byId.get(id).source_text.trim()}`;
      rememberPosition(true);writeURL();highlightPlaying();
    }
  }
  for(const audio of document.querySelectorAll('audio'))if(audio!==event.target)audio.pause();
  updatePlayerControls();
},true);
document.addEventListener('pause',event=>{if(event.target instanceof HTMLAudioElement){if(event.target===cursorAudio)rememberPosition(true);updatePlayerControls();}},true);
document.addEventListener('ended',event=>{if(event.target===cursorAudio&&event.target!==$('#sequence')){gapPending=true;rememberPosition(true);updatePlayerControls();}},true);
function mergePlayedRanges(ranges) {
  const merged=[];
  for(const [start,end] of ranges.sort((a,b)=>a[0]-b[0])){
    if(end<=start)continue;
    if(merged.length&&start<=merged.at(-1)[1])merged.at(-1)[1]=Math.max(end,merged.at(-1)[1]);
    else merged.push([start,end]);
  }
  return merged;
}
// HTMLMediaElement.played records actual playback and excludes seeks and repeated listening.
document.addEventListener('loadstart',event=>{
  const audio=event.target,id=audio.dataset?.clip;
  if(audio instanceof HTMLAudioElement&&id&&loaded)playedEvidence.set(audio,{id,sig:fingerprint(byId.get(id)),last:0});
},true);
for(const eventName of ['seeking','seeked'])document.addEventListener(eventName,event=>{
  const evidence=playedEvidence.get(event.target);if(evidence)evidence.last=eventName==='seeking'?null:event.target.currentTime;
  if(event.target===cursorAudio){
    if(eventName==='seeking'&&gapPending){clearTimeout(gapTimer);gapPending=false;if(event.target.paused)sequenceActive=false;}
    if(eventName==='seeked'){rememberPosition(true);updatePlayerControls();}
  }
},true);
document.addEventListener('timeupdate',event=>{if(event.target===cursorAudio)rememberPosition();},true);
document.addEventListener('timeupdate',event=>{
  const audio=event.target,id=audio.dataset?.clip;if(!(audio instanceof HTMLAudioElement)||!id||!loaded)return;
  if(!globalThis.BucketHls?.isOriginal(audio,audioURL(precision[id].audio)))return;
  const row=byId.get(id),sig=fingerprint(row),entry=heardRanges.get(id);
  const raw=Array.from({length:audio.played.length},(_,index)=>[audio.played.start(index),audio.played.end(index)]);
  let evidence=playedEvidence.get(audio);
  if(!evidence||evidence.id!==id||evidence.sig!==sig)evidence={id,sig,last:null};
  playedEvidence.set(audio,evidence);
  const heard=entry?.sig===sig?entry:{sig,ranges:[]};
  // Intersect this playback interval with native evidence; old ranges cannot be
  // credited after a label change, and replaying after a move can count again.
  if(!audio.seeking&&evidence.last!==null&&audio.currentTime>=evidence.last){
    const fresh=raw.map(([a,b])=>[Math.max(a,evidence.last),Math.min(b,audio.currentTime)]).filter(([a,b])=>b>a);
    heard.ranges=mergePlayedRanges([...heard.ranges,...fresh]);
  }
  evidence.last=audio.seeking?null:audio.currentTime;heardRanges.set(id,heard);
  if(Number.isFinite(audio.duration)&&heard.ranges.reduce((sum,[a,b])=>sum+b-a,0)>=audio.duration*.75){listened.set(id,sig);updateSummary();}
},true);
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-action]'),el=button?.closest('article');if(!el)return;
  if(button.dataset.action==='hint'){
    const hint=acousticState.suggestions?.get(el.dataset.id)?.find(h=>h.bucket===button.dataset.bucket);
    if(!hint)return;
    const [voice,mood]=hint.bucket.split('|');
    el.querySelector('[data-field=voice]').value=voice;el.querySelector('[data-field=mood]').value=mood;
    moveClip(el.dataset.id,voice,mood);
  }

  if(button.dataset.action==='mood'){
    const id=el.dataset.id;moveClip(id,labels(byId.get(id)).voice,button.dataset.mood,'mood');playNextClip(id);
  }
  if(button.dataset.action==='confirm'){
    const l=labels(byId.get(el.dataset.id));
    if(el.querySelector('[data-field=voice]').value!==l.voice||el.querySelector('[data-field=mood]').value!==l.mood){
      el.querySelector('.clip-status').textContent='Choose a voice or mood to save it before confirming.';
    }else confirmClip(el.dataset.id);
  }
});
document.addEventListener('input',event=>{if(event.target.dataset.field!=='comment')return;const id=event.target.closest('article').dataset.id;store.decisions[id]={...decision(id),bucket_comment:event.target.value};queueSave();});
$('#confirm-listened').onclick=()=>{for(const id of buckets.get(selected)||[])if(listened.get(id)===fingerprint(byId.get(id))&&!hasStagedLabels(id))confirmClip(id);};
document.addEventListener('change',event=>{
  const field=event.target.dataset.field;if(!['voice','mood'].includes(field))return;
  const el=event.target.closest('article'),id=el.dataset.id,current=labels(byId.get(id));
  moveClip(id,field==='voice'?event.target.value:current.voice,field==='mood'?event.target.value:current.mood,field);
  if(field==='mood')playNextClip(id);
});
$('#undo').onclick=undoLastMove;$('#save').onclick=saveNow;
$('#bucket').onchange=()=>{stopSequence();selected=$('#bucket').value;pageIndex=0;restorePosition();render(true);};
$('#filter').onchange=()=>{stopSequence();pageIndex=0;restorePosition();render(true);};
for(const suffix of ['','-bottom']){ $('#prev'+suffix).onclick=()=>{pageIndex--;render(true);};$('#next'+suffix).onclick=()=>{pageIndex++;render(true);}; }
$('#refresh').onclick=async()=>{
  try{await store.save();await store.refresh();rebuildBuckets();render();$('#save-status').textContent='Latest saved labels loaded';}
  catch(error){$('#save-status').textContent=error.message;reviewConflictControls(store,$('#save-status'),async()=>{await saveNow();render();});}
};
$('#export').onclick=()=>{const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([JSON.stringify(store.payload(),null,2)],{type:'application/json'}));link.download='book1-grouped-review.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);};
window.addEventListener('popstate',()=>{if(loaded){stopSequence();readURL();rebuildBuckets();restorePosition(true);render();}});
window.addEventListener('beforeunload',()=>{if(loaded){rememberPosition(true);store.stash();}});
document.addEventListener('voice-family-taxonomy-changed',event=>{
  mergeGlobalFamilies(event.detail);
  if(loaded){const active=currentQueueClip();rebuildBuckets();render(false,active);}
});
const playerSize=new ResizeObserver(()=>document.documentElement.style.setProperty('--player-height',`${$('.player-bar').getBoundingClientRect().height}px`));
playerSize.observe($('.player-bar'));
async function init() {
  const fetchJSON=async path=>{const response=await fetch(path,{cache:'no-store'});if(!response.ok)throw Error(`${path}: HTTP ${response.status}`);return response.json();};
  const [summary,taxonomy,p,q,raw]=await Promise.all([fetchJSON('summary.json'),fetchJSON('voice-families.json'),fetchJSON('precision-v2/status.json'),fetchJSON('quality-screen.json'),fetch('candidates.jsonl').then(r=>{if(!r.ok)throw Error('Could not load clips');return r.text();})]);
  try{
    const features=await fetchJSON('audio-acoustics.json');
    if(features.schema!==1||features.candidates_sha256!==summary.candidates_sha256||!features.clips)throw Error('Audio measurements do not match this corpus.');
    acousticFeatures=features.clips;
  }catch(error){acousticLoadMessage='Audio comparisons unavailable. '+error.message;}
  if(p.candidates_sha256!==summary.candidates_sha256||q.candidates_sha256!==summary.candidates_sha256)throw Error('Clip checks do not match current candidates');
  families=taxonomy.families;aliases=taxonomy.legacy_aliases||{};precision=p.clips;quality=q.clips;
  try{mergeGlobalFamilies(await fetchJSON('/voice-family-registry/vlad-rimsky-v1/data'));}catch{}
  rows=raw.trim().split('\n').map(JSON.parse).sort((a,b)=>a.recording_id.localeCompare(b.recording_id)||a.audio_start-b.audio_start);byId=new Map(rows.map(row=>[row.id,row]));
  const overrideResponse=await fetch('voice-proposal-overrides.json',{cache:'no-store'});
  if(overrideResponse.ok){const overrides=await overrideResponse.json();
    if(overrides.candidates_sha256&&overrides.candidates_sha256!==summary.candidates_sha256)throw Error('Context proposals are stale');
    for(const row of rows){row.original_voice_id_proposed=row.base_voice_id_proposed;Object.assign(row,overrides.clips?.[row.id]||{});}
  }
  store=new Book1ReviewStore(summary);await store.load();loaded=true;
  try{undoMove=JSON.parse(sessionStorage.getItem(store.key+'-undo')||'null');}catch{}$('#undo').disabled=!undoMove;
  try{playbackPositions=JSON.parse(localStorage.getItem(playbackKey())||'{}');}catch{}
  rebuildBuckets();readURL();rebuildBuckets();restorePosition(true);render();$('#save-status').textContent='Saved labels loaded';if(Object.keys(store.changes()).length||store.reviewer!==store.baseReviewer)saveNow();
  initialiseBookNavigation(store,stopSequence);
}
init().catch(error=>{$('#save-status').className='status error';$('#save-status').textContent='Could not load grouped review: '+error.message;});
