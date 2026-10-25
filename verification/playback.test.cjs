const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

function load(file) {
  const filename = path.resolve(__dirname, '..', file);
  const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
    fileName: filename,
  }).outputText;
  const module = {exports: {}};
  new Function('require', 'module', 'exports', source)(name => name.startsWith('./') && fs.existsSync(path.resolve(path.dirname(filename), name + '.ts')) ? load(path.relative(path.resolve(__dirname, '..'), path.resolve(path.dirname(filename), name + '.ts'))) : require(name), module, module.exports);
  return module.exports;
}
const {createPlaybackController} = load('src/lib/playback.ts');
const {visualForPassage} = load('src/lib/visualSelection.ts');
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };

function audioElement() {
  const target = new EventTarget();
  Object.assign(target, {src: '', currentTime: 0, duration: 2, paused: true, ended: false, playbackRate: 1, volume: 1, plays: 0});
  target.removeAttribute = name => { if (name === 'src') target.src = ''; };
  target.hasAttribute = name => name === 'src' && Boolean(target.src);
  target.pause = () => { const playing = !target.paused; target.paused = true; if (playing) target.dispatchEvent(new Event('pause')); };
  target.play = async () => { target.ended = false; target.paused = false; target.plays++; target.dispatchEvent(new Event('play')); };
  target.load = () => { target.ended = false; if (target.src) queueMicrotask(() => target.dispatchEvent(new Event('loadedmetadata'))); };
  return target;
}
function segment(id = 's1', status = 'ready', overrides = {}) {
  return {id, chapter_id: 'c1', source_id: 'p1', start: 0, end: 10, text: 'text', spoken: 'text',
    status, audio_hash: status === 'ready' ? 'hash-' + id : null, duration: status === 'ready' ? 2 : 0, cues: [], ...overrides};
}
function data(segments = [segment()], id = 'g1') {
  return {book: {id: 'b1', position: null}, generation: {id, book_id: 'b1', segments}};
}
function harness(loader = async () => new Blob(['synthetic-media-fixture']), options = {}) {
  const audio = audioElement();
  const history = [], saved = [], loads = [], created = [], revoked = [];
  const controller = createPlaybackController(audio, {
    loadAudio: async (hash, signal) => { loads.push(hash); return loader(hash, signal); },
    savePosition: async (id, value) => { saved.push({id, ...value}); },
    notify: state => history.push(state),
    createUrl: () => { const url = 'blob:' + created.length; created.push(url); return url; },
    revokeUrl: url => revoked.push(url),
    ...options,
  });
  return {controller, audio, history, saved, loads, created, revoked, state: () => history.at(-1)};
}

test('empty library does not enter an endless waiting state', () => {
  const h = harness(); h.controller.update(null, null); h.controller.toggle();
  assert.equal(h.state().waiting, false); assert.equal(h.state().segment, null); h.controller.dispose();
});

test('published correction adopts the exact source, resets changed audio and saves its new generation anchor',async()=>{
  const h=harness();const d=data();h.controller.update(d.book,d.generation);await settle();
  h.controller.toggle();await settle();h.audio.currentTime=1;h.audio.dispatchEvent(new Event('timeupdate'));
  const updated={...d.generation,id:'g2',segments:[segment('new-id','ready',{audio_hash:'corrected'})]};
  assert.equal(h.controller.adoptCorrection(d.book,updated,['s1'],['p1']),true);await settle();
  assert.equal(h.state().segment.id,'new-id');assert.equal(h.state().position,0);assert.equal(h.state().playing,true);
  assert.equal(h.saved.at(-1).generation_id,'g2');assert.equal(h.saved.at(-1).segment_id,'new-id');assert.equal(h.saved.at(-1).offset,0);
  h.controller.dispose();
});

test('unaffected paused passage keeps exact offset when corrected generation is adopted',async()=>{
  const h=harness();const d=data();h.controller.update(d.book,d.generation);await settle();
  h.controller.seek(.8);await settle();
  assert.equal(h.controller.adoptCorrection(d.book,{...d.generation,id:'g2'},['another'],['p1']),true);await settle();
  assert.equal(h.state().playing,false);assert.equal(h.audio.currentTime,.8);assert.equal(h.saved.at(-1).generation_id,'g2');
  assert.equal(h.saved.at(-1).offset,.8);h.controller.dispose();
});

test('correction adoption refuses a missing source instead of jumping to another chapter',async()=>{
  const h=harness();const d=data();h.controller.update(d.book,d.generation);await settle();
  assert.equal(h.controller.adoptCorrection(d.book,{...d.generation,id:'g2',segments:[segment('elsewhere','ready',{source_id:'other'})]},[],[]),false);
  assert.equal(h.state().segment.id,'s1');h.controller.dispose();
});

test('saved generation restores source-bound offset without autoplay', async () => {
  const h = harness(); const d = data();
  d.book.position = {generation_id: 'g1', segment_id: 's1', offset: .75};
  h.controller.update(d.book, d.generation); await settle();
  assert.equal(h.audio.currentTime, .75); assert.equal(h.audio.paused, true); h.controller.dispose();
});

test('polling unchanged generation does not reload or restart audio', async () => {
  const h = harness(); const d = data(); h.controller.update(d.book, d.generation); await settle();
  h.controller.toggle(); await settle(); h.audio.currentTime = 1; h.audio.dispatchEvent(new Event('timeupdate'));
  h.controller.update({...d.book}, JSON.parse(JSON.stringify(d.generation))); await settle();
  assert.equal(h.loads.length, 1); assert.equal(h.audio.currentTime, 1); assert.equal(h.audio.plays, 1); h.controller.dispose();
});

test('analysis splitting a pending source rebinds queued playback', async () => {
  const h = harness(); const d = data([segment('base', 'pending')]);
  h.controller.update(d.book, d.generation); h.controller.toggle(); assert.equal(h.state().waiting, true);
  h.controller.update(d.book, {...d.generation, segments: [segment('split1', 'ready', {end: 5}), segment('split2', 'ready', {start: 5})]});
  await settle(); assert.equal(h.state().segment.id, 'split1'); assert.equal(h.state().playing, true); assert.equal(h.state().waiting, false); h.controller.dispose();
});

test('failed asset load can be retried with Play', async () => {
  let attempt = 0;
  const h = harness(async () => { if (!attempt++) throw new Error('fixture failure'); return new Blob(['tone']); });
  const d = data(); h.controller.update(d.book, d.generation); await settle(); assert.match(h.state().error, /failure/);
  h.controller.toggle(); await settle(); assert.equal(h.state().playing, true); assert.equal(h.loads.length, 2); h.controller.dispose();
});

test('outdated asset completion never replaces newly selected audio', async () => {
  const pending = new Map();
  const h = harness(hash => new Promise(resolve => pending.set(hash, resolve)));
  const d = data([segment('s1'), segment('s2', 'ready', {source_id: 'p2'})]);
  h.controller.update(d.book, d.generation); h.controller.select('s2', true);
  pending.get('hash-s2')(new Blob(['second'])); await settle();
  pending.get('hash-s1')(new Blob(['first'])); await settle();
  assert.equal(h.state().segment.id, 's2'); assert.equal(h.created.length, 1); assert.equal(h.state().playing, true); h.controller.dispose();
});

test('ending a passage waits for the next and resumes when it becomes ready', async () => {
  const h = harness(); const d = data([segment('s1'), segment('s2', 'pending', {source_id: 'p2'})]);
  h.controller.update(d.book, d.generation); await settle(); h.controller.toggle(); await settle();
  h.audio.ended = true; h.audio.paused = true; h.audio.dispatchEvent(new Event('ended')); assert.equal(h.state().segment.id, 's2'); assert.equal(h.state().waiting, true);
  h.controller.update(d.book, {...d.generation, segments: [segment('s1'), segment('s2', 'ready', {source_id: 'p2'})]}); await settle();
  assert.equal(h.state().playing, true); h.controller.dispose();
});

test('continuous playback and skip controls pass over held passages', async () => {
  const h = harness();
  const d = data([segment('s1'), segment('held', 'review', {source_id: 'p2'}), segment('s3', 'ready', {source_id: 'p3'})]);
  h.controller.update(d.book, d.generation); await settle(); h.controller.toggle(); await settle();
  h.audio.ended = true; h.audio.paused = true; h.audio.dispatchEvent(new Event('ended')); await settle();
  assert.equal(h.state().segment.id, 's3'); assert.equal(h.state().playing, true);
  h.controller.select('s1', false, 1.5); await settle(); h.controller.skip(1); await settle();
  assert.equal(h.state().segment.id, 's3'); assert.equal(h.audio.currentTime, .5);
  h.controller.dispose();
});

test('polling replacement audio for the same passage resets and saves its offset', async () => {
  const h = harness(); const d = data();
  h.controller.update(d.book, d.generation); await settle(); h.controller.select('s1', false, 1.25); await settle();
  const replacement = segment('s1', 'ready', {audio_hash: 'new-hash', duration: 3});
  h.controller.update(d.book, {...d.generation, segments: [replacement]}); await settle();
  assert.equal(h.audio.currentTime, 0);
  assert.equal(h.saved.at(-1).offset, 0);
  assert.equal(h.loads.at(-1), 'new-hash');
  h.controller.dispose();
});

test('pausing while waiting prevents autoplay when preparation finishes', async () => {
  const h = harness(); const d = data([segment('s1', 'pending')]);
  h.controller.update(d.book, d.generation); h.controller.toggle(); h.controller.pause();
  h.controller.update(d.book, {...d.generation, segments: [segment('s1')]}); await settle();
  assert.equal(h.audio.plays, 0); assert.equal(h.state().waiting, false); h.controller.dispose();
});

test('switching generations persists the old binding and pauses at the matching passage offset', async () => {
  const h = harness(); const d = data(); h.controller.update(d.book, d.generation); await settle();
  h.audio.currentTime = 1.25; h.controller.update(d.book, {...d.generation, id: 'g2'}); await settle();
  assert.ok(h.saved.some(item => item.generation_id === 'g1' && item.offset === 1.25));
  assert.equal(h.state().segment.id, 's1'); assert.equal(h.audio.currentTime, 1.25); assert.equal(h.audio.paused, true);
  assert.deepEqual(h.saved.at(-1), {id: 'b1', generation_id: 'g2', chapter_id: 'c1', source_id: 'p1', segment_id: 's1', offset: 1.25});
  h.controller.dispose();
});

