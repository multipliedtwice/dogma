// Additive character-grouped review for the shared Book 1/2/3 bucket player.
// This changes presentation and queue order only. Voice edits continue through
// the existing conflict-safe review store and retain mood, split, and playback
// evidence unless the existing voice-change policy explicitly invalidates it.
(()=>{
  let characterMode=new URL(location.href).searchParams.get('view')==='character';
  let characterBuckets=new Map(),characterMeta=new Map(),modeSelections={bucket:'',character:''};
  let filterLabels=null;
  const base={rebuildBuckets,filteredIds,updateSummary,card,updateCard,readURL,writeURL,queueClipAllowed};

  const modeLabel=document.createElement('label');
  modeLabel.innerHTML='Group by <select id="grouping-mode" aria-label="Group clips by"><option value="bucket">Voice + mood</option><option value="character">Character</option></select>';
  const bucketControl=$('#bucket')?.closest('label');
  bucketControl?.parentElement?.insertBefore(modeLabel,bucketControl);
  const modeSelect=$('#grouping-mode');
  if(!modeSelect)return;
  modeSelect.value=characterMode?'character':'bucket';

  const filter=$('#filter'),filterLabel=filter?.closest('label');
  if(filter)filterLabels=[...filter.options].map(option=>({value:option.value,text:option.textContent}));
  const help=document.createElement('p');
  help.id='character-mode-help';help.className='small';help.hidden=true;
  help.textContent='Character view keeps every saved label intact. Minority voice families and clips without voice-review evidence appear first so you can compare one character across moods and chapters.';
  $('#bucket-summary')?.insertAdjacentElement('afterend',help);
  const style=document.createElement('style');
  style.textContent='.character-review-mode .character-voice-context{background:#eef4ee;border-left:3px solid #5f876a;padding:8px 11px;margin:10px 0}.character-review-mode article.character-voice-minority{border-color:#b8873c}.character-review-mode article.character-voice-unchecked .character-voice-context{background:#fff8e9;border-left-color:#c49342}.character-review-mode #confirm-listened,.character-review-mode article [data-action="confirm"]{display:none}.character-review-mode #voice-family-editor{display:none}';
  document.head.append(style);

  function characterIdentity(row){
    // A mixed parent's speaker cannot be projected onto its independently
    // voiced parts. Part-level speaker attribution is not collected yet.
    if(row.parent_id){
      const partSpeaker=decision(row.id).corrected_character_id?.trim();
      return partSpeaker?{id:partSpeaker,label:partSpeaker}:{id:'__split_unresolved__',label:'Split parts — speaker unresolved'};
    }
    const corrected=decision(row.id).corrected_character_id?.trim();
    const proposed=String(row.character_display||row.character_id||'').trim();
    if(corrected)return {id:corrected,label:corrected};
    if(proposed&&!['unknown','unresolved'].includes(proposed.toLowerCase()))return {id:proposed,label:proposed.replace(/^rb\d+_/,'').replaceAll('_',' ')};
    if(row.role==='narration')return {id:'__narrator__',label:'Narrator'};
    if(row.role==='game_ui')return {id:'__game_ui__',label:'Game voice'};
    return {id:'__unresolved__',label:'Unresolved speaker'};
  }
  const characterKey=row=>'character:'+encodeURIComponent(characterIdentity(row).id);
  function explicitVoiceReviewed(row){
    const d=decision(row.id),voice=labels(row).voice,audio=precision[row.id]?.audio_sha256;
    if(!voice)return false;
    if(['yes','no'].includes(d.voice_verdict))return true;
    return d.bucket_review?.voice_family_id===voice&&d.bucket_review.audio_sha256===audio;
  }
  function playedUnchanged(row){
    const d=decision(row.id),voice=labels(row).voice,audio=precision[row.id]?.audio_sha256;
    return !!voice&&d.playback_acceptance?.voice_family_id===voice&&d.playback_acceptance.audio_sha256===audio;
  }
  function voiceReviewState(row){return explicitVoiceReviewed(row)?'explicit':playedUnchanged(row)?'played':'needs';}
  function buildCharacterBuckets(){
    characterBuckets=new Map();characterMeta=new Map();
    const sourceOrder=new Map(rows.map((row,index)=>[row.id,index]));
    for(const row of rows){
      if(row.comparison_book||!eligible(row))continue;
      const key=characterKey(row),identity=characterIdentity(row);
      if(!characterBuckets.has(key))characterBuckets.set(key,[]);
      characterBuckets.get(key).push(row.id);
      if(!characterMeta.has(key))characterMeta.set(key,{...identity,voices:new Map(),pendingVoice:0,sourceOrder});
      const meta=characterMeta.get(key),voice=labels(row).voice||'voice_pending';
      if(voice==='voice_pending')meta.pendingVoice++;
      else meta.voices.set(voice,(meta.voices.get(voice)||0)+1);
      const state=voiceReviewState(row);meta[state]=(meta[state]||0)+1;
    }
    characterBuckets=new Map([...characterBuckets].sort((a,b)=>{
      const am=characterMeta.get(a[0]),bm=characterMeta.get(b[0]);
      return Number(bm.voices.size>1)-Number(am.voices.size>1)||b[1].length-a[1].length||am.label.localeCompare(bm.label);
    }));
  }
  function characterOptionLabel(key){
    const ids=characterBuckets.get(key)||[],meta=characterMeta.get(key),families=meta?.voices.size||0;
    return `${meta?.label||key} (${ids.length})${families>1?` · ${families} voice families`:''}`;
  }
  function installCharacterOptions(){
    const prior=selected;
    if(!characterBuckets.has(selected))selected=characterBuckets.keys().next().value||'';
    $('#bucket').innerHTML=[...characterBuckets.keys()].map(key=>`<option value="${escapeHTML(key)}" ${key===selected?'selected':''}>${escapeHTML(characterOptionLabel(key))}</option>`).join('');
    $('#bucket').value=selected;
    if(prior&&prior!==selected)pageIndex=0;
  }
  function setFilterPresentation(){
    if(!filter)return;
    const current=filter.value;
    if(characterMode){
      filter.innerHTML='<option value="all">All clips</option><option value="pending">Needs voice decision</option><option value="played">Played unchanged</option><option value="verified">Explicit voice decision</option>';
      filter.value=['all','pending','played','verified'].includes(current)?current:'all';
    }else if(filterLabels){
      filter.innerHTML=filterLabels.map(item=>`<option value="${item.value}">${escapeHTML(item.text)}</option>`).join('');
      filter.value=['all','pending','verified'].includes(current)?current:'all';
    }
  }
  function setModePresentation(){
    document.body.classList.toggle('character-review-mode',characterMode);
    modeSelect.value=characterMode?'character':'bucket';help.hidden=!characterMode;
    if(bucketControl?.firstChild?.nodeType===Node.TEXT_NODE)bucketControl.firstChild.nodeValue=characterMode?'Character ':'Bucket ';
    if(filterLabel?.firstChild?.nodeType===Node.TEXT_NODE)filterLabel.firstChild.nodeValue=characterMode?'Voice review ':'Show ';
    setFilterPresentation();
    const mixed=$('#mixed-mode');
    if(mixed){
      if(characterMode&&mixed.checked){mixed.checked=false;mixed.dispatchEvent(new Event('change',{bubbles:true}));}
      mixed.disabled=characterMode;const label=mixed.closest('label');if(label)label.title=characterMode?'Mixed Book 1 references are available in Voice + mood view.':'';
    }
  }
  function voiceDistribution(meta){
    const assigned=[...meta.voices].sort((a,b)=>b[1]-a[1]||voiceLabel(a[0]).localeCompare(voiceLabel(b[0])))
      .map(([voice,count])=>`${voiceLabel(voice)} ${count}`);
    if(meta.pendingVoice)assigned.push(`Needs voice review ${meta.pendingVoice}`);
    return assigned.join(' · ')||'No voice assigned';
  }
  function isMinority(row){
    const meta=characterMeta.get(characterKey(row)),voice=labels(row).voice||'voice_pending';
    if(!meta||voice==='voice_pending'||meta.voices.size<2)return false;
    const largest=Math.max(...meta.voices.values());return (meta.voices.get(voice)||0)<largest;
  }
  function characterHint(row){
    const key=characterKey(row),meta=characterMeta.get(key),voice=labels(row).voice;
    if(!meta)return '';
    const minority=isMinority(row),state=voiceReviewState(row),checked=state==='explicit';
    const lead=minority?'Compare this voice family with the character’s more common assignment. ':checked?'Voice assignment has an explicit saved decision. ':state==='played'?'Played unchanged; passive playback evidence is not a voice verdict. ':'Voice assignment still needs an explicit decision. ';
    return `<p class="character-voice-context"><b>${escapeHTML(voiceLabel(voice||'voice_pending'))}</b> · ${escapeHTML(lead)}<span class="small">Character distribution: ${escapeHTML(voiceDistribution(meta))}.</span></p>`;
  }

  rebuildBuckets=function(){
    if(!characterMode){base.rebuildBuckets();return;}
    const keep=selected;characterMode=false;base.rebuildBuckets();characterMode=true;selected=keep;
    buildCharacterBuckets();installCharacterOptions();
    $('#summary').textContent=`${[...characterBuckets.values()].reduce((sum,ids)=>sum+ids.length,0)} reviewable clips grouped into ${characterBuckets.size} characters. This view changes presentation only; saved voice, mood, split, and playback evidence is unchanged.`;
    updateSummary();
  };
  filteredIds=function(){
    if(!characterMode)return base.filteredIds();
    const meta=characterMeta.get(selected),ids=[...(characterBuckets.get(selected)||[])];
    return ids.filter(id=>filter.value==='all'||voiceReviewState(byId.get(id))===(filter.value==='verified'?'explicit':filter.value==='played'?'played':'needs'))
      .sort((a,b)=>Number((labels(byId.get(b)).voice||'voice_pending')==='voice_pending')-Number((labels(byId.get(a)).voice||'voice_pending')==='voice_pending')||
        Number(isMinority(byId.get(b)))-Number(isMinority(byId.get(a)))||
        ['needs','played','explicit'].indexOf(voiceReviewState(byId.get(a)))-['needs','played','explicit'].indexOf(voiceReviewState(byId.get(b)))||
        Number(typeof voicePrefillReview==='function'&&voicePrefillReview(b))-Number(typeof voicePrefillReview==='function'&&voicePrefillReview(a))||
        (meta?.sourceOrder.get(a)||0)-(meta?.sourceOrder.get(b)||0));
  };
  updateSummary=function(){
    if(!characterMode){base.updateSummary();return;}
    const ids=characterBuckets.get(selected)||[],meta=characterMeta.get(selected);
    $('#bucket-summary').textContent=meta?`${meta.label}: ${ids.length} clips · ${meta.explicit||0} explicit voice decisions · ${meta.played||0} played unchanged (passive only) · ${meta.needs||0} need voice review · ${meta.voices.size} voice ${meta.voices.size===1?'family':'families'}. ${voiceDistribution(meta)}.`:'No character clips in this view.';
    $('#acoustic-summary').textContent='Audio warnings still compare each clip with its saved voice + mood peers; they are not character-level verdicts.';
    $('#confirm-listened').disabled=true;updatePlayerControls();
  };
  card=function(row){
    const html=base.card(row);if(!characterMode||row.comparison_book)return html;
    const classes=`${isMinority(row)?' character-voice-minority':''}${explicitVoiceReviewed(row)?'':' character-voice-unchecked'}`;
    return html.replace('<article data-id=',`<article class="${classes.trim()}" data-id=`)
      .replace('<p class="text">',characterHint(row)+'<p class="text">');
  };
  updateCard=function(id,message){
    base.updateCard(id,message);if(!characterMode)return;
    refreshVisibleCharacterHints();
  };
  function refreshVisibleCharacterHints(){
    for(const el of document.querySelectorAll('#cards article')){
      const row=byId.get(el.dataset.id);if(!row||row.comparison_book)continue;
      el.classList.remove('moved');el.classList.toggle('character-voice-minority',isMinority(row));el.classList.toggle('character-voice-unchecked',!explicitVoiceReviewed(row));
      const hint=el.querySelector('.character-voice-context');if(hint)hint.outerHTML=characterHint(row);
    }
  }
  queueClipAllowed=function(id){
    if(!characterMode)return base.queueClipAllowed(id);
    const row=byId.get(id);if(!row||row.comparison_book||!eligible(row)||characterKey(row)!==queueBucket)return false;
    return filter.value==='all'||voiceReviewState(row)===(filter.value==='verified'?'explicit':filter.value==='played'?'played':'needs');
  };
  readURL=function(){
    const params=new URL(location.href).searchParams,wantsCharacter=params.get('view')==='character';
    if(characterMode!==wantsCharacter){characterMode=wantsCharacter;setModePresentation();}
    base.readURL();if(!characterMode)return;
    const requested=params.get('character');
    const target=params.get('focus')==='1'?byId.get(params.get('clip')):null;
    if(target&&eligible(target)){
      selected=characterKey(target);filter.value='all';pageIndex=0;linkedClip=target.id;
    }else if(characterBuckets.has(requested))selected=requested;
    filter.value=['pending','played','verified'].includes(params.get('show'))?params.get('show'):filter.value;
  };
  writeURL=function(push=false,clip=currentQueueClip()){
    base.writeURL(push,clip);const url=new URL(location.href);
    if(characterMode){url.searchParams.set('view','character');url.searchParams.set('character',selected);url.searchParams.delete('bucket');}
    else {url.searchParams.delete('view');url.searchParams.delete('character');}
    history.replaceState({},'',url);
  };

  modeSelect.onchange=()=>{
    stopSequence();modeSelections[characterMode?'character':'bucket']=selected;
    characterMode=modeSelect.value==='character';selected=modeSelections[characterMode?'character':'bucket']||'';pageIndex=0;
    setModePresentation();rebuildBuckets();restorePosition();render(true);
  };
  document.addEventListener('change',event=>{
    if(!characterMode||event.target.dataset.field!=='speaker')return;
    setTimeout(()=>{const current=event.target.closest('article')?.dataset.id;rebuildBuckets();render(false,current);},0);
  });
  setModePresentation();
})();
