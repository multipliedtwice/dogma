const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const selectionHelpers=(()=>{const module={exports:{}};new Function('module','exports',ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../src/lib/readerSelection.ts'),'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText)(module,module.exports);return module.exports;})();

function load(file, environment = {}) {
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '..', file), 'utf8'), {
    fileName: file, compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX},
  }).outputText;
  const module = {exports: {}};
  new Function('module', 'exports', ...Object.keys(environment), code)(module, module.exports, ...Object.values(environment));
  return module.exports;
}
function clock() {
  const timers = new Map(); let id = 0, now = 0;
  return {
    setTimeout: (fn, delay) => { timers.set(++id, {fn, at: now + delay}); return id; },
    clearTimeout: key => timers.delete(key),
    advance: delta => { now += delta; for (const [key, item] of [...timers]) if (item.at <= now) { timers.delete(key); item.fn(); } },
    size: () => timers.size,
  };
}
const settle = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); };
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; }
function events(extra = {}) {
  const listeners = new Map();
  return {...extra, addEventListener: (name, fn) => { const all = listeners.get(name) || new Set(); all.add(fn); listeners.set(name, all); },
    removeEventListener: (name, fn) => listeners.get(name)?.delete(fn), emit: name => { for (const fn of listeners.get(name) || []) fn({}); }};
}
function reader() {
  const timer = clock();
  const helpers = load('src/lib/readerNavigation.ts', timer);
  const layout = {top: 1000, height: 2400};
  const document = events({defaultView: events({matchMedia: () => ({matches: false})}), visibilityState: 'visible'});
  const root = events({ownerDocument: document, scrollTop: 2080, clientHeight: 600, getBoundingClientRect: () => ({top: 30})});
  const source = {dataset: {sourceId: 'long'}, getBoundingClientRect: () => ({top: 30 + layout.top - root.scrollTop, height: layout.height}),
    scrollIntoView: () => { root.scrollTop = layout.top; }, focus: () => {}};
  const first = {dataset: {sourceId: 'first'}, getBoundingClientRect: () => ({top: 30 - root.scrollTop, height: 800})};
  document.getElementById = id => id === 'long' ? source : id === 'first' ? first : null;
  root.contains = element => [first, source].includes(element);
  root.querySelectorAll = () => [first, source];
  root.scrolls = [];
  root.scrollTo = value => { root.scrolls.push(value); root.scrollTop = value.top; };
  return {helpers, layout, root, source, timer};
}

test('long-paragraph manual scrolling saves its source fraction, not just root pixels', () => {
  const {helpers, root} = reader(), saved = [];
  const binding = helpers.bindReadingPosition(root, () => {}, (source, offset, fraction) => saved.push({source, offset, fraction}));
  root.emit('scroll'); binding.flush();
  assert.deepEqual(saved, [{source: 'long', offset: 2080, fraction: .5}]);
  binding.dispose();
});

test('restoring after font and preceding-layout height changes preserves the middle of a long paragraph', () => {
  const {helpers, root, layout} = reader();
  const position = helpers.readingOffset(root, 'long');
  layout.top = 3100; layout.height = 4800; root.scrollTop = 0;
  assert.equal(helpers.scrollToReadingPosition(root, 'long', position.offset, position.source_fraction), true);
  assert.equal(root.scrollTop, 5380);
  assert.equal(helpers.readingOffset(root, 'long').source_fraction, .5);
});

test('playback centers the exact spoken point instead of pinning its block to the top', () => {
  const {helpers, root} = reader();
  helpers.scrollToPlaybackPosition(root, 'long', .5);
  assert.equal(root.scrollTop, 1900);
  assert.equal(root.scrolls.at(-1).behavior, 'instant');
  const viewport = root.getBoundingClientRect();
  const passage = root.ownerDocument.getElementById('long').getBoundingClientRect();
  assert.equal(passage.top + passage.height * .5, viewport.top + root.clientHeight * .5);
});

test('playback auto-follow scrolls smoothly after the instant initial restore', () => {
  const {helpers, root} = reader();
  helpers.scrollToPlaybackPosition(root, 'long', .5, true);
  assert.equal(root.scrolls.at(-1).behavior, 'smooth');
});

