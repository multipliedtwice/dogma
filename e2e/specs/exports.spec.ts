import {test, expect} from '../fixtures';
import {EPUB_TITLE, FB2_TITLE, generateChapter, importBook, readyGeneration, startGeneration} from '../helpers';
import {downloadExport, probeAudio, requireExportTools, validateCaptions, validateChapterZip, validateM4B} from '../exports';

test.beforeEach(requireExportTools);

test('phase 3: downloaded SRT and WebVTT preserve text and exact saved timing', async ({app, runtime, bookFiles}, info) => {
  const book = await importBook(app, runtime, bookFiles.epub, EPUB_TITLE);
  const generation = await generateChapter(app, runtime, book);
  const srt = await downloadExport(app, info, book.title, 'srt');
  const srtText = await validateCaptions(srt, generation.segments, true);
  expect(srtText).toContain('R&D <lantern>');
  expect(srtText).toContain('Café readers say: «Добрый вечер».');
  const vtt = await downloadExport(app, info, book.title, 'vtt');
  const vttText = await validateCaptions(vtt, generation.segments, false);
  expect(vttText).toContain('R&amp;D &lt;lantern&gt;');
  expect(vttText).toContain('Café readers say: «Добрый вечер».');
  expect((await runtime.api('generation', {id: generation.id})).segments).toEqual(generation.segments);
});

test('phase 3: downloaded MP3, chaptered M4B and chapter ZIP contain real encoded audio', async ({app, runtime, bookFiles}, info) => {
  test.setTimeout(120_000);
  const health = await runtime.api('health', {});
  expect(health.ffmpeg, 'Real runtime must find ffmpeg on PATH').toBe(true);
  expect(health.ffprobe, 'Real runtime must find ffprobe on PATH').toBe(true);
  const book = await importBook(app, runtime, bookFiles.fb2, FB2_TITLE);
  const started = await startGeneration(app, runtime, book, 'all');
  const generation = await readyGeneration(app, runtime, started.id);
  expect(generation.config.chapters).toEqual(book.chapters.map(chapter => chapter.id));
  const mp3 = await downloadExport(app, info, book.title, 'mp3');
  await probeAudio(mp3, 'mp3', generation.segments, book.title, book.author);
  const m4b = await downloadExport(app, info, book.title, 'm4b');
  await validateM4B(m4b, book, generation);
  const zip = await downloadExport(app, info, book.title, 'chapters');
  await validateChapterZip(zip, info.outputPath('chapter-files'), book, generation);
  const saved = await runtime.api('generation', {id: generation.id});
  expect(saved.exports.map(item => item.format).sort()).toEqual(['chapters', 'm4b', 'mp3']);
  expect(saved.segments).toEqual(generation.segments);
});
