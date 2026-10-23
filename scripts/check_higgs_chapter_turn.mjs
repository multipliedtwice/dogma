import fs from 'node:fs';
import {chromium} from 'playwright';

const token = JSON.parse(fs.readFileSync('training-work/book1-listening-app/runtime.stdout.run.log', 'utf8').split(/\r?\n/)[0]).token;
const plan = JSON.parse(fs.readFileSync('training-work/higgs-tts-3-pilot/book1-half/plan.json', 'utf8'));
const url = `http://127.0.0.1:1421/?book=676d6ad200230b7cfde56a63fd1d8d28&generation=${plan.generation_id}#port=1421&token=${token}`;
const browser = await chromium.launch({channel: 'msedge', headless: true});
try {
  const page = await browser.newPage({viewport: {width: 1250, height: 900}});
  await page.goto(url);
  await page.getByText('Book 1 · Higgs first half review').first().waitFor();
  await page.locator('.chapter-item').filter({hasText: 'Annotation'}).first().click();
  await page.locator('#c0002-p00002 .passage-actions button').first().click();
  await page.locator('.dock-play[aria-label="Пауза"]').waitFor({timeout: 15000});
  const range = page.locator('.dock-timeline input[type="range"]');
  await range.evaluate(input => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, String(Number(input.max) - .7));
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
  });
  await page.waitForFunction(() => document.querySelector('.chapter-title')?.textContent?.includes('Михаил'), {timeout: 12000});
  const state = await page.evaluate(() => ({
    chapter: document.querySelector('.chapter-title')?.textContent,
    speaking: document.querySelector('.source-block.speaking')?.id,
    visible: (() => { const root = document.querySelector('.reading-scroll')?.getBoundingClientRect();
      const active = document.querySelector('.source-block.speaking')?.getBoundingClientRect();
      return Boolean(root && active && active.top < root.bottom && active.bottom > root.top); })(),
  }));
  console.log(JSON.stringify(state));
  if (!state.visible || !state.speaking?.startsWith('c0003')) process.exitCode = 1;
} finally {
  await browser.close();
}
