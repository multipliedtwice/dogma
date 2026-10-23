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
  new Function('require', 'module', 'exports', source)(require, module, module.exports);
  return module.exports;
}
const {musicTimeline, cueOffset, elapsedSince, musicLayers, narrationEnded, createMusicController,transitionEnvelope} = load('src/lib/music.ts');
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };

const segment = (id, ordinal, chapter, duration = 10, status = 'ready') => ({id, ordinal, chapter_id: chapter, status, duration, audio_hash: 'h' + id});
const take = (id, kind, duration, anchor = 'a', start = 0) => ({id, kind, chapter_id: 'c1', anchor_segment_id: anchor, start, mood: 'calm', status: 'ready', audio_key: 'k-' + id, duration, error: ''});
const scene = (id, anchor, bed, ambience, phrases, cycle) => ({id, chapter_id: 'c1', anchor_segment_id: anchor, mood: bed, bed, ambience, released: true, phrases, cycle});
const generation = {id: 'g', segments: [segment('a', 0, 'c1'), segment('b', 1, 'c1'), segment('c', 2, 'c2', 4), segment('d', 3, 'c2', 6)]};
const timeline = musicTimeline(generation);
const snap = (id, position, playing = true) => ({segment: generation.segments.find(s => s.id === id), position, playing, waiting: false, error: '', saveError: '', previewing: false});
const settings = {music: true, music_volume: .5, volume: .8, playback_rate: 1.25};
const mix = {crossfade: 6, ending_fade: 2, variants: 3};
const status = (overrides = {}) => ({enabled: true, available: true, pack_error: '', active: false, error: '', mix,
  cues: [take('bed:calm:0', 'bed', 5), take('bed:calm:1', 'bed', 5), take('bed:tense:0', 'bed', 5), take('ambience:rain', 'ambience', 8),
    take('ending:c2', 'ending', 40, 'd', 4)],
  scenes: [scene('c1:0', 'a', 'calm', 'rain', [[0, 0], [1, 7]], 14), scene('c2:0', 'c', 'tense', 'none', [[0, 0]], 8)], ...overrides});
const byId = layers => Object.fromEntries(layers.map(layer => [layer.id, layer]));

test('structural transitions swell before a break and crest only in its pause',()=>{
  assert.equal(transitionEnvelope({start:10,end:14},7),.6);
  assert.equal(transitionEnvelope({start:10,end:14},10),1.2);
  assert.equal(transitionEnvelope({start:10,end:14},11),2);
  assert.equal(transitionEnvelope({start:10,end:14},14),0);
  const directed=status({transitions:[{kind:'scene',anchor_segment_id:'b',start:0,end:4}],
    cues:status().cues.map(cue=>cue.id==='bed:calm:0'?{...cue,duration:20}:cue)});
  const before=byId(musicLayers(directed,timeline,snap('a',7)));
  assert.equal(before['transition:b'].gain,.6);
  assert.equal(before['transition:b'].offset,3);
  const pause=byId(musicLayers(directed,timeline,snap('b',1)));
  assert.equal(pause['transition:b'].gain,2);
  assert.equal(Object.values(pause).filter(l=>!l.id.startsWith('transition:')).every(l=>l.gain===0),true);
});

test('speech time is measured from an anchor across ready segments only', () => {
  assert.equal(elapsedSince('a', timeline, snap('c', 2).segment, 2), 22);
  assert.equal(elapsedSince('c', timeline, snap('a', 1).segment, 1), null);
  const gap = musicTimeline({segments: [segment('a', 0, 'c1'), segment('b', 1, 'c1', 0, 'pending'), segment('c', 2, 'c1')]});
  assert.equal(elapsedSince('a', gap, segment('c', 2, 'c1'), 1), null);
  assert.equal(cueOffset(take('e', 'ending', 40, 'd', 4), timeline, snap('d', 5).segment, 5), 1);
  assert.equal(cueOffset(take('e', 'ending', 40, 'd', 4), timeline, snap('d', 3).segment, 3), null);
});

