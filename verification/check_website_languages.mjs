/** Execute the shipped player against synthetic DOM/media/data; no network. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const script = await readFile(new URL('../website/script.js', import.meta.url), 'utf8');

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = new Map();
    this.textContent = ''; this.hidden = false; this.dataset = {}; this.disabled = false;
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, listener) {
    const entries = this.listeners.get(name) || []; entries.push(listener); this.listeners.set(name, entries);
  }
  dispatch(name) { for (const listener of this.listeners.get(name) || []) listener({ target: this }); }
}

const english = { id: 'en', status: 'ready', title: 'English story', author: 'English author', duration: 72,
  audio: 'demos/en/english.mp3', captions: 'demos/en/english.json', source: 'https://example.test/english-story' };
const russian = { id: 'ru', status: 'preparing', title: 'Хамелеон', author: 'Антон Чехов',
  source: 'https://example.test/russian-story' };
const readyRussian = { ...russian, status: 'ready', duration: 64, audio: 'demos/ru/russian.mp3', captions: 'demos/ru/russian.json' };
const captions = language => [{ id: 'u0000', source_id: 'p1', text: language === 'ru' ? 'Русский текст для проверки.' : 'English fixture text.', start: 8, end: 12 }];

async function run(language, catalog) {
  const selectors = ['#story-audio', '#story-reader', '#follow-audio', '#playback-note', '#reader-hint',
    '#demo-status', '#audio-unavailable', '#player-region', '#demo-title', '.demo-author', '#demo-duration',
    '#download-audio', '#story-source', '#playback-rate', '.follow-toggle'];
  const elements = Object.fromEntries(selectors.map(selector => [selector, new Element()]));
  const audio = elements['#story-audio'];
  Object.assign(audio, { currentTime: 0, playbackRate: 1, duration: 64, paused: true, src: '' });
  elements['#player-region'].hidden = true;
  elements['#story-reader'].lang = language;
  elements['#follow-audio'].checked = false;
  const readyText = new Element(); readyText.dataset.readyText = 'Ready copy';
  const pendingOnly = new Element();
  const requests = [], saved = new Map([['dogma-listen-en', 'protected English position']]);
  const document = {
    documentElement: { lang: language }, currentScript: { src: 'https://site.test/dogma/script.js?v=language-demo-v1' },
    baseURI: `https://site.test/dogma/${language === 'ru' ? 'ru/' : ''}`, hidden: false,
    querySelector: selector => elements[selector] ?? null,
    querySelectorAll: selector => selector === '[data-ready-text]' ? [readyText] : selector === '[data-pending-only]' ? [pendingOnly] : [],
    createElement: tag => new Element(tag), createTextNode: textContent => ({ textContent }), addEventListener() {}
  };
  const context = vm.createContext({
    document, URL, navigator: {}, window: { addEventListener() {} }, matchMedia: () => ({ matches: true }),
    localStorage: { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value) },
    fetch: async value => {
      const url = String(value); requests.push(url);
      let payload;
      if (url === 'https://site.test/dogma/demos/catalog.json') payload = catalog;
      else if (url === 'https://site.test/dogma/demos/en/english.json') payload = captions('en');
      else if (url === 'https://site.test/dogma/demos/ru/russian.json') payload = captions('ru');
      else throw new Error(`Unexpected asset fetch: ${url}`);
      return { ok: true, json: async () => JSON.parse(JSON.stringify(payload)) };
    }
  });
  vm.runInContext(script, context, { filename: 'website/script.js' });
  for (let turn = 0; turn < 3; turn++) await new Promise(resolve => setImmediate(resolve));
  return { elements, audio, requests, saved, readyText, pendingOnly };
}

test('pending Russian page selects Russian metadata and never loads English media or text', async () => {
  const page = await run('ru', [english, russian]);
  assert.deepEqual(page.requests, ['https://site.test/dogma/demos/catalog.json']);
  assert.equal(page.audio.src, ''); assert.equal(page.elements['#player-region'].hidden, true);
  assert.equal(page.elements['.follow-toggle'].hidden, true);
  assert.equal(page.elements['#demo-title'].textContent, 'Хамелеон');
  assert.equal(page.elements['.demo-author'].textContent, 'Антон Чехов');
  assert.equal(page.elements['#story-source'].href, russian.source);
  assert.match(page.elements['#audio-unavailable'].textContent, /Русская запись готовится/);
  assert.equal(page.saved.size, 1);
});

test('missing Russian entry fails closed instead of falling back to the ready English entry', async () => {
  const page = await run('ru', [english]);
  assert.equal(page.requests.length, 1); assert.equal(page.audio.src, '');
  assert.equal(page.elements['#player-region'].hidden, true);
  assert.match(page.elements['#audio-unavailable'].textContent, /Русская/);
});

test('future ready Russian entry loads only Russian assets and keeps separate listening storage', async () => {
  const page = await run('ru', [english, readyRussian]);
  assert.deepEqual(page.requests, ['https://site.test/dogma/demos/catalog.json', 'https://site.test/dogma/demos/ru/russian.json']);
  assert.equal(page.audio.src, 'https://site.test/dogma/demos/ru/russian.mp3');
  assert.equal(page.elements['#story-reader'].lang, 'ru');
  assert.equal(page.elements['#player-region'].hidden, false);
  assert.equal(page.elements['.follow-toggle'].hidden, false);
  assert.equal(page.readyText.textContent, 'Ready copy'); assert.equal(page.pendingOnly.hidden, true);
  page.audio.currentTime = 8; page.audio.dispatch('seeked');
  assert.equal(page.saved.get('dogma-listen-en'), 'protected English position');
  const resume = JSON.parse(page.saved.get('dogma-listen-ru'));
  assert.equal(resume.unit_id, 'u0000'); assert.equal(resume.source, russian.source);
});

test('ready Russian entry cannot point its media at English-language assets', async () => {
  for (const audio of [english.audio, 'demos/ru/../en/english.mp3']) {
    const page = await run('ru', [english, { ...readyRussian, audio, captions: english.captions }]);
    assert.equal(page.requests.length, 1); assert.equal(page.audio.src, '');
    assert.equal(page.elements['#player-region'].hidden, true);
  }
});

test('English page still selects and plays the English entry when Russian is also ready', async () => {
  const page = await run('en', [readyRussian, english]);
  assert.equal(page.audio.src, 'https://site.test/dogma/demos/en/english.mp3');
  assert.equal(page.elements['#story-reader'].lang, 'en');
  assert.deepEqual(page.requests, ['https://site.test/dogma/demos/catalog.json', 'https://site.test/dogma/demos/en/english.json']);
});

test('static Russian page features Chekhov and has no English audio or read-along embedded', async () => {
  const html = await readFile(new URL('../website/ru/index.html', import.meta.url), 'utf8');
  assert.match(html, /id="demo-title" lang="ru">Хамелеон/);
  assert.match(html, /id="story-reader"[^>]*lang="ru"/);
  assert.doesNotMatch(html.match(/<audio\b[^>]*>/)?.[0] ?? '', /\bsrc=/);
  assert.doesNotMatch(html, /One dollar and eighty-seven cents/);
  assert.match(html, /\.\.\/#listen[^>]*>Демо на английском/);
  assert.match(html, /script\.js\?v=language-demo-v1/);
});
