const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../scripts/render_higgs_progressive_page.py'),'utf8');
const script=source.match(/<script>([\s\S]*?)<\/script>/)[1];
function fixture(){
 const events=new Map();
 const audio={currentTime:0,duration:300,paused:true,ended:false,plays:0,load(){this.currentTime=0;this.paused=true;this.ended=false;},
  addEventListener(name,fn,opts){const list=events.get(name)||[];list.push({fn,once:!!opts?.once});events.set(name,list);},
  fire(name){for(const event of [...(events.get(name)||[])]){if(event.once)events.set(name,events.get(name).filter(v=>v!==event));event.fn();}},
  play(){this.plays++;this.paused=false;this.ended=false;this.fire('play');return Promise.resolve();},
  pause(){this.paused=true;this.fire('pause');},removeAttribute(){this.src='';}};
 const select={value:'',options:[],replaceChildren(){this.options=[];},append(option){this.options.push(option);},addEventListener(){}};
 const nodes={'#player':audio,'#chapter':select,'#download':{},'#status':{},'#checks':{},
  '#music-enabled':{checked:true},'#music-volume':{value:'1'},'#music-credit':{}};
 for(const id of ['reader-text','reader-status','report-dialog','stress-options','report-status','report-comment','report-other','report-title'])nodes['#'+id]={replaceChildren(){},querySelectorAll:()=>[]};
 const music=[];
 class MusicAudio {constructor(src){this.src=src;this.paused=true;this.currentTime=0;music.push(this);}play(){this.paused=false;return Promise.resolve();}pause(){this.paused=true;}}
 const context=vm.createContext({document:{activeElement:null,querySelector:key=>nodes[key],createElement:()=>({})},
  localStorage:{getItem:()=>null,setItem(){}},fetch:()=>new Promise(()=>{}),setInterval(){},console,Audio:MusicAudio});
 vm.runInContext(script,context);
 return {audio,context,music,nodes,enableMusic(){vm.runInContext([...source.matchAll(/<script>([\s\S]*?)<\/script>/g)][1][1],context);},
  update(state){context.next=state;vm.runInContext('update(next)',context);},chapter(){return vm.runInContext('active',context);}};
}
const row=(id,seconds,complete,hash)=>({id,seconds,complete,wav_sha256:hash,stream:`/${hash}.wav`,audio:`${hash}.mp3`,word_flags:0,assignment_flags:0});
const state=(chapters)=>({generation:'new-render',status:'rendering',chapters});
{
 const f=fixture(),ready=row('c0001',600,true,'one');ready.word_flags=2;
 f.update({...state({c0001:ready}),held_sections:{'c0002/chunk-000':{reason:'word check'}}});
 assert.match(f.nodes['#status'].textContent,/оценочная/);
 f.nodes['#chapter'].value='c0002';f.nodes['#chapter'].onchange();
 assert.match(f.nodes['#status'].textContent,/не опубликована/);
 assert.match(f.nodes['#status'].textContent,/1 частей/);
 assert.equal(f.nodes['#download'].hidden,true);
}
{
 const f=fixture();f.update(state({c0001:row('c0001',300,false,'one')}));f.audio.fire('loadedmetadata');f.audio.play();
 f.audio.currentTime=300;f.audio.ended=true;f.audio.fire('ended');
 f.update(state({c0001:row('c0001',600,true,'two'),c0002:row('c0002',300,false,'three')}));
 assert.equal(f.chapter(),'c0001','A new complete prefix must not skip unread audio');
 f.audio.duration=600;f.audio.fire('loadedmetadata');
 assert.equal(f.audio.currentTime,300);assert.equal(f.audio.src,'/two.wav');assert.equal(f.audio.paused,false);
 f.audio.currentTime=600;f.audio.ended=true;f.audio.fire('ended');
 f.update(state({c0001:row('c0001',600,true,'two'),c0002:row('c0002',300,false,'three')}));
 assert.equal(f.chapter(),'c0002','Advance only after the available complete chapter has actually ended');
}
{
 const f=fixture();f.update(state({c0001:row('c0001',300,false,'one')}));f.audio.fire('loadedmetadata');
 f.audio.currentTime=83;f.audio.pause();
 f.update(state({c0001:row('c0001',600,false,'two')}));f.audio.duration=600;f.audio.fire('loadedmetadata');
 assert.equal(f.audio.currentTime,83);assert.equal(f.audio.plays,0,'Appending audio must not restart paused playback');
}
console.log('Progressive player: extension, buffer-end resume, paused state and chapter transition passed.');
{
 const f=fixture();f.enableMusic();const r=row('c0001',600,false,'voice');
 r.music=[{kind:'opening',start:0,seconds:75,sha256:'cue',stream:'/cue.wav'}];r.audio_with_music='mixed.mp3';
 f.update(state({c0001:r}));f.audio.fire('loadedmetadata');f.audio.currentTime=10;f.audio.play();
 assert.equal(f.music[0].paused,false);assert.equal(f.music[0].currentTime,10);
 assert.equal(f.nodes['#download'].href,'mixed.mp3');
 f.nodes['#music-enabled'].checked=false;f.nodes['#music-enabled'].onchange();
 assert.equal(f.music[0].paused,true);assert.equal(f.nodes['#download'].href,'voice.mp3');
 f.nodes['#music-enabled'].checked=true;f.nodes['#music-enabled'].onchange();f.audio.pause();
 assert.equal(f.music[0].paused,true,'Narration pause must also pause music');
 f.audio.currentTime=50;f.audio.play();assert.equal(f.music[0].currentTime,50);
 f.audio.currentTime=90;f.audio.fire('timeupdate');assert.equal(f.music[0].paused,true,'Opening bed ends rather than looping indefinitely');
}
console.log('Progressive music: synchronized play, pause, seek, toggle and export selection passed.');
