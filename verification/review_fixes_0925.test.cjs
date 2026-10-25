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
const {nextGenerationName, revisionName} = load('src/lib/readerSelection.ts');
const {localMessage, operationMessage, blockKindLabel} = load('src/lib/i18n.ts');
const {bytes} = load('src/lib/utils.ts');
const {speakable} = load('src/lib/preparation.ts');
const {visualCaption} = load('src/lib/visualCaption.ts');
const app = fs.readFileSync(path.resolve(__dirname, '../src/App.tsx'), 'utf8');
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
function segment(id, chapter = 'c1', overrides = {}) {
  return {id, chapter_id: chapter, source_id: 'p-' + id, start: 0, end: 10, text: 'text', spoken: 'text',
    status: 'ready', audio_hash: 'hash-' + id, duration: 2, cues: [], ...overrides};
}
function harness(loader = async () => new Blob(['fixture'])) {
  const audio = audioElement();
  const history = [], saved = [];
  const controller = createPlaybackController(audio, {
    loadAudio: async hash => loader(hash),
    savePosition: async (id, value) => { saved.push({id, ...value}); },
    notify: state => history.push(state),
    createUrl: () => 'blob:fixture', revokeUrl: () => {},
  });
  return {controller, audio, saved, state: () => history.at(-1)};
}

test('switching to a generation without the current chapter keeps the saved listening position', async () => {
  const h = harness();
  const book = {id: 'b1', position: {generation_id: 'full', chapter_id: 'c1', source_id: 'p-a2', segment_id: 'a2', offset: 1.5}};
  const full = {id: 'full', book_id: 'b1', status: 'ready', segments: [segment('a1'), segment('a2'), segment('b1', 'c2')]};
  const partial = {id: 'partial', book_id: 'b1', status: 'ready', segments: [segment('x1', 'c2')]};
  h.controller.update(book, full); await settle();
  assert.equal(h.state().segment.id, 'a2');
  h.saved.length = 0;
  h.controller.update(book, partial); await settle();
  assert.equal(h.state().segment.id, 'x1');
  h.controller.update(book, full); await settle();
  assert.equal(h.saved.some(value => value.segment_id === 'x1'), false, 'The fallback passage must not overwrite the saved position');
  assert.equal(h.state().segment.id, 'a2'); assert.equal(h.audio.currentTime, 1.5);
  h.controller.dispose();
});

test('playing the fallback passage makes it the saved position', async () => {
  const h = harness();
  const book = {id: 'b1', position: {generation_id: 'full', chapter_id: 'c1', source_id: 'p-a1', segment_id: 'a1', offset: 0}};
  h.controller.update(book, {id: 'partial', book_id: 'b1', status: 'ready', segments: [segment('x1', 'c2')]}); await settle();
  h.controller.toggle(); await settle(); h.controller.pause(); await settle();
  assert.equal(h.saved.at(-1).segment_id, 'x1');
  h.controller.dispose();
});

test('a carried offset is scaled to the shorter passage and saved offsets never exceed the passage', async () => {
  const h = harness();
  const book = {id: 'b1', position: {generation_id: 'a', chapter_id: 'c1', source_id: 'p-s1', segment_id: 's1', offset: 1.5}};
  h.controller.update(book, {id: 'a', book_id: 'b1', status: 'ready', segments: [segment('s1', 'c1', {duration: 2})]}); await settle();
  h.saved.length = 0;
  h.controller.update(book, {id: 'b', book_id: 'b1', status: 'ready', segments: [segment('s1', 'c1', {duration: 1})]}); await settle();
  assert.ok(h.saved.filter(value => value.generation_id === 'b').every(value => value.offset <= 1), JSON.stringify(h.saved));
  assert.equal(h.state().position, .75);
  h.controller.select('s1', false, 30); await settle();
  assert.ok(h.saved.filter(value => value.generation_id === 'b').every(value => value.offset <= 1));
  h.controller.dispose();
});

test('a load error clears once repair changes the passage, without pressing Play', async () => {
  let fail = true;
  const h = harness(async () => { if (fail) throw new Error('A saved audio segment is missing or cannot be opened.'); return new Blob(['ok']); });
  const book = {id: 'b1', position: null};
  const ready = {id: 'g', book_id: 'b1', status: 'ready', segments: [segment('s1')]};
  h.controller.update(book, ready); await settle();
  assert.match(h.state().error, /missing/);
  h.controller.update(book, {...ready, status: 'rendering', segments: [segment('s1', 'c1', {status: 'pending', audio_hash: null, duration: 0})]}); await settle();
  assert.equal(h.state().error, '');
  fail = false;
  h.controller.update(book, ready); await settle();
  assert.equal(h.state().error, ''); assert.equal(h.state().playing, false);
  h.controller.dispose();
});

