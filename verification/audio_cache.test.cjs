const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

function load(file) {
  const filename = path.resolve(__dirname, '..', file);
  const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  const module = {exports: {}};
  new Function('require', 'module', 'exports', source)(name => name.startsWith('./') ? load(path.join(path.dirname(file), name + '.ts')) : require(name), module, module.exports);
  return module.exports;
}
const {createPlaybackController} = load('src/lib/playback.ts');
const {createAudioCache} = load('src/lib/audioCache.ts');
const {bindMediaSession, describeMediaSession} = load('src/lib/mediaSession.ts');
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

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
const segment = (id, overrides = {}) => ({id, chapter_id: 'c1', source_id: 'p-' + id, start: 0, end: 10, text: 't', spoken: 't', speaker: 'narrator',
  status: 'ready', audio_hash: 'hash-' + id, duration: 2, cues: [], ...overrides});
const many = (count, overrides = {}) => Array.from({length: count}, (_, i) => segment('s' + (i + 1), overrides));

function harness({segments = many(3), loader, gapless = false, ...options} = {}) {
  const main = audioElement(), elements = [main], saved = [], loads = [], signals = [], revoked = [], created = [], history = [];
  const controller = createPlaybackController(main, {
    loadAudio: (hash, signal, progress) => {
      loads.push(hash); signals.push([hash, signal]);
      return loader ? loader(hash, signal, progress) : Promise.resolve(new Blob([hash]));
    },
    savePosition: async (_book, value) => { saved.push(value); },
    notify: state => history.push(state),
    createUrl: blob => { const url = 'blob:' + created.length; created.push(url); return url; },
    revokeUrl: url => revoked.push(url),
    saveData: () => false,
    ...(gapless ? {createAudio: () => { const element = audioElement('standby-' + elements.length); elements.push(element); return element; }} : {}),
    ...options,
  });
  const book = {id: 'b1', position: null};
  const generation = {id: 'g1', book_id: 'b1', status: 'ready', error: '', segments};
  controller.update(book, generation);
  const playing = () => elements.find(element => !element.paused);
  const end = element => { element.ended = true; element.paused = true; element.dispatchEvent(new Event('ended')); };
  return {controller, main, elements, saved, loads, signals, revoked, created, book, generation, playing, end, state: () => history.at(-1)};
}

test('replaying an earlier passage is served from the cache without a second fetch', async () => {
  const h = harness({segments: many(3)});
  await settle();
  h.controller.select('s2', true); await settle();
  const before = h.loads.length;
  h.controller.select('s1', true); await settle();
  assert.equal(h.state().segment.id, 's1'); assert.equal(h.state().playing, true);
  h.controller.select('s2', true); await settle();
  assert.equal(h.loads.length, before, 'no network for cached passages');
  assert.equal(h.loads.filter(hash => hash === 'hash-s1').length, 1);
  assert.deepEqual(h.revoked, [], 'unloading never revokes cached URLs');
  h.controller.dispose();
});

test('skipping back across a passage boundary reuses the cached audio', async () => {
  const h = harness({segments: many(3)});
  await settle();
  h.controller.select('s3', true); await settle();
  const before = h.loads.length;
  h.controller.skip(-1); await settle();
  assert.equal(h.state().segment.id, 's2');
  assert.equal(h.loads.length, before);
  h.controller.dispose();
});

test('selecting the passage held by the standby element uses it without any network', async () => {
  const h = harness({segments: many(4), gapless: true});
  await settle();
  h.controller.toggle(); await settle();
  const standby = h.elements[1];
  assert.ok(standby.src, 'next passage is attached to the standby element');
  const before = h.loads.length;
  h.controller.select('s2', true, .5); await settle();
  assert.equal(h.playing(), standby); assert.equal(standby.currentTime, .5);
  assert.equal(h.state().segment.id, 's2'); assert.equal(h.loads.length, before);
  h.controller.dispose();
});

test('prefetch window follows playback order up to 12 passages and about three minutes', async () => {
  const h = harness({segments: many(30, {duration: 5})});
  await settle();
  assert.deepEqual(h.loads, Array.from({length: 13}, (_, i) => 'hash-s' + (i + 1)));
  h.controller.dispose();
  const long = harness({segments: many(30, {duration: 70})});
  await settle();
  assert.equal(long.loads.length, 1 + 3, 'three passages of 70 s reach the three-minute horizon');
  long.controller.dispose();
});

test('prefetch never runs more than two downloads and waits behind the current passage', async () => {
  const pending = new Map(); let active = 0, peak = 0;
  const h = harness({segments: many(8), loader: hash => new Promise(resolve => {
    active++; peak = Math.max(peak, active); pending.set(hash, () => { active--; resolve(new Blob([hash])); });
  })});
  await settle();
  assert.deepEqual(h.loads, ['hash-s1'], 'only the current passage is requested first');
  pending.get('hash-s1')(); await settle();
  assert.deepEqual(h.loads, ['hash-s1', 'hash-s2', 'hash-s3']);
  pending.get('hash-s2')(); await settle();
  assert.equal(h.loads.length, 4);
  assert.ok(peak <= 2, 'peak concurrency ' + peak);
  h.controller.dispose();
});

test('prefetch stops at pending passages and skips held ones like playback does', async () => {
  const h = harness({segments: [segment('s1'), segment('s2'), segment('s3', {status: 'pending', audio_hash: null, duration: 0}), segment('s4')]});
  await settle();
  assert.deepEqual(h.loads, ['hash-s1', 'hash-s2']);
  h.controller.dispose();
  const held = harness({segments: [segment('s1'), segment('s2', {status: 'review', audio_hash: null}), segment('s3'), segment('s4', {status: 'pending', audio_hash: null}), segment('s5')]});
  await settle();
  assert.deepEqual(held.loads, ['hash-s1', 'hash-s3']);
  held.controller.dispose();
});