test('switching generations keeps the listening source when passages differ and restarts that source', async () => {
  const h = harness();
  const first = [segment('a1'), segment('a2', 'ready', {source_id: 'p2', start: 0, end: 10}), segment('a3', 'ready', {source_id: 'p3'})];
  const second = [segment('b1'), segment('b2', 'ready', {source_id: 'p2', start: 0, end: 6}), segment('b3', 'ready', {source_id: 'p3'})];
  h.controller.update({id: 'b1', position: null}, {id: 'g1', book_id: 'b1', segments: first}); await settle();
  h.controller.select('a2', false, 1.5); await settle();
  h.controller.update({id: 'b1', position: null}, {id: 'g2', book_id: 'b1', segments: second}); await settle();
  assert.equal(h.state().segment.id, 'b2'); assert.equal(h.audio.currentTime, 0); assert.equal(h.audio.paused, true);
  assert.deepEqual(h.saved.at(-1), {id: 'b1', generation_id: 'g2', chapter_id: 'c1', source_id: 'p2', segment_id: 'b2', offset: 0});
  h.controller.dispose();
});

test('same passage with a different segment range is not treated as matching', async () => {
  const h = harness();
  const first = [segment('s1'), segment('s2', 'ready', {source_id: 'p2', start: 0, end: 10})];
  const second = [segment('s1'), segment('s2', 'ready', {source_id: 'p2', start: 0, end: 7})];
  h.controller.update({id: 'b1', position: null}, {id: 'g1', book_id: 'b1', segments: first}); await settle();
  h.controller.select('s2', false, 1.5); await settle();
  h.controller.update({id: 'b1', position: null}, {id: 'g2', book_id: 'b1', segments: second}); await settle();
  assert.equal(h.state().segment.id, 's2'); assert.equal(h.audio.currentTime, 0); h.controller.dispose();
});

test('book-wide listening position from another generation restores its source when the book opens', async () => {
  const h = harness(); const d = data([segment('s1'), segment('s2', 'ready', {source_id: 'p2'})], 'g2');
  d.book.position = {generation_id: 'g1', chapter_id: 'c1', source_id: 'p2', segment_id: 's2', offset: .5};
  h.controller.update(d.book, d.generation); await settle();
  assert.equal(h.state().segment.id, 's2'); assert.equal(h.audio.currentTime, .5); assert.equal(h.audio.paused, true);
  h.controller.dispose();
});

test('resumed preparation clears a stale not-ready error without autoplay', async () => {
  const h = harness(); const d = data([segment('s1'), segment('s2', 'pending', {source_id: 'p2'})]);
  d.generation.status = 'paused'; h.controller.update(d.book, d.generation); await settle();
  h.controller.select('s2', true); await settle();
  assert.match(h.state().error, /not ready/);
  h.controller.update(d.book, {...d.generation, status: 'paused'}); await settle();
  assert.match(h.state().error, /not ready/);
  h.controller.update(d.book, {...d.generation, status: 'rendering'}); await settle();
  assert.equal(h.state().error, ''); assert.equal(h.state().waiting, false);
  h.controller.update(d.book, {...d.generation, status: 'ready', segments: [segment('s1'), segment('s2', 'ready', {source_id: 'p2'})]}); await settle();
  assert.equal(h.state().error, ''); assert.equal(h.state().playing, false); assert.equal(h.audio.plays, 0);
  h.controller.dispose();
});

test('resuming a failed generation clears its stale failure message but keeps audio errors', async () => {
  const h = harness(async () => { throw new Error('Saved audio is damaged.'); });
  const d = data([segment('s1'), segment('s2', 'pending', {source_id: 'p2'})]);
  d.generation.status = 'failed'; d.generation.error = 'Controlled failure';
  h.controller.update(d.book, d.generation); await settle();
  h.controller.select('s2', true); await settle(); assert.equal(h.state().error, 'Controlled failure');
  h.controller.update(d.book, {...d.generation, status: 'queued', error: ''}); await settle(); assert.equal(h.state().error, '');
  h.controller.select('s1', true); await settle(); assert.equal(h.state().error, 'Saved audio is damaged.');
  h.controller.update(d.book, {...d.generation, status: 'rendering', error: ''}); await settle();
  assert.equal(h.state().error, 'Saved audio is damaged.'); h.controller.dispose();
});

test('space scrolls natively unless a playable selection exists', () => {
  const {playbackShortcut} = load('src/lib/readerSelection.ts');
  global.Element = function Element() {};
  global.HTMLElement = function HTMLElement() {};
  try {
    const text = Object.assign(Object.create(Element.prototype), {closest: () => null});
    assert.equal(playbackShortcut({code: 'Space', target: text}, false), null);
    assert.equal(playbackShortcut({code: 'Space', target: text}, true), 'toggle');
    assert.equal(playbackShortcut({code: 'ArrowRight', target: text}, false), 'forward');
  } finally { delete global.Element; delete global.HTMLElement; }
});

test('a paused or failed generation covering the chapter offers resume instead of a duplicate', () => {
  const {resumesChapter} = load('src/lib/readerSelection.ts');
  const covered = [segment('s1')];
  assert.equal(resumesChapter({status: 'paused'}, covered), true);
  assert.equal(resumesChapter({status: 'failed'}, covered), true);
  assert.equal(resumesChapter({status: 'ready'}, covered), false);
  assert.equal(resumesChapter({status: 'rendering'}, covered), false);
  assert.equal(resumesChapter({status: 'paused'}, []), false);
  assert.equal(resumesChapter(null, covered), false);
});

test('playback rate survives loading another audio segment', async () => {
  const h = harness(); const d = data([segment('s1'), segment('s2')]);
  h.controller.configure({playback_rate: 1.5, volume: .4}); h.controller.update(d.book, d.generation); await settle();
  h.controller.select('s2', true); await settle(); assert.equal(h.audio.playbackRate, 1.5); assert.equal(h.audio.volume, .4); h.controller.dispose();
});

test('skip uses current playback position and known durations', async () => {
  const h = harness(); const d = data([segment('s1'), segment('s2')]);
  h.controller.update(d.book, d.generation); await settle(); h.controller.seek(1.5); h.controller.skip(1); await settle();
  assert.equal(h.state().segment.id, 's2'); assert.equal(h.audio.currentTime, .5); h.controller.dispose();
});

test('disposing cancels pending work and revokes loaded media', async () => {
  const h = harness(); const d = data(); h.controller.update(d.book, d.generation); await settle();
  h.controller.dispose(); assert.equal(h.revoked.length, 1); assert.equal(h.audio.src, '');
});

test('a generation from another book cannot play in the current book', async () => {
  const h = harness(); const d = data(); h.controller.update(d.book, {...d.generation, book_id: 'other'}); await settle();
  assert.equal(h.loads.length, 0); assert.equal(h.state().segment, null); h.controller.dispose();
});

test('illustrations follow source order rather than creation order', () => {
  const chapter = {blocks: [{id: 'p1'}, {id: 'p2'}, {id: 'p3'}]};
  const visuals = [{id: 'later-scene', source_id: 'p2'}, {id: 'created-last', source_id: 'p1'}];
  assert.equal(visualForPassage(chapter, visuals, 'p3').visual.id, 'later-scene');
});

test('original figure after earlier artwork takes over the visual stage', () => {
  const chapter = {blocks: [{id: 'p1'}, {id: 'f1', kind: 'figure'}, {id: 'p2'}]};
  assert.equal(visualForPassage(chapter, [{id: 'early', source_id: 'p1'}], 'p2').block.id, 'f1');
  assert.equal(visualForPassage(chapter, [], 'missing'), null);
});

test('browser session keeps its random runtime port across reload and duplicate connect', async () => {
  const saved = new Map();
  const previous = Object.fromEntries(['window', 'location', 'history', 'sessionStorage', 'fetch'].map(key => [key, global[key]]));
  try {
    global.window = {};
    global.location = {hash: '#port=32189&token=fixture-session-token', pathname: '/'};
    global.history = {replaceState() { global.location.hash = ''; }};
    global.sessionStorage = {getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value)};
    let requested = '';
    global.fetch = async (url, options) => { requested = url; return {ok: true, json: async () => []}; };
    const api = load('src/lib/api.ts');
    await api.connect(); await api.connect(); await api.api('library');
    assert.equal(requested, 'http://127.0.0.1:32189/api');
    assert.equal(saved.get('rimsky-port'), '32189');
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete global[key]; else global[key] = value;
    }
  }
});

test('hosted listener uses the production API through its same-origin path without exposing a bearer token', async () => {
  const previous = Object.fromEntries(['window', 'location', 'document', 'fetch'].map(key => [key, global[key]]));
  const book = 'a'.repeat(32), generation = 'b'.repeat(24);
  try {
    global.window = {};
    global.location = {href: `https://listen.example/review/ch123/?listener=1&book=${book}&generation=${generation}`,
      pathname: '/review/ch123/', search: `?listener=1&book=${book}&generation=${generation}`, hash: ''};
    global.document = {querySelector: () => null};
    let requested, authorization;
    global.fetch = async (url, options) => { requested = url; authorization = options.headers.get('Authorization'); return {ok: true, status: 200, json: async () => []}; };
    const api = load('src/lib/api.ts');
    const listener = api.hostedListenerConfig();
    assert.deepEqual(listener.target, {bookId: book, generationId: generation});
    await api.connect(); await api.api('library');
    assert.equal(requested, 'https://listen.example/review/ch123/api');
    assert.equal(authorization, null, 'The gateway injects its runtime token server-side');
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete global[key]; else global[key] = value;
    }
  }
});

test('hosted listener accepts gateway-injected metadata at the bare stable URL', () => {
  const api = load('src/lib/api.ts');
  const values = {'rimsky-runtime-mode': 'hosted-listener', 'rimsky-book-id': 'c'.repeat(32), 'rimsky-generation-id': 'd'.repeat(24)};
  const page = {querySelector: selector => {
    const name = /meta\[name="([^"]+)"\]/.exec(selector)?.[1];
    return name && values[name] ? {content: values[name]} : null;
  }};
  assert.deepEqual(api.hostedListenerConfig({href: 'https://listen.example/review/ch123/', pathname: '/review/ch123/', search: ''}, page), {
    enabled: true, base: 'https://listen.example/review/ch123/', target: {bookId: 'c'.repeat(32), generationId: 'd'.repeat(24)},
  });
});

test('hosted listener accepts a book-only link and leaves current audio selection to the book pointer',()=>{
  const api=load('src/lib/api.ts');const book='a'.repeat(32);
  assert.deepEqual(api.hostedListenerConfig({href:`https://listen.example/current/?listener=1&book=${book}`,pathname:'/current/',search:`?listener=1&book=${book}`},{querySelector:()=>null}).target,{bookId:book,generationId:''});
});

test('another part replacement preserves the currently playing part offset',async()=>{
  const h=harness();const d=data([segment('s1'),segment('s2')]);
  h.controller.update(d.book,d.generation);await settle();h.controller.select('s1',false,1.25);await settle();
  const loads=h.loads.filter(key=>key==='hash-s1').length;h.controller.update(d.book,{...d.generation,segments:[d.generation.segments[0],segment('s2','ready',{audio_hash:'new-other-part'})]});await settle();
  assert.equal(h.audio.currentTime,1.25);assert.equal(h.loads.filter(key=>key==='hash-s1').length,loads);h.controller.dispose();
});

