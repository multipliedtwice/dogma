// Browser smoke test for an isolated copy of the complete Book 2 bucket board.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {python as configuredPython} from '../scripts/python.mjs';

const root=path.resolve('.');
const liveHuman=path.join(root,'manual-labeling/reality-benders-2-full/review.json');
const liveOriginal=fs.readFileSync(liveHuman);
const liveReview=JSON.parse(liveOriginal);
let tempRoot=null,server=null;
function linkTree(source,target){
  fs.mkdirSync(target,{recursive:true});
  for(const entry of fs.readdirSync(source,{withFileTypes:true})){
    const from=path.join(source,entry.name),to=path.join(target,entry.name);
    if(entry.isDirectory())linkTree(from,to);
    else {try{fs.linkSync(from,to);}catch{fs.copyFileSync(from,to);}}
  }
}
async function isolatedBoard(){
  tempRoot=fs.mkdtempSync(path.join(os.tmpdir(),'rimskify-book2-ui-'));
  fs.mkdirSync(path.join(tempRoot,'training'),{recursive:true});
  fs.copyFileSync(path.join(root,'training/voice_families.json'),path.join(tempRoot,'training/voice_families.json'));
  linkTree(path.join(root,'training-work/character-rb2-full/fine-tune-all'),
           path.join(tempRoot,'training-work/character-rb2-full/fine-tune-all'));
  // Review data is tiny and valuable. Give the fixture its own inode so even a
  // future in-place writer cannot reach the live review through a hard link.
  const liveDraft=path.join(root,'training-work/character-rb2-full/fine-tune-all/review-draft.json');
  const testDraft=path.join(tempRoot,'training-work/character-rb2-full/fine-tune-all/review-draft.json');
  fs.unlinkSync(testDraft);fs.copyFileSync(liveDraft,testDraft);
  assert.notEqual(fs.statSync(testDraft).ino,fs.statSync(liveDraft).ino);
  // This regression checks the proposal editor. The listener has since saved
  // this split, so make only the isolated fixture's parent visible again.
  const fixtureReview=JSON.parse(fs.readFileSync(testDraft));
  if(fixtureReview.decisions?.rb2_289d8ca780ebd80322?.split_review){
    delete fixtureReview.decisions.rb2_289d8ca780ebd80322.split_review;
    fs.writeFileSync(testDraft,JSON.stringify(fixtureReview));
  }
  linkTree(path.join(root,'training-work/character-rb2/full/clips'),
           path.join(tempRoot,'training-work/character-rb2/full/clips'));
  linkTree(path.join(root,'manual-labeling/reality-benders-1/audio'),
           path.join(tempRoot,'manual-labeling/reality-benders-1/audio'));
  const legacyPython=path.join(root,'training-work/.venv-tts/Scripts/python.exe');
  const python=process.env.RIMSKY_PYTHON||(fs.existsSync(legacyPython)?legacyPython:configuredPython());
  server=spawn(python,['-X','utf8','scripts/serve_label_pilot.py','--host','127.0.0.1','--port','0',
    '--board-folder','character-rb2-full','--audio-prefix','character-rb2/full','--root',tempRoot],
    {cwd:root,stdio:['ignore','pipe','pipe']});
  const output=[];server.stderr.on('data',chunk=>output.push(chunk.toString()));
  const url=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Isolated review server did not start: '+output.join(''))),15000);
    server.once('exit',code=>{clearTimeout(timer);reject(new Error(`Isolated review server exited ${code}: ${output.join('')}`));});
    server.stdout.on('data',chunk=>{const match=chunk.toString().match(/http:\/\/[^\s]+/);if(match){clearTimeout(timer);resolve(match[0]);}});
  });
  return url;
}
const base=await isolatedBoard();
const human=path.join(tempRoot,'training-work/character-rb2-full/fine-tune-all/review-draft.json');
const original=fs.readFileSync(human);
const review=JSON.parse(original);
const baseClipCount=fs.readFileSync(
  'training-work/character-rb2-full/fine-tune-all/candidates.jsonl','utf8').trim().split('\n').length;
