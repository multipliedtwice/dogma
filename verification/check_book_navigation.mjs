// All saves use copied reviews on a random-port fixture, never the live boards.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';

const root=path.resolve('.');
const protectedPaths=['reality-benders-1','reality-benders-2-full'].map(book=>path.join(root,'manual-labeling',book,'review.json'));
const originals=protectedPaths.map(file=>fs.readFileSync(file));
const temp=fs.mkdtempSync(path.join(os.tmpdir(),'rimskify-book-nav-'));
function copyTree(from,to){
  fs.mkdirSync(to,{recursive:true});
  for(const entry of fs.readdirSync(from,{withFileTypes:true})){
    const source=path.join(from,entry.name),target=path.join(to,entry.name);
    if(entry.isDirectory())copyTree(source,target);
    else if(entry.name==='review-draft.json')fs.copyFileSync(source,target);
    else fs.linkSync(source,target);
  }
}
let server,browser;
try{
  fs.mkdirSync(path.join(temp,'training'),{recursive:true});
  fs.copyFileSync(path.join(root,'training/voice_families.json'),path.join(temp,'training/voice_families.json'));
  for(const folder of ['character-rb1/fine-tune-all','character-rb2-full/fine-tune-all',
                       'character-rb2/full/clips'])
    copyTree(path.join(root,'training-work',folder),path.join(temp,'training-work',folder));
  copyTree(path.join(root,'manual-labeling/reality-benders-1/audio'),path.join(temp,'manual-labeling/reality-benders-1/audio'));
  const book1Before=fs.readFileSync(path.join(temp,'training-work/character-rb1/fine-tune-all/review-draft.json'));
  server=spawn(path.join(root,'training-work/.venv-tts/Scripts/python.exe'),['-X','utf8','-u',
    'scripts/serve_label_pilot.py','--port','0','--root',temp,'--board-folder','character-rb2-full','--audio-prefix','character-rb2/full'],
    {cwd:root,stdio:['ignore','pipe','pipe']});
  const logs=[];server.stderr.on('data',chunk=>logs.push(chunk.toString()));
  const base=await new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>reject(Error(logs.join(''))),15000);
    server.stdout.on('data',chunk=>{const match=chunk.toString().match(/http:\/\/\S+/);if(match){clearTimeout(timeout);resolve(match[0]);}});
  });
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:390,height:844}});
  page.setDefaultTimeout(20000);
  const errors=[],failures=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('response',response=>{if(response.status()>=400)failures.push(`${response.status()} ${response.url()}`);});
  await page.goto(base);
  await page.waitForFunction(()=>!document.querySelector('#book').disabled);
  assert.equal(await page.locator('#book').inputValue(),'book2');
  const clip=await page.locator('article').first().getAttribute('data-id');
  await page.locator('article').first().locator('[data-field=voice]').selectOption('masculine_2');
  const priorURL=page.url();
  await page.locator('#book').selectOption('book1');
  await page.waitForURL('**/character-rb1/fine-tune-all/buckets.html');
  await page.waitForFunction(()=>!document.querySelector('#book').disabled);
  assert.equal(await page.locator('#book').inputValue(),'book1');
  assert.match(await page.locator('h1').innerText(),/Book 1/);
  const book2=JSON.parse(fs.readFileSync(path.join(temp,'training-work/character-rb2-full/fine-tune-all/review-draft.json')));
  assert.equal(book2.decisions[clip].corrected_voice_id,'masculine_2','Pending label must save before switching');
  assert.deepEqual(fs.readFileSync(path.join(temp,'training-work/character-rb1/fine-tune-all/review-draft.json')),book1Before);
  const visible=await page.locator('article').first().locator('[data-field=mood]').inputValue();
  assert.ok(visible,'Book 1 must display resolved saved labels');
  const sound=page.locator('article audio').first();
  await sound.evaluate(audio=>audio.load());
  await sound.evaluate(audio=>new Promise((resolve,reject)=>{
    if(audio.readyState>=1)return resolve();
    audio.addEventListener('loadedmetadata',resolve,{once:true});audio.addEventListener('error',reject,{once:true});
  }));
  assert.ok(await sound.evaluate(audio=>audio.duration>0));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Mobile selector must not cause horizontal overflow');
  await page.screenshot({path:path.join(os.tmpdir(),'rimskify-book-selector.png')});
  await page.locator('#book').selectOption('book2');
  await page.waitForFunction(()=>document.querySelector('#book')?.value==='book2'&&!document.querySelector('#book').disabled);
  assert.equal(page.url(),priorURL,'Returning to a book preserves its prior bucket/page URL');
  await page.route('**/patch-review',route=>route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'fixture save failure'})}));
  await page.evaluate(()=>{const id=document.querySelector('article').dataset.id;store.decisions[id]={...store.decisions[id],bucket_comment:'pending failure'};});
  await page.locator('#book').selectOption('book1');
  await page.waitForFunction(()=>document.querySelector('#save-status').textContent.includes('Could not switch books'));
  assert.equal(await page.locator('#book').inputValue(),'book2');
  assert.equal(page.url(),priorURL,'A failed save must keep the user on the current book');
  assert.equal(await page.locator('main').evaluate(element=>element.inert),false);
  assert.deepEqual(errors,[]);
  assert.ok(failures.every(value=>value.startsWith('500 ')&&value.endsWith('/patch-review')),failures.join('\n'));
  for(let index=0;index<protectedPaths.length;index++)assert.deepEqual(fs.readFileSync(protectedPaths[index]),originals[index]);
  console.log('PASS: same-origin book switching, pending-save protection, Book 1 saved labels/audio, mobile layout, return position, failed-save retention, and unchanged live reviews.');
}finally{
  if(browser)await browser.close();
  if(server)server.kill();
  // Verify the recursive cleanup target is the unique fixture we created.
  assert.ok(path.resolve(temp).startsWith(path.resolve(os.tmpdir())+path.sep));
  assert.ok(path.basename(temp).startsWith('rimskify-book-nav-'));
  fs.rmSync(temp,{recursive:true,force:true});
}
