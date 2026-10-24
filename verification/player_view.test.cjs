const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
const {sourceTree}=require('./layout_source.cjs');
const m={exports:{}};
new Function('module','exports',ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/lib/playerView.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(m,m.exports);
const {chapterTimeline,chapterTrackTimeline,timelineTarget,timelineKeyTarget,spaceToggles,sleepDeadline}=m.exports;
const segment=(id,chapter='c1',duration=10,ready=true)=>({id,chapter_id:chapter,status:ready?'ready':'pending',audio_hash:ready?id:null,duration:ready?duration:0,cues:[],spoken:'Prepared passage',speaker:'narrator'});
const g=segments=>({id:'g',book_id:'b',name:'Saved narration',status:'ready',segments,cast:[],analysis:{complete:true},exports:[]});
const walk=(tree,check)=>Array.isArray(tree)?tree.flatMap(v=>walk(v,check)):!tree||typeof tree!=='object'?[]:[...(check(tree)?[tree]:[]),...walk(tree.props?.children,check)];

test('chapter time follows complete saved segments instead of resetting per passage',()=>{
  const generation=g([segment('a'),segment('b'),segment('c','c2')]);
  const time=chapterTimeline(generation,generation.segments[1],3);
  assert.equal(time.elapsed,13);assert.equal(time.duration,20);assert.equal(time.complete,true);assert.equal(time.previous,null);assert.equal(time.next.id,'c');
  assert.deepEqual(timelineTarget(time.seekable,15),{id:'b',offset:5});
  assert.deepEqual(timelineTarget(time.seekable,10),{id:'b',offset:0});
});
test('chapter seek uses only the complete prefix and never jumps over a pending passage',()=>{
  const generation=g([segment('a'),segment('b','c1',0,false),segment('c')]);
  const ready=chapterTimeline(generation,generation.segments[0],2);
  assert.equal(ready.duration,10);assert.equal(ready.complete,false);
  assert.deepEqual(timelineTarget(ready.seekable,999),{id:'a',offset:10});
  const waiting=chapterTimeline(generation,generation.segments[1],0);
  assert.equal(waiting.elapsed,10);assert.equal(waiting.mode,'chapter');
  const after=chapterTimeline(generation,generation.segments[2],3);
  assert.equal(after.mode,'passage');assert.equal(after.elapsed,3);assert.equal(after.duration,10);
  generation.config={opening:{variants:{music:{audio_hash:'brand',duration:8}}}};
  const isolated=chapterTrackTimeline(generation,after);
  assert.equal(isolated.elapsed,3);assert.equal(isolated.duration,10);assert.equal(isolated.openingDuration,0);
});
test('missing, zero-duration, unready and nonfinite timeline inputs cannot become seek targets',()=>{
  assert.equal(timelineTarget([],1),null);
  for(const bad of [segment('x','c',0),segment('x','c',Infinity),segment('x','c',10,false),{...segment('x'),audio_hash:null}])assert.equal(timelineTarget([bad],1),null);
  assert.equal(timelineTarget([segment('x')],NaN),null);
  assert.deepEqual(timelineTarget([segment('x')],-3),{id:'x',offset:0});
  assert.equal(chapterTimeline(g([]),null,Infinity).duration,0);
});
test('chapter navigation uses saved selection order and ignores chapters without audio segments',()=>{
  const generation=g([segment('a','c2'),segment('b','c2'),segment('c','c5'),segment('d','c9')]);
  const time=chapterTimeline(generation,generation.segments[2],1);
  assert.equal(time.previous.id,'a');assert.equal(time.next.id,'d');
});
test('sleep choices cannot turn an active timer into NaN',()=>{
  assert.equal(sleepDeadline('off',1),0);assert.equal(sleepDeadline('15',100),900100);
  for(const value of ['active','on','-1','0','100000','NaN'])assert.equal(sleepDeadline(value,1),null);
});
test('actual player exposes the follow action only after scrolling, plus playback settings and honest chapter seeking',()=>{
  const generation=g([segment('a'),segment('b')]),selected=generation.segments[1];
  const calls=[];
  const {tree}=sourceTree('src/components/PlayerBar.tsx',{}, {component:'PlayerBar',book:{id:'b',title:'Book',chapters:[{id:'c1',title:'First chapter'}]},generation,
    settings:{language:'en',playback_rate:1},player:{segment:selected,position:3,playing:false,waiting:false,error:'',sleepAt:0},status:'Saved',statusAction:null,
    onSelect:(...args)=>calls.push(args),onUpdate:value=>calls.push(value),onToggle:()=>{},onSkip:()=>{},onSettings:()=>calls.push('settings'),onHistory:()=>{},showFollow:true,onFollow:()=>calls.push('follow')});
  const range=walk(tree,n=>n.tag==='input'&&n.props.type==='range')[0];
  assert.equal(range.props.value,13);assert.equal(range.props.max,20);
  range.props.onChange({target:{value:'15'}});assert.deepEqual(calls[0],['b',false,5]);
  const follow=walk(tree,n=>n.tag==='button'&&n.props['aria-label']==='Follow')[0];
  assert.ok(follow);follow.props.onClick();assert.equal(calls[1],'follow');
  assert.equal(walk(tree,n=>n.tag==='button'&&n.props['aria-label']==='Subtitles').length,0);
  walk(tree,n=>n.tag==='button'&&n.props['aria-label']==='Playback settings')[0].props.onClick();assert.equal(calls[2],'settings');
});
test('actual player seeking while waiting preserves play intent and shows no imaginary duration',()=>{
  const generation=g([segment('a'),segment('b','c1',0,false)]),calls=[];
  const {tree}=sourceTree('src/components/PlayerBar.tsx',{}, {component:'PlayerBar',book:{title:'Book',chapters:[]},generation,settings:null,
    player:{segment:generation.segments[1],position:0,playing:false,waiting:true,error:'',sleepAt:0},status:'Preparing',statusAction:null,onSelect:(...args)=>calls.push(args),onUpdate:()=>{},onSettings:()=>{},onHistory:()=>{},onToggle:()=>{},onSkip:()=>{}});
  const range=walk(tree,n=>n.tag==='input'&&n.props.type==='range')[0];
  assert.equal(range.props.max,10);assert.equal(range.props['aria-label'],'Prepared audio only');
  assert.equal(walk(tree,n=>n.tag==='button'&&n.props['aria-label']==='Follow').length,0);
  range.props.onChange({target:{value:'3'}});assert.deepEqual(calls,[['a',true,3]]);
});

test('a stale selected segment from another generation cannot enable transport',()=>{
  const generation=g([segment('new')]);
  const {tree}=sourceTree('src/components/PlayerBar.tsx',{}, {component:'PlayerBar',book:{title:'New book',chapters:[]},generation,settings:{},
    player:{segment:{...segment('old'),spoken:'Wrong book text'},position:2,playing:false,waiting:false,error:'',sleepAt:0},status:'Saved',statusAction:null,
    onSelect:()=>{},onUpdate:()=>{},onSettings:()=>{},onHistory:()=>{},onToggle:()=>{},onSkip:()=>{}});
  assert.equal(walk(tree,n=>n.tag==='button'&&n.props['aria-label']==='Play')[0].props.disabled,true);
});

test('timeline arrow keys move five seconds and stay inside the prepared range',()=>{
  assert.equal(timelineKeyTarget('ArrowRight',10,240),15);
  assert.equal(timelineKeyTarget('ArrowUp',10,240),15);
  assert.equal(timelineKeyTarget('ArrowLeft',10,240),5);
  assert.equal(timelineKeyTarget('ArrowDown',2,240),0);
  assert.equal(timelineKeyTarget('ArrowRight',238,240),240);
  assert.equal(timelineKeyTarget('PageUp',10,240),null);
  assert.equal(timelineKeyTarget('ArrowRight',Number.NaN,240),null);
});

test('space toggles only playing audio or a passage with playable saved audio and no error',()=>{
  assert.equal(spaceToggles(true,'',null),true);
  assert.equal(spaceToggles(false,'',segment('s1')),true);
  assert.equal(spaceToggles(false,'',segment('s2','c1',10,false)),false);
  assert.equal(spaceToggles(false,'',{...segment('s3'),audio_hash:null}),false);
  assert.equal(spaceToggles(false,'',{...segment('s4'),duration:0}),false);
  assert.equal(spaceToggles(false,'A saved audio segment is missing or cannot be opened.',segment('s5')),false);
  assert.equal(spaceToggles(false,'',null),false);
});


test('intro is the seekable first chapter prefix, including while speech is pending',()=>{
  const calls=[];
  const generation=g([]);generation.config={chapters:['c1'],opening:{variants:{music:{audio_hash:'brand',duration:8}}}};
  const {tree}=sourceTree('src/components/PlayerBar.tsx',{}, {component:'PlayerBar',book:{id:'b',title:'Book',chapters:[]},
    generation,settings:{language:'en',playback_rate:1},player:{segment:null,position:0,playing:true,waiting:false,error:'',sleepAt:0,
      opening:{key:'brand',position:3,duration:8}},status:'Preparing',statusAction:null,
    onSelect:()=>calls.push('source'),onSeekTrack:(...args)=>calls.push(args),onToggle:()=>calls.push('pause'),
    onSkip:()=>{},onUpdate:()=>{},onSettings:()=>{},onHistory:()=>{}});
  const range=walk(tree,n=>n.tag==='input'&&n.props.type==='range')[0];
  assert.equal(range.props.value,3);assert.equal(range.props.max,8);assert.equal(range.props.disabled,false);
  range.props.onChange({target:{value:'7'}});assert.deepEqual(calls,[['c1',7,true]]);
  const pause=walk(tree,n=>n.tag==='button'&&n.props['aria-label']==='Pause')[0];
  assert.equal(pause.props.disabled,false);pause.props.onClick();assert.deepEqual(calls,[['c1',7,true],'pause']);
  assert.equal(walk(tree,n=>n.tag==='button'&&n.props.children?.includes('Skip intro')).length,0);
  assert.notEqual(range.props['aria-label'],'Dogma intro');
});

test('first chapter has one title and one continuous clock before and after the musical prefix',()=>{
  const generation=g([segment('a'),segment('b'),segment('c','c2')]);
  generation.config={chapters:['c1','c2'],opening:{variants:{music:{audio_hash:'brand',duration:8}}}};
  const before=chapterTrackTimeline(generation,chapterTimeline(generation,generation.segments[0],0),{position:3,duration:8});
  assert.equal(before.elapsed,3);assert.equal(before.duration,28);
  const after=chapterTrackTimeline(generation,chapterTimeline(generation,generation.segments[1],3));
  assert.equal(after.elapsed,21);assert.equal(after.duration,28);
  const later=chapterTrackTimeline(generation,chapterTimeline(generation,generation.segments[2],3));
  assert.equal(later.elapsed,3);assert.equal(later.duration,10);
  const calls=[];
  const {tree}=sourceTree('src/components/PlayerBar.tsx',{}, {component:'PlayerBar',book:{title:'Book',chapters:[{id:'c1',title:'Chapter one'}]},generation,
    settings:{language:'en'},player:{segment:generation.segments[0],position:3,playing:false,waiting:false,sleepAt:0,premixed:true,bufferedSeconds:7},
    status:'Saved',statusAction:null,onSelect:()=>calls.push('source'),onSeekTrack:(...args)=>calls.push(args),onToggle:()=>{},onSkip:()=>{},onUpdate:()=>{},onSettings:()=>{},onHistory:()=>{}});
  const range=walk(tree,n=>n.tag==='input'&&n.props.type==='range')[0];
  assert.equal(range.props.value,11);assert.equal(range.props.max,28);
  range.props.onChange({target:{value:'0'}});assert.deepEqual(calls,[['c1',0,false]]);
  assert.ok(walk(tree,n=>n.tag==='span'&&n.props.title==='Chapter one').length);
  assert.ok(walk(tree,n=>n.tag==='small'&&n.props.children?.includes('Buffered 0:07')).length);
});

test('continuous player distinguishes buffered media from prepared narration',()=>{
  const generation=g([segment('a'),segment('b')]);
  const {tree}=sourceTree('src/components/PlayerBar.tsx',{}, {component:'PlayerBar',book:{title:'Book',chapters:[]},generation,
    settings:{language:'en'},player:{segment:generation.segments[0],position:3,playing:true,waiting:true,error:'',sleepAt:0,premixed:true,bufferedSeconds:7},
    status:'Saved locally',statusAction:null,onSelect:()=>{},onUpdate:()=>{},onSettings:()=>{},onHistory:()=>{},onToggle:()=>{},onSkip:()=>{}});
  assert.ok(walk(tree,n=>n.tag==='span'&&n.props.title==='Buffering audio…').length);
  assert.ok(walk(tree,n=>n.tag==='small'&&n.props.children?.includes('Buffered 0:07')).length);
  const range=walk(tree,n=>n.tag==='input'&&n.props.type==='range')[0];
  assert.equal(range.props.value,3);
  assert.match(range.props.style.background,/\.45\) 50%/);
});