test('auto-follow glides for three seconds on desktop and speeds up with a narrower viewport', () => {
  const run = (width, halfway, finished) => {
    const {helpers, root} = reader();
    const page = root.ownerDocument.defaultView;
    const frames = new Map(); let frame = 0;
    root.clientWidth = width;
    page.performance = {now: () => 0};
    page.requestAnimationFrame = callback => { frames.set(++frame, callback); return frame; };
    page.cancelAnimationFrame = id => frames.delete(id);
    const advance = now => { const [id, callback] = frames.entries().next().value; frames.delete(id); callback(now); };
    helpers.scrollToPlaybackPosition(root, 'long', .5, true);
    assert.equal(root.scrollTop, 2080);
    advance(halfway); assert.equal(root.scrollTop, 1990);
    advance(finished); assert.equal(root.scrollTop, 1900);
    assert.ok(root.scrolls.every(call => call.behavior === 'instant'));
  };
  run(900, 1500, 3000);
  run(450, 750, 1500);
});

test('restoring a source fraction also accounts for a changed viewport reading inset', () => {
  const {helpers, root, layout} = reader();
  const position = helpers.readingOffset(root, 'long');
  root.clientHeight = 300; layout.height = 1200; layout.top = 500;
  helpers.scrollToReadingPosition(root, 'long', position.offset, position.source_fraction);
  assert.equal(root.scrollTop, 1040);
  assert.equal(helpers.readingOffset(root, 'long').source_fraction, .5);
});

test('zero source fraction is a real source-relative position, not a missing offset', () => {
  const {helpers, root} = reader();
  helpers.scrollToReadingPosition(root, 'long', 9999, 0);
  assert.equal(root.scrollTop, 880);
});

test('legacy persisted pixels retain exact placement when the source still matches', () => {
  const {helpers, root} = reader();
  helpers.scrollToReadingPosition(root, 'long', 2080);
  assert.equal(root.scrollTop, 2080);
});

test('legacy pixels with changed layout still fall back to their saved source instead of a wrong paragraph', () => {
  const {helpers, root, layout} = reader();
  layout.top = 8000;
  helpers.scrollToReadingPosition(root, 'long', 2080);
  assert.equal(root.scrollTop, 8000);
});

test('malformed fractions fall back to legacy behavior and never produce a nonfinite scroll target', () => {
  for (const fraction of [NaN, Infinity, -1, 2]) {
    const {helpers, root} = reader();
    helpers.scrollToReadingPosition(root, 'long', 2080, fraction);
    assert.equal(root.scrollTop, 2080);
  }
});

