const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const {sourceTree} = require('./layout_source.cjs');

const root = path.resolve(__dirname, '..');
const cache = new Map();
function load(file) {
  const filename = path.resolve(root, file);
  if (cache.has(filename)) return cache.get(filename);
  const module = {exports: {}};
  cache.set(filename, module.exports);
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  const local = request => request.startsWith('.') ? load(path.relative(root, path.resolve(path.dirname(filename), request)) + '.ts') : require(request);
  new Function('require', 'module', 'exports', code)(local, module, module.exports);
  return module.exports;
}
const review = load('src/lib/castReview.ts');
const i18n = load('src/lib/i18n.ts');
const selection = load('src/lib/readerSelection.ts');
const appFile = path.join(root, 'src/App.tsx');
const app = ts.createSourceFile(appFile, fs.readFileSync(appFile, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function find(predicate) { let found; const visit = node => { if (found) return; if (predicate(node)) { found = node; return; } ts.forEachChild(node, visit); }; visit(app); assert.ok(found, 'App source node exists'); return found; }
function evaluate(node, environment) { return new Function(...Object.keys(environment), ts.transpileModule('return (' + node.getText(app) + ')', {compilerOptions: {target: ts.ScriptTarget.ES2022}}).outputText)(...Object.values(environment)); }
const variable = (name, environment) => evaluate(find(node => ts.isVariableDeclaration(node) && node.name.getText(app) === name).initializer, environment);
const effect = (fragment, environment) => evaluate(find(node => ts.isCallExpression(node) && node.expression.getText(app) === 'useEffect' && node.arguments[0]?.getText(app).includes(fragment)).arguments[0], environment);
const walk = node => Array.isArray(node) ? node.flatMap(walk) : !node || typeof node !== 'object' ? [] : [node, ...walk(node.props?.children)];
const text = node => Array.isArray(node) ? node.map(text).join('') : !node || typeof node === 'boolean' ? '' : typeof node === 'object' ? text(node.props?.children) : String(node);
const settle = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); };

