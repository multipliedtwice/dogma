const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
const source = ts.transpileModule(fs.readFileSync('src/lib/hlsTransport.ts', 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true},
}).outputText;
const loaded = {exports: {}};
new Function('require', 'module', 'exports', source)(require, loaded, loaded.exports);
const {createHlsPlaybackAdapter} = loaded.exports;

class Engine {
  static instances = [];
  static Events = {ERROR: 'error'};
  static isSupported() {return true;}
  constructor(config) {this.config = config; this.events = {}; this.destroyed = 0; Engine.instances.push(this);}
  on(event, callback) {this.events[event] = callback;}
  attachMedia(audio) {this.audio = audio;}
  loadSource(url) {this.url = url;}
  destroy() {this.destroyed++;}
}
const media = native => ({canPlayType: () => native ? 'probably' : '', loads: 0, load() {this.loads++;}});

test('native HLS delegates playlist playback to the media element', () => {
  const adapter = createHlsPlaybackAdapter(Engine, () => true), audio = media(true), signal = new AbortController();
  const count = Engine.instances.length;
  const binding = adapter.bind(audio, '/asset?kind=playback_hls&id=program', 123, signal.signal, () => assert.fail());
  assert.equal(audio.src, '/asset?kind=playback_hls&id=program');
  assert.equal(audio.loads, 1);
  assert.equal(Engine.instances.length, count);
  signal.abort(); binding.destroy();
});

test('hls.js owns one media source with bounded buffers and is destroyed on cancellation', () => {
  const adapter = createHlsPlaybackAdapter(Engine), audio = media(false), abort = new AbortController(), errors = [];
  const binding = adapter.bind(audio, '/playlist', 123, abort.signal, error => errors.push(error));
  const engine = Engine.instances.at(-1);
  assert.equal(engine.audio, audio); assert.equal(engine.url, '/playlist');
  assert.equal(engine.config.startPosition, 123);
  assert.equal(engine.config.maxBufferLength, 180); assert.equal(engine.config.backBufferLength, 30);
  assert.equal(engine.config.enableWorker, true);
  engine.events.error('error', {fatal: false, details: 'retry-in-progress'});
  assert.deepEqual(errors, [], 'Library retries must not be interrupted by application error handling');
  engine.events.error('error', {fatal: true, details: 'fragLoadError'});
  assert.match(errors[0].message, /fragLoadError/);
  abort.abort(); binding.destroy(); assert.equal(engine.destroyed, 1);
  engine.events.error('error', {fatal: true, details: 'obsolete'});
  assert.equal(errors.length, 1);
});

test('an already cancelled binding cannot start downloading audio', () => {
  const adapter = createHlsPlaybackAdapter(Engine), audio = media(false), abort = new AbortController();
  abort.abort();
  const count = Engine.instances.length;
  adapter.bind(audio, '/obsolete', 0, abort.signal, () => assert.fail()).destroy();
  assert.equal(Engine.instances.length, count);
});

test('Chrome uses hls.js retries even when it advertises native HLS support', () => {
  const adapter = createHlsPlaybackAdapter(Engine, () => false), audio = media(true), abort = new AbortController();
  const binding = adapter.bind(audio, '/playlist', 0, abort.signal, () => assert.fail());
  assert.equal(Engine.instances.at(-1).audio, audio);
  assert.equal(audio.loads, 0);
  binding.destroy();
});
