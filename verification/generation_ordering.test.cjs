const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const appPath = path.resolve(__dirname, '../src/App.tsx');
const source = fs.readFileSync(appPath, 'utf8');
const tree = ts.createSourceFile(appPath, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const modulePath = path.resolve(__dirname, '../src/lib/readerSelection.ts');
const selectionModule = {exports: {}};
new Function('module', 'exports', ts.transpileModule(fs.readFileSync(modulePath, 'utf8'), {
  compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS},
}).outputText)(selectionModule, selectionModule.exports);
const {acceptGenerationRefresh, acceptGenerationPoll} = selectionModule.exports;
const i18nModule={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/i18n.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(i18nModule,i18nModule.exports);
const t=i18nModule.exports.translator('en');

function find(predicate) {
  let found;
  function visit(node) {
    if (found) return;
    if (predicate(node)) { found = node; return; }
    ts.forEachChild(node, visit);
  }
  visit(tree);
  assert.ok(found, 'Expected application callback exists');
  return found;
}

function evaluate(node, environment) {
  environment = {t, pollResult: () => {}, ...environment};
  const code = ts.transpileModule('return (' + node.getText(tree) + ')', {
    compilerOptions: {target: ts.ScriptTarget.ES2022},
  }).outputText;
  return new Function(...Object.keys(environment), code)(...Object.values(environment));
}

function variable(name, environment) {
  return evaluate(find(node => ts.isVariableDeclaration(node) && node.name.getText(tree) === name).initializer, environment);
}

function fixture(revision = 10) {
  return {id: 'generation', book_id: 'book', revision, status: 'rendering', exports: [], segments: [
    {id: 's0', ordinal: 0, chapter_id: 'completed', status: 'ready', audio_hash: 'audio0'},
    {id: 's1', ordinal: 1, chapter_id: 'working', status: 'pending'},
    {id: 's2', ordinal: 2, chapter_id: 'working', status: 'pending'},
  ]};
}

const settle = () => new Promise(resolve => setImmediate(resolve));
const ready = segment => ({...segment, status: 'ready', audio_hash: 'audio-' + segment.id});

function harness(generation, api) {
  let current = generation;
  const timers = [];
  const errors = [];
  const selectionEpoch = {current: 1};
  const setGeneration = update => { current = typeof update === 'function' ? update(current) : update; };
  const environment = {generation, selectionEpoch, api, setGeneration, acceptGenerationPoll,
    setError: reason => errors.push(reason), errorText: String, active: status => status === 'rendering',
    setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout: () => {},
  };
  const effect = find(node => ts.isCallExpression(node) && node.expression.getText(tree) === 'useEffect'
    && node.arguments[0]?.getText(tree).includes("'generation_poll'"));
  const dispose = evaluate(effect.arguments[0], environment)();
  return {current: () => current, setGeneration, selectionEpoch, dispose, errors,
    poll: async () => { assert.ok(timers.length); await timers.shift()(); }};
}

test('full generation refresh rejects older revisions without changing selection', () => {
  const current = fixture(12);
  assert.equal(acceptGenerationRefresh(current, fixture(11)), current);
  assert.equal(acceptGenerationRefresh(current, {...fixture(20), id: 'other'}), current);
  assert.equal(acceptGenerationRefresh(current, {...fixture(20), book_id: 'other'}), current);
  assert.equal(acceptGenerationRefresh(null, fixture(20)), null);
  const fresh = fixture(13);
  assert.equal(acceptGenerationRefresh(current, fresh), fresh);
});

test('stale polling cannot restore removed passages or rewind generation metadata', () => {
  const current = {...fixture(12), segments: [ready(fixture().segments[0])], status: 'ready', exports: [{id: 'new-export'}]};
  const old = {...fixture(11), segments: [fixture().segments[1]]};
  assert.equal(acceptGenerationPoll(current, old, false, ['s0']), current);
  assert.equal(acceptGenerationPoll(current, old, true, []), current);
});

test('retired generation polling adopts its same-book current alias, including a lower revision', async () => {
  const previous=fixture(50);
  const current={...fixture(3),id:'current-output',replaces_generation_id:previous.id};
  const h=harness(previous,async()=>({id:current.id,revision:3,generation:current,full:true,removed_segment_ids:[]}));
  await h.poll();
  assert.equal(h.current(),current);
  h.dispose();
});

test('an alias response cannot redirect polling to another book or an unrelated output', async () => {
  for(const bad of [{...fixture(3),id:'foreign',book_id:'another-book',replaces_generation_id:'generation'},
                   {...fixture(3),id:'unrelated'}]){
    const previous=fixture(50);
    const h=harness(previous,async()=>({id:bad.id,revision:3,generation:bad,full:true,removed_segment_ids:[]}));
    await h.poll();
    assert.equal(h.current(),previous);
    h.dispose();
  }
});

test('newer delta merges updates, additions and removals while retaining untouched audio', () => {
  const current = fixture(12);
  const next = {...current, revision: 15, status: 'ready', segments: [ready(current.segments[1]),
    {id: 's3', ordinal: 3, status: 'ready'}]};
  const merged = acceptGenerationPoll(current, next, false, ['s2']);
  assert.equal(merged.revision, 15);
  assert.equal(merged.status, 'ready');
  assert.deepEqual(merged.segments.map(segment => segment.id), ['s0', 's1', 's3']);
  assert.equal(merged.segments[0], current.segments[0]);
  assert.equal(merged.segments[1].status, 'ready');
});

test('delayed export refresh cannot undo a newer acknowledged polling update', async () => {
  const generation = fixture();
  const exported = {id: 'export', format: 'mp3', file: 'export.mp3'};
  const oldFull = {...generation, revision: 11, exports: [exported]};
  const delta = {...oldFull, revision: 12, segments: [ready(generation.segments[1])]};
  let resolveFull;
  const requests = [];
  const api = async (op, data) => {
    if (op === 'export') return exported;
    if (op === 'generation') return new Promise(resolve => { resolveFull = resolve; });
    if (op === 'generation_poll') {
      requests.push(data.revision);
      return {id: generation.id, revision: 12, generation: requests.length === 1 ? delta : null,
        full: false, removed_segment_ids: []};
    }
    throw new Error('Unexpected operation ' + op);
  };
  const h = harness(generation, api);
  const exportAudio = variable('exportAudio', {run: (_label, fn) => fn(), generation,
    book: {id: 'book', title: 'Book'}, selectionEpoch: h.selectionEpoch, api,
    saveExport: async () => true, setNotice: () => {}, setExporting: () => {}, setGeneration: h.setGeneration, acceptGenerationRefresh});
  const task = exportAudio('mp3', ['completed']);
  await settle();
  assert.equal(typeof resolveFull, 'function');
  await h.poll();
  resolveFull(oldFull);
  await task;
  await h.poll();
  assert.deepEqual(requests, [10, 12]);
  assert.equal(h.current().revision, 12);
  assert.equal(h.current().segments[1].status, 'ready');
  assert.deepEqual(h.current().exports, [exported]);
  assert.deepEqual(h.errors, []);
  h.dispose();
});

test('polling rejected behind a foreground snapshot retains a safe cursor for later changes', async () => {
  const generation = fixture();
  const fresh = {...generation, revision: 12, segments: generation.segments.map(segment => segment.id === 's1' ? ready(segment) : segment)};
  let resolvePoll;
  const requests = [];
  const api = async (_op, data) => {
    requests.push(data.revision);
    if (requests.length === 1) return new Promise(resolve => { resolvePoll = resolve; });
    return {id: generation.id, revision: 13, generation: {...fresh, revision: 13, segments: [ready(generation.segments[2])]},
      full: false, removed_segment_ids: []};
  };
  const h = harness(generation, api);
  const pending = h.poll();
  h.setGeneration(current => acceptGenerationRefresh(current, fresh));
  resolvePoll({id: generation.id, revision: 11, generation: {...generation, revision: 11},
    full: true, removed_segment_ids: []});
  await pending;
  assert.equal(h.current(), fresh);
  await h.poll();
  assert.deepEqual(requests, [10, 11]);
  assert.equal(h.current().revision, 13);
  assert.deepEqual(h.current().segments.map(segment => segment.status), ['ready', 'ready', 'ready']);
  assert.deepEqual(h.errors, []);
  h.dispose();
});

test('delayed resume response cannot replace a newer generation snapshot', async () => {
  const generation = {...fixture(), status: 'paused'};
  const current = {...fixture(12), segments: fixture().segments.map(ready)};
  let selected = generation;
  let resolveResume;
  const api = () => new Promise(resolve => { resolveResume = resolve; });
  let task;
  variable('resumePreparation', {run: (_label, fn) => { task = fn(); return task; },
    selectionEpoch: {current: 1}, api, acceptGenerationRefresh,
    setGeneration: update => { selected = typeof update === 'function' ? update(selected) : update; }})(generation.id);
  selected = current;
  resolveResume({...fixture(11), status: 'queued'});
  await task;
  assert.equal(selected, current);
});

test('reopening the selected generation cannot rewind a poll started after selection', async () => {
  const generation = fixture();
  let resolveFull;
  const api = async op => {
    if (op === 'generation') return new Promise(resolve => { resolveFull = resolve; });
    if (op === 'book') return {id: 'book'};
    if (op === 'generation_poll') return {id: generation.id, revision: 12,
      generation: {...generation, revision: 12, segments: [ready(generation.segments[1])]},
      full: false, removed_segment_ids: []};
    throw new Error('Unexpected operation ' + op);
  };
  const h = harness(generation, api);
  const selectGeneration = variable('selectGeneration', {book: {id: 'book'},
    flushReading: {current: () => {}}, selectionEpoch: h.selectionEpoch,
    afterPlaybackSaved: selectionModule.exports.afterPlaybackSaved, player: {stop: async () => {}},
    api, manualReading: {current: false}, setBook: () => {}, setGeneration: h.setGeneration,
    acceptGenerationRefresh, setBrowsing: () => {}});
  const selecting = selectGeneration(generation.id);
  await settle();
  assert.equal(h.selectionEpoch.current, 2);
  await h.poll();
  assert.equal(h.current().revision, 12);
  resolveFull({...generation, revision: 11});
  await selecting;
  assert.equal(h.current().revision, 12);
  assert.equal(h.current().segments[1].status, 'ready');
  assert.deepEqual(h.errors, []);
  h.dispose();
});

test('intentional generation selection accepts a different generation regardless of its revision', async () => {
  let selected = fixture(20);
  const next = {...fixture(1), id: 'other-generation'};
  const selectGeneration = variable('selectGeneration', {book: {id: 'book'},
    flushReading: {current: () => {}}, selectionEpoch: {current: 1},
    afterPlaybackSaved: selectionModule.exports.afterPlaybackSaved, player: {stop: async () => {}},
    api: async op => op === 'generation' ? next : {id: 'book'}, manualReading: {current: false},
    setBook: () => {}, setGeneration: update => { selected = typeof update === 'function' ? update(selected) : update; },
    acceptGenerationRefresh, setBrowsing: () => {}});
  await selectGeneration(next.id);
  assert.equal(selected, next);
});
