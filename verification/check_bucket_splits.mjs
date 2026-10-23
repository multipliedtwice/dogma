// Real audio and UI; intercepted saves never touch the user's labeling.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import {isolatedBook2Board} from './isolated_book2_board.mjs';
const hashContext={};
vm.runInNewContext(fs.readFileSync('scripts/book_bucket_splits.js','utf8')+
  ";result=bucketSha256Fallback(new Uint8Array([97,98,99]));",hashContext);
assert.equal(hashContext.result,
  'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
const fixture=await isolatedBook2Board();
const base=fixture.base;
const original=fs.readFileSync('manual-labeling/reality-benders-2/review.json');
let review=JSON.parse(original);
// The user may already have split this clip; exercise a fresh split virtually.
if(review.decisions.rb2_a2422f6005c497a4d2)delete review.decisions.rb2_a2422f6005c497a4d2.split_review;
const rows=fs.readFileSync('training-work/character-rb2/fine-tune-all/candidates.jsonl','utf8').trim().split('\n').map(JSON.parse);
const parent=rows.find(r=>r.id==='rb2_a2422f6005c497a4d2'),before=structuredClone(review.decisions[parent.id]||{});
const valid=new Set(rows.map(r=>r.id));let writes=0;
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
 const page=await browser.newPage({viewport:{width:1250,height:950}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/review-draft.json',r=>r.fulfill({json:review}));
 await page.route('**/patch-review',async route=>{
   const data=route.request().postDataJSON();
   for(const [id,c] of Object.entries(data.changes)){assert(valid.has(id),'Child answers must save under original parent');review.decisions[id]={...c.after,_revision:(review.decisions[id]?._revision||0)+1};}
   writes++;await route.fulfill({json:{saved:true,review}});
 });
 await page.goto(base+'?focus=1&clip='+parent.id);await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved labels loaded');
 assert.equal(writes,0);assert.deepEqual(review.decisions[parent.id]||{},before);
 const card=page.locator(`article[data-id="${parent.id}"]`),editor=card.locator('.split-editor');
 await editor.locator('summary').click();await page.waitForFunction(()=>document.querySelector('.split-editor[open] [data-part]'));
 assert.equal(await editor.locator('[data-part]').count(),2);
 const parts=editor.locator('[data-part]');
 assert.match(await parts.nth(0).locator('.text').innerText(),/Желтов/);
 assert.match(await parts.nth(1).locator('.text').innerText(),/закричал/);
 await parts.nth(0).locator('[data-split-label=voice]').selectOption('masculine_2');
 await parts.nth(0).locator('[data-split-label=mood]').selectOption('shouting');
 await parts.nth(1).locator('[data-split-label=voice]').selectOption('narrator_baseline');
 await parts.nth(1).locator('[data-split-label=mood]').selectOption('neutral');
 await parts.nth(0).locator('[data-split-action=preview]').click();
 const audio=parts.nth(0).locator('audio');await page.waitForFunction(()=>document.querySelector('.split-editor[open] audio.split-preview').currentTime>.1);
 assert.equal(await audio.evaluate(a=>a.playbackRate),1);assert((await audio.evaluate(a=>a.duration))<parent.audio_end-parent.audio_start);await audio.evaluate(a=>a.pause());
 await editor.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(fixture.tempRoot,'split-ui-check.png'),fullPage:false});
 await editor.locator('[data-split-action=save]').click();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
 const saved=review.decisions[parent.id];assert.equal(saved.split_review.enabled,true);assert.equal(saved.split_review.training_approved,false);
 for(const [key,value] of Object.entries(before))if(key!=='_revision')assert.deepEqual(saved[key],value,'Original human choice preserved');
 const ids=Object.keys(saved.split_review.parts),first=ids.find(id=>saved.split_review.parts[id].corrected_mood==='shouting');
 const second=ids.find(id=>saved.split_review.parts[id].corrected_voice_id==='narrator_baseline');
 assert.equal(ids.length,2);assert.equal(await page.locator('#bucket').inputValue(),'masculine_2|shouting');
 assert.equal(await page.locator(`article[data-id="${parent.id}"]`).count(),0);
 const child=page.locator(`article[data-id="${first}"]`);
 assert.equal(await child.locator('[data-field=mood]').innerText(),'Split mix');
 assert.equal(await child.locator('.split-mix-badge,.split-mix-summary').count(),0);
 await child.locator('[data-field=mood]').click();
 assert.equal(await child.locator('.split-editor').evaluate(e=>e.open),true);
 assert.equal(await child.locator('[data-part]').nth(0).locator('[data-split-label=mood]').inputValue(),'shouting');
 assert.equal(await child.locator('[data-part]').nth(1).locator('[data-split-label=mood]').inputValue(),'neutral');
 await child.locator('.split-editor summary').click();
 await page.locator('#bucket').selectOption('narrator_baseline|neutral');
 const sibling=page.locator(`article[data-id="${second}"]`);
 assert.equal(await sibling.locator('[data-field=mood]').innerText(),'Split mix');
 assert.equal(await page.locator('#bucket').inputValue(),'narrator_baseline|neutral');
 await page.locator('#bucket').selectOption('masculine_2|shouting');
 await child.locator('[data-field=voice]').selectOption('masculine_1');await child.locator('[data-action=move]').click();
 await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
 assert.equal(review.decisions[parent.id].split_review.parts[first].corrected_voice_id,'masculine_1');
 assert.equal(review.decisions[parent.id].split_review.parts[first].corrected_mood,'shouting');
 assert.equal(await child.locator('[data-field=mood]').innerText(),'Split mix');
 await page.locator('#undo').click();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
 assert.equal(review.decisions[parent.id].split_review.parts[first].corrected_mood,'shouting');
 await child.locator('.clip-details > summary').click();await child.locator('.split-audio-edges summary').click();
 assert.match(await child.locator('.clip-context').innerText(),/Before[\s\S]*Current paragraph[\s\S]*After/);
 await child.locator('[data-edge=boundary_end]').check();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
 assert.equal(review.decisions[parent.id].split_review.parts[first].boundary_end,true);
 assert.equal(await child.locator('[data-action=confirm], [data-field=comment]').count(),0);
 assert.equal(review.decisions[parent.id].split_review.parts[first].bucket_review,undefined);
 await child.locator('[data-edge=boundary_end]').uncheck();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
 // Manual split point and per-part draft choices survive refresh before saving.
 await child.locator('.split-editor summary').click();
 const reopened=child.locator('.split-editor');
 await reopened.locator('[data-split-point]').selectOption({index:0});await reopened.locator('[data-split-action=add]').click();
 assert.equal(await reopened.locator('[data-part]').count(),3);
 await reopened.locator('[data-part]').first().locator('[data-split-label=mood]').selectOption('surprised');
 await page.reload();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved labels loaded');
 const refreshed=page.locator(`article[data-id="${first}"] .split-editor`);await refreshed.locator('summary').click();
 await page.waitForFunction(id=>document.querySelector(`article[data-id="${id}"] .split-editor [data-part]`),first);
 assert.equal(await refreshed.locator('[data-part]').count(),3);assert.equal(await refreshed.locator('[data-part]').first().locator('[data-split-label=mood]').inputValue(),'surprised');
 // Discard the unsaved extra cut explicitly; playback remains the saved two parts.
 await refreshed.locator('[data-split-action=suggested]').click();await refreshed.locator('summary').click();
 await page.locator('#play-bucket').click();await page.waitForFunction(()=>document.querySelector('#sequence').currentTime>.1);
 assert.equal(await page.locator('#sequence').getAttribute('data-clip'),first);await page.locator('#stop').click();
 await page.locator('#bucket').selectOption('narrator_baseline|neutral');
 assert.equal(await page.locator(`article[data-id="${second}"]`).count(),1);
 const returned=page.locator(`article[data-id="${second}"] .split-editor`);await returned.locator('summary').click();
 await returned.locator('[data-split-action=disable]').click();await page.waitForFunction(()=>document.querySelector('#save-status').textContent==='Saved locally');
 assert.equal(review.decisions[parent.id].split_review.enabled,false);
 assert.equal(await page.locator(`article[data-id="${parent.id}"]`).count(),1);
 assert.deepEqual(errors,[]);assert.deepEqual(fs.readFileSync('manual-labeling/reality-benders-2/review.json'),original);
 console.log('PASS: dialogue/narrator inside a sentence, distinct voice+mood buckets, real part audio, parent answer preservation, nested saves, undo, reload/resume, rejoin.');
}finally{await browser.close();await fixture.close();}
