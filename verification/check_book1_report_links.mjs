import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {chromium} from 'playwright';

const report=JSON.parse(await readFile('training-work/character-rb1/warned-audio-review-20260929/assessment.json','utf8'));
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const context=await browser.newContext({viewport:{width:1300,height:900}});
  let writes=0;
  for(const endpoint of ['patch-review','save-review'])await context.route(`**/${endpoint}`,route=>{writes++;return route.abort();});
  const page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const base='http://127.0.0.1:1437/character-rb1/fine-tune-all/';
  await page.goto(base+'warned-audio-review.html');
  assert.equal(await page.locator('a[href*="buckets.html?clip="]').count(),21);
  for(const clip of report.clips){
    // An obsolete bucket/page/filter must not prevent an explicit report link
    // from finding the current saved bucket and the right sorted page.
    await page.goto(base+'buckets.html?'+new URLSearchParams({bucket:'mechanical_stats|neutral',page:'999',show:'verified',clip:clip.id,focus:'1'}));
    await page.waitForFunction(id=>loaded&&pageSnapshot.includes(id),clip.id);
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const state=await page.evaluate(id=>{
      const card=clipCard(id),rect=card.getBoundingClientRect(),bar=document.querySelector('.player-bar').getBoundingClientRect();
      return {bucket:selected,expected:bucketKey(labels(byId.get(id)).voice,labels(byId.get(id)).mood),
        filter:document.querySelector('#filter').value,clip:currentQueueClip(),top:rect.top,barBottom:bar.bottom,
        paused:[...document.querySelectorAll('audio')].every(audio=>audio.paused)};
    },clip.id);
    assert.equal(state.bucket,state.expected,clip.id);
    assert.equal(state.filter,'all');assert.equal(state.clip,clip.id);
    assert.ok(state.top>=state.barBottom-2&&state.top<450,JSON.stringify(state));
    assert.ok(state.paused,'A link must not autoplay');
  }
  assert.equal(writes,0);assert.deepEqual(errors,[]);
  console.log('All 21 report links locate and scroll to the current bucket clip. No autoplay or review writes.');
}finally{await browser.close();}
