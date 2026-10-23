// Isolated browser test of the mood rulebook page. Playwright routes answer
// every request, so no server, label file or audio is touched.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {chromium} from 'playwright';

const html = fs.readFileSync(path.resolve('scripts/mood_rulebook.html'), 'utf8');
const example = (id, extra = {}) => ({id, book: 'Book 2', kind: 'clip', voice: 'narrator_baseline', text: `Пример ${id}.`, audio: `/clips/${id}.wav`, ...extra});
const entry = (mood, name, rival, rivalName, examples) => ({mood, name, group: 'Baseline', meaning: `Meaning of ${name}.`, rival, rival_name: rivalName,
  boundary: `${name} versus ${rivalName}.`, count: examples.length, books: {'reality-benders-2-full': examples.length}, split_parts: 0,
  confidence: 'Tentative', examples, speakers: 1, measured: {clips: 0, tendencies: []},
  sections: [{speaker: 'Narrator · narrator_baseline · Book 2', examples,
              baseline: {mood: rival, example: example(`${mood}-base`)}, rival: null}]});
const book = {version: 'v1', entries: [entry('calm', 'Calm', 'neutral', 'Normal', [example('a'), example('b')]),
                                       entry('neutral', 'Normal', 'calm', 'Calm', [example('c')])]};

async function open({delay = 0} = {}) {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  const page = await browser.newPage();
  const saved = {}, posts = [];
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/rulebook/') return route.fulfill({contentType: 'text/html', body: html});
    if (url.pathname === '/rulebook/rulebook.json') return route.fulfill({json: book});
    if (url.pathname === '/rulebook/progress') return route.fulfill({json: {version: 'v1', feedback: saved}});
    if (url.pathname === '/rulebook/feedback') {
      const body = route.request().postDataJSON(); posts.push(body);
      if (delay) await new Promise(resolve => setTimeout(resolve, delay));
      saved[body.mood] = body;
      return route.fulfill({json: {saved: true, version: 'v1', feedback: saved}});
    }
    return route.fulfill({status: 204, body: ''});
  });
  await page.goto('http://rulebook.test/rulebook/');
  await page.getByText('Mood rulebook').waitFor();
  return {browser, page, posts};
}

test('feedback needs a verdict, locks the form while saving and shows on the list', async () => {
  const {browser, page, posts} = await open({delay: 300});
  try {
    assert.match(await page.locator('main').textContent(), /Reviewed 0 of 2/);
    await page.getByRole('button', {name: /Calm/}).first().click();
    await page.getByText('Meaning of Calm.').waitFor();
    assert.equal(await page.locator('.example').count(), 3, 'two own examples plus the same performer baseline');
    assert.match(await page.locator('main').textContent(), /Same performer, baseline: Normal/);
    await page.getByRole('button', {name: 'Save'}).click();
    await page.getByText(/Choose Approve, Revise or Not sure/).waitFor();
    assert.equal(posts.length, 0);
    await page.getByLabel('Revise').check();
    await page.locator('#distinction').fill('Тише и медленнее.');
    await page.locator('[data-flag="b"]').check();
    await page.getByRole('button', {name: 'Save'}).click();
    assert.equal(await page.locator('#next').isDisabled(), true);
    await page.evaluate(() => document.getElementById('next').click());
    await page.getByText('Saved.').waitFor();
    assert.match(await page.locator('h1').textContent(), /^Calm$/, 'a click during the save did not move away');
    assert.deepEqual(posts.map(post => [post.mood, post.verdict, post.distinction, post.flagged_examples]),
      [['calm', 'revise', 'Тише и медленнее.', ['b']]]);
    await page.getByRole('button', {name: '← All moods'}).click();
    assert.match(await page.locator('main').textContent(), /Reviewed 1 of 2/);
    assert.equal(await page.locator('.chip.revise').count(), 1);
  } finally { await browser.close(); }
});

test('reopening an entry restores the saved feedback', async () => {
  const {browser, page} = await open();
  try {
    await page.getByRole('button', {name: /Normal/}).first().click();
    await page.getByLabel('Approve').check();
    await page.locator('#note').fill('Обычная речь.');
    await page.getByRole('button', {name: 'Save'}).click();
    await page.getByText('Saved.').waitFor();
    await page.locator('#next').click();
    await page.locator('#previous').click();
    assert.equal(await page.getByLabel('Approve').isChecked(), true);
    assert.equal(await page.locator('#note').inputValue(), 'Обычная речь.');
  } finally { await browser.close(); }
});
