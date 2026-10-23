// Shared bucket transport. Original WAVs and their hashes remain the review identity.
(function(global){
  'use strict';
  const states=new WeakMap(),cuts=new Map(),active=new Set(),originalChoices=new Set();
  const absolute=value=>new URL(value,global.location.href).href;
  const originalTime=audio=>{const state=states.get(audio);return Math.max(0,audio.currentTime-(state?.hls?Number(state.metadata?.timeline?.media_start)||0:0));};
  const sourceTime=audio=>{const state=states.get(audio),duration=Number(state?.metadata?.duration);return Number.isFinite(duration)&&state?.hls?Math.min(duration,originalTime(audio)):originalTime(audio);};
  function capability(audio){
    const native=!!audio.canPlayType?.('application/vnd.apple.mpegurl');
    if(/Apple/i.test(global.navigator?.vendor||'')&&native)return 'native-hls';
    if(global.Hls?.isSupported())return 'hls.js';
    if(native)return 'native-hls';
    if(!global.Hls&&global.MediaSource)return 'unavailable';
    return 'wav-unsupported';
  }
  function cancel(audio){
    const state=states.get(audio);if(!state)return;
    state.abort.abort();state.engine?.destroy();state.engine=null;active.delete(audio);
  }
  function status(audio,state,text,error=false){
    if(!global.document||!audio.parentNode)return;
    let host=state.status;
    if(!host?.isConnected){host=global.document.createElement('div');host.className='bucket-hls-status small';host.setAttribute('role','status');audio.insertAdjacentElement('afterend',host);state.status=host;}
    host.replaceChildren();const label=global.document.createElement('span');label.textContent=text;host.append(label);
    const original=state.mode==='wav-original';
    const button=global.document.createElement('button');button.type='button';button.textContent=error?(original?'Retry Original WAV':'Retry streaming'):(original?'HLS listening copy':'Original WAV');
    button.addEventListener('click',()=>{const offset=sourceTime(audio);load(audio,state.original,{...state.options,offset,original:error?original:!original}).then(()=>audio.play()).catch(()=>{});});host.append(' ',button);
  }
  function isHls(audio){const state=states.get(audio);return !!(state?.hls&&state.ready&&!state.failed&&!state.abort.signal.aborted);}
  function isOriginal(audio,url){
    const state=states.get(audio);
    return (!state||!state.hls&&!state.failed)&&audio.currentSrc===absolute(url);
  }
  function provenance(audio){
    const state=states.get(audio);if(!isHls(audio))return null;
    const m=state.metadata;
    return {kind:'hls',implementation:state.mode,lossy:true,derivative_key:m.key,source_audio_sha256:m.source_audio_sha256,
      codec:m.codec,sample_rate:m.sample_rate,channels:m.channels,manifest_url:absolute(m.playlist_url),timeline:m.timeline,
      ...(m.parent_audio_sha256?{parent_audio_sha256:m.parent_audio_sha256,first_frame:m.first_frame,last_frame:m.last_frame}:{})};
  }
  async function resolve(source,options,signal){
    const cut=cuts.get(source),request=new URL('/_clip_hls/resolve',global.location.href);
    const url=new URL(cut?.source||source,global.location.href);
    if(url.origin!==global.location.origin||url.protocol==='blob:')throw Error('This clip has no verified same-origin HLS source. Use Original WAV.');
    request.searchParams.set('source',url.pathname);
    const expected=options.sha256||cut?.sha256;
    if(cut){request.searchParams.set('sha256',cut.parent_audio_sha256);request.searchParams.set('first_frame',cut.first_frame);request.searchParams.set('last_frame',cut.last_frame);request.searchParams.set('clip_sha256',expected);}
    else if(expected)request.searchParams.set('sha256',expected);
    let response;
    for(let attempt=0;attempt<4;attempt++){
      response=await global.fetch(request.href,{signal,cache:'no-store'});
      if(response.status!==503||attempt===3)break;
      await new Promise((done,reject)=>{const timer=setTimeout(done,Math.min(3000,Math.max(250,Number(response.headers.get('Retry-After')||1)*1000)));signal.addEventListener('abort',()=>{clearTimeout(timer);reject(new DOMException('Aborted','AbortError'));},{once:true});});
    }
    if(!response.ok){const detail=await response.json().catch(()=>({}));throw Error(detail.error||'HLS preparation failed (HTTP '+response.status+').');}
    const metadata=await response.json();
    if(!metadata.key||!metadata.playlist_url||!metadata.source_audio_sha256||expected&&metadata.source_audio_sha256!==expected)throw Error('HLS derivative does not match the original clip hash.');
    return metadata;
  }
  function expectedHash(audio,source){
    const id=audio.dataset?.clip;
    return cuts.get(source)?.sha256||(id&&typeof precision!=='undefined'?precision[id]?.audio_sha256:'')||'';
  }
  async function load(audio,source,options={}){
    source=String(source);const prior=states.get(audio),offset=Number.isFinite(options.offset)?Math.max(0,options.offset):0;
    cancel(audio);audio.pause();audio.removeAttribute('src');audio.load();
    const sha256=options.sha256||expectedHash(audio,source),choice=sha256?'sha256:'+sha256:'url:'+source;
    if(options.original===true)originalChoices.add(choice);else if(options.original===false)originalChoices.delete(choice);
    const useOriginal=options.original??originalChoices.has(choice);
    const state={original:source,options:{...options,sha256,original:useOriginal},abort:new AbortController(),engine:null,hls:false,ready:false,failed:false,status:prior?.status,last:null,ranges:[],signature:null};
    states.set(audio,state);active.add(audio);audio.dataset.bucketSource=source;
    const mode=useOriginal?'wav-original':capability(audio);state.mode=mode;
    const fail=error=>{
      if(state.abort.signal.aborted||state.failed||states.get(audio)!==state)return;
      state.failed=true;audio.pause();state.engine?.destroy();state.engine=null;
      status(audio,state,'Could not stream this clip: '+error.message+'. ',true);audio.dispatchEvent(new Event('error'));
    };
    status(audio,state,mode.startsWith('wav')?(mode==='wav-original'?'Original WAV. ':'WAV: HLS is unsupported in this browser. '):'Preparing HLS playback… ');
    try{
      if(mode==='unavailable')throw Error('The locally pinned HLS library did not load');
      if(!mode.startsWith('wav')){state.metadata=await resolve(source,state.options,state.abort.signal);state.hls=true;}
      if(state.abort.signal.aborted)return;
      return await new Promise((done,reject)=>{
        const timer=setTimeout(()=>{const error=Error('Audio loading timed out');fail(error);reject(error);},45000);
        const cleanup=()=>{clearTimeout(timer);audio.removeEventListener('loadedmetadata',ready);audio.removeEventListener('error',failed);};
        const failed=()=>{cleanup();reject(Error('Audio could not be played'));};
        const ready=()=>{
          if(state.abort.signal.aborted){cleanup();return;}
          cleanup();state.ready=true;
          const lead=state.hls?Number(state.metadata.timeline?.media_start)||0:0;
          const duration=state.hls?state.metadata.duration:audio.duration;
          audio.currentTime=lead+Math.min(offset,Math.max(0,(Number.isFinite(duration)?duration:offset)-.001));
          status(audio,state,state.hls?'HLS / AAC listening copy. Use Original WAV for exact audio-edge verification. ':'Original WAV. ');
          done(state.metadata||{source_audio_sha256:state.options.sha256});
        };
        audio.addEventListener('loadedmetadata',ready,{once:true});audio.addEventListener('error',failed,{once:true});
        state.abort.signal.addEventListener('abort',()=>{cleanup();reject(new DOMException('Aborted','AbortError'));},{once:true});
        if(mode==='hls.js'){
          const Hls=global.Hls;
          state.engine=new Hls({enableWorker:true,startPosition:offset+(Number(state.metadata.timeline?.media_start)||0),maxBufferLength:30,maxMaxBufferLength:45,backBufferLength:5,maxBufferSize:2*1024*1024});
          state.engine.on(Hls.Events.ERROR,(_event,data)=>{if(data.fatal)fail(Error('HLS retries exhausted ('+data.details+')'));});
          state.engine.attachMedia(audio);state.engine.loadSource(absolute(state.metadata.playlist_url));
        }else{audio.src=state.hls?absolute(state.metadata.playlist_url):source;audio.load();}
      });
    }catch(error){if(!state.abort.signal.aborted)fail(error);throw error;}
  }
  function scan(root=global.document){
    for(const audio of root.querySelectorAll?.('audio')||[]){
      const source=audio.dataset.bucketSource||audio.getAttribute('src');if(!source||states.has(audio))continue;
      if(audio.closest('details:not([open])'))continue;
      load(audio,source).catch(()=>{});
    }
    for(const audio of active)if(!audio.isConnected)cancel(audio);
  }
  function record(audio){
    if(!isHls(audio)||typeof loaded==='undefined'||!loaded||typeof byId==='undefined')return;
    const id=audio.dataset.clip,row=byId.get(id),state=states.get(audio);if(!row||row.comparison_book===1||hasStagedLabels(id))return;
    const sig=fingerprint(row),time=sourceTime(audio),duration=state.metadata.duration;
    if(state.signature!==sig){state.signature=sig;state.last=null;state.ranges=[];}
    if(!audio.seeking&&state.last!==null&&time>=state.last){
      const lead=Number(state.metadata.timeline?.media_start)||0;
      const fresh=Array.from({length:audio.played.length},(_,i)=>[Math.max(0,audio.played.start(i)-lead,state.last),Math.min(duration,audio.played.end(i)-lead,time)]).filter(([a,b])=>b>a);
      state.ranges=mergePlayedRanges([...state.ranges,...fresh]);
    }
    state.last=audio.seeking?null:time;
    const covered=state.ranges.reduce((sum,[a,b])=>sum+b-a,0);if(!(duration>0&&covered>=duration*.75))return;
    const current=decision(id),label=labels(row),transport=provenance(audio);
    if(current.listening_evidence?.transport?.derivative_key===transport.derivative_key&&current.listening_evidence.voice_family_id===label.voice&&current.listening_evidence.mood===label.mood)return;
    const next={...current,listening_evidence:{schema:1,kind:'lossy_listening_copy',source_audio_sha256:state.metadata.source_audio_sha256,
      voice_family_id:label.voice,mood:label.mood,coverage:Math.min(1,covered/duration),playback_rate:audio.playbackRate,
      playback_mode:audio.id==='sequence'?'play_all':'single_clip',tab_visible:global.document.visibilityState==='visible',transport,
      exact_wav_acceptance:false,training_approved:false,completed_at:new Date().toISOString()}};
    if(typeof setDecision==='function')setDecision(id,next);else store.decisions[id]=next;
    store.stash();queueSave();
  }
  global.BucketHls={load,cancel,sourceTime,isHls,isOriginal,provenance,scan,registerCut:(url,info)=>cuts.set(url,info)};
  if(global.document){
    const start=()=>{scan();new MutationObserver(()=>scan()).observe(global.document.body,{childList:true,subtree:true});};
    if(global.document.readyState==='loading')global.document.addEventListener('DOMContentLoaded',start,{once:true});else start();
    global.document.addEventListener('toggle',event=>{if(event.target.open)scan(event.target);},true);
    global.document.addEventListener('seeking',event=>{const state=states.get(event.target);if(state)state.last=null;},true);
    global.document.addEventListener('play',event=>{
      const audio=event.target,state=states.get(audio);if(!state)return;
      const row=typeof byId==='undefined'?null:byId.get(audio.dataset?.clip);
      if(row){const sig=fingerprint(row);if(state.signature!==sig){state.signature=sig;state.ranges=[];}}
      state.last=sourceTime(audio);
    },true);
    global.document.addEventListener('timeupdate',event=>record(event.target),true);
    global.document.addEventListener('ended',event=>record(event.target),true);
    global.addEventListener('pagehide',()=>{for(const audio of active)cancel(audio);});
  }
})(globalThis);