const pythonNotices = Object.fromEntries([...fs.readFileSync(path.join(root, 'runtime/rimsky/casting.py'), 'utf8').match(/NOTICES = \{([\s\S]*?)\n\}/)[1].matchAll(/'(\w+)': '([^']+)'/g)].map(match => [match[1], match[2]]));
const notice = (code, count, params = {}) => ({code, count, params});

test('casting notices use the exact runtime English templates and have Russian renderings', () => {
  assert.ok(Object.keys(pythonNotices).length >= 14);
  const params = {names: ['Анна', 'Бо'], limit: 256, fields: ['gender', 'age_bands']};
  for (const [code, template] of Object.entries(pythonNotices)) {
    const expected = template.replace('{count}', '3').replace('{limit}', '256').replace('{fields}', 'gender or age bands').replace('{names}', 'Анна, Бо and 1 more');
    assert.equal(review.noticeText('en', notice(code, 3, params)), expected, code);
    const russian = review.noticeText('ru', notice(code, 3, params));
    assert.ok(russian && russian !== expected && /[а-я]/.test(russian), code);
  }
  assert.equal(review.noticeText('en', notice('delivery_not_supported', 7, {count: 7})), 'This model pack has no delivery control; 7 expressive passage(s) use neutral delivery.');
  assert.match(review.noticeText('ru', notice('unknown_gender_speakers', 3, {names: ['Анна', 'Бо']})), /Анна, Бо и ещё 1\.$/);
  assert.equal(review.noticeText('ru', notice('pack_missing_metadata', 2, {fields: ['gender', 'age_bands']})), 'Ни один голосовой профиль персонажей не указывает пол или возрастные группы; эти черты персонажей не учитываются при подборе голоса.');
  assert.equal(review.noticeText('en', notice('future_code', 1)), '');
});

test('old generations without coded notices still get translated warnings', () => {
  const warnings = ['2 local dialogue context(s) still share a character performance. Voice reuse elsewhere in the book is allowed.',
    '1 character(s) have no voice profile matching their gender; another profile is used.',
    'No character voice profile declares age bands; these character traits cannot guide casting.',
    '4 possible alias pair(s) were not compared because the 256-comparison limit was reached; they remain separate characters.',
    'Something the runtime said once.'];
  assert.deepEqual(review.legacyNotice(warnings[1]), notice('no_compatible_profile', 1));
  assert.deepEqual(review.legacyNotice(warnings[2]), notice('pack_missing_metadata', 1, {fields: ['age_bands']}));
  assert.deepEqual(review.legacyNotice(warnings[3]), notice('alias_skipped_limit', 4, {limit: 256}));
  const views = review.noticeViews('ru', {casting_warnings: warnings}, []);
  assert.equal(views.length, 5);
  assert.ok(views.slice(0, 4).every(view => /[а-я]/.test(view.text)));
  assert.equal(views[4].text, 'Something the runtime said once.');
  assert.deepEqual(review.noticeViews('en', {casting_warnings: warnings.slice(0, 1)}, []).map(view => view.text), warnings.slice(0, 1));
});

test('coded notices name current characters, list chapters and fall back for unknown codes', () => {
  const cast = [{id: 'a', name: 'Renamed Anna'}, {id: 'b', name: 'Bo'}];
  const views = review.noticeViews('en', {casting_notices: [notice('no_compatible_profile', 3, {names: ['Anna', 'Bo'], ids: ['a', 'b']}),
    notice('shared_performance_contexts', 2, {chapters: ['c1']}), notice('future_code', 2)]}, cast);
  assert.equal(views[0].names, 'Renamed Anna, Bo and 1 more');
  assert.deepEqual(views[1].chapters, ['c1']); assert.equal(views[1].names, '');
  assert.equal(views[2].text, 'future_code: 2');
  const aligned = review.noticeViews('en', {casting_notices: [notice('future_code', 2)], casting_warnings: ['A future English warning.']}, []);
  assert.equal(aligned[0].text, 'A future English warning.');
});

test('every exact runtime message added in this effort has a Russian translation and worker prefixes are removed', () => {
  const english = ['Text model context must be at least 8192 tokens.', 'Model pack files changed after verification. Select the pack again in Settings.',
    'Speaker analysis failed for this passage; the narrator voice is used.', 'A passage could not be synthesized after a retry. Open the marked passage to correct its spoken text, then resume.',
    'The selected device is not available for speech synthesis. Choose another device in Settings and resume.', 'Word alignment stopped because the local aligner exited; passage subtitles are kept.',
    'Speech duration looks unusual for this passage. Use New take if it sounds wrong.', 'Export cancelled. No incomplete output was published.',
    'Secure connection to the image service failed (certificate check). No automatic paid retry was made.', 'This paragraph exceeds the 16,000-character illustration limit and was skipped. No text was truncated.',
    'The bundled inference runtime is missing. Reinstall the application.', 'Voice families must be stable, whitespace-free identifiers.', 'A voice pack needs at least one character performance.',
    'Expected nonempty text of at most 64 characters.', 'Local runtime is not running', 'The book text encoding could not be read.', 'Some vector drawings were too large and were omitted.',
    'Speech output is empty, invalid, silent, or too long.', 'This passage is not ready. Review the cast and start narration to continue.'];
  for (const message of english) {
    assert.equal(i18n.localMessage('en', message), message);
    assert.notEqual(i18n.localMessage('ru', message), message, message);
  }
  assert.equal(i18n.localMessage('ru', 'Expected nonempty text of at most 64 characters.'), 'Ожидается непустой текст длиной не более 64 символов.');
  assert.equal(i18n.localMessage('en', 'AppError: Speech output is empty, invalid, silent, or too long.'), 'Speech output is empty, invalid, silent, or too long.');
  assert.equal(i18n.localMessage('ru', 'AppError: Speech output is empty, invalid, silent, or too long.'), i18n.localMessage('ru', 'Speech output is empty, invalid, silent, or too long.'));
  assert.equal(i18n.localMessage('ru', 'Unresolved speaker; narrator voice is used. Word alignment is unavailable; passage subtitles are retained.'),
    'Говорящий не определён; используется голос рассказчика. Выравнивание по словам недоступно; субтитры по отрывкам сохранены.');
  assert.equal(i18n.localMessage('ru', 'Unresolved speaker; narrator voice is used. Unknown tail.'), 'Говорящий не определён; используется голос рассказчика. Unknown tail.');
  assert.equal(i18n.localMessage('ru', 'Preparation could not start: Local runtime is not running'), 'Не удалось начать подготовку: Локальная среда не запущена');
  assert.equal(i18n.statusLabel('en', 'review'), 'Cast review'); assert.equal(i18n.statusLabel('ru', 'review'), 'Проверка ролей');
  const long = Object.assign(new Error('The paragraph “Long opening…” in “Chapter 2” exceeds the 16,000-character illustration limit. No text was truncated.'),
    {code: 'illustration_paragraph_too_long', details: {source_id: 'p', chapter_id: 'c', chapter: 'Глава 2', opening: 'Длинное начало', characters: 17000, limit: 16000}});
  assert.equal(i18n.operationMessage(long, 'en'), long.message);
  assert.equal(i18n.operationMessage(long, 'ru'), 'Абзац «Длинное начало…» в главе «Глава 2» превышает лимит иллюстраций в 16 000 символов. Текст не сокращался.');
});

test('joined passage warnings translate part by part, aligner validation texts included, and unknown parts stay raw', () => {
  const integrity = fs.readFileSync(path.join(root, 'runtime/rimsky/integrity.py'), 'utf8');
  const aligner = ['Alignment did not return a complete cue sequence.', 'Invalid alignment cue.', 'Alignment timestamps or text are invalid.', 'Alignment cue starts outside the audio.',
    'Alignment text does not cover the spoken passage; using passage subtitles.'];
  for (const message of aligner) {
    assert.ok(integrity.includes(`AppError('${message}')`), `integrity.py raises exactly: ${message}`);
    assert.equal(i18n.localMessage('en', message), message);
    assert.match(i18n.localMessage('ru', message), /^[^a-z]*[а-яё][^a-z]*$/i, message);
  }
  const ru = message => i18n.localMessage('ru', message);
  const unresolved = 'Говорящий не определён; используется голос рассказчика.', retained = 'Субтитры по отрывкам сохранены.';
  assert.equal(ru('Unresolved speaker; narrator voice is used. Alignment text does not cover the spoken passage; using passage subtitles. Passage subtitles are retained.'),
    `${unresolved} Текст выравнивания по словам не совпадает с произносимым текстом отрывка; используются субтитры по отрывкам. ${retained}`);
  assert.equal(ru('Speech duration looks unusual for this passage. Use New take if it sounds wrong. ValueError: Alignment model changed during a pinned generation. Passage subtitles are retained.'),
    `Длительность речи в этом отрывке выглядит необычно. Если звучит неправильно, используйте «Новый дубль». ValueError: Alignment model changed during a pinned generation. ${retained}`);
  assert.equal(ru('Unresolved speaker; narrator voice is used. RuntimeError: CUDA error: device-side assert triggered Passage subtitles are retained.'),
    `${unresolved} RuntimeError: CUDA error: device-side assert triggered ${retained}`);
  assert.equal(ru('Unresolved speaker; narrator voice is used. Local inference exceeded its 900-second operation limit. Passage subtitles are retained.'),
    `${unresolved} Локальный процесс модели превысил лимит времени операции (900 с). ${retained}`);
  assert.equal(ru('AppError: Alignment cue starts outside the audio. Passage subtitles are retained.'), `Субтитр выравнивания по словам начинается за пределами аудио. ${retained}`);
  assert.equal(ru('Local runtime is not running on this port.'), 'Local runtime is not running on this port.', 'a key without closing punctuation only matches a whole part');
  assert.equal(ru('Something unknown happened. Another unknown sentence.'), 'Something unknown happened. Another unknown sentence.');
  assert.equal(i18n.localMessage('en', 'Unresolved speaker; narrator voice is used. Alignment cue starts outside the audio. Passage subtitles are retained.'),
    'Unresolved speaker; narrator voice is used. Alignment cue starts outside the audio. Passage subtitles are retained.');
});

test('cast helpers order speakers, filter voices by role and disambiguate equal names with aliases', () => {
  const cast = [{id: 'narrator', name: 'Narrator', aliases: [], voice_id: 'n'}, {id: 'x', name: 'Stranger', aliases: ['Kirill', 'stranger']}, {id: 'y', name: 'Stranger', aliases: []},
    {id: 'z', name: 'Stranger', aliases: []}, {id: 'r', name: 'Robot', aliases: [], kind: 'mechanical'}];
  const labels = review.speakerLabels('en', cast);
  assert.deepEqual([...labels.values()], ['Narrator', 'Stranger (Kirill)', 'Stranger', 'Stranger · 2', 'Robot']);
  assert.deepEqual(review.aliasesOf(cast[1]), ['Kirill']);
  const shares = review.speechShares({analysis: {casting_inputs: [{speaker: 'narrator', characters: 50}, {speaker: 'y', characters: 30}, {speaker: 'x', characters: 20}]}, segments: []});
  assert.equal(shares.get('y'), .3);
  const groups = review.castGroups(cast, shares);
  assert.equal(groups.narrator.id, 'narrator'); assert.deepEqual(groups.speaking.map(item => item.id), ['y', 'x']); assert.deepEqual(groups.silent.map(item => item.id), ['z', 'r']);
  const fromSegments = review.speechShares({analysis: {}, segments: [{speaker: 'x', text: 'abcd'}, {speaker: 'narrator', text: 'abcd'}]});
  assert.equal(fromSegments.get('x'), .5);
  const voices = [{id: 'n', role: 'narrator'}, {id: 'nv', role: 'narrator_variant'}, {id: 'a', role: 'character'}, {id: 'b', role: ''}, {id: 'm', role: 'mechanical'}];
  assert.deepEqual(review.voiceChoices({kind: 'person'}, voices).map(voice => voice.id), ['a', 'b']);
  assert.deepEqual(review.voiceChoices({kind: 'mechanical'}, voices).map(voice => voice.id), ['m']);
  assert.deepEqual(review.voiceChoices({kind: 'mechanical'}, voices.filter(voice => voice.role !== 'mechanical')).map(voice => voice.id), ['a', 'b']);
  assert.deepEqual(review.voiceChoices({kind: 'person'}, [...voices, {id:'review',role:'character',castable:false}]).map(voice => voice.id), ['a', 'b']);
  assert.deepEqual(review.narratorChoices(voices).map(voice => voice.id), ['n', 'nv']);
  assert.equal(review.narratorVoice({analysis: {narrator_voices: {c2: 'nv'}}, cast}, 'c2'), 'nv');
  assert.equal(review.narratorVoice({analysis: {narrator_voices: {'': 'nv'}}, cast}, 'c1'), 'nv');
  assert.equal(review.narratorVoice({analysis: {}, cast}, 'c1'), 'n');
  assert.equal(review.characterTraits('en', {kind: 'mechanical', gender: {value: 'female'}, age_band: {value: 'adult', conflict: true}}), 'Mechanical · female · disputed');
  const kinds = [...fs.readFileSync(path.join(root, 'runtime/rimsky/casting.py'), 'utf8').match(/^KINDS = \(([^)]*)\)/m)[1].matchAll(/'(\w+)'/g)].map(match => match[1]);
  assert.deepEqual(review.kindChoices('en'), [{value: 'person', label: 'Person'}, {value: 'mechanical', label: 'Mechanical'}, {value: 'collective', label: 'Group'}]);
  assert.deepEqual(review.kindChoices('en').map(choice => choice.value), kinds);
  assert.deepEqual(review.kindChoices('ru').map(choice => choice.label), ['Человек', 'Механический', 'Группа']);
});