const splitExpansion=Object.values(review.decisions||{}).reduce((total,decision)=>{
  const split=decision.split_review;
  return total+(split?.enabled?Math.max(0,Object.keys(split.parts||{}).length-1):0);
},0);
const displayedClipCount=baseClipCount+splitExpansion;
const overlay=JSON.parse(fs.readFileSync('training-work/character-rb2-full/fine-tune-all/voice-proposal-overrides.json'));
const voiceCheck=Object.entries(overlay.clips).find(([id,value])=>value.voice_prefill_review&&
  !review.decisions?.[id]?.split_review?.enabled&&!review.decisions?.[id]?.voice_verdict&&
  !review.decisions?.[id]?.playback_acceptance)?.[0];
assert.ok(voiceCheck);
const splitClip='rb2_cf60c327f0cb48ce1a';
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const page=await browser.newPage({viewport:{width:1250,height:950}});
  const errors=[],badResponses=[];let writes=0;
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST')writes++;});
  page.on('response',response=>{if(response.status()>=400)badResponses.push(`${response.status()} ${response.url()}`);});
  await page.goto(base);
  await page.waitForFunction(()=>document.querySelector('#summary')&&!document.querySelector('#summary').textContent.includes('Loading'));
  assert.match(await page.locator('h1').innerText(),/Full-book labeling/);
  assert.match(await page.locator('#summary').innerText(),
    new RegExp(`${displayedClipCount} clips in \\d+ tentative buckets`));
  assert.match(await page.locator('#summary').innerText(),/\d+ strong voice disagreements are shown first/);
  const bucketSizes=await page.locator('#bucket option').evaluateAll(options=>
    options.map(option=>Number(option.textContent.match(/\((\d+)\)(?:\s*·\s*⚠\s*\d+)?$/)[1])));
  assert.ok(bucketSizes.every((size,index)=>!index||bucketSizes[index-1]>=size),
    'Bucket dropdown must list largest buckets first');
  const gap=page.locator('#clip-gap');
  assert.equal(await gap.getAttribute('max'),'10');
  assert.equal(await gap.getAttribute('step'),'0.5');
  await gap.evaluate(input=>{input.value='7.5';input.dispatchEvent(new Event('input',{bubbles:true}));});
  assert.equal(await page.locator('#clip-gap-value').innerText(),'7.5s');
  assert.equal(await page.evaluate(()=>betweenClipGapMs),7500);
  assert.equal(await page.locator('#bucket').inputValue(),'narrator_baseline|neutral');
  assert.equal(await page.locator('article').count(),12);
    const warningOrder=await page.evaluate(()=>{
      const ordered=baseFilteredIds(),priorities=ordered.map(pilotReviewPriority),warnings=ordered.map(hasReviewWarning);
      const savedBucket=selected;
      const everyBucket=[...buckets.keys()].map(key=>{
        selected=key;
        const ids=baseFilteredIds();
        const flags=ids.map(hasReviewWarning),firstPlain=flags.indexOf(false);
        return {key,ok:firstPlain<0||!flags.slice(firstPlain).includes(true)};
      });
      selected=savedBucket;
      const cursor=ordered[Math.min(5,ordered.length-1)],expected=ordered.filter(id=>id!==cursor);
      queue=[cursor,...[...expected].reverse()];queueBucket=selected;queueFilter=$('#filter').value;queueIndex=0;
      rebaseQueueAfterCursor();
      return {priorities,warnings,everyBucket,cursor,first:queue[0],tail:queue.slice(1),expected};
    });
    const firstPlain=warningOrder.warnings.indexOf(false);
    assert.ok(firstPlain<0||!warningOrder.warnings.slice(firstPlain).includes(true));
    assert.ok(warningOrder.everyBucket.every(result=>result.ok),JSON.stringify(warningOrder.everyBucket.filter(result=>!result.ok)));
  assert.ok(warningOrder.priorities.every((value,index,all)=>!index||all[index-1]>=value));
  assert.equal(warningOrder.first,warningOrder.cursor);
  assert.deepEqual(warningOrder.tail,warningOrder.expected);
  const first=page.locator('article').first();
  const mechanicalFeminine=first.locator('select[data-field="voice"] option[value="feminine_4_mechanical"]');
  assert.equal(await mechanicalFeminine.count(),1);
  assert.equal(await mechanicalFeminine.innerText(),'Feminine voice 4 (mechanical)');
  const loweredFeminine=first.locator('select[data-field="voice"] option[value="feminine_5"]');
  assert.equal(await loweredFeminine.count(),1);
  assert.equal(await loweredFeminine.innerText(),'Feminine voice 5 (lowered)');
  const loweredMasculine=first.locator('select[data-field="voice"] option[value="masculine_8"]');
  assert.equal(await loweredMasculine.count(),1);
  assert.equal(await loweredMasculine.innerText(),'Masculine voice 8 (lowered)');
  assert.doesNotMatch(await first.innerText(),/Pace estimate|Average volume/);
  const audio=first.locator('audio[data-clip]');
  await audio.evaluate(async a=>{if(globalThis.BucketHls){await BucketHls.load(a,audioURL(precision[a.dataset.clip].audio),{sha256:precision[a.dataset.clip].audio_sha256});await a.play();return;}a.load();await new Promise((resolve,reject)=>{if(a.readyState>=1)resolve();else{a.addEventListener('loadedmetadata',resolve,{once:true});a.addEventListener('error',reject,{once:true});}});await a.play();});
  await page.waitForFunction(()=>document.querySelector('audio[data-clip]').currentTime>.1);
  await audio.evaluate(a=>a.pause());
  assert.equal(await audio.evaluate(a=>a.playbackRate),1);
  await first.locator('.clip-details > summary').click();
  assert.match(await first.locator('.clip-context').innerText(),/Before[\s\S]*Current paragraph[\s\S]*After/);
  await page.goto(base+`?focus=1&clip=${voiceCheck}`);
  await page.waitForFunction(id=>document.querySelector(`article[data-id="${id}"]`),voiceCheck);
  assert.match(await page.locator(`article[data-id="${voiceCheck}"] .acoustic-header`).innerText(),/Check voice: audio suggests/);
  const passiveResult=await page.evaluate(id=>{
    const row=byId.get(id),label=labels(row),current=decision(id);
    globalThis.__voiceCheckBefore=Object.hasOwn(store.decisions,id)?structuredClone(store.decisions[id]):null;
    setDecision(id,{...current,playback_acceptance:{schema:1,voice_family_id:label.voice,
      mood:label.mood,audio_sha256:precision[id].audio_sha256,coverage:.8}});
    return {warning:voicePrefillReview(id),voiceVerdict:decision(id).voice_verdict||null};
  },voiceCheck);
  assert.deepEqual(passiveResult,{warning:false,voiceVerdict:null});
  await page.evaluate(id=>{
    if(globalThis.__voiceCheckBefore===null)delete store.decisions[id];
    else setDecision(id,globalThis.__voiceCheckBefore);
    store.stash();delete globalThis.__voiceCheckBefore;
  },voiceCheck);
  const alignmentClip='rb2_33b7439a82c17c55eb';
  await page.goto(base+`?focus=1&clip=${alignmentClip}`);
  await page.waitForFunction(id=>document.querySelector(`article[data-id="${id}"]`),alignmentClip);
  const visibleHints=await page.locator(`article[data-id="${alignmentClip}"] .proposal-card-hints`).innerText();
  assert.match(visibleHints,/Audio warning: check the beginning and ending/);
  assert.match(visibleHints,/Suggested label: Narrator baseline · /);
  assert.match(visibleHints,/Other mood ideas: /);
  await page.locator('#mixed-mode').check();
  const mixedQueue=await page.evaluate(id=>{
    const at=queue.indexOf(id),next=queue.slice(at,at+5);
    return next.map(clip=>({id:clip,book:byId.get(clip)?.comparison_book||2,audio:precision[clip]?.audio}));
  },alignmentClip);
  const mixedBooks=mixedQueue.map(item=>item.book);
  assert.equal(mixedBooks[0],2);
  assert.ok(mixedBooks.length>=3);
  for(let index=1;index<mixedBooks.length;index++)assert.equal(mixedBooks[index],index%2?1:2);
  assert.match(mixedQueue[1].audio,/manual-labeling\/reality-benders-1\/audio\/[a-f0-9]{64}\.wav/);
  const referenceStatus=await page.evaluate(async path=>(await fetch('/'+path)).status,mixedQueue[1].audio);
  assert.equal(referenceStatus,200);
  await page.locator('#mixed-mode').uncheck();
  const correctedSpeakerClip='rb2_37cbf4c868609c12aa';
  await page.goto(base+`?focus=1&clip=${correctedSpeakerClip}`);
  await page.waitForFunction(id=>document.querySelector(`article[data-id="${id}"]`),correctedSpeakerClip);
  const correctedCard=page.locator(`article[data-id="${correctedSpeakerClip}"]`);
  assert.equal(await correctedCard.locator('.clip-heading h2').innerText(),'Иришка');
  await correctedCard.locator('.clip-details > summary').click();
  const speakerSelect=correctedCard.locator('select[data-field="speaker"]');
  assert.equal(await speakerSelect.inputValue(),'Иришка');
  assert.match(await speakerSelect.locator('option[value=""]').innerText(),/model attribution: Радугин/i);
  const placeholderPart='rb2_c801dbf042f3505832~56-68';
  await page.goto(base+`?focus=1&clip=${placeholderPart}`);
  await page.waitForFunction(id=>document.querySelector(`article[data-id="${id}"]`),placeholderPart);
  const placeholderCard=page.locator(`article[data-id="${placeholderPart}"]`);
  assert.match(await placeholderCard.locator(':scope > p.text').innerText(),/^\(нецензурно\)$/);
  assert.equal(await placeholderCard.locator('[data-field="voice"]').inputValue(),'narrator_baseline');
  const unclearClip='rb2_289d8ca780ebd80322';
  await page.goto(base+`?focus=1&clip=${unclearClip}`);
  await page.waitForFunction(id=>document.querySelector(`article[data-id="${id}"]`),unclearClip);
  const unclearSplit=page.locator(`article[data-id="${unclearClip}"] .split-editor`);
  await unclearSplit.locator('summary').click();
  await page.waitForFunction(()=>document.querySelector('.split-editor[open] [data-part]'));
  assert.equal(await unclearSplit.locator('[data-part]').count(),2);
  assert.equal((await unclearSplit.locator('[data-part]').nth(0).locator('p.text').innerText()).trim(),
    '— Возвращаем задержанных…');
  assert.equal((await unclearSplit.locator('[data-part]').nth(1).locator('p.text').innerText()).trim(),
    '(непонятно).');
  assert.equal(await unclearSplit.locator('[data-part]').nth(1)
    .locator('[data-split-label="voice"]').inputValue(),'narrator_baseline');
  await page.goto(base+`?focus=1&clip=${splitClip}`);
  await page.waitForFunction(id=>document.querySelector(`article[data-id="${id}"]`),splitClip);
  const split=page.locator(`article[data-id="${splitClip}"] .split-editor`);
  await split.locator('summary').click();
  await page.waitForFunction(()=>document.querySelector('.split-editor[open] [data-part]'));
  assert.equal(await split.locator('[data-part]').count(),2);
  const bucketBeforeSplit=await page.locator('#bucket').inputValue();
  await split.locator('[data-split-action="save"]').click();
  await page.waitForFunction(()=>document.querySelector('#save-status')?.textContent.includes('stayed in the current bucket'));
  assert.equal(await page.locator('#bucket').inputValue(),bucketBeforeSplit);
  await page.waitForTimeout(1000);
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>parseFloat(getComputedStyle(document.querySelector('main')).paddingBottom)>=document.querySelector('.player-bar').getBoundingClientRect().height);
  const mobilePlayer=await page.evaluate(()=>{
    const bar=document.querySelector('.player-bar'),main=document.querySelector('main'),rect=bar.getBoundingClientRect();
    return {position:getComputedStyle(bar).position,top:rect.top,bottom:rect.bottom,height:rect.height,
      viewport:innerHeight,paddingBottom:parseFloat(getComputedStyle(main).paddingBottom)};
  });
  assert.equal(mobilePlayer.position,'fixed');
  assert.ok(Math.abs(mobilePlayer.bottom-mobilePlayer.viewport)<2);
  assert.ok(mobilePlayer.top>mobilePlayer.viewport/2);
  assert.ok(mobilePlayer.paddingBottom>=mobilePlayer.height);
  const mobileButtons=await page.locator('#play-bucket, #stop, #next-clip').evaluateAll(buttons=>
    buttons.map(button=>({height:button.getBoundingClientRect().height,fontSize:getComputedStyle(button).fontSize})));
  assert.ok(mobileButtons.every(button=>button.height>=44&&button.fontSize==='15px'));
  assert.equal(writes,1);
  // Restore the original mobile Play all / Next following and non-playing move.
  await page.goto(base+'?bucket=narrator_baseline%7Cneutral');
  await page.waitForFunction(()=>loaded&&selected==='narrator_baseline|neutral');
  await page.locator('#follow').check();
  await page.evaluate(()=>{
    stopSequence();queue=[];queueIndex=-1;queueComplete=false;gapPending=false;
    cursorAudio=null;cursorTime=0;window.scrollTo(0,0);
  });
  await page.locator('#play-bucket').click();
  await page.waitForFunction(()=>!document.querySelector('#sequence').paused&&sequenceActive);
  const firstMobileClip=await page.locator('#sequence').getAttribute('data-clip');
  const visiblePlayingCard=()=>{
    const id=document.querySelector('#sequence').dataset.clip;
    const card=[...document.querySelectorAll('article')].find(el=>el.dataset.id===id);
    const rect=card?.getBoundingClientRect(),player=document.querySelector('.player-bar').getBoundingClientRect();
    return scrollY>0&&rect&&rect.top<player.top&&rect.bottom>0;
  };
  await page.waitForFunction(visiblePlayingCard);
  await page.locator('#next-clip').click();
  await page.waitForFunction(id=>document.querySelector('#sequence').dataset.clip!==id&&!document.querySelector('#sequence').paused,firstMobileClip);
  await page.waitForFunction(visiblePlayingCard);
  await page.locator('#stop').click();
  const unchangedId=await page.locator('#sequence').getAttribute('data-clip');
  assert.equal(await page.locator('article [data-action="next"]').count(),0);
  const dropdownMood='disgusted';
  assert.equal(await page.locator(`article[data-id="${unchangedId}"] [data-field="mood"] option[value="disgusted"]`).innerText(),'Disgusted');
  const dropdownVoice=await page.evaluate(id=>{
    const d=decision(id);return {verdict:d.voice_verdict||null,confirmed:d.confirmed_voice_id||null,corrected:d.corrected_voice_id||null};
  },unchangedId);
  await page.locator(`article[data-id="${unchangedId}"] [data-field="mood"]`).selectOption(dropdownMood);
  await page.waitForFunction(id=>document.querySelector('#sequence').dataset.clip!==id&&!document.querySelector('#sequence').paused,unchangedId);
  await page.waitForFunction(visiblePlayingCard);
  assert.equal(await page.evaluate(id=>decision(id).corrected_mood,unchangedId),dropdownMood);
  assert.deepEqual(await page.evaluate(id=>{
    const d=decision(id);return {verdict:d.voice_verdict||null,confirmed:d.confirmed_voice_id||null,corrected:d.corrected_voice_id||null};
  },unchangedId),dropdownVoice,'A dropdown mood choice must preserve the voice verdict');
  const singleId=await page.locator('#sequence').getAttribute('data-clip');
  await page.evaluate(()=>setClipGap(0,false));
  await page.locator('#sequence').evaluate(audio=>{audio.currentTime=Math.max(0,audio.duration-.08);});
  await page.waitForFunction(()=>document.querySelector('#sequence').ended);
  await page.waitForTimeout(250);
  assert.equal(await page.evaluate(()=>currentQueueClip()),singleId);
  assert.equal(await page.evaluate(()=>sequenceActive),false,
    'A mood dropdown choice must play only one successor');
  await page.locator('#play-bucket').click();
  await page.waitForFunction(id=>document.querySelector('#sequence').dataset.clip!==id&&!document.querySelector('#sequence').paused,singleId);
  await page.waitForFunction(visiblePlayingCard);
  await page.locator('#stop').click();
  const movedId=await page.locator('#sequence').getAttribute('data-clip');
  const moveCard=page.locator(`article[data-id="${movedId}"]`);
  const moveMood=await page.evaluate(id=>labels(byId.get(id)).mood==='calm'?'neutral':'calm',movedId);
  const voiceBeforeMood=await page.evaluate(id=>{
    const d=decision(id);return {verdict:d.voice_verdict||null,confirmed:d.confirmed_voice_id||null,corrected:d.corrected_voice_id||null};
  },movedId);
  await moveCard.locator('[data-field="mood"]').scrollIntoViewIfNeeded();
  await moveCard.locator('[data-field="mood"]').selectOption(moveMood);
  await page.waitForFunction(id=>document.querySelector('#sequence').dataset.clip!==id&&!document.querySelector('#sequence').paused,movedId);
  await page.waitForFunction(visiblePlayingCard);
  assert.equal(await page.evaluate(id=>decision(id).corrected_mood,movedId),moveMood,
    'Selecting a mood must save it immediately');
  assert.deepEqual(await page.evaluate(id=>{
    const d=decision(id);return {verdict:d.voice_verdict||null,confirmed:d.confirmed_voice_id||null,corrected:d.corrected_voice_id||null};
  },movedId),voiceBeforeMood,'A mood choice must not approve the untouched voice proposal');
  await page.locator('#stop').click();
  const beforeVoiceClip=await page.evaluate(()=>currentQueueClip());
  await moveCard.locator('[data-field="voice"]').scrollIntoViewIfNeeded();
  const scrollBeforeVoice=await page.evaluate(()=>scrollY);
  const moodBeforeVoice=await page.evaluate(id=>JSON.stringify({verdict:decision(id).mood_verdict,mood:decision(id).corrected_mood}),movedId);
  const newVoice=await page.evaluate(id=>families.find(f=>f.id!==labels(byId.get(id)).voice&&f.id!=='voice_pending').id,movedId);
  await moveCard.locator('[data-field="voice"]').selectOption(newVoice);
  assert.equal(await page.evaluate(id=>decision(id).corrected_voice_id,movedId),newVoice);
  assert.equal(await page.evaluate(id=>JSON.stringify({verdict:decision(id).mood_verdict,mood:decision(id).corrected_mood}),movedId),moodBeforeVoice);
  assert.equal(await page.locator('#sequence').evaluate(audio=>audio.paused),true);
  assert.equal(await page.evaluate(()=>currentQueueClip()),beforeVoiceClip);
  assert.ok(Math.abs(await page.evaluate(()=>scrollY)-scrollBeforeVoice)<5,
    'A voice-only change must preserve mobile scroll and remain paused');
  const moodChip=moveCard.locator('[data-action="mood"]').first();
  assert.ok(await moodChip.count());
  const suggestedMood=await moodChip.getAttribute('data-mood');
  const chipVoiceBefore=await page.evaluate(id=>decision(id).corrected_voice_id,movedId);
  await moodChip.click();
  await page.waitForFunction(id=>document.querySelector('#sequence').dataset.clip!==id&&!document.querySelector('#sequence').paused,movedId);
  assert.equal(await page.evaluate(id=>decision(id).corrected_mood,movedId),suggestedMood);
  assert.equal(await page.evaluate(id=>decision(id).corrected_voice_id,movedId),chipVoiceBefore);
  await page.waitForFunction(visiblePlayingCard);
  const afterChip=await page.locator('#sequence').getAttribute('data-clip');
  await page.locator('#sequence').evaluate(audio=>{audio.currentTime=Math.max(0,audio.duration-.08);});
  await page.waitForFunction(()=>document.querySelector('#sequence').ended);
  await page.waitForTimeout(250);
  assert.equal(await page.evaluate(()=>currentQueueClip()),afterChip,'A mood chip must play only one successor');
  assert.equal(await page.evaluate(()=>sequenceActive),false);
  await page.evaluate(async()=>{stopSequence();await saveNow();});
  assert.equal(JSON.parse(fs.readFileSync(human)).decisions[unchangedId].corrected_mood,'disgusted',
    'The new Disgusted mood must persist through the labeling server');
  const popularity=await page.evaluate(()=>{
    const savedRows=rows,fixtureRows=[],fixtureIds=[];
    const add=(prefix,voice,mood,count,kind='human')=>{
      for(let i=0;i<count;i++){
        const id=`popularity-${prefix}-${i}`;fixtureIds.push(id);
        fixtureRows.push({id,base_voice_id_proposed:voice,mood_proposed:[mood]});
        store.decisions[id]=kind==='human'?{voice_verdict:'no',corrected_voice_id:voice,mood_verdict:'no',corrected_mood:mood}:
          kind==='rejected'?{voice_verdict:'no',corrected_voice_id:voice,mood_verdict:'no',corrected_mood:mood,rejected:true}:
          {playback_acceptance:{voice_family_id:voice,mood,coverage:1}};
      }
    };
    const target={id:'popularity-target',base_voice_id_proposed:'narrator_baseline',mood_proposed:['neutral'],
      hybrid:{top3:[{label:'neutral'},{label:'calm'}]}};
    fixtureIds.push(target.id);
    store.decisions[target.id]={voice_verdict:'no',corrected_voice_id:'narrator_baseline'};
    const popular=()=>{
      const holder=document.createElement('div');holder.innerHTML=moodChips(target);
      return [...holder.querySelectorAll('[data-source="popular"]')].map(button=>button.dataset.mood);
    };
    try{
      add('neutral','narrator_baseline','neutral',6);add('calm','narrator_baseline','calm',5);
      add('energetic','narrator_baseline','energetic',4);add('curious','narrator_baseline','curious',3);
      add('passive','narrator_baseline','angry',30,'passive');add('rejected','narrator_baseline','angry',20,'rejected');
      add('other','masculine_2','angry',10);
      rows=fixtureRows;rebuildPopularMoods();const initial=popular();
      store.decisions['popularity-energetic-0'].corrected_mood='curious';
      rebuildPopularMoods();const updated=popular();
      store.decisions[target.id].corrected_voice_id='masculine_2';const otherVoice=popular();
      return {initial,updated,otherVoice};
    }finally{
      rows=savedRows;for(const id of fixtureIds)delete store.decisions[id];rebuildPopularMoods();
    }
  });
  assert.deepEqual(popularity,{initial:['energetic','curious'],updated:['curious','energetic'],otherVoice:['angry']},
    'Popularity must use saved mood choices per voice, exclude shown suggestions, and ignore passive or rejected records');
  await page.screenshot({path:path.join(os.tmpdir(),'rimskify-mobile-buttons.png')});
  assert.deepEqual(errors,[]);
  assert.deepEqual(badResponses,[]);
  const liveAfter=JSON.parse(fs.readFileSync(liveHuman));
  assert.deepEqual(liveAfter.decisions?.[splitClip],liveReview.decisions?.[splitClip]);
  assert.deepEqual(fs.readFileSync(liveHuman),liveOriginal,'Live human review must remain byte-for-byte unchanged');
  console.log('PASS: full Book 2 buckets, real audio playback, context, split UI, and read-only loading.');
}finally{
  await browser.close();
  if(server&&!server.killed)server.kill();
  if(tempRoot)fs.rmSync(tempRoot,{recursive:true,force:true});
}
