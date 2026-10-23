const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('scripts/voice_family_descriptor_review.js','utf8');

test('queued descriptor changes keep their family and revision after navigation',async()=>{
  const requests=[];
  let server={schema:1,narrator_id:'vlad-rimsky',registry_version:'vlad-rimsky-v1',decisions:{}};
  const context=vm.createContext({document:{querySelector:()=>({textContent:''})},window:{},
    clearTimeout,setTimeout,URL,fetch:async(url,options)=>{
      const request=JSON.parse(options.body);requests.push(request);
      const before=server.decisions[request.family_id]||{};
      assert.equal(request.before_revision,before._revision||0);
      server={...server,decisions:{...server.decisions,[request.family_id]:{
        ...request.decision,_revision:(before._revision||0)+1}}};
      return {ok:true,json:async()=>({review:server})};
    }});
  vm.runInContext(source.slice(0,source.lastIndexOf('\nboot().catch')),context);
  await vm.runInContext(`
    data={narrator_id:'vlad-rimsky',registry_version:'vlad-rimsky-v1'};
    activeFamily='one';
    pendingNote={id:'one',notes:'heavy and dark'};
    flushNotes();
    save({descriptors:{voice_weight:'heavy',voice_depth:'low'}},'one');
    activeFamily='two';
    save({notes:'bright'},'two');
  `,context);
  await vm.runInContext('saveQueue',context);
  assert.deepEqual(requests.map(r=>r.family_id),['one','one','two']);
  assert.equal(server.decisions.one.notes,'heavy and dark');
  assert.equal(server.decisions.one.descriptors.voice_depth,'low');
  assert.equal(server.decisions.two.notes,'bright');
  assert.equal(server.decisions.two.descriptors,undefined);
});

test('unspecified descriptors stay blank rather than becoming implicit verdicts',()=>{
  const context=vm.createContext({document:{querySelector:()=>({})},window:{}});
  vm.runInContext(source.slice(0,source.lastIndexOf('\nboot().catch')),context);
  const rendered=vm.runInContext("selectField('voice_depth','Depth',[['low','Low']],undefined)",context);
  assert.match(rendered,/<option value="" selected>Not described<\/option>/);
});
