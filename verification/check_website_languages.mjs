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

const english = { id: 'en', language: 'English', status: 'ready', title: 'English story', author: 'English author', duration: 72,
  audio: 'demos/en/english.mp3', captions: 'demos/en/english.json', source: 'https://example.test/english-story' };
const russian = { id: 'ru', language: 'Russian', status: 'preparing', title: 'Хамелеон', author: 'Антон Чехов',
  source: 'https://example.test/russian-story' };
const readyRussian = { ...russian, status: 'ready', duration: 64, audio: 'demos/ru/russian.mp3', captions: 'demos/ru/russian.json' };
const captions = language => [{ id: 'u0000', source_id: 'p1', text: language === 'ru' ? 'Русский текст для проверки.' : 'English fixture text.', start: 8, end: 12 }];

async function run(language, catalog, options = {}) {
  const selectors = ['#story-audio', '#story-reader', '#follow-audio', '#playback-note', '#reader-hint',
    '#demo-status', '#audio-unavailable', '#player-region', '#demo-title', '.demo-author', '#demo-duration',
    '#download-audio', '#story-source', '#playback-rate', '.follow-toggle', '#demo-select', '#demo-picker', '.demo-description', '#demo-kind'];
  const elements = Object.fromEntries(selectors.map(selector => [selector, new Element()]));
  const audio = elements['#story-audio'];
  Object.assign(audio, { currentTime: 0, playbackRate: 1, duration: 64, paused: true, src: '' });
  audio.pause = () => { audio.paused = true; audio.dispatch('pause'); };
  audio.load = () => {};
  const remove = audio.removeAttribute.bind(audio);
  audio.removeAttribute = name => { remove(name); if (name === 'src') audio.src = ''; };
  elements['#player-region'].hidden = true;
  elements['#story-reader'].lang = language;
  elements['#follow-audio'].checked = false;
  const readyText = new Element(); readyText.dataset.readyText = 'Ready copy';
  const pendingOnly = new Element();
  const requests = [], urls = [], saved = new Map([['dogma-listen-en', 'protected English position'], ...(options.saved || [])]);
  const flush = async () => { for (let turn = 0; turn < 4; turn++) await new Promise(resolve => setImmediate(resolve)); };
  const document = {
    documentElement: { lang: language }, currentScript: { src: 'https://site.test/dogma/script.js?v=language-demo-v1' },
    baseURI: `https://site.test/dogma/${language === 'ru' ? 'ru/' : ''}`, hidden: false,
    querySelector: selector => elements[selector] ?? null,
    querySelectorAll: selector => selector === '[data-ready-text]' ? [readyText] : selector === '[data-pending-only]' ? [pendingOnly] : [],
    createElement: tag => new Element(tag), createTextNode: textContent => ({ textContent }), addEventListener() {}
  };
  const context = vm.createContext({
    document, URL, navigator: {}, window: { addEventListener() {}, location: { href: options.url || `https://site.test/dogma/${language === 'ru' ? 'ru/' : ''}` }, history: { replaceState(_state, _title, url) { urls.push(String(url)); } } }, matchMedia: () => ({ matches: true }),
    localStorage: { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value) },
    fetch: async value => {
      const url = String(value); requests.push(url);
      let payload;
      if (options.fetch) {
        const result = options.fetch(url);
        if (result !== undefined) return { ok: true, json: async () => await result };
      }
      if (url === 'https://site.test/dogma/demos/catalog.json') payload = catalog;
      else if (url === 'https://site.test/dogma/demos/en/english.json') payload = captions('en');
      else if (url === 'https://site.test/dogma/demos/ru/russian.json') payload = captions('ru');
      else throw new Error(`Unexpected asset fetch: ${url}`);
      return { ok: true, json: async () => JSON.parse(JSON.stringify(payload)) };
    }
  });
  vm.runInContext(script, context, { filename: 'website/script.js' });
  await flush();
  audio.dispatch('loadedmetadata');
  return { elements, audio, requests, saved, urls, readyText, pendingOnly, flush };
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

