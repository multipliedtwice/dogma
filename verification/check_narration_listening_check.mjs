// Isolated browser test of the blind listening-check page. Every request is
// answered by Playwright routes, so no server, label file or audio is touched.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {chromium} from 'playwright';

const html = fs.readFileSync(path.resolve('scripts/narration_listening_check.html'), 'utf8');
const BASE = 'http://listening.test/check/';
const manifest = {schema: 1, experiment_id: 'fixture', answers: ['calm', 'neutral', 'uncertain', 'neither'],
  items: [1, 2, 3].map(item => ({item, clip_id: `clip-${item}`, audio: `/clips/${item}.wav`, text: `Предложение ${item}.`}))};

async function open({delay = 0} = {}) {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  const page = await browser.newPage();
  const answered = new Set();
  const posts = [];
  let release = () => {};
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/check/') return route.fulfill({contentType: 'text/html', body: html});
    if (url.pathname === '/check/manifest.json') return route.fulfill({json: manifest});
    if (url.pathname === '/check/progress') return route.fulfill({json: {experiment_id: 'fixture', answered: [...answered].sort()}});
    if (url.pathname === '/check/answer') {
      const body = route.request().postDataJSON();
      posts.push(body);
      if (delay) await new Promise(resolve => { release = resolve; setTimeout(resolve, delay); });
      answered.add(body.item);
      return route.fulfill({json: {saved: true, experiment_id: 'fixture', answered: [...answered].sort()}});
    }
    return route.fulfill({status: 204, body: ''});
  });
  await page.goto(BASE);
  await page.locator('.count').waitFor();
  return {browser, page, posts, answered, release: () => release()};
}

const count = page => page.locator('.count').textContent();
const done = page => page.getByText(/All 3 clips answered/).count();

test('moving through unanswered clips never claims completion', async () => {
  const {browser, page, posts} = await open();
  try {
    for (let n = 0; n < 7; n++) {
      await page.locator('#next').click();
      assert.equal(await done(page), 0, 'no completion without saved answers');
      assert.match(await count(page), /3 left to answer/);
    }
    for (let n = 0; n < 4; n++) await page.locator('#back').click();
    assert.equal(await done(page), 0);
    assert.equal(posts.length, 0);
  } finally { await browser.close(); }
});

test('navigation is locked while an answer saves, so no clip is skipped', async () => {
  const {browser, page, posts} = await open({delay: 400});
  try {
    assert.match(await count(page), /^Clip 1 of 3/);
    await page.getByRole('button', {name: 'Calm'}).click();
    assert.equal(await page.locator('#next').isDisabled(), true);
    await page.evaluate(() => { document.getElementById('next').click(); document.getElementById('back').click(); });
    await page.waitForFunction(() => /Clip 2 of 3/.test(document.querySelector('.count')?.textContent ?? ''));
    assert.match(await count(page), /^Clip 2 of 3 · 2 left/);
    await page.getByRole('button', {name: 'Normal'}).click();
    await page.waitForFunction(() => /Clip 3 of 3/.test(document.querySelector('.count')?.textContent ?? ''));
    await page.getByRole('button', {name: 'Uncertain'}).click();
    await page.getByText(/All 3 clips answered/).waitFor();
    assert.deepEqual(posts.map(post => [post.item, post.answer]), [[1, 'calm'], [2, 'neutral'], [3, 'uncertain']]);
  } finally { await browser.close(); }
});

test('answering out of order wraps back to the remaining clip before completing', async () => {
  const {browser, page, posts} = await open();
  try {
    await page.locator('#next').click();
    assert.match(await count(page), /^Clip 2 of 3/);
    await page.getByRole('button', {name: 'Neither'}).click();
    await page.waitForFunction(() => /Clip 3 of 3/.test(document.querySelector('.count')?.textContent ?? ''));
    await page.getByRole('button', {name: 'Calm'}).click();
    await page.waitForFunction(() => /Clip 1 of 3 · 1 left/.test(document.querySelector('.count')?.textContent ?? ''));
    assert.equal(await done(page), 0);
    await page.getByRole('button', {name: 'Normal'}).click();
    await page.getByText(/All 3 clips answered/).waitFor();
    assert.deepEqual(posts.map(post => post.item), [2, 3, 1]);
  } finally { await browser.close(); }
});