test('a scene plays its phrase pattern, fills gaps with ambience and repeats its cycle', () => {
  const early = byId(musicLayers(status(), timeline, snap('a', 3)));
  assert.deepEqual(Object.keys(early).sort(), ['c1:0:0:0', 'c1:0:ambience']);
  assert.equal(early['c1:0:0:0'].key, 'k-bed:calm:0');
  assert.equal(early['c1:0:0:0'].gain, .5);  // Fading in over the crossfade.
  const gap = byId(musicLayers(status(), timeline, snap('a', 6)));
  assert.deepEqual(Object.keys(gap), ['c1:0:ambience']);  // Between phrases only ambience sounds.
  const second = byId(musicLayers(status(), timeline, snap('b', 0)));
  assert.equal(second['c1:0:0:1'].key, 'k-bed:calm:1');
  assert.equal(second['c1:0:0:1'].offset, 3);
  assert.equal(second['c1:0:ambience'].offset, 2);
  assert.equal(second['c1:0:ambience'].period, 8);
  const repeat = byId(musicLayers(status(), timeline, snap('b', 5)));
  assert.equal(repeat['c1:0:1:0'].offset, 1);  // Second cycle, first phrase.
});

test('scenes crossfade, missing variants fall back and unreleased scenes stay silent', () => {
  const layers = byId(musicLayers(status(), timeline, snap('c', 3)));
  assert.equal(layers['c2:0:0:0'].gain, .5);
  assert.equal(layers['c1:0:ambience'].gain, .5);  // The outgoing scene fades out as the next fades in.
  assert.equal(layers['c1:0:1:1'].key, 'k-bed:calm:1');
  const later = byId(musicLayers(status(), timeline, snap('d', 3)));
  assert.equal(Object.keys(later).some(id => id.startsWith('c1:0')), false);
  const onlyOne = status({cues: status().cues.filter(cue => cue.id !== 'bed:calm:1')});
  assert.equal(byId(musicLayers(onlyOne, timeline, snap('b', 0)))['c1:0:0:1'].key, 'k-bed:calm:0');
  const held = status({scenes: status().scenes.map(s => ({...s, released: false}))});
  assert.deepEqual(musicLayers(held, timeline, snap('a', 3)), []);
});

test('ending music starts under the last words while the bed fades out', () => {
  const layers = byId(musicLayers(status(), timeline, snap('d', 5)));
  assert.equal(layers['ending:c2'].offset, 1);
  assert.equal(layers['c2:0:1:0'].gain, .5);  // One second into a two-second ending fade.
  assert.equal(narrationEnded(timeline, snap('d', 5.9, false)), true);
  assert.equal(narrationEnded(timeline, snap('d', 2, false)), false);
});

test('an imported chapter mix replaces generated scene layers and stays locked to speech', () => {
  const imported = take('chapter_mix:c1', 'chapter_mix', 20);
  const layers = musicLayers(status({cues: [...status().cues, imported]}), timeline, snap('b', 2));
  assert.deepEqual(layers, [{id: 'chapter_mix:c1', key: 'k-chapter_mix:c1', offset: 12, gain: 1, period: null}]);
});

function element() {
  const target = new EventTarget();
  Object.assign(target, {src: '', currentTime: 0, paused: true, playbackRate: 1, volume: 1, loop: false, plays: 0});
  target.removeAttribute = name => { if (name === 'src') target.src = ''; };
  target.pause = () => { target.paused = true; };
  target.play = async () => { target.paused = false; target.plays++; };
  target.load = () => {};
  return target;
}

async function controller() {
  const made = [];
  const loads = [];
  const music = createMusicController({loadMusic: async key => { loads.push(key); return new Blob([key]); },
    createAudio: () => { const audio = element(); made.push(audio); return audio; }, createUrl: blob => 'blob:' + blob.size, revokeUrl: () => {}});
  music.configure(settings);
  music.setStatus(generation, status());
  return {music, made, loads};
}
const ready = made => made.forEach(audio => audio.dispatchEvent(new Event('loadedmetadata')));