test('flagged passages, render estimate and preparation stages are derived without inventing progress', () => {
  const known = new Set(['p1', 'p2', 'p-3', 'p9']);
  const degradedText = 'Speaker analysis failed for this passage; the narrator voice is used.';
  const generation = {analysis: {degraded: [{source: 'p1-0-40', reason: 'invalid'}, {source: 'p-3-0-420', reason: 'first half'}, {source: 'p-3-420-700', reason: ''}, {source: 'p9-0-30', reason: 'unrendered chapter'}, {source: 'p2-0-6', reason: 'corrected'}]},
    segments: [{id: 'p1-0-40', source_id: 'p1', text: 'Degraded passage.', speaker: 'unknown', warning: degradedText, status: 'pending', spoken: 'abc'},
      {id: 'p2-0-6', source_id: 'p2', text: 'Fixed.', speaker: 'anna', warning: '', status: 'ready', spoken: 'abcdef'}, {id: 'p2-6-8', source_id: 'p2', text: 'xy', speaker: 'unknown', warning: '', status: 'pending', spoken: 'xy'},
      {id: 'p-3-0-420', source_id: 'p-3', text: 'First half.', speaker: 'unknown', warning: '', status: 'pending', spoken: ''}, {id: 'p-3-420-700', source_id: 'p-3', text: 'Second half.', speaker: 'unknown', warning: degradedText, status: 'pending', spoken: ''}]};
  const flagged = review.flaggedPassages(generation, id => known.has(id));
  assert.deepEqual(flagged, {degraded: [{id: 'p1-0-40', source: 'p1', text: 'Degraded passage.', reasons: ['invalid']}, {id: 'p-3-0-420', source: 'p-3', text: 'First half.', reasons: ['first half']},
    {id: 'p-3-420-700', source: 'p-3', text: 'Second half.', reasons: []}], unresolved: ['p2']}, 'chapters that are not rendered and corrected speakers are not listed');
  const sources = new Set(generation.analysis.degraded.map(item => item.source));
  assert.equal(flagged.degraded.length, generation.segments.filter(segment => sources.has(segment.id) && segment.speaker === 'unknown').length, 'the list counts what the degraded_passages notice counts');
  assert.deepEqual(review.renderEstimate(generation.segments), {passages: 4, characters: 5});
  assert.deepEqual(review.renderEstimate(generation.segments, ['p1-0-40']), {passages: 3, characters: 2}, 'held passages are not promised as synthesis work');
  const status = (state, progress) => review.preparationStatus('en', {status: state, analysis: progress ? {progress} : {}}, 3, 40);
  assert.equal(status('analyzing'), 'Reading characters');
  assert.equal(status('analyzing', {stage: 'analysis', done: 12, total: 112}), 'Reading characters · 12/112');
  assert.equal(status('casting', {stage: 'reconcile', done: 30, total: 256}), 'Matching character names · 30/256');
  assert.equal(status('casting', {stage: 'casting', done: 0, total: 1}), 'Assigning voices');
  assert.equal(status('rendering', {stage: 'casting', done: 1, total: 1}), 'Generating audio · 3/40');
  const startingNarration = {status: 'queued', analysis: {complete: true, approved: true, progress: {stage: 'casting', done: 1, total: 1}}, segments: [{status: 'ready'}, {status: 'pending'}, {status: 'pending'}, {status: 'pending'}]};
  assert.equal(review.stageProgress(startingNarration), null);
  assert.equal(review.preparationStatus('en', startingNarration, 1, 4), 'Queued · 1/4', 'a queued generation with an assigned cast is not shown as casting');
  assert.equal(review.preparationStatus('ru', startingNarration, 1, 4), 'В очереди · 1/4');
  assert.equal(review.preparationPercent(startingNarration), 25);
  assert.equal(status('queued'), 'Queued');
  assert.equal(status('queued', {stage: 'analysis', done: 5, total: 9}), 'Reading characters · 5/9', 'a queued resume of unfinished analysis keeps its stage');
  assert.equal(status('review'), 'Voices assigned. Review the cast, then start narration.');
  assert.equal(status('ready'), '3/40 passages saved');
  assert.equal(review.preparationStatus('ru', {status: 'analyzing', analysis: {progress: {stage: 'analysis', done: 1200, total: 2000}}}, 0, 0), 'Анализ персонажей · 1 200/2 000');
  assert.equal(review.preparationPercent({status: 'analyzing', analysis: {progress: {stage: 'analysis', done: 25, total: 100}}, segments: []}), 25);
  assert.equal(review.preparationPercent({status: 'rendering', analysis: {}, segments: [{status: 'ready'}, {status: 'pending'}]}), 50);
  assert.equal(review.preparationPercent({status: 'rendering', analysis: {}, segments: [{status: 'ready'}, {status: 'ready'}], preparation_progress: {ready: 1,total: 2,held: 0}}), 50);
});

test('corrections may request a new take of an unchanged passage', () => {
  const original = {id: 's', spoken: 'Text', speaker: 'a', delivery: 'neutral'};
  assert.equal(selection.correctionRequest(original, {...original}, false), null);
  assert.deepEqual(selection.correctionRequest(original, {...original}, true), {id: 's', spoken: 'Text', speaker: 'a', delivery: 'neutral', retake: true});
  assert.deepEqual(selection.correctionRequest(original, {...original, spoken: ' Other '}, false), {id: 's', spoken: 'Other', speaker: 'a', delivery: 'neutral'});
  assert.equal(selection.correctionRequest(original, {...original, spoken: '  '}, true), null);
  assert.equal(selection.correctionRequest(undefined, original, true), null);
});

