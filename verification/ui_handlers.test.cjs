const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const filename = path.resolve(__dirname, '../src/App.tsx');
const source = fs.readFileSync(filename, 'utf8');
const revisionModule={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/readerIndexes.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(revisionModule,revisionModule.exports);
const {preserveVisualRevision}=revisionModule.exports;
const tree = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const i18nModule={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/i18n.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(i18nModule,i18nModule.exports);
const t=i18nModule.exports.translator('en');
const selectionModule={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/readerSelection.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(selectionModule,selectionModule.exports);
const {defaultGeneration,repairOutcome}=selectionModule.exports;
const stressModule={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/stressSelection.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(stressModule,stressModule.exports);
const {stressSelection,stressVariants}=stressModule.exports;

test('stress feedback offers every vowel placement for one Russian word',()=>{
  assert.deepEqual(stressVariants('\u043f\u043b\u043e\u0442\u0438'),['\u043f\u043b\u043e\u0301\u0442\u0438','\u043f\u043b\u043e\u0442\u0438\u0301']);
  assert.deepEqual(stressVariants('\u0451\u043b\u043a\u0430'),['\u0451\u0301\u043b\u043a\u0430','\u0451\u043b\u043a\u0430\u0301'],'Evidence collection exposes every vowel placement literally');
  assert.deepEqual(stressVariants('\u043a\u043e\u0433\u0434\u0430 \u0442\u043e'),[],'A stress report is bound to exactly one selected word');
  assert.deepEqual(stressVariants('\u043a\u043e\u0433\u0434\u0430\u0301'),['\u043a\u043e\u0301\u0433\u0434\u0430','\u043a\u043e\u0433\u0434\u0430\u0301'],'An existing mark is replaced rather than duplicated');
});

test('Save accepts a normal stress correction as well as a permitted respelling',()=>{
  const {stressCorrection}=stressModule.exports;
  assert.equal(stressCorrection('деревушки','дереву\u0301шки'),'дереву\u0301шки');
  assert.equal(stressCorrection('деревушки','деревушки'),null);
  assert.equal(stressCorrection('деревушки','дереву\u0301шка'),null);
  assert.equal(stressCorrection('деревушки','де\u0301реву\u0301шки'),null);
  assert.equal(stressCorrection('Моес','Мо\u0301эс'),'Мо\u0301эс');
});

test('narration feedback offsets count Unicode code points',()=>{
  const markup={contains:()=>true};
  const section={dataset:{sourceId:'p'},contains:()=>true,querySelector:()=>markup};
  const before={selectNodeContents:()=>{},setEnd:()=>{},toString:()=> '😀 '};
  const range={startContainer:{},endContainer:{},startOffset:0,cloneRange:()=>before};
  const selection={isCollapsed:false,rangeCount:1,getRangeAt:()=>range,toString:()=> 'слово'};
  assert.deepEqual(stressSelection(section,'😀 слово',selection),{sourceId:'p',text:'слово',textOffset:2});
});

function mockStressSelection({before,selected,rendered,source}){
  const markup={contains:()=>true,textContent:rendered};
  const section={dataset:{sourceId:'p'},contains:()=>true,querySelector:()=>markup};
  const prefix={selectNodeContents:()=>{},setEnd:()=>{},toString:()=>before};
  const range={startContainer:{},endContainer:{},startOffset:0,cloneRange:()=>prefix};
  const selection={isCollapsed:false,rangeCount:1,getRangeAt:()=>range,toString:()=>selected};
  return stressSelection(section,source,selection);
}

test('narration feedback maps NBSP and rendered line breaks to exact source text',()=>{
  assert.deepEqual(mockStressSelection({
    before:'Первая\u00a0строка\n',selected:'вторая\u00a0строка',
    rendered:'Первая\u00a0строка\nвторая\u00a0строка',source:'Первая строка вторая строка',
  }),{sourceId:'p',text:'вторая строка',textOffset:14});
});

test('narration feedback maps past a DOM-only footnote marker',()=>{
  assert.deepEqual(mockStressSelection({
    before:'Первое1 ',selected:'слово',rendered:'Первое1 слово. Второе слово.',
    source:'Первое слово. Второе слово.',
  }),{sourceId:'p',text:'слово',textOffset:7});
  assert.deepEqual(mockStressSelection({
    before:'',selected:'Первое1 слово',rendered:'Первое1 слово.',source:'Первое слово.',
  }),{sourceId:'p',text:'Первое слово',textOffset:0});
});

test('narration feedback ignores soft hyphens and zero width EPUB hints',()=>{
  assert.deepEqual(mockStressSelection({
    before:'',selected:'пере\u00adход',rendered:'пере\u00adход через мост',
    source:'переход через мост',
  }),{sourceId:'p',text:'переход',textOffset:0});
  assert.deepEqual(mockStressSelection({
    before:'пере\u200bход ',selected:'через',rendered:'пере\u200bход через мост',
    source:'переход через мост',
  }),{sourceId:'p',text:'через',textOffset:8});
});

function find(predicate) {
  let found;
  function visit(node) {
    if (found) return;
    if (predicate(node)) { found = node; return; }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  assert.ok(found, 'Expected source callback is present');
  return found;
}

function compile(node, environment) {
  environment={t,settings:null,generation:null,chapterStartIntent:{current:''},flushReading:{current:()=>{}},manualReading:{current:false},scrollReaderTo:root=>root?.scrollTo({top:0}),...environment};
  const js = ts.transpileModule(`const callback = ${node.getText(tree)};`, {
    compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS},
  }).outputText;
  return new Function(...Object.keys(environment), js + '\nreturn callback;')(...Object.values(environment));
}

function callback(name, environment) {
  return compile(find(node => ts.isVariableDeclaration(node) && node.name.getText(tree) === name).initializer, environment);
}

test('feedback save stays bound to the generation selected when its context menu opened',async()=>{
  const requests=[],pending=[];
  const save=callback('saveNarrationFeedback',{ru:false,run:(_label,action)=>{pending.push(action());},player:{segment:{id:'segment-1',audio_hash:'heard-hash'},position:12.5},
    api:(operation,payload)=>{requests.push([operation,payload]);return Promise.resolve({});},correctionAdoption:{current:{watch:()=>{},receive:async()=>{}}},feedbackServicesRef:{current:{selection:()=>({})}},setStressMenu:()=>{},setStressMenuComment:()=>{},setModal:()=>{},setFeedbackSelection:()=>{},setFeedbackComment:()=>{},setNotice:()=>{}});
  save({sourceId:'source-1',text:'плоти',textOffset:7,segmentId:'segment-1',generationId:'heard-generation',audioHash:'heard-hash',audioRevision:4},'stress','','плоти́');
  await Promise.all(pending);
  assert.equal(requests[0][0],'report_stress');
  assert.equal(requests[0][1].generation_id,'heard-generation');
  assert.equal(requests[0][1].audio_offset,12.5);
  assert.equal(requests[0][1].audio_hash,'heard-hash');assert.equal(requests[0][1].audio_revision,4);
  assert.equal(requests[0][1].intended_stress,'плоти́');
});

test('feedback menu retains the actually heard hash after the same audiobook part is replaced',async()=>{
  const requests=[],pending=[];
  const save=callback('saveNarrationFeedback',{ru:false,run:(_label,action)=>{pending.push(action());},player:{segment:{id:'part',audio_hash:'new-current'},position:3},
    api:(operation,payload)=>{requests.push(payload);return Promise.resolve({});},correctionAdoption:{current:{watch:()=>{},receive:async()=>{}}},feedbackServicesRef:{current:{selection:()=>({})}},setStressMenu:()=>{},setStressMenuComment:()=>{},setNotice:()=>{}});
  save({sourceId:'source',text:'word',textOffset:0,segmentId:'part',generationId:'same-current',audioHash:'actual-old-heard',audioRevision:2},'stress','','wórd');
  await Promise.all(pending);assert.equal(requests[0].audio_hash,'actual-old-heard');assert.equal(requests[0].audio_revision,2);
  assert.equal(requests[0].audio_offset,null,'The new clip position cannot be attached to the older heard take');
});

test('opening a book prefers its current audiobook over a stale generation URL or saved position',()=>{
  assert.equal(defaultGeneration({current_generation_id:'current',position:{generation_id:'old'},generations:[{id:'old'},{id:'current'}]}).id,'current');
});

function pauseClick(environment) {
  const button = find(node => ts.isJsxElement(node) && node.openingElement.tagName.getText(tree) === 'button'
    && node.children.some(child => child.getText(tree).includes('Pause preparation')));
  const attribute = button.openingElement.attributes.properties.find(item => item.name?.getText(tree) === 'onClick');
  return compile(attribute.initializer.expression, environment);
}

test('chapter navigation explicitly holds the reading location independently from playback', () => {
  const calls=[];
  const select=callback('selectChapter', {manualReading:{current:false},setBrowsing:value=>calls.push(['browsing',value]), setChapterId:value=>calls.push(['chapter',value]),
    setSelectedSource:value=>calls.push(['source',value]), setNavigation:()=>{}, viewRef:{current:{scrollTo:()=>{}}},book:null,focusIfLost:()=>{}});
  select('chapter-3');
  assert.deepEqual(calls,[['browsing',true],['chapter','chapter-3'],['source','']]);
});

test('follow-narration effect cannot override an explicitly browsed chapter', () => {
  const effect = find(node => ts.isCallExpression(node) && node.expression.getText(tree) === 'useEffect'
    && node.arguments[0]?.getText(tree).includes('setChapterId(player.segment.chapter_id)'));
  let moves=0;
  const follow=compile(effect.arguments[0], {player:{segment:{chapter_id:'playing',source_id:'source'}},settings:{follow:true},browsing:true,
    setChapterId:()=>moves++,setTimeout:()=>{moves++;return 1;},clearTimeout:()=>{}});
  follow();
  assert.equal(moves,0);
});

test('explicit listening leaves browsing mode and selects the requested source audio', () => {
  const calls=[];
  callback('listenTo',{manualReading:{current:true},setBrowsing:value=>calls.push(['browsing',value]),setSelectedSource:value=>calls.push(['source',value]),
    player:{select:(id,play)=>calls.push(['select',id,play])}})('segment-3');
  assert.deepEqual(calls,[['browsing',false],['source',''],['select','segment-3',true]]);
});

test('main Play starts the chapter selected in the reader instead of resuming another chapter', () => {
  const calls=[];
  const toggle=callback('togglePlayback',{
    browsing:true,chapter:{id:'chapter-3'},chapterSegments:[
      {id:'pending',chapter_id:'chapter-3',status:'pending',audio_hash:'',duration:0},
      {id:'chapter-3-start',chapter_id:'chapter-3',status:'ready',audio_hash:'audio',duration:2},
    ],
    playable:segment=>segment.status==='ready'&&Boolean(segment.audio_hash)&&segment.duration>0,
    player:{playing:false,waiting:false,segment:{id:'old',chapter_id:'chapter-1'},seekChapterTrack:(...args)=>calls.push(['track',...args]),toggle:()=>calls.push(['toggle'])},
    resumeFollowing:()=>calls.push(['follow']),setNotice:value=>calls.push(['notice',value]),
  });
  toggle();
  assert.deepEqual(calls,[['follow'],['track','chapter-3',0,true]]);
});

test('selecting the same chapter then Play starts track zero; scrolling alone resumes',()=>{
  const calls=[],intent={current:''};
  const select=callback('selectChapter',{chapterStartIntent:intent,manualReading:{current:false},setBrowsing:()=>{},setChapterId:()=>{},setSelectedSource:()=>{},setNavigation:()=>{},viewRef:{current:null},book:null,focusIfLost:()=>{}});
  select('c1');assert.equal(intent.current,'c1');
  const environment={chapterStartIntent:intent,browsing:true,chapter:{id:'c1'},chapterSegments:[{status:'ready'}],playable:()=>true,
    player:{playing:false,waiting:false,segment:{chapter_id:'c1'},seekChapterTrack:(...args)=>calls.push(['track',...args]),toggle:()=>calls.push(['resume'])},
    resumeFollowing:()=>{intent.current='';},setNotice:()=>{}};
  callback('togglePlayback',environment)();assert.deepEqual(calls,[['track','c1',0,true]]);
  callback('togglePlayback',environment)();assert.deepEqual(calls,[['track','c1',0,true],['resume']]);
});

test('first chapter Play starts its verified intro while every source is pending',()=>{
  const calls=[];
  callback('togglePlayback',{chapterStartIntent:{current:'c1'},browsing:true,chapter:{id:'c1'},chapterSegments:[],playable:()=>false,
    generation:{segments:[{chapter_id:'c1'}],config:{opening:{variants:{music:{audio_hash:'brand',duration:8}}}}},
    player:{playing:false,waiting:false,segment:null,seekChapterTrack:(...args)=>calls.push(args),toggle:()=>calls.push('toggle')},
    resumeFollowing:()=>{},setNotice:()=>calls.push('missing')})();
  assert.deepEqual(calls,[['c1',0,true]]);
});

test('main Play never resumes another chapter when the selected chapter has no playable audio', () => {
  const calls=[];
  const toggle=callback('togglePlayback',{
    browsing:true,chapter:{id:'chapter-3'},chapterSegments:[],playable:()=>false,
    player:{playing:false,waiting:false,segment:{id:'old',chapter_id:'chapter-1'},select:()=>calls.push('select'),toggle:()=>calls.push('toggle')},
    resumeFollowing:()=>calls.push('follow'),setNotice:value=>calls.push(value),
  });
  toggle();
  assert.deepEqual(calls,['The selected generation does not include this chapter. Open another generation or prepare this chapter.']);
});

test('Pause preparation bypasses unrelated busy actions and suppresses duplicate dispatch until inactive', async () => {
  const module={exports:{}};
  new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/preparationPause.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(module,module.exports);
  let requested=0, release, confirm;
  const status=[];
  const pauseRequest=module.exports.createPreparationPause({
    pause:async()=>{requested++;await new Promise(resolve=>release=resolve);},
    refresh:()=>new Promise(resolve=>confirm=resolve), pending:value=>status.push(value), refreshError:()=>{}
  });
  try {
    const pausePreparation=callback('pausePreparation',{pauseRequest,setNotice:()=>{},setError:()=>{},errorText:String});
    const run=callback('run',{runningAction:{current:true},setBusy:()=>{},setError:()=>{},errorText:String,operationMessage:String,language:'en'});
    const click=pauseClick({run,pausePreparation,generation:{id:'generation'}});
    click();click();assert.equal(requested,1);assert.deepEqual(status,['generation']);
    release();await new Promise(resolve=>setImmediate(resolve));
    click();assert.equal(requested,1);assert.deepEqual(status,['generation']);
    confirm({generation:{id:'generation',status:'paused'},health:{active:null}});
    await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(status,['generation',null]);
  } finally {pauseRequest.dispose();}
});

test('completion of an older modal operation does not close a newer panel', () => {
  const closed=[];const modalRevision={current:3};
  const close=callback('closeModal',{modalRevision,setModal:value=>closed.push(value)});
  close(2);assert.deepEqual(closed,[]);
  close(3);assert.deepEqual(closed,[null]);
});

test('late import completion does not navigate away from the user’s newer selection', async () => {
  let release;const epoch={current:1};const opened=[];
  const importFiles=callback('importFiles',{run:(_label,action)=>action(),selectionEpoch:epoch,
    runningAction:{current:false},setError:()=>{},library:[],language:'en',operationMessage:()=>'',importNotice:()=>'',importBooks:async(files,upload)=>({imported:[await upload(files[0])],errors:[]}),uploadBook:()=>new Promise(resolve=>release=resolve),refreshLibrary:async()=>{},open:async id=>opened.push(id),setNotice:()=>{}});
  importFiles([{}]);epoch.current=2;release({id:'imported'});
  await new Promise(resolve=>setImmediate(resolve));assert.deepEqual(opened,[]);
});

test('older library response cannot restore removed library entries', async () => {
  let current = [], replies = [];
  const refresh = callback('refreshLibrary', {useCallback: fn => fn, libraryRefresh: {current: 0},
    api: () => new Promise(resolve => replies.push(resolve)), setLibrary: value => { current = value; }});
  const old = refresh();
  const recent = refresh();
  replies[1]([{id: 'remaining'}]);
  await recent;
  replies[0]([{id: 'removed'}, {id: 'remaining'}]);
  await old;
  assert.deepEqual(current, [{id: 'remaining'}]);
});

test('older book refresh cannot restore removed generation history', async () => {
  let current = {id: 'book', generations: ['deleted', 'kept']}, replies = [];
  const refresh = callback('refreshBook', {preserveVisualRevision, book: {id: 'book'}, selectionEpoch: {current: 1}, bookRefresh: {current: 0},
    api: () => new Promise(resolve => replies.push(resolve)), setBook: fn => { current = fn(current); }});
  const old = refresh();
  const recent = refresh();
  replies[1]({id: 'book', generations: ['kept']});
  await recent;
  replies[0]({id: 'book', generations: ['deleted', 'kept']});
  await old;
  assert.deepEqual(current.generations, ['kept']);
});

test('checking an unselected generation does not stop or replace current playback', async () => {
  let selected={id:'listening',book_id:'book'},stopped=0;
  const old={id:'older',book_id:'book',status:'ready'};
  const repair=callback('repairGeneration',{run:(_label,action)=>action(),selectionEpoch:{current:1},generation:selected,
    player:{stop:async()=>{stopped++;}},api:async()=>old,setGeneration:value=>{selected=typeof value==='function'?value(selected):value;},
    acceptGenerationRefresh:(current,next)=>current?.id===next.id&&current.book_id===next.book_id?next:current,
    refreshBook:async()=>{},refreshLibrary:async()=>{},setNotice:()=>{}});
  await repair('older');
  assert.equal(stopped,0);assert.equal(selected.id,'listening');
});

test('checking selected audio still pauses and adopts its matching repaired snapshot', async () => {
  let selected={id:'selected',book_id:'book'},stopped=0;
  const next={...selected,status:'rendering'};
  const pendingRepair={current:''};let cleared=0;
  const repair=callback('repairGeneration',{run:(_label,action)=>action(),selectionEpoch:{current:1},generation:selected,
    player:{stop:async()=>{stopped++;},clearError:()=>{cleared++;}},api:async()=>next,setGeneration:value=>{selected=typeof value==='function'?value(selected):value;},
    acceptGenerationRefresh:(current,value)=>current?.id===value.id?value:current,repairOutcome,pendingRepair,
    refreshBook:async()=>{},refreshLibrary:async()=>{},setNotice:()=>{}});
  await repair('selected');
  assert.equal(stopped,1);assert.equal(selected,next);
  assert.equal(cleared,0,'A started repair must not clear the player error before it succeeds');
  assert.equal(pendingRepair.current,'selected');
});

test('repair that verifies the selected audio at once clears the stale player error', async () => {
  let selected={id:'selected',book_id:'book',status:'ready'},cleared=0;const pendingRepair={current:'older'};
  const repair=callback('repairGeneration',{run:(_label,action)=>action(),selectionEpoch:{current:1},generation:selected,
    player:{stop:async()=>{},clearError:()=>{cleared++;}},api:async()=>({...selected}),setGeneration:value=>{selected=typeof value==='function'?value(selected):value;},
    acceptGenerationRefresh:(current,value)=>current?.id===value.id?value:current,repairOutcome,pendingRepair,
    refreshBook:async()=>{},refreshLibrary:async()=>{},setNotice:()=>{}});
  await repair('selected');
  assert.equal(cleared,1);assert.equal(pendingRepair.current,'');
});

test('repair outcome waits for success, clears on ready and drops on failure, pause or another selection', () => {
  assert.equal(repairOutcome('g',{id:'g',status:'rendering'}),'wait');
  assert.equal(repairOutcome('g',{id:'g',status:'queued'}),'wait');
  assert.equal(repairOutcome('g',{id:'g',status:'ready'}),'clear');
  assert.equal(repairOutcome('g',{id:'g',status:'failed'}),'drop');
  assert.equal(repairOutcome('g',{id:'g',status:'paused'}),'drop');
  assert.equal(repairOutcome('g',{id:'other',status:'ready'}),'drop');
  assert.equal(repairOutcome('g',null),'drop');
  assert.equal(repairOutcome('',{id:'g',status:'ready'}),'drop');
});

function deletion({current,remaining,position=null}) {
  const calls=[];let selected=current,stopped=0,notice='';
  const book={id:'book'};
  const remove=callback('deleteGeneration',{run:(_label,action)=>action(),selectionEpoch:{current:1},generation:current,book,settings:null,
    player:{stop:async()=>{stopped++;}},pauseRequest:{removed:id=>calls.push('removed:'+id)},refreshHealth:async()=>{},
    api:async(op,data)=>{calls.push(op+':'+data.id);return op==='delete_generation'?{deleted:true,cleanup_warnings:[]}:{id:data.id,book_id:'book'};},
    refreshBook:async()=>({id:'book',generations:remaining,position}),defaultGeneration,refreshLibrary:async()=>{},
    setGeneration:value=>{selected=typeof value==='function'?value(selected):value;},setNotice:value=>{notice=value;},
    deletionNotice:()=>'Generation removed.'});
  return {remove,calls,state:()=>({selected,stopped,notice})};
}

test('deleting the selected generation selects the same fallback used when opening a book', async () => {
  const saved=deletion({current:{id:'deleted',book_id:'book'},remaining:[{id:'first'},{id:'last-played'}],position:{generation_id:'last-played',chapter_id:'c',offset:0}});
  await saved.remove({id:'deleted'});
  assert.deepEqual(saved.calls,['delete_generation:deleted','removed:deleted','generation:last-played']);
  assert.equal(saved.state().selected.id,'last-played');assert.equal(saved.state().stopped,1);
  const first=deletion({current:{id:'deleted',book_id:'book'},remaining:[{id:'first'},{id:'second'}],position:{generation_id:'deleted',chapter_id:'c',offset:0}});
  await first.remove({id:'deleted'});
  assert.equal(first.state().selected.id,'first');
});

test('deleting the only selected generation clears it, and deleting another keeps the current one', async () => {
  const only=deletion({current:{id:'deleted',book_id:'book'},remaining:[]});
  await only.remove({id:'deleted'});
  assert.equal(only.state().selected,null);assert.deepEqual(only.calls,['delete_generation:deleted','removed:deleted']);
  const other=deletion({current:{id:'kept',book_id:'book'},remaining:[{id:'kept'}]});
  await other.remove({id:'older'});
  assert.equal(other.state().selected.id,'kept');assert.equal(other.state().stopped,0);
  assert.deepEqual(other.calls,['delete_generation:older','removed:older']);
});

test('runtime loss does not replace the connection banner with a raw action error', async () => {
  const errors=[];const loss=new Error('The local runtime is not responding.');
  const run=callback('run',{runningAction:{current:false},setBusy:()=>{},setError:value=>errors.push(value),errorText:String,operationMessage:String,language:'en',isRuntimeLoss:reason=>reason===loss,busy:''});
  await run('Loading',async()=>{throw loss;});
  assert.deepEqual(errors,['']);
  await run('Loading',async()=>{throw new Error('Real failure');});
  assert.deepEqual(errors,['','','Error: Real failure']);
});

test('illustration opening freezes the target rather than following the next narrated passage', () => {
  let selected='',modal='';
  const open=callback('openModal',{setError:()=>{},setScope:()=>{},setModal:value=>{modal=value;},
    selectedBlock:{id:'clicked-passage',kind:'paragraph'},setSelectedSource:value=>{selected=value;}});
  open('illustration');assert.equal(modal,'illustration');assert.equal(selected,'clicked-passage');
});

test('export callback sends an explicit chapter selection and never invokes generation', async () => {
  const requests=[];
  const generation={id:'g',book_id:'book'},book={id:'book',title:'Book'};
  const run=(_label,action)=>action();
  const exportAudio=callback('exportAudio',{run,generation,book,selectionEpoch:{current:1},
    api:async(op,data)=>{requests.push({op,data});return op==='export'?{id:'file',file:'file.m4b',format:'m4b'}:generation;},
    saveExport:async()=>true,setGeneration:()=>{},acceptGenerationRefresh:()=>generation,setNotice:()=>{},setExporting:()=>{}});
  await exportAudio('m4b',['chapter-2']);
  assert.deepEqual(requests.map(item=>item.op),['export','generation']);
  assert.deepEqual(requests[0].data.chapters,['chapter-2']);
  await assert.rejects(exportAudio('m4b',[]),/Select/);
  assert.equal(requests.length,2);
});

test('illustrated player preparation targets the displayed paragraph rather than a stale text selection',()=>{
  const calls=[];const prepare=callback('openModal',{setError:()=>{},selectedBlock:{id:'old',kind:'paragraph'},chapter:{blocks:[]},readerIndex:{sources:new Map([['current',{block:{id:'current',kind:'paragraph'}}]])},setSelectedSource:id=>calls.push(id),setModal:panel=>calls.push(panel)});
  prepare('illustration','current');assert.deepEqual(calls,['current','illustration']);
});

const focusModule={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/dialogFocus.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(focusModule,focusModule.exports);
const {createDialogFocus}=focusModule.exports;
function focusable(name,{connected=true,disabled=false}={}){return {name,isConnected:connected,matches:selector=>selector===':disabled'&&disabled,focus(){focusable.active=this;}};}

test('dialog focus returns to the element that opened the first dialog, not to an intermediate dialog', () => {
  const focus=createDialogFocus();const opener=focusable('Book tools'),row=focusable('Narration row'),reader=focusable('Book text');
  focus.change(true,opener);focus.change(true,row);
  assert.equal(focus.restore(reader,()=>focusable.active),false,'Switching dialogs must not restore focus');
  focus.change(false,null);
  assert.equal(focus.restore(reader,()=>focusable.active),true);assert.equal(focusable.active,opener);
  focusable.active=null;assert.equal(focus.restore(reader,()=>focusable.active),true);assert.equal(focusable.active,reader,'A restored opener is used once');
});

test('dialog focus falls back to the reader when the opener disappeared or became disabled', () => {
  for(const options of [{connected:false},{disabled:true}]){
    const focus=createDialogFocus();const reader=focusable('Book text');
    focus.change(true,focusable('Prepare audiobook',options));focus.change(false,null);focusable.active=null;
    assert.equal(focus.restore(reader,()=>focusable.active),true);assert.equal(focusable.active,reader);
  }
  const empty=createDialogFocus();empty.change(true,null);empty.change(false,null);focusable.active=null;
  assert.equal(empty.restore(null,()=>focusable.active),false);
});

test('actual setModal records the focused opener only when a dialog first opens', () => {
  const changes=[];const opener={id:'opener'};const inside={id:'inside'};
  const HTMLElement=function(){};Object.setPrototypeOf(opener,HTMLElement.prototype);Object.setPrototypeOf(inside,HTMLElement.prototype);
  const document={activeElement:opener,body:{}};
  const setModal=callback('setModal',{useCallback:fn=>fn,modalRevision:{current:0},dialogFocus:{change:(open,candidate)=>changes.push([open,candidate?.id??null])},
    pointerTarget:{current:null},setModalState:()=>{},setError:()=>{},setNotice:()=>{},document,HTMLElement,Date});
  setModal('bookTools');document.activeElement=inside;setModal('narration');setModal(null);
  assert.deepEqual(changes,[[true,'opener'],[true,'inside'],[false,'inside']]);
  document.activeElement=document.body;const clicked={id:'clicked'};Object.setPrototypeOf(clicked,HTMLElement.prototype);
  const recent=callback('setModal',{useCallback:fn=>fn,modalRevision:{current:0},dialogFocus:{change:(open,candidate)=>changes.push([open,candidate?.id??null])},
    pointerTarget:{current:{element:clicked,at:Date.now()}},setModalState:()=>{},setError:()=>{},setNotice:()=>{},document,HTMLElement,Date});
  recent('settings');assert.deepEqual(changes.at(-1),[true,'clicked'],'WebKit clicks do not focus buttons; the pressed control is the opener');
});

test('no-op corrections are recognised before they reach the runtime', () => {
  const {correctionChanged}=selectionModule.exports;
  const original={spoken:'Text',speaker:'narrator',delivery:'neutral'};
  assert.equal(correctionChanged(original,{...original}),false);
  assert.equal(correctionChanged(original,{...original,spoken:'Text.'}),true);
  assert.equal(correctionChanged(original,{...original,speaker:'unknown'}),true);
  assert.equal(correctionChanged(original,{...original,delivery:'soft'}),true);
  assert.equal(correctionChanged(undefined,original),false);
});

test('navigation from a dialog focuses the destination passage instead of the dialog opener', () => {
  const focus=createDialogFocus();const opener=focusable('Illustrate paragraph'),row=focusable('Passage'),reader=focusable('Book text');
  focus.change(true,opener);focus.change(false,null);focusable.active=null;
  assert.equal(focus.restore(reader,()=>focusable.active,row),true);assert.equal(focusable.active,row);
  focus.change(true,opener);focus.change(false,null);focusable.active=null;
  assert.equal(focus.restore(reader,()=>focusable.active,focusable('Missing',{connected:false})),true);assert.equal(focusable.active,opener);
});

test('generation rename trims names, ignores cancel and unchanged names, and rejects blank input', () => {
  const {generationRename}=selectionModule.exports;
  assert.equal(generationRename(null,'Take A'),null);
  assert.equal(generationRename('  Take A ','Take A'),null);
  assert.deepEqual(generationRename('   ','Take A'),{invalid:true});
  assert.deepEqual(generationRename('  Take B  ','Take A'),{name:'Take B'});
});

test('whitespace-only or whitespace-changed corrections never reach the runtime', () => {
  const {correctionChanged}=selectionModule.exports;
  const original={spoken:'Text',speaker:'narrator',delivery:'neutral'};
  assert.equal(correctionChanged(original,{...original,spoken:'   '}),false);
  assert.equal(correctionChanged(original,{...original,spoken:' Text  '}),false);
  assert.equal(correctionChanged(original,{...original,spoken:'Other text '}),true);
});

test('correcting a chapter outside the selected generation explains the missing chapter', () => {
  const assignment=find(node=>ts.isBinaryExpression(node)&&node.left.getText(tree)==='passageActionRef.current');
  const notices=[];
  const environment=generation=>({segments:{bySource:new Map()},generation,setNotice:message=>notices.push(message),setEditSegment:()=>{},setModal:()=>{},listenTo:()=>{},toggleBookmark:()=>{},
    setGenerationSource:()=>{},setGenerationChapter:()=>{},setScope:()=>{},setSelectedSource:()=>{},readerIndex:{sources:new Map()}});
  compile(assignment.right,environment({id:'g1'}))('p9','correct');
  compile(assignment.right,environment(null))('p9','correct');
  assert.deepEqual(notices,[t('generationMissingChapter'),t('createBeforeCorrect')]);
});

test('opening Settings from any dialog shows the active pack path, not a rejected entry', () => {
  const effect=find(node=>ts.isCallExpression(node)&&node.expression.getText(tree)==='useEffect'&&node.arguments[0]?.getText(tree).includes("modal==='settings'")&&node.arguments[0].getText(tree).includes('setPackPath'));
  const paths=[];
  for(const modal of ['settings','generate'])compile(effect.arguments[0],{modal,settings:{pack_path:'/active/pack'},setPackPath:value=>paths.push(value)})();
  assert.deepEqual(paths,['/active/pack']);
});

test('book XML failures are localized with only the document name and line', () => {
  const {operationMessage}=i18nModule.exports;
  const failure=(details)=>Object.assign(new Error('Raw runtime text'),{code:'invalid_book_xml',details});
  assert.equal(operationMessage(failure({document:'EPUB/a.xhtml',line:3}),'en'),'Book document EPUB/a.xhtml is not well-formed XML at line 3.');
  assert.equal(operationMessage(failure({document:'EPUB/a.xhtml',line:3}),'ru'),'Документ книги EPUB/a.xhtml содержит некорректный XML (строка 3).');
  assert.equal(operationMessage(failure({document:'',line:null}),'en'),'The book is not well-formed XML.');
  assert.equal(operationMessage(failure({document:'',line:1}),'ru'),'Книга содержит некорректный XML (строка 1).');
  assert.equal(operationMessage(new Error('Only EPUB and FB2 are supported.'),'ru'),'Поддерживаются только EPUB и FB2.');
  assert.equal(operationMessage(new Error('Unmapped runtime text.'),'ru'),'Unmapped runtime text.');
  assert.equal(operationMessage('text','en'),'text');
});

test('jobs for another book are named and explained instead of silently blocking controls', () => {
  const {jobElsewhere}=i18nModule.exports;
  assert.equal(jobElsewhere('en','audio','Harbor'),'Audio for “Harbor” is being prepared. Pause it or wait until it finishes before starting or resuming another generation.');
  assert.equal(jobElsewhere('ru','illustrations','Гавань'),'Сейчас готовятся иллюстрации для «Гавань». Остановите их или дождитесь завершения, чтобы начать другую подготовку.');
  assert.match(jobElsewhere('en','audio',''),/^Audio is being prepared\./);
  const runsModule={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/illustrationRuns.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(runsModule,runsModule.exports);
  const {illustrationElsewhere}=runsModule.exports;
  assert.equal(illustrationElsewhere('other-run',[{id:'own-run'}]),true);
  assert.equal(illustrationElsewhere('own-run',[{id:'own-run'}]),false);
  assert.equal(illustrationElsewhere(null,[{id:'own-run'}]),false);
});

test('opening the active preparation selects its generation in the owning book', () => {
  const calls=[];
  const environment=(book)=>({t,health:{active:'g-active',active_book:{id:'harbor',title:'Harbor'}},book,run:(_label,fn)=>fn(),
    open:async(id,generation)=>calls.push(['open',id,generation]),selectGeneration:async id=>calls.push(['select',id])});
  return (async()=>{
    await callback('openActive',environment({id:'forest'}))();
    await callback('openActive',environment({id:'harbor'}))();
    assert.deepEqual(calls,[['open','harbor','g-active'],['select','g-active']]);
  })();
});

test('counts, dates, labels, import results and player messages follow the interface language', () => {
  const {count,locale,importNotice,deliveryLabel,playerMessage}=i18nModule.exports;
  assert.deepEqual([1,2,5,11,21,22,25,112].map(value=>count('ru',value,'chapters')),['1 глава','2 главы','5 глав','11 глав','21 глава','22 главы','25 глав','112 глав']);
  assert.deepEqual([0,1,2].map(value=>count('en',value,'books')),['0 books','1 book','2 books']);
  assert.equal(count('en',1,'sections'),'1 section');assert.equal(count('ru',1,'sections'),'1 раздел');
  assert.equal(count('en',2097,'narrationCharacters'),'2,097 narration characters');
  assert.equal(locale('ru'),'ru-RU');assert.equal(locale('en'),'en-US');
  assert.equal(importNotice('en',1,0),'1 book imported. Saved in your local library.');
  assert.equal(importNotice('en',0,1),'1 book already in your library.');
  assert.equal(importNotice('ru',2,1),'Импортировано: 2 книги. Сохранено в локальной библиотеке. Уже в библиотеке: 1 книга.');
  assert.equal(deliveryLabel('ru','whisper'),'Шёпотом');assert.equal(deliveryLabel('en','neutral'),'Neutral');assert.equal(deliveryLabel('en','custom'),'custom');
  assert.equal(playerMessage('ru','This passage is not ready. Resume preparation to continue.'),'Этот отрывок ещё не готов. Продолжите подготовку.');
  assert.equal(playerMessage('ru','Could not save playback position: offline'),'Не удалось сохранить место прослушивания: offline');
  assert.equal(playerMessage('en','The saved audio could not be played. Press Play to retry.'),'The saved audio could not be played. Press Play to retry.');
  assert.equal(playerMessage('ru',''),'');
});

test('re-importing a book already in the library is reported as existing, not as a new import', async () => {
  const notices=[];let closed=null;
  const importFiles=callback('importFiles',{runningAction:{current:false},setError:()=>{},selectionEpoch:{current:1},library:[{id:'known'}],language:'en',t,
    importBooks:async()=>({imported:[{id:'known'},{id:'fresh'}],errors:[]}),uploadBook:()=>{},operationMessage:()=>'',refreshLibrary:async()=>{},open:async()=>{},
    importNotice:i18nModule.exports.importNotice,setNotice:message=>notices.push(message),run:async(_label,fn)=>{closed=fn();await closed;}});
  importFiles([{name:'a.epub'}]);await closed;
  assert.deepEqual(notices,['1 book imported. Saved in your local library. 1 book already in your library.']);
});

test('opening a dialog clears an earlier notice so it is not repeated inside the dialog', () => {
  const notices=[];
  const setModal=callback('setModal',{useCallback:fn=>fn,modalRevision:{current:0},dialogFocus:{change:()=>{}},pointerTarget:{current:null},
    setModalState:()=>{},setError:()=>{},setNotice:value=>notices.push(value),document:{activeElement:null,body:{}},HTMLElement:function(){},Date});
  setModal('illustration');setModal(null);
  assert.deepEqual(notices,['']);
});

test('removing a bookmark that has a note requires confirmation; bookmarks without notes toggle directly', async () => {
  const calls=[];let answer=false;
  const make=bookmarks=>callback('toggleBookmark',{book:{id:'b',bookmarks},t,ru:false,run:async(_label,fn)=>fn(),refreshBook:async()=>{},setNotice:()=>{},
    removeNoteText:note=>note,confirmed:async options=>{calls.push(['ask',options.title]);return answer;},api:async(op,data)=>calls.push([op,data.id??data.source_id])});
  await make([{id:'m1',source_id:'p1',note:'Keep this'}])('p1');
  assert.deepEqual(calls,[['ask',t('removeBookmarkTitle')]]);
  answer=true;await make([{id:'m1',source_id:'p1',note:'Keep this'}])('p1');
  assert.deepEqual(calls.slice(1),[['ask',t('removeBookmarkTitle')],['delete_bookmark','m1']]);
  calls.length=0;await make([{id:'m2',source_id:'p2',note:'  '}])('p2');
  assert.deepEqual(calls,[['delete_bookmark','m2']]);
});

test('a rejected model pack path is replaced by the active path', async () => {
  const paths=[];
  const verify=callback('verifyPack',{settingsRequests:{current:Promise.resolve()},packPath:'/wrong',settings:{pack_path:'/active'},setPackPath:value=>paths.push(value),
    api:async()=>{throw new Error('Selected folder does not contain a valid pack.json.');},setSettings:()=>{},refreshHealth:async()=>{},setNotice:()=>{},ru:false});
  await assert.rejects(verify(),/valid pack/);
  assert.deepEqual(paths,['/active']);
});

test('library and chapter search ignore case, surrounding space and the difference between е and ё', () => {
  const {searchKey,visibleChapters}=selectionModule.exports;
  assert.equal(searchKey('  Пётр Ёлкин '),'петр елкин');
  assert.ok(searchKey('Пётр и море').includes(searchKey('петр')));
  assert.deepEqual(visibleChapters([{title:'Ёлка'},{title:'Дом'}],' елка').map(item=>item.number),[1]);
});

test('narrator names, passage progress and voice labels follow the interface language', () => {
  const {characterName,passageProgress}=i18nModule.exports;
  assert.equal(characterName('ru',{id:'narrator',name:'Narrator'}),'Рассказчик');
  assert.equal(characterName('ru',{id:'masha',name:'Маша'}),'Маша');
  assert.equal(passageProgress('en',3,10,'saved'),'3/10 passages saved');
  assert.equal(passageProgress('en',1,1,'prepared'),'1/1 passage prepared');
  assert.equal(passageProgress('ru',2,21,'prepared'),'Готово отрывков: 2/21');
  assert.equal(passageProgress('ru',5,5,'saved'),'Сохранено отрывков: 5/5');
});

test('the interface never falls back to native browser prompt, confirm or alert dialogs', () => {
  const files=['src/App.tsx',...fs.readdirSync(path.resolve(__dirname,'../src/components')).filter(name=>name.endsWith('.tsx')).map(name=>'src/components/'+name)];
  for(const file of files){
    const text=fs.readFileSync(path.resolve(__dirname,'..',file),'utf8');
    assert.doesNotMatch(text,/(^|[^.\w])(confirm|prompt|alert)\(/,file);
  }
});

const removalModule={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/removalFocus.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(removalModule,removalModule.exports);
function rows(count){
  const focused={current:null};
  const control=(name,disabled=false)=>({name,isConnected:true,disabled,matches:selector=>selector===':disabled'&&disabled,focus(){focused.current=this;}});
  const list=Array.from({length:count},(_,index)=>({index,button:control('row '+index),matches:selector=>selector==='.row'}));
  list.forEach((row,index)=>{row.nextElementSibling=list[index+1]||null;row.previousElementSibling=list[index-1]||null;row.querySelector=()=>row.button;row.button.closest=selector=>selector==='.row'?row:null;});
  return {list,focused,control};
}

test('after a confirmed removal focus moves to the next surviving row, then the previous row, then a fallback', () => {
  const {removalFocus}=removalModule.exports;
  let dom=rows(3);let restore=removalFocus(dom.list[1].button,'.row',()=>[]);dom.list[1].button.isConnected=false;
  assert.equal(restore(),dom.list[2].button);assert.equal(dom.focused.current,dom.list[2].button);
  dom=rows(2);restore=removalFocus(dom.list[1].button,'.row',()=>[]);dom.list[1].button.isConnected=false;
  assert.equal(restore(),dom.list[0].button);
  dom=rows(1);const fallback=dom.control('New generation');restore=removalFocus(dom.list[0].button,'.row',()=>[fallback]);dom.list[0].button.isConnected=false;
  assert.equal(restore(),fallback);assert.equal(dom.focused.current,fallback);
  dom=rows(2);restore=removalFocus(dom.list[0].button,'.row',()=>[]);
  assert.equal(restore(),null,'A failed or cancelled removal keeps focus on the opener');assert.equal(dom.focused.current,null);
});

test('confirmed removals restore focus only after the mutation commits; cancel leaves the opener focused', async () => {
  const {removalFocus}=removalModule.exports;const events=[];
  const make=(answer,dom)=>callback('removeWith',{currentOpener:()=>dom.list[0].button,request:async()=>{events.push('ask');return answer;},removalFocus,
    afterCommit:async()=>events.push('commit')});
  let dom=rows(2);
  await make(null,dom)({title:'x'},'.row',()=>[],async()=>{events.push('remove');});
  assert.deepEqual(events,['ask']);assert.equal(dom.focused.current,null);
  events.length=0;dom=rows(2);
  await make('',dom)({title:'x'},'.row',()=>[],async()=>{events.push('remove');dom.list[0].button.isConnected=false;});
  assert.deepEqual(events,['ask','remove','commit']);assert.equal(dom.focused.current,dom.list[1].button);
  events.length=0;dom=rows(2);
  await make('',dom)(null,'.row',()=>[],async()=>{events.push('remove');dom.list[0].button.isConnected=false;});
  assert.deepEqual(events,['remove','commit'],'Removals without a note skip the dialog but still restore focus');
});

test('a maximum-length bookmark note is previewed briefly so dialog actions stay reachable', () => {
  const {preview}=i18nModule.exports;
  const note='Заметка '.repeat(250).slice(0,2000);
  assert.equal(preview(note).length,160);assert.ok(preview(note).endsWith('…'));
  assert.equal(preview('  short\n note '),'short note');
  const text=callback('removeNoteText',{ru:false,preview})(note);
  assert.ok(text.length<220,text.length);
  const css=fs.readFileSync(path.resolve(__dirname,'../src/styles.css'),'utf8');
  assert.match(css,/\.ask-dialog \.dialog-header\{flex-shrink:1;min-height:0;overflow:auto\}/);
  assert.match(css,/\.ask-dialog \.modal-body\{flex-shrink:0/);
});

test('an image failure shows its fallback in the language passed by the application, including after a switch', async () => {
  const media=(()=>{
    let calls=0;const jsx=(type,props)=>({type,props});
    const modules={react:{memo:fn=>fn,useEffect:()=>{},useLayoutEffect:()=>{},useRef:value=>({current:value}),useState:initial=>[calls++%4===2?true:initial,()=>{}]},
      'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'},dompurify:{sanitize:()=>null},'../lib/api':{asset:()=>{}},'../lib/bookAssets':{hydrateBookAssets:()=>()=>{}},
      '../lib/imageCache':{createImageCache:()=>({}),whenVisible:()=>()=>{},observeVisibility:()=>()=>{}},'../lib/i18n':i18nModule.exports};
    const module={exports:{}};
    new Function('require','module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/components/Media.tsx'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText)(name=>modules[name],module,module.exports);
    return module.exports;
  })();
  for(const [language,label] of [['en','Image unavailable · Retry'],['ru','Изображение недоступно · Повторить'],['en','Image unavailable · Retry']]){
    const tree=media.AssetImage({kind:'visual',id:'v',language});
    const button=tree.props.children;
    assert.equal(button.type,'button');assert.equal(button.props.children,label);
  }
  const mediaSource=fs.readFileSync(path.resolve(__dirname,'../src/components/Media.tsx'),'utf8');
  assert.doesNotMatch(mediaSource,/documentElement/);assert.match(mediaSource,/\},\[block\.html,book,language\]\);/);
  const hydrate=(()=>{const module={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/bookAssets.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(module,module.exports);return module.exports.hydrateBookAssets;})();
  const image=()=>{const attributes={'data-asset':'a'.repeat(64)+'.png'};return {attributes,localName:'img',namespaceURI:'http://www.w3.org/1999/xhtml',removeAttribute:name=>{delete attributes[name];},removeAttributeNS:()=>{},getAttribute:name=>attributes[name]??null,setAttribute:(name,value)=>{attributes[name]=value;}};};
  const results=[];
  for(const label of ['Image unavailable','Изображение недоступно']){
    const current=image();const dispose=hydrate({querySelectorAll:()=>[current]},async()=>{throw new Error('missing');},label);
    for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));
    results.push(current.attributes.alt);dispose();
  }
  assert.deepEqual(results,['Image unavailable','Изображение недоступно']);
});

test('deleting the final saved export skips a disabled Export button and focuses the Format select', () => {
  const {removalFocus}=removalModule.exports;
  const dom=rows(1);const exportButton=dom.control('Export',true);const format=dom.control('Format');
  const restore=removalFocus(dom.list[0].button,'.row',()=>[exportButton,format]);dom.list[0].button.isConnected=false;
  assert.equal(restore(),format);assert.equal(dom.focused.current,format);
  const panel=fs.readFileSync(path.resolve(__dirname,'../src/components/ExportPanel.tsx'),'utf8');
  assert.match(panel,/'\.saved-export', \(\) => \[exportButton\.current, formatSelect\.current\]/);
  assert.match(panel,/<select ref=\{formatSelect\}/);
});