test('pending selection is saved and restored before its audio exists', async () => {
  const h = harness(); const d = data([segment('s1'), segment('s2', 'pending', {source_id: 'p2'})]);
  h.controller.update(d.book, d.generation); await settle();
  h.controller.select('s2', true); h.controller.pause(); await settle();
  const saved = h.saved.at(-1);
  assert.equal(saved.segment_id, 's2'); assert.equal(saved.offset, 0);
  h.controller.dispose();
  const reopened = harness();
  reopened.controller.update({...d.book, position: saved}, d.generation);
  assert.equal(reopened.state().segment.id, 's2'); assert.equal(reopened.loads.length, 0);
  reopened.controller.dispose();
});

test('polling does not repeatedly retry an audio load that needs user recovery', async () => {
  const h = harness(async () => { throw new Error('unavailable fixture'); }); const d = data();
  h.controller.update(d.book, d.generation); await settle();
  for (let i = 0; i < 3; i++) { h.controller.update(d.book, {...d.generation}); await settle(); }
  assert.equal(h.loads.length, 1); assert.match(h.state().error, /unavailable/);
  h.controller.toggle(); await settle(); assert.equal(h.loads.length, 2); h.controller.dispose();
});

test('invalidated selected audio stops rather than continuing the stale binding', async () => {
  const h = harness(); const d = data();
  h.controller.update(d.book, d.generation); await settle(); h.controller.toggle(); await settle();
  h.controller.update(d.book, {...d.generation, segments: [segment('s1', 'pending')]});
  assert.equal(h.audio.paused, true); assert.equal(h.audio.src, '');
  assert.equal(h.state().waiting, true); h.controller.dispose();
});

test('saved pending position survives analysis replacing its segment IDs', async () => {
  const h = harness();
  const d = data([segment('s1'), segment('split', 'ready', {chapter_id:'c2', source_id:'p2'})]);
  d.book.position = {generation_id:'g1', segment_id:'old-unsplit', chapter_id:'c2', source_id:'p2', offset:.75};
  h.controller.update(d.book, d.generation); await settle();
  assert.equal(h.state().segment.id, 'split'); assert.equal(h.audio.currentTime, 0);
  assert.equal(h.audio.paused, true); h.controller.dispose();
});


test('reselecting the loaded passage while paused persists its new offset', async () => {
  const h = harness(); const d = data();
  h.controller.update(d.book, d.generation); await settle();
  h.controller.seek(1.5); await settle();
  h.controller.select('s1', false, .25); await settle();
  assert.equal(h.audio.currentTime, .25);
  assert.equal(h.saved.at(-1).offset, .25);
  assert.equal(h.audio.paused, true);
  h.controller.dispose();
});

test('an intentional pause does not surface an obsolete play rejection', async () => {
  const h = harness(); const d = data();
  h.controller.update(d.book, d.generation); await settle();
  let rejectPlay;
  h.audio.play = () => new Promise((resolve, reject) => { rejectPlay = reject; });
  h.controller.toggle(); h.controller.pause();
  rejectPlay(new Error('play interrupted by pause')); await settle();
  assert.equal(h.state().error, ''); assert.equal(h.state().playing, false);
  h.controller.dispose();
});

test('obsolete play rejection cannot cancel a newer play request', async () => {
  const h = harness(); const d = data([segment('s1'), segment('s2')]);
  h.controller.update(d.book, d.generation); await settle();
  const actualPlay = h.audio.play; let rejectPlay;
  h.audio.play = () => new Promise((resolve, reject) => { rejectPlay = reject; });
  h.controller.toggle(); h.controller.pause(); h.audio.play = actualPlay;
  h.controller.toggle(); await settle();
  rejectPlay(new Error('old play interrupted')); await settle();
  assert.equal(h.state().error, ''); assert.equal(h.state().playing, true);
  h.audio.ended = true; h.audio.paused = true; h.audio.dispatchEvent(new Event('ended')); await settle();
  assert.equal(h.state().segment.id, 's2'); assert.equal(h.state().playing, true);
  h.controller.dispose();
});

test('generation polling does not multiply unresolved play requests', async () => {
  const h = harness(); const d = data();
  h.controller.update(d.book, d.generation); await settle();
  let calls = 0; let resolvePlay;
  h.audio.play = () => { calls++; return new Promise(resolve => { resolvePlay = resolve; }); };
  h.controller.toggle();
  for (let i = 0; i < 3; i++) h.controller.update(d.book, {...d.generation});
  assert.equal(calls, 1);
  h.controller.pause(); resolvePlay(); await settle(); h.controller.dispose();
});

test('a media-originated pause is not undone by generation polling', async () => {
  const h = harness(); const d = data();
  h.controller.update(d.book, d.generation); await settle();
  h.controller.toggle(); await settle(); h.audio.pause();
  h.controller.update(d.book, {...d.generation}); await settle();
  assert.equal(h.audio.paused, true); assert.equal(h.state().playing, false);
  h.controller.dispose();
});


test('a delayed pause event cannot cancel audio that has already resumed', async () => {
  const h = harness(); const d = data([segment('s1'), segment('s2')]);
  h.controller.update(d.book, d.generation); await settle(); h.controller.toggle(); await settle();
  h.audio.dispatchEvent(new Event('pause'));
  assert.equal(h.audio.paused, false);
  h.audio.ended = true; h.audio.paused = true; h.audio.dispatchEvent(new Event('ended')); await settle();
  assert.equal(h.state().segment.id, 's2'); assert.equal(h.state().playing, true);
  h.controller.dispose();
});


test('late completion from old audio cannot skip the newly selected passage', async () => {
  const h = harness(); const d = data([segment('s1'), segment('s2'), segment('s3')]);
  h.controller.update(d.book, d.generation); await settle(); h.controller.toggle(); await settle();
  h.controller.select('s2', true); await settle();
  h.audio.dispatchEvent(new Event('ended')); await settle();
  assert.equal(h.state().segment.id, 's2'); assert.equal(h.state().playing, true); h.controller.dispose();
});

test('duplicate completion cannot skip audio whose load is still pending', async () => {
  const pending = new Map();
  const h = harness(hash => new Promise(resolve => pending.set(hash, resolve)));
  const d = data([segment('s1'), segment('s2'), segment('s3')]);
  h.controller.update(d.book, d.generation); pending.get('hash-s1')(new Blob(['one'])); await settle();
  h.controller.toggle(); await settle(); h.audio.ended = true; h.audio.paused = true;
  h.audio.dispatchEvent(new Event('ended'));
  h.audio.dispatchEvent(new Event('ended'));
  assert.equal(h.state().segment.id, 's2'); assert.notEqual(h.state().segment.id, 's3');
  pending.get('hash-s2')(new Blob(['two'])); await settle();
  assert.equal(h.state().playing, true); h.controller.dispose();
});

const {defaultNarrationChapters, narrationSelection, sourceChapter, hasNarratableText} = load('src/lib/preparation.ts');
function preparedBook() {
  const block = (id, kind = 'paragraph', text = 'Текст.') => ({id, kind, text});
  return {chapters: [
    {id: 'contents', default_narration: false, blocks: [block('contents-p')]},
    {id: 'one', blocks: [block('one-p')]},
    {id: 'cover', blocks: [block('image', 'figure', ''), block('break', 'separator', '* * *')]},
    {id: 'two', default_narration: true, blocks: [block('two-p')]},
    {id: 'notes', default_narration: false, blocks: [block('note-p')]},
  ]};
}
test('preparation defaults exclude optional sections and visual-only content', () => {
  assert.deepEqual(defaultNarrationChapters(preparedBook()), ['one', 'two']);
});
test('explicit narration selection keeps book order and removes duplicate section IDs', () => {
  assert.deepEqual(narrationSelection(preparedBook(), 'selected', 'one', ['notes', 'two', 'notes', 'missing', 'one']), ['one', 'two', 'notes']);
});
test('empty custom narration never falls back to the whole book', () => {
  assert.deepEqual(narrationSelection(preparedBook(), 'selected', 'one', []), []);
});
test('all-sections narration deliberately includes contents and notes', () => {
  assert.deepEqual(narrationSelection(preparedBook(), 'everything', 'one', []), ['contents', 'one', 'two', 'notes']);
});
test('current-section narration may explicitly select a note section', () => {
  assert.deepEqual(narrationSelection(preparedBook(), 'chapter', 'notes', []), ['notes']);
});
test('invalid scope and visual-only chapter have no narration selection', () => {
  assert.deepEqual(narrationSelection(preparedBook(), 'invalid', 'one', []), []);
  assert.deepEqual(narrationSelection(preparedBook(), 'chapter', 'cover', []), []);
  assert.equal(hasNarratableText(preparedBook().chapters[2]), false);
});
test('internal source navigation resolves only prepared passage identifiers', () => {
  assert.equal(sourceChapter(preparedBook(), 'note-p').id, 'notes');
  assert.equal(sourceChapter(preparedBook(), 'javascript:alert(1)'), undefined);
});
test('legacy chapter without narration metadata remains included', () => {
  assert.deepEqual(defaultNarrationChapters({chapters: [{id: 'old', blocks: [{id: 'p', kind: 'paragraph', text: 'Legacy prose.'}]}]}), ['old']);
});

test('pausing can be awaited before reading persisted playback position', async () => {
  let hold = false, release, latest = null;
  const h = harness(undefined, {savePosition: async (_id, value) => {
    if (hold) { hold = false; await new Promise(resolve => { release = resolve; }); }
    latest = value;
  }});
  const d = data(); h.controller.update(d.book, d.generation); await settle();
  h.audio.currentTime = 1.25; hold = true;
  let settled = false;
  const paused = Promise.resolve(h.controller.pause()).then(() => { settled = true; });
  await settle(); assert.equal(settled, false);
  release(); await paused;
  assert.equal(latest.offset, 1.25); assert.equal(latest.segment_id, 's1');
  h.controller.dispose();
});

test('first narration starts at an existing text-only reading position', async () => {
  const h = harness(); const d = data([segment('s1'), segment('s2', 'ready', {chapter_id: 'c2', source_id: 'p2'})]);
  d.book.position = {chapter_id: 'c2', source_id: 'p2', offset: 0};
  h.controller.update(d.book, d.generation); await settle();
  assert.equal(h.state().segment.id, 's2'); assert.equal(h.state().playing, false);
  h.controller.dispose();
});

