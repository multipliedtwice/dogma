// Real media/UI, virtual saves: this must not write any user's review answers.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {isolatedBook2Board} from './isolated_book2_board.mjs';
const fixture=await isolatedBook2Board();
const url=fixture.base;
const human='manual-labeling/reality-benders-2/review.json';
const original=fs.readFileSync(human);let review={...JSON.parse(original),decisions:{}},writes=0;
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const page=await browser.newPage({viewport:{width:1250,height:950}});const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/review-draft.json',route=>route.fulfill({json:review}));
  await page.route('**/patch-review',async route=>{
    const data=route.request().postDataJSON();assert.equal(data.candidates_sha256,review.candidates_sha256);
    for(const [id,change] of Object.entries(data.changes))review.decisions[id]={...change.after,_revision:(review.decisions[id]?._revision||0)+1};
    writes++;await route.fulfill({json:{saved:true,review}});
  });
  await page.goto(url);await page.waitForFunction(()=>document.querySelector('#summary').textContent.includes('45 clips'));
  assert.match(await page.locator('h1').innerText(),/Book 2/);
  assert.match(await page.locator('#summary').innerText(),/tentative/);
  assert.equal(writes,0);assert.equal(Object.keys(review.decisions).length,0);
  assert.equal(await page.locator('#bucket').inputValue(),'narrator_baseline|neutral');
  const first=page.locator('article').first();const id=await first.getAttribute('data-id');
  const audio=first.locator('audio[data-clip]');
  await audio.evaluate(async a=>{a.load();await new Promise((resolve,reject)=>{if(a.readyState>=1)resolve();else{a.addEventListener('loadedmetadata',resolve,{once:true});a.addEventListener('error',reject,{once:true});}});await a.play();});
  await page.waitForFunction(id=>document.querySelector(`audio[data-clip="${id}"]`).currentTime>.1,id);await audio.evaluate(a=>a.pause());
  assert.equal(await audio.evaluate(a=>a.playbackRate),1);
  await first.locator('.clip-details > summary').click();
  assert.equal(await first.locator('[data-field=comment], [data-action=confirm], .muted .tag').count(),0);
  assert.match(await first.locator('.clip-context').innerText(),/Before[\s\S]*Current paragraph[\s\S]*After/);
  assert.equal(await page.locator('#confirm-listened').isVisible(),false);
  await first.locator('.clip-details details > summary').filter({hasText:'Audio mood hints'}).click();
  const reference=first.locator('details audio').first();await reference.evaluate(a=>a.load());
  await page.waitForFunction(()=>[...document.querySelectorAll('article details audio')].some(a=>a.readyState>=1&&a.duration>0));
  await audio.evaluate(async a=>{a.currentTime=0;a.playbackRate=16;await a.play();});
  await page.waitForFunction(id=>document.querySelector(`audio[data-clip="${id}"]`).ended,id,{timeout:10000});
  await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  assert.equal(review.decisions[id].playback_acceptance.playback_mode,'single_clip');
  assert.equal(review.decisions[id].playback_acceptance.playback_rate,16);
  assert.equal(review.decisions[id].voice_verdict,undefined);
  assert.equal(review.decisions[id].mood_verdict,undefined);
  assert.equal(review.decisions[id].bucket_review,undefined);
  await page.locator('#play-bucket').click();await page.waitForFunction(()=>document.querySelector('#sequence').currentTime>.1);
  await page.locator('#stop').click();assert.match(await page.locator('#play-bucket').innerText(),/Resume/);
  await first.locator('[data-field=mood]').selectOption('curious');await first.locator('[data-action=move]').click();
  await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  assert.equal(review.decisions[id].corrected_mood,'curious');assert.equal(review.decisions[id].bucket_review,undefined);
  assert.equal(review.decisions[id].playback_acceptance,undefined);
  await page.locator('#play-bucket').click();await page.waitForFunction(id=>document.querySelector('#sequence').dataset.clip!==id,id);
  await page.locator('#stop').click();
  await page.reload();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved labels loaded');
  assert.equal(review.decisions[id].corrected_mood,'curious');
  await page.locator('#bucket').selectOption('narrator_baseline|curious');
  const moved=page.locator(`article[data-id="${id}"]`);await moved.locator('.clip-details > summary').click();await moved.locator('[data-edge=boundary_end]').check();
  await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
  await page.reload();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved labels loaded');
  assert.equal(await page.locator(`article[data-id="${id}"] [data-edge=boundary_end]`).isChecked(),true);
  assert.equal(await page.locator(`article[data-id="${id}"] [data-action=confirm]`).count(),0);
  assert.equal(review.decisions[id].bucket_review,undefined);
  // Largest pending group spans two pages. Native ended events advance the queue.
  await page.locator('#bucket').selectOption('voice_pending|mood_pending');
  await page.locator('#play-bucket').click();
  for(let i=0;i<12;i++){
    await page.waitForFunction(()=>document.querySelector('#sequence').readyState>=1&&!document.querySelector('#sequence').paused);
    await page.locator('#sequence').evaluate(a=>{a.currentTime=Math.max(0,a.duration-.04);});
    const old=await page.locator('#sequence').getAttribute('data-clip');
    await page.waitForFunction(old=>document.querySelector('#sequence').dataset.clip!==old,old,{timeout:10000});
  }
  assert.match(await page.locator('#page-label').innerText(),/Page 2/);assert.match(page.url(),/page=2/);
  await page.locator('#stop').click();
  await page.screenshot({path:path.join(fixture.tempRoot,'ui-check.png'),fullPage:false});
  assert.deepEqual(errors,[]);assert.deepEqual(fs.readFileSync(human),original);
  console.log('PASS: tentative buckets, real audio/ref seeking, isolated saves, moves, resume, holds after refresh, page following, no real review writes.');
}finally{await browser.close();await fixture.close();}
