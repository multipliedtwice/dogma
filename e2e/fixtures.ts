import {test as base, expect, type Page} from '@playwright/test';
import {mkdtemp, rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createRuntime, type Runtime} from './runtime';

export type BookFiles = {epub: string; fb2: string; review: string; image: string; invalid: string; brokenEpub: string; brokenFb2: string};
type Fixtures = {runtime: Runtime; app: Page; bookFiles: BookFiles};

export const test = base.extend<Fixtures>({
  runtime: [async ({request, page}, use) => {
    const directory = await mkdtemp(join(tmpdir(), 'rimskify-e2e-runtime-'));
    let runtime: Runtime | undefined;
    try {
      runtime = await createRuntime(directory, request);
      await use(runtime);
    } finally {
      try {await page.close();}
      finally {
        try {await runtime?.stop();}
        finally {await rm(directory, {recursive: true, force: true, maxRetries: 3});}
      }
    }
  }, {timeout: 40_000}],
  app: async ({page, runtime}, use) => {
    await page.goto(runtime.url);
    await expect(page.getByRole('heading', {name: 'Library', exact: true})).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await use(page);
  },
  bookFiles: async ({}, use) => {
    const directory = process.env.RIMSKIFY_E2E_INPUTS;
    if (!directory) throw new Error('Generated E2E inputs missing. Run the suite with its global setup.');
    await use({epub: join(directory, 'harbor.epub'), fb2: join(directory, 'forest.fb2'), review: join(directory, 'review.epub'), image: join(directory, 'paragraph.png'), invalid: join(directory, 'invalid.txt'), brokenEpub: join(directory, 'broken.epub'), brokenFb2: join(directory, 'broken.fb2')});
  },
});

export {expect} from '@playwright/test';
