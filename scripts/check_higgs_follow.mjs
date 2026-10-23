import fs from 'node:fs';
import {chromium} from 'playwright';

const token = JSON.parse(fs.readFileSync('training-work/book1-listening-app/runtime.stdout.run.log', 'utf8').split(/\r?\n/)[0]).token;
const url = `http://127.0.0.1:1421/?book=676d6ad200230b7cfde56a63fd1d8d28&generation=64ac04b23f4e44318726e14f#port=1421&token=${token}`;
const browser = await chromium.launch({channel: 'msedge', headless: true});
try {
  const page = await browser.newPage({viewport: {width: 1250, height: 900}});
  await page.goto(url);
  await page.getByText('Book 1 · Higgs TTS 3 opening demo').first().waitFor();
  await page.locator('.chapter-item').filter({hasText: 'Введение'}).first().click();
  await page.locator('#c0004-p00002 .passage-actions button').first().click();
  await page.locator('.dock-play[aria-label="Пауза"]').waitFor({timeout: 15000});
  const range = page.locator('.dock-timeline input[type="range"]');
  await range.press('End');
  await page.waitForTimeout(1000);
  const state = await page.evaluate(() => {
    const root = document.querySelector('.reading-scroll');
    const speaking = document.querySelector('.source-block.speaking');
    const bounds = root?.getBoundingClientRect();
    const active = speaking?.getBoundingClientRect();
    return {chapter: document.querySelector('.chapter-title')?.textContent,
      speaking: speaking?.id, scrollTop: root?.scrollTop,
      visible: Boolean(bounds && active && active.top < bounds.bottom && active.bottom > bounds.top)};
  });
  console.log(JSON.stringify(state));
  if (!state.speaking || !state.visible) process.exitCode = 1;
} finally {
  await browser.close();
}