test('data saver keeps only the next passage', async () => {
  const h = harness({segments: many(6), saveData: () => true});
  await settle();
  assert.deepEqual(h.loads, ['hash-s1', 'hash-s2']);
  h.controller.dispose();
});

test('a changed audio hash is fetched again and the old entry is left to age out', async () => {
  const h = harness({segments: many(2)});
  await settle();
  h.controller.update(h.book, {...h.generation, segments: [segment('s1', {audio_hash: 'hash-new'}), segment('s2')]}); await settle();
  assert.ok(h.loads.includes('hash-new'));
  assert.equal(h.state().segment.audio_hash, 'hash-new');
  h.controller.dispose();
});

test('a prefetched download is not aborted when playback jumps away from it', async () => {
  const pending = new Map();
  const h = harness({segments: many(4), loader: hash => hash === 'hash-s1' ? Promise.resolve(new Blob(['one'])) : new Promise(resolve => pending.set(hash, resolve))});
  await settle();
  const s2 = h.signals.find(([hash]) => hash === 'hash-s2')[1];
  h.controller.select('s2', true); await settle();
  h.controller.select('s1', true); await settle();
  assert.equal(s2.aborted, false, 'still wanted by the prefetch window');
  pending.get('hash-s2')(new Blob(['two'])); await settle();
  const before = h.loads.length;
  h.controller.select('s2', true); await settle();
  assert.equal(h.state().playing, true); assert.equal(h.loads.filter(hash => hash === 'hash-s2').length, 1);
  assert.equal(h.loads.length, before);
  h.controller.dispose();
});

test('an abandoned passage nobody else wants is cancelled', async () => {
  const h = harness({segments: many(2), loader: () => new Promise(() => {})});
  await settle();
  const signal = h.signals[0][1];
  h.controller.select('s2', true); await settle();
  assert.equal(signal.aborted, true);
  h.controller.dispose();
});

test('stall timeout fires only when no bytes arrive', async () => {
  const stalled = harness({segments: many(1), loadTimeoutMs: 40, loader: (_hash, _signal, progress) => { progress(); return new Promise(() => {}); }});
  stalled.controller.toggle(); await sleep(120);
  assert.match(stalled.state().error, /timed out/);
  assert.equal(stalled.signals[0][1].aborted, true);
  stalled.controller.dispose();
  const slow = harness({segments: many(1), loadTimeoutMs: 40, loader: (hash, _signal, progress) => new Promise(resolve => {
    let ticks = 0;
    const timer = setInterval(() => { progress(); if (++ticks === 8) { clearInterval(timer); resolve(new Blob([hash])); } }, 20);
  })});
  slow.controller.toggle(); await sleep(260);
  assert.equal(slow.state().error, ''); assert.equal(slow.state().playing, true, 'a slow but progressing download completes past the stall window');
  slow.controller.dispose();
});

test('cache evicts least recently used unpinned entries over budget and keeps pinned and recent ones', async () => {
  const revoked = [];
  const cache = createAudioCache({load: async () => new Blob([new Uint8Array(100)]), createUrl: () => 'u' + Math.random(), revokeUrl: url => revoked.push(url), budgetBytes: 250});
  for (const hash of ['a', 'b']) await cache.acquire(hash).ready;
  cache.pin('a'); cache.keep(['b']);
  await cache.acquire('c').ready; await settle();
  assert.deepEqual(cache.stats().hashes.sort(), ['a', 'b'], 'unprotected c is evicted before kept b; pinned a stays');
  assert.equal(revoked.length, 1);
  await cache.acquire('d').ready; await settle();
  assert.ok(cache.stats().hashes.includes('a'), 'pinned entry is never evicted');
  assert.ok(cache.stats().bytes <= 300);
  cache.dispose();
});

test('shared fetch survives one consumer leaving and is cancelled with the last', async () => {
  let calls = 0, aborted = false, resolve;
  const cache = createAudioCache({load: (_hash, signal) => { calls++; signal.addEventListener('abort', () => { aborted = true; }); return new Promise(done => { resolve = done; }); },
    createUrl: () => 'u', revokeUrl: () => {}});
  const first = cache.acquire('a'), second = cache.acquire('a');
  assert.equal(calls, 1);
  first.release(); await settle();
  assert.equal(aborted, false);
  resolve(new Blob(['x']));
  assert.equal(await second.ready, 'u');
  const third = cache.acquire('b'); third.release(); await settle();
  assert.equal(aborted, true);
  cache.dispose();
});

test('media session wires phone controls and reports metadata', () => {
  const handlers = {};
  const target = {mediaSession: {setActionHandler: (name, handler) => { handlers[name] = handler; }, metadata: null, playbackState: 'none'}};
  globalThis.MediaMetadata = class { constructor(value) { Object.assign(this, value); } };
  const calls = [];
  const off = bindMediaSession({play: () => calls.push('play'), pause: () => calls.push('pause'), skip: seconds => calls.push('skip' + seconds), step: direction => calls.push('step' + direction)}, target);
  handlers.play(); handlers.pause(); handlers.seekbackward({}); handlers.seekforward({seekOffset: 30}); handlers.previoustrack(); handlers.nexttrack();
  assert.deepEqual(calls, ['play', 'pause', 'skip-15', 'skip30', 'step-1', 'step1']);
  describeMediaSession({title: 'Chapter', artist: 'Author', album: 'Book'}, true, target);
  assert.equal(target.mediaSession.metadata.title, 'Chapter'); assert.equal(target.mediaSession.playbackState, 'playing');
  off(); assert.equal(handlers.play, null);
  assert.doesNotThrow(() => bindMediaSession({}, {}));
});