test('book score stays silent throughout the branded opening, including settings and cue refreshes', async () => {
  const {music, made, loads} = await controller();
  const opening = {...snap('a', 0), opening: {key: 'dogma', position: 1, duration: 8}};
  assert.deepEqual(musicLayers(status(), timeline, opening), []);
  music.update(opening);
  music.configure({...settings});
  music.setStatus({...generation}, status());
  await settle();
  assert.deepEqual(loads, []);
  music.update(snap('a', 0));
  await settle();
  ready(made);
  assert.ok(made.some(audio => !audio.paused));
  music.update(opening);
  assert.ok(made.every(audio => audio.paused && audio.src === ''));
  assert.equal(narrationEnded(timeline, {...snap('d', 6, false), opening: opening.opening}), false);
  music.dispose();
});

test('controller mixes layers, follows speech, pauses with it and stops when music is turned off', async () => {
  const {music, made, loads} = await controller();
  music.update(snap('b', 0));
  await settle();
  ready(made);
  assert.deepEqual(loads.sort(), ['k-ambience:rain', 'k-bed:calm:1']);
  assert.equal(made.length, 2);
  const [phrase, ambience] = made[0].loop ? [made[1], made[0]] : made;
  assert.equal(ambience.loop, true);
  assert.equal(phrase.paused, false);
  assert.equal(phrase.currentTime, 3);
  assert.equal(phrase.volume, .4);
  assert.equal(phrase.playbackRate, 1.25);
  music.update(snap('b', .2, false));
  assert.equal(phrase.paused, true);
  music.configure({...settings, music: false});
  assert.equal(made.every(audio => audio.src === ''), true);
  music.update(snap('b', 3));
  assert.equal(loads.length, 2);
});

test('continuous premixed playback never downloads a second chapter WAV and cancels stale downloads', async () => {
  const pending = new Map(), created = [], revoked = [], made = [];
  const music = createMusicController({
    loadMusic: (key, signal) => new Promise(resolve => pending.set(key, {resolve, signal})),
    createAudio: () => { const audio = element(); made.push(audio); return audio; },
    createUrl: () => { const url = 'blob:music-' + created.length; created.push(url); return url; },
    revokeUrl: url => revoked.push(url),
  });
  music.configure(settings);
  music.setStatus(generation, status());
  const continuous = {...snap('b', 0), premixed: true};
  music.update(continuous);
  music.configure({...settings});
  assert.equal(pending.size, 0);
  assert.deepEqual(musicLayers(status(), timeline, continuous), []);
  music.update(snap('b', 0));
  assert.ok(pending.size > 0);
  music.update(continuous);
  for (const request of pending.values()) {
    assert.equal(request.signal.aborted, true);
    request.resolve(new Blob(['obsolete full chapter music']));
  }
  await settle();
  assert.deepEqual(created, []);
  assert.deepEqual(made, []);
  assert.deepEqual(revoked, []);
  music.dispose();
});

test('ending music keeps playing after the final words while beds stop', async () => {
  const {music, made, loads} = await controller();
  music.update(snap('d', 5));
  await settle();
  ready(made);
  assert.equal(loads[0], 'k-ending:c2');
  const ending = made[0];
  assert.equal(ending.currentTime, 1);
  assert.equal(ending.paused, false);
  music.update(snap('d', 6, false));
  assert.equal(ending.paused, false);
  assert.notEqual(ending.src, '');
  assert.equal(made.filter(audio => audio !== ending).every(audio => audio.src === ''), true);
});

test('an ending that starts exactly after speech is created once and is not restarted', async () => {
  const made = [], loads = [];
  const music = createMusicController({loadMusic: async key => { loads.push(key); return new Blob([key]); },
    createAudio: () => { const audio = element(); made.push(audio); return audio; }, createUrl: () => 'blob:ending', revokeUrl: () => {}});
  music.configure(settings);
  music.setStatus(generation, status({cues: [...status().cues.filter(cue => cue.kind !== 'ending'),
    take('ending:c2', 'ending', 20, 'c', 10)]}));
  music.update(snap('d', 6, false));
  await settle();
  ready(made);
  assert.deepEqual(loads, ['k-ending:c2']);
  assert.equal(made[0].plays, 1);
  made[0].currentTime = 20;
  made[0].paused = true;
  music.update(snap('d', 6, false));
  assert.equal(made[0].plays, 1);
});