test('stalled local audio download times out, cancels, and remains retryable', async () => {
  let release, firstSignal, count = 0;
  const h = harness((_hash, signal) => {
    if (!count++) { firstSignal = signal; return new Promise(resolve => { release = resolve; }); }
    return Promise.resolve(new Blob(['ready']));
  }, {loadTimeoutMs: 25});
  const d = data(); h.controller.update(d.book, d.generation); h.controller.toggle();
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.match(h.state().error, /timed out/); assert.equal(h.state().waiting, false);
  assert.equal(firstSignal.aborted, true);
  h.controller.toggle(); await settle(); assert.equal(h.state().playing, true);
  release(new Blob(['obsolete'])); await settle();
  assert.equal(h.created.length, 1); assert.equal(h.state().error, '');
  h.controller.dispose();
});

test('metadata that never loads is bounded by the same audio load deadline', async () => {
  const h = harness(undefined, {loadTimeoutMs: 25});
  h.audio.load = () => { h.audio.ended = false; };
  const d = data(); h.controller.update(d.book, d.generation); h.controller.toggle(); await settle();
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.match(h.state().error, /timed out/);
  assert.equal(h.audio.hasAttribute('src'), false); assert.equal(h.revoked.length, 0, 'the cached URL is kept for reuse');
  h.controller.dispose(); assert.equal(h.revoked.length, 1, 'the cache revokes it when it closes');
  h.controller.dispose();
});

test('old audio deadline cannot stop a newer decoded passage', async () => {
  const h = harness(hash => hash === 'hash-s1' ? new Promise(() => {}) : Promise.resolve(new Blob(['second'])), {loadTimeoutMs: 25});
  const d = data([segment('s1'), segment('s2')]);
  h.controller.update(d.book, d.generation); h.controller.select('s2', true); await settle();
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.equal(h.state().segment.id, 's2'); assert.equal(h.state().playing, true); assert.equal(h.state().error, '');
  h.controller.dispose();
});

test('disposing pending download cancels deadline without late notifications', async () => {
  const h = harness(() => new Promise(() => {}), {loadTimeoutMs: 25});
  const d = data(); h.controller.update(d.book, d.generation); h.controller.toggle();
  h.controller.dispose(); const notifications = h.history.length;
  await new Promise(resolve => setTimeout(resolve, 60));
  assert.equal(h.history.length, notifications); assert.equal(h.created.length, 0);
});

test('generation refresh cannot reinstate a discarded selection', () => {
  const {acceptGenerationRefresh} = load('src/lib/readerSelection.ts');
  const old = data().generation;
  const selected = {...old, id: 'g2'};
  assert.equal(acceptGenerationRefresh(selected, old), selected);
  assert.equal(acceptGenerationRefresh(null, old), null);
  assert.equal(acceptGenerationRefresh({...old, book_id: 'b2'}, old).book_id, 'b2');
  const fresh = {...selected, status: 'rendering'};
  assert.equal(acceptGenerationRefresh(selected, fresh), fresh);
});

test('reader selection waits for final playback save before requesting the book', async () => {
  const {afterPlaybackSaved} = load('src/lib/readerSelection.ts');
  let release, requested = false;
  const pending = afterPlaybackSaved(() => new Promise(resolve => { release = resolve; }), async () => {
    requested = true; return 'newest-position';
  });
  await settle(); assert.equal(requested, false);
  release(); assert.equal(await pending, 'newest-position'); assert.equal(requested, true);
});

test('listen-from-here chooses the requested source in a newly prepared generation', async () => {
  const {playbackStart} = load('src/lib/readerSelection.ts');
  const h = harness();
  const d = data([segment('s1'), segment('base', 'pending', {source_id:'requested-source'}), segment('s3', 'pending', {source_id:'later-source'})]);
  const chosen = playbackStart(d.generation, 'requested-source');
  assert.equal(chosen.id, 'base'); assert.equal(playbackStart(d.generation, 'absent'), undefined);
  h.controller.update(d.book, d.generation); h.controller.select(chosen.id, true);
  assert.equal(h.state().waiting, true); assert.equal(h.state().segment.source_id, 'requested-source');
  h.controller.update(d.book, {...d.generation, segments:[d.generation.segments[0],segment('split', 'ready', {source_id:'requested-source'}), d.generation.segments[2]]});
  await settle(); assert.equal(h.state().playing, true); assert.equal(h.state().segment.id, 'split');
  h.controller.dispose();
});

test('failed preparation stops an outstanding wait without skipping the source', async () => {
  const h = harness(); const d = data([segment('pending', 'pending')]);
  d.generation.status = 'rendering'; h.controller.update(d.book, d.generation); h.controller.toggle();
  assert.equal(h.state().waiting, true);
  h.controller.update(d.book, {...d.generation, status: 'failed', error: 'Controlled inference failure'});
  assert.equal(h.state().waiting, false); assert.equal(h.state().playing, false);
  assert.match(h.state().error, /Controlled inference failure/); assert.equal(h.state().segment.id, 'pending');
  assert.equal(h.loads.length, 0); h.controller.dispose();
});

test('paused preparation does not show a perpetual loading spinner for missing audio', () => {
  const h = harness(); const d = data([segment('pending', 'pending')]); d.generation.status = 'paused';
  h.controller.update(d.book, d.generation); h.controller.toggle();
  assert.equal(h.state().waiting, false); assert.match(h.state().error, /Resume preparation/);
  assert.equal(h.loads.length, 0); h.controller.dispose();
});

test('completed audio remains playable even when a later passage failed', async () => {
  const h = harness(); const d = data([segment('ready'), segment('pending', 'pending')]);
  d.generation.status = 'failed'; d.generation.error = 'Later operation failed';
  h.controller.update(d.book, d.generation); await settle(); h.controller.toggle(); await settle();
  assert.equal(h.state().playing, true); assert.equal(h.state().segment.id, 'ready'); h.controller.dispose();
});

test('resuming preparation after a stopped wait does not silently autoplay', async () => {
  const h = harness(); const d = data([segment('pending', 'pending')]); d.generation.status = 'paused';
  h.controller.update(d.book, d.generation); h.controller.toggle();
  h.controller.update(d.book, {...d.generation, status: 'rendering', segments: [segment('pending', 'ready')]});
  await settle(); assert.equal(h.state().playing, false); assert.equal(h.audio.plays, 0);
  h.controller.toggle(); await settle(); assert.equal(h.state().playing, true); h.controller.dispose();
});

test('chapter search preserves canonical numbers and trims the query', () => {
  const {visibleChapters} = load('src/lib/readerSelection.ts');
  const chapters = [{id: 'one', title: 'First'}, {id: 'two', title: 'Second'}, {id: 'three', title: 'Третья глава'}];
  assert.deepEqual(visibleChapters(chapters, ' ТРЕТЬЯ '), [{chapter: chapters[2], number: 3}]);
  assert.equal(visibleChapters(chapters, '').length, 3); assert.deepEqual(visibleChapters(chapters, 'missing'), []);
});

test('audiobook panel shows only the current output with its live progress', () => {
  const {generationHistory} = load('src/lib/readerSelection.ts');
  const saved = {id: 'g1', book_id: 'b1', status: 'paused', name: 'Earlier name', progress: {total: 2, ready: 0, duration: 0}};
  const other = {...saved, id: 'g2'}; const book = {id: 'b1', generations: [other, saved]};
  const current = {...saved, status: 'rendering', name: 'New name', error: '', segments: [segment(), segment('s2', 'pending')]};
  const result = generationHistory(book, current);
  assert.equal(result.length,1);assert.equal(result[0].status, 'rendering');
  assert.deepEqual(result[0].progress, {total: 2, ready: 1, duration: 2});
  assert.equal(result[0].name, 'New name'); assert.equal(saved.status, 'paused');
  assert.equal(saved.progress.ready, 0);
});

test('generation history cannot reinsert a deleted or cross-book version', () => {
  const {generationHistory} = load('src/lib/readerSelection.ts');
  const saved = {id: 'g1', book_id: 'b1', status: 'paused'};
  const book = {id: 'b1', generations: [saved]};
  assert.deepEqual(generationHistory(book, {...saved, book_id: 'b2', segments: []}), [saved]);
  assert.deepEqual(generationHistory(book, {...saved, id: 'deleted', segments: []}), [saved]);
});

test('reader and player position writes reach the runtime in submission order', async () => {
  const previous = global.fetch;
  const sent = [];
  let release;
  try {
    global.fetch = async (_url, init) => {
      const data = JSON.parse(init.body).data;
      sent.push(data.position.chapter_id);
      if (sent.length === 1) await new Promise(resolve => { release = resolve; });
      return {ok: true, json: async () => data.position};
    };
    const transport = load('src/lib/api.ts');
    const first = transport.api('save_position', {book_id: 'book', position: {chapter_id: 'c1', offset: 0}});
    const second = transport.api('save_position', {book_id: 'book', position: {chapter_id: 'c2', offset: 0}});
    await settle();
    try { assert.deepEqual(sent, ['c1']); } finally { release(); }
    await Promise.all([first, second]);
    assert.deepEqual(sent, ['c1', 'c2']);
  } finally { global.fetch = previous; }
});

test('failed position save does not poison later writes and flush waits for all submitted positions', async () => {
  const previous = global.fetch;
  const sent = [];
  let release;
  try {
    global.fetch = async (_url, init) => {
      sent.push(JSON.parse(init.body).data.position.chapter_id);
      if (sent.length === 1) return {ok: false, json: async () => ({error: 'controlled save failure'})};
      await new Promise(resolve => { release = resolve; });
      return {ok: true, json: async () => true};
    };
    const transport = load('src/lib/api.ts');
    const first = transport.api('save_position', {book_id: 'book', position: {chapter_id: 'c1', offset: 0}}).catch(e => e.message);
    const second = transport.api('save_position', {book_id: 'book', position: {chapter_id: 'c2', offset: 0}});
    let flushed = false;
    const flushing = transport.flushPositionWrites().then(() => { flushed = true; });
    await settle();
    assert.equal(await first, 'controlled save failure');
    assert.deepEqual(sent, ['c1', 'c2']);
    assert.equal(flushed, false);
    release();
    await Promise.all([second, flushing]);
    assert.equal(flushed, true);
  } finally { global.fetch = previous; }
});

test('queued positions capture values at submission and do not block unrelated pause requests', async () => {
  const previous = global.fetch;
  const sent = [];
  let release;
  try {
    global.fetch = async (_url, init) => {
      const item = JSON.parse(init.body);
      sent.push(item);
      if (sent.length === 1) await new Promise(resolve => { release = resolve; });
      return {ok: true, json: async () => true};
    };
    const transport = load('src/lib/api.ts');
    const first = transport.api('save_position', {book_id: 'book', position: {chapter_id: 'c1', offset: 0}});
    await settle();
    const value = {book_id: 'book', position: {chapter_id: 'c2', offset: 0}};
    const second = transport.api('save_position', value);
    value.position.chapter_id = 'later-mutation';
    await transport.api('pause', {id: 'generation'});
    assert.equal(sent[1].op, 'pause');
    release();
    await Promise.all([first, second]);
    assert.equal(sent[2].data.position.chapter_id, 'c2');
  } finally { global.fetch = previous; }
});

