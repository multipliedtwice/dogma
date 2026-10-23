// Browser smoke test for the complete Book 3 board and shared three-book selector.
// Every writable review file lives in a temporary copy; live human labels are
// compared byte-for-byte before and after the test.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import crypto from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {python as configuredPython} from '../scripts/python.mjs';

const root=path.resolve('.');
const protectedFiles=[
  'manual-labeling/reality-benders-1/review.json',
  'manual-labeling/reality-benders-2-full/review.json',
  'manual-labeling/reality-benders-3/review.json',
  'training-work/character-rb1/fine-tune-all/review-draft.json',
  'training-work/character-rb2-full/fine-tune-all/review-draft.json',
  'training-work/character-rb3-full/fine-tune-all/review-draft.json',
];
const originals=new Map(protectedFiles.map(file=>[file,fs.readFileSync(path.join(root,file))]));
let tempRoot=null,server=null,browser=null,splitFixtureChild='';

function linkTree(source,target){
  fs.mkdirSync(target,{recursive:true});
  for(const entry of fs.readdirSync(source,{withFileTypes:true})){
    const from=path.join(source,entry.name),to=path.join(target,entry.name);
    if(entry.isDirectory())linkTree(from,to);
    else {try{fs.linkSync(from,to);}catch{fs.copyFileSync(from,to);}}
  }
}

function copyBoard(relative){
  const source=path.join(root,relative),target=path.join(tempRoot,relative);
  linkTree(source,target);
  const liveDraft=path.join(source,'review-draft.json');
  const testDraft=path.join(target,'review-draft.json');
  fs.unlinkSync(testDraft);
  fs.copyFileSync(liveDraft,testDraft);
  assert.notEqual(fs.statSync(testDraft).ino,fs.statSync(liveDraft).ino);
}

async function startIsolatedServer(){
  tempRoot=fs.mkdtempSync(path.join(os.tmpdir(),'rimskify-book3-ui-'));
  fs.mkdirSync(path.join(tempRoot,'training'),{recursive:true});
  fs.copyFileSync(path.join(root,'training/voice_families.json'),path.join(tempRoot,'training/voice_families.json'));
  const basePath=path.join(tempRoot,'training/voice_families.json'),base=JSON.parse(fs.readFileSync(basePath,'utf8'));
  const created={id:'custom_fixture_guardian',label:'Fixture guardian',group:'Masculine voices',
    origin:'listener_created',synthesis_status:'requires_pack_voice',created_at:'2026-10-18T00:00:00Z'};
  const taxonomy={schema:1,narrator_id:'vlad-rimsky',registry_version:'vlad-rimsky-v1',
    base_taxonomy_sha256:crypto.createHash('sha256').update(fs.readFileSync(basePath)).digest('hex'),revision:1,
    updated_at:'2026-10-18T00:00:00Z',families:[...base.families,created],legacy_aliases:base.legacy_aliases||{},
    audit:[{revision:1,action:'create_family',family:created,previous_families_sha256:'fixture',created_at:'2026-10-18T00:00:00Z'}]};
  const registry=path.join(tempRoot,'manual-labeling/voice-family-registries/vlad-rimsky-v1');fs.mkdirSync(registry,{recursive:true});
  const familyBytes=Buffer.from(JSON.stringify(taxonomy,null,2)+'\n');fs.writeFileSync(path.join(registry,'families.json'),familyBytes);
  fs.writeFileSync(path.join(registry,'latest.json'),JSON.stringify({schema:1,families_sha256:crypto.createHash('sha256').update(familyBytes).digest('hex')}));
  copyBoard('training-work/character-rb1/fine-tune-all');
  copyBoard('training-work/character-rb2-full/fine-tune-all');
  copyBoard('training-work/character-rb3-full/fine-tune-all');
  // Seed one split only in the copied review. Its parent speaker correction
  // must not leak into children because mixed parts can have different voices.
  const fixtureBoard=path.join(tempRoot,'training-work/character-rb3-full/fine-tune-all');
  const splitPlan=JSON.parse(fs.readFileSync(path.join(fixtureBoard,'split-plan.json'),'utf8'));
  const splitReviewPath=path.join(fixtureBoard,'review-draft.json');
  const splitReview=JSON.parse(fs.readFileSync(splitReviewPath,'utf8'));
  const [splitFixtureParent,splitFixturePlan]=Object.entries(splitPlan.clips).find(([id,plan])=>
    id!=='rb3_0f79f16024e5859051'&&plan.cuts?.length&&!splitReview.decisions?.[id]?.split_review);
  const splitFixtureCut=splitFixturePlan.cuts.find(cut=>splitFixturePlan.suggested?.includes(cut.offset))||splitFixturePlan.cuts[0];
  splitFixtureChild=`${splitFixtureParent}~0-${splitFixtureCut.offset}`;
  splitReview.decisions[splitFixtureParent]={...(splitReview.decisions[splitFixtureParent]||{}),speaker_verdict:'no',
    corrected_character_id:'Parent fixture that must not leak',split_review:{schema:1,enabled:true,
      candidates_sha256:splitPlan.candidates_sha256,parent_audio_sha256:splitFixturePlan.audio_sha256,
      cuts:[splitFixtureCut.offset],parts:{},boundary_proposals:[{offset:splitFixtureCut.offset,frame:splitFixtureCut.frame,
        proposal_method:splitFixtureCut.proposal_method||'legacy_word_alignment',suggested:splitFixturePlan.suggested?.includes(splitFixtureCut.offset)||false}],
      training_approved:false,updated_at:'2026-10-19T00:00:00Z'}};
  fs.writeFileSync(splitReviewPath,JSON.stringify(splitReview,null,2)+'\n');
  for(const relative of ['training-work/character-rb1/fine-tune-expanded/clips',
    'training-work/full-book-pilot/clips','training-work/sentence-clips',
    'training-work/character-rb2/full/clips'])
    linkTree(path.join(root,relative),path.join(tempRoot,relative));
  linkTree(path.join(root,'training-work/character-rb3/labeling-full/clips'),
           path.join(tempRoot,'training-work/character-rb3/labeling-full/clips'));
  linkTree(path.join(root,'manual-labeling/reality-benders-1/audio'),
           path.join(tempRoot,'manual-labeling/reality-benders-1/audio'));
  const legacyPython=path.join(root,'training-work/.venv-tts/Scripts/python.exe');
  const python=process.env.RIMSKY_PYTHON||(fs.existsSync(legacyPython)?legacyPython:configuredPython());
  server=spawn(python,['-X','utf8','scripts/serve_label_pilot.py','--host','127.0.0.1','--port','0',
    '--board-folder','character-rb3-full','--audio-prefix','character-rb3/labeling-full','--root',tempRoot],
    {cwd:root,stdio:['ignore','pipe','pipe']});
  const errors=[];server.stderr.on('data',chunk=>errors.push(chunk.toString()));
  return await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Isolated server did not start: '+errors.join(''))),15000);
    server.once('exit',code=>{clearTimeout(timer);reject(new Error(`Isolated server exited ${code}: ${errors.join('')}`));});
    server.stdout.on('data',chunk=>{const match=chunk.toString().match(/http:\/\/[^\s]+/);if(match){clearTimeout(timer);resolve(match[0]);}});
  });
}

