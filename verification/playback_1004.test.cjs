const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

function load(file) {
  const filename = path.resolve(__dirname, '..', file);
  const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  const module = {exports: {}};
  new Function('require', 'module', 'exports', source)(name => name.startsWith('./') && fs.existsSync(path.resolve(path.dirname(filename), name + '.ts')) ? load(path.relative(path.resolve(__dirname, '..'), path.resolve(path.dirname(filename), name + '.ts'))) : require(name), module, module.exports);
  return module.exports;
}
const {createPlaybackController} = load('src/lib/playback.ts');
const {copyWhenReady} = load('src/lib/clipboard.ts');
const {bindDropGuard} = load('src/lib/importFiles.ts');
const {bindNativeBooks} = load('src/lib/nativeBooks.ts');
const {createPreparationPause, healthSettling} = load('src/lib/preparationPause.ts');
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };

function audioElement(name = 'main') {
  const target = new EventTarget();
  Object.assign(target, {name, src: '', currentTime: 0, duration: 2, paused: true, ended: false, playbackRate: 1, volume: 1, plays: 0, preload: ''});
  target.removeAttribute = attribute => { if (attribute === 'src') target.src = ''; };
  target.hasAttribute = attribute => attribute === 'src' && Boolean(target.src);
  target.pause = () => { const playing = !target.paused; target.paused = true; if (playing) target.dispatchEvent(new Event('pause')); };
  target.play = async () => { target.ended = false; target.paused = false; target.plays++; target.dispatchEvent(new Event('play')); };
  target.load = () => { target.ended = false; if (target.src) queueMicrotask(() => target.dispatchEvent(new Event('loadedmetadata'))); };
  return target;
}
const segment = (id, overrides = {}) => ({id, chapter_id: 'c1', source_id: 'p-' + id, start: 0, end: 10, text: 'text', spoken: 'text', speaker: 'narrator',
  status: 'ready', audio_hash: 'hash-' + id, duration: 2, cues: [], warning: '', ...overrides});
function harness({gapless = false, segments = [segment('s1'), segment('s2'), segment('s3')], status = 'ready'} = {}) {
  const main = audioElement(), elements = [main], saved = [], loads = [], history = [];
  const controller = createPlaybackController(main, {
    loadAudio: async hash => { loads.push(hash); return new Blob([hash]); },
    savePosition: async (_book, value) => { saved.push(value); },
    notify: state => history.push(state),
    createUrl: blob => 'blob:' + loads.length, revokeUrl: () => {},
    ...(gapless ? {createAudio: () => { const element = audioElement('standby-' + elements.length); elements.push(element); return element; }} : {}),
  });
  const book = {id: 'b1', position: null};
  const generation = {id: 'g1', book_id: 'b1', status, error: '', segments};
  controller.update(book, generation);
  const playing = () => elements.find(element => !element.paused);
  const end = element => { element.ended = true; element.paused = true; element.dispatchEvent(new Event('ended')); };
  return {controller, main, elements, saved, loads, state: () => history.at(-1), book, generation, playing, end};
}

test('a cast preview never saves its passage as the listening position and restores the previous passage afterwards', async () => {
  const h = harness();
  await settle();
  h.controller.select('s2', true); await settle();
  h.main.currentTime = 1.25;
  const before = h.saved.length;
  h.controller.preview('s3'); await settle();
  assert.equal(h.state().segment.id, 's3'); assert.equal(h.state().previewing, true); assert.equal(h.state().playing, true);
  assert.deepEqual(h.saved.slice(before).map(item => [item.segment_id, item.offset]), [['s2', 1.25]], 'only the real position is saved, before the preview');
  h.main.currentTime = 1; h.main.dispatchEvent(new Event('timeupdate')); h.controller.pause();
  h.controller.toggle(); await settle();
  h.end(h.main); await settle();
  assert.equal(h.state().previewing, false); assert.equal(h.state().segment.id, 's2'); assert.equal(h.state().playing, false);
  assert.equal(h.main.currentTime, 1.25);
  assert.ok(h.saved.every(item => item.segment_id !== 's3'), 'the previewed passage is never persisted');
  h.controller.dispose();
});

