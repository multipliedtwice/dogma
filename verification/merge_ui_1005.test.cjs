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
const utils = load('src/lib/utils.ts');
const pause = load('src/lib/preparationPause.ts');
const appFile = path.join(root, 'src/App.tsx');
const appSource = fs.readFileSync(appFile, 'utf8');
const app = ts.createSourceFile(appFile, appSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function find(predicate) { let found; const visit = node => { if (found) return; if (predicate(node)) { found = node; return; } ts.forEachChild(node, visit); }; visit(app); assert.ok(found, 'App source node exists'); return found; }
function evaluate(node, environment) { return new Function(...Object.keys(environment), ts.transpileModule('return (' + node.getText(app) + ')', {compilerOptions: {target: ts.ScriptTarget.ES2022}}).outputText)(...Object.values(environment)); }
const effect = (fragment, environment) => evaluate(find(node => ts.isCallExpression(node) && node.expression.getText(app) === 'useEffect' && node.arguments[0]?.getText(app).includes(fragment)).arguments[0], environment);
const walk = node => Array.isArray(node) ? node.flatMap(walk) : !node || typeof node !== 'object' ? [] : [node, ...walk(node.props?.children)];
const text = node => Array.isArray(node) ? node.map(text).join('') : !node || typeof node === 'boolean' ? '' : typeof node === 'object' ? text(node.props?.children) : String(node);
const button = (nodes, label) => nodes.find(node => node.tag === 'button' && text(node) === label);

const chapter = {id: 'c1', title: 'Harbor', blocks: [{id: 'p1', kind: 'paragraph', text: 'First passage text.', html: '<p>First passage text.</p>', assets: []}, {id: 'p2', kind: 'paragraph', text: 'Second passage.', html: '<p>Second passage.</p>', assets: []}]};
const passages = new Map(chapter.blocks.map(block => [block.id, {chapter, block}]));
const seg = (id, source, speaker, overrides = {}) => ({id, ordinal: 1, chapter_id: 'c1', source_id: source, start: 0, end: 5, text: 'Hello', spoken: 'Hello', speaker, delivery: 'neutral', status: 'ready', audio_hash: 'h', duration: 1, cues: [], warning: '', ...overrides});
const cast = [{id: 'narrator', name: 'Narrator', aliases: [], description: '', evidence: [], voice_id: 'n', voice_name: 'Narrator voice'},
  {id: 'anna', name: 'Anna', aliases: [], description: '', evidence: [], voice_id: 'a', voice_name: 'Aiden'}];
const resolution = (source, start, end, status = 'review', extra = {}) => ({target_id: source, source_id: source, start, end, speaker: 'anna', primary_speaker: 'anna', tier: 'local', triggers: [], status, ...extra});
const heldGeneration = {id: 'g', book_id: 'b', name: 'Narration', status: 'held', error: '', created: '', updated: '', parent_id: null, revision: 2, exports: [],
  config: {chapters: ['c1'], pack_name: 'Pack', pack_fingerprint: 'f', align_words: false},
  cast,
  analysis: {complete: true, approved: true, speaker_resolution: [resolution('p1', 0, 5), resolution('p1', 1, 3, 'review', {reason: 'neighbor_only_evidence'}), resolution('p9', 0, 4), resolution('p2', 0, 5, 'resolved')],
    speaker_resolution_metrics: {review: 3}, speaker_review_holds: {flagged_segments: 2, held_with_neighbours: 1, reasons: {speaker_review: 1, neighbour: 1}}},
  segments: [seg('s1', 'p1', 'anna', {status: 'review', audio_hash: null, duration: 0}), seg('s2', 'p2', 'unknown', {status: 'review', audio_hash: null, duration: 0, text: 'Second passage.'}), seg('s3', 'p2', 'narrator', {start: 6, end: 15})]};

function castPanel(generation, props = {}) {
  return walk(sourceTree('src/components/CastPanel.tsx', {states: {voices: []}}, {component: 'CastPanel', generation, chapters: [chapter], passages, language: 'en', busy: false, blocked: '', previewing: '',
    onPreview: () => {}, onNavigate: () => {}, onRename: () => {}, onMerge: () => {}, onVoice: () => {}, onKind: () => {}, onNarrator: () => {}, onStart: () => {}, onReview: () => {}, ...props}).tree);
}
function appTree(states, player = {}) {
  const book = {id: 'b', title: 'Book', author: 'Author', chapters: [chapter], bookmarks: [], generations: [], visuals: [], warnings: []};
  return walk(sourceTree('src/App.tsx', {runtimeLost: false, player: {playing: false, waiting: false, position: 0, error: '', saveError: '', previewing: false, segment: null, ...player},
    states: {connected: true, book, chapterId: 'c1', health: {ready: true, active: null, missing: [], word_alignment: true, pack_error: '', storage_bytes: 0, ffmpeg: true, ffprobe: true},
      settings: {theme: 'light', language: 'en', font_size: 20, line_height: 1.9, playback_rate: 1, volume: 1, follow: true, align_words: false}, ...states}}).tree);
}

test('held generation status is labelled, inactive, resumable and reports its held count', () => {
  assert.equal(i18n.statusLabel('en', 'held'), 'Held for review');
  assert.equal(i18n.statusLabel('ru', 'held'), 'Ожидает проверки');
  assert.equal(utils.active('held'), false);
  assert.equal(review.preparationStatus('en', heldGeneration, 1, 3), 'Held for review · 1/3 · held: 2');
  assert.equal(review.heldCount({progress: {total: 4, ready: 1, duration: 0, held: 3}}), 3);
  const nodes = appTree({generation: heldGeneration});
  assert.equal(button(nodes, 'Resume preparation')?.props.disabled, false);
  assert.ok(button(nodes, 'Review held passages'));
  assert.ok(!nodes.some(node => node.tag === 'button' && /Pause preparation|Requesting pause/.test(text(node))));
  assert.match(text(nodes.find(node => String(node.props?.className).includes('dock-status'))), /^Held for review · 1\/3 · held: 2/);
  const pill = appTree({generation: heldGeneration, modal: 'generations', book: {id: 'b', title: 'Book', author: 'Author', chapters: [chapter], bookmarks: [], visuals: [], warnings: [],
    generations: [{...heldGeneration, progress: {total: 3, ready: 1, duration: 1, held: 2}}]}});
  assert.ok(pill.some(node => node.tag === 'span' && String(node.props?.className).includes('status-pill') && String(node.props.className).includes('held') && text(node) === 'Held for review'));
  assert.ok(pill.some(node => node.tag === 'span' && text(node) === 'held: 2'));
  const summary = appTree({generation: null, modal: 'generations', book: {id: 'b', title: 'Book', author: 'Author', chapters: [chapter], bookmarks: [], visuals: [], warnings: [],
    generations: [{...heldGeneration, progress: {total: 3, ready: 1, duration: 1, held: 5}}]}});
  assert.ok(summary.some(node => node.tag === 'span' && text(node) === 'held: 5'), 'saved summaries use progress.held');
  const exporting = text(appTree({generation: heldGeneration, modal: 'export'}));
  assert.match(exporting, /A complete audiobook cannot be exported while passages are held for speaker review\./);
  assert.doesNotMatch(text(appTree({generation: {...heldGeneration, status: 'ready', segments: [seg('s3', 'p2', 'narrator')]}, modal: 'export'})), /cannot be exported while passages are held/);
});

test('the cast panel lists the speaker hold queue in review and held states and opens the held segment for correction', () => {
  const opened = [];
  const nodes = castPanel(heldGeneration, {onReview: segment => opened.push(segment.id)});
  const holds = nodes.find(node => node.props?.className === 'cast-holds');
  assert.ok(holds, 'hold queue is shown after cast approval');
  assert.match(text(holds), /Passages held for speaker review: 3/);
  assert.match(text(holds), /Preparation finished, but some passages are held for speaker review\./);
  assert.match(text(holds), /speaker needs review: 1 · next to a held passage: 1/);
  const items = walk(holds).filter(node => node.tag === 'button');
  assert.deepEqual(items.map(text), ['Hello', 'p9', 'Second passage.']);
  assert.deepEqual(items.map(item => item.props.disabled), [false, true, false]);
  items[0].props.onClick(); items[2].props.onClick();
  assert.deepEqual(opened, ['s1', 's2']);
  const inReview = castPanel({...heldGeneration, status: 'review', approved: false});
  assert.ok(inReview.find(node => node.props?.className === 'cast-holds'));
  assert.match(text(inReview), /Starting narration does not release speaker holds\./);
  const clear = castPanel({...heldGeneration, status: 'ready', analysis: {complete: true}, segments: [seg('s3', 'p2', 'narrator')]});
  assert.ok(!clear.find(node => node.props?.className === 'cast-holds'));
});

test('confirm speaker sends an explicit verdict only for a held segment with a known, unchanged speaker', () => {
  const known = heldGeneration.segments[0], unknown = heldGeneration.segments[1];
  assert.deepEqual(review.confirmSpeakerRequest(known, {...known}), {id: 's1', confirm_speaker: true});
  assert.equal(review.confirmSpeakerRequest(unknown, {...unknown}), null);
  assert.equal(review.confirmSpeakerRequest(known, {...known, speaker: 'narrator'}), null);
  assert.equal(review.confirmSpeakerRequest(undefined, known), null);
  const confirm = editSegment => button(appTree({generation: heldGeneration, modal: 'correct', editSegment, retake: false}), 'Confirm speaker');
  assert.equal(confirm(known).props.disabled, false);
  assert.equal(confirm(unknown), undefined, 'an unresolved non-speaker hold has no ineffective confirmation action');
  assert.match(text(appTree({generation: heldGeneration, modal: 'correct', editSegment: unknown, retake: false})), /An unresolved speaker cannot be confirmed\./);
  assert.equal(confirm(heldGeneration.segments[2]), undefined, 'a passage that is not held has no confirm action');
  const sent = [];
  const reviseSegment = evaluate(find(node => ts.isVariableDeclaration(node) && node.name.getText(app) === 'reviseSegment').initializer, {
    run: (_label, task) => task(), generation: heldGeneration, book: {generations: []}, language: 'en', ru: false, selectionEpoch: {current: 1}, modalRevision: {current: 1},
    revisionName: () => 'Narration (corrected)', api: async (operation, body) => { sent.push([operation, body]); return {name: 'Narration (corrected)'}; },
    setGeneration: () => {}, closeModal: () => {}, refreshBook: async () => {}, setNotice: () => {}});
  return reviseSegment('confirmingSpeaker', [review.confirmSpeakerRequest(known, {...known})]).then(() => {
    assert.deepEqual(sent, [['revise', {id: 'g', name: 'Narration', changes: [{id: 's1', confirm_speaker: true}]}]]);
  });
});

test('published holds are authoritative and non-speaker holds offer no speaker confirmation', () => {
  const stale = {...heldGeneration, analysis: {...heldGeneration.analysis, speaker_review_holds: {flagged_ids: [], held_ids: []}}};
  assert.equal(review.segmentHeld(stale, stale.segments[0]), false, 'a stale review status cannot reopen a resolved hold');
  assert.equal(review.speakerConfirmationAvailable({reasons: ['backend_limit']}), false);
  assert.equal(review.speakerConfirmationAvailable({reasons: ['neighbour']}), false);
  assert.equal(review.speakerConfirmationAvailable({reasons: ['presentation_review']}), false);
  assert.equal(review.speakerConfirmationAvailable({reasons: ['critic_disagreement']}), true);
  assert.equal(review.speakerConfirmationAvailable(undefined), true, 'legacy held passages keep their confirmation action');
});

test('a sentence with an internal transition shows its parts read-only and locks spoken-text editing', () => {
  const parts = [{start: 0, end: 7, text: '— Стой,', speaker: 'anna', delivery: 'firm'}, {start: 7, end: 20, text: ' — сказала она.', speaker: 'narrator', delivery: 'neutral'}];
  const mixed = seg('m1', 'p1', 'anna', {status: 'review', text: '— Стой, — сказала она.', spoken: '— Стой, — сказала она.', end: 20,
    performance: {schema: 2, delivery: 'firm', pace: 'normal', ending_pace: null, pause_before_seconds: 0, pause_after_seconds: 0, cues: [], source_id: 'p1', basis: 'context', analysis_parts: parts, internal_transition: true}});
  const generation = {...heldGeneration, segments: [mixed, ...heldGeneration.segments.slice(1)]};
  const nodes = appTree({generation, modal: 'correct', editSegment: mixed, retake: false});
  assert.equal(nodes.find(node => node.tag === 'textarea').props.disabled, true);
  assert.match(text(nodes), /Each part keeps its own voice, so its spoken text cannot be edited as one passage\./);
  const listed = walk(nodes.find(node => node.props?.className === 'analysis-parts')).filter(node => node.tag === 'li')
    .map(li => text(li.props.children[0]) + '|' + text(li.props.children.find(child => child?.tag === 'small')));
  assert.deepEqual(listed, ['— Стой,|Anna · Firm', ' — сказала она.|Narrator · Neutral']);
  const plain = appTree({generation: heldGeneration, modal: 'correct', editSegment: heldGeneration.segments[0], retake: false});
  assert.equal(plain.find(node => node.tag === 'textarea').props.disabled, false);
  assert.ok(!plain.find(node => node.props?.className === 'analysis-parts'));
});

test('the in-passage follow effect never scrolls the reader during a cast voice preview', () => {
  const follows = [];
  let listener = null, subscribed = 0;
  const segment = {id: 's', source_id: 'p1', chapter_id: 'c1', start: 0, end: 10, duration: 2};
  const snapshot = {playing: true, previewing: false, segment, position: 1};
  const clock = {subscribe: callback => { subscribed++; listener = callback; return () => {}; }, getSnapshot: () => snapshot};
  const environment = previewing => ({player: {previewing, clock}, settings: {follow: true}, browsing: false, mode: 'read', book: {id: 'b'}, generation: {id: 'g'}, manualReading: {current: false},
    chapter: {id: 'c1'}, segments: {byId: new Map([['s', segment]])}, readerIndex: {sources: new Map([['p1', {block: {text: 'x'.repeat(20)}}]])}, viewRef: {current: 'root'},
    followPlaybackProgress: (...args) => follows.push(args)});
  assert.equal(effect('followPlaybackProgress', environment(true))(), undefined);
  assert.equal(subscribed, 0);
  effect('followPlaybackProgress', environment(false))();
  assert.equal(subscribed, 1);
  listener();
  assert.deepEqual(follows, [['root', 'p1', 0.25]]);
  snapshot.previewing = true; listener();
  assert.equal(follows.length, 1, 'a preview that starts after subscription is ignored too');
});

test('the launch selection guard keeps App renderable without a browser window', () => {
  assert.equal(typeof globalThis.window, 'undefined');
  assert.match(appSource, /const listener=useRef\(hostedListenerConfig\(\)\)\.current;\r?\n\s*const listenerMode=listener\.enabled;\r?\n\s*const launchSelection=useRef\(listener\.target\);/);
  const {tree, refs} = sourceTree('src/App.tsx', {runtimeLost: false, states: {connected: true, book: null, library: [], health: {ready: true, active: null, missing: []},
    settings: {theme: 'light', language: 'en', font_size: 20, line_height: 1.9, playback_rate: 1, volume: 1, follow: true,
      online_recommendations: false, recommendation_genres: []}}});
  assert.ok(walk(tree).length > 0);
  assert.equal(refs[1].current, null);
});

test('stress feedback presents the selected word and compact accent choices before other issues', () => {
  const nodes = appTree({generation: heldGeneration, stressMenu: {sourceId: 'p1', text: 'плоти', textOffset: 0,
    segmentId: 's1', generationId: 'g', x: 20, y: 20}});
  const dialog = nodes.find(node => node.props?.role === 'dialog');
  assert.equal(dialog?.props['aria-labelledby'], 'stress-feedback-title');
  assert.equal(text(nodes.find(node => node.tag === 'h2' && node.props?.id === 'stress-feedback-title')), 'Narration feedback');
  assert.equal(text(nodes.find(node => String(node.props?.className).includes('stress-menu-header'))).includes('Selectedплоти'), true);
  assert.deepEqual(nodes.filter(node => String(node.props?.className).includes('stress-variant-word')).map(text), ['пло́ти', 'плоти́']);
  assert.ok(nodes.some(node => node.tag === 'legend' && text(node) === 'Choose the correct stress'));
  assert.ok(nodes.some(node => String(node.props?.className).includes('stress-other-heading') && text(node).includes('Another issue')));
  assert.ok(nodes.some(node => node.tag === 'button' && node.props?.['aria-label'] === 'Close'));
});

const mixedParts = [{start: 0, end: 7, text: '— Стой,', speaker: 'anna', delivery: 'firm'}, {start: 7, end: 20, text: ' — сказала она.', speaker: 'narrator', delivery: 'neutral'}, {start: 20, end: 26, text: ' — Кто?', speaker: 'unknown', delivery: 'questioning'}];
const mixedHeld = seg('m2', 'p1', 'anna', {status: 'review', audio_hash: null, text: '— Стой, — сказала она. — Кто?', spoken: '— Стой, — сказала она. — Кто?', end: 26,
  performance: {schema: 2, delivery: 'firm', pace: 'normal', ending_pace: null, pause_before_seconds: 0, pause_after_seconds: 0, cues: [], source_id: 'p1', basis: 'context', analysis_parts: mixedParts, internal_transition: true}});

test('stress Save enables for a chosen or typed accent and keeps errors inside the dialog',()=>{
  const stressMenu={sourceId:'p1',text:'деревушки',textOffset:0,segmentId:'s1',generationId:'g',x:20,y:20};
  assert.equal(button(appTree({generation:heldGeneration,stressMenu}),'Save stress').props.disabled,true);
  const nodes=appTree({generation:heldGeneration,stressMenu,respelling:'дереву\u0301шки',error:'Could not save; retry'});
  assert.equal(button(nodes,'Save stress').props.disabled,false);
  assert.equal(nodes.filter(n=>n.props?.['aria-pressed']===true&&n.props?.['aria-label']?.startsWith('Stress:')).length,1);
  assert.ok(nodes.some(n=>n.props?.className==='stress-menu-error'&&n.props.role==='alert'&&text(n)==='Could not save; retry'));
  assert.equal(button(appTree({generation:heldGeneration,stressMenu,respelling:'дереву\u0301шка'}),'Save stress').props.disabled,true);
});

test('part-level verdicts build exact revise payloads and never mix with whole-passage changes', () => {
  const edit = {...mixedHeld};
  assert.deepEqual(review.partRequests(mixedHeld, edit), []);
  assert.deepEqual(review.confirmPartRequest(mixedHeld, edit, 0), {id: 'm2', part: 0, confirm_speaker: true});
  assert.deepEqual(review.confirmPartRequest(mixedHeld, edit, 1), {id: 'm2', part: 1, confirm_speaker: true});
  assert.equal(review.confirmPartRequest(mixedHeld, edit, 2), null, 'an unknown part speaker cannot be confirmed');
  assert.equal(review.confirmPartRequest(mixedHeld, edit, 3), null);
  const changed = review.withPartSpeaker(review.withPartSpeaker(edit, 2, 'anna'), 1, 'anna');
  assert.equal(mixedHeld.performance.analysis_parts[2].speaker, 'unknown', 'editing a part never mutates the saved segment');
  assert.deepEqual(review.partRequests(mixedHeld, changed), [{id: 'm2', part: 1, speaker: 'anna'}, {id: 'm2', part: 2, speaker: 'anna'}]);
  assert.equal(review.confirmPartRequest(mixedHeld, changed, 0), null, 'unsaved part edits disable part confirmation');
  assert.equal(review.confirmSpeakerRequest(mixedHeld, changed), null, 'unsaved part edits disable whole confirmation');
  assert.equal(review.confirmPartRequest(mixedHeld, {...edit, delivery: 'soft'}, 0), null);
  assert.deepEqual(review.partRequests(heldGeneration.segments[0], {...heldGeneration.segments[0]}), [], 'a plain segment has no part verdicts');
});

test('the correction modal offers per-part speaker selects and Confirm part only for held mixed sentences', () => {
  const generation = {...heldGeneration, segments: [mixedHeld, ...heldGeneration.segments.slice(1)]};
  const render = (editSegment, overrides = {}) => appTree({generation: {...generation, ...overrides}, modal: 'correct', editSegment, retake: false});
  const nodes = render(mixedHeld);
  const selects = nodes.filter(node => node.tag === 'select' && /^Speaker for part \d$/.test(node.props['aria-label']));
  assert.deepEqual(selects.map(node => node.props.value), ['anna', 'narrator', 'unknown']);
  assert.deepEqual(walk(selects[0]).filter(node => node.tag === 'option').map(node => node.props.value), ['narrator', 'anna', 'unknown']);
  const confirms = nodes.filter(node => node.tag === 'button' && /^Confirm part \d$/.test(node.props['aria-label']));
  assert.deepEqual(confirms.map(node => node.props.disabled), [false, false, true]);
  assert.match(text(nodes), /Confirm speaker accepts only the parts spoken by the shown speaker\. Other parts stay held/);
  assert.equal(button(nodes, 'Confirm speaker').props.disabled, false);
  assert.equal(button(nodes, 'Save correction').props.disabled, true);
  const edited = render(review.withPartSpeaker(mixedHeld, 0, 'narrator'));
  assert.deepEqual(edited.filter(node => node.tag === 'button' && /^Confirm part \d$/.test(node.props['aria-label'])).map(node => node.props.disabled), [true, true, true]);
  assert.equal(button(edited, 'Confirm speaker').props.disabled, true);
  assert.equal(button(edited, 'Save correction').props.disabled, false);
  assert.doesNotMatch(text(edited), /Change the spoken text, speaker or delivery/);
  const both = render({...review.withPartSpeaker(mixedHeld, 0, 'narrator'), delivery: 'soft'});
  assert.equal(button(both, 'Save correction').props.disabled, true);
  assert.match(text(both), /Part speakers and whole-passage changes are saved separately\./);
  const notHeld = render({...mixedHeld, status: 'ready'}, {segments: [{...mixedHeld, status: 'ready'}], analysis: {complete: true}});
  assert.ok(!notHeld.some(node => node.tag === 'select' && /^Speaker for part/.test(node.props['aria-label'] || '')), 'parts stay read-only when nothing is held');
  assert.ok(notHeld.find(node => node.props?.className === 'analysis-parts'));
  const sent = [];
  const env = {run: (_label, task) => task(), generation, book: {generations: []}, language: 'en', ru: false, selectionEpoch: {current: 1}, modalRevision: {current: 1},
    revisionName: () => 'Narration (corrected)', api: async (operation, body) => { sent.push(body.changes); return {name: 'Narration (corrected)'}; },
    setGeneration: () => {}, closeModal: () => {}, refreshBook: async () => {}, setNotice: () => {}};
  const reviseSegment = evaluate(find(node => ts.isVariableDeclaration(node) && node.name.getText(app) === 'reviseSegment').initializer, env);
  return reviseSegment('savingCorrection', review.partRequests(mixedHeld, review.withPartSpeaker(mixedHeld, 2, 'anna'))).then(() => {
    assert.deepEqual(sent, [[{id: 'm2', part: 2, speaker: 'anna'}]]);
  });
});

test('preparation pause treats a held generation as settled', () => {
  assert.equal(pause.healthSettling({id: 'g', status: 'held'}, {active: 'g'}), true);
  assert.equal(pause.healthSettling({id: 'g', status: 'rendering'}, {active: 'g'}), false);
  assert.equal(pause.healthSettling({id: 'g', status: 'held'}, {active: null}), false);
});

const presentationBinding = [{part: null, start: 0, end: 26, voice: 'a', base_voice: 'n', channel: 'radio', register: 'transmission', confidence: 'medium', pack: 'Pack'},
  {part: 1, start: 7, end: 20, voice: 'radio-x', base_voice: 'n', channel: 'radio', register: 'written', confidence: 'low', pack: 'Pack'}];
const presented = {...mixedHeld, id: 'm3', performance: {...mixedHeld.performance, presentation_binding: presentationBinding}};

test('every hold trigger code has a readable English and Russian label', () => {
  for (const code of ['critic_disagreement', 'critic_unavailable', 'descriptor_identity', 'local_fallback_review', 'presentation_review', 'unresolved_speaker', 'degraded_analysis', 'neighbor_only_evidence', 'backend_limit']) {
    const english = review.holdReason('en', code), russian = review.holdReason('ru', code);
    assert.notEqual(english, code, code); assert.match(english, /^[a-z][a-z ,;-]+$/i, code);
    assert.match(russian, /[а-яё]/i, code); assert.doesNotMatch(russian, /[a-z]/i, code);
  }
  assert.equal(review.holdReason('en', 'future_code'), 'future_code');
});

test('the hold queue merges trigger codes per passage and shows critic and presentation details', () => {
  const generation = {...heldGeneration, segments: [presented, ...heldGeneration.segments.slice(1)],
    analysis: {...heldGeneration.analysis, speaker_resolution: [
      resolution('p1', 0, 5, 'review', {triggers: ['critic_disagreement', 'unattributed_exchange'], critic_speaker: 'narrator', critic_reason: 'The quote is narration.'}),
      resolution('p1', 7, 20, 'review', {triggers: ['presentation_review', 'descriptor_identity']})]}};
  const queue = review.holdQueue(generation);
  assert.deepEqual(queue.map(item => [item.key, item.reasons]), [['m3', ['critic_disagreement', 'presentation_review', 'descriptor_identity']], ['s2', []]]);
  assert.deepEqual(queue[0].critics, [{speaker: 'narrator', reason: 'The quote is narration.'}]);
  const unflagged = review.holdQueue({analysis: {}, segments: [{...presented, status: 'review'}]});
  assert.deepEqual(unflagged[0].reasons, ['presentation_review'], 'a held segment with a presentation binding is labelled as such');
  const holds = castPanel(generation).find(node => node.props?.className === 'cast-holds');
  const all = text(holds);
  assert.match(all, /an independent critic pass disagreed, presentation voice needs review, identity rests on a description, not a name/);
  assert.match(all, /Critic: Narrator — The quote is narration\./);
  assert.match(all, /Whole passage: voice: Aiden · channel: radio · register: transmission/);
  assert.match(all, /Part 2: voice: radio-x · channel: radio · register: written/);
  assert.match(all, /Confirming the speaker does not approve the presentation voice, and confirming the presentation voice does not confirm the speaker\./);
  const russian = text(walk(sourceTree('src/components/CastPanel.tsx', {states: {voices: [{id: 'radio-x', name: 'Эфир'}]}}, {component: 'CastPanel', generation, chapters: [chapter], passages, language: 'ru', busy: false, blocked: '', previewing: '',
    onPreview: () => {}, onNavigate: () => {}, onRename: () => {}, onMerge: () => {}, onVoice: () => {}, onKind: () => {}, onNarrator: () => {}, onStart: () => {}, onReview: () => {}}).tree));
  assert.match(russian, /независимая проверка не согласилась/);
  assert.match(russian, /Часть 2: голос: Эфир · канал: radio · регистр: written/);
  assert.match(russian, /Подтверждение говорящего не утверждает голос подачи/);
});

test('presentation verdicts are separate segment and part actions with their own payloads', () => {
  assert.deepEqual(review.confirmPresentationRequest(presented, {...presented}, null), {id: 'm3', confirm_presentation: true});
  assert.deepEqual(review.confirmPresentationRequest(presented, {...presented}, 1), {id: 'm3', part: 1, confirm_presentation: true});
  assert.equal(review.confirmPresentationRequest(presented, {...presented}, 0), null, 'a part without a presentation binding has nothing to confirm');
  assert.equal(review.confirmPresentationRequest(presented, review.withPartSpeaker(presented, 0, 'narrator'), null), null);
  assert.equal(review.confirmPresentationRequest(presented, {...presented, delivery: 'soft'}, 1), null);
  assert.equal(review.confirmPresentationRequest(mixedHeld, {...mixedHeld}, null), null);
  const generation = {...heldGeneration, segments: [presented, ...heldGeneration.segments.slice(1)]};
  const render = editSegment => appTree({generation, modal: 'correct', editSegment, retake: false});
  const nodes = render(presented);
  const box = nodes.find(node => String(node.props?.className).includes('presentation-review'));
  assert.deepEqual(walk(box).filter(node => node.tag === 'li').map(li => text(li.props.children.find(child => child?.tag === 'small'))),
    ['voice: Aiden · channel: radio · register: transmission', 'voice: radio-x · channel: radio · register: written']);
  const actions = tree => tree.filter(node => node.tag === 'button' && /^Confirm presentation voice( \d)?$/.test(node.props['aria-label'] || ''));
  assert.deepEqual(actions(nodes).map(node => [node.props['aria-label'], node.props.disabled]), [['Confirm presentation voice', false], ['Confirm presentation voice 2', false]]);
  assert.ok(button(nodes, 'Confirm speaker'), 'speaker confirmation stays a separate action');
  assert.match(text(box), /confirming the presentation voice does not confirm the speaker/);
  assert.deepEqual(actions(render({...presented, delivery: 'soft'})).map(node => node.props.disabled), [true, true]);
  assert.ok(!render(mixedHeld).some(node => String(node.props?.className).includes('presentation-review')));
  const notHeld = appTree({generation: {...generation, analysis: {complete: true}, segments: [{...presented, status: 'ready'}]}, modal: 'correct', editSegment: {...presented, status: 'ready'}, retake: false});
  assert.ok(!notHeld.some(node => String(node.props?.className).includes('presentation-review')));
});

test('verdict rejection codes map to localized messages', () => {
  const failure = (code, message) => Object.assign(new Error(message), {code});
  assert.equal(i18n.operationMessage(failure('unknown_speaker', 'Choose a speaker before confirming this passage.'), 'en'), 'An unresolved speaker cannot be confirmed. Choose the speaker and save a corrected version.');
  assert.match(i18n.operationMessage(failure('unknown_speaker', 'x'), 'ru'), /^Неопределённого говорящего нельзя подтвердить/);
  assert.equal(i18n.operationMessage(failure('not_held', 'Only a held passage can be confirmed.'), 'en'), 'This passage is no longer held for review. Reopen it to see its current state.');
  assert.match(i18n.operationMessage(failure('not_held', 'x'), 'ru'), /^Этот отрывок больше не ожидает проверки/);
});

const computedGeneration = (holds, segments, resolutions) => ({...heldGeneration, segments,
  analysis: {complete: true, approved: true, speaker_resolution: resolutions, speaker_review_holds: {flagged_segments: holds.flagged_ids.length, held_with_neighbours: holds.held_ids.length - holds.flagged_ids.length, ...holds}}});

test('the hold queue follows published held_ids: a released presentation item is no longer listed or offered', () => {
  const released = computedGeneration({flagged_ids: [], held_ids: []}, [{...presented, status: 'ready'}, seg('s3', 'p2', 'narrator', {start: 6, end: 15})],
    [resolution('p1', 7, 20, 'review', {triggers: ['presentation_review']})]);
  assert.deepEqual(review.holdQueue(released), []);
  assert.equal(review.segmentHeld(released, released.segments[0]), false, 'an item still marked review does not hold a passage outside held_ids');
  assert.ok(!castPanel(released).find(node => node.props?.className === 'cast-holds'));
  const modal = appTree({generation: released, modal: 'correct', editSegment: released.segments[0], retake: false});
  assert.ok(!modal.some(node => String(node.props?.className).includes('presentation-review')));
  assert.equal(button(modal, 'Confirm speaker'), undefined);
  const speakerStillOpen = computedGeneration({flagged_ids: ['m3'], held_ids: ['m3']}, [{...presented, status: 'review'}],
    [resolution('p1', 7, 20, 'review', {triggers: ['presentation_review']}), resolution('p1', 0, 5, 'review', {triggers: ['critic_disagreement']})]);
  assert.deepEqual(review.holdQueue(speakerStillOpen)[0].presentation.length, 2);
  const presentationReleased = computedGeneration({flagged_ids: ['m3'], held_ids: ['m3']}, [{...presented, status: 'review'}],
    [resolution('p1', 0, 5, 'review', {triggers: ['critic_disagreement']})]);
  const [item] = review.holdQueue(presentationReleased);
  assert.deepEqual([item.reasons, item.presentation], [['critic_disagreement'], []], 'bindings appear only while a presentation item is open');
  const modalAfter = appTree({generation: presentationReleased, modal: 'correct', editSegment: presentationReleased.segments[0], retake: false});
  assert.ok(!modalAfter.some(node => String(node.props?.className).includes('presentation-review')));
  assert.ok(button(modalAfter, 'Confirm speaker'));
});

test('a neighbour-only passage is listed with reason neighbour and no borrowed details', () => {
  const generation = computedGeneration({flagged_ids: ['s1'], held_ids: ['s1', 's3']},
    [seg('s1', 'p1', 'anna', {status: 'review', audio_hash: null}), seg('s3', 'p2', 'narrator', {status: 'review', audio_hash: null, start: 0, end: 15, text: 'Second passage.'}), seg('s4', 'p2', 'narrator', {start: 16, end: 20, text: 'Free.'})],
    [resolution('p1', 0, 5, 'review', {triggers: ['critic_disagreement'], critic_speaker: 'narrator', critic_reason: 'Narration.'}), resolution('p2', 0, 15, 'review', {triggers: ['presentation_review']})]);
  const queue = review.holdQueue(generation);
  assert.deepEqual(queue.map(item => [item.key, item.reasons, item.critics.length, item.presentation.length]), [['s1', ['critic_disagreement'], 1, 0], ['s3', ['neighbour'], 0, 0]]);
  assert.equal(review.segmentHeld(generation, generation.segments[1]), true);
  assert.equal(review.segmentHeld(generation, generation.segments[2]), false);
  const holds = castPanel(generation).find(node => node.props?.className === 'cast-holds');
  assert.match(text(holds), /Second passage\.(?: · Harbor)? · next to a held passage/);
});

test('older generations without held_ids keep the status and open-item fallback', () => {
  const old = {...heldGeneration, analysis: {...heldGeneration.analysis, speaker_review_holds: {flagged_segments: 2, held_with_neighbours: 1}}};
  assert.deepEqual(review.holdQueue(old).map(item => item.key), ['s1', 'p9:0:2', 's2']);
  assert.equal(review.segmentHeld(old, {...old.segments[2], status: 'ready', source_id: 'p1', start: 0, end: 5}), true, 'open item overlap still holds without held_ids');
  assert.equal(review.segmentHeld({analysis: {}}, {id: 'x', status: 'review', source_id: 'p', start: 0, end: 1}), true);
});
