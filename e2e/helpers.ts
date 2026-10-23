import {expect, type Locator, type Page} from '@playwright/test';
import type {Book, Chapter, Generation} from '../src/lib/types';
import type {Runtime} from './runtime';

export const EPUB_TITLE = 'E2E Harbor Book';
export const FB2_TITLE = 'E2E Forest Book';
export const player = (page: Page) => page.getByLabel('Audiobook player', {exact: true});
export const chapterRows = (page: Page) => page.getByRole('button').filter({has: page.locator('.chapter-number')});
export const chapterRow = (page: Page, title: string) => chapterRows(page).filter({hasText: title});
export const passage = (page: Page, id: string) => page.locator(`[data-source-id="${id}"]`);

export async function closeDialog(page: Page) {
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', {name: /^(Close|Закрыть)$/}).click();
  await expect(dialog).toBeHidden();
}

export async function chooseFile(page: Page, trigger: Locator, path: string) {
  const chooser = page.waitForEvent('filechooser');
  await trigger.click();
  await (await chooser).setFiles(path);
}

export async function importBook(page: Page, runtime: Runtime, path: string, title: string): Promise<Book> {
  await chooseFile(page, page.getByRole('button', {name: 'Open book', exact: true}).first(), path);
  await expect(page.getByLabel('Book text', {exact: true})).toBeVisible();
  await expect.poll(async () => (await runtime.api('library', {})).some(book => book.title === title)).toBe(true);
  const summary = (await runtime.api('library', {})).find(book => book.title === title);
  if (!summary) throw new Error(`Imported ${title} not found.`);
  const book = await runtime.api('book', {id: summary.id});
  await expect(chapterRow(page, book.chapters[0].title)).toHaveAttribute('aria-current', 'true');
  return book;
}

export async function toLibrary(page: Page) {
  await page.getByRole('button', {name: 'Library', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Library', exact: true})).toBeVisible();
}

export async function openSavedBook(page: Page, title: string, language: 'en' | 'ru' = 'en') {
  const card = page.getByRole('article').filter({has: page.getByRole('heading', {name: title, exact: true})});
  await card.getByRole('button').filter({has: page.getByRole('heading', {name: title, exact: true})}).click();
  await expect(page.getByLabel(language === 'ru' ? 'Текст книги' : 'Book text', {exact: true})).toBeVisible();
}

export async function selectChapter(page: Page, chapter: Chapter) {
  await chapterRow(page, chapter.title).click();
  await expect(chapterRow(page, chapter.title)).toHaveAttribute('aria-current', 'true');
  await expect(page.getByLabel('Book text', {exact: true}).getByRole('heading', {name: chapter.title, exact: true}).first()).toBeVisible();
}

export async function startGeneration(page: Page, runtime: Runtime, book: Book, scope: 'chapter' | 'all' = 'chapter'): Promise<Generation> {
  await page.getByRole('button', {name: 'Prepare this chapter', exact: true}).click();
  const dialog = page.getByRole('dialog', {name: 'New generation', exact: true});
  await dialog.getByLabel('Generation name', {exact: true}).fill('E2E saved narration');
  const scopeSelect = dialog.getByRole('combobox');
  await expect(scopeSelect).toHaveCount(1);
  await scopeSelect.selectOption(scope);
  await dialog.getByRole('button', {name: 'Prepare audiobook', exact: true}).click();
  await expect(dialog).toBeHidden();
  await expect(player(page)).toBeVisible();
  const current = await runtime.api('book', {id: book.id});
  const summary = current.generations.find(generation => generation.name === 'E2E saved narration');
  if (!summary) throw new Error('UI generation did not create saved narration.');
  return runtime.api('generation', {id: summary.id});
}

export async function readyGeneration(page: Page, runtime: Runtime, id: string) {
  await expect.poll(async () => (await runtime.api('generation', {id})).status, {timeout: 30_000}).toBe('ready');
  await expect(player(page).getByText('Saved locally', {exact: true})).toBeVisible({timeout: 20_000});
  const generation = await runtime.api('generation', {id});
  expect(generation.segments.length).toBeGreaterThan(1);
  expect(generation.segments.every(segment => segment.status === 'ready' && segment.duration > 0 && segment.audio_hash)).toBe(true);
  return generation;
}

export async function generateChapter(page: Page, runtime: Runtime, book: Book) {
  const chapter = book.chapters.at(-1);
  if (!chapter) throw new Error('Fixture has no chapters.');
  await selectChapter(page, chapter);
  const generation = await startGeneration(page, runtime, book);
  const ready = await readyGeneration(page, runtime, generation.id);
  expect(ready.config.chapters).toEqual([chapter.id]);
  return ready;
}

export async function setRange(range: Locator, value: number) {
  await expect(range).toBeEnabled();
  await range.focus();
  const minimum = Number(await range.getAttribute('min') || 0);
  const step = Number(await range.getAttribute('step') || 1);
  await range.press('Home');
  const steps = Math.round((value - minimum) / step);
  for (let count = 0; count < steps; count++) await range.press('ArrowRight');
  await expect(range).toHaveValue(String(value));
}

export const timeline = (page: Page) => player(page).getByRole('slider', {name: 'Chapter position', exact: true});
export const timelineSeconds = async (page: Page) => Number(await timeline(page).inputValue());
export const captions = (page: Page) => player(page).locator('div[aria-label="Subtitles"]');

export async function listenToPassage(page: Page, sourceId: string) {
  const row = passage(page, sourceId);
  await row.hover();
  await row.getByRole('button', {name: /^Listen to passage: /}).click();
}

export async function pausePlayback(page: Page) {
  await player(page).getByRole('button', {name: 'Pause', exact: true}).click();
  await expect(player(page).getByRole('button', {name: 'Play', exact: true})).toBeVisible();
}