test('generation polling replaces polled analysis keys but keeps full-payload details such as conflicts', () => {
  const current = {id: 'g', book_id: 'b', revision: 1, segments: [], analysis: {complete: true, casting_conflicts: [{source_ids: ['p']}], casting_inputs: [{speaker: 'a', characters: 3}], progress: {stage: 'casting', done: 1, total: 1}}};
  const next = {id: 'g', book_id: 'b', revision: 2, segments: [], analysis: {complete: true, casting_notices: []}};
  for (const full of [true, false]) {
    const merged = selection.acceptGenerationPoll(current, next, full, []);
    assert.deepEqual(merged.analysis, {casting_conflicts: [{source_ids: ['p']}], casting_inputs: [{speaker: 'a', characters: 3}], complete: true, casting_notices: []});
  }
});

test('books dropped during a long operation are queued and imported once it finishes', () => {
  const queue = {current: []}, counts = [];
  const importFiles = variable('importFiles', {runningAction: {current: true}, importQueue: queue, setQueued: value => counts.push(value), run: () => { throw new Error('No second action'); }});
  importFiles([{name: 'a.epub'}]); importFiles([{name: 'b.fb2'}]);
  assert.deepEqual(queue.current.map(file => file.name), ['a.epub', 'b.fb2']); assert.deepEqual(counts, [1, 2]);
  const processed = [];
  effect('importQueue.current.splice(0)', {busy: 'Exporting saved audio', importQueue: queue, setQueued: value => counts.push(value), importRef: {current: files => processed.push(files)}})();
  assert.deepEqual(processed, []);
  effect('importQueue.current.splice(0)', {busy: '', importQueue: queue, setQueued: value => counts.push(value), importRef: {current: files => processed.push(files.map(file => file.name))}})();
  assert.deepEqual(processed, [['a.epub', 'b.fb2']]); assert.deepEqual(queue.current, []); assert.equal(counts.at(-1), 0);
});

test('a cancelled export is a neutral notice and cast actions refresh the full generation', async () => {
  const notices = [], saves = [], exporting = [];
  const cancelled = Object.assign(new Error('Export cancelled. No incomplete output was published.'), {code: 'export_cancelled'});
  const exportAudio = variable('exportAudio', {run: (_label, task) => task(), generation: {id: 'g'}, book: {id: 'b', title: 'Book'}, selectionEpoch: {current: 1}, t: key => key, setExporting: value => exporting.push(value),
    api: async () => { throw cancelled; }, saveExport: async () => { saves.push('save'); return true; }, setNotice: value => notices.push(value), localMessage: i18n.localMessage, language: 'ru'});
  await exportAudio('m4b', ['c1']);
  assert.deepEqual(notices, ['Экспорт отменён. Незавершённый файл не сохранён.']); assert.deepEqual(saves, []); assert.deepEqual(exporting, [true, false]);
  const failing = variable('exportAudio', {run: (_label, task) => task(), generation: {id: 'g'}, book: {id: 'b', title: 'Book'}, selectionEpoch: {current: 1}, t: key => key, setExporting: value => exporting.push(value),
    api: async () => { throw new Error('FFmpeg is missing from the local runtime.'); }, saveExport: async () => true, setNotice: value => notices.push(value), localMessage: i18n.localMessage, language: 'en'});
  await assert.rejects(failing('m4b', ['c1']), /FFmpeg/);
  assert.deepEqual(exporting, [true, false, true, false]);
  const order = [];
  const succeeding = variable('exportAudio', {run: (_label, task) => task(), generation: {id: 'g'}, book: {id: 'b', title: 'Book'}, selectionEpoch: {current: 1}, t: key => key, setExporting: value => order.push(value),
    api: async op => { order.push(op); return {id: op}; }, saveExport: async () => { order.push('save'); return true; }, setNotice: () => {}, localMessage: i18n.localMessage, language: 'en',
    setGeneration: () => {}, acceptGenerationRefresh: (_current, next) => next});
  await succeeding('m4b', ['c1']);
  assert.deepEqual(order, [true, 'export', false, 'save', 'generation'], 'Cancel export is offered only while the runtime export runs');
  const calls = [], closed = [];
  const castChange = variable('castChange', {run: (_label, task) => task(), generation: {id: 'g'}, modalRevision: {current: 4},
    api: async (op, data) => { calls.push([op, data]); return {requested: true}; }, refreshGeneration: async id => { calls.push(['refresh', id]); }, closeModal: revision => closed.push(revision)});
  await castChange('Updating cast', 'cast_voice', {character_id: 'a', voice_id: 'v2'});
  await castChange('Starting narration', 'start_narration', {}, true);
  await castChange('Updating cast', 'cast_kind', {character_id: 'bot', kind: 'person'});
  assert.deepEqual(calls, [['cast_voice', {id: 'g', character_id: 'a', voice_id: 'v2'}], ['refresh', 'g'], ['start_narration', {id: 'g'}], ['refresh', 'g'],
    ['cast_kind', {id: 'g', character_id: 'bot', kind: 'person'}], ['refresh', 'g']]);
  assert.deepEqual(closed, [4]);
  const kinds = [];
  const onKind = evaluate(find(node => ts.isJsxAttribute(node) && node.name.getText(app) === 'onKind').initializer.expression, {castChange: (...args) => kinds.push(args), t: key => key});
  onKind({id: 'bot', name: 'Unit 7'}, 'person');
  assert.deepEqual(kinds, [['updatingCast', 'cast_kind', {character_id: 'bot', kind: 'person'}]]);
});

test('a character preview neither moves the reader nor stores a reading position', () => {
  const moves = [];
  const segment = {id: 's', source_id: 'p', chapter_id: 'c', start: 0, end: 5, duration: 2};
  const environment = previewing => ({player: {segment, previewing, position: 1}, settings: {follow: true}, browsing: false, manualReading: {current: false}, generation: {book_id: 'b'}, book: {id: 'b'},
    restoringPlayback: {current: ''}, readerIndex: {sources: new Map([['p', {block: {text: '1234567890'}}]])},
    segments: {byId: new Map([['s', segment]])}, mode: 'read', setChapterId: value => moves.push(value), setNavigation: value => moves.push(value), storeReading: (_book, value) => moves.push(value)});
  effect('setChapterId(player.segment.chapter_id)', environment(true))();
  assert.deepEqual(moves, []);
  effect('setChapterId(player.segment.chapter_id)', environment(false))();
  assert.equal(moves.length, 3);
});

