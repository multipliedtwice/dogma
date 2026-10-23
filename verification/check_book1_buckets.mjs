import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {readFile} from 'node:fs/promises';

// All review reads and writes are intercepted. The real listener's answers stay untouched.
const root='training-work/character-rb1/fine-tune-all/';
const summary=JSON.parse(await readFile(root+'summary.json','utf8'));
const rows=(await readFile(root+'candidates.jsonl','utf8')).trim().split('\n').map(JSON.parse);
const precision=JSON.parse(await readFile(root+'precision-v2/status.json','utf8')).clips;
const quality=JSON.parse(await readFile(root+'quality-screen.json','utf8')).clips;
const ids=rows.filter(row=>precision[row.id]?.status==='precision_pass'&&row.role!=='mixed'&&
  !quality[row.id]?.reasons?.some(reason=>['missing_or_changed_audio','unexpected_pcm_format','empty_audio','duration_mismatch','clipping','level_outlier'].includes(reason))).slice(0,28).map(row=>row.id);
let canonical={schema:1,candidates_sha256:summary.candidates_sha256,reviewer:'test-only',updated_at:new Date().toISOString(),
  decisions:Object.fromEntries(ids.map(id=>[id,{voice_verdict:'no',corrected_voice_id:'narrator_baseline',mood_verdict:'no',corrected_mood:'neutral'}]))};
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const context=await browser.newContext({viewport:{width:1300,height:950}});
  await context.route('**/review-draft.json',route=>route.fulfill({json:canonical}));
  await context.route('**/save-review',route=>route.abort());
  await context.route('**/patch-review',async route=>{
    const request=route.request().postDataJSON();assert.equal(request.candidates_sha256,summary.candidates_sha256);
    const conflicts=[];
    for(const [id,change] of Object.entries(request.changes)){
      const current=canonical.decisions[id]||{},fields=[];
      for(const field of new Set([...Object.keys(change.before||{}),...Object.keys(change.after)])){
        if(['_revision','reviewed_at','review_origin'].includes(field)||JSON.stringify(change.before?.[field])===JSON.stringify(change.after[field]))continue;
        if(JSON.stringify(current[field])!==JSON.stringify(change.before?.[field])&&JSON.stringify(current[field])!==JSON.stringify(change.after[field]))fields.push(field);
      }
      if(fields.length)conflicts.push({id,fields,current});
    }
    if(conflicts.length){await route.fulfill({status:409,json:{error:'Conflict',conflicts}});return;}
    for(const [id,change] of Object.entries(request.changes)){
      const existing=canonical.decisions[id]||{},merged={...existing};
      for(const field of new Set([...Object.keys(change.before||{}),...Object.keys(change.after)])){
        if(field==='_revision'||JSON.stringify(change.before?.[field])===JSON.stringify(change.after[field]))continue;
        if(Object.hasOwn(change.after,field))merged[field]=change.after[field];else delete merged[field];
      }
      canonical.decisions[id]={...merged,_revision:(existing._revision||0)+1};
    }
    canonical.updated_at=new Date().toISOString();await route.fulfill({json:{saved:true,review:canonical}});
  });
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const url='http://127.0.0.1:1437/character-rb1/fine-tune-all/buckets.html';
  await page.goto(url);await page.locator('article').first().waitFor();
  assert.equal(await page.locator('article').count(),12);
  assert.match(await page.locator('#summary').textContent(),/28 labeled clips in 1 buckets/);
  let card=page.locator('article').first(),id=await card.getAttribute('data-id');
  assert.equal(await card.locator('[data-field=voice]').inputValue(),'narrator_baseline');
  await page.locator('#play-bucket').click();
  await page.waitForFunction(()=>!document.querySelector('#sequence').paused&&document.querySelector('#sequence').currentTime>.1);
  assert.equal(await page.locator('#sequence').getAttribute('data-clip'),id);
  await card.locator('[data-field=mood]').selectOption('excited');await card.locator('[data-action=move]').click();
  assert.match(await card.locator('.clip-status').textContent(),/Moved to/);
  assert.equal(await card.count(),1,'Moving does not remove the playing card');
  assert.equal(await page.locator('#sequence').getAttribute('data-clip'),id,'Moving does not restart playback');
  await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  assert.equal(canonical.decisions[id].corrected_mood,'excited');
  await page.locator('#undo').click();await page.locator('#save').click();
  await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  assert.equal(canonical.decisions[id].corrected_mood,'neutral');
  await page.locator('#stop').click();
  await card.locator('[data-action=confirm]').click();await page.locator('#save').click();
  await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  assert.equal(canonical.decisions[id].bucket_review.audio_sha256,precision[id].audio_sha256);
  await page.locator('#next').click();assert.match(page.url(),/page=2/);
  const nextId=await page.locator('article').first().getAttribute('data-id');
  await page.reload();assert.equal(await page.locator('article').first().getAttribute('data-id'),nextId);
  await page.goBack();assert.equal(await page.locator('article').first().getAttribute('data-id'),id);
  // A label saved by another tab becomes visible after refresh.
  canonical.decisions[id]={...canonical.decisions[id],mood_verdict:'no',corrected_mood:'calm',_revision:(canonical.decisions[id]._revision||0)+1};
  await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Latest saved labels loaded');
  await page.locator('#bucket').selectOption('narrator_baseline|calm');
  card=page.locator('article').first();assert.equal(await card.getAttribute('data-id'),id);
  assert.match(await card.locator('.clip-status').textContent(),/Listen and check/,'Relabeling invalidates the old verification');
  await card.locator('[data-field=voice]').selectOption('masculine_5');await card.locator('[data-action=move]').click();
  await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  await page.locator('#bucket').selectOption('masculine_5|calm');assert.equal(await page.locator('article').first().getAttribute('data-id'),id);
  await page.reload();await page.locator('article').first().waitFor();
  assert.equal(await page.locator('article [data-field=voice]').first().inputValue(),'masculine_5');
  // Reload keeps persisted undo and original sentence labels, without touching other rows.
  await page.locator('#undo').click();await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  assert.equal(canonical.decisions[id].corrected_voice_id,'narrator_baseline');
  assert.equal(Object.keys(canonical.decisions).length,28);
  await page.locator('#bucket').selectOption('narrator_baseline|neutral');
  // Pending-only playback keeps its filter, and advancing within a page keeps staged choices.
  await page.locator('#filter').selectOption('pending');await page.locator('#play-bucket').click();
  await page.waitForFunction(()=>sequenceActive&&!document.querySelector('#sequence').paused);
  assert.equal(await page.locator('#filter').inputValue(),'pending');
  const staged=page.locator('article').nth(2),stagedId=await staged.getAttribute('data-id');
  await staged.locator('[data-field=mood]').selectOption('energetic');
  await page.locator('#next-clip').click();await page.waitForFunction(()=>queueIndex===1&&!document.querySelector('#sequence').paused);
  assert.equal(await page.locator(`article[data-id="${stagedId}"] [data-field=mood]`).inputValue(),'energetic');
  // An aborted earlier play promise must not stop a newer clip.
  await page.evaluate(async()=>{
    const audio=document.querySelector('#sequence'),native=audio.play.bind(audio);let rejectOld,started;
    const pending=new Promise(resolve=>{started=resolve;});
    audio.play=()=>new Promise((resolve,reject)=>{rejectOld=reject;started();});
    const earlier=playQueued(2);await pending;audio.play=native;await playQueued(3);
    rejectOld(new DOMException('Interrupted by newer source','AbortError'));await earlier;
  });
  assert.equal(await page.evaluate(()=>sequenceActive),true);
  assert.equal(await page.evaluate(()=>queueIndex),3);
  await page.waitForFunction(()=>!document.querySelector('#sequence').paused);
  // Following the next page preserves the pending filter and the URL.
  await page.evaluate(()=>playQueued(12));await page.waitForFunction(()=>queueIndex===12&&!document.querySelector('#sequence').paused);
  assert.equal(await page.locator('#filter').inputValue(),'pending');assert.match(page.url(),/page=2/);
  // Seeking past most of a clip must not qualify it for bulk confirmation.
  const listenId=await page.evaluate(()=>queue.reduce((best,id)=>
    !best||precision[id].audio_end-precision[id].audio_start<precision[best].audio_end-precision[best].audio_start?id:best,null));
  await page.evaluate(async id=>{
    listened.delete(id);heardRanges.delete(id);await playQueued(queue.indexOf(id));
    document.querySelector('#sequence').pause();
  },listenId);
  await page.waitForFunction(()=>{const a=document.querySelector('#sequence');return a.seekable.length&&a.seekable.end(a.seekable.length-1)>=a.duration-.05;},null,{timeout:5000}).catch(async error=>{
    const details=await page.evaluate(()=>{const a=document.querySelector('#sequence');return {time:a.currentTime,duration:a.duration,seekable:Array.from({length:a.seekable.length},(_,i)=>[a.seekable.start(i),a.seekable.end(i)]),buffered:Array.from({length:a.buffered.length},(_,i)=>[a.buffered.start(i),a.buffered.end(i)]),ready:a.readyState};});
    throw Error(`${error.message}: ${JSON.stringify(details)}`);
  });
  const nextIndex=await page.evaluate(async()=>{
    const audio=document.querySelector('#sequence'),next=queueIndex+1;
    const seeked=new Promise(resolve=>audio.addEventListener('seeked',resolve,{once:true}));
    audio.currentTime=audio.duration-.05;await seeked;await audio.play();return next;
  });
  await page.waitForFunction(index=>queueIndex===index||!sequenceActive,nextIndex);
  const seekEvidence=await page.evaluate(id=>({listened:listened.has(id),heard:heardRanges.get(id),clip:id}),listenId);
  assert.equal(seekEvidence.listened,false,JSON.stringify(seekEvidence));
  await page.evaluate(id=>playQueued(queue.indexOf(id)),listenId);
  await page.waitForFunction(id=>listened.get(id)===fingerprint(byId.get(id)),listenId);
  await page.locator('#stop').click();
  const listenCard=page.locator(`article[data-id="${listenId}"]`);
  await listenCard.locator('[data-field=mood]').selectOption('energetic');
  await page.evaluate(()=>document.querySelector('#confirm-listened').onclick());
  assert.equal(canonical.decisions[listenId].bucket_review,undefined);
  await listenCard.locator('[data-field=mood]').selectOption('neutral');
  await page.locator('#confirm-listened').click();await page.locator('#save').click();
  await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  assert.equal(canonical.decisions[listenId].bucket_review.audio_sha256,precision[listenId].audio_sha256);
  if(await page.locator('#stop').isEnabled())await page.locator('#stop').click();
  await page.locator('#filter').selectOption('all');
  card=page.locator('article').first();const remoteId=await card.getAttribute('data-id');
  // A background save refreshes untouched dropdowns, but keeps deliberately staged choices.
  canonical.decisions[remoteId]={...canonical.decisions[remoteId],corrected_mood:'warning',bucket_comment:'new note from another tab',_revision:5};
  await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  assert.equal(await card.locator('[data-field=mood]').inputValue(),'warning');
  assert.equal(await card.locator('[data-field=comment]').inputValue(),'new note from another tab');
  await card.locator('[data-field=mood]').selectOption('energetic');
  canonical.decisions[remoteId]={...canonical.decisions[remoteId],corrected_mood:'calm',_revision:6};
  await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  assert.equal(await card.locator('[data-field=mood]').inputValue(),'energetic');
  // Show a real field conflict and resolve it without losing an unrelated comment.
  canonical.decisions[remoteId]={...canonical.decisions[remoteId],corrected_mood:'tired',_revision:7};
  await card.locator('[data-action=move]').click();await page.locator('#save').click();
  await page.getByRole('button',{name:'Use latest saved labels'}).waitFor();
  await page.getByRole('button',{name:'Use latest saved labels'}).click();
  await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  assert.equal(canonical.decisions[remoteId].corrected_mood,'tired');
  assert.equal(canonical.decisions[remoteId].bucket_comment,'new note from another tab');
  await page.screenshot({path:'training-work/character-rb1/bucket-review-check.png',fullPage:false});
  assert.deepEqual(errors,[]);
  console.log('Bucket playback, filter/page following, rapid Next, staged choices, moves, undo, verification, URL reload, cross-tab updates, and conflict resolution passed with isolated mock saves.');
}finally{await browser.close();}
