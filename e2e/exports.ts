import {execFile} from 'node:child_process';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {promisify} from 'node:util';
import {expect, type Page, type TestInfo} from '@playwright/test';
import type {Book, Generation, Segment} from '../src/lib/types';
import {fixtureEnvironment, python, root} from './environment';

const execute = promisify(execFile);
export type ExportFormat = 'srt' | 'vtt' | 'mp3' | 'm4b' | 'chapters';
type Probe = {
  streams: {codec_type: string; codec_name: string; sample_rate: string; channels: number}[];
  format: {format_name: string; duration: string; tags?: {title?: string; artist?: string}};
  chapters: {start_time: string; end_time: string; tags?: {title?: string}}[];
};

export async function requireExportTools() {
  for (const tool of ['ffmpeg', 'ffprobe']) {
    try {await execute(tool, ['-version'], {timeout: 5_000});}
    catch (error) {throw new Error(`${tool} is required. Install ffmpeg AND ffprobe on PATH before export specs. Export specs never skip.`, {cause: error});}
  }
}

export async function downloadExport(page: Page, info: TestInfo, title: string, format: ExportFormat) {
  if (!(await page.getByRole('dialog').isVisible())) await page.getByRole('button', {name: 'Export', exact: true}).click();
  const dialog = page.getByRole('dialog', {name: 'Export saved audio', exact: true});
  await dialog.getByRole('combobox', {name: 'Format', exact: true}).selectOption(format);
  const button = dialog.getByRole('button', {name: 'Export', exact: true});
  await expect(button).toBeEnabled();
  const pending = page.waitForEvent('download', {timeout: 60_000});
  await button.click();
  const download = await pending;
  const filename = `${title}.${format === 'chapters' ? 'zip' : format}`;
  expect(download.suggestedFilename()).toBe(filename);
  expect(await download.failure()).toBeNull();
  const path = info.outputPath(filename);
  await download.saveAs(path);
  await expect(button).toBeEnabled();
  return path;
}

function seconds(timestamp: string, srt: boolean) {
  const match = /^(\d{2}):(\d{2}):(\d{2})([,.])(\d{3})$/.exec(timestamp);
  if (!match || match[4] !== (srt ? ',' : '.') || Number(match[2]) > 59 || Number(match[3]) > 59) throw new Error(`Invalid caption timestamp: ${timestamp}`);
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]) + Number(match[5]) / 1000;
}

export async function validateCaptions(path: string, segments: Segment[], srt: boolean) {
  const text = await readFile(path, 'utf8');
  expect(text).not.toContain('\uFFFD');
  expect(text).not.toContain('\r');
  const body = srt ? text : text.replace(/^WEBVTT\n\n/, '');
  if (srt) expect(text).not.toContain('WEBVTT');
  else expect(text.startsWith('WEBVTT\n\n')).toBe(true);
  const cues = body.trimEnd().split('\n\n').map((entry, index) => {
    const [id, timing, ...lines] = entry.split('\n');
    expect(id).toBe(String(index + 1));
    const times = timing.split(' --> ');
    expect(times).toHaveLength(2);
    const start = seconds(times[0], srt), end = seconds(times[1], srt);
    expect(end).toBeGreaterThan(start);
    return {start, end, text: lines.join('\n')};
  });
  // Alignment is disabled in this fixture: one cue per saved narration segment.
  expect(segments.every(segment => segment.cues.length === 0)).toBe(true);
  expect(cues).toHaveLength(segments.length);
  let offset = 0;
  for (let index = 0; index < segments.length; index++) {
    const segment = segments[index];
    const spoken = segment.spoken.replace(/[\r\n]/g, ' ');
    expect(cues[index].text).toBe(srt ? spoken : spoken.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'));
    expect(cues[index].start).toBeCloseTo(offset, 3);
    offset += segment.duration;
    expect(cues[index].end).toBeCloseTo(offset, 3);
  }
  return text;
}

export async function probeAudio(path: string, codec: 'aac' | 'mp3', segments: Segment[], title: string, author: string) {
  const {stdout} = await execute('ffprobe', ['-v', 'error', '-show_streams', '-show_chapters', '-show_format', '-of', 'json', path], {timeout: 15_000, maxBuffer: 1024 ** 2});
  const probe: Probe = JSON.parse(stdout);
  const audio = probe.streams.filter(stream => stream.codec_type === 'audio');
  expect(audio).toHaveLength(1);
  expect(audio[0]).toMatchObject({codec_name: codec, sample_rate: '24000', channels: 1});
  expect(probe.streams.filter(stream => stream.codec_type === 'video')).toHaveLength(0);
  expect(probe.format.format_name.split(',')).toContain(codec === 'mp3' ? 'mp3' : 'm4a');
  expect(probe.format.tags).toMatchObject({title, artist: author});
  const expected = segments.reduce((total, segment) => total + segment.duration, 0);
  expect(Number(probe.format.duration)).toBeGreaterThanOrEqual(expected - .005);
  expect(Number(probe.format.duration)).toBeLessThanOrEqual(expected + .15);
  return probe;
}

export async function validateM4B(path: string, book: Book, generation: Generation) {
  const probe = await probeAudio(path, 'aac', generation.segments, book.title, book.author);
  const chapters = book.chapters.filter(chapter => generation.config.chapters.includes(chapter.id));
  expect(probe.chapters).toHaveLength(chapters.length);
  let offset = 0;
  for (let index = 0; index < chapters.length; index++) {
    const chapter = chapters[index];
    expect(probe.chapters[index].tags?.title).toBe(chapter.title);
    expect(Math.abs(Number(probe.chapters[index].start_time) - offset)).toBeLessThan(.003);
    offset += generation.segments.filter(segment => segment.chapter_id === chapter.id).reduce((sum, segment) => sum + segment.duration, 0);
    expect(Math.abs(Number(probe.chapters[index].end_time) - offset)).toBeLessThan(.003);
  }
}

export async function validateChapterZip(path: string, directory: string, book: Book, generation: Generation) {
  const {stdout} = await execute(python, [join(root, 'e2e/runtime/unpack_export.py'), path, directory], {env: fixtureEnvironment(), timeout: 15_000});
  const names: string[] = JSON.parse(stdout);
  const chapters = book.chapters.map((chapter, index) => ({chapter, number: index + 1})).filter(({chapter}) => generation.config.chapters.includes(chapter.id));
  const expected = ['chapters.json', ...chapters.flatMap(({number}) => [`${String(number).padStart(3, '0')}.mp3`, `${String(number).padStart(3, '0')}.vtt`])];
  expect([...names].sort()).toEqual([...expected].sort());
  expect(JSON.parse(await readFile(join(directory, 'chapters.json'), 'utf8'))).toEqual(chapters.map(({chapter, number}) => ({number, title: chapter.title})));
  for (const {chapter, number} of chapters) {
    const segments = generation.segments.filter(segment => segment.chapter_id === chapter.id);
    const basename = join(directory, String(number).padStart(3, '0'));
    await probeAudio(basename + '.mp3', 'mp3', segments, chapter.title, book.author);
    await validateCaptions(basename + '.vtt', segments, false);
  }
}
