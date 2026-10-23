import fs from 'node:fs';
import { chromium } from 'playwright';

const root = new URL('../training-work/book1-listening-app/', import.meta.url);
const token = JSON.parse(fs.readFileSync(new URL('runtime.stdout.run.log', root), 'utf8').split(/\r?\n/)[0]).token;
const book = '676d6ad200230b7cfde56a63fd1d8d28';
const generation = '64ac04b23f4e44318726e14f';
const url = `http://127.0.0.1:1421/?book=${book}&generation=${generation}#port=1421&token=${token}`;
const browser = await chromium.launch({channel: 'msedge', headless: true});
try {
  const page = await browser.newPage();
  await page.goto(url);
  await page.getByText('Book 1 · Higgs TTS 3 opening demo').first().waitFor({timeout: 20000});
  const before = await page.locator('.dock-timeline-heading').innerText();
  const play = page.locator('.dock-play');
  console.log(JSON.stringify({playLabel: await play.getAttribute('aria-label'), disabled: await play.isDisabled()}));
  await play.click();
  await page.waitForTimeout(2500);
  const after = await page.locator('.dock-timeline-heading').innerText();
  const playbackLabel = await play.getAttribute('aria-label');
  const clock = await page.locator('.dock-timeline').innerText();
  await page.screenshot({path: new URL('../training-work/higgs-tts-3-pilot/ui-check.png', import.meta.url).pathname.replace(/^\//, ''), fullPage: false});
  console.log(JSON.stringify({before, after, playbackLabel, clock}));
  if (playbackLabel !== 'Пауза' || !after.includes('Введение')) process.exitCode = 1;
} finally {
  await browser.close();
}
