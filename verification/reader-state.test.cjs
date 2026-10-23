const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
const selectionHelpers=(()=>{const module={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/readerSelection.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(module,module.exports);return module.exports;})();

const filename=path.resolve(__dirname,'../src/App.tsx');
const app=ts.createSourceFile(filename,fs.readFileSync(filename,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
function find(predicate){
  let result;
  const visit=node=>{if(result)return;if(predicate(node)){result=node;return;}ts.forEachChild(node,visit);};
  visit(app);assert.ok(result);return result;
}
function evaluate(node,environment){
  const code=ts.transpileModule('return ('+node.getText(app)+')',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
  return new Function(...Object.keys(environment),code)(...Object.values(environment));
}
function variable(name,environment){return evaluate(find(node=>ts.isVariableDeclaration(node)&&node.name.getText(app)===name).initializer,environment);}
function effect(fragment,environment){return evaluate(find(node=>ts.isCallExpression(node)&&['useEffect','useLayoutEffect'].includes(node.expression.getText(app))&&node.arguments[0]?.getText(app).includes(fragment)).arguments[0],environment);}
function events(extra={}){
  const listeners=new Map();
  return {...extra,addEventListener:(name,callback)=>{const callbacks=listeners.get(name)||new Set();callbacks.add(callback);listeners.set(name,callbacks);},removeEventListener:(name,callback)=>listeners.get(name)?.delete(callback),emit:(name,event={})=>{for(const callback of listeners.get(name)||[])callback(event);}};
}
function viewport(){
  let nextTimer=1;const timers=new Map();
  const module={exports:{}};
  const code=ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/readerNavigation.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
  new Function('module','exports','setTimeout','clearTimeout',code)(module,module.exports,fn=>{const id=nextTimer++;timers.set(id,fn);return id;},id=>timers.delete(id));
  const page=events({matchMedia:()=>({matches:false})});
  const document=events({body:{closest:()=>null},defaultView:page,visibilityState:'visible'});
  const sources=[{dataset:{sourceId:'p1'},getBoundingClientRect:()=>({top:20})},{dataset:{sourceId:'p2'},getBoundingClientRect:()=>({top:200})}];
  const root=events({ownerDocument:document,scrollTop:0,clientHeight:600,getBoundingClientRect:()=>({top:0}),querySelectorAll:()=>sources,closest:()=>null});
  const anchor={scrollIntoView:()=>{},focus:()=>{}};
  root.contains=target=>target===root||target===anchor;
  root.scrollTo=({top})=>{root.scrollTop=top;};
  document.getElementById=()=>anchor;
  const saves=[];let manual=0;
  const binding=module.exports.bindReadingPosition(root,()=>manual++,(sourceId,offset)=>saves.push({sourceId,offset}));
  return {...module.exports,root,page,document,sources,saves,binding,manual:()=>manual,flushTimers:()=>{const pending=[...timers.values()];timers.clear();for(const fn of pending)fn();}};
}

test('preparing the displayed chapter replaces missing and stale chapter choices',()=>{
  const button=find(node=>ts.isJsxElement(node)&&node.openingElement.tagName.getText(app)==='button'&&node.children.some(child=>ts.isJsxExpression(child)&&child.expression?.getText(app)==="t('prepareChapter')"));
  const handler=button.openingElement.attributes.properties.find(item=>item.name?.getText(app)==='onClick').initializer.expression;
  for(const initial of ['', 'previous-chapter']){
    const values={chapter:initial,name:'Previous name',source:'previous-source'};
    const openGenerate=variable('openGenerate',{book:null,defaultNarrationChapters:()=>[],setSelectedChapters:()=>{},setGenerationChapter:id=>{values.chapter=id;},setGenerationName:name=>{values.name=name;},setGenerationSource:id=>{values.source=id;},setScope:value=>{values.scope=value;},setModal:value=>{values.modal=value;}});
    evaluate(handler,{chapter:{id:'current-chapter'},openGenerate})();
    assert.deepEqual(values,{chapter:'current-chapter',name:'',source:'',scope:'chapter',modal:'generate'});
  }
});

test('keyboard reading holds narration before the first scrolling event and saves the viewport',()=>{
  const view=viewport();
  view.page.emit('keydown',{key:'PageDown',target:view.root});
  assert.equal(view.manual(),1);
  view.root.scrollTop=450;view.sources[1].getBoundingClientRect=()=>({top:30});view.root.emit('scroll');
  view.flushTimers();assert.deepEqual(view.saves,[{sourceId:'p2',offset:450}]);
  view.binding.dispose();
});

test('scrollbar movement is saved even when the platform omits pointer events',()=>{
  const view=viewport();view.root.scrollTop=200;view.root.emit('scroll');
  assert.equal(view.manual(),1);view.binding.flush();assert.deepEqual(view.saves,[{sourceId:'p1',offset:200}]);
  view.binding.dispose();
});

test('narration scrolling does not enter browsing mode or overwrite manual reading position',()=>{
  const view=viewport();
  assert.equal(view.scrollToSource(view.root,'p2',false),true);
  view.root.scrollTop=500;view.root.emit('scroll');view.root.emit('scroll');view.root.emit('scrollend');
  view.flushTimers();assert.equal(view.manual(),0);assert.deepEqual(view.saves,[]);
  view.scrollReaderTo(view.root);view.root.emit('scroll');view.flushTimers();
  assert.equal(view.manual(),0);assert.deepEqual(view.saves,[]);view.binding.dispose();
});

test('smooth centering accelerates with distance and stays gentle nearby',()=>{
  const view=viewport();
  view.root.clientWidth=900;
  view.root.getBoundingClientRect=()=>({top:0,width:900,height:600});
  assert.equal(view.autoFollowScrollDuration(view.root,600),3000);
  assert.equal(view.autoFollowScrollDuration(view.root,5400),1000);
  assert.equal(view.autoFollowScrollDuration(view.root,60000),320);
  view.root.clientWidth=360;
  view.root.getBoundingClientRect=()=>({top:0,width:360,height:600});
  assert.equal(view.autoFollowScrollDuration(view.root,600),1200);
  assert.equal(view.autoFollowScrollDuration(view.root,5400),400);
});

test('wheel, touch, and scrollbar input interrupt programmatic scrolling immediately',()=>{
  for(const event of ['wheel','touchstart','pointerdown']){
    const view=viewport();view.scrollToSource(view.root,'p2',false);view.root.emit(event);view.root.scrollTop=300;view.root.emit('scroll');
    assert.equal(view.manual(),2);view.binding.dispose();assert.deepEqual(view.saves,[{sourceId:'p1',offset:300}]);
  }
});

test('leaving the reader flushes its captured source rather than querying replacement content',()=>{
  const view=viewport();view.root.scrollTop=120;view.root.emit('scroll');
  view.sources[0].dataset.sourceId='new-book-source';view.root.scrollTop=0;
  view.binding.dispose();assert.deepEqual(view.saves,[{sourceId:'p1',offset:120}]);
  view.flushTimers();view.root.emit('scroll');assert.equal(view.saves.length,1);
});

test('page close and hiding flush pending reading once before the debounce expires',()=>{
  const view=viewport();view.root.scrollTop=120;view.root.emit('scroll');view.page.emit('pagehide');view.binding.flush();
  assert.deepEqual(view.saves,[{sourceId:'p1',offset:120}]);
  view.root.scrollTop=240;view.root.emit('scroll');view.document.visibilityState='hidden';view.document.emit('visibilitychange');
  assert.deepEqual(view.saves,[{sourceId:'p1',offset:120},{sourceId:'p1',offset:240}]);view.binding.dispose();
});

test('editing controls and playback shortcuts do not pause narration follow',()=>{
  const view=viewport();
  for(const key of ['PageDown','ArrowDown'])view.page.emit('keydown',{key,target:{closest:()=>({}),parentElement:view.root}});
  view.page.emit('keydown',{key:' ',target:view.root});view.page.emit('keydown',{key:'ArrowRight',target:view.root});
  view.page.emit('keydown',{key:'PageDown',target:view.root,defaultPrevented:true});
  assert.equal(view.manual(),0);view.binding.dispose();
});

test('selecting a chapter flushes the previous scroll before writing the new chapter',()=>{
  const writes=[];const manualReading={current:false};
  variable('selectChapter',{flushReading:{current:()=>writes.push('old-scroll')},manualReading,setNavigation:()=>{},setBrowsing:()=>{},setChapterId:()=>{},setSelectedSource:()=>{},scrollReaderTo:()=>{},viewRef:{current:{}},book:{id:'book'},focusIfLost:()=>{},storeReading:variable('storeReading',{setBook:()=>{},api:async(op,data)=>writes.push(data.position.chapter_id),setError:()=>{},t:()=>'',errorText:String})})('next');
  assert.deepEqual(writes,['old-scroll','next']);assert.equal(manualReading.current,true);
});

test('reopening with saved narration returns to the last listened block instead of an older reading location',async()=>{
  const book={id:'book',chapters:[{id:'audio-chapter',blocks:[{id:'audio-source'}]},{id:'reading-chapter',blocks:[]}],generations:[{id:'generation'}],position:{generation_id:'generation',chapter_id:'audio-chapter',source_id:'audio-source',offset:12},reading_position:{chapter_id:'reading-chapter',offset:0}};
  const generation={id:'generation',book_id:'book'};const manualReading={current:false};let browsing=false,chapter,navigation;
  const open=variable('open',{defaultGeneration:selectionHelpers.defaultGeneration,flushReading:{current:()=>{}},selectionEpoch:{current:0},restoringPlayback:{current:''},afterPlaybackSaved:async(stop,read)=>{await stop();return read();},player:{stop:async()=>{}},api:async op=>op==='book'?book:generation,manualReading,
    setModal:()=>{},setNavigation:value=>{navigation=value;},setGeneration:()=>{},setSelectedSource:()=>{},setGenerationSource:()=>{},setPlayFrom:()=>{},setChapterQuery:()=>{},setNavTab:()=>{},setBrowsing:value=>{browsing=value;},setBook:()=>{},setSelectedChapters:()=>{},defaultNarrationChapters:()=>[],setChapterId:value=>{chapter=value;},setQuery:()=>{},focusIfLost:()=>{}});
  await open('book');assert.equal(chapter,'audio-chapter');assert.equal(browsing,false);assert.equal(manualReading.current,false);assert.deepEqual(navigation,{bookId:'book',sourceId:'audio-source',focus:false,playback:true});
  effect('setChapterId(player.segment.chapter_id)',{player:{segment:{id:'segment',source_id:'audio-source',chapter_id:'audio-chapter'}},settings:{follow:true},browsing,manualReading,generation,book,segments:{byId:new Map()},setChapterId:value=>{chapter=value;}})();
  assert.equal(chapter,'audio-chapter');
});

test('manual browsing returns to narration after three minutes without another reader move',()=>{
  let callback,delay,browsing=true,selected='manual';const manualReading={current:true},followReturnTimer={current:null};
  const cancelFollowingReturn=variable('cancelFollowingReturn',{followReturnTimer,clearTimeout:()=>{}});
  const scheduleFollowingReturn=variable('scheduleFollowingReturn',{cancelFollowingReturn,followReturnTimer,manualReading,
    setBrowsing:value=>{browsing=value;},setSelectedSource:value=>{selected=value;},FOLLOW_RETURN_DELAY:180000,
    setTimeout:(fn,ms)=>{callback=fn;delay=ms;return 1;}});
  scheduleFollowingReturn();assert.equal(delay,180000);callback();assert.equal(manualReading.current,false);assert.equal(browsing,false);assert.equal(selected,'');
});

test('manual input blocks a pending narration update before browsing state rerenders',()=>{
  let moves=0;
  effect('setChapterId(player.segment.chapter_id)',{player:{segment:{id:'segment',source_id:'source',chapter_id:'audio'}},settings:{follow:true},browsing:false,manualReading:{current:true},setChapterId:()=>moves++})();
  assert.equal(moves,0);
});

test('navigation or a restored reading position at the hidden first heading scrolls to the chapter start',()=>{
  const indexes={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/readerIndexes.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(indexes,indexes.exports);
  const navigationModule={exports:{}};new Function('module','exports','setTimeout','clearTimeout',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/readerNavigation.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(navigationModule,navigationModule.exports,()=>0,()=>{});
  const chapter={id:'chapter',blocks:[{id:'heading',kind:'heading'},{id:'paragraph',kind:'paragraph'}]};
  const calls=[];let cleared=0;
  const run=(sourceId,saved={},focus=true)=>effect('scrollToChapterStart',{navigation:{bookId:'book',sourceId,focus,...saved},book:{id:'book'},chapter,viewRef:{current:'root'},titleSource:indexes.exports.titleSource,
    scrollToChapterStart:(...args)=>{calls.push(['start',...args]);return true;},scrollToSource:(...args)=>{calls.push(['source',...args]);return true;},
    scrollToPlaybackPosition:(...args)=>{calls.push(['playback',...args]);return true;},scrollToReadingPosition:(...args)=>{calls.push(['position',...args]);return true;},scrollReaderTo:()=>true,setNavigation:()=>{cleared++;}})();
  run('heading');run('paragraph');
  run('heading',{offset:0},false);run('heading',{offset:0,sourceFraction:.5},false);run('heading',{sourceFraction:0},false);
  run('paragraph',{offset:0,sourceFraction:.25},false);
  run('paragraph',{sourceFraction:.4,playback:true,smooth:true},false);
  assert.deepEqual(calls,[['start','root','heading',true],['source','root','paragraph',true],
    ['start','root','heading',false],['start','root','heading',false],['start','root','heading',false],
    ['position','root','paragraph',0,.25],['playback','root','paragraph',.4,true]]);
  assert.equal(cleared,7);
  const focused=[];const element={focus:options=>focused.push(options)};const scrolls=[];
  const root={ownerDocument:{getElementById:id=>id==='heading'?element:null},contains:node=>node===element,scrollTo:value=>scrolls.push(value)};
  assert.equal(navigationModule.exports.scrollToChapterStart(root,'heading',true),true);
  assert.deepEqual(scrolls,[{top:0,behavior:'instant'}]);assert.deepEqual(focused,[{preventScroll:true}]);
  assert.equal(navigationModule.exports.scrollToChapterStart(root,'missing',true),false);
});

test('pressing a passage control keeps narration following while pressing text holds it',()=>{
  const view=viewport();
  const control={closest:selector=>selector.includes('button')?{}:null};
  view.root.emit('pointerdown',{target:control});view.root.emit('touchstart',{target:control});
  assert.equal(view.manual(),0);
  view.root.emit('pointerdown',{target:{closest:()=>null}});
  assert.equal(view.manual(),1);
  view.binding.dispose();
});