test('explicit listening during a preview takes over without persisting the previewed passage', async () => {
  const h = harness();
  await settle();
  h.controller.preview('s3'); await settle();
  h.controller.select('s2', true); await settle();
  assert.equal(h.state().previewing, false);
  assert.ok(h.saved.every(item => item.segment_id !== 's3'));
  assert.equal(h.saved.at(-1).segment_id, 's2');
  h.end(h.main); await settle();
  assert.equal(h.state().segment.id, 's3', 'normal playback continues to the next passage');
  h.controller.dispose();
});

test('a pending passage in a generation waiting for cast review explains the review step instead of spinning', async () => {
  const h = harness({segments: [segment('s1', {status: 'pending', audio_hash: null, duration: 0})], status: 'review'});
  h.controller.toggle(); await settle();
  assert.equal(h.state().waiting, false);
  assert.equal(h.state().error, 'This passage is not ready. Review the cast and start narration to continue.');
  h.controller.dispose();
});

test('continuous playback switches to the prefetched next passage on ended without loading it again', async () => {
  const h = harness({gapless: true});
  await settle();
  h.controller.toggle(); await settle();
  assert.deepEqual(h.loads, ['hash-s1', 'hash-s2', 'hash-s3'], 'the following ready passages are fetched while the current one plays');
  const standby = h.elements[1];
  assert.equal(standby.preload, 'auto'); assert.ok(standby.src);
  h.end(h.main); await settle();
  assert.equal(h.state().segment.id, 's2'); assert.equal(h.state().playing, true);
  assert.equal(standby.plays, 1); assert.equal(h.playing(), standby);
  assert.equal(h.elements.length, 2, 'two audio elements alternate');
  assert.deepEqual(h.loads, ['hash-s1', 'hash-s2', 'hash-s3'], 'nothing was loaded twice');
  assert.equal(h.saved.at(-1).segment_id, 's2');
  standby.currentTime = 1.5; standby.dispatchEvent(new Event('timeupdate'));
  h.end(standby); await settle();
  assert.equal(h.state().segment.id, 's3'); assert.equal(h.playing(), h.main, 'the released element is reused as the next standby');
  h.controller.dispose();
});

test('a stale prefetch is dropped when the listener jumps elsewhere, and unready passages still load on demand', async () => {
  const h = harness({gapless: true, segments: [segment('s1'), segment('s2', {status: 'pending', audio_hash: null, duration: 0}), segment('s3')]});
  await settle();
  h.controller.toggle(); await settle();
  assert.deepEqual(h.loads, ['hash-s1'], 'nothing is prefetched for a passage that is not ready');
  h.controller.select('s3', true); await settle();
  assert.equal(h.state().segment.id, 's3'); assert.equal(h.state().playing, true);
  h.controller.update(h.book, {...h.generation, segments: [segment('s1'), segment('s2'), segment('s3')]}); await settle();
  h.controller.select('s1', true); await settle();
  assert.equal(h.loads.filter(hash => hash === 'hash-s1').length, 1, 'the played passage comes from the cache');
  assert.equal(h.loads.at(-1), 'hash-s2');
  h.controller.update(h.book, {...h.generation, segments: [segment('s1'), segment('s2', {audio_hash: 'hash-new'}), segment('s3')]}); await settle();
  assert.equal(h.loads.at(-1), 'hash-new', 'a changed next passage replaces the outdated standby');
  h.end(h.playing()); await settle();
  assert.equal(h.state().segment.audio_hash, 'hash-new'); assert.equal(h.state().playing, true);
  h.controller.dispose();
});

test('previews do not prefetch continuous playback', async () => {
  const h = harness({gapless: true});
  h.controller.preview('s1'); await settle();
  assert.deepEqual([...new Set(h.loads)], ['hash-s1']);
  h.controller.dispose();
});

