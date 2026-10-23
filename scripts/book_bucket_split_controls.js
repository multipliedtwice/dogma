// Controls for the shared bucket player. Split drafts are kept in this tab;
// saved decisions live under the original candidate, alongside its old answer.
const splitDrafts=new Map();
let sourceContexts={};
function speakerChoiceLabel(value){return value==='narrator'?'Narrator':value;}
function speakerOptions(row){
  const selected=decision(row.id).corrected_character_id?.trim()||'';
  const model=row.character_display||row.character_id||row.role||'unknown';
  const names=[...new Set(rows.filter(item=>!item.parent_id&&!item.comparison_book&&item.role==='character')
    .map(item=>item.character_display||item.character_id).filter(Boolean))].sort((a,b)=>a.localeCompare(b));
  if(!names.includes('narrator'))names.unshift('narrator');
  if(selected&&!names.includes(selected))names.push(selected);
  return `<option value="" ${selected?'':'selected'}>Keep model attribution: ${escapeHTML(speakerChoiceLabel(model))}</option>`+
    names.map(name=>`<option value="${escapeHTML(name)}" ${name===selected?'selected':''}>${escapeHTML(speakerChoiceLabel(name))}</option>`).join('');
}
function speakerControl(row){
  if(!proposalMode||row.parent_id)return '';
  return `<fieldset class="speaker-attribution"><legend>Speaker attribution</legend><label>Who performs this clip?<select data-field="speaker" aria-label="Speaker attribution">${speakerOptions(row)}</select></label><p class="small">Change this only when the displayed character is wrong. It is saved separately from the voice family.</p></fieldset>`;
}
function sourceContext(row){
  const context=sourceContexts[row.source_id];
  if(!context)return `<p class="text">${escapeHTML(row.paragraph_text||row.source_text)}</p>`;
  const p=(label,value)=>value?`<p class="small"><b>${label}</b></p><p class="text">${escapeHTML(value)}</p>`:'';
  return `<p><b>${escapeHTML(context.chapter)}</b></p>`+p('Before',context.before)+p('Current paragraph',context.current)+p('After',context.after);
}
function compactDetails(row){
  const extra=document.createElement('div');extra.innerHTML=row.parent_id?splitPartNotice(row):pilotHints(row);
  for(const summary of extra.querySelectorAll('details>summary'))if(summary.textContent==='Full paragraph for context')summary.parentElement.remove();
  return `<details class="clip-details"><summary>Details</summary>${speakerControl(row)}<div class="clip-context"><h3>Text context</h3>${sourceContext(row)}</div>${extra.innerHTML}</details>`;
}
function cardMoodControl(row,mood){
  if(row.parent_id)return `<label>Mood<button type="button" class="split-mix-mood" data-field="mood" value="${escapeHTML(mood)}" data-open-split aria-label="Mood">Split mix</button></label>`;
  return `<label>Mood<select data-field="mood" aria-label="Mood">${options(mood,true)}</select></label>`;
}
function splitOptions(current,mood=false){
  const pending=mood?'mood_pending':'voice_pending';
  const placeholder=current===pending||!current?`<option value="" selected disabled>${mood?'Choose mood':'Choose voice'}</option>`:'';
  if(mood)return placeholder+Object.entries(MOOD_GROUPS).map(([name,ids])=>{
    const allowed=ids.filter(id=>id!=='mood_pending');if(!allowed.length)return '';
    return `<optgroup label="${escapeHTML(name)}">${allowed.map(id=>`<option value="${id}" ${id===current?'selected':''}>${escapeHTML(MOOD_LABELS[id])}</option>`).join('')}</optgroup>`;
  }).join('');
  return placeholder+families.filter(f=>f.id!=='voice_pending').map(f=>`<option value="${f.id}" ${f.id===current?'selected':''}>${escapeHTML(f.label)}</option>`).join('');
}
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-open-split]');if(!button)return;
  const editor=button.closest('article').querySelector('.split-editor');if(editor)editor.open=true;
});
function splitPartNotice(row){
  const d=decision(row.id);
  return `${precision[row.id]?.alignment_hold?'<p class="acoustic-warning">The parent clip has approximate alignment. Keep this part out of training until its text and both audio edges are checked independently.</p>':''}<details class="split-audio-edges"><summary>Audio edges</summary><fieldset><legend>Audio edges</legend><label class="check"><input type="checkbox" data-edge="boundary_start" ${d.boundary_start?'checked':''}> Beginning is cut or includes extra words</label>`+
    `<label class="check"><input type="checkbox" data-edge="boundary_end" ${d.boundary_end?'checked':''}> Ending is cut or includes extra words</label></fieldset></details>`;
}
function splitDraft(id){
  const saved=store.decisions[id]?.split_review,plan=splitReview.plan.clips[id];
  const current=splitDrafts.get(id),baseSplit=JSON.stringify(saved||null);
  if(current&&(current.dirty||current.baseSplit===baseSplit))return current;
  let draft={cuts:[...(saved?.cuts||plan.suggested||[])],parts:reviewClone(saved?.parts||{}),baseSplit,dirty:false};
  try{const cached=JSON.parse(sessionStorage.getItem(store.key+'-split-draft-'+id)||'null');if(cached)draft=cached;}catch{}
  if(!draft.dirty&&draft.baseSplit!==baseSplit)draft={cuts:[...(saved?.cuts||plan.suggested||[])],parts:reviewClone(saved?.parts||{}),baseSplit,dirty:false};
  splitDrafts.set(id,draft);return draft;
}
function stashSplit(id){sessionStorage.setItem(store.key+'-split-draft-'+id,JSON.stringify(splitDraft(id)));}
function splitControls(row){
  if(!splitReview)return '';
  const id=row.parent_id||row.id,plan=splitReview.plan.clips[id];
  if(!plan?.cuts.length)return '<p class="small">No reliable word boundaries for a split yet.</p>';
  const parent=splitReview.parents.get(id),saved=store.decisions[id]?.split_review;
  return `<details class="split-editor" data-parent="${escapeHTML(id)}"><summary>${row.parent_id?'Change split, voices, or moods':'Different voices or moods? Split this clip'}</summary>`+
    `<p>Use a separate part when the voice or mood changes. This also works for dialogue followed by the narrator, even inside one sentence.</p>`+
    (row.parent_id?`<p class="text">${escapeHTML(parent.source_text)}</p><audio controls preload="none" src="${escapeHTML(audioURL(precision[id].audio))}"></audio>`:'')+
    '<p class="small">The split points come from word timing. Listen to the parts and adjust if needed. Labels save separately; new cuts stay out of training until their audio edges are checked.</p>'+
    `<label>Split before these words<select data-split-point>${plan.cuts.map(c=>`<option value="${c.offset}">${escapeHTML(c.after)}</option>`).join('')}</select></label>`+
    '<div class="actions"><button data-split-action="add">Add split point</button><button data-split-action="suggested">Use suggested splits</button></div>'+
    '<div class="split-parts"></div><div class="actions"><button class="primary" data-split-action="save">Save split</button>'+
    (saved?.enabled?'<button data-split-action="disable">Use whole clip again</button>':'')+
    '</div><p class="split-status" role="status"></p></details>';
}
function splitPartVoicePrefill(plan,parent,start,end){
  return (plan.voice_prefill_spans||[]).find(span=>{
    if(start>span.start||end<span.end)return false;
    const outside=parent.source_text.slice(start,span.start)+parent.source_text.slice(span.end,end);
    return /^[\s.,!?\u2026:;\u2014\u2013-]*$/.test(outside);
  })?.voice_family_id;
}
function drawSplitParts(editor){
  const id=editor.dataset.parent,draft=splitDraft(id),parent=splitReview.parents.get(id),base=labels(parent),plan=splitReview.plan.clips[id];
  const edges=[0,...draft.cuts.sort((a,b)=>a-b),parent.source_text.length];
  editor.querySelector('.split-parts').innerHTML=edges.slice(0,-1).map((start,i)=>{
    const end=edges[i+1],partId=splitReview.partId(id,start,end),answer=draft.parts[partId]||{};
    const voice=answer.corrected_voice_id||answer.confirmed_voice_id||splitPartVoicePrefill(plan,parent,start,end)||base.voice,mood=answer.corrected_mood||base.mood;
    return `<fieldset data-part="${escapeHTML(partId)}" data-start="${start}" data-end="${end}"><legend>Part ${i+1}</legend><p class="text">${escapeHTML(parent.source_text.slice(start,end))}</p>`+
      '<button data-split-action="preview">Play this part</button><audio controls preload="none" class="split-preview" hidden></audio>'+
      `<div class="labels"><label>Voice family<select data-split-label="voice">${splitOptions(voice)}</select></label><label>Mood<select data-split-label="mood">${splitOptions(mood,true)}</select></label></div>`+
      (i?`<button data-split-action="remove" data-offset="${start}">Remove split before this part</button>`:'')+'</fieldset>';
  }).join('');
}
function captureSplitParts(editor){
  const draft=splitDraft(editor.dataset.parent);
  for(const part of editor.querySelectorAll('[data-part]')){
    const before=draft.parts[part.dataset.part]||{};
    const voice=part.querySelector('[data-split-label=voice]').value,mood=part.querySelector('[data-split-label=mood]').value;
    const touched=draft.touched?.[part.dataset.part]||{},answer={...before,mood_scope:'part'};
    answer.label_provenance={
      voice:touched.voice?'listener_override':before.voice_verdict?'saved_listener_label':'model_prefill',
      mood:touched.mood?'listener_override':before.mood_verdict?'saved_listener_label':'model_prefill'};
    if(touched.voice){answer.voice_verdict='no';answer.corrected_voice_id=voice;delete answer.confirmed_voice_id;delete answer.bucket_review;}
    else if(!before.voice_verdict){delete answer.voice_verdict;delete answer.corrected_voice_id;delete answer.confirmed_voice_id;}
    if(touched.mood){answer.mood_verdict='no';answer.corrected_mood=mood;delete answer.bucket_review;}
    else if(!before.mood_verdict){delete answer.mood_verdict;delete answer.corrected_mood;}
    answer.review_origin=touched.voice||touched.mood?'mixed_delivery_split_override':'mixed_delivery_split_prefill';
    draft.parts[part.dataset.part]=answer;
  }
  stashSplit(editor.dataset.parent);
}
document.addEventListener('toggle',event=>{
  const editor=event.target;if(editor.matches?.('.split-editor')&&editor.open)drawSplitParts(editor);
},true);
document.addEventListener('change',event=>{
  if(event.target.dataset.field==='speaker'){
    const id=event.target.closest('article').dataset.id,value=event.target.value.trim();
    const next={...decision(id)};
    if(value){next.speaker_verdict='no';next.corrected_character_id=value;next.speaker_reviewed_at=new Date().toISOString();next.speaker_review_origin='bucket_speaker_correction';}
    else {delete next.speaker_verdict;delete next.corrected_character_id;delete next.speaker_reviewed_at;delete next.speaker_review_origin;}
    delete next.bucket_review;delete next.playback_acceptance;delete next.listening_evidence;
    setDecision(id,next);queueSave();updateCard(id,value?`Speaker corrected to ${speakerChoiceLabel(value)}.`:'Model speaker attribution restored.');
    return;
  }
  if(!event.target.dataset.splitLabel)return;
  const editor=event.target.closest('.split-editor'),draft=splitDraft(editor.dataset.parent),part=event.target.closest('[data-part]');
  draft.dirty=true;draft.touched=draft.touched||{};draft.touched[part.dataset.part]={...(draft.touched[part.dataset.part]||{}),[event.target.dataset.splitLabel]:true};
  captureSplitParts(editor);editor.querySelector('.split-status').textContent='Draft kept in this tab. Press Save split to apply.';
});