test('deletion notification distinguishes committed removal from pending disk cleanup', () => {
  const {deletionNotice} = load('src/lib/readerSelection.ts');
  const clean = deletionNotice({deleted: true, cleanup_warnings: []}, 'Book');
  const pending = deletionNotice({deleted: true, cleanup_warnings: ['locked file']}, 'Generation');
  assert.match(clean, /removed from the library/);
  assert.doesNotMatch(clean, /incomplete/);
  assert.match(pending, /cleanup is incomplete/);
  assert.match(pending, /outside the app are unchanged/);
});

const {exportSelection} = load('src/lib/exportSelection.ts');
function exportFixture() {
  const book={id:'b',chapters:[{id:'c1'},{id:'c2'},{id:'c3'}]};
  const generation={id:'g',book_id:'b',config:{chapters:['c1','c2','c3']},segments:[
    segment('s1','ready',{chapter_id:'c1'}),segment('s2','pending',{chapter_id:'c2'}),
    segment('s3','ready',{chapter_id:'c3'}),segment('s4','pending',{chapter_id:'c3'})]};
  return {book,generation};
}
test('export selection offers whole completed chapters without including partial chapters',()=>{
  const {book,generation}=exportFixture();
  assert.deepEqual(exportSelection(book,generation,'ready','c2'),{chapters:['c1'],readyCount:1,totalCount:3,reason:''});
  assert.match(exportSelection(book,generation,'all','c2').reason,/Finish/);
});
test('current chapter export never silently expands to the whole generation',()=>{
  const {book,generation}=exportFixture();
  generation.config.chapters=['c1'];
  const selected=exportSelection(book,generation,'chapter','c2');
  assert.deepEqual(selected.chapters,[]);assert.match(selected.reason,/not in/);
});
test('export eligibility rejects missing audio and invalid durations before encoding',()=>{
  const {book,generation}=exportFixture();
  for(const change of [{audio_hash:null},{duration:0},{duration:NaN},{status:'pending'}]){
    const broken={...generation,segments:[{...generation.segments[0],...change}]};
    assert.equal(exportSelection(book,broken,'ready','c1').readyCount,0);
  }
});
test('export eligibility does not use ready counts from another book or missing sections',()=>{
  const {book,generation}=exportFixture();
  assert.match(exportSelection({...book,id:'other'},generation,'ready','c1').reason,/this book/);
  assert.match(exportSelection(book,{...generation,config:{chapters:['c1','unknown']}},'ready','c1').reason,/inconsistent/);
});

test('an obsolete media error cannot unload currently healthy audio', async () => {
  const h = harness(); const d = data();
  h.controller.update(d.book, d.generation); await settle();
  h.controller.toggle(); await settle();
  h.audio.error = null;
  h.audio.dispatchEvent(new Event('error'));
  assert.equal(h.state().playing, true);
  assert.equal(h.state().error, '');
  assert.equal(h.revoked.length, 0);
  h.audio.error = {code: 3, message: 'Controlled decode failure'};
  h.audio.dispatchEvent(new Event('error'));
  assert.equal(h.state().playing, false);
  assert.match(h.state().error, /could not be played/);
  h.controller.dispose();
});

test('clearing a playback error after repair reloads the passage without autoplay', async () => {
  let attempt = 0;
  const h = harness(async () => { if (!attempt++) throw new Error('A saved audio segment is missing or cannot be opened.'); return new Blob(['repaired']); });
  const d = data(); h.controller.update(d.book, d.generation); await settle();
  assert.match(h.state().error, /missing/);
  h.controller.update(d.book, JSON.parse(JSON.stringify(d.generation))); await settle();
  assert.match(h.state().error, /missing/, 'A generation refresh alone must not hide the failure');
  h.controller.clearError(); await settle();
  assert.equal(h.state().error, ''); assert.equal(h.loads.length, 2); assert.equal(h.audio.plays, 0); assert.equal(h.state().playing, false);
  h.controller.clearError(); await settle();
  assert.equal(h.loads.length, 2, 'Clearing without an error must not reload audio');
  h.controller.dispose();
});

test('runtime connection loss is reported once, recognised, and cleared by the next successful request', async () => {
  const previous = Object.fromEntries(['window', 'location', 'history', 'sessionStorage', 'fetch'].map(key => [key, global[key]]));
  const saved = new Map();
  try {
    global.window = {};
    global.location = {hash: '#port=32190&token=fixture-session-token', pathname: '/'};
    global.history = {replaceState() { global.location.hash = ''; }};
    global.sessionStorage = {getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value)};
    let reply = async () => ({ok: true, status: 200, json: async () => []});
    global.fetch = (...args) => reply(...args);
    const api = load('src/lib/api.ts');
    await api.connect();
    let notifications = 0;
    const unsubscribe = api.runtimeConnection.subscribe(() => notifications++);
    reply = async () => { throw new TypeError('Failed to fetch'); };
    const lost = await api.api('library').catch(reason => reason);
    assert.equal(api.isRuntimeLoss(lost), true); assert.doesNotMatch(lost.message, /Failed to fetch/);
    assert.equal(api.runtimeConnection.lost(), true);
    await api.api('health').catch(() => {});
    assert.equal(notifications, 1);
    reply = async () => ({ok: true, status: 200, json: async () => ({})});
    await api.api('health');
    assert.equal(api.runtimeConnection.lost(), false); assert.equal(notifications, 2);
    reply = async () => ({ok: false, status: 401, json: async () => ({error: 'Local session authentication failed.'})});
    assert.equal(api.isRuntimeLoss(await api.api('library').catch(reason => reason)), true);
    assert.equal(api.runtimeConnection.lost(), true);
    reply = async () => ({ok: false, status: 400, json: async () => ({error: 'Book not found.'})});
    const ordinary = await api.api('book').catch(reason => reason);
    assert.equal(api.isRuntimeLoss(ordinary), false); assert.equal(ordinary.message, 'Book not found.');
    assert.equal(api.runtimeConnection.lost(), false, 'A reachable runtime with an ordinary error is connected');
    const controller = new AbortController(); controller.abort();
    reply = async () => { throw new DOMException('Aborted', 'AbortError'); };
    const aborted = await api.api('generation', {}, controller.signal).catch(reason => reason);
    assert.equal(api.isRuntimeLoss(aborted), false); assert.equal(api.runtimeConnection.lost(), false);
    unsubscribe();
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete global[key]; else global[key] = value;
    }
  }
});

test('a stale not-ready error clears and the passage loads when it becomes ready while preparation stays paused', async () => {
  const h = harness(); const d = data([segment('s1'), segment('s2', 'pending', {source_id: 'p2'})]);
  d.generation.status = 'paused'; h.controller.update(d.book, d.generation); await settle();
  h.controller.select('s2', true); await settle(); assert.match(h.state().error, /not ready/);
  const loads = h.loads.length;
  h.controller.update(d.book, {...d.generation, status: 'paused', segments: [segment('s1'), segment('s2', 'ready', {source_id: 'p2'})]}); await settle();
  assert.equal(h.state().error, ''); assert.equal(h.loads.length, loads + 1); assert.equal(h.audio.plays, 0); assert.equal(h.state().playing, false);
  h.controller.dispose();
});


function withOpening(d = data()) {
  d.generation.config = {chapters:['c1'],opening: {schema: 1, key: 'a'.repeat(64), language: 'en', text: 'Narrated with Dogma.',
    attribution: 'Fixture', variants: {speech: {audio_hash: 'dry-brand', duration: 3}, music: {audio_hash: 'musical-brand', duration: 8}}}};
  d.generation.status = 'rendering';
  return d;
}

test('branded intro uses musical asset with background music off and keeps source offsets untouched', async () => {
  const h = harness(); h.audio.duration = 8;
  const d = withOpening(); h.controller.configure({playback_rate: 1.5, volume: .7, music: false});
  h.controller.update(d.book, d.generation); await settle(); h.controller.toggle(); await settle();
  assert.deepEqual(h.loads, ['musical-brand','hash-s1']); assert.equal(h.state().playing, true);assert.equal(h.audio.src,'blob:0');
  assert.equal(h.audio.playbackRate, 1); assert.equal(h.state().segment.id, 's1');
  h.audio.currentTime = 4; h.audio.dispatchEvent(new Event('timeupdate')); await h.controller.persist();
  assert.equal(h.state().position, 0); assert.equal(h.state().opening.position, 4);
  assert.equal(h.saved.at(-1).offset, 0); assert.equal(h.saved.at(-1).opening.offset, 4);
  h.controller.configure({playback_rate: 2, volume: .6, music: true});
  assert.deepEqual(h.loads, ['musical-brand','hash-s1']); assert.equal(h.audio.currentTime, 4);assert.equal(h.audio.src,'blob:0');
  assert.equal(d.generation.segments.length, 1); h.controller.dispose();
});

test('first chapter track zero restores intro after an old generation saved midchapter',async()=>{
  const h=harness();h.audio.duration=20;const d=withOpening(data([segment('s1'),segment('s2','ready',{source_id:'p2'})],'new'));
  d.book.position={generation_id:'old',chapter_id:'c1',source_id:'p2',segment_id:'s2',offset:1};
  h.controller.configure({playback_rate:2,volume:.7,music:false});h.controller.update(d.book,d.generation);await settle();
  assert.equal(h.state().opening,undefined);assert.equal(h.state().segment.id,'s2');
  h.controller.seekChapterTrack('c1',0,true);await settle();
  assert.equal(h.state().opening.position,0);assert.equal(h.state().segment.id,'s1');assert.equal(h.audio.playbackRate,1);
  assert.equal(h.saved.at(-1).generation_id,'new');assert.equal(h.saved.at(-1).offset,0);assert.equal(h.saved.at(-1).opening.complete,false);
  h.audio.ended=true;h.audio.dispatchEvent(new Event('ended'));await settle();
  assert.equal(h.state().opening,undefined);assert.equal(h.state().segment.id,'s1');assert.equal(h.state().position,0);assert.equal(h.audio.playbackRate,2);
  h.controller.select('s2',false);h.controller.select('s1',false,0);await settle();
  assert.equal(h.state().opening,undefined);assert.equal(h.saved.at(-1).opening.complete,true);h.controller.dispose();
});

