// Browser-only editor check. Network calls are intercepted; no review file is opened or changed.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import path from 'node:path';

const families=Array.from({length:20},(_,index)=>({id:`family_${index}`,label:`Family ${index}`,group:'Fixture'}));
let review={schema:1,narrator_id:'vlad-rimsky',registry_version:'vlad-rimsky-v1',decisions:{}};
let taxonomy={schema:1,narrator_id:'vlad-rimsky',registry_version:'vlad-rimsky-v1',revision:0,families_sha256:'base-sha',families};
const posts=[],createPosts=[];
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
  const page=await browser.newPage();
  await page.route('http://fixture.test/**',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:`<!doctype html><html><head></head><body>
      <select id="bucket"><option value="family_3|calm" selected>bucket</option><option value="family_4|neutral">other</option></select>
      <p id="bucket-summary">Family 3</p><div id="voice-family-editor"></div></body></html>`});
    if(url.pathname.endsWith('/data'))return route.fulfill({json:taxonomy});
    if(url.pathname.endsWith('/review'))return route.fulfill({json:review});
    if(url.pathname.endsWith('/save-decision')){
      const payload=request.postDataJSON();posts.push(payload);
      const saved={...payload.decision,_revision:1,reviewed_at:'fixture'};
      review={...review,decisions:{...review.decisions,[payload.family_id]:saved}};
      return route.fulfill({json:{saved:true,review}});
    }
    if(url.pathname.endsWith('/create-family')){
      const payload=request.postDataJSON();createPosts.push(payload);
      const created={id:'custom_glubokiy_strazh',label:'Глубокий страж',group:'Fixture',origin:'listener_created',synthesis_status:'requires_pack_voice'};
      taxonomy={...taxonomy,revision:1,families_sha256:'created-sha',families:[...families,created]};
      return route.fulfill({status:201,json:{created,taxonomy}});
    }
    return route.abort();
  });
  await page.goto('http://fixture.test/');
  await page.addScriptTag({path:path.resolve('scripts/bucket_voice_family_editor.js')});
  const editor=page.locator('#voice-family-editor');
  await editor.locator('[data-family-selector]').waitFor();
  await page.evaluate(()=>document.addEventListener('voice-family-taxonomy-changed',event=>window.createdTaxonomy=event.detail));
  assert.equal(await editor.locator('[data-family-selector]').inputValue(),'family_3');
  assert.equal(await editor.locator('[data-family-selector] option').count(),21,'blank plus all 20 taxonomy families');
  assert.equal(await editor.locator('[name=perceived_age] option[value=young]').textContent(),'Young adult');
  assert.equal(await editor.locator('[name=perceived_age] option[value=child]').textContent(),'Child');
  await editor.locator('[name=perceived_age]').selectOption('mature');
  await editor.locator('[name=casting_description]').fill('Low, dry and textured.');
  await editor.locator('[name=character_fit]').fill('Older authority figures.');
  await editor.locator('[name=avoid_for]').fill('Keep separate from the smoother low family.');
  assert.equal(posts.length,0,'Typing must not autosave');
  assert.match(await editor.locator('[data-family-summary-status]').textContent(),/Unsaved changes/);
  await editor.locator('[data-save-family]').click();
  await page.waitForFunction(()=>document.querySelector('.family-editor-status')?.textContent.includes('Saved revision 1'));
  assert.equal(posts.length,1);
  assert.equal(posts[0].family_id,'family_3');
  assert.equal(posts[0].decision.descriptors.perceived_age,'mature');
  assert.equal(posts[0].decision.character_fit,'Older authority figures.');
  assert.match(await editor.locator('[data-family-summary-status]').textContent(),/Saved revision 1/);
  assert.ok(!('mood' in posts[0].decision)&&!('clip' in posts[0].decision));
  await page.locator('#bucket').selectOption('family_4|neutral');
  await page.locator('#bucket').dispatchEvent('change');
  await page.waitForFunction(()=>document.querySelector('[data-family-selector]')?.value==='family_4');
  await editor.locator('.create-family summary').click();
  await editor.locator('[data-new-family-name]').fill('Глубокий страж');
  assert.equal(await editor.locator('[data-new-family-id]').textContent(),'custom_glubokiy_strazh');
  await editor.locator('[data-new-family-group]').selectOption('Fixture');
  await editor.locator('[data-create-family]').click();
  await page.waitForFunction(()=>document.querySelector('[data-family-selector]')?.value==='custom_glubokiy_strazh');
  assert.equal(createPosts.length,1);assert.equal(createPosts[0].before_families_sha256,'base-sha');
  assert.equal(createPosts[0].id_preview,'custom_glubokiy_strazh');
  assert.equal(await editor.locator('[data-family-selector] option').count(),22,'blank plus the protected 20 and new family');
  assert.match(await editor.textContent(),/not synthesizable until a reviewed performance is added to a Higgs pack/i);
  assert.equal(await page.evaluate(()=>window.createdTaxonomy?.families.at(-1).id),'custom_glubokiy_strazh');
  assert.equal(posts.length,1,'Creating a family must not write a description or clip decision');
  console.log('PASS: editor saves descriptions explicitly and creates a global label-only family with a server-authoritative ID.');
}finally{await browser.close()}