test('Back 15 near a passage start stays in the current passage when the previous one is not ready', async () => {
  const h = harness();
  const book = {id: 'b1', position: {generation_id: 'g', chapter_id: 'c1', source_id: 'p-s2', segment_id: 's2', offset: 1}};
  h.controller.update(book, {id: 'g', book_id: 'b1', status: 'paused', segments: [segment('s1', 'c1', {status: 'pending', audio_hash: null, duration: 0}), segment('s2')]}); await settle();
  h.controller.skip(-15); await settle();
  assert.equal(h.state().segment.id, 's2'); assert.equal(h.state().position, 0); assert.equal(h.state().error, '');
  h.controller.dispose();
});

test('Play during preload shows waiting immediately and media-key play continues to the next passage', async () => {
  let release;
  const h = harness(() => new Promise(resolve => { release = resolve; }));
  const book = {id: 'b1', position: null};
  h.controller.update(book, {id: 'g', book_id: 'b1', status: 'ready', segments: [segment('s1'), segment('s2')]});
  h.controller.toggle();
  assert.equal(h.state().waiting, true);
  release(new Blob(['ok'])); await settle();
  h.controller.pause(); await settle();
  await h.audio.play(); await settle();
  h.audio.ended = true; h.audio.paused = true; h.audio.dispatchEvent(new Event('ended')); await settle();
  assert.equal(h.state().segment.id, 's2');
  h.controller.dispose();
});

test('default and corrected generation names stay unique and localized', () => {
  assert.equal(nextGenerationName(['Озвучка 2'], 'Озвучка'), 'Озвучка 3');
  assert.equal(nextGenerationName(['Generation 1', 'Generation 2'], 'Generation'), 'Generation 3');
  assert.equal(nextGenerationName([], 'Generation'), 'Generation 1');
  assert.equal(revisionName('Озвучка 1', ['Озвучка 1'], 'ru'), 'Озвучка 1 · исправление');
  assert.equal(revisionName('Озвучка 1 · исправление', ['Озвучка 1', 'Озвучка 1 · исправление'], 'ru'), 'Озвучка 1 · исправление 2');
  assert.equal(revisionName('Main · revision 2', ['Main · revision', 'Main · revision 2'], 'en'), 'Main · revision 3');
  assert.ok(revisionName('x'.repeat(300), [], 'en').length <= 200);
});

test('backend messages, units, captions and block kinds are localized for Russian', () => {
  assert.equal(localMessage('ru', 'Selected folder does not contain a valid pack.json.'), 'В выбранной папке нет корректного pack.json.');
  assert.equal(localMessage('ru', 'Could not save playback position: Playback offset exceeds the saved segment.'), 'Не удалось сохранить место прослушивания: Позиция выходит за пределы сохранённого фрагмента.');
  assert.equal(localMessage('en', 'Bookmark note is too long.'), 'Bookmark note is too long.');
  assert.match(localMessage('ru', 'The saved audio could not be played. Press Play to retry.'), /«Слушать»/);
  assert.equal(operationMessage(new Error('Not a valid EPUB archive.'), 'ru'), 'Файл не является корректным архивом EPUB.');
  assert.equal(bytes(25.2 * 1024 ** 2, 'ru'), '25,2 МБ');
  assert.equal(bytes(25.2 * 1024 ** 2, 'en'), '25.2 MB');
  assert.equal(visualCaption('Codex illustration · Глава 1', 'ru'), 'Иллюстрация Codex · Глава 1');
  assert.equal(visualCaption('My caption', 'ru'), 'My caption');
  assert.equal(blockKindLabel('ru', 'figure'), 'Рисунок');
  assert.equal(speakable({kind: 'figure', text: ''}), false);
  assert.equal(speakable({kind: 'note', text: 'Сноска.'}), false);
  assert.equal(speakable({kind: 'paragraph', text: 'Текст.'}), true);
});

