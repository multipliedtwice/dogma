const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const ts=require('typescript');
const moduleValue={exports:{}};
new Function('module','exports',ts.transpileModule(fs.readFileSync('src/lib/correctionAdoption.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(moduleValue,moduleValue.exports);
const {createCorrectionAdoption}=moduleValue.exports;
const pending=(id='r')=>({id,book_id:'b',generation_id:'old',retake_queue:{status:'queued'}});
const applied=(id='r')=>({...pending(id),retake_queue:{status:'applied',published_generation_id:'new',affected_segment_ids:['s'],affected_source_ids:['p']}});
function setup(){
  const manager=createCorrectionAdoption();let current={bookId:'b',generationId:'old',epoch:0};const adopted=[],offered=[];
  const services={selection:()=>current,load:async()=>({book:{id:'b'},generation:{id:'new',book_id:'b'}}),
    adopt:(book,generation,publication)=>{adopted.push({book,generation,publication});current={...current,generationId:generation.id};return true;},offer:value=>offered.push(value)};
  return {manager,services,adopted,offered,set:value=>{current=value;}};
}
test('pending canonical notes rehydrate without opening feedback modal; coalesced publication adopts once',async()=>{
  const h=setup();await h.manager.receive([pending(),pending('r2')],h.services);
  assert.deepEqual(h.manager.bookIds(),['b']);await h.manager.receive([applied(),applied('r2')],h.services);
  assert.equal(h.adopted.length,1);assert.deepEqual(h.adopted[0].publication.affectedSegmentIds,['s']);
  await h.manager.receive([applied(),applied('r2')],h.services);assert.equal(h.adopted.length,1);assert.deepEqual(h.manager.bookIds(),[]);
});
test('subsequent book or generation navigation offers completed audio without stealing focus',async()=>{
  for(const changed of [{bookId:'other',generationId:'different',epoch:1},{bookId:'b',generationId:'other',epoch:1},{bookId:'b',generationId:'old',epoch:2}]){
    const h=setup();await h.manager.receive([pending()],h.services);h.set(changed);
    await h.manager.receive([applied()],h.services);assert.equal(h.adopted.length,0);assert.equal(h.offered.length,1);
  }
});
test('navigation during publication fetch is checked again before any playback change',async()=>{
  const h=setup();await h.manager.receive([pending()],h.services);
  h.services.load=async()=>{h.set({bookId:'b',generationId:'different',epoch:1});return {book:{id:'b'},generation:{id:'new',book_id:'b'}};};
  await h.manager.receive([applied()],h.services);assert.equal(h.adopted.length,0);assert.equal(h.offered.length,1);
});
test('historical applied receipts do not interrupt an unrelated listening session',async()=>{
  const h=setup();await h.manager.receive([{...applied(),generation_id:'unrelated'}],h.services);assert.equal(h.adopted.length,0);assert.equal(h.offered.length,0);
});
test('first mount seeing already delivered matching generation offers without autoplay',async()=>{
  const h=setup();await h.manager.receive([applied()],h.services);assert.equal(h.adopted.length,0);assert.equal(h.offered.length,1);
});
test('fast publication before report POST returns still adopts its trusted submitted request',async()=>{
  const h=setup();h.manager.watch(applied(),h.services.selection(),true);await h.manager.receive([applied()],h.services);
  assert.equal(h.adopted.length,1);assert.equal(h.offered.length,0);
});
test('stale original report is tracked when queue targets the currently selected generation',async()=>{
  const h=setup();await h.manager.receive([{...pending(),generation_id:'historic',retake_queue:{status:'queued',origin_generation_ids:['old']}}],h.services);
  await h.manager.receive([applied()],h.services);assert.equal(h.adopted.length,1);
});
test('missing playable source leaves current audio intact and offers explicit selection',async()=>{
  const h=setup();await h.manager.receive([pending()],h.services);h.services.adopt=()=>false;
  await h.manager.receive([applied()],h.services);assert.equal(h.offered.length,1);
});
test('request saved after navigation retains the original submission epoch',async()=>{
  const h=setup();const original=h.services.selection();h.set({bookId:'other',generationId:'different',epoch:1});
  h.manager.watch(pending(),original);await h.manager.receive([applied()],h.services);
  assert.equal(h.adopted.length,0);assert.equal(h.offered.length,1);
});