const chapter = {id: 'c1', title: 'Harbor', blocks: [{id: 'p1', kind: 'paragraph', text: 'First passage text.', html: '<p>First passage text.</p>', assets: []}, {id: 'p2', kind: 'paragraph', text: 'Second passage.', html: '<p>Second passage.</p>', assets: []}]};
const chapterTwo = {id: 'c2', title: 'Pier', blocks: [{id: 'p3', kind: 'paragraph', text: 'Third.', html: '<p>Third.</p>', assets: []}]};
const passages = new Map([...chapter.blocks.map(block => [block.id, {chapter, block}]), ...chapterTwo.blocks.map(block => [block.id, {chapter: chapterTwo, block}])]);
const seg = (id, source, speaker, overrides = {}) => ({id, ordinal: 1, chapter_id: source === 'p3' ? 'c2' : 'c1', source_id: source, start: 0, end: 5, text: 'Hello', spoken: 'Hello', speaker, delivery: 'neutral', status: 'pending', audio_hash: null, duration: 0, cues: [], warning: '', ...overrides});
const reviewGeneration = {id: 'g', book_id: 'b', name: 'Narration', status: 'review', error: '', created: '', updated: '', parent_id: null, revision: 3, exports: [],
  config: {chapters: ['c1', 'c2'], pack_name: 'Pack', pack_fingerprint: 'f', align_words: false},
  cast: [{id: 'narrator', name: 'Narrator', aliases: [], description: '', evidence: [], voice_id: 'n', voice_name: 'Narrator voice', family: 'n'},
    {id: 'anna', name: 'Stranger', aliases: ['Anna', 'Anya'], description: '', evidence: [], voice_id: 'a', voice_name: 'Aiden', group: 'masculine', family: 'warm', kind: 'person', voice_compatible: false, voice_reused: true, gender: {value: 'female'}},
    {id: 'bot', name: 'Unit 7', aliases: [], description: '', evidence: [], voice_id: 'a', voice_name: 'Aiden', kind: 'mechanical'},
    {id: 'ghost', name: 'Ghost', aliases: [], description: '', evidence: [], voice_id: 'b', voice_name: 'Serena'}],
  analysis: {complete: true, approved: false, casting_notices: [notice('no_compatible_profile', 1, {names: ['Stranger'], ids: ['anna']}), notice('shared_performance_contexts', 1, {chapters: ['c1']})],
    casting_conflicts: [{chapter_id: 'c1', source_ids: ['p2'], speakers: ['anna', 'bot'], shared_speakers: [['anna', 'bot']]}],
    degraded: [{source: 'p3-0-6', reason: 'invalid'}], narrator_voices: {c2: 'nv'}},
  segments: [seg('s1', 'p1', 'narrator'), seg('s2', 'p2', 'anna'), seg('s3', 'p2', 'bot'), seg('p3-0-6', 'p3', 'unknown', {text: 'Third.', end: 6, warning: 'Speaker analysis failed for this passage; the narrator voice is used.'}), seg('s5', 'p1', 'unknown')]};
const packVoices = [{id: 'n', name: 'Narrator voice', role: 'narrator', family: 'n', group: 'narrator', gender: '', age_bands: []}, {id: 'nv', name: 'Night reader', role: 'narrator_variant', family: 'n', group: '', gender: '', age_bands: []},
  {id: 'a', name: 'Aiden', role: 'character', family: 'warm', group: 'masculine', gender: 'male', age_bands: []}, {id: 'b', name: 'Serena', role: 'character', family: 'b', group: 'feminine', gender: 'female', age_bands: []},
  {id: 'm', name: 'Servo', role: 'mechanical', family: 'm', group: 'mechanical', gender: '', age_bands: []}];
function castPanel(generation = reviewGeneration, states = {voices: packVoices}, props = {}) {
  return walk(sourceTree('src/components/CastPanel.tsx', {states}, {component: 'CastPanel', generation, chapters: [chapter, chapterTwo], passages, language: 'en', busy: false, blocked: '', previewing: '',
    onPreview: () => {}, onNavigate: () => {}, onRename: () => {}, onMerge: () => {}, onVoice: () => {}, onKind: () => {}, onNarrator: () => {}, onStart: () => {}, ...props}).tree);
}

test('the cast review panel lists speakers first with aliases, traits, voice family and warning-styled notices', () => {
  const nodes = castPanel();
  const all = text(nodes);
  assert.ok(nodes.some(node => node.tag === 'button' && text(node) === 'Start narration' && node.props.disabled === false));
  assert.match(all, /5 passages · 25 characters will be synthesized\./);
  const rows = nodes.filter(node => String(node.props?.className).includes('character-row'));
  assert.deepEqual(rows.map(row => text(walk(row).find(node => node.tag === 'strong'))), ['Narrator', 'Stranger', 'Unit 7', 'Ghost']);
  assert.match(text(rows[1]), /Also: Anna, Anya/); assert.match(text(rows[1]), /Person · female · 19% of text/);
  assert.match(text(rows[1]), /Voice: Aiden · masculine · family warm/);
  assert.match(text(rows[1]), /Voice profile does not match the character traits/); assert.match(text(rows[1]), /Voice is also used by another character/);
  const notices = nodes.filter(node => String(node.props?.className).includes('casting-notice') && !String(node.props?.className).includes('casting-notices'));
  assert.equal(notices.length, 2);
  assert.match(text(notices[0]), /1 character\(s\) have no compatible voice profile; another profile is used\.Stranger/);
  assert.match(text(notices[1]), /Stranger \(Anna, Anya\) = Unit 7/);
  assert.equal(nodes.filter(node => String(node.props?.className).includes('validation-error')).length, 0, 'notices are warnings, not errors');
  assert.ok(walk(nodes.find(node => node.tag === 'details' && /Characters without speech: 1/.test(text(node)))).some(node => /Ghost/.test(text(node))));
});

test('the narrator sharing notice lists its chapters by title like shared contexts without a saved conflict list', () => {
  const casting_notices = [notice('narrator_performance_shared', 3, {names: ['Anna', 'Bot'], ids: ['anna', 'bot'], chapters: ['c2', 'c9']}), notice('shared_performance_contexts', 1, {chapters: ['c1']})];
  const expected = {en: ['3 character(s) share the narrator performance in chapters where they speak.', 'Stranger, Unit 7 and 1 more'],
    ru: ['Персонажей, звучащих исполнением рассказчика в главах, где они говорят: 3.', 'Stranger, Unit 7 и ещё 1']};
  const boxes = (analysis, language = 'en') => castPanel({...reviewGeneration, analysis: {...reviewGeneration.analysis, casting_notices, ...analysis}}, {voices: packVoices}, {language})
    .filter(node => node.props?.className === 'casting-notice').map(walk);
  for (const language of ['en', 'ru']) {
    const [narration, contexts] = boxes({casting_conflicts: []}, language);
    assert.deepEqual([text(narration.find(node => node.tag === 'p')), ...narration.filter(node => node.tag === 'small').map(text)], [...expected[language], 'Pier, c9']);
    assert.deepEqual(contexts.filter(node => node.tag === 'small').map(text), ['Harbor']);
  }
  const [narration, contexts] = boxes({});
  assert.deepEqual(narration.filter(node => node.tag === 'small').map(text), [expected.en[1], 'Pier, c9']);
  assert.deepEqual(contexts.filter(node => node.tag === 'button').map(text), ['Stranger (Anna, Anya) = Unit 7']);
  assert.ok(!contexts.some(node => node.tag === 'small' && text(node) === 'Harbor'), 'a saved conflict list replaces the chapter line');
});

