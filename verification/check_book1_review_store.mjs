import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';

const storage=()=>{const data=new Map();return {getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};};
const localStorage=storage(),sessionStorage=storage();
let canonical={schema:1,candidates_sha256:'fixture',updated_at:'2026-09-28T00:00:00Z',decisions:{a:{corrected_mood:'neutral',_revision:2}}},respond;
const context=vm.createContext({structuredClone,localStorage,sessionStorage,crypto:webcrypto,fetch:async(path,options)=>{
  if(options?.method!=='POST')return {ok:true,json:async()=>structuredClone(canonical)};
  return new Promise(resolve=>{respond=data=>resolve({ok:true,json:async()=>data});});
}});
vm.runInContext(await readFile('scripts/book1_review_shared.js','utf8'),context);
const store=vm.runInContext("new Book1ReviewStore({candidates_sha256:'fixture'})",context);
// A stale full snapshot from an old tab must not replace a newer Step 2 revision.
localStorage.setItem(store.key,JSON.stringify({...canonical,updated_at:'2026-09-28T23:00:00Z',decisions:{a:{corrected_mood:'excited'}}}));
await store.load();assert.equal(store.decisions.a.corrected_mood,'neutral');assert.ok(localStorage.getItem(store.key+'-legacy-backup'));
// A concurrent server edit and another local edit during the save both survive.
store.decisions.a.corrected_mood='calm';store.stash();
const saving=store.save();await new Promise(resolve=>setTimeout(resolve,0));
store.decisions.a.comment='typed during save';store.stash();
canonical.decisions.a={corrected_mood:'calm',bucket_comment:'from another tab',_revision:3};respond({review:structuredClone(canonical)});await saving;
assert.equal(store.decisions.a.comment,'typed during save');assert.equal(store.decisions.a.bucket_comment,'from another tab');
assert.equal(store.changes().a.after.bucket_comment,'from another tab');
// Pending edits survive reload in the same tab.
const restored=vm.runInContext("new Book1ReviewStore({candidates_sha256:'fixture'})",context);await restored.load();
assert.equal(restored.decisions.a.comment,'typed during save');
// Accept server conflict labels while preserving independent unsaved notes.
restored.decisions.a.corrected_mood='excited';restored.conflicts=[{id:'a',fields:['corrected_mood']}];
canonical.decisions.a={...canonical.decisions.a,corrected_mood:'warning',_revision:4};
await restored.resolveConflicts(false);assert.equal(restored.decisions.a.corrected_mood,'warning');assert.equal(restored.decisions.a.comment,'typed during save');
restored.decisions.a.corrected_mood='relieved';restored.conflicts=[{id:'a',fields:['corrected_mood']}];
canonical.decisions.a.corrected_mood='tired';await restored.resolveConflicts(true);assert.equal(restored.decisions.a.corrected_mood,'relieved');
assert.equal(restored.base.a.corrected_mood,'tired');
console.log('Legacy snapshot safety, in-flight edits, pending reload, and conflict choices passed.');
