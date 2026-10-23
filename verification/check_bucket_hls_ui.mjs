// Real board/audio smoke test. Every writable file belongs to a random-port fixture.
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import {chromium} from 'playwright';

const root=path.resolve('.');
const boards=['character-rb1','character-rb2','character-rb2-full','character-rb3-full'];
const protectedPaths=boards.flatMap(b=>['review-draft.json','candidates.jsonl','summary.json'].map(n=>`training-work/${b}/fine-tune-all/${n}`))
  .concat(['reality-benders-1','reality-benders-2','reality-benders-2-full','reality-benders-3'].flatMap(b=>['review.json','latest.json'].map(n=>`manual-labeling/${b}/${n}`)))
  .filter(p=>fs.existsSync(p));
const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
const originals=new Map(protectedPaths.map(p=>[p,hash(p)]));
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'dogma-bucket-hls-'));
let server,browser;
function copyTree(source,target,immutable=false){
  fs.mkdirSync(target,{recursive:true});
  for(const item of fs.readdirSync(source,{withFileTypes:true})){
    const from=path.join(source,item.name),to=path.join(target,item.name);
    if(item.isDirectory())copyTree(from,to,immutable);
    else if(immutable||/\.wav$/i.test(item.name)){try{fs.linkSync(from,to);}catch{fs.copyFileSync(from,to);}}
    else fs.copyFileSync(from,to);
  }
}
try{
  fs.mkdirSync(path.join(temporary,'training'),{recursive:true});
  fs.copyFileSync('training/voice_families.json',path.join(temporary,'training/voice_families.json'));
  for(const board of boards)copyTree(`training-work/${board}/fine-tune-all`,path.join(temporary,`training-work/${board}/fine-tune-all`));
  for(const relative of ['training-work/character-rb2/pilot/clips','training-work/character-rb2/full/clips',
    'training-work/character-rb3/labeling-full/clips','training-work/character-rb1/fine-tune-expanded/clips',
    'training-work/full-book-pilot/clips','training-work/sentence-clips','manual-labeling/reality-benders-1/audio']){
    if(fs.existsSync(relative))copyTree(relative,path.join(temporary,relative),true);
  }
  const python=path.join(root,'training-work/higgs-production/.venv/Scripts/python.exe');
  const args=boards.flatMap(b=>['--board',path.join(temporary,`training-work/${b}/fine-tune-all`)]);
  const installed=spawnSync(python,['scripts/install_bucket_hls_ui.py',...args],{cwd:root,encoding:'utf8'});
  assert.equal(installed.status,0,installed.stderr||installed.stdout);
  server=spawn(python,['-X','utf8','-u','scripts/serve_label_pilot.py','--host','127.0.0.1','--port','0',
    '--board-folder','character-rb3-full','--audio-prefix','character-rb3/labeling-full','--root',temporary],{cwd:root,stdio:['ignore','pipe','pipe']});
  let serverErrors='';server.stderr.on('data',chunk=>serverErrors+=chunk);
  const base=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Fixture failed to start: '+serverErrors.slice(-2000))),15000);
    server.once('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exited '+code));});
    server.stdout.on('data',chunk=>{const found=String(chunk).match(/http:\/\/[^\s]+/);if(found){clearTimeout(timer);resolve(found[0]);}});
  });
  assert.ok(![1438,1439,1440,1442].includes(Number(new URL(base).port)));
  browser=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',args:['--autoplay-policy=no-user-gesture-required']});
  const page=await browser.newPage({viewport:{width:390,height:844}}),errors=[],requests=[];
  let failedFragment='';
  await page.route('**/_clip_hls/*/f000001.m4s',async route=>{
    if(!failedFragment){failedFragment=route.request().url();await route.abort('failed');}
    else await route.continue();
  });
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>requests.push(r.url()));
  await page.goto(base);
  await page.waitForFunction(()=>typeof loaded!=='undefined'&&loaded,{timeout:60000});
  await page.locator('#play-bucket').click();
  await page.waitForFunction(()=>{const a=document.querySelector('#sequence');return a&&!a.paused&&a.currentTime>.2;},undefined,{timeout:90000});
  const playing=await page.locator('#sequence').evaluate(a=>({src:a.currentSrc,duration:a.duration,time:a.currentTime,clip:a.dataset.clip}));
  assert.ok(playing.src.startsWith('blob:')||playing.src.includes('m3u8'),'Bucket player must attach HLS');
  const originalAcceptance=await page.evaluate(id=>JSON.stringify(decision(id).playback_acceptance??null),playing.clip);
  await page.waitForFunction(()=>{const a=document.querySelector('#sequence');return BucketHls.sourceTime(a)>=Number(BucketHls.provenance(a)?.timeline.source_duration)*.8;},undefined,{timeout:90000});
  await page.locator('#stop').click();
  const streamedEvidence=await page.evaluate(id=>({acceptance:JSON.stringify(decision(id).playback_acceptance??null),evidence:decision(id).listening_evidence}),playing.clip);
  assert.equal(streamedEvidence.acceptance,originalAcceptance,'Streamed listening cannot create exact-WAV acceptance');
  assert.equal(streamedEvidence.evidence?.transport?.lossy,true);
  assert.equal(streamedEvidence.evidence?.exact_wav_acceptance,false);
  const cursor=await page.evaluate(()=>({clip:currentQueueClip(),time:cursorTime}));
  assert.ok(cursor.time>.1);
  await page.locator('#play-bucket').click();
  await page.waitForFunction(time=>{const a=document.querySelector('#sequence');return !a.paused&&a.currentTime>=time-.2;},cursor.time,{timeout:60000});
  await page.locator('#sequence + .bucket-hls-status button').click();
  await page.waitForFunction(()=>BucketHls.isOriginal(document.querySelector('#sequence'),audioURL(precision[currentQueueClip()].audio)),undefined,{timeout:60000});
  await page.locator('#stop').click();
  await page.locator('#play-bucket').click();
  await page.waitForFunction(()=>{const a=document.querySelector('#sequence');return !a.paused&&BucketHls.isOriginal(a,audioURL(precision[currentQueueClip()].audio));},undefined,{timeout:60000});
  await page.locator('#next-clip').click();
  await page.waitForFunction(previous=>{const a=document.querySelector('#sequence');return a.dataset.clip!==previous&&!a.paused&&a.currentTime>.1;},playing.clip,{timeout:60000});
  await page.locator('#stop').click();
  const evidence=await page.evaluate(()=>({acceptance:decision(currentQueueClip()).playback_acceptance,evidence:decision(currentQueueClip()).listening_evidence}));
  assert.ok(!evidence.acceptance?.transport?.lossy,'HLS must never create exact-WAV acceptance');
  assert.deepEqual(errors,[]);
  assert.ok(failedFragment&&requests.filter(url=>url===failedFragment).length>=2,'hls.js retries a failed fragment');
  assert.ok(requests.some(url=>/hls/i.test(url)&&!/hls(?:\.min)?\.js/.test(url)),'HLS media must be requested');
  console.log(JSON.stringify({isolated:true,playing,cursor,requests:requests.filter(u=>/hls/i.test(u)).length,errors}));
}finally{
  if(browser)await browser.close();
  if(server&&!server.killed)server.kill();
  for(const [file,before] of originals)assert.equal(hash(file),before,`${file} changed`);
  // This absolute path is created by mkdtemp within the OS temporary directory.
  assert.ok(path.resolve(temporary).startsWith(path.resolve(os.tmpdir())+path.sep));
  fs.rmSync(temporary,{recursive:true,force:true});
}
