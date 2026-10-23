import {test, expect} from '../fixtures';
import {closeDialog, EPUB_TITLE, importBook, player, readyGeneration, selectChapter, startGeneration} from '../helpers';

test('phase 3: narration stops for cast review and renders only after the cast is approved', async ({app, runtime, bookFiles}) => {
  const book = await importBook(app, runtime, bookFiles.epub, EPUB_TITLE);
  await app.getByRole('button', {name: 'Settings', exact: true}).click();
  const settings = app.getByRole('dialog', {name: 'Settings', exact: true});
  const stop = settings.getByRole('switch', {name: 'Stop for cast review before narration', exact: true});
  if (await stop.getAttribute('aria-checked') !== 'true') await stop.click();
  await expect(stop).toHaveAttribute('aria-checked', 'true');
  await expect.poll(async () => (await runtime.api('settings', {})).cast_review).toBe(true);
  await closeDialog(app);
  const chapter = book.chapters.at(-1);
  if (!chapter) throw new Error('Fixture has no chapters.');
  await selectChapter(app, chapter);
  const started = await startGeneration(app, runtime, book);
  await expect.poll(async () => (await runtime.api('generation', {id: started.id})).status, {timeout: 30_000}).toBe('review');
  expect((await runtime.api('generation', {id: started.id})).segments.some(segment => segment.status === 'ready')).toBe(false);
  const dock = player(app);
  await expect(dock.getByText('Voices assigned. Review the cast, then start narration.', {exact: true})).toBeVisible({timeout: 20_000});
  await dock.getByRole('button', {name: 'Review cast', exact: true}).click();
  const panel = app.getByRole('dialog', {name: 'Characters & narration', exact: true});
  await expect(panel.getByText('Narrator', {exact: true}).first()).toBeVisible();
  await expect(panel.getByText(/will be synthesized\.$/)).toBeVisible();
  await panel.getByRole('button', {name: 'Start narration', exact: true}).click();
  await expect(panel).toBeHidden();
  await readyGeneration(app, runtime, started.id);
});
