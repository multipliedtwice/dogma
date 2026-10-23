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
const {exportSelection} = load('src/lib/exportSelection.ts');
const {playbackShortcut, deletionNotice} = load('src/lib/readerSelection.ts');
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
  return {id, chapter_id: 'c1', source_id: 'p-' + id, start: 0, end: 10, text: 'text', spoken: 'text',
    status, audio_hash: status === 'ready' ? 'hash-' + id : null, duration: status === 'ready' ? 2 : 0, cues: [], ...overrides};
}
function harness(savePosition = async () => {}) {
  const audio = audioElement();
  const history = [], saved = [], loads = [];
  const controller = createPlaybackController(audio, {
    loadAudio: async hash => { loads.push(hash); return new Blob(['synthetic-media-fixture']); },
    savePosition: async (id, value) => { saved.push({id, ...value}); return savePosition(id, value); },
    notify: state => history.push(state),
    createUrl: () => 'blob:fixture',
    revokeUrl: () => {},
  });
  return {controller, audio, saved, loads, state: () => history.at(-1)};
}

test('a failed position save does not stop a newly ready passage from loading and playing', async () => {
  let fail = true;
  const h = harness(async () => { if (fail) throw new Error('controlled save failure'); });
  const book = {id: 'b1', position: null};
  const pending = {id: 'g1', book_id: 'b1', status: 'rendering', segments: [segment('s1', 'pending')]};
  h.controller.update(book, pending); h.controller.toggle(); await settle();
  assert.match(h.state().saveError, /controlled save failure/);
  assert.equal(h.state().error, '');
  fail = false;
  h.controller.update(book, {...pending, segments: [segment('s1')]}); await settle();
  assert.deepEqual(h.loads, ['hash-s1']);
  assert.equal(h.state().playing, true);
  h.controller.pause(); await settle();
  assert.equal(h.state().saveError, '');
  h.controller.dispose();
});

test('a corrected revision keeps the current passage and offset instead of restarting the book', async () => {
  const h = harness();
  const book = {id: 'b1', position: {generation_id: 'g1', segment_id: 's1', chapter_id: 'c1', source_id: 'p-s1', offset: 0}};
  const original = {id: 'g1', book_id: 'b1', parent_id: null, status: 'ready', segments: [segment('s1'), segment('s2'), segment('s3')]};
  h.controller.update(book, original); await settle();
  h.controller.select('s3', false, 1.25); await settle();
  const revision = {...original, id: 'g2', parent_id: 'g1', status: 'paused', segments: [segment('s1'), segment('s2', 'pending'), segment('s3')]};
  h.controller.update(book, revision); await settle();
  assert.equal(h.state().segment.id, 's3');
  const latest = h.saved.at(-1);
  assert.equal(latest.generation_id, 'g2'); assert.equal(latest.segment_id, 's3'); assert.equal(latest.offset, 1.25);
  h.controller.dispose();
});

test('an unrelated generation switch carries the book-wide listening passage and its matching offset', async () => {
  const h = harness();
  const book = {id: 'b1', position: null};
  const first = {id: 'g1', book_id: 'b1', parent_id: null, segments: [segment('s1'), segment('s2')]};
  h.controller.update(book, first); await settle(); h.controller.select('s2', false, 1); await settle();
  h.controller.update(book, {...first, id: 'g3', parent_id: null}); await settle();
  assert.equal(h.state().segment.id, 's2'); assert.equal(h.audio.currentTime, 1);
  h.controller.dispose();
});

test('a rejected position write does not poison flushing before opening another book', async () => {
  const previous = global.fetch;
  try {
    global.fetch = async () => ({ok: false, json: async () => ({error: 'controlled save failure'})});
    const transport = load('src/lib/api.ts');
    await assert.rejects(transport.api('save_reading_position', {book_id: 'book', position: {chapter_id: 'c1', offset: 0}}), /controlled save failure/);
    await transport.flushPositionWrites();
    await transport.flushPositionWrites();
  } finally { global.fetch = previous; }
});

test('export selection reasons follow the interface language', () => {
  const book = {id: 'b1', chapters: [{id: 'c1'}, {id: 'c2'}]};
  const generation = {book_id: 'b1', config: {chapters: ['c1', 'c2']}, segments: [segment('s1'), segment('s2', 'pending', {chapter_id: 'c2'})]};
  assert.match(exportSelection(book, generation, 'all', 'c1').reason, /Finish/);
  assert.match(exportSelection(book, generation, 'all', 'c1', 'ru').reason, /Завершите/);
  assert.equal(exportSelection(book, generation, 'ready', 'c1', 'ru').reason, '');
});

test('deletion notices follow the interface language', () => {
  assert.match(deletionNotice({deleted: true, cleanup_warnings: []}, 'Book', 'ru'), /^Книга удалена/);
  assert.match(deletionNotice({deleted: true, cleanup_warnings: ['x']}, 'Generation', 'ru'), /очистка диска не завершена/);
});

test('space on a focused summary opens it instead of toggling playback', () => {
  const element = tag => ({closest: selector => selector.split(',').map(item => item.trim()).includes(tag) ? {} : null});
  global.Element = function Element() {};
  global.HTMLElement = function HTMLElement() {};
  try {
    for (const tag of ['summary', 'audio']) {
      const target = Object.assign(Object.create(Element.prototype), element(tag));
      assert.equal(playbackShortcut({code: 'Space', target}), null);
    }
    const plain = Object.assign(Object.create(Element.prototype), element('p'));
    assert.equal(playbackShortcut({code: 'Space', target: plain}), 'toggle');
  } finally { delete global.Element; delete global.HTMLElement; }
});

test('reopening restores the saved reading scroll offset when it still shows the saved passage', () => {
  const module = {exports: {}};
  const code = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../src/lib/readerNavigation.ts'), 'utf8'), {compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS}}).outputText;
  new Function('module', 'exports', 'setTimeout', 'clearTimeout', code)(module, module.exports, () => 1, () => {});
  const scrolled = [];
  const make = paragraphTop => {
    const anchor = {scrollIntoView: () => scrolled.push('source'), focus: () => {}};
    const root = {scrollTop: 0, clientHeight: 600, getBoundingClientRect: () => ({top: 0}),
      ownerDocument: {getElementById: () => anchor, defaultView: {matchMedia: () => ({matches: false})}},
      contains: () => true, scrollTo: ({top}) => { root.scrollTop = top; },
      querySelectorAll: () => [{dataset: {sourceId: 'p1'}, getBoundingClientRect: () => ({top: -root.scrollTop})},
        {dataset: {sourceId: 'p2'}, getBoundingClientRect: () => ({top: paragraphTop - root.scrollTop})}]};
    return root;
  };
  const stable = make(900);
  assert.equal(module.exports.scrollToReadingPosition(stable, 'p2', 950), true);
  assert.equal(stable.scrollTop, 950); assert.deepEqual(scrolled, []);
  const reflowed = make(5000);
  assert.equal(module.exports.scrollToReadingPosition(reflowed, 'p2', 950), true);
  assert.deepEqual(scrolled, ['source']);
});
