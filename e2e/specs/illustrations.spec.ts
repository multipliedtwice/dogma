import {test, expect} from '../fixtures';
import {chooseFile, closeDialog, EPUB_TITLE, importBook, openSavedBook, passage, selectChapter} from '../helpers';
import type {Page} from '@playwright/test';

async function openParagraphImages(page: Page, sourceId: string) {
  const row = passage(page, sourceId);
  await row.hover();
  await row.getByRole('button', {name: /^Illustrate paragraph: /}).click();
  return page.getByRole('dialog', {name: 'Paragraph illustrations', exact: true});
}

test('phase 4: image attachment renders inline and jumps back from illustrated reading', async ({app, runtime, bookFiles}) => {
  const book = await importBook(app, runtime, bookFiles.epub, EPUB_TITLE);
  await selectChapter(app, book.chapters[1]);
  const block = book.chapters[1].blocks.find(item => item.kind === 'paragraph');
  if (!block) throw new Error('Fixture has no paragraph for an image.');
  const dialog = await openParagraphImages(app, block.id);
  await chooseFile(app, dialog.getByText('Attach an image to this paragraph', {exact: true}), bookFiles.image);
  await expect(dialog.getByRole('status')).toHaveText('Image saved beside this paragraph.');
  const visuals = await runtime.api('visuals', {book_id: book.id});
  expect(visuals).toHaveLength(1);
  expect(visuals[0].source_id).toBe(block.id);
  await dialog.getByRole('button', {name: 'Show this paragraph in text', exact: true}).click();
  const row = passage(app, block.id);
  await expect(dialog).toBeHidden();
  await expect(row).toBeFocused();
  await expect(row.locator('img')).toBeVisible();
  await expect.poll(() => row.locator('img').evaluate(image => image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0)).toBe(true);
  await app.getByRole('button', {name: 'Illustrated', exact: true}).click();
  const stage = app.getByRole('complementary', {name: 'Paragraph illustration', exact: true});
  await expect(stage.locator('img').first()).toBeVisible();
  await stage.getByRole('button', {name: 'Show this paragraph in text', exact: true}).click();
  await expect(app.getByRole('button', {name: 'Illustrated', exact: true})).toHaveAttribute('aria-pressed', 'true');
  await expect(stage).toBeVisible();
  await expect(row).toBeFocused();
  await row.getByRole('button', {name: 'Show in text', exact: true}).click();
  await expect(row).toBeFocused();
  await app.reload();
  await openSavedBook(app, EPUB_TITLE);
  await expect(row.locator('img')).toBeVisible();
  expect(await runtime.api('visuals', {book_id: book.id})).toEqual(visuals);
});

test('phase 4: Codex consent, held progress, explicit failure retry and saved success', async ({app, runtime, bookFiles}) => {
  const book = await importBook(app, runtime, bookFiles.epub, EPUB_TITLE);
  await selectChapter(app, book.chapters[1]);
  const paragraphs = book.chapters[1].blocks.filter(block => block.kind === 'paragraph');
  expect(paragraphs).toHaveLength(3);
  let dialog = await openParagraphImages(app, paragraphs[0].id);
  await expect(dialog.getByRole('button', {name: 'Prepare paragraph illustrations', exact: true})).toBeDisabled();
  await dialog.getByRole('button', {name: 'Enable optional online illustrations in Settings', exact: true}).click();
  await app.getByRole('dialog', {name: 'Settings', exact: true}).getByRole('switch', {name: 'Allow explicit image-generation requests', exact: true}).check();
  await expect.poll(async () => (await runtime.api('settings', {})).online_images).toBe(true);
  await closeDialog(app);
  dialog = await openParagraphImages(app, paragraphs[0].id);
  await dialog.getByRole('combobox', {name: 'Paragraphs', exact: true}).selectOption('chapter');
  await dialog.getByRole('combobox', {name: 'Style', exact: true}).selectOption('ink');
  const prepare = dialog.getByRole('button', {name: 'Prepare paragraph illustrations', exact: true});
  await expect(prepare).toBeDisabled();
  expect(await runtime.api('illustration_history', {book_id: book.id})).toEqual([]);
  await runtime.control({op: 'hold-illustration', after: 1});
  await dialog.getByRole('checkbox', {name: /^Allow the selected paragraphs/}).check();
  await prepare.click();
  await runtime.control({op: 'wait-illustration-held'});
  await expect(dialog.getByText('Running · 1/3', {exact: true})).toBeVisible();
  const history = await runtime.api('illustration_history', {book_id: book.id});
  expect(history).toHaveLength(1);
  const run = history[0];
  expect(run.items.filter(item => item.status === 'ready')).toHaveLength(1);
  const preserved = run.items.find(item => item.status === 'ready')?.visual_id;
  expect(preserved).toBeTruthy();
  await runtime.control({op: 'fail-next-illustration'});
  await runtime.control({op: 'release-illustration'});
  await expect.poll(async () => (await runtime.api('illustration_status', {id: run.id})).status).toBe('failed');
  await expect(dialog.getByText('Controlled illustration failure. Retry this saved run explicitly.', {exact: true})).toBeVisible();
  const retry = dialog.getByRole('button', {name: 'Retry unfinished images', exact: true});
  await expect(retry).toBeDisabled();
  await dialog.getByRole('checkbox', {name: /^Allow the 2 unfinished paragraphs from this saved ink run/}).check();
  await retry.click();
  await expect(dialog.getByText('Ready · 3/3', {exact: true})).toBeVisible({timeout: 20_000});
  const completed = await runtime.api('illustration_history', {book_id: book.id});
  expect(completed).toHaveLength(1);
  expect(completed[0]).toMatchObject({id: run.id, status: 'ready', error: ''});
  expect(completed[0].items.every(item => item.status === 'ready')).toBe(true);
  expect(completed[0].items.some(item => item.visual_id === preserved)).toBe(true);
  const visuals = await runtime.api('visuals', {book_id: book.id});
  expect(visuals).toHaveLength(3);
  expect(visuals.every(visual => visual.kind === 'generated-codex')).toBe(true);
  expect(visuals.map(visual => visual.source_id).sort()).toEqual(paragraphs.map(block => block.id).sort());
  await dialog.getByRole('button', {name: 'Show this paragraph in text', exact: true}).click();
  for (const block of paragraphs) {
    await passage(app, block.id).locator('figure').scrollIntoViewIfNeeded();
    await expect(passage(app, block.id).getByRole('img', {name: 'Codex illustration · Lantern evening', exact: true})).toBeVisible();
  }
});