test('chapter track seeks across intro boundary while keeping source offsets and pause intent',async()=>{
  const h=harness();h.audio.duration=20;const d=withOpening(data([segment('s1','ready',{duration:10})]));
  h.controller.update(d.book,d.generation);await settle();
  h.controller.seekChapterTrack('c1',3,false);await settle();
  assert.equal(h.state().opening.position,3);assert.equal(h.state().playing,false);assert.equal(h.saved.at(-1).offset,0);
  h.controller.seekChapterTrack('c1',12,false);await settle();
  assert.equal(h.state().opening,undefined);assert.equal(h.state().position,4);assert.equal(h.saved.at(-1).offset,4);
  h.controller.seekChapterTrack('c1',0,false);await settle();assert.equal(h.state().opening.position,0);
  h.controller.skip(5);await settle();assert.equal(h.state().opening.position,5);
  h.controller.skip(5);await settle();assert.equal(h.state().opening,undefined);assert.equal(h.state().position,2);
  h.controller.skip(-5);await settle();assert.equal(h.state().opening.position,5);assert.equal(h.state().playing,false);h.controller.dispose();
});

test('track seek leaves preview and plays intro before waiting for pending first speech',async()=>{
  const h=harness();const d=withOpening(data([segment('s1','pending'),segment('s2','ready',{source_id:'p2'})]));
  h.controller.update(d.book,d.generation);await settle();h.controller.preview('s2');await settle();
  assert.equal(h.state().previewing,true);h.controller.seekChapterTrack('c1',0,true);await settle();
  assert.equal(h.state().previewing,false);assert.equal(h.state().segment.id,'s1');assert.equal(h.state().opening.position,0);
  h.audio.ended=true;h.audio.dispatchEvent(new Event('ended'));await settle();
  assert.equal(h.state().opening,undefined);assert.equal(h.state().waiting,true);assert.equal(h.state().segment.id,'s1');
  h.controller.update(d.book,{...d.generation,segments:[segment('s1'),d.generation.segments[1]]});await settle();
  assert.equal(h.state().playing,true);assert.equal(h.state().segment.id,'s1');h.controller.dispose();
});

test('intro pauses, restores exact offset, survives polls and hands off once to narration', async () => {
  const h = harness(); h.audio.duration = 8; const d = withOpening();
  h.controller.update(d.book, d.generation); await settle(); h.controller.toggle(); await settle();
  h.audio.currentTime = 5; h.audio.dispatchEvent(new Event('timeupdate'));
  await h.controller.pause(); const persisted = h.saved.at(-1);
  h.controller.update({...d.book}, JSON.parse(JSON.stringify(d.generation))); await settle();
  assert.equal(h.state().playing, false); assert.equal(h.state().opening.position, 5);
  h.controller.toggle(); await settle(); assert.equal(h.audio.currentTime, 5);
  h.audio.ended = true; h.audio.dispatchEvent(new Event('ended')); await settle();
  assert.equal(h.state().opening, undefined); assert.equal(h.state().playing, true);
  assert.deepEqual(h.loads, ['musical-brand', 'hash-s1']); assert.equal(h.state().position, 0);
  await h.controller.pause(); h.controller.toggle(); await settle();
  assert.deepEqual(h.loads, ['musical-brand', 'hash-s1']); h.controller.dispose();
  const restored = harness(); restored.audio.duration = 8;
  restored.controller.update({...d.book, position: persisted}, d.generation); await settle();
  assert.equal(restored.state().opening.position, 5); assert.equal(restored.state().playing, false);
  restored.controller.dispose();
});

test('completed branded intro stays complete across reload and chapter navigation', async () => {
  const h = harness(); const d = withOpening(data([segment(), segment('s2', 'ready', {chapter_id:'c2', source_id:'p2'})]));
  d.book.position = {segment_id:'s1', chapter_id:'c1', source_id:'p1', offset:0,
    opening:{key:d.generation.config.opening.key,offset:8,complete:true,variant:'music'}};
  h.controller.update(d.book,d.generation); await settle(); h.controller.toggle(); await settle();
  h.controller.select('s2',true); await settle(); h.controller.select('s1',true); await settle();
  assert.equal(h.state().opening,undefined); assert.equal(h.loads.includes('musical-brand'),false); h.controller.dispose();
});

test('skipping branded intro waits for pending narration and resumes progressive first source', async () => {
  const h = harness(); const d = withOpening(data([segment('base','pending')]));
  h.controller.update(d.book,d.generation); await settle(); h.controller.toggle(); await settle();
  h.controller.skipOpening(); await settle();
  assert.equal(h.state().opening,undefined); assert.equal(h.state().waiting,true);
  h.controller.update(d.book,{...d.generation,segments:[segment('split','ready')]}); await settle();
  assert.equal(h.state().segment.id,'split'); assert.equal(h.state().playing,true);
  assert.equal(h.saved.at(-1).opening.complete,true); h.controller.dispose();
});

test('correction delivered during branded intro leaves its playback intact for an explicit later switch',async()=>{
  const h=harness();const d=withOpening();h.controller.update(d.book,d.generation);await settle();
  h.controller.toggle();await settle();h.audio.currentTime=1;h.audio.dispatchEvent(new Event('timeupdate'));
  assert.equal(h.controller.adoptCorrection(d.book,{...d.generation,id:'g2'},['s1'],['p1']),false);await settle();
  assert.equal(h.state().opening.position,1);assert.equal(h.state().playing,true);assert.deepEqual(h.loads,['musical-brand','hash-s1']);assert.equal(h.audio.src,'blob:0');
  h.controller.dispose();
});

test('adopting first corrected source after intro completion does not replay a new opening asset',async()=>{
  const h=harness();const d=withOpening();h.controller.update(d.book,d.generation);await settle();
  h.controller.skipOpening();await settle();
  const next={...d.generation,id:'g2',config:{...d.generation.config,opening:{...d.generation.config.opening,key:'other-opening'}}};
  assert.equal(h.controller.adoptCorrection(d.book,next,['s1'],['p1']),true);await settle();
  assert.equal(h.state().opening,undefined);assert.equal(h.state().playing,false);assert.equal(h.saved.at(-1).opening.complete,true);
  h.controller.dispose();
});

test('branding can play while analysis has no source segments and later hands off without replay', async () => {
  const h = harness(); const d = withOpening(data([]));
  h.controller.update(d.book,d.generation); await settle(); h.controller.toggle(); await settle();
  assert.equal(h.state().opening.position,0); assert.equal(h.state().playing,true);
  await h.controller.persist(); assert.deepEqual(h.saved,[], 'No fabricated source anchor is saved before analysis');
  h.audio.ended = true; h.audio.dispatchEvent(new Event('ended')); await settle();
  assert.equal(h.state().waiting,true); assert.equal(h.state().opening,undefined);
  h.controller.update(d.book,{...d.generation,segments:[segment()]}); await settle();
  assert.equal(h.state().playing,true); assert.equal(h.state().segment.id,'s1');
  assert.deepEqual(h.loads,['musical-brand','hash-s1']); h.controller.dispose();
});

test('explicit later-chapter navigation bypasses branding and stale intro downloads cannot replace it', async () => {
  let release;
  const h = harness(hash=> hash === 'musical-brand' ? new Promise(resolve=>{release=resolve;}) : Promise.resolve(new Blob(['book'])));
  const d = withOpening(data([segment(),segment('s2','ready',{chapter_id:'c2',source_id:'p2'})]));
  h.controller.update(d.book,d.generation); h.controller.toggle(); await settle();
  h.controller.select('s2',true); await settle();
  assert.equal(h.state().opening,undefined); assert.equal(h.state().segment.id,'s2');
  release(new Blob(['old-brand'])); await settle();
  assert.equal(h.state().segment.id,'s2'); assert.equal(h.state().playing,true);
  assert.equal(h.saved.at(-1).opening.complete,true); h.controller.dispose();
});

test('intro autoplay rejection stays paused through polls and Play retries without losing branding', async () => {
  const h = harness(); const d = withOpening(); let calls=0;
  const play=h.audio.play; h.audio.play=()=>calls++ ? play() : Promise.reject(new Error('Autoplay blocked'));
  h.controller.update(d.book,d.generation); await settle(); h.controller.toggle(); await settle();
  assert.match(h.state().error,/Autoplay blocked/); assert.equal(h.state().playing,false);
  h.controller.update(d.book,JSON.parse(JSON.stringify(d.generation))); await settle(); assert.equal(calls,1);
  h.controller.toggle(); await settle(); assert.equal(h.state().playing,true); assert.ok(h.state().opening);
  h.controller.dispose(); const count=h.history.length; h.audio.dispatchEvent(new Event('ended'));
  assert.equal(h.history.length,count);
});

test('preview during branding restores intro position without making the source a listening position', async () => {
  const h=harness(); h.audio.duration=8; const d=withOpening();
  h.controller.update(d.book,d.generation); await settle(); h.controller.toggle(); await settle();
  h.audio.currentTime=3; h.audio.dispatchEvent(new Event('timeupdate')); await h.controller.persist();
  const saves=h.saved.length;
  h.controller.preview('s1'); await settle(); assert.equal(h.state().previewing,true); assert.equal(h.state().opening,undefined);
  h.audio.ended=true; h.audio.dispatchEvent(new Event('ended')); await settle();
  assert.equal(h.state().previewing,false); assert.equal(h.state().opening.position,3);
  assert.equal(h.state().playing,false); assert.equal(h.saved.length,saves+1);
  h.controller.dispose();
});


test('listen-from-start on a fresh generation retains branding before its first source', async()=>{
  const h=harness();const d=withOpening();h.controller.update(d.book,d.generation);await settle();
  h.controller.select('s1',true,0);await settle();
  assert.ok(h.state().opening);assert.equal(h.state().playing,true);
  assert.deepEqual(h.loads,['musical-brand','hash-s1']);assert.equal(h.audio.src,'blob:0');h.controller.dispose();
});


function streamData() {
  const d=data([segment('s1'),segment('s2','ready',{source_id:'p2',duration:3}),segment('s3','ready',{source_id:'p3'})]);
  d.generation.status='rendering';
  const parts=(ids)=>ids.map((id,index)=>({segment_id:id,audio_hash:'hash-'+id,start:index?2:0,duration:id==='s2'?3:2}));
  const manifest={schema:1,key:'manifest',status:'ready',music:true,music_volume:.2,holds:[],chunks:[
    {key:'chunk-one',audio_hash:'encoded-file-sha-one',chapter_id:'c1',start:0,duration:5,status:'ready',parts:parts(['s1','s2'])},
    {key:'chunk-two',audio_hash:'encoded-file-sha-two',chapter_id:'c1',start:5,duration:2,status:'ready',parts:parts(['s3'])}]};
  return {...d,manifest};
}
function streamHarness(extra={}) {
  const fetched=[];
  const h=harness(undefined,{loadPlayback:async(key)=>{fetched.push(key);return new Blob(['compressed']);},
    createAudio:()=>{const audio=audioElement();audio.duration=300;return audio;},...extra});
  h.audio.duration=300;
  return {...h,fetched};
}