const app = ts.createSourceFile('App.tsx', fs.readFileSync(path.join(__dirname, '../src/App.tsx'), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function variable(name, environment) {
  let found;
  const visit = node => { if (ts.isVariableDeclaration(node) && node.name.getText(app) === name) found = node; else ts.forEachChild(node, visit); };
  visit(app); assert.ok(found, name);
  const code = ts.transpileModule('return (' + found.initializer.getText(app) + ')', {compilerOptions: {target: ts.ScriptTarget.ES2022}}).outputText;
  return new Function(...Object.keys(environment), code)(...Object.values(environment));
}

async function openBookAtSavedPosition(book) {
  const destinations = [];
  const open = variable('open', {flushReading: {current: () => {}}, selectionEpoch: {current: 0}, restoringPlayback: {current: ''}, defaultGeneration: selectionHelpers.defaultGeneration,
    afterPlaybackSaved: async (_, read) => read(), player: {stop: async () => {}},
    api: async operation => operation === 'book' ? book : {id: 'generation', book_id: book.id},
    manualReading: {current: false}, setModal: () => {}, setNavigation: value => destinations.push(value),
    setGeneration: () => {}, setSelectedSource: () => {}, setGenerationSource: () => {}, setPlayFrom: () => {},
    setChapterQuery: () => {}, setNavTab: () => {}, setBrowsing: () => {}, setBook: () => {}, setSelectedChapters: () => {},
    defaultNarrationChapters: () => [], setChapterId: () => {}, setQuery: () => {}, focusIfLost: () => {}});
  await open(book.id);
  return destinations.at(-1);
}

function savedBook(position, reading_position = null) {
  return {id: 'book', chapters: [{id: 'chapter', blocks: [{id: 'long'}]}], generations: [], position, reading_position};
}

test('reopening a book without narration does not discard the saved relative restore offset', async () => {
  const book = savedBook(null, {chapter_id: 'chapter', source_id: 'long', offset: 2080, source_fraction: .5});
  assert.deepEqual(await openBookAtSavedPosition(book), {bookId: 'book', sourceId: 'long', focus: false, offset: 2080, sourceFraction: .5});
});

test('legacy reading state in positions keeps its absolute pixels through the actual App open callback', async () => {
  const book = savedBook({chapter_id: 'chapter', source_id: 'long', offset: 2080});
  const destination = await openBookAtSavedPosition(book);
  assert.equal(destination.offset, 2080);
  const {helpers, root} = reader();
  root.scrollTop = 0;
  helpers.scrollToReadingPosition(root, destination.sourceId, destination.offset, destination.sourceFraction);
  assert.equal(root.scrollTop, 2080);
});

test('a saved listening block wins on reopen and its seconds are never interpreted as reader pixels', async () => {
  const book = savedBook({generation_id: 'generation', chapter_id: 'chapter', source_id: 'long', offset: 75});
  book.generations = [{id: 'generation'}];
  assert.deepEqual(await openBookAtSavedPosition(book), {bookId: 'book', sourceId: 'long', focus: false, playback: true});
  book.reading_position = {chapter_id: 'chapter', source_id: 'long', offset: 2080, source_fraction: .5};
  assert.deepEqual(await openBookAtSavedPosition(book), {bookId: 'book', sourceId: 'long', focus: false, playback: true});
});

test('click-to-save uses the same relative coordinate as debounced scrolling', async () => {
  const {helpers, root} = reader(), writes = [];
  const save = variable('saveReading', {book: {id: 'book'}, flushReading: {current: () => {}},
    readerIndex: {sources: new Map([['long', {chapter: {id: 'chapter'}}]])}, viewRef: {current: root},
    readingOffset: helpers.readingOffset, storeReading: variable('storeReading', {setBook: () => {}, api: async (op, data) => writes.push({op, data}), setError: () => {}, t: () => '', errorText: String})});
  save('long'); await settle();
  assert.deepEqual(writes[0].data.position, {chapter_id: 'chapter', source_id: 'long', offset: 2080, source_fraction: .5});
});

function pauseHarness() {
  const timer = clock(), acceptance = deferred(), requests = [], pending = [], errors = [];
  const {createPreparationPause} = load('src/lib/preparationPause.ts', timer);
  let sent = 0;
  const controller = createPreparationPause({pause: () => { sent++; return acceptance.promise; },
    refresh: (id, signal) => { const result = deferred(); requests.push({id, signal, ...result}); return result.promise; },
    pending: id => pending.push(id), refreshError: error => errors.push(error)});
  return {controller, timer, acceptance, requests, pending, errors, sent: () => sent};
}
const snapshot = (status, active, id = 'generation') => ({generation: {id, status}, health: {active}});

test('accepted pause remains requested while cleanup is active, then clears only on final inactive confirmation', async () => {
  const h = pauseHarness();
  const accepted = h.controller.request('generation');
  assert.equal(await h.controller.request('generation'), false); assert.equal(h.sent(), 1);
  h.acceptance.resolve({requested: true}); assert.equal(await accepted, true);
  assert.equal(h.requests.length, 1, 'confirmation starts immediately after acceptance');
  assert.deepEqual(h.pending, ['generation']);
  h.requests[0].resolve(snapshot('paused', 'generation')); await settle();
  assert.deepEqual(h.pending, ['generation']); assert.equal(await h.controller.request('generation'), false);
  h.timer.advance(1000); h.requests[1].resolve(snapshot('paused', null)); await settle();
  assert.deepEqual(h.pending, ['generation', null]); assert.equal(h.timer.size(), 0);
  h.controller.dispose();
});

test('inactive health alone cannot clear a pause while generation still reports active work', async () => {
  const h = pauseHarness(), start = h.controller.request('generation');
  h.acceptance.resolve({requested: true}); await start;
  h.requests[0].resolve(snapshot('rendering', null)); await settle();
  assert.deepEqual(h.pending, ['generation']);
  h.timer.advance(1000); h.requests[1].resolve(snapshot('ready', null)); await settle();
  assert.deepEqual(h.pending, ['generation', null]); h.controller.dispose();
});

test('confirmation failures retain the requested pause and retry without sending another pause command', async () => {
  const h = pauseHarness(), start = h.controller.request('generation');
  h.acceptance.resolve({requested: true}); await start;
  h.requests[0].reject(new Error('controlled refresh failure')); await settle();
  assert.deepEqual(h.pending, ['generation']); assert.equal(h.errors.length, 1);
  h.timer.advance(1000); h.requests[1].resolve(snapshot('failed', null)); await settle();
  assert.deepEqual(h.pending, ['generation', null]); assert.equal(h.sent(), 1); h.controller.dispose();
});

test('hung confirmation is bounded and aborted, and its late answer cannot clear a newer poll', async () => {
  const h = pauseHarness(), start = h.controller.request('generation');
  h.acceptance.resolve({requested: true}); await start;
  h.timer.advance(5000); await settle();
  assert.equal(h.requests[0].signal.aborted, true); assert.equal(h.errors.length, 1);
  h.timer.advance(1000); assert.equal(h.requests.length, 2);
  h.requests[0].resolve(snapshot('paused', null)); await settle();
  assert.deepEqual(h.pending, ['generation']);
  h.requests[1].resolve(snapshot('paused', null)); await settle();
  assert.deepEqual(h.pending, ['generation', null]); h.controller.dispose();
});

test('disposal aborts confirmation and ignores late state from an old generation', async () => {
  const h = pauseHarness(), start = h.controller.request('generation');
  h.acceptance.resolve({requested: true}); await start;
  h.controller.dispose(); assert.equal(h.requests[0].signal.aborted, true);
  const newer = h.controller.request('next'); await newer;
  h.requests[0].resolve(snapshot('paused', null)); await settle();
  assert.deepEqual(h.pending, ['generation', 'next']);
  h.requests[1].resolve(snapshot('paused', null, 'next')); await settle();
  assert.deepEqual(h.pending, ['generation', 'next', null]); h.controller.dispose();
});

test('a rejected pause restores the action and a mismatched confirmation cannot clear another generation', async () => {
  const h = pauseHarness(), start = h.controller.request('generation');
  h.acceptance.reject(new Error('pause refused'));
  await assert.rejects(start, /pause refused/); assert.deepEqual(h.pending, ['generation', null]);
  assert.equal(h.timer.size(), 0); h.controller.dispose();
  const other = pauseHarness(), accepted = other.controller.request('generation');
  other.acceptance.resolve({requested: true}); await accepted;
  other.requests[0].resolve(snapshot('paused', null, 'other')); await settle();
  assert.deepEqual(other.pending, ['generation']); assert.equal(other.errors.length, 1); other.controller.dispose();
});

test('older health responses cannot reinstate active state after pause confirmation', async () => {
  const replies = [], healthRefresh = {current: 0}; let displayed;
  const refresh = variable('refreshHealth', {useCallback: fn => fn, healthRefresh,
    api: () => { const response = deferred(); replies.push(response); return response.promise; },
    setHealth: value => { displayed = value; }, pollResult: () => {}});
  const old = refresh(), newest = refresh();
  replies[1].resolve({active: null}); await newest;
  replies[0].resolve({active: 'generation'}); await old;
  assert.deepEqual(displayed, {active: null});
});

test('actual status-action JSX retains a disabled pause through acceptance and exposes enabled Resume only when inactive', () => {
  const {sourceTree} = require('./layout_source.cjs');
  const chapter = {id: 'chapter', title: 'Chapter', blocks: [{id: 'paragraph', kind: 'paragraph', text: 'Text', html: '<p>Text</p>', assets: []}]};
  const generation = {id: 'generation', book_id: 'book', name: 'Narration', status: 'rendering', config: {chapters: ['chapter']},
    analysis: {complete: true}, cast: [], segments: [], exports: []};
  const fixture = {states: {connected: true, book: {id: 'book', title: 'Title', author: 'Author', chapters: [chapter],
      bookmarks: [], generations: [], visuals: [], warnings: []}, chapterId: chapter.id, generation, pausing: 'generation',
      health: {ready: true, active: 'generation', offline: true, missing: []},
      settings: {theme: 'light', language: 'en', font_size: 20, line_height: 1.9, playback_rate: 1, volume: 1, follow: true}},
    player: {playing: false, waiting: false, position: 0, error: '', segment: null}};
  const walk = node => Array.isArray(node) ? node.flatMap(walk) : !node || typeof node !== 'object' ? [] : [node, ...walk(node.props?.children)];
  const text = node => Array.isArray(node) ? node.map(text).join('') : !node || typeof node === 'boolean' ? '' : typeof node === 'object' ? text(node.props?.children) : String(node);
  const actions = () => walk(sourceTree('src/App.tsx', fixture).tree).filter(node => node.tag === 'button');
  for (const status of ['rendering', 'paused']) {
    fixture.states.generation = {...generation, status};
    const buttons = actions();
    assert.equal(buttons.find(button => text(button) === 'Requesting pause…')?.props.disabled, true);
    assert.equal(buttons.some(button => text(button) === 'Resume preparation'), false);
  }
  fixture.states.pausing = null; fixture.states.health.active = null;
  const buttons = actions();
  assert.equal(buttons.some(button => text(button) === 'Requesting pause…'), false);
  assert.equal(buttons.find(button => text(button) === 'Resume preparation')?.props.disabled, false);
});

test('successful deletion releases only its own pending pause and cancels late confirmation', async () => {
  const h = pauseHarness(), start = h.controller.request('generation');
  h.acceptance.resolve({requested: true}); await start;
  h.controller.removed('unrelated'); assert.deepEqual(h.pending, ['generation']);
  h.controller.removed('generation');
  assert.deepEqual(h.pending, ['generation', null]); assert.equal(h.requests[0].signal.aborted, true);
  h.requests[0].resolve(snapshot('rendering', 'generation')); await settle();
  assert.deepEqual(h.pending, ['generation', null]); assert.equal(h.timer.size(), 0);
  h.controller.dispose();
});


test('opening Settings cannot apply a late active-health reply after pause confirmation', async () => {
  const replies = [], healthRefresh = {current: 0}; let displayed;
  const refreshHealth = variable('refreshHealth', {useCallback: fn => fn, healthRefresh,
    api: () => { const response = deferred(); replies.push(response); return response.promise; },
    setHealth: value => { displayed = value; }, pollResult: () => {}});
  const open = variable('openModal', {setError: () => {}, setModal: () => {}, refreshHealth, errorText: String});
  open('settings');
  assert.equal(replies.length, 1);
  const confirmation = refreshHealth();
  replies[1].resolve({active: null}); await confirmation;
  replies[0].resolve({active: 'generation'}); await settle();
  assert.deepEqual(displayed, {active: null});
});

test('slow pack verification refreshes health instead of reinstating its pre-pause active snapshot', async () => {
  const replies = [], pack = deferred(), healthRefresh = {current: 0}; let displayed, appliedSettings, appliedPath;
  const api = operation => {
    if (operation === 'select_pack') return pack.promise;
    assert.equal(operation, 'health');
    const response = deferred(); replies.push(response); return response.promise;
  };
  const refreshHealth = variable('refreshHealth', {useCallback: fn => fn, healthRefresh, api,
    setHealth: value => { displayed = value; }, pollResult: () => {}});
  const verify = variable('verifyPack', {settingsRequests: {current: Promise.resolve()}, api, packPath: '/pack',
    setSettings: value => { appliedSettings = value; }, setPackPath: value => { appliedPath = value; },
    refreshHealth, pollResult: () => {}, t: value => value, errorText: String, ru: false, setNotice: () => {}});
  const checking = verify(); await settle();
  const confirmation = refreshHealth(); replies[0].resolve({active: null}); await confirmation;
  pack.resolve({settings: {pack_path: '/pack'}, health: {active: 'generation'}}); await checking;
  assert.deepEqual(displayed, {active: null});
  assert.deepEqual(appliedSettings, {pack_path: '/pack'}); assert.equal(appliedPath, '/pack');
  assert.equal(replies.length, 2, 'pack completion requests fresh ordered health');
  replies[1].resolve({active: null, ready: true}); await settle();
  assert.deepEqual(displayed, {active: null, ready: true});
});


test('BookMarkup materializes long-paragraph content before the App layout restore consumes its position', () => {
  const {helpers, root, layout} = reader();
  layout.height = 14; root.scrollTop = 0;
  const layoutEffects = [], passiveEffects = [];
  const markup = {replaceChildren: () => { layout.height = 4800; }, querySelectorAll: () => []};
  const React = {memo: fn => fn, useRef: () => ({current: markup}),
    useEffect: fn => passiveEffects.push(fn), useLayoutEffect: fn => layoutEffects.push(fn)};
  const require = name => {
    if (name === 'react') return React;
    if (name === 'react/jsx-runtime') return {jsx: () => null};
    if (name === 'dompurify') return {default: {sanitize: value => value}};
    if (name.endsWith('imageCache')) return {createImageCache: () => ({}), whenVisible: () => () => {}};
    return {};
  };
  const {BookMarkup} = load('src/components/Media.tsx', {require});
  BookMarkup({block: {html: '<p>Long paragraph</p>'}, book: 'book'});
  for (const effect of layoutEffects) effect();
  helpers.scrollToReadingPosition(root, 'long', 2080, .5);
  for (const effect of passiveEffects) effect();
  assert.equal(root.scrollTop, 3280);
  assert.equal(helpers.readingOffset(root, 'long').source_fraction, .5);
});


test('a stale failed Settings health request cannot show an error after newer pause confirmation succeeds', async () => {
  const replies = [], healthRefresh = {current: 0}, errors = [], polls = [];
  const refreshHealth = variable('refreshHealth', {useCallback: fn => fn, healthRefresh, errorText: String,
    api: () => { const response = deferred(); replies.push(response); return response.promise; },
    setHealth: () => {}, pollResult: (...args) => polls.push(args)});
  const open = variable('openModal', {setError: error => errors.push(error), setModal: () => {}, refreshHealth, errorText: String});
  open('settings');
  const confirmation = refreshHealth();
  replies[1].resolve({active: null}); await confirmation;
  replies[0].reject(new Error('stale health transport failure')); await settle();
  assert.deepEqual(errors.filter(Boolean), []);
  assert.deepEqual(polls.filter(([, message]) => Boolean(message)), []);
});

test('latest health failures remain visible but an aborted confirmation cannot publish its late error', async () => {
  const replies = [], healthRefresh = {current: 0}, polls = [];
  const refresh = variable('refreshHealth', {useCallback: fn => fn, healthRefresh, errorText: String,
    api: () => { const response = deferred(); replies.push(response); return response.promise; },
    setHealth: () => {}, pollResult: (...args) => polls.push(args)});
  const current = refresh();
  replies[0].reject(new Error('current health failure'));
  await assert.rejects(current, /current health failure/);
  assert.equal(polls.length, 1);
  assert.equal(polls[0][0], 'health');
  assert.match(polls[0][1], /current health failure/);
  const controller = new AbortController(), aborted = refresh(controller.signal);
  controller.abort(); replies[1].reject(new Error('aborted health failure'));
  await assert.rejects(aborted, /aborted health failure/);
  assert.equal(polls.length, 1);
  const recovery = refresh(); replies[2].resolve({active: null}); await recovery;
  assert.deepEqual(polls.at(-1), ['health']);
});

test('lost runtime shows one connection banner and a disconnected badge, then platform restart advice after a failed retry', () => {
  const {sourceTree} = require('./layout_source.cjs');
  const chapter = {id: 'chapter', title: 'Chapter', blocks: [{id: 'paragraph', kind: 'paragraph', text: 'Text', html: '<p>Text</p>', assets: []}]};
  const walk = node => Array.isArray(node) ? node.flatMap(walk) : !node || typeof node !== 'object' ? [] : [node, ...walk(node.props?.children)];
  const text = node => Array.isArray(node) ? node.map(text).join('') : !node || typeof node === 'boolean' ? '' : typeof node === 'object' ? text(node.props?.children) : String(node);
  const render = (fixture) => walk(sourceTree('src/App.tsx', {runtimeLost: true, ...fixture, states: {connected: true, book: {id: 'book', title: 'Title', author: 'Author', chapters: [chapter],
      bookmarks: [], generations: [], visuals: [], warnings: []}, chapterId: chapter.id, health: {ready: true, active: null, offline: true, missing: []},
      error: 'Could not save reading position: The local runtime is not responding.',
      settings: {theme: 'light', language: 'en', font_size: 20, line_height: 1.9, playback_rate: 1, volume: 1, follow: true, online_recommendations: false, recommendation_genres: []}, ...fixture.states}}).tree);
  const first = render({states: {runtimeRetries: 0}});
  const alerts = first.filter(node => node.props?.role === 'alert');
  assert.equal(alerts.length, 1);
  assert.match(text(alerts[0]), /^Lost connection to the local runtime\./);
  assert.ok(walk(alerts[0]).some(node => node.tag === 'button' && text(node) === 'Retry'));
  const badge = first.find(node => String(node.props?.className).includes('offline-badge'));
  assert.match(badge.props.className, /disconnected/); assert.equal(text(badge), 'Runtime disconnected');
  const desktop = render({native: true, states: {runtimeRetries: 1}}).filter(node => node.props?.role === 'alert');
  assert.match(text(desktop[0]), /Quit and reopen the app to restart it\./);
  const browser = render({native: false, states: {runtimeRetries: 1}}).filter(node => node.props?.role === 'alert');
  assert.match(text(browser[0]), /open the new URL printed in its terminal\./);
  const connected = walk(sourceTree('src/App.tsx', {runtimeLost: false, states: {connected: true, book: null, health: {ready: true, missing: []}, settings: {theme: 'light', language: 'en', online_recommendations: false, recommendation_genres: []}}}).tree);
  assert.equal(text(connected.find(node => String(node.props?.className).includes('offline-badge'))), 'On your computer');
});

test('navigating to the hidden first heading focuses a named target and marks the visible chapter title', async () => {
  const {sourceTree} = require('./layout_source.cjs');
  const heading = {id: 'heading', kind: 'heading', text: 'Harbor morning', html: '<h1>Harbor morning</h1>', assets: []};
  const paragraph = {id: 'paragraph', kind: 'paragraph', text: 'Text', html: '<p>Text</p>', assets: []};
  const chapter = {id: 'chapter', title: 'Harbor morning', blocks: [heading, paragraph]};
  const readerIndex = {sources: new Map([[heading.id, {chapter, block: heading}], [paragraph.id, {chapter, block: paragraph}]])};
  let selected = '', navigation = null, mode = '';
  const navigate = variable('navigateSource', {useCallback: fn => fn, book: {id: 'book'}, readerIndex, flushReading: {current: () => {}}, manualReading: {current: false},
    setBrowsing: () => {}, setMode: value => { mode = value; }, setChapterId: () => {}, setSelectedSource: value => { selected = value; },
    setNavigation: value => { navigation = value; }, storeReading: () => {}});
  navigate(heading.id);
  assert.equal(selected, heading.id); assert.equal(mode, '', 'Show in text keeps the current view');
  assert.deepEqual(navigation, {bookId: 'book', sourceId: heading.id, focus: true});
  const walk = node => Array.isArray(node) ? node.flatMap(walk) : !node || typeof node !== 'object' ? [] : [node, ...walk(node.props?.children)];
  const render = (selectedSource, speaking) => walk(sourceTree('src/App.tsx', {player: {playing: false, waiting: false, position: 0, error: '', segment: speaking ? {id: 's', source_id: speaking, chapter_id: chapter.id} : null},
    states: {connected: true, book: {id: 'book', title: 'Title', author: 'Author', chapters: [chapter], bookmarks: [], generations: [], visuals: [], warnings: []},
      chapterId: chapter.id, selectedSource, health: {ready: true, active: null, missing: []},
      settings: {theme: 'light', language: 'en', font_size: 20, line_height: 1.9, playback_rate: 1, volume: 1, follow: true}}}).tree);
  const title = nodes => nodes.find(node => node.tag === 'h1' && String(node.props.className).includes('chapter-title'));
  const selectedTree = render(heading.id, '');
  assert.match(title(selectedTree).props.className, /title-selected/);
  const target = selectedTree.find(node => node.tag === 'section' && node.props.id === heading.id);
  assert.equal(target.props['aria-label'], 'Harbor morning'); assert.equal(target.props.tabIndex, -1);
  assert.equal(selectedTree.find(node => node.tag === 'section' && node.props.id === paragraph.id).props['aria-label'], undefined);
  assert.match(title(render('', heading.id)).props.className, /title-speaking/);
  const other = title(render(paragraph.id, paragraph.id)).props.className;
  assert.doesNotMatch(other, /title-selected|title-speaking/);
});
