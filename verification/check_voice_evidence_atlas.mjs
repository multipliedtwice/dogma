import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { chromium } from 'playwright'

const folder = resolve('training-work/character-rb1/voice-evidence-atlas')
const manifestBytes = await readFile(resolve(folder, 'manifest.json'))
const manifest = JSON.parse(manifestBytes.toString('utf8'))
const expectedHash = createHash('sha256').update(manifestBytes).digest('hex')
const browser = await chromium.launch({ headless: true, channel: 'msedge',
  args: ['--autoplay-policy=no-user-gesture-required'] })
try {
  const page = await browser.newPage({ acceptDownloads: true })
  await page.goto(process.env.VOICE_ATLAS_URL || pathToFileURL(resolve(folder, 'index.html')).href)
  assert.equal(await page.locator('article').count(), manifest.cases.length)
  assert.equal(await page.locator('article:visible').count(), manifest.summary.priority_first_pass_cases)
  const playback = await page.locator('article:visible audio').first().evaluate(async audio => {
    try {
      await audio.play()
      return { duration: audio.duration,
        readyState: audio.readyState, networkState: audio.networkState,
        paused: audio.paused, error: audio.error?.message || '' }
    } catch (error) {
      return { error: String(error) }
    }
  })
  assert.ok(playback.duration > 0 && playback.readyState >= 2 && !playback.paused && !playback.error,
    `Audio failed to load and start: ${JSON.stringify(playback)}`)
  assert.equal(await page.locator('header').evaluate(el => getComputedStyle(el).position), 'static')
  await page.evaluate(() => window.scrollTo(0, 600))
  assert.ok(await page.evaluate(() => window.scrollY > 500))
  assert.ok(await page.locator('header').evaluate(el => el.getBoundingClientRect().bottom < 0))
  await page.getByRole('button', { name: 'Show all cases' }).click()
  assert.equal(await page.locator('article:visible').count(), manifest.cases.length)
  await page.locator('#reviewer').fill('UI verification')
  const first = page.locator('article').first()
  await first.locator('select').selectOption('distinct')
  await first.locator('input').fill('A clearly different vocal attack in this excerpt.')
  await page.getByRole('button', { name: 'Show first pass' }).click()
  await page.getByRole('button', { name: 'Show all cases' }).click()
  assert.equal(await first.locator('select').inputValue(), 'distinct')
  assert.equal(await first.locator('input').inputValue(), 'A clearly different vocal attack in this excerpt.')
  assert.ok(await page.getByRole('button', { name: 'Submit review' }).isVisible())
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export review JSON' }).click()
  const download = await downloadPromise
  const output = JSON.parse((await readFile(await download.path())).toString('utf8'))
  assert.equal(output.manifest_sha256, expectedHash)
  assert.equal(output.reviewer, 'UI verification')
  assert.equal(output.decisions[manifest.cases[0].id].verdict, 'distinct')
  console.log(`Voice atlas UI passed: ${manifest.summary.priority_first_pass_cases} first-pass cases, ${manifest.cases.length} total`)
} finally {
  await browser.close()
}