test('the clipboard write starts inside the click and receives the brief later', async () => {
  const calls = [];
  let release;
  const text = new Promise(resolve => { release = resolve; });
  class Item { constructor(items) { this.items = items; } }
  const clipboard = {write: async ([item]) => { calls.push('write'); calls.push(await (await item.items['text/plain']).text()); }, writeText: async value => calls.push('text:' + value)};
  const copied = copyWhenReady(text, clipboard, Item);
  assert.deepEqual(calls, ['write'], 'write is called synchronously, before the brief exists');
  release('{"brief":1}'); await copied;
  assert.deepEqual(calls, ['write', '{"brief":1}']);
  const fallback = [];
  await copyWhenReady(Promise.resolve('plain'), {writeText: async value => fallback.push(value)}, undefined);
  await copyWhenReady(Promise.resolve('denied'), {write: async () => { throw new Error('NotAllowedError'); }, writeText: async value => fallback.push(value)}, Item);
  assert.deepEqual(fallback, ['plain', 'denied']);
  await assert.rejects(copyWhenReady(Promise.reject(new Error('brief failed')), clipboard, Item), /brief failed/);
});

test('the window drop guard blocks navigation for every drag, but keeps text drops into inputs', () => {
  const window = new EventTarget();
  globalThis.Element ??= class Element { closest() { return null; } };
  const input = Object.assign(Object.create(globalThis.Element.prototype), {closest: selector => selector.includes('textarea') ? {} : null});
  const dispose = bindDropGuard(window);
  const drag = (type, types, target = null) => { const event = new Event(type, {cancelable: true}); Object.defineProperty(event, 'dataTransfer', {value: {types, dropEffect: 'copy'}}); Object.defineProperty(event, 'target', {value: target}); window.dispatchEvent(event); return event; };
  const link = drag('dragover', ['text/uri-list']);
  assert.equal(link.defaultPrevented, true); assert.equal(link.dataTransfer.dropEffect, 'none');
  assert.equal(drag('drop', ['text/uri-list']).defaultPrevented, true);
  assert.equal(drag('drop', ['Files']).defaultPrevented, true, 'files dropped before the library is ready are not opened by the webview');
  assert.equal(drag('drop', ['text/plain'], input).defaultPrevented, false, 'text can still be dropped into an input');
  assert.equal(drag('drop', ['Files'], input).defaultPrevented, true);
  dispose();
  assert.equal(drag('drop', ['text/uri-list']).defaultPrevented, false);
});

test('OS-opened books report a visible queued state while another operation runs', async () => {
  let busy = true, notify;
  const states = [], opened = [];
  const timers = [];
  const realTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (fn, delay) => { timers.push(fn); return timers.length; };
  try {
    const dispose = bindNativeBooks({listen: async callback => { notify = callback; return () => {}; },
      process: async () => { if (busy) return false; opened.push('book'); return true; }, error: () => {}, queued: value => states.push(value)});
    await settle(); await timers.shift()(); await settle();
    assert.deepEqual(states, [true]);
    await timers.shift()(); await settle();
    assert.deepEqual(states, [true], 'retries while busy do not repeat the queued notice');
    busy = false; await timers.shift()(); await settle();
    assert.deepEqual(states, [true, false]); assert.deepEqual(opened, ['book']);
    notify(); await timers.shift()(); await settle();
    assert.deepEqual(states, [true, false]);
    dispose();
  } finally { globalThis.setTimeout = realTimeout; }
});

test('health is re-read after a terminal status while the job still reports this generation', () => {
  for (const status of ['failed', 'paused', 'ready', 'review']) assert.equal(healthSettling({id: 'g', status}, {active: 'g'}), true, status);
  assert.equal(healthSettling({id: 'g', status: 'rendering'}, {active: 'g'}), false);
  assert.equal(healthSettling({id: 'g', status: 'failed'}, {active: null}), false);
  assert.equal(healthSettling({id: 'g', status: 'failed'}, {active: 'other'}), false);
  assert.equal(healthSettling(null, {active: 'g'}), false);
});

test('a pause confirmation finishes when casting stops at the review gate', async () => {
  const pending = [];
  const controller = createPreparationPause({pause: async () => ({requested: true}), refresh: async () => ({generation: {id: 'g', status: 'review'}, health: {active: null}}),
    pending: value => pending.push(value), refreshError: () => {}}, 5000, 10);
  assert.equal(await controller.request('g'), true); await settle();
  assert.deepEqual(pending, ['g', null]);
  controller.dispose();
});