let playbackAcceptanceDirty=false;
function flushPlaybackAcceptances(){
  if(!playbackAcceptanceDirty)return;
  playbackAcceptanceDirty=false;queueSave();
}
// The listener asked for a no-button workflow. Keep full-play evidence apart
// from explicit label verdicts and training gates. It is saved as one batch
// when playback pauses, the view changes, or the bucket finishes.
function acceptFullyPlayedProposal(audio){
  if(globalThis.BucketHls?.isHls(audio))return;
  if(typeof proposalMode==='undefined'||!proposalMode||!(audio instanceof HTMLAudioElement))return;
  const id=audio.dataset.clip,row=byId.get(id);if(!id||!row||hasStagedLabels(id))return;
  const duration=audio.duration,covered=Number.isFinite(duration)?Array.from({length:audio.played.length},(_,i)=>audio.played.end(i)-audio.played.start(i)).reduce((a,b)=>a+b,0):0;
  if(!Number.isFinite(duration)||duration<=0||covered<duration*.75)return;
  const current=decision(id),{voice,mood}=labels(row);
  if(!eligible(row)||!voice||!mood||voice==='voice_pending'||current.boundary_start||current.boundary_end)return;
  const prior=current.playback_acceptance;
  if(prior?.audio_sha256===precision[id].audio_sha256&&prior.voice_family_id===voice&&prior.mood===mood)return;
  setDecision(id,{...current,playback_acceptance:{schema:1,voice_family_id:voice,mood,
    audio_sha256:precision[id].audio_sha256,coverage:Math.min(1,covered/duration),
    playback_rate:audio.playbackRate,playback_mode:audio.id==='sequence'?'play_all':'single_clip',
    tab_visible:document.visibilityState==='visible',proposal_source:{
      voice:row.base_voice_proposal_basis||'book1_voice_predictor',
      mood:row.hybrid?.reason||'book1_mood_model_and_audio_hints'},
    completed_at:new Date().toISOString()}});
  playbackAcceptanceDirty=true;store.stash();$('#save-status').textContent='Listening reviews kept in this browser until playback pauses.';
  updateCard(id);refreshAcousticWarnings();updateSummary();
  if(audio.id!=='sequence')flushPlaybackAcceptances();
}
document.addEventListener('ended',event=>acceptFullyPlayedProposal(event.target),true);
const stopSequenceBeforePlaybackBatch=stopSequence;
stopSequence=(...args)=>{const result=stopSequenceBeforePlaybackBatch(...args);flushPlaybackAcceptances();return result;};
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')flushPlaybackAcceptances();});
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-split-action]'),editor=button?.closest('.split-editor');if(!editor)return;
  const id=editor.dataset.parent,draft=splitDraft(id),plan=splitReview.plan.clips[id],status=editor.querySelector('.split-status');
  button.disabled=true;
  try{
    captureSplitParts(editor);
    switch(button.dataset.splitAction){
      case 'add':{const offset=Number(editor.querySelector('[data-split-point]').value);draft.dirty=true;draft.cuts=[...new Set([...draft.cuts,offset])];stashSplit(id);drawSplitParts(editor);break;}
      case 'suggested':draft.dirty=true;draft.cuts=[...plan.suggested];stashSplit(id);drawSplitParts(editor);if(!draft.cuts.length)status.textContent='No suggested change. Choose a split point yourself.';break;
      case 'remove':draft.dirty=true;draft.cuts=draft.cuts.filter(c=>c!==Number(button.dataset.offset));stashSplit(id);drawSplitParts(editor);break;
      case 'preview':{
        const field=button.closest('[data-part]'),start=Number(field.dataset.start),end=Number(field.dataset.end);
        const frame=offset=>offset===0?0:offset===splitReview.parents.get(id).source_text.length?plan.frames:plan.cuts.find(c=>c.offset===offset).frame;
        const media=await splitReview.cut(id,{id:field.dataset.part,first_frame:frame(start),last_frame:frame(end)},audioURL(precision[id].audio));
        const audio=field.querySelector('audio');audio.hidden=false;await globalThis.BucketHls.load(audio,media.url,{sha256:media.sha256});await audio.play();break;
      }
      case 'save':{
        const stayBucket=selected,priorIds=filteredIds(),priorIndex=Math.max(0,priorIds.indexOf(id));
        const ranges=splitReview.ranges(id,draft.cuts),parent=store.decisions[id]||{};
        const parts=Object.fromEntries(ranges.map((r,i)=>[r.id,{...draft.parts[r.id],
          ...(i===0&&parent.boundary_start?{boundary_start:true}:{}),
          ...(i===ranges.length-1&&parent.boundary_end?{boundary_end:true}:{}),
          reviewed_at:new Date().toISOString()}]));
        const boundary_proposals=[...draft.cuts].sort((a,b)=>a-b).map(offset=>{const proposal=plan.cuts.find(c=>c.offset===offset);return {
          offset,frame:proposal.frame,proposal_method:proposal.proposal_method||'legacy_word_alignment',suggested:plan.suggested.includes(offset)};});
        const after={...parent,split_review:{schema:1,enabled:true,candidates_sha256:splitReview.digest,parent_audio_sha256:plan.audio_sha256,
          cuts:[...draft.cuts].sort((a,b)=>a-b),parts,boundary_proposals,training_approved:false,updated_at:new Date().toISOString()}};
        // Prepare playback first. A failed audio fetch must not hide the whole clip.
        await splitReview.prepare({...store.decisions,[id]:after},p=>audioURL(precision[p].audio));
        for(const r of ranges)parts[r.id].split_source=splitReview.children.get(r.id).split_source;
        stopSequence();store.decisions[id]=after;sessionStorage.removeItem(store.key+'-split-draft-'+id);splitDrafts.delete(id);queueSave();rebuildBuckets();
        selected=stayBucket;const remaining=filteredIds(),focusId=remaining[Math.min(priorIndex,Math.max(0,remaining.length-1))]||'';
        pageIndex=focusId?Math.floor(remaining.indexOf(focusId)/PAGE_SIZE):0;
        linkedClip=focusId;queue=[];queueIndex=-1;cursorAudio=null;cursorTime=0;render(false,focusId);
        $('#save-status').textContent='Split saved. You stayed in the current bucket. Each part still needs its own audio-boundary check.';break;
      }
      case 'disable':{
        const parent=store.decisions[id];stopSequence();store.decisions[id]={...parent,split_review:{...parent.split_review,enabled:false}};
        sessionStorage.removeItem(store.key+'-split-draft-'+id);splitDrafts.delete(id);
        queueSave();rebuildBuckets();const label=labels(splitReview.parents.get(id));selected=bucketKey(label.voice,label.mood);pageIndex=0;
        queue=[];queueIndex=-1;render(false,id);break;
      }
    }
  }catch(error){status.textContent=error.message;status.className='split-status error';}
  finally{button.disabled=false;}
});
