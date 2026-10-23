const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
const {sourceTree}=require('./layout_source.cjs');
const load=file=>{const module={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(module,module.exports);return module.exports;};
const {bookIndex,segmentIndex,paragraphImages,illustrationSources}=load('src/lib/readerIndexes.ts');
const {createImageCache}=load('src/lib/imageCache.ts');
const {createPlaybackClock,samePlaybackStructure}=load('src/lib/playbackClock.ts');
const {importBooks}=load('src/lib/importFiles.ts');
const walk=(value,predicate)=>Array.isArray(value)?value.flatMap(item=>walk(item,predicate)):!value||typeof value!=='object'?[]:[...(predicate(value)?[value]:[]),...walk(value.props?.children,predicate)];
const block=(id,kind='paragraph')=>({id,kind,text:'Original '+id,html:'<p>Original '+id+'</p>',assets:[]});
const chapter={id:'c1',title:'Chapter',blocks:[block('h','heading'),block('p1'),block('p2'),block('p3')]};
const visual=(id,source_id)=>({id,source_id,caption:'Illustration '+id,kind:'imported',file:id+'.png',created:id});
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('indexed passages and generation progress preserve chapter and source identities',()=>{
  const book={chapters:[chapter,{id:'c2',blocks:[block('q1')]}]};
  const index=bookIndex(book);assert.equal(index.sources.get('q1').chapter.id,'c2');assert.equal(index.sources.get('p2').block,chapter.blocks[2]);
  const segments=[{id:'a',source_id:'p1',chapter_id:'c1',status:'ready'},{id:'b',source_id:'p1',chapter_id:'c1',status:'rendering'},{id:'c',source_id:'q1',chapter_id:'c2',status:'pending'}];
  const state=segmentIndex({segments});assert.equal(state.ready,1);assert.deepEqual(state.bySource.get('p1'),segments.slice(0,2));assert.deepEqual(state.progress.get('c1'),{total:2,ready:1,rendering:true});assert.equal(state.byId.get('b'),segments[1]);
});

test('paragraph selection never groups scenes and includes existing records for authoritative file checks',()=>{
  assert.deepEqual(illustrationSources([chapter],'p2','paragraph'),['p2']);
  assert.deepEqual(illustrationSources([chapter],'p2','chapter'),['p1','p2','p3']);
  assert.deepEqual(illustrationSources([chapter],'h','paragraph'),[]);
  assert.deepEqual(illustrationSources([chapter,{id:'c2',blocks:[block('p4'),{...block('blank'),text:' '}]}],'','book'),['p1','p2','p3','p4']);
});

test('inline images follow their own paragraphs and only the latest version is displayed',()=>{
  const images=paragraphImages([visual('old','p1'),visual('other','p3'),visual('latest','p1')]);const calls=[];
  const {tree}=sourceTree('src/components/ReaderChapter.tsx',{}, {component:'ReaderChapter',chapter,bookId:'book',speaking:'p2',selected:'p1',images,onSelect:()=>{},onNavigate:id=>calls.push(id),onAction:()=>{}});
  const passages=walk(tree,item=>item.props?.['data-source-id']);assert.deepEqual(passages.map(item=>item.props.id),['h','p1','p2','p3']);
  const p1=passages[1];const figures=walk(p1,item=>item.tag==='figure');assert.equal(figures.length,1);assert.equal(figures[0].props['data-illustration-source'],'p1');assert.equal(walk(figures[0],item=>item.tag==='img')[0].props.alt,'Illustration latest');
  const children=p1.props.children;assert.ok(children.findIndex(item=>item.tag==='figure')>children.findIndex(item=>item.props?.dangerouslySetInnerHTML));
  assert.equal(walk(passages[2],item=>item.tag==='figure').length,0);
  walk(figures[0],item=>item.tag==='button')[0].props.onClick({stopPropagation(){}});assert.deepEqual(calls,['p1']);assert.equal(images.get('p1').length,2);
});

test('illustrated view links its own paragraph and never borrows an earlier picture for a missing paragraph',()=>{
  const images=paragraphImages([visual('image','p1')]),calls=[];
  let result=sourceTree('src/components/IllustratedPanel.tsx',{}, {component:'IllustratedPanel',chapter,bookId:'book',sourceId:'p1',images,onNavigate:id=>calls.push(id),onIllustrate:id=>calls.push('illustrate:'+id)});
  walk(result.tree,item=>item.tag==='button'&&JSON.stringify(item.props.children).includes('Show this paragraph'))[0].props.onClick();assert.deepEqual(calls,['p1']);
  result=sourceTree('src/components/IllustratedPanel.tsx',{}, {component:'IllustratedPanel',chapter,bookId:'book',sourceId:'p2',images,onNavigate:id=>calls.push(id),onIllustrate:id=>calls.push('illustrate:'+id)});
  assert.equal(walk(result.tree,item=>item.props.className==='stage-empty').length,1);
  walk(result.tree,item=>item.tag==='button'&&JSON.stringify(item.props.children).includes('Illustrate paragraph'))[0].props.onClick();assert.equal(calls.at(-1),'illustrate:p2');
});

test('playback clock ticks notify the dock without changing the reader structural state',()=>{
  const clock=createPlaybackClock(),segment={id:'s1'};let notifications=0,changes=0;let previous={segment,position:0,playing:true,waiting:false,error:''};
  const unsubscribe=clock.subscribe(()=>notifications++);for(let position=0;position<600;position++){const next={...previous,position};clock.publish(next);if(!samePlaybackStructure(previous,next))changes++;previous=next;}
  assert.equal(notifications,600);assert.equal(changes,0);assert.equal(clock.getSnapshot().position,599);assert.equal(samePlaybackStructure(previous,{...previous,segment:{id:'s2'}}),false);assert.equal(samePlaybackStructure(previous,{...previous,error:'Missing audio'}),false);
  unsubscribe();clock.publish(previous);assert.equal(notifications,600);
});

test('batch import handles every file sequentially and retains successes around invalid inputs',async()=>{
  const files=['one.epub','invalid.txt','broken.fb2','two.FB2'].map(name=>new File(['source'],name));let active=0,max=0;const names=[];
  const result=await importBooks(files,async file=>{names.push(file.name);max=Math.max(max,++active);await settle();active--;if(file.name==='broken.fb2')throw Error('Bad book');return file.name;});
  assert.deepEqual(names,['one.epub','broken.fb2','two.FB2']);assert.equal(max,1);assert.deepEqual(result.imported,['one.epub','two.FB2']);assert.equal(result.errors.length,2);
});

test('empty and oversized files are rejected without reading or uploading them',async()=>{
  let calls=0;const result=await importBooks([{name:'a.epub',size:0},{name:'b.fb2',size:50*1024*1024+1}],async()=>calls++);assert.equal(calls,0);assert.equal(result.errors.length,2);
});

test('image cache shares in-flight loads across reader and illustrated view',async()=>{
  let calls=0;const cache=createImageCache(async()=>{calls++;return new Blob(['image']);});const a=cache.acquire('visual_preview','v'),b=cache.acquire('visual_preview','v');assert.equal(await a.promise,await b.promise);assert.equal(calls,1);a.release();a.release();b.release();
  const c=cache.acquire('visual_preview','v');await c.promise;assert.equal(calls,1);c.release();cache.clear();assert.equal(cache.size(),0);
});

test('unmounted pending image loads abort and later visits can retry',async()=>{
  const signals=[];let finish;const cache=createImageCache((_kind,_id,_book,signal)=>{signals.push(signal);return new Promise(resolve=>{finish=resolve;});});
  const first=cache.acquire('visual','v');await settle();first.release();assert.equal(signals[0].aborted,true);const second=cache.acquire('visual','v');await settle();assert.equal(signals.length,2);assert.equal(signals[1].aborted,false);finish(new Blob(['second']));await second.promise;second.release();cache.clear();
});

test('image cache evicts only idle entries when its byte budget is exceeded',async()=>{
  let calls=0;const cache=createImageCache(async()=>{calls++;return new Blob(['12345']);},5);const a=cache.acquire('visual','a');await a.promise;const b=cache.acquire('visual','b');await b.promise;assert.equal(cache.size(),2);a.release();assert.equal(cache.size(),1);b.release();const again=cache.acquire('visual','a');await again.promise;assert.equal(calls,3);again.release();cache.clear();
});

test('image failures are not cached as permanent success',async()=>{
  let calls=0;const cache=createImageCache(async()=>{if(++calls===1)throw Error('temporary');return new Blob(['ok']);});const a=cache.acquire('visual','v');await assert.rejects(a.promise);a.release();const b=cache.acquire('visual','v');assert.equal((await b.promise).size,2);b.release();assert.equal(calls,2);
});

test('native configuration leaves HTML file dropping enabled rather than intercepting it',()=>{
  const config=JSON.parse(fs.readFileSync(path.join(__dirname,'../src-tauri/tauri.conf.json'),'utf8'));assert.equal(config.app.windows[0].dragDropEnabled,false);
  assert.equal(config.bundle.resources['../resources/series-characters.json'],'series-characters.json');
});

test('late visual polls and full book refreshes cannot resurrect deleted illustrations',()=>{
  const {acceptVisualRefresh,preserveVisualRevision}=load('src/lib/readerIndexes.ts');
  const original={id:'book',visuals:[visual('old','p1')],visual_revision:2};
  const deleted=acceptVisualRefresh(original,{id:'book',revision:3,visuals:[]});assert.equal(deleted.visuals.length,0);
  assert.equal(acceptVisualRefresh(deleted,{id:'book',revision:2,visuals:original.visuals}),deleted);
  assert.equal(acceptVisualRefresh(deleted,{id:'other',revision:9,visuals:original.visuals}),deleted);
  assert.equal(acceptVisualRefresh(deleted,{id:'book',revision:3,visuals:original.visuals}),deleted);
  const refreshed=preserveVisualRevision(deleted,{...original,title:'New book title'});assert.deepEqual(refreshed.visuals,[]);assert.equal(refreshed.title,'New book title');
  assert.deepEqual(preserveVisualRevision(deleted,{...original,id:'other'}).visuals,original.visuals);
});

test('import errors use the supplied localized description after the file name',async()=>{
  const failure=Object.assign(new Error('raw'),{code:'invalid_book_xml'});
  const result=await importBooks([{name:'bad.epub',size:10}],async()=>{throw failure;},reason=>reason===failure?'localized':'other');
  assert.deepEqual(result.errors,['bad.epub: localized']);
});

test('passages are single keyboard stops that select with Enter and expose actions only when selected',()=>{
  const selected=[];const images=paragraphImages([]);
  const {tree}=sourceTree('src/components/ReaderChapter.tsx',{}, {component:'ReaderChapter',chapter,bookId:'book',selected:'p1',images,bookmarks:new Set(['p2']),onSelect:id=>selected.push(id),onNavigate:()=>{},onAction:()=>{}});
  const passages=walk(tree,item=>item.props?.['data-source-id']);
  assert.deepEqual(passages.map(item=>item.props.tabIndex),[-1,0,0,0],'The hidden narrated title stays a programmatic focus target only');
  const actions=item=>walk(item,node=>node.tag==='button'&&node.props['aria-label']);
  assert.deepEqual(actions(passages[1]).map(button=>button.props.tabIndex),[0,0,0,0]);
  assert.deepEqual(actions(passages[2]).map(button=>button.props.tabIndex),[-1,-1,-1,-1]);
  const bookmark=item=>actions(item).find(button=>/^(Bookmark passage|Remove bookmark): /.test(button.props['aria-label']));
  assert.equal(bookmark(passages[2]).props['aria-label'],'Remove bookmark: passage 3, Original p2');assert.equal(bookmark(passages[2]).props.title,'Remove bookmark');
  assert.equal(bookmark(passages[1]).props['aria-label'],'Bookmark passage: passage 2, Original p1');
  const section=passages[2];let prevented=false;
  section.props.onKeyDown({key:'Enter',target:section,currentTarget:section,preventDefault:()=>{prevented=true;}});
  section.props.onKeyDown({key:'Enter',target:{},currentTarget:section,preventDefault:()=>{}});
  section.props.onKeyDown({key:' ',target:section,currentTarget:section,preventDefault:()=>{}});
  assert.deepEqual(selected,['p2']);assert.equal(prevented,true);
});
