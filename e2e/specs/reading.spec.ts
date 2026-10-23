import {test, expect, type BookFiles} from '../fixtures';
import {chapterRow, chapterRows, chooseFile, closeDialog, EPUB_TITLE, FB2_TITLE, importBook, openSavedBook, passage, selectChapter, setRange, toLibrary} from '../helpers';

test('phase 1: EPUB chooser, library search, chapter search and navigation', async ({app, runtime, bookFiles}) => {
  const book = await importBook(app, runtime, bookFiles.epub, EPUB_TITLE);
  expect(book.format).toBe('epub');
  expect(book.chapters.map(chapter => chapter.title)).toEqual(['Harbor morning', 'Lantern evening']);
  const search = app.getByRole('textbox', {name: 'Find chapter', exact: true});
  await search.fill('lantern');
  await expect(chapterRows(app)).toHaveCount(1);
  await selectChapter(app, book.chapters[1]);
  await search.fill('not-a-chapter');
  await expect(chapterRows(app)).toHaveCount(0);
  await expect(app.getByText('No matching chapters', {exact: true})).toBeVisible();
  await search.fill('');
  await expect(chapterRows(app)).toHaveCount(2);
  await app.getByLabel('Book text', {exact: true}).getByRole('button', {name: /Previous chapter/}).click();
  await expect(chapterRow(app, book.chapters[0].title)).toHaveAttribute('aria-current', 'true');
  await app.getByLabel('Book text', {exact: true}).getByRole('button', {name: /Next chapter/}).click();
  await expect(chapterRow(app, book.chapters[1].title)).toHaveAttribute('aria-current', 'true');
  await toLibrary(app);
  await expect(app.getByRole('article')).toHaveCount(1);
  await app.getByRole('textbox', {name: 'Search your library'}).fill('forest');
  await expect(app.getByText('No matching books', {exact: true})).toBeVisible();
  await app.getByRole('button', {name: 'Clear search', exact: true}).click();
  await openSavedBook(app, EPUB_TITLE);
  await expect(chapterRow(app, book.chapters[1].title)).toHaveAttribute('aria-current', 'true');
});

test('phase 1: FB2 chooser preserves chapters and formatted text', async ({app, runtime, bookFiles}) => {
  const book = await importBook(app, runtime, bookFiles.fb2, FB2_TITLE);
  expect(book.format).toBe('fb2');
  expect(book.chapters.map(chapter => chapter.title)).toEqual(['Pine trail', 'River crossing']);
  await expect(app.getByLabel('Book text', {exact: true}).locator('em')).toHaveText('pine trail');
  await selectChapter(app, book.chapters[1]);
  await expect(app.getByText('The last page waits on the other side.', {exact: true})).toBeVisible();
  await toLibrary(app);
  await expect(app.getByRole('article').getByText('FB2', {exact: true})).toBeVisible();
});

const invalidFiles: {key: keyof BookFiles; error: RegExp}[] = [
  {key: 'invalid', error: /choose a nonempty EPUB or FB2/i},
  {key: 'brokenEpub', error: /Not a valid EPUB archive/},
  {key: 'brokenFb2', error: /The book is not well-formed XML at line 1\./},
];
for (const item of invalidFiles) {
  test(`phase 1: reject ${item.key} without a library entry`, async ({app, runtime, bookFiles}) => {
    await chooseFile(app, app.getByRole('button', {name: 'Open book', exact: true}).first(), bookFiles[item.key]);
    await expect(app.getByRole('alert')).toContainText(item.error);
    expect(await runtime.api('library', {})).toEqual([]);
    await expect(app.getByRole('button', {name: 'Open book', exact: true}).first()).toBeEnabled();
  });
}

test('phase 1: bookmark note, return to passage and internal link', async ({app, runtime, bookFiles}) => {
  const book = await importBook(app, runtime, bookFiles.epub, EPUB_TITLE);
  const source = book.chapters[0].blocks.find(block => block.text.includes('Read the lantern note'));
  const target = book.chapters[1].blocks.find(block => block.text.includes('The lantern note ends here.'));
  if (!source || !target) throw new Error('Fixture link passages missing.');
  const row = passage(app, source.id);
  await row.hover();
  await row.getByRole('button', {name: /^Bookmark passage: /}).click();
  await expect.poll(async () => (await runtime.api('book', {id: book.id})).bookmarks.length).toBe(1);
  await app.getByRole('button', {name: 'Bookmarks', exact: true}).click();
  await app.getByRole('button', {name: `Edit bookmark note: ${source.text}`, exact: true}).click();
  const noteDialog = app.getByRole('dialog', {name: 'Edit bookmark note', exact: true});
  await noteDialog.getByRole('textbox', {name: 'Bookmark note', exact: true}).fill('Return to the harbor');
  await noteDialog.getByRole('button', {name: 'Save', exact: true}).click();
  await expect(noteDialog).toBeHidden();
  await expect.poll(async () => (await runtime.api('book', {id: book.id})).bookmarks[0]?.note).toBe('Return to the harbor');
  await row.getByRole('link', {name: 'Read the lantern note', exact: true}).click();
  await expect(passage(app, target.id)).toBeFocused();
  await expect.poll(async () => (await runtime.api('book', {id: book.id})).reading_position?.source_id).toBe(target.id);
  await app.getByRole('button', {name: 'Return to the harbor', exact: true}).click();
  await expect(row).toBeFocused();
  await app.reload();
  await openSavedBook(app, EPUB_TITLE);
  await app.getByRole('button', {name: 'Bookmarks', exact: true}).click();
  await expect(app.getByRole('button', {name: 'Return to the harbor', exact: true})).toBeVisible();
  await app.locator('aside.sidebar').getByRole('button', {name: 'Remove bookmark: Return to the harbor', exact: true}).click();
  const removeDialog = app.getByRole('dialog', {name: 'Remove bookmark and note?', exact: true});
  await removeDialog.getByRole('button', {name: 'Remove bookmark', exact: true}).click();
  await expect(removeDialog).toBeHidden();
  await expect(app.getByText('Bookmark a passage to find it here.', {exact: true})).toBeVisible();
  expect((await runtime.api('book', {id: book.id})).bookmarks).toEqual([]);
});