test('dialogs and reader actions follow the review fixes', () => {
  assert.match(app, /if\(value\)\{setNotice\(''\);setError\(''\);\}setModalState\(value\)/, 'Opening any dialog clears an earlier error');
  assert.match(app, /\{error&&!modal&&<div className="error-banner"/, 'The page banner does not duplicate a dialog error');
  assert.doesNotMatch(app, /setMode\('read'\)/, 'Show in text keeps the current view');
  assert.match(app, /const openGenerate=\([^)]*\)=>\{[^}]*setGenerationName\(''\)/, 'Every New generation entry point resets the name');
  assert.match(app, /else openGenerate\(readerIndex\.sources\.get\(id\)\?\.chapter\.id\|\|'','chapter',id\)/);
  assert.match(app, /generation\?\.id===g\.id\?setModal\(null\):/, 'Open on the selected generation only closes the dialog');
  assert.match(app, /spaceToggles\(player\.playing\|\|player\.waiting,/, 'Space cancels a pending Play');
  assert.match(app, /input:\{label:t\('generationName'\),value:g\.name,maxLength:200,required:true\}/);
  assert.match(app, /input:\{label:t\('bookmarkNote'\),value:mark\.note,maxLength:2000\}/);
  assert.match(app, /count\(language,book\.chapters\.length,'chapters'\)\.toLocaleUpperCase/);
  const reader = fs.readFileSync(path.resolve(__dirname, '../src/components/ReaderChapter.tsx'), 'utf8');
  assert.match(reader, /aria-label=\{named\(ru\?'Исправить отрывок':'Correct passage'\)\} disabled=\{!narrated\}/);
  const ask = fs.readFileSync(path.resolve(__dirname, '../src/components/AskDialog.tsx'), 'utf8');
  assert.match(ask, /type="submit" disabled=\{blank\}/);
  const dialog = fs.readFileSync(path.resolve(__dirname, '../src/components/ui/dialog.tsx'), 'utf8');
  assert.doesNotMatch(dialog, /Change the selected book settings/);
});

test('passage action buttons have distinct names even for repeated text and several figures', () => {
  const {sourceTree} = require('./layout_source.cjs');
  const {paragraphImages} = load('src/lib/readerIndexes.ts');
  const long = 'Harbor passage 1. The reader watches the quiet boats near the stone steps and the water.';
  const paragraph = (id, text) => ({id, kind: 'paragraph', text, html: '<p>' + text + '</p>', assets: []});
  const figure = id => ({id, kind: 'figure', text: '', html: '<figure></figure>', assets: []});
  const chapter = {id: 'c1', title: 'Chapter', blocks: [
    {id: 'h', kind: 'heading', text: 'Chapter', html: '<h1>Chapter</h1>', assets: []},
    paragraph('p1', long), paragraph('p2', long), paragraph('p3', 'Same.'), paragraph('p4', 'Same.'), figure('f1'), figure('f2')]};
  const names = language => {
    const {tree} = sourceTree('src/components/ReaderChapter.tsx', {}, {component: 'ReaderChapter', chapter, bookId: 'book', selected: '', images: paragraphImages([]), bookmarks: new Set(['p4']), language, onSelect: () => {}, onNavigate: () => {}, onAction: () => {}});
    const walk = node => Array.isArray(node) ? node.flatMap(walk) : !node || typeof node !== 'object' ? [] : [node, ...walk(node.props?.children)];
    return walk(tree).filter(node => node.tag === 'button' && node.props['aria-label']).map(node => node.props['aria-label']);
  };
  for (const language of ['en', 'ru']) {
    const list = names(language);
    assert.equal(list.length, chapter.blocks.length * 4);
    assert.equal(new Set(list).size, list.length, `${language}: every action button has a distinct accessible name`);
  }
  const english = names('en');
  const excerpt = long.slice(0, 59).trimEnd() + '…';
  assert.ok(english.includes('Listen to passage: passage 2, ' + excerpt), english.join(' | '));
  assert.ok(english.includes('Listen to passage: passage 3, ' + excerpt));
  assert.ok(english.includes('Bookmark passage: passage 4, Same.'));
  assert.ok(english.includes('Remove bookmark: passage 5, Same.'));
  assert.ok(english.includes('Correct passage: passage 6, Figure'));
  assert.ok(english.includes('Correct passage: passage 7, Figure'));
  const russian = names('ru');
  assert.ok(russian.includes('Иллюстрировать абзац: отрывок 6, Рисунок'));
  assert.ok(russian.includes('Иллюстрировать абзац: отрывок 7, Рисунок'));
});

test('library book covers are decorative so each card announces its title and author once', () => {
  assert.match(app, /<div className=\{cn\('book-cover','cover-'\+index%4\)\} aria-hidden="true">\{item\.cover\?<AssetImage kind="book" id=\{item\.cover\} book=\{item\.id\} alt="" /);
  const {sourceTree} = require('./layout_source.cjs');
  const {tree} = sourceTree('src/App.tsx', {states: {connected: true, book: null, library: [{id: 'b', title: 'Harbor', author: 'Fixture', chapter_count: 2, format: 'epub', cover: null, generations: []}], settings: {theme: 'light', language: 'en',online_recommendations:false,recommendation_genres:[]}, health: {ready: true, missing: []}}});
  const walk = node => Array.isArray(node) ? node.flatMap(walk) : !node || typeof node !== 'object' ? [] : [node, ...walk(node.props?.children)];
  const card = walk(tree).find(node => node.props?.className === 'book-card-main');
  const headings = walk(card).filter(node => node.tag === 'h3');
  const hiddenCover = walk(card).find(node => node.props?.['aria-hidden'] === 'true');
  assert.ok(hiddenCover, 'The cover is hidden from assistive technology');
  assert.equal(headings.filter(node => !walk(hiddenCover).includes(node)).length, 1, 'Only the metadata heading remains exposed');
});