test('review voice choices follow role and kind, merges exclude the character and narration can vary per chapter', () => {
  const nodes = castPanel();
  const select = label => nodes.find(node => node.tag === 'select' && node.props['aria-label'] === label);
  const values = node => walk(node).filter(child => child.tag === 'option').map(child => child.props.value);
  assert.deepEqual(values(select('Voice for Stranger')), ['a', 'b']);
  assert.deepEqual(values(select('Voice for Unit 7')), ['a', 'm'], 'a mechanical speaker gets mechanical voices; its current voice stays visible');
  assert.deepEqual(values(select('Merge Stranger')), ['', 'bot', 'ghost']);
  assert.equal(select('Narrator: Pier').props.value, 'nv'); assert.equal(select('Narrator: Harbor').props.value, 'n');
  assert.deepEqual(values(select('Narrator: Harbor')), ['n', 'nv']);
  const flagged = nodes.filter(node => node.tag === 'details' && /Passages (without speaker analysis|with an unresolved speaker)/.test(text(walk(node).find(child => child.tag === 'summary'))));
  assert.match(text(flagged[0]), /Passages without speaker analysis: 1.*Third\. · Pier/);
  assert.match(text(flagged[1]), /Passages with an unresolved speaker: 1.*First passage text\./);
  const navigated = [];
  const buttons = castPanel(reviewGeneration, {voices: packVoices}, {onNavigate: id => navigated.push(id)}).filter(node => node.tag === 'button' && node.props.type === 'button');
  buttons.forEach(button => button.props.onClick());
  assert.deepEqual(navigated, ['p2', 'p3', 'p1']);
  const ready = castPanel({...reviewGeneration, status: 'ready'});
  assert.equal(ready.filter(node => node.tag === 'select').length, 0); assert.ok(!ready.some(node => node.tag === 'button' && text(node) === 'Start narration'));
  const blocked = castPanel(reviewGeneration, {voices: packVoices}, {blocked: 'Audio for “Other” is being prepared.'});
  assert.equal(blocked.find(node => node.tag === 'button' && text(node) === 'Start narration').props.disabled, true);
});

test('review offers a kind control next to each speaker voice and reports the chosen kind', () => {
  const changes = [];
  const nodes = castPanel(reviewGeneration, {voices: packVoices}, {onKind: (character, kind) => changes.push([character.id, kind])});
  const select = (tree, label) => tree.find(node => node.tag === 'select' && node.props['aria-label'] === label);
  const options = node => walk(node).filter(child => child.tag === 'option');
  assert.deepEqual(options(select(nodes, 'Kind for Stranger')).map(option => [option.props.value, text(option)]), [['person', 'Person'], ['mechanical', 'Mechanical'], ['collective', 'Group']]);
  assert.equal(select(nodes, 'Kind for Stranger').props.value, 'person'); assert.equal(select(nodes, 'Kind for Unit 7').props.value, 'mechanical');
  assert.equal(select(nodes, 'Kind for Ghost'), undefined, 'a character without speech has neither a voice nor a kind control');
  const actions = nodes.find(node => node.props?.className === 'character-actions');
  assert.deepEqual(actions.props.children.map(child => child.props['aria-label']), ['Kind for Stranger', 'Voice for Stranger', 'Merge Stranger', undefined]);
  select(nodes, 'Kind for Unit 7').props.onChange({target: {value: 'person'}});
  assert.deepEqual(changes, [['bot', 'person']]);
  assert.equal(select(castPanel(reviewGeneration, {voices: packVoices}, {busy: true}), 'Kind for Stranger').props.disabled, true);
  assert.equal(select(nodes, 'Kind for Stranger').props.disabled, false);
  assert.deepEqual(options(select(castPanel(reviewGeneration, {voices: packVoices}, {language: 'ru'}), 'Тип персонажа: Stranger')).map(text), ['Человек', 'Механический', 'Группа']);
  const legacy = castPanel({...reviewGeneration, cast: reviewGeneration.cast.map(character => character.id === 'anna' ? {...character, kind: undefined} : character)});
  assert.equal(select(legacy, 'Kind for Stranger').props.value, 'person', 'a missing kind reads as person, as in the runtime');
  assert.equal(castPanel({...reviewGeneration, status: 'ready'}).filter(node => node.tag === 'select').length, 0);
});

test('the player keeps preparation text visible and shows passage warnings separately', () => {
  const book = {id: 'b', title: 'Book', cover: null, chapters: [chapter, chapterTwo]};
  const generation = {...reviewGeneration, status: 'rendering', segments: [seg('s1', 'p1', 'unknown', {status: 'ready', audio_hash: 'h', duration: 2, warning: 'Unresolved speaker; narrator voice is used.'})]};
  const render = props => walk(sourceTree('src/components/PlayerBar.tsx', {}, {component: 'PlayerBar', book, generation, settings: {language: 'en', playback_rate: 1},
    player: {segment: generation.segments[0], position: 0, playing: false, waiting: false, error: '', saveError: '', previewing: false, sleepAt: 0},
    status: 'Generating audio · 1/1', statusAction: null, onToggle() {}, onSkip() {}, onSelect() {}, onSettings() {}, onHistory() {}, onUpdate() {}, ...props}).tree);
  const nodes = render({warning: 'Unresolved speaker; narrator voice is used.'});
  assert.equal(text(nodes.find(node => String(node.props?.className).includes('dock-status'))), 'Generating audio · 1/1');
  const note = nodes.find(node => String(node.props?.className).includes('dock-note'));
  assert.match(note.props.className, /dock-warning/); assert.equal(text(note), 'Unresolved speaker; narrator voice is used.');
  assert.equal(nodes.some(node => node.props?.className === 'dock-captions'), false);
  assert.match(render({error: 'Preparation failed.'}).find(node => String(node.props?.className).includes('dock-note')).props.className, /dock-error/);
});

function appTree(states, player = {}) {
  const book = {id: 'b', title: 'Book', author: 'Author', chapters: [chapter, chapterTwo], bookmarks: [], generations: [], visuals: [], warnings: []};
  return walk(sourceTree('src/App.tsx', {runtimeLost: false, player: {playing: false, waiting: false, position: 0, error: '', saveError: '', previewing: false, segment: null, ...player},
    states: {connected: true, book, chapterId: 'c1', health: {ready: true, active: null, missing: [], word_alignment: true, pack_error: '', storage_bytes: 0},
      settings: {theme: 'light', language: 'en', font_size: 20, line_height: 1.9, playback_rate: 1, volume: 1, follow: true, align_words: false}, ...states}}).tree);
}

