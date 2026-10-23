const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const code=fs.readFileSync('scripts/bucket_hls.js','utf8');
function setup({native=false,apple=false,supported=true,metadata={}}={}){
  const requests=[],engines=[];
  class Hls {
    static Events={ERROR:'error'};
    static isSupported(){return supported;}
    constructor(config){this.config=config;this.listeners={};this.destroyed=0;engines.push(this);}
    on(name,listener){this.listeners[name]=listener;}
    attachMedia(audio){this.audio=audio;audio.src='blob:hls';}
    loadSource(url){this.url=url;this.audio.load();}
    destroy(){this.destroyed++;}
  }
  const context={URL,AbortController,DOMException,Event,setTimeout,clearTimeout,console,Hls,
    navigator:{vendor:apple?'Apple Computer, Inc.':'Google Inc.'},location:new URL('http://fixture.invalid/board/'),
    fetch:async(url,options)=>{requests.push({url:new URL(url),options});return {ok:true,status:200,json:async()=>({key:'derivative-key',source_audio_sha256:'source-hash',codec:'aac',sample_rate:24000,channels:1,
      duration:4,encoded_duration:4.05,playlist_url:'/_clip_hls/derivative-key/index.m3u8',timeline:{media_start:1024/24000,source_start:0,source_duration:4},...metadata})};}};
  vm.createContext(context);vm.runInContext(code,context);
  const audio=new EventTarget();Object.assign(audio,{dataset:{},src:'',paused:true,currentTime:0,duration:4.05,parentNode:null,canPlayType:()=>native?'maybe':'',pause(){this.paused=true;},load(){if(this.src)queueMicrotask(()=>this.dispatchEvent(new Event('loadedmetadata')));},removeAttribute(name){if(name==='src')this.src='';}});
  Object.defineProperty(audio,'currentSrc',{get:()=>audio.src.startsWith('/')?new URL(audio.src,context.location).href:audio.src});
  return {api:context.BucketHls,audio,requests,engines};
}
test('bucket HLS validates original identity and maps encoder lead to original clip time',async()=>{
  const h=setup();await h.api.load(h.audio,'/clips/a.wav',{sha256:'source-hash',offset:1.2});
  assert.equal(h.requests[0].url.searchParams.get('sha256'),'source-hash');assert.equal(h.engines.length,1);
  assert.ok(Math.abs(h.audio.currentTime-(1.2+1024/24000))<1e-8);assert.ok(Math.abs(h.api.sourceTime(h.audio)-1.2)<1e-8);
  assert.equal(h.api.isOriginal(h.audio,'/clips/a.wav'),false);assert.equal(h.api.provenance(h.audio).lossy,true);
  assert.equal(h.api.provenance(h.audio).source_audio_sha256,'source-hash');assert.equal(h.engines[0].config.maxBufferLength,30);
  h.api.cancel(h.audio);assert.equal(h.engines[0].destroyed,1);
});
test('Original WAV is an explicit transport switch with no derivative listening credit',async()=>{
  const h=setup();await h.api.load(h.audio,'/clips/a.wav',{sha256:'source-hash'});
  await h.api.load(h.audio,'/clips/a.wav',{sha256:'source-hash',offset:1,original:true});
  assert.equal(h.engines[0].destroyed,1);assert.equal(h.requests.length,1);assert.equal(h.api.isHls(h.audio),false);
  assert.equal(h.api.provenance(h.audio),null);assert.equal(h.api.isOriginal(h.audio,'/clips/a.wav'),true);assert.equal(h.audio.currentTime,1);
});
test('split HLS resolves the verified canonical child WAV from its exact parent frame range',async()=>{
  const h=setup({metadata:{source_audio_sha256:'child-hash'}});
  h.api.registerCut('blob:child',{source:'/clips/parent.wav',parent_audio_sha256:'parent-hash',first_frame:240,last_frame:24240,sha256:'child-hash'});
  await h.api.load(h.audio,'blob:child',{sha256:'child-hash'});
  const query=h.requests[0].url.searchParams;
  assert.equal(query.get('source'),'/clips/parent.wav');assert.equal(query.get('sha256'),'parent-hash');assert.equal(query.get('clip_sha256'),'child-hash');
  assert.equal(query.get('first_frame'),'240');assert.equal(query.get('last_frame'),'24240');h.api.cancel(h.audio);
});
test('bucket HLS source mismatches fail visibly without silently loading a WAV',async()=>{
  const h=setup({metadata:{source_audio_sha256:'wrong'}});let failed=0;h.audio.addEventListener('error',()=>failed++);
  await assert.rejects(h.api.load(h.audio,'/clips/a.wav',{sha256:'source-hash'}),/original clip hash/);
  assert.equal(h.audio.src,'');assert.equal(h.engines.length,0);assert.equal(failed,1);
});
test('unsupported browsers use the explicit WAV path, while Apple can use native HLS',async()=>{
  const fallback=setup({supported:false});await fallback.api.load(fallback.audio,'/clips/a.wav');assert.equal(fallback.requests.length,0);assert.equal(fallback.api.isOriginal(fallback.audio,'/clips/a.wav'),true);
  const apple=setup({apple:true,native:true});await apple.api.load(apple.audio,'/clips/a.wav',{sha256:'source-hash'});
  assert.equal(apple.engines.length,0);assert.match(apple.audio.currentSrc,/m3u8/);assert.equal(apple.api.provenance(apple.audio).implementation,'native-hls');apple.api.cancel(apple.audio);
});
test('fatal bucket HLS errors stop the derivative and never switch to original audio',async()=>{
  const h=setup();await h.api.load(h.audio,'/clips/a.wav',{sha256:'source-hash'});let failed=0;h.audio.addEventListener('error',()=>failed++);
  h.engines[0].listeners.error(null,{fatal:true,details:'fragmentLoadError'});
  assert.equal(h.audio.paused,true);assert.equal(h.engines[0].destroyed,1);assert.equal(h.api.isHls(h.audio),false);assert.equal(failed,1);assert.equal(h.requests.length,1);
});
test('Original WAV choice survives a sequence pause and resume until an explicit switch back to HLS',async()=>{
  const h=setup();await h.api.load(h.audio,'/clips/a.wav',{sha256:'source-hash',original:true,offset:1});
  h.audio.currentTime=1.7;h.audio.pause();
  await h.api.load(h.audio,'/clips/a.wav',{sha256:'source-hash',offset:h.api.sourceTime(h.audio)});
  assert.equal(h.api.isOriginal(h.audio,'/clips/a.wav'),true);assert.equal(h.audio.currentTime,1.7);assert.equal(h.requests.length,0);
  await h.api.load(h.audio,'/clips/a.wav',{sha256:'source-hash',original:false,offset:1.7});
  assert.equal(h.api.isHls(h.audio),true);assert.equal(h.requests.length,1);h.api.cancel(h.audio);
});
test('external sequence metadata positioning cannot replace the verified HLS source-clock mapping',async()=>{
  const h=setup();h.audio.addEventListener('loadedmetadata',()=>{h.audio.currentTime=1.25;});
  await h.api.load(h.audio,'/clips/a.wav',{sha256:'source-hash',offset:1.25});
  assert.ok(Math.abs(h.audio.currentTime-(1.25+1024/24000))<1e-8);assert.equal(h.api.sourceTime(h.audio),1.25);h.api.cancel(h.audio);
});