test('first chapter track seeks cross the intro and prefetched compressed speech without changing source offsets',async()=>{
  const h=streamHarness(),d=withOpening(streamData());
  h.controller.configure({music:false,playback_rate:1.5});
  h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);await settle();
  h.controller.toggle();await settle();
  assert.ok(h.state().opening);assert.equal(h.audio.playbackRate,1);
  assert.deepEqual(h.loads,['musical-brand']);assert.ok(h.fetched.includes('chunk-one'));
  assert.equal(h.state().streamKey,undefined,'Prefetch does not start source playback during the intro');
  h.controller.seekChapterTrack('c1',11,true);await settle();
  assert.equal(h.state().opening,undefined);assert.equal(h.state().segment.id,'s2');
  assert.equal(h.state().position,1);assert.equal(h.audio.currentTime,3);assert.equal(h.audio.playbackRate,1.5);
  await h.controller.persist();assert.equal(h.saved.at(-1).offset,1);
  h.controller.seekChapterTrack('c1',0,true);await settle();assert.ok(h.state().opening);
  assert.equal(h.audio.currentTime,0);assert.equal(h.audio.playbackRate,1);
  h.audio.ended=true;h.audio.dispatchEvent(new Event('ended'));await settle();
  assert.equal(h.state().segment.id,'s1');assert.equal(h.state().opening,undefined);
  assert.equal(h.audio.currentTime,0);assert.equal(h.audio.playbackRate,1.5);
  assert.equal(h.fetched.filter(key=>key==='chunk-one').length,1);
  h.controller.dispose();
});

test('continuous playback waits for prepared chunks, never preloads sentence WAVs, and maps every source without handoffs',async()=>{
  const h=streamHarness(),d=streamData();
  h.controller.update(d.book,d.generation);h.controller.toggle();await settle();
  assert.equal(h.state().waiting,true);assert.equal(h.state().premixed,true);assert.deepEqual(h.loads,[]);
  h.controller.setStreams('g1',d.manifest);await settle();
  assert.equal(h.state().playing,true);assert.equal(h.state().streamKey,'chunk-one');assert.deepEqual(h.loads,[]);
  const plays=h.audio.plays,src=h.audio.src;
  h.audio.currentTime=2.7;h.audio.dispatchEvent(new Event('timeupdate'));
  assert.equal(h.state().segment.id,'s2');assert.ok(Math.abs(h.state().position-.7)<1e-8);
  assert.equal(h.audio.src,src);assert.equal(h.audio.plays,plays);
  await h.controller.persist();
  assert.equal(h.saved.at(-1).segment_id,'s2');assert.ok(Math.abs(h.saved.at(-1).offset-.7)<1e-8);
  h.controller.dispose();
});

test('continuous seeks stay in one native media element and persist source-relative offsets',async()=>{
  const h=streamHarness(),d=streamData();h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);await settle();
  const src=h.audio.src;
  h.controller.select('s2',true,1.25);await settle();assert.equal(h.audio.currentTime,3.25);assert.equal(h.audio.src,src);
  h.controller.seek(2);await settle();assert.equal(h.audio.currentTime,4);assert.equal(h.state().position,2);
  await h.controller.pause();assert.equal(h.saved.at(-1).segment_id,'s2');assert.equal(h.saved.at(-1).offset,2);
  h.controller.toggle();await settle();assert.equal(h.audio.currentTime,4);assert.equal(h.state().playing,true);
  assert.deepEqual(h.loads,[]);h.controller.dispose();
});

test('background completion without timeupdate hands off from the chunk final source, not an obsolete sentence',async()=>{
  const h=streamHarness(),d=streamData();h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);await settle();h.controller.toggle();await settle();
  assert.equal(h.state().segment.id,'s1');
  h.audio.currentTime=5;h.audio.ended=true;h.audio.dispatchEvent(new Event('ended'));await settle();
  assert.equal(h.state().segment.id,'s3');assert.equal(h.state().streamKey,'chunk-two');assert.equal(h.state().playing,true);
  assert.deepEqual(h.fetched,['chunk-one','chunk-two'], 'Asset URLs use immutable recipe keys, not encoded-file proof hashes');assert.deepEqual(h.loads,[]);
  await h.controller.persist();assert.equal(h.saved.at(-1).segment_id,'s3');assert.equal(h.saved.at(-1).offset,0);
  h.controller.dispose();
});

test('continuous native waiting reports buffering and resumes without losing playback intent',async()=>{
  const h=streamHarness(),d=streamData();h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);await settle();h.controller.toggle();await settle();
  h.audio.buffered={length:1,start:()=>0,end:()=>4};h.audio.currentTime=1;
  h.audio.dispatchEvent(new Event('waiting'));assert.equal(h.state().waiting,true);assert.equal(h.state().bufferedSeconds,3);
  h.audio.dispatchEvent(new Event('progress'));assert.equal(h.state().waiting,true,'Network progress alone does not mean decoder resumed');
  h.audio.dispatchEvent(new Event('playing'));assert.equal(h.state().waiting,false);assert.equal(h.state().playing,true);
  h.controller.dispose();
});

test('continuous hosted playback uses direct range URLs instead of fetching full blobs',async()=>{
  const h=streamHarness({playbackAssetUrl:key=>'https://listener.invalid/asset/'+key}),d=streamData();
  h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);await settle();h.controller.toggle();await settle();
  assert.equal(h.audio.src,'https://listener.invalid/asset/chunk-one');assert.deepEqual(h.fetched,[]);assert.deepEqual(h.loads,[]);
  h.controller.dispose();
});

test('source corrections invalidate stale encoded streams and hold playback until a matching manifest arrives',async()=>{
  const h=streamHarness(),d=streamData();h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);await settle();h.controller.toggle();await settle();
  const updated={...d.generation,segments:[{...d.generation.segments[0],audio_hash:'corrected'},...d.generation.segments.slice(1)]};
  h.controller.update(d.book,updated);await settle();assert.equal(h.state().waiting,true);assert.equal(h.state().playing,false);assert.deepEqual(h.loads,[]);
  const manifest={...d.manifest,chunks:[{...d.manifest.chunks[0],key:'corrected-chunk',parts:[{...d.manifest.chunks[0].parts[0],audio_hash:'corrected'},d.manifest.chunks[0].parts[1]]},d.manifest.chunks[1]]};
  h.controller.setStreams('g1',manifest);await settle();assert.equal(h.state().streamKey,'corrected-chunk');assert.equal(h.state().playing,true);h.controller.dispose();
});

test('continuous preview uses isolated sentence audio and restores the original source inside its chunk',async()=>{
  const h=streamHarness(),d=streamData();h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);await settle();h.controller.toggle();await settle();
  h.audio.currentTime=3.2;h.audio.dispatchEvent(new Event('timeupdate'));h.controller.preview('s3');await settle();
  assert.equal(h.state().previewing,true);assert.equal(h.state().premixed,false);assert.deepEqual(h.loads,['hash-s3']);
  h.audio.ended=true;h.audio.dispatchEvent(new Event('ended'));await settle();
  assert.equal(h.state().previewing,false);assert.equal(h.state().segment.id,'s2');assert.equal(h.state().streamKey,'chunk-one');
  assert.ok(Math.abs(h.state().position-1.2)<1e-8);assert.equal(h.state().playing,false);assert.equal(h.state().premixed,true);
  h.controller.dispose();
});

test('a premixed ending tail retains the final source at its end until native playback finishes',async()=>{
  const h=streamHarness(),d=streamData();d.manifest.chunks=[{...d.manifest.chunks[0],duration:9}];
  d.generation.segments=d.generation.segments.slice(0,2);d.generation.status='ready';
  h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);await settle();h.controller.toggle();await settle();
  h.audio.currentTime=8;h.audio.dispatchEvent(new Event('timeupdate'));assert.equal(h.state().segment.id,'s2');assert.equal(h.state().position,3);assert.equal(h.state().playing,true);
  h.audio.currentTime=9;h.audio.ended=true;h.audio.dispatchEvent(new Event('ended'));await settle();
  assert.equal(h.state().playing,false);assert.equal(h.state().waiting,false);h.controller.dispose();
});


test('continuous progressive end waits for later published sources instead of replaying or silently stopping',async()=>{
  const h=streamHarness(),d=streamData();
  const initial={...d.generation,segments:d.generation.segments.slice(0,2)};
  const first={...d.manifest,chunks:d.manifest.chunks.slice(0,1)};
  h.controller.update(d.book,initial);h.controller.setStreams('g1',first);await settle();h.controller.toggle();await settle();
  h.audio.currentTime=5;h.audio.ended=true;h.audio.dispatchEvent(new Event('ended'));await settle();
  assert.equal(h.state().waiting,true);assert.equal(h.state().playing,false);
  h.controller.setStreams('g1',first);await settle();assert.equal(h.state().playing,false);
  h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);await settle();
  assert.equal(h.state().segment.id,'s3');assert.equal(h.state().playing,true);
  h.controller.dispose();
});

test('continuous mapping rejects stale timing, reordered or duplicate parts',()=>{
  const {validPlaybackChunk}=load('src/lib/playbackStreams.ts'),d=streamData(),base=d.manifest.chunks[0];
  assert.equal(validPlaybackChunk(base,d.generation),true);
  assert.equal(validPlaybackChunk({...base,parts:[{...base.parts[0],duration:1},base.parts[1]]},d.generation),false);
  assert.equal(validPlaybackChunk({...base,parts:[base.parts[0],{...base.parts[0],start:2}]},d.generation),false);
  assert.equal(validPlaybackChunk({...base,parts:[{...base.parts[1],start:0},{...base.parts[0],start:3}]},d.generation),false);
});


test('a failed continuous chunk reports its error even when the manifest is partial',async()=>{
  const h=streamHarness(),d=streamData();
  const broken={...d.manifest,status:'partial',chunks:[{...d.manifest.chunks[0],status:'failed',error:'Encoder test failure'},d.manifest.chunks[1]]};
  h.controller.update(d.book,d.generation);h.controller.setStreams('g1',broken);h.controller.toggle();await settle();
  assert.equal(h.state().waiting,false);assert.match(h.state().error,/Encoder test failure/);
  h.controller.setStreams('g1',d.manifest);await settle();
  assert.equal(h.state().error,'');assert.equal(h.state().playing,false);
  h.controller.toggle();await settle();assert.equal(h.state().playing,true);h.controller.dispose();
});