test('the review status replaces Resume with a cast review action, and failed passages are marked with a way to reach them', () => {
  const review = appTree({generation: reviewGeneration});
  assert.ok(review.some(node => node.tag === 'button' && text(node) === 'Review cast'));
  assert.ok(!review.some(node => node.tag === 'button' && text(node) === 'Resume preparation'));
  assert.ok(review.some(node => text(node) === 'Voices assigned. Review the cast, then start narration.'));
  const failed = {...reviewGeneration, status: 'failed', error: 'A passage could not be synthesized after a retry. Open the marked passage to correct its spoken text, then resume.',
    segments: [seg('s1', 'p1', 'narrator', {status: 'ready', audio_hash: 'h', duration: 2}), seg('s2', 'p2', 'anna', {status: 'failed', warning: 'Speech output is empty, invalid, silent, or too long.'})]};
  const nodes = appTree({generation: failed});
  assert.ok(nodes.some(node => node.tag === 'button' && text(node) === 'Go to passage'));
  assert.ok(nodes.some(node => node.tag === 'button' && text(node) === 'Resume preparation'));
  const marked = nodes.find(node => node.tag === 'section' && node.props.id === 'p2');
  assert.match(marked.props.className, /failed-source/);
  assert.match(text(marked), /Could not be synthesized/);
  assert.match(text(nodes.find(node => String(node.props?.className).includes('dock-note'))), /could not be synthesized after a retry/);
});

test('settings explain unavailable word alignment and expose the cast review stop', () => {
  const nodes = appTree({modal: 'settings', health: {ready: false, active: null, missing: ['rimsky-infer'], word_alignment: false, pack_error: '', runtime_error: 'The bundled inference runtime is missing. Reinstall the application.', storage_bytes: 0},
    settings: {theme: 'light', language: 'ru', font_size: 20, line_height: 1.9, playback_rate: 1, volume: 1, follow: true, align_words: false}});
  const toggle = label => nodes.find(node => node.props?.role === 'switch' && node.props['aria-label'] === label);
  assert.equal(toggle('Выравнивание по словам').props.disabled, true);
  assert.equal(toggle('Останавливаться для проверки ролей перед озвучкой').props['aria-checked'], true);
  const all = text(nodes);
  assert.match(all, /Встроенная среда озвучки отсутствует\. Переустановите приложение\./); assert.doesNotMatch(all, /Отсутствуют модули среды/);
  assert.match(all, /Недоступно: в комплекте нет модуля выравнивания/);
  const enabled = appTree({modal: 'settings', settings: {theme: 'light', language: 'en', font_size: 20, line_height: 1.9, playback_rate: 1, volume: 1, follow: true, align_words: true, cast_review: false},
    health: {ready: true, active: null, missing: [], word_alignment: false, pack_error: '', storage_bytes: 0}});
  assert.equal(enabled.find(node => node.props?.role === 'switch' && node.props['aria-label'] === 'Word-level alignment').props.disabled, false, 'an enabled but unavailable aligner can still be turned off');
  assert.equal(enabled.find(node => node.props?.role === 'switch' && node.props['aria-label'] === 'Stop for cast review before narration').props['aria-checked'], false);
});

test('the correction dialog shows passage warnings, alias-qualified speakers and a New take option', () => {
  const generation = {...reviewGeneration, status: 'failed', cast: [...reviewGeneration.cast, {id: 'twin', name: 'Stranger', aliases: [], description: '', evidence: []}],
    segments: [seg('s2', 'p2', 'anna', {status: 'failed', warning: 'AppError: Speech output is empty, invalid, silent, or too long.'})]};
  const render = retake => appTree({modal: 'correct', generation, editSegment: generation.segments[0], retake});
  const nodes = render(false);
  const warning = nodes.find(node => String(node.props?.className).includes('passage-warning'));
  assert.equal(text(warning), 'Could not be synthesized. Correct its spoken text, then resume.');
  assert.equal(warning.props.title, 'Speech output is empty, invalid, silent, or too long.');
  const speakers = walk(nodes.find(node => node.tag === 'select' && node.props.value === 'anna')).filter(node => node.tag === 'option').map(text);
  assert.deepEqual(speakers, ['Narrator', 'Stranger (Anna, Anya)', 'Unit 7', 'Ghost', 'Stranger', 'Unresolved']);
  const save = tree => tree.find(node => node.tag === 'button' && text(node) === 'Save correction');
  assert.equal(save(nodes).props.disabled, true);
  assert.ok(nodes.some(node => node.tag === 'input' && node.props.type === 'checkbox'));
  assert.equal(save(render(true)).props.disabled, false);
});

test('cast, platform and export runtime messages translate, and an exit message keeps its process output tail', () => {
  const english = ['Cast changes are possible only while the generation waits for cast review.', 'Choose two different characters to merge.', 'This voice cannot be used for this character.',
    'Choose a narrator voice of this model pack.', 'Unknown character kind.', 'Missing speech model files.', 'Bundled runtime components are from different builds. Reinstall the application.', 'Another export is in progress.',
    'Local inference process exited.', 'Local inference process is unavailable.', 'Local inference process exited before completing its request.'];
  const runtime = ['runtime/rimsky/jobs.py', 'runtime/worker.py', 'runtime/rimsky/packs.py', 'runtime/server.py'].map(file => fs.readFileSync(path.join(root, file), 'utf8')).join('\n');
  assert.ok(runtime.includes("f'Local inference exceeded its {timeout}-second operation limit.'"));
  for (const message of [...english, 'Local inference exceeded its 1800-second operation limit.']) {
    assert.ok(message.includes('1800-second') || runtime.includes(`'${message}'`), `runtime raises exactly: ${message}`);
    assert.equal(i18n.localMessage('en', message), message);
    assert.match(i18n.localMessage('ru', message), /^[^a-z]*[а-яё][^a-z]*$/i, message);
  }
  const tail = 'Traceback (most recent call last): ImportError: DLL load failed while importing torch';
  assert.equal(i18n.localMessage('en', `Local inference process exited. Process output: ${tail}`), `Local inference process exited. Process output: ${tail}`);
  assert.equal(i18n.localMessage('ru', `AppError: Local inference process exited before completing its request. Process output: ${tail}`), `Локальный процесс модели завершил работу, не выполнив запрос. Вывод процесса: ${tail}`);
  assert.equal(i18n.localMessage('ru', `Preparation could not start: Local inference process is unavailable. Process output: ${tail}`), `Не удалось начать подготовку: Локальный процесс модели недоступен. Вывод процесса: ${tail}`);
  assert.equal(i18n.localMessage('ru', `A future exit text. Process output: ${tail}`), `A future exit text. Вывод процесса: ${tail}`);
  assert.equal(i18n.localMessage('ru', 'Local inference exceeded its 1800-second operation limit.'), 'Локальный процесс модели превысил лимит времени операции (1800 с).');
});