test('static Russian page has no English audio or read-along embedded', async () => {
  const html = await readFile(new URL('../website/ru/index.html', import.meta.url), 'utf8');
  assert.match(html, /id="story-reader"[^>]*lang="ru"/);
  assert.doesNotMatch(html.match(/<audio\b[^>]*>/)?.[0] ?? '', /\bsrc=/);
  assert.doesNotMatch(html, /One dollar and eighty-seven cents/);
  assert.match(html, /\.\.\/#listen[^>]*>Демо на английском/);
  assert.match(html, /script\.js\?v=multi-demo-v2/);
});

const extraBook = (id, language, title) => ({ id, language, title, author: 'Fixture author', status: 'ready',
  duration: 360, extent: 'excerpt', description: `Description for ${title}`,
  audio: `demos/${id.slice(0, 2)}/${id}.mp3`, captions: `demos/${id.slice(0, 2)}/${id}.json`, source: `https://example.test/${id}` });
const additions = [extraBook('ru-queen-spades', 'Russian', 'Пиковая дама'), extraBook('ru-malefactor', 'Russian', 'Злоумышленник'),
  extraBook('en-open-window', 'English', 'The Open Window'), extraBook('en-alice-tea-party', 'English', 'A Mad Tea-Party')];
const extraFetch = url => {
  const row = additions.find(row => url.endsWith(row.captions));
  return row ? [{ id: 'u0000', source_id: row.id, text: row.title, start: 8, end: 12 }] : undefined;
};
const choose = async (page, id) => {
  page.elements['#demo-select'].value = id; page.elements['#demo-select'].dispatch('change');
  await page.flush(); page.audio.dispatch('loadedmetadata');
};

test('six demos offer only the three local-language books and retain legacy defaults', async () => {
  const catalog = [...additions, readyRussian, english];
  for (const language of ['en', 'ru']) {
    const page = await run(language, catalog, { fetch: extraFetch });
    assert.equal(page.elements['#demo-picker'].hidden, false);
    assert.equal(page.elements['#demo-select'].children.length, 3);
    assert(page.elements['#demo-select'].children.every(row => row.value.startsWith(language)));
    assert.equal(page.elements['#demo-select'].value, language);
    assert.equal(page.audio.src.endsWith(language === 'en' ? '/english.mp3' : '/russian.mp3'), true);
  }
});

test('switching books pauses old playback and restores separate source-bound positions', async () => {
  const page = await run('ru', [english, readyRussian, ...additions], { fetch: extraFetch });
  page.audio.currentTime = 9; page.audio.paused = false; page.audio.dispatch('seeked');
  await choose(page, 'ru-queen-spades');
  assert.equal(page.audio.paused, true); assert.equal(page.audio.currentTime, 0);
  assert.equal(page.elements['#demo-title'].textContent, 'Пиковая дама');
  assert.equal(page.elements['.demo-description'].textContent, 'Description for Пиковая дама');
  assert.equal(page.elements['#story-source'].href, additions[0].source);
  assert.match(page.elements['#demo-duration'].textContent, /Отрывок/);
  page.audio.currentTime = 10; page.audio.dispatch('seeked');
  await choose(page, 'ru'); assert.equal(page.audio.currentTime, 9);
  await choose(page, 'ru-queen-spades'); assert.equal(page.audio.currentTime, 10);
  assert.equal(page.saved.get('dogma-listen-en'), 'protected English position');
  assert.equal(page.audio.listeners.get('timeupdate').length, 1);
});

test('late captions from the previous book cannot overwrite the current book', async () => {
  let release;
  const slow = new Promise(resolve => { release = resolve; });
  const page = await run('ru', [english, readyRussian, ...additions], { fetch: url =>
    url.endsWith(additions[0].captions) ? slow : extraFetch(url) });
  page.elements['#demo-select'].value = additions[0].id; page.elements['#demo-select'].dispatch('change');
  await page.flush();
  await choose(page, additions[1].id);
  const before = page.elements['#story-reader'].children;
  release([{ id: 'u0000', text: 'Stale text', start: 8, end: 12 }]); await page.flush();
  assert.equal(page.elements['#demo-title'].textContent, 'Злоумышленник');
  assert.equal(page.audio.src.endsWith('/ru-malefactor.mp3'), true);
  assert.equal(page.elements['#story-reader'].children, before);
});

test('switching to invalid cross-language media never keeps the previous audio or reader', async () => {
  const invalid = { ...additions[0], audio: english.audio };
  const page = await run('ru', [english, readyRussian, invalid], { fetch: extraFetch });
  await choose(page, invalid.id);
  assert.equal(page.audio.src, ''); assert.equal(page.elements['#player-region'].hidden, true);
  assert.equal(page.elements['#story-reader'].children.length, 1);
  assert.equal(page.requests.filter(url => url.includes('/demos/en/')).length, 0);
});

test('duplicate IDs and conflicting locale declarations fail closed', async () => {
  for (const catalog of [[english, readyRussian, readyRussian], [english, { ...readyRussian, locale: 'en' }],
    [english, { ...readyRussian, id: 'en-pretending-russian' }]]) {
    const page = await run('ru', catalog);
    assert.equal(page.audio.src, ''); assert.equal(page.requests.length, 1);
  }
});

test('a selected-book URL stays within the page language', async () => {
  const catalog = [english, readyRussian, ...additions];
  const page = await run('ru', catalog, { fetch: extraFetch, url: 'https://site.test/dogma/ru/?demo=ru-queen-spades' });
  assert.equal(page.audio.src.endsWith('/ru-queen-spades.mp3'), true);
  const cross = await run('ru', catalog, { fetch: extraFetch, url: 'https://site.test/dogma/ru/?demo=en-open-window' });
  assert.equal(cross.audio.src.endsWith('/russian.mp3'), true);
});

test('ready featured demos become locale defaults while explicit legacy links win', async () => {
  const catalog = [english, readyRussian, ...additions.map(row => ({ ...row,
    featured: ['en-open-window', 'ru-malefactor'].includes(row.id) }))];
  for (const [locale, identifier] of [['en', 'en-open-window'], ['ru', 'ru-malefactor']]) {
    const page = await run(locale, catalog, { fetch: extraFetch });
    assert.equal(page.elements['#demo-select'].value, identifier);
    assert.equal(page.elements['#demo-select'].children[0].value, identifier);
    await choose(page, locale);
    assert.equal(new URL(page.urls.at(-1)).searchParams.get('demo'), locale);
    assert.equal(page.elements['.demo-description'].textContent, locale === 'en' ?
      'Listen to the original text and follow along as it is read.' : 'Послушайте оригинальный текст и следите за чтением.');
    const legacy = await run(locale, catalog, { fetch: extraFetch,
      url: `https://site.test/dogma/${locale === 'ru' ? 'ru/' : ''}?demo=${locale}` });
    assert.equal(legacy.elements['#demo-select'].value, locale);
  }
  const explicit = await run('ru', catalog, { fetch: extraFetch,
    url: 'https://site.test/dogma/ru/?demo=ru-queen-spades' });
  assert.equal(explicit.elements['#demo-select'].value, 'ru-queen-spades');
});

test('duplicate, pending or nonboolean featured selections fail closed', async () => {
  const featured = { ...additions[0], featured: true };
  for (const rows of [[featured, { ...additions[1], featured: true }],
    [{ ...featured, status: 'preparing' }], [{ ...featured, featured: 1 }]]) {
    const page = await run('ru', [english, readyRussian, ...rows], { fetch: extraFetch });
    assert.equal(page.audio.src, ''); assert.equal(page.requests.length, 1);
  }
});

test('both static pages provide an accessible book selector and retain the existing theme switch', async () => {
  const catalog = JSON.parse(await readFile(new URL('../website/demos/catalog.json', import.meta.url), 'utf8'));
  for (const name of ['index.html', 'ru/index.html']) {
    const html = await readFile(new URL(`../website/${name}`, import.meta.url), 'utf8');
    assert.match(html, /label for="demo-select"/); assert.match(html, /id="demo-select" aria-controls="player-region story-reader"/);
    assert.match(html, /id="demo-kind"/);
    const chooser = html.match(/<label for="demo-select">([^<]+)<\/label>/)?.[1];
    assert.equal(chooser, name.startsWith('ru/') ? 'Выберите произведение' : 'Choose a story');
    assert.doesNotMatch(chooser, /\?{2,}|\uFFFD/);
    assert.match(html, /class="theme-toggle"/);
    const locale = name.startsWith('ru/') ? 'ru' : 'en';
    const books = catalog.filter(row => row.language === (locale === 'ru' ? 'Russian' : 'English'));
    const expected = books.find(row => row.featured === true && row.status === 'ready') || books.find(row => row.id === locale);
    assert.equal(html.match(/<h3 id="demo-title"[^>]*>([^<]+)<\/h3>/)?.[1], expected.title);
    assert(html.includes(`id="story-source" href="${expected.source}"`));
  }
});