test('continuous playback preserves the always-musical opening before waiting for its first prepared stream',async()=>{
  const h=streamHarness(),d=streamData();withOpening(d);
  h.controller.update(d.book,d.generation);await settle();h.controller.toggle();await settle();
  assert.equal(h.state().premixed,false);assert.deepEqual(h.loads,['musical-brand']);
  h.controller.skipOpening();await settle();assert.equal(h.state().premixed,true);assert.equal(h.state().waiting,true);
  h.controller.setStreams('g1',d.manifest);await settle();assert.equal(h.state().streamKey,'chunk-one');assert.equal(h.state().playing,true);
  await h.controller.persist();assert.equal(h.saved.at(-1).opening.complete,true);
  h.controller.dispose();
});

test('disposing an in-flight continuous chunk aborts it and prevents stale media installation',async()=>{
  let release,signal;
  const h=streamHarness({loadPlayback:(_key,current)=>{signal=current;return new Promise(resolve=>{release=resolve;});}}),d=streamData();
  h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);h.controller.toggle();await settle();
  h.controller.dispose();const count=h.history.length;assert.equal(signal.aborted,true);
  release(new Blob(['obsolete']));await settle();assert.equal(h.history.length,count);assert.equal(h.audio.src,'');
});

test('continuous manifests from a previous generation cannot change the current book audio',async()=>{
  const h=streamHarness(),d=streamData();h.controller.update(d.book,d.generation);
  h.controller.setStreams('obsolete',d.manifest);h.controller.toggle();await settle();
  assert.equal(h.state().waiting,true);assert.deepEqual(h.fetched,[]);
  h.controller.setStreams('g1',d.manifest);await settle();assert.equal(h.state().playing,true);h.controller.dispose();
});


test('failed saved-source holds surface immediately instead of leaving continuous playback buffering forever',async()=>{
  const h=streamHarness(),d=streamData();
  const broken={...d.manifest,status:'partial',chunks:d.manifest.chunks.slice(1),holds:[{id:'s1',chapter_id:'c1',status:'failed',error:'Saved source checksum mismatch'}]};
  h.controller.update(d.book,d.generation);h.controller.setStreams('g1',broken);h.controller.toggle();await settle();
  assert.equal(h.state().waiting,false);assert.equal(h.state().playing,false);assert.match(h.state().error,/checksum mismatch/);
  assert.equal(h.state().segment.id,'s1');assert.deepEqual(h.loads,[]);
  h.controller.setStreams('g1',d.manifest);await settle();assert.equal(h.state().error,'');assert.equal(h.state().playing,false);
  h.controller.toggle();await settle();assert.equal(h.state().playing,true);h.controller.dispose();
});

test('continuous API operation errors pause at the current real anchor and repair resumes only on Play',async()=>{
  const h=streamHarness(),d=streamData();
  h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);await settle();h.controller.toggle();await settle();
  h.audio.currentTime=3.4;h.audio.dispatchEvent(new Event('timeupdate'));
  h.controller.setStreams('g1',{schema:1,key:'',status:'failed',music:true,music_volume:.2,chunks:[],holds:[],error:'FFmpeg and FFprobe are required for continuous playback.'});
  await settle();assert.equal(h.state().waiting,false);assert.equal(h.state().playing,false);assert.match(h.state().error,/FFmpeg/);
  assert.equal(h.state().segment.id,'s2');assert.ok(Math.abs(h.state().position-1.4)<1e-8);
  assert.deepEqual(h.loads,[]);
  h.controller.setStreams('g1',d.manifest);await settle();assert.equal(h.state().error,'');assert.equal(h.state().playing,false);
  h.controller.toggle();await settle();assert.ok(Math.abs(h.audio.currentTime-3.4)<1e-8);h.controller.dispose();
});

test('continuous API failure is visible before source analysis has produced an anchor',async()=>{
  const h=streamHarness(),d=streamData();d.generation.segments=[];
  h.controller.update(d.book,d.generation);
  h.controller.setStreams('g1',{...d.manifest,chunks:[],status:'failed',error:'FFmpeg is missing'});
  await settle();assert.match(h.state().error,/FFmpeg/);assert.equal(h.state().waiting,false);assert.deepEqual(h.saved,[]);
  h.controller.dispose();
});


function hlsData() {
  const d=streamData();
  d.manifest={...d.manifest,chunks:[{key:'hls-chapter',hls:true,chapter_id:'c1',start:0,duration:7,status:'ready',
    parts:[...d.manifest.chunks[0].parts,{...d.manifest.chunks[1].parts[0],start:5}]}]};
  return d;
}
function hlsHarness() {
  const bound=[],destroyed=[];
  const h=streamHarness({bindPlayback:(audio,key,offset,signal,onFatal)=>{
    const entry={audio,key,offset,signal,onFatal};bound.push(entry);audio.src='mediasource:'+key;audio.load();
    return {destroy:()=>destroyed.push(key)};
  }});
  return {...h,bound,destroyed};
}

test('HLS chapter playback delegates all buffering to one transport and crosses sources without native element handoffs',async()=>{
  const h=hlsHarness(),d=hlsData();h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);h.controller.toggle();await settle();
  assert.equal(h.bound.length,1);assert.equal(h.bound[0].key,'hls-chapter');assert.equal(h.bound[0].offset,0);
  assert.deepEqual(h.loads,[]);assert.deepEqual(h.fetched,[]);
  const plays=h.audio.plays;
  for(const time of [2.4,5.3]){h.audio.currentTime=time;h.audio.dispatchEvent(new Event('timeupdate'));}
  assert.equal(h.state().segment.id,'s3');assert.ok(Math.abs(h.state().position-.3)<1e-8);
  assert.equal(h.bound.length,1);assert.equal(h.audio.plays,plays);assert.deepEqual(h.destroyed,[]);
  h.controller.dispose();assert.deepEqual(h.destroyed,['hls-chapter']);assert.equal(h.bound[0].signal.aborted,true);
});

test('seeking within an HLS program uses the existing transport and exact source-relative position',async()=>{
  const h=hlsHarness(),d=hlsData();h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);await settle();
  h.controller.select('s2',true,1.4);await settle();assert.equal(h.audio.currentTime,3.4);assert.equal(h.bound.length,1);
  h.controller.seek(.5);await settle();assert.equal(h.audio.currentTime,2.5);await h.controller.pause();
  assert.equal(h.saved.at(-1).segment_id,'s2');assert.equal(h.saved.at(-1).offset,.5);
  h.controller.dispose();
});

test('a larger HLS program preserves the valid playing prefix and joins new sources at the correct media offset',async()=>{
  const h=hlsHarness(),d=hlsData(),full=d.manifest.chunks[0];
  const prefix={...full,key:'hls-prefix',duration:5,parts:full.parts.slice(0,2)};
  h.controller.update(d.book,d.generation);h.controller.setStreams('g1',{...d.manifest,status:'partial',chunks:[prefix]});h.controller.toggle();await settle();
  h.audio.currentTime=3.2;h.audio.dispatchEvent(new Event('timeupdate'));
  h.controller.setStreams('g1',d.manifest);h.controller.update(d.book,{...d.generation,segments:[...d.generation.segments]});await settle();
  assert.equal(h.state().streamKey,'hls-prefix');assert.equal(h.bound.length,1);assert.deepEqual(h.destroyed,[]);assert.equal(h.audio.currentTime,3.2);
  h.audio.currentTime=5;h.audio.ended=true;h.audio.dispatchEvent(new Event('ended'));await settle();
  assert.equal(h.state().segment.id,'s3');assert.equal(h.state().streamKey,'hls-chapter');assert.equal(h.bound.length,2);
  assert.equal(h.bound[1].audio,h.bound[0].audio);assert.equal(h.bound[1].offset,5);assert.equal(h.audio.currentTime,5);
  assert.deepEqual(h.destroyed,['hls-prefix']);assert.equal(h.state().position,0);h.controller.dispose();
});

test('HLS source corrections and music changes invalidate the affected transport while normal manifest polls do not',async()=>{
  const h=hlsHarness(),d=hlsData();h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);h.controller.toggle();await settle();
  h.audio.currentTime=3.4;h.audio.dispatchEvent(new Event('timeupdate'));
  h.controller.setStreams('g1',JSON.parse(JSON.stringify(d.manifest)));await settle();assert.equal(h.bound.length,1);
  const changed={...d.manifest,music_volume:.35,chunks:[{...d.manifest.chunks[0],key:'new-mix'}]};
  h.controller.setStreams('g1',changed);await settle();assert.equal(h.bound.length,2);assert.equal(h.bound[1].offset,3.4);assert.equal(h.state().segment.id,'s2');
  h.controller.update(d.book,{...d.generation,segments:[d.generation.segments[0],{...d.generation.segments[1],audio_hash:'corrected-s2'},d.generation.segments[2]]});await settle();
  assert.equal(h.state().waiting,true);assert.equal(h.state().playing,false);assert.equal(h.destroyed.length,2);assert.deepEqual(h.loads,[]);
  h.controller.dispose();
});

test('fatal HLS failures preserve the source anchor and ignore obsolete fatal callbacks after retry',async()=>{
  const h=hlsHarness(),d=hlsData();h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);h.controller.toggle();await settle();
  h.audio.currentTime=3.3;h.audio.dispatchEvent(new Event('timeupdate'));h.bound[0].onFatal(new Error('HLS retry limit reached'));await settle();
  assert.match(h.state().error,/retry limit/);assert.equal(h.state().segment.id,'s2');assert.equal(h.state().playing,false);
  h.controller.toggle();await settle();assert.equal(h.bound.length,2);assert.equal(h.bound[1].offset,3.3);assert.equal(h.state().playing,true);
  h.bound[0].onFatal(new Error('obsolete'));await settle();assert.equal(h.state().error,'');assert.equal(h.state().playing,true);
  h.controller.dispose();
});

test('HLS preview tears down the chapter transport then restores its original source and offset paused',async()=>{
  const h=hlsHarness(),d=hlsData();h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);h.controller.toggle();await settle();
  h.audio.currentTime=3.2;h.audio.dispatchEvent(new Event('timeupdate'));h.controller.preview('s3');await settle();
  assert.deepEqual(h.destroyed,['hls-chapter']);assert.equal(h.state().previewing,true);assert.deepEqual(h.loads,['hash-s3']);
  h.audio.ended=true;h.audio.dispatchEvent(new Event('ended'));await settle();
  assert.equal(h.bound.length,2);assert.equal(h.bound[1].offset,3.2);assert.equal(h.state().segment.id,'s2');assert.equal(h.state().playing,false);
  h.controller.dispose();
});

test('an HLS manifest never silently falls back to MP3 when its transport is unavailable',async()=>{
  const h=streamHarness(),d=hlsData();h.controller.update(d.book,d.generation);h.controller.setStreams('g1',d.manifest);h.controller.toggle();await settle();
  assert.match(h.state().error,/HLS playback is not supported/);assert.deepEqual(h.loads,[]);assert.deepEqual(h.fetched,[]);
  h.controller.dispose();
});