try{
  const base=await startIsolatedServer();
  browser=await chromium.launch({channel:'msedge',headless:true,args:['--autoplay-policy=no-user-gesture-required','--disable-gpu','--use-gl=swiftshader']});
  const page=await browser.newPage({viewport:{width:1250,height:900}});
  const errors=[],badResponses=[];let writes=0;
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.method()==='POST')writes++;});
  page.on('response',response=>{if(response.status()>=400)badResponses.push(`${response.status()} ${response.url()}`);});

  await page.goto(base);
  await page.waitForFunction(()=>document.querySelector('#summary')&&!document.querySelector('#summary').textContent.includes('Loading'));
  assert.match(await page.locator('h1').innerText(),/Book 3/);
  assert.equal(await page.locator('#book').inputValue(),'book3');
  assert.deepEqual(await page.locator('#book option').allTextContents(),['Book 1','Book 2','Book 3']);
  assert.match(await page.locator('#summary').innerText(),/^\d+ clips in \d+ tentative buckets/);
  const summary=await page.evaluate(async()=>await (await fetch('summary.json')).json());
  const modelStatus=await page.locator('#proposal-model-status').innerText();
  assert.match(modelStatus,/Suggestions: frozen Books 1\+2 model/);
  assert.match(modelStatus,new RegExp(summary.prediction_model_artifacts.voice_family.slice(0,12)));
  assert.match(modelStatus,new RegExp(summary.prediction_model_artifacts.mood.slice(0,12)));
  assert.equal(await page.locator('article').count(),12);
  assert.equal(await page.locator('article [data-field=voice] option[value=custom_fixture_guardian]').count(),12);
  assert.match(await page.locator('article [data-field=voice] option[value=custom_fixture_guardian]').first().innerText(),/label only/);
  assert.equal(await page.locator('#grouping-mode').inputValue(),'bucket');
  assert.equal(new URL(page.url()).searchParams.has('view'),false);
  await page.locator('#grouping-mode').selectOption('character');
  await page.waitForFunction(()=>document.body.classList.contains('character-review-mode')&&document.querySelector('#bucket')?.value.startsWith('character:'));
  assert.match(await page.locator('#summary').innerText(),/reviewable clips grouped into \d+ characters/);
  assert.match(await page.locator('#bucket-summary').innerText(),/explicit voice decisions.*played unchanged.*need voice review.*voice famil/i);
  assert.equal(await page.locator('#confirm-listened').isVisible(),false);
  assert.equal(await page.locator('#voice-family-editor').isVisible(),false);
  assert.ok(await page.locator('.character-voice-context').count()>0);
  assert.equal(new URL(page.url()).searchParams.get('view'),'character');
  assert.ok(new URL(page.url()).searchParams.get('character')?.startsWith('character:'));
  assert.equal(new URL(page.url()).searchParams.has('bucket'),false);
  const characterHeadings=await page.locator('article .clip-heading h2').allTextContents();
  assert.equal(new Set(characterHeadings).size,1,'Character view must contain one resolved speaker');
  const tempReviewBefore=await page.evaluate(async()=>await (await fetch('review-draft.json',{cache:'no-store'})).json());
  const visibleIds=await page.locator('article').evaluateAll(cards=>cards.map(card=>card.dataset.id));
  const voiceEditId=visibleIds.find(id=>!id.includes('~')&&!tempReviewBefore.decisions?.[id]?.playback_acceptance)||visibleIds.find(id=>!id.includes('~'));
  assert.ok(voiceEditId,'Expected a whole clip for the isolated voice edit');
  const untouchedId=Object.keys(tempReviewBefore.decisions||{}).find(id=>id!==voiceEditId);
  const beforeTarget=structuredClone(tempReviewBefore.decisions?.[voiceEditId]||{});
  const beforeUntouched=structuredClone(tempReviewBefore.decisions?.[untouchedId]||{});
  const characterBucketBefore=await page.locator('#bucket').inputValue();
  const voiceCard=page.locator(`article[data-id="${voiceEditId}"]`),moodBefore=await voiceCard.locator('[data-field="mood"]').inputValue();
  const voiceBefore=await voiceCard.locator('[data-field="voice"]').inputValue();
  const replacementVoice=await voiceCard.locator('[data-field="voice"] option').evaluateAll((options,current)=>
    options.find(option=>option.value&&option.value!==current&&option.value!=='voice_pending')?.value,voiceBefore);
  assert.ok(replacementVoice&&replacementVoice!==voiceBefore);
  await voiceCard.locator('[data-field="voice"]').selectOption(replacementVoice);
  await page.waitForFunction(()=>document.querySelector('#save-status')?.textContent==='Saved locally');
  assert.equal(await page.locator('#bucket').inputValue(),characterBucketBefore,'Voice edit must stay in the same character group');
  assert.equal(await page.locator(`article[data-id="${voiceEditId}"] [data-field="mood"]`).inputValue(),moodBefore);
  assert.match(await page.locator(`article[data-id="${voiceEditId}"] .character-voice-context`).innerText(),/Character distribution:/);
  const tempReviewAfter=await page.evaluate(async()=>await (await fetch('review-draft.json',{cache:'no-store'})).json());
  const afterTarget=tempReviewAfter.decisions[voiceEditId];
  assert.equal(afterTarget.corrected_voice_id,replacementVoice);
  for(const field of ['mood_verdict','corrected_mood','mood_scope','split_review'])assert.deepEqual(afterTarget[field],beforeTarget[field],`${field} must survive a voice edit`);
  assert.deepEqual(tempReviewAfter.decisions?.[untouchedId]||{},beforeUntouched,'Unrelated decision changed');
  await page.locator('#grouping-mode').selectOption('bucket');
  await page.waitForFunction(()=>!document.body.classList.contains('character-review-mode')&&!new URL(location.href).searchParams.has('view'));
  await page.goBack();
  await page.waitForFunction(()=>document.body.classList.contains('character-review-mode')&&document.querySelector('#grouping-mode')?.value==='character');
  await page.goForward();
  await page.waitForFunction(()=>!document.body.classList.contains('character-review-mode')&&document.querySelector('#grouping-mode')?.value==='bucket');
  await page.goto(base+`?view=character&focus=1&clip=${encodeURIComponent(splitFixtureChild)}`);
  await page.waitForFunction(id=>document.body.classList.contains('character-review-mode')&&!!document.querySelector(`article[data-id="${CSS.escape(id)}"]`),splitFixtureChild,{timeout:60000}).catch(async error=>{
    console.error(JSON.stringify({stage:'split-focus',errors,badResponses,state:await page.evaluate(()=>({summary:document.querySelector('#summary')?.textContent,status:document.querySelector('#save-status')?.textContent,bucket:document.querySelector('#bucket')?.value,classes:document.body.className,cards:[...document.querySelectorAll('article')].map(card=>card.dataset.id)}))}));throw error;
  });
  assert.equal(await page.locator('#bucket').inputValue(),'character:'+encodeURIComponent('__split_unresolved__'));
  assert.match(await page.locator('#bucket option:checked').innerText(),/Split parts/);
  assert.doesNotMatch(await page.locator(`article[data-id="${splitFixtureChild}"] .clip-heading h2`).innerText(),/Parent fixture/);
  assert.ok(await page.locator(`article[data-id="${splitFixtureChild}"] .character-voice-context`).count()>0);
  await page.locator('#grouping-mode').selectOption('bucket');
  await page.waitForFunction(()=>!document.body.classList.contains('character-review-mode'));
  assert.equal(await page.locator('#confirm-listened').isVisible(),false,'Book 3 keeps its existing passive proposal workflow');
  const audio=page.locator('article audio[data-clip]').first();
  await audio.evaluate(async element=>{
    if(globalThis.BucketHls){await BucketHls.load(element,audioURL(precision[element.dataset.clip].audio),{sha256:precision[element.dataset.clip].audio_sha256});await element.play();return;}
    element.load();
    await new Promise((resolve,reject)=>{
      if(element.readyState>=1)resolve();
      else {element.addEventListener('loadedmetadata',resolve,{once:true});element.addEventListener('error',reject,{once:true});}
    });
    await element.play();
  });
  await page.waitForFunction(()=>document.querySelector('article audio[data-clip]').currentTime>.1);
  await audio.evaluate(element=>element.pause());

  // Book 3 typesets dialogue transitions with U+2013 en dashes. Verify the
  // prepared plan reaches the existing split editor and exposes both anchored
  // boundaries without writing a listener decision.
  const enDashSplitClip='rb3_0f79f16024e5859051';
  await page.goto(base+`?focus=1&clip=${enDashSplitClip}`);
  await page.waitForFunction(id=>document.querySelector(`article[data-id="${id}"]`),enDashSplitClip);
  const enDashCard=page.locator(`article[data-id="${enDashSplitClip}"]`);
  assert.equal(await enDashCard.getByText('No reliable word boundaries for a split yet.').count(),0);
  const enDashEditor=enDashCard.locator('.split-editor');
  await enDashEditor.locator('summary').click();
  await page.waitForFunction(id=>document.querySelectorAll(`article[data-id="${id}"] .split-editor [data-part]`).length===3,enDashSplitClip);
  assert.equal(await enDashEditor.locator('[data-split-point] option').count(),2);
  assert.deepEqual(await enDashEditor.locator('[data-part] .text').allTextContents(),[
    'А ты, Кирилл, скорее иди к первому столику, – ',
    'Лидия указала мне на длинный большой стол перед самой сценой, – ',
    'там место специально для тебя зарезервировали, откуда выступление будет хорошо видно.',
  ]);

  for(const [value,title] of [['book2',/Book 2/],['book1',/Book 1/],['book3',/Book 3/]]){
    const navigation=page.waitForNavigation({waitUntil:'domcontentloaded'});
    await page.locator('#book').selectOption(value);
    await navigation;
    await page.waitForFunction(()=>document.querySelector('#summary')&&!document.querySelector('#summary').textContent.includes('Loading'));
    await page.waitForFunction(([expected,book])=>{
      const selector=document.querySelector('#book');
      return new RegExp(expected).test(document.querySelector('h1')?.textContent||'')
        && selector&&!selector.disabled&&selector.value===book;
    },[title.source,value]);
    assert.match(await page.locator('h1').innerText(),title);
    assert.equal(await page.locator('#book').inputValue(),value);
    assert.equal(await page.locator('article [data-field=voice] option[value=custom_fixture_guardian]').count(),12);
    assert.equal(await page.locator('#grouping-mode').inputValue(),'bucket');
    await page.locator('#grouping-mode').selectOption('character');
    await page.waitForFunction(()=>document.body.classList.contains('character-review-mode')&&document.querySelector('#bucket')?.value.startsWith('character:'));
    assert.match(await page.locator('#summary').innerText(),/grouped into \d+ characters/);
    assert.ok(await page.locator('.character-voice-context').count()>0);
    await page.locator('#grouping-mode').selectOption('bucket');
    await page.waitForFunction(()=>!document.body.classList.contains('character-review-mode'));
  }
  assert.equal(writes,1,'Only the isolated copied-review voice edit may write');
  assert.deepEqual(errors,[]);
  assert.deepEqual(badResponses,[]);
  console.log('Book 3 board, audio playback, and Book 1/2/3 selector passed in an isolated workspace.');
} finally {
  if(browser)await browser.close();
  if(server&&!server.killed)server.kill();
  for(const [file,contents] of originals)assert.deepEqual(fs.readFileSync(path.join(root,file)),contents,`${file} changed`);
  if(tempRoot)fs.rmSync(tempRoot,{recursive:true,force:true});
}
