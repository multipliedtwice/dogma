import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {readFile} from 'node:fs/promises';

// Review writes are isolated; playback bookmarks use this temporary browser only.
const root='training-work/character-rb1/fine-tune-all/';
const summary=JSON.parse(await readFile(root+'summary.json','utf8'));
const rows=(await readFile(root+'candidates.jsonl','utf8')).trim().split('\n').map(JSON.parse);
const p=JSON.parse(await readFile(root+'precision-v2/status.json','utf8')).clips;
const q=JSON.parse(await readFile(root+'quality-screen.json','utf8')).clips;
const ids=rows.filter(row=>p[row.id]?.status==='precision_pass'&&row.role!=='mixed'&&
  p[row.id].audio_end-p[row.id].audio_start>4&&
  !q[row.id]?.reasons?.some(r=>['missing_or_changed_audio','unexpected_pcm_format','empty_audio','duration_mismatch','clipping','level_outlier'].includes(r))).slice(0,20).map(row=>row.id);
let draft={schema:1,candidates_sha256:summary.candidates_sha256,reviewer:'test-only',
  decisions:Object.fromEntries(ids.map(id=>[id,{voice_verdict:'no',corrected_voice_id:'narrator_baseline',mood_verdict:'no',corrected_mood:'neutral'}]))};
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const context=await browser.newContext({viewport:{width:1300,height:900}});
  await context.route('**/review-draft.json',route=>route.fulfill({json:draft}));
  await context.route('**/save-review',route=>route.abort());
  await context.route('**/patch-review',route=>{
    const {changes,candidates_sha256}=route.request().postDataJSON();assert.equal(candidates_sha256,summary.candidates_sha256);
    for(const [id,change] of Object.entries(changes)){
      const next={...draft.decisions[id]};
      for(const field of new Set([...Object.keys(change.before||{}),...Object.keys(change.after||{})])){
        if(JSON.stringify(change.before?.[field])===JSON.stringify(change.after?.[field]))continue;
        if(Object.hasOwn(change.after||{},field))next[field]=change.after[field];else delete next[field];
      }
      draft.decisions[id]={...next,_revision:(draft.decisions[id]?._revision||0)+1};
    }
    return route.fulfill({json:{saved:true,review:draft}});
  });
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  const url='http://127.0.0.1:1437/character-rb1/fine-tune-all/buckets.html';
  await page.goto(url);await page.locator('article').first().waitFor();
  await page.locator('#play-bucket').click();
  await page.waitForFunction(()=>!document.querySelector('#sequence').paused&&document.querySelector('#sequence').currentTime>.25);
  const first=await page.locator('#sequence').getAttribute('data-clip');
  await page.locator('#stop').click();
  const paused=await page.evaluate(()=>document.querySelector('#sequence').currentTime);
  assert.equal(await page.locator('#play-bucket').textContent(),'Resume all');
  await page.locator('#play-bucket').click();
  await page.waitForFunction(()=>!document.querySelector('#sequence').paused);
  assert.equal(await page.locator('#sequence').getAttribute('data-clip'),first);
  assert.ok(await page.evaluate(()=>document.querySelector('#sequence').currentTime)>=paused-.03,'Resume keeps the timestamp');
  await page.locator('#stop').click();

  // Move the paused current clip. Resume skips it rather than restarting the bucket.
  const next=await page.evaluate(()=>queue[queueIndex+1]);
  let card=page.locator(`article[data-id="${first}"]`);
  await card.locator('[data-field=mood]').selectOption('energetic');await card.locator('[data-action=move]').click();
  await page.locator('#save').click();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  await page.reload();await page.locator('article').first().waitFor();
  assert.equal(await page.evaluate(()=>queue[queueIndex]),first,'Refresh remembers the moved cursor before skipping it');
  await page.locator('#play-bucket').click();
  await page.waitForFunction(id=>document.querySelector('#sequence').dataset.clip===id&&!document.querySelector('#sequence').paused,next);
  await page.locator('#stop').click();
  const nextTime=await page.evaluate(()=>document.querySelector('#sequence').currentTime);

  // Refresh does not autoplay or discard the remembered queue and clip time.
  await page.reload();await page.locator('article').first().waitFor();
  assert.equal(await page.locator('#play-bucket').textContent(),'Resume all');
  assert.equal(await page.evaluate(()=>document.querySelector('#sequence').paused),true);
  assert.equal(await page.evaluate(()=>queue[queueIndex]),next);
  await page.locator('#play-bucket').click();
  await page.waitForFunction(()=>!document.querySelector('#sequence').paused);
  assert.equal(await page.locator('#sequence').getAttribute('data-clip'),next);
  assert.ok(await page.evaluate(()=>document.querySelector('#sequence').currentTime)>=nextTime-.03);
  await page.locator('#stop').click();

  // Every bucket has its own remembered position.
  await page.locator('#bucket').selectOption('narrator_baseline|energetic');
  await page.locator('#play-bucket').click();
  await page.waitForFunction(()=>!document.querySelector('#sequence').paused&&document.querySelector('#sequence').currentTime>.15);
  assert.equal(await page.locator('#sequence').getAttribute('data-clip'),first);
  await page.locator('#stop').click();
  await page.locator('#bucket').selectOption('narrator_baseline|neutral');
  assert.equal(await page.evaluate(()=>queue[queueIndex]),next);
  await page.locator('#play-bucket').click();await page.waitForFunction(()=>!document.querySelector('#sequence').paused);

  // Pause in the gap after a completed clip; resume begins the next clip.
  const afterGap=await page.evaluate(()=>queue[queueIndex+1]);
  await page.evaluate(async()=>{
    const a=document.querySelector('#sequence');
    const ended=new Promise(resolve=>a.addEventListener('ended',resolve,{once:true}));
    a.currentTime=a.duration-.05;await ended;stopSequence();
  });
  assert.equal(await page.evaluate(()=>gapPending),true);
  await page.locator('#play-bucket').click();
  await page.waitForFunction(id=>document.querySelector('#sequence').dataset.clip===id&&!document.querySelector('#sequence').paused,afterGap);
  await page.locator('#stop').click();

  // Pause all also handles an individually played clip and resumes from it.
  const individual=await page.evaluate(()=>queue[queueIndex+1]);
  card=page.locator(`article[data-id="${individual}"]`);
  await card.locator('audio').evaluate(a=>a.play());
  await page.waitForFunction(id=>cursorAudio?.dataset.clip===id&&cursorAudio.currentTime>.1,individual);
  await page.locator('#stop').click();
  const individualTime=await page.evaluate(()=>cursorTime);
  await page.locator('#play-bucket').click();
  await page.waitForFunction(id=>document.querySelector('#sequence').dataset.clip===id&&!document.querySelector('#sequence').paused,individual);
  assert.ok(await page.evaluate(()=>document.querySelector('#sequence').currentTime)>=individualTime-.03);
  await page.locator('#stop').click();

  // Controls stay at the top while the review list can scroll fully below them.
  await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight));
  const bar=await page.locator('.player-bar').boundingBox();
  assert.equal(bar.y,0);assert.ok(bar.height<80,'Desktop playback bar stays narrow');
  assert.equal(await page.locator('#play-bucket').isVisible(),true);
  await page.screenshot({path:'training-work/character-rb1/bucket-resume-check.png'});
  await page.setViewportSize({width:390,height:844});
  await page.waitForTimeout(100);
  const mobileBar=await page.locator('.player-bar').boundingBox();
  assert.equal(mobileBar.y,0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'No horizontal overflow');
  await page.screenshot({path:'training-work/character-rb1/bucket-resume-mobile-check.png'});
  assert.deepEqual(errors,[]);
  console.log('Pause/resume timestamps, moved-clip continuation, refresh, separate bucket bookmarks, end gaps, individual playback and fixed responsive controls passed.');
}finally{await browser.close();}
