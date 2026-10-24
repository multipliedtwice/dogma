const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
const selectionHelpers=(()=>{const module={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/readerSelection.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(module,module.exports);return module.exports;})();
const i18nModule={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/i18n.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(i18nModule,i18nModule.exports);
const t=i18nModule.exports.translator('en');

function code(file) {
  const filename=path.resolve(__dirname,'../src',file);
  const text=fs.readFileSync(filename,'utf8');
  return ts.createSourceFile(filename,text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
}
function find(tree,predicate) {
  let result;
  const visit=node=>{if(result)return;if(predicate(node)){result=node;return;}ts.forEachChild(node,visit);};
  visit(tree);assert.ok(result,'Source node is present');return result;
}
function evaluate(tree,node,environment) {
  environment={t,settings:null,flushReading:{current:()=>{}},manualReading:{current:false},chapterStartIntent:{current:''},scrollReaderTo:root=>root?.scrollTo({top:0}),...environment};
  const js=ts.transpileModule('const result = '+node.getText(tree)+';', {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
  return new Function(...Object.keys(environment),js+'\nreturn result;')(...Object.values(environment));
}
function variable(tree,name,environment) {
  return evaluate(tree,find(tree,n=>ts.isVariableDeclaration(n)&&n.name.getText(tree)===name).initializer,environment);
}
const app=code('App.tsx');
const panel=code('components/IllustrationPanel.tsx');
const nextTurn=()=>new Promise(resolve=>setImmediate(resolve));

test('illustration target survives playback crossing to a different chapter',()=>{
  const target={id:'old-paragraph',kind:'paragraph',text:'Intended paragraph.'};
  const current={id:'new-paragraph',kind:'paragraph',text:'Next chapter paragraph.'};
  const env={chapter:{id:'c2',blocks:[current]},selectedSource:target.id,modal:'illustration',
    readerIndex:{sources:new Map([[target.id,{block:target}],[current.id,{block:current}]])},player:{segment:{source_id:current.id}}};
  assert.equal(variable(app,'selectedBlock',env),target);
});

test('ordinary reading still resolves selected text inside the visible chapter',()=>{
  const target={id:'old-paragraph',kind:'paragraph'};
  const current={id:'new-paragraph',kind:'paragraph'};
  const env={chapter:{id:'c2',blocks:[current]},selectedSource:target.id,modal:null,
    readerIndex:{sources:new Map([[target.id,{block:target}],[current.id,{block:current}]])},player:{segment:{source_id:current.id}}};
  assert.equal(variable(app,'selectedBlock',env),current);
});

test('newer illustration history cannot be replaced by an earlier empty response',async()=>{
  const requests=[];let runs=[];
  const refresh=variable(panel,'refresh',{book:{id:'book'},useCallback:fn=>fn,
    refreshRevision:{current:0},mounted:{current:true},
    api:op=>new Promise(resolve=>requests.push({op,resolve})),setRuns:value=>{runs=value;},onVisuals:()=>{}});
  const first=refresh();const newer=refresh();
  requests[1].resolve([{id:'new-run',status:'running'}]);
  await newer;
  requests[0].resolve([]);
  await first;
  assert.deepEqual(runs,[{id:'new-run',status:'running'}]);
});

test('chapter-only generation retains the chapter chosen before playback advances',async()=>{
  const old={id:'c1',title:'Original chapter'},next={id:'c2',title:'Playing next'};
  const sent=[];const book={id:'book',chapters:[old,next]};
  const start=variable(app,'startGeneration',{
    run:(_label,fn)=>fn(),book,selectionEpoch:{current:1},modalRevision:{current:1},
    scope:'chapter',chapter:next,generationChapter:old.id,generationName:'Saved choice',generationSource:'',selectedChapters:[],
    narrationSelection:(_book,scope,id)=>[id],api:async(op,data)=>{sent.push({op,data});return {id:'new-generation'};},
    setGeneration:()=>{},setPlayFrom:()=>{},setGenerationSource:()=>{},setGenerationName:()=>{},
    closeModal:()=>{},refreshBook:async()=>{},refreshLibrary:async()=>{}
  });
  await start();
  assert.deepEqual(sent[0].data.chapters,['c1']);
});

test('read-only source navigation saves the exact paragraph for reopening',async()=>{
  const target={id:'p2',kind:'paragraph'};const chapter={id:'c2',blocks:[target]};
  let book={id:'book',chapters:[chapter],position:null,reading_position:null};const writes=[];
  const navigate=variable(app,'navigateSource',{useCallback:fn=>fn,book,generation:null,manualReading:{current:false},
    readerIndex:{sources:new Map([['p2',{chapter,block:target}]])},setBrowsing:()=>{},setMode:()=>{},setChapterId:()=>{},setSelectedSource:()=>{},setNavigation:()=>{},
    storeReading:variable(app,'storeReading',{setBook:fn=>{book=fn(book);},api:async(op,data)=>{writes.push({op,data});}})});
  navigate('p2');await nextTurn();
  assert.deepEqual(writes,[{op:'save_reading_position',data:{book_id:'book',position:{chapter_id:'c2',source_id:'p2',offset:0}}}]);
  assert.equal(book.reading_position.source_id,'p2');
});

test('reopening a read-only book schedules navigation to its saved source anchor',async()=>{
  const book={id:'book',chapters:[{id:'c2',blocks:[{id:'p2'}]}],generations:[],position:{chapter_id:'c2',source_id:'p2',offset:0}};
  let navigation=null,selected='';const chapterIds=[];
  const open=variable(app,'open',{defaultGeneration:selectionHelpers.defaultGeneration,selectionEpoch:{current:1},restoringPlayback:{current:''},manualReading:{current:true},afterPlaybackSaved:async(stop,read)=>{await stop();return read();},player:{stop:async()=>{}},api:async()=>book,
    setModal:()=>{},setNavigation:value=>{navigation=value;},setGeneration:()=>{},setSelectedSource:value=>{selected=value;},setGenerationSource:()=>{},setPlayFrom:()=>{},setChapterQuery:()=>{},setNavTab:()=>{},setBrowsing:()=>{},
    setBook:()=>{},setSelectedChapters:()=>{},defaultNarrationChapters:()=>['c2'],setChapterId:id=>chapterIds.push(id),setQuery:()=>{},
    sourceChapter:(value,id)=>value.chapters.find(chapter=>chapter.blocks.some(block=>block.id===id)),focusIfLost:()=>{}});
  await open(book.id);
  assert.deepEqual(navigation,{bookId:'book',sourceId:'p2',focus:false,offset:0,sourceFraction:undefined});assert.equal(selected,'p2');assert.equal(chapterIds.at(-1),'c2');
});

test('navigation while audio is selected does not overwrite the listening position',async()=>{
  const chapter={id:'c2'};let writes=0;const listening={generation_id:'narration',chapter_id:'c1',source_id:'p1',offset:3};let book={id:'book',position:listening,reading_position:null};
  variable(app,'navigateSource',{useCallback:fn=>fn,book,generation:{id:'narration'},manualReading:{current:false},
    readerIndex:{sources:new Map([['p2',{chapter,block:{id:'p2'}}]])},setBrowsing:()=>{},setMode:()=>{},setChapterId:()=>{},setSelectedSource:()=>{},setNavigation:()=>{},
    storeReading:variable(app,'storeReading',{setBook:update=>{book=update(book);},api:async(op)=>{assert.equal(op,'save_reading_position');writes++;}})})('p2');
  await nextTurn();assert.equal(writes,1);assert.equal(book.position,listening);assert.equal(book.reading_position.source_id,'p2');
});

test('closed illustration panels ignore late successful history responses',async()=>{
  const replies=[];let updated=0;const mounted={current:true};
  const refresh=variable(panel,'refresh',{book:{id:'book'},useCallback:fn=>fn,refreshRevision:{current:0},mounted,
    api:()=>new Promise(resolve=>replies.push(resolve)),setRuns:()=>updated++,onVisuals:()=>updated++});
  const task=refresh();mounted.current=false;replies[0]([]);await task;assert.equal(updated,0);
});

test('a superseded history error does not override a successful newer refresh',async()=>{
  const replies=[];let runs=[];
  const refresh=variable(panel,'refresh',{book:{id:'book'},useCallback:fn=>fn,refreshRevision:{current:0},mounted:{current:true},
    api:()=>new Promise((resolve,reject)=>replies.push({resolve,reject})),setRuns:value=>{runs=value;},onVisuals:()=>{}});
  const old=refresh(),current=refresh();replies[1].resolve([{id:'current'}]);await current;
  replies[0].reject(new Error('Obsolete failure'));await assert.doesNotReject(old);assert.equal(runs[0].id,'current');
});

test('the current history failure is still reported',async()=>{
  const refresh=variable(panel,'refresh',{book:{id:'book'},useCallback:fn=>fn,refreshRevision:{current:0},mounted:{current:true},
    api:async()=>{throw new Error('Current service failure');},setRuns:()=>{},onVisuals:()=>{}});
  await assert.rejects(refresh(),/Current service failure/);
});

const {sourceTree}=require('./layout_source.cjs');
const walk=(value,predicate)=>Array.isArray(value)?value.flatMap(item=>walk(item,predicate)):!value||typeof value!=='object'?[]:[...(predicate(value)?[value]:[]),...walk(value.props?.children,predicate)];
const text=value=>Array.isArray(value)?value.map(text).join(''):value&&typeof value==='object'?text(value.props?.children):String(value??'');
const savedRun={id:'older-run',book_id:'book',status:'paused',created:'2026-09-23T10:00:00Z',style:'ink',error:'',items:[{source_id:'old-paragraph',status:'pending',error:'',visual_id:null}],resume:{remaining:1,retry_failed:false,local_only:false}};
function panelTree(run,states={},enabled=true){
  return sourceTree('src/components/IllustrationPanel.tsx',{states:{runs:[run],...states}},{component:'IllustrationPanel',book:{id:'book',chapters:[{id:'chapter',blocks:[{id:'current-paragraph',kind:'paragraph',text:'Current selection'}]}],visuals:[]},sourceId:'current-paragraph',enabled,onVisuals:()=>{},onNavigate:()=>{},onSettings:()=>{}}).tree;
}

test('new-selection consent cannot authorize an older saved illustration run',()=>{
  const tree=panelTree(savedRun,{consent:true});
  const button=walk(tree,node=>node.tag==='button'&&text(node)==='Resume saved run')[0];assert.ok(button);assert.equal(button.props.disabled,true);
  assert.match(text(tree),/unfinished paragraph from this saved ink run/);
  assert.match(text(tree),/not the selection above/);
});

test('saved-run approval is specific and is disabled when online permission is off',()=>{
  for(const [enabled,resumeConsent,disabled] of [[true,'older-run',false],[true,'different-run',true],[false,'older-run',true]]){
    const tree=panelTree(savedRun,{resumeConsent},enabled);
    assert.equal(walk(tree,node=>node.tag==='button'&&text(node)==='Resume saved run')[0].props.disabled,disabled);
  }
});

test('locally finalizable runs expose an offline action without a permission checkbox',()=>{
  const tree=panelTree({...savedRun,items:[{...savedRun.items[0],status:'ready'}],resume:{remaining:0,retry_failed:false,local_only:true}},{consent:false,resumeConsent:null},false);
  const button=walk(tree,node=>node.tag==='button'&&text(node)==='Finish saved run')[0];assert.ok(button);assert.equal(button.props.disabled,false);
  assert.match(text(tree),/no text will be sent and no images regenerated/);
  assert.doesNotMatch(text(tree),/Allow the 0 unfinished/);
});

test('resume dispatch uses the saved run identity and explicit retry decision',async()=>{
  const button=find(panel,node=>ts.isJsxElement(node)&&node.openingElement.tagName.getText(panel)==='Button'&&node.getText(panel).includes("api('illustration_resume'"));
  const attribute=button.openingElement.attributes.properties.find(item=>item.name?.getText(panel)==='onClick');
  for(const localOnly of [false,true]){
    const requests=[],cleared=[];
    const click=evaluate(panel,attribute.initializer.expression,{run:savedRun,resume:{localOnly,retryFailed:!localOnly},action:fn=>fn(),setResumeConsent:value=>cleared.push(value),api:async(op,data)=>requests.push({op,data})});
    click();await nextTurn();assert.deepEqual(requests,[{op:'illustration_resume',data:{id:'older-run',consent:!localOnly,retry_failed:!localOnly}}]);assert.deepEqual(cleared,[null]);
  }
});

test('a stale playback snapshot cannot move a newly opened read-only book',()=>{
  const effect=find(app,node=>ts.isCallExpression(node)&&node.expression.getText(app)==='useEffect'&&node.arguments[0]?.getText(app).includes('setChapterId(player.segment.chapter_id)'));
  const moves=[];
  const follow=evaluate(app,effect.arguments[0],{book:{id:'new-book'},generation:null,segments:{byId:new Map()},mode:'read',browsing:false,settings:{follow:true},
    player:{segment:{id:'old-segment',source_id:'old-source',chapter_id:'old-chapter'}},setChapterId:value=>moves.push(value),setNavigation:value=>moves.push(value)});
  follow();assert.deepEqual(moves,[]);
});

test('matching saved narration still follows its current paragraph',()=>{
  const effect=find(app,node=>ts.isCallExpression(node)&&node.expression.getText(app)==='useEffect'&&node.arguments[0]?.getText(app).includes('setChapterId(player.segment.chapter_id)'));
  const moves=[];const segment={id:'segment',source_id:'source',chapter_id:'chapter',start:10,end:20,duration:4};
  evaluate(app,effect.arguments[0],{book:{id:'book'},generation:{book_id:'book'},segments:{byId:new Map([['segment',segment]])},mode:'read',browsing:false,settings:{follow:true},
    player:{segment,position:2},restoringPlayback:{current:''},readerIndex:{sources:new Map([['source',{block:{text:'x'.repeat(40)}}]])},setChapterId:value=>moves.push(value),setNavigation:value=>moves.push(value),storeReading:(_book,position)=>moves.push(position)})();
  assert.deepEqual(moves,['chapter',{bookId:'book',sourceId:'source',focus:false,playback:true,smooth:true,offset:0,sourceFraction:.375},{chapter_id:'chapter',source_id:'source',offset:0}]);
});