test('phase 1: reading preferences and scroll position survive reload', async ({app, runtime, bookFiles}) => {
  const book = await importBook(app, runtime, bookFiles.epub, EPUB_TITLE);
  await app.getByRole('button', {name: 'Reading appearance', exact: true}).click();
  const dialog = app.getByRole('dialog', {name: 'Appearance', exact: true});
  await dialog.getByRole('button', {name: /Sepia/}).click();
  await setRange(dialog.getByRole('slider', {name: /^Text size/}), 26);
  await setRange(dialog.getByRole('slider', {name: /^Line spacing/}), 2.2);
  await dialog.getByRole('switch', {name: 'Follow narration', exact: true}).uncheck();
  await expect.poll(async () => {
    const settings = await runtime.api('settings', {});
    return [settings.theme, settings.font_size, settings.line_height, settings.follow];
  }).toEqual(['sepia', 26, 2.2, false]);
  await closeDialog(app);
  const reader = app.getByLabel('Book text', {exact: true});
  await reader.hover();
  await app.mouse.wheel(0, 850);
  await expect.poll(async () => (await runtime.api('book', {id: book.id})).reading_position?.offset ?? 0).toBeGreaterThan(100);
  const saved = (await runtime.api('book', {id: book.id})).reading_position;
  if (!saved?.source_id) throw new Error('No saved reading anchor.');
  await app.reload();
  await openSavedBook(app, EPUB_TITLE);
  await expect(app.locator('html')).toHaveAttribute('data-theme', 'sepia');
  await expect(reader.getByRole('article')).toHaveCSS('font-size', '26px');
  await expect(reader.getByRole('article')).toHaveCSS('line-height', '57.2px');
  await expect(chapterRow(app, book.chapters[0].title)).toHaveAttribute('aria-current', 'true');
  await expect.poll(async () => Math.abs(await reader.evaluate(element => element.scrollTop) - saved.offset)).toBeLessThan(4);
  expect((await runtime.api('book', {id: book.id})).reading_position?.source_id).toBe(saved.source_id);
  await app.getByRole('button', {name: 'Reading appearance', exact: true}).click();
  await expect(dialog.getByRole('switch', {name: 'Follow narration', exact: true})).not.toBeChecked();
});

test('phase 1: Russian interface persists and remains usable', async ({app, runtime, bookFiles}) => {
  await importBook(app, runtime, bookFiles.epub, EPUB_TITLE);
  await app.getByRole('button', {name: 'Reading appearance', exact: true}).click();
  await app.getByRole('combobox', {name: 'Interface language', exact: true}).selectOption('ru');
  await expect(app.locator('html')).toHaveAttribute('lang', 'ru');
  await expect.poll(async () => (await runtime.api('settings', {})).language).toBe('ru');
  await closeDialog(app);
  await expect(app.getByRole('button', {name: 'Читать', exact: true})).toBeVisible();
  await expect(app.getByRole('button', {name: 'Главы', exact: true})).toBeVisible();
  await app.reload();
  await expect(app.getByRole('heading', {name: 'Библиотека', exact: true})).toBeVisible();
  await expect(app.getByRole('button', {name: 'Открыть книгу', exact: true}).first()).toBeVisible();
  await openSavedBook(app, EPUB_TITLE, 'ru');
  await app.getByRole('textbox', {name: 'Найти главу', exact: true}).fill('Lantern');
  await expect(chapterRows(app)).toHaveCount(1);
});

test('phase 1: confirm deletion and keep the original input', async ({app, runtime, bookFiles}) => {
  await importBook(app, runtime, bookFiles.fb2, FB2_TITLE);
  await toLibrary(app);
  await app.getByRole('button', {name: `Delete ${FB2_TITLE}`, exact: true}).click();
  await app.getByRole('dialog', {name: 'Delete book?', exact: true}).getByRole('button', {name: 'Delete', exact: true}).click();
  await expect(app.getByRole('heading', {name: 'Your next audiobook starts with a book.', exact: true})).toBeVisible();
  expect(await runtime.api('library', {})).toEqual([]);
  await app.reload();
  await expect(app.getByRole('article')).toHaveCount(0);
  await importBook(app, runtime, bookFiles.fb2, FB2_TITLE);
});