test('a failed passage shows the generic localized text and keeps the raw worker message as detail only', () => {
  const raw = 'RuntimeError: CUDA error: device-side assert triggered';
  const generic = {en: 'Could not be synthesized. Correct its spoken text, then resume.', ru: 'Не удалось озвучить. Исправьте произносимый текст и продолжите озвучку.'};
  assert.deepEqual(i18n.passageNote('ru', {status: 'failed', warning: raw}), {text: generic.ru, detail: raw});
  assert.deepEqual(i18n.passageNote('ru', {status: 'failed', warning: 'AppError: Speech output is empty, invalid, silent, or too long.'}),
    {text: generic.ru, detail: 'Синтезированная речь пустая, повреждённая, беззвучная или слишком длинная.'});
  assert.deepEqual(i18n.passageNote('en', {status: 'failed', warning: ''}), {text: generic.en, detail: ''});
  assert.deepEqual(i18n.passageNote('ru', {status: 'ready', warning: 'Unresolved speaker; narrator voice is used.'}), {text: 'Говорящий не определён; используется голос рассказчика.', detail: ''});
  assert.deepEqual(i18n.passageNote('en', null), {text: '', detail: ''});
  const failed = {...reviewGeneration, status: 'paused', segments: [seg('s1', 'p1', 'narrator', {status: 'ready', audio_hash: 'h', duration: 2}), seg('s2', 'p2', 'anna', {status: 'failed', warning: raw})]};
  for (const language of ['en', 'ru']) {
    const nodes = appTree({generation: failed, settings: {theme: 'light', language, font_size: 20, line_height: 1.9, playback_rate: 1, volume: 1, follow: true, align_words: false}}, {segment: failed.segments[1]});
    const note = nodes.find(node => String(node.props?.className).includes('dock-note'));
    assert.equal(text(note), generic[language]); assert.equal(note.props.title, raw); assert.match(note.props.className, /dock-warning/);
    assert.equal(text(walk(nodes.find(node => node.tag === 'section' && node.props.id === 'p2')).find(node => node.props?.className === 'passage-flag')), generic[language]);
  }
});

test('degraded passages of rendered chapters are listed once per analysed passage with a localized line and model-facing reasons as detail', () => {
  const reason = 'Passage p3-0-6: parts must cover units 0 to 2 in order without gaps or overlaps; the next part must start at unit 1 and end at or before unit 2.';
  const timeout = {en: 'Local inference exceeded its 1800-second operation limit.', ru: 'Локальный процесс модели превысил лимит времени операции (1800 с).'};
  const generation = {...reviewGeneration, analysis: {...reviewGeneration.analysis, degraded: [{source: 'p3-0-6', reason}, {source: 'p2-0-15', reason: timeout.en}, {source: 'p9-0-12', reason: 'A default chapter this generation does not narrate.'}]},
    segments: [...reviewGeneration.segments, seg('p2-0-15', 'p2', 'unknown', {text: 'Second passage.', end: 15})]};
  const line = {en: 'Speaker analysis failed; held for review.', ru: 'Не удалось определить говорящих; отрывок ожидает проверки.'};
  const summary = {en: 'Passages without speaker analysis: 2', ru: 'Отрывки без анализа говорящих: 2'};
  const notes = {id: 'c9', title: 'Notes', blocks: [{id: 'p9', kind: 'paragraph', text: 'An analysed default chapter.', html: '', assets: []}]};
  const book = new Map([...passages, ['p9', {chapter: notes, block: notes.blocks[0]}]]);
  for (const language of ['en', 'ru']) {
    const nodes = castPanel(generation, {voices: packVoices}, {language, passages: book});
    const lines = nodes.filter(node => node.props?.className === 'cast-reason');
    assert.deepEqual(lines.map(text), [line[language], line[language]]); assert.deepEqual(lines.map(node => node.props.title), [reason, timeout[language]]);
    assert.doesNotMatch(text(nodes), /parts must cover units|operation limit|лимит времени|does not narrate/);
    const list = nodes.find(node => node.tag === 'details' && text(walk(node).find(child => child.tag === 'summary')) === summary[language]);
    assert.deepEqual(walk(list).filter(node => node.tag === 'button').map(text), ['Third.', 'Second passage.']);
  }
  const legacy = castPanel({...reviewGeneration, analysis: {...reviewGeneration.analysis, degraded: []}}).find(node => node.props?.className === 'cast-reason');
  assert.equal(text(legacy), 'Speaker analysis failed; held for review.'); assert.equal(legacy.props.title, undefined);
});

test('the illustration panel receives Codex availability and opening it refreshes health', () => {
  const health = codex => ({ready: true, active: null, missing: [], word_alignment: true, pack_error: '', storage_bytes: 0, ...(codex === undefined ? {} : {codex})});
  const hint = 'Codex CLI was not found. Install it and sign in, then reopen this panel.';
  assert.ok(appTree({modal: 'illustration', health: health(false)}).some(node => node.tag === 'p' && text(node) === hint));
  for (const codex of [true, undefined]) assert.ok(!appTree({modal: 'illustration', health: health(codex)}).some(node => text(node) === hint));
  const calls = [];
  for (const modal of ['illustration', 'settings', null]) effect("modal==='illustration'", {modal, refreshHealth: async () => { calls.push(modal); }})();
  assert.deepEqual(calls, ['illustration']);
});

test('Cancel export follows the running export instead of the label language, and a queued narration start is not shown as casting', () => {
  const settings = language => ({theme: 'light', language, font_size: 20, line_height: 1.9, playback_rate: 1, volume: 1, follow: true, align_words: false});
  const cancel = (nodes, label) => walk(nodes.find(node => node.props?.className === 'busy-pill')).filter(node => node.tag === 'button' && text(node) === label);
  const english = i18n.translator('en')('exportingAudio');
  for (const [language, label] of [['en', 'Cancel export'], ['ru', 'Отменить экспорт']]) {
    assert.equal(cancel(appTree({busy: english, exporting: true, settings: settings(language)}), label).length, 1, `an export started in English stays cancellable in ${language}`);
    assert.equal(cancel(appTree({busy: i18n.translator(language)('exportingAudio'), exporting: false, settings: settings(language)}), label).length, 0);
  }
  const panel = appTree({modal: 'export', generation: {...reviewGeneration, status: 'ready'}, busy: english, exporting: true, settings: settings('ru')});
  assert.ok(panel.some(node => node.tag === 'button' && text(node) === 'Отменить экспорт'));
  const queued = {...reviewGeneration, status: 'queued', analysis: {...reviewGeneration.analysis, approved: true, progress: {stage: 'casting', done: 1, total: 1}}};
  assert.equal(text(appTree({generation: queued}).find(node => node.props?.className === 'dock-status').props.children[0]), 'Queued · 0/5');
});

test('a degraded render hold is explained and cannot be cleared by confirming the speaker', () => {
  const holdCodes = [...fs.readFileSync(path.join(root, 'runtime/rimsky/store.py'), 'utf8').match(/^HOLD_CODES = \(([^)]*)\)/m)[1].matchAll(/'(\w+)'/g)].map(match => match[1]);
  for (const code of holdCodes) assert.notEqual(review.holdReason('en', code), code, `${code} has an explanation`);
  assert.match(review.holdReason('en', 'render_degraded'), /^provisional take/);
  assert.match(review.holdReason('ru', 'render_degraded'), /^черновой дубль/);
  const segment = {id: 's2', source_id: 'src', start: 0, end: 9, status: 'ready', text: 'Он ушёл.', performance: {}};
  const queue = review.holdQueue({analysis: {speaker_resolution: [{source_id: 'src', start: 0, end: 9, status: 'review', triggers: ['render_degraded']}]}, segments: [segment]});
  assert.deepEqual(queue.map(item => item.reasons), [['render_degraded']]);
  assert.equal(review.speakerConfirmationAvailable(queue[0]), false);
});
