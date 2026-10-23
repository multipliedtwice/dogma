import {test, expect} from '../fixtures';
import {closeDialog, importBook, player, selectChapter, startGeneration} from '../helpers';
import {downloadExport, validateCaptions} from '../exports';
import type {Page} from '@playwright/test';
import type {Book, Generation, Segment, SpeakerResolution} from '../../src/lib/types';
import type {Runtime} from '../runtime';

const TITLE = 'E2E Review Book';
const RADIO = '“Tower, this is Anna, we are landing now.”';
const MIXED = '“Hold the rope,” Anna said, and a stranger called back, “Who is there?”';
const RADIO_NEIGHBOURS = ['The night shift began in the quiet control room.', 'The lights on the runway blinked twice.'];
const SECONDS = 24;
const ACTIVE = ['queued', 'analyzing', 'casting', 'rendering', 'aligning'];
const SPEAKER_NOTICE = 'Held until the speaker is reviewed.';
const PRESENTATION_NOTICE = 'Held until its presentation voice is reviewed.';
const NEIGHBOUR_NOTICE = 'Held next to a speaker-review gap.';
const HELD_NOTE = 'Held for speaker review. It is not part of the finished audiobook until you confirm or correct it.';
const SEPARATE = 'Confirming the speaker does not approve the presentation voice, and confirming the presentation voice does not confirm the speaker.';

const segment = (generation: Generation, text: string): Segment => {
  const found = generation.segments.find(item => item.text === text);
  if (!found) throw new Error(`Fixture passage missing: ${text}`);
  return found;
};
const parts = (item: Segment) => item.performance?.analysis_parts ?? [];
const held = (generation: Generation) => generation.segments.filter(item => item.status === 'review');
const records = (generation: Generation, item: Segment, trigger: string) => (generation.analysis.speaker_resolution ?? [])
  .filter(record => record.source_id === item.source_id && record.start < item.end && record.end > item.start && record.triggers.includes(trigger));
const only = (found: SpeakerResolution[]) => {
  expect(found).toHaveLength(1);
  return found[0];
};
const anna = (generation: Generation) => {
  const found = generation.cast.find(character => character.name === 'Anna');
  if (!found) throw new Error('Fixture speaker Anna was not cast.');
  return found;
};

async function settled(runtime: Runtime, id: string, status: string, after = -1) {
  await expect.poll(async () => {
    const current = await runtime.api('generation', {id});
    return current.revision > after && !ACTIVE.includes(current.status) ? current.status : 'running';
  }, {timeout: 45_000}).toBe(status);
  return runtime.api('generation', {id});
}

async function revision(runtime: Runtime, book: Book, parent: Generation) {
  await expect.poll(async () => (await runtime.api('book', {id: book.id})).generations.find(item => item.parent_id === parent.id)?.status ?? 'missing').toBe('paused');
  const summary = (await runtime.api('book', {id: book.id})).generations.find(item => item.parent_id === parent.id);
  if (!summary) throw new Error('Verdict did not create a corrected generation.');
  return runtime.api('generation', {id: summary.id});
}

async function resume(page: Page, runtime: Runtime, generation: Generation) {
  await player(page).getByRole('button', {name: 'Resume preparation', exact: true}).click();
  return settled(runtime, generation.id, 'held', generation.revision);
}

async function heldRun(page: Page, runtime: Runtime, path: string, scope: 'chapter' | 'all' = 'chapter') {
  const book = await importBook(page, runtime, path, TITLE);
  if (scope === 'chapter') await selectChapter(page, book.chapters[0]);
  const started = await startGeneration(page, runtime, book, scope);
  const generation = await settled(runtime, started.id, 'held');
  await expect(player(page).getByTitle(/^Held for review · \d+\/\d+ · held: 6$/)).toBeVisible({timeout: 20_000});
  return {book, generation};
}

async function openHeld(page: Page, text: string) {
  await player(page).getByRole('button', {name: 'Review held passages', exact: true}).click();
  const panel = page.getByRole('dialog', {name: 'Characters & narration', exact: true});
  const queue = panel.getByRole('note').filter({hasText: /^Passages held for speaker review: /});
  await expect(queue).toBeVisible();
  await queue.getByRole('button', {name: text, exact: true}).click();
  const dialog = page.getByRole('dialog', {name: 'Correct passage', exact: true});
  await expect(dialog.getByText(HELD_NOTE, {exact: true})).toBeVisible();
  return dialog;
}

async function verdict(page: Page, runtime: Runtime, book: Book, parent: Generation, action: () => Promise<void>) {
  await action();
  await expect(page.getByRole('dialog')).toBeHidden();
  return revision(runtime, book, parent);
}

test('holds: speaker and presentation holds are released only by their own verdicts', async ({app, runtime, bookFiles}) => {
  const {book, generation} = await heldRun(app, runtime, bookFiles.review);
  const radio = segment(generation, RADIO);
  expect(radio).toMatchObject({status: 'review', audio_hash: null, speaker: anna(generation).id});
  expect(radio.warning).toContain(PRESENTATION_NOTICE);
  expect(radio.performance?.presentation_binding).toEqual([expect.objectContaining({part: null, channel: 'radio', register: 'transmitted_speech', voice: anna(generation).voice_id})]);
  expect(only(records(generation, radio, 'critic_disagreement'))).toMatchObject({status: 'review', critic_speaker: 'unknown'});
  expect(only(records(generation, radio, 'presentation_review')).status).toBe('review');
  const neighbours = RADIO_NEIGHBOURS;
  for (const text of neighbours) expect(segment(generation, text)).toMatchObject({status: 'review', audio_hash: null, warning: NEIGHBOUR_NOTICE});
  await runtime.control({op: 'check-rendered', unrendered: [RADIO, ...neighbours]});

  let dialog = await openHeld(app, RADIO);
  const presentation = dialog.getByRole('button', {name: 'Confirm presentation voice', exact: true});
  await expect(dialog.getByText(SEPARATE, {exact: true})).toBeVisible();
  await expect(presentation).toBeEnabled();
  const speakerOnly = await verdict(app, runtime, book, generation, () => dialog.getByRole('button', {name: 'Confirm speaker', exact: true}).click());
  let current = segment(speakerOnly, RADIO);
  expect(current).toMatchObject({status: 'review', audio_hash: null});
  expect(current.warning).toContain(PRESENTATION_NOTICE);
  expect(only(records(speakerOnly, current, 'critic_disagreement'))).toMatchObject({status: 'resolved', tier: 'human_confirmation', speaker: anna(speakerOnly).id});
  expect(only(records(speakerOnly, current, 'presentation_review')).status).toBe('review');
  const resumed = await resume(app, runtime, speakerOnly);
  expect(segment(resumed, RADIO)).toMatchObject({status: 'review', audio_hash: null});
  expect(held(resumed).map(item => item.id)).toEqual(held(generation).map(item => item.id));
  await runtime.control({op: 'check-rendered', unrendered: [RADIO, ...neighbours]});

  dialog = await openHeld(app, RADIO);
  const released = await verdict(app, runtime, book, resumed, () => dialog.getByRole('button', {name: 'Confirm presentation voice', exact: true}).click());
  current = segment(released, RADIO);
  expect(current).toMatchObject({status: 'pending', audio_hash: null});
  expect(current.warning).not.toContain(PRESENTATION_NOTICE);
  expect(current.performance?.presentation_binding).toEqual(radio.performance?.presentation_binding);
  expect(segment(released, MIXED).status).toBe('review');
  const finished = await resume(app, runtime, released);
  expect(segment(finished, RADIO)).toMatchObject({status: 'ready', duration: SECONDS});
  expect(segment(finished, RADIO).audio_hash).toBeTruthy();
  expect(neighbours.map(text => segment(finished, text).status)).toEqual(neighbours.map(() => 'ready'));
  expect(segment(finished, MIXED)).toMatchObject({status: 'review', audio_hash: null});
  await runtime.control({op: 'check-rendered', rendered: [RADIO, ...neighbours], unrendered: [MIXED, ...parts(segment(finished, MIXED)).map(part => part.text)]});
});

test('holds: approving the cast starts narration without releasing any hold', async ({app, runtime, bookFiles}) => {
  const book = await importBook(app, runtime, bookFiles.review, TITLE);
  await app.getByRole('button', {name: 'Settings', exact: true}).click();
  const settings = app.getByRole('dialog', {name: 'Settings', exact: true});
  const stop = settings.getByRole('switch', {name: 'Stop for cast review before narration', exact: true});
  if (await stop.getAttribute('aria-checked') !== 'true') await stop.click();
  await expect(stop).toHaveAttribute('aria-checked', 'true');
  await expect.poll(async () => (await runtime.api('settings', {})).cast_review).toBe(true);
  await closeDialog(app);
  await selectChapter(app, book.chapters[0]);
  const started = await startGeneration(app, runtime, book);
  const review = await settled(runtime, started.id, 'review');
  const open = (review.analysis.speaker_resolution ?? []).filter(record => record.status === 'review');
  expect(open.flatMap(record => record.triggers).sort()).toEqual(['critic_disagreement', 'critic_disagreement', 'unresolved_speaker']);
  expect(review.segments.every(item => item.status === 'pending' && item.audio_hash === null)).toBe(true);
  await player(app).getByRole('button', {name: 'Review cast', exact: true}).click();
  const panel = app.getByRole('dialog', {name: 'Characters & narration', exact: true});
  const queue = panel.getByRole('note').filter({hasText: /^Passages held for speaker review: 2/});
  await expect(queue).toBeVisible();
  await expect(panel.getByText(/^Starting narration does not release speaker holds\./)).toBeVisible();
  await panel.getByRole('button', {name: 'Start narration', exact: true}).click();
  await expect(panel).toBeHidden();
  const approved = await settled(runtime, started.id, 'held', review.revision);
  expect(approved.analysis.approved).toBe(true);
  for (const record of open) {
    expect((approved.analysis.speaker_resolution ?? []).find(item => item.source_id === record.source_id && item.start === record.start && item.end === record.end
      && item.triggers.join() === record.triggers.join())?.status).toBe('review');
  }
  const radio = segment(approved, RADIO), mixed = segment(approved, MIXED);
  expect(radio).toMatchObject({status: 'review', audio_hash: null});
  expect(radio.warning).toContain(PRESENTATION_NOTICE);
  expect(mixed).toMatchObject({status: 'review', audio_hash: null, speaker: 'unknown'});
  expect(held(approved)).toHaveLength(6);
  await runtime.control({op: 'check-rendered', unrendered: held(approved).flatMap(item => [item.text, ...parts(item).map(part => part.text)])});
  await openHeld(app, MIXED);
  await closeDialog(app);
});

test('holds: resuming a held generation keeps its holds and only unheld chapters export', async ({app, runtime, bookFiles}, info) => {
  const {book, generation} = await heldRun(app, runtime, bookFiles.review, 'all');
  const [night, morning] = book.chapters;
  const unheld = generation.segments.filter(item => item.chapter_id === morning.id);
  expect(unheld.every(item => item.status === 'ready' && item.audio_hash && item.duration > 0)).toBe(true);
  expect(held(generation).every(item => item.chapter_id === night.id && item.audio_hash === null && item.duration === 0)).toBe(true);
  expect(segment(generation, MIXED).warning).toContain(SPEAKER_NOTICE);
  for (const chapters of [[night.id], undefined]) {
    await expect(runtime.api('export', {id: generation.id, format: 'srt', ...(chapters ? {chapters} : {})})).rejects.toThrow(/"code":\s*"held_segments"/);
  }
  const resumed = await resume(app, runtime, generation);
  expect(held(resumed)).toEqual(held(generation));
  expect(resumed.segments.filter(item => item.chapter_id === morning.id)).toEqual(unheld);
  expect(resumed.analysis.speaker_review_holds).toMatchObject({flagged_segments: 2, held_with_neighbours: 6});
  await runtime.control({op: 'check-rendered', rendered: unheld.map(item => item.text), unrendered: held(resumed).flatMap(item => [item.text, ...parts(item).map(part => part.text)])});
  await expect(runtime.api('export', {id: generation.id, format: 'srt', chapters: [night.id]})).rejects.toThrow(/"code":\s*"held_segments"/);

  await app.getByRole('button', {name: 'Export', exact: true}).click();
  const dialog = app.getByRole('dialog', {name: 'Export saved audio', exact: true});
  const scope = dialog.getByRole('combobox', {name: 'Chapters', exact: true});
  const button = dialog.getByRole('button', {name: 'Export', exact: true});
  await expect(dialog.getByText('A complete audiobook cannot be exported while passages are held for speaker review. Confirm or correct them in Characters & narration.', {exact: true})).toBeVisible();
  await scope.selectOption('all');
  await expect(dialog.getByRole('status')).toHaveText('Finish these chapters, or select completed chapters only.');
  await expect(button).toBeDisabled();
  await scope.selectOption('ready');
  await expect(dialog.getByRole('status')).toHaveText('1 completed chapter selected. Saved audio is checked again before export.');
  const srt = await downloadExport(app, info, book.title, 'srt');
  const text = await validateCaptions(srt, unheld, true);
  for (const item of held(resumed)) expect(text).not.toContain(item.text);
  expect(held(await runtime.api('generation', {id: generation.id}))).toEqual(held(generation));
});

test('holds: a mixed sentence keeps per-part holds and renders part by part after its verdicts', async ({app, runtime, bookFiles}) => {
  const {book, generation} = await heldRun(app, runtime, bookFiles.review);
  const mixed = segment(generation, MIXED);
  const speaker = anna(generation).id;
  expect(mixed).toMatchObject({status: 'review', audio_hash: null, speaker: 'unknown'});
  expect(mixed.performance?.internal_transition).toBe(true);
  expect(parts(mixed).map(part => [part.text, part.speaker])).toEqual([['“Hold the rope,” ', speaker], ['Anna said, and a stranger called back, ', 'narrator'], ['“Who is there?”', 'unknown']]);
  expect(parts(mixed).map(part => part.text).join('')).toBe(MIXED);
  const [first, , last] = parts(mixed);
  expect(only(records(generation, mixed, 'critic_disagreement'))).toMatchObject({status: 'review', start: first.start, end: first.end});
  expect(only(records(generation, mixed, 'unresolved_speaker'))).toMatchObject({status: 'review', start: last.start, end: last.end});

  let dialog = await openHeld(app, MIXED);
  await expect(dialog.getByRole('textbox', {name: 'Spoken text / pronunciation', exact: true})).toBeDisabled();
  await expect(dialog.getByRole('button', {name: 'Confirm speaker', exact: true})).toBeDisabled();
  await expect(dialog.getByText('An unresolved speaker cannot be confirmed. Choose the speaker and save a corrected version.', {exact: true})).toBeVisible();
  for (const [index, value] of [speaker, 'narrator', 'unknown'].entries()) {
    await expect(dialog.getByRole('combobox', {name: `Speaker for part ${index + 1}`, exact: true})).toHaveValue(value);
  }
  await expect(dialog.getByRole('button', {name: 'Confirm part 1', exact: true})).toBeEnabled();
  await expect(dialog.getByRole('button', {name: 'Confirm part 3', exact: true})).toBeDisabled();
  await expect(dialog.getByRole('button', {name: 'Save corrected version', exact: true})).toBeDisabled();
  const confirmed = await verdict(app, runtime, book, generation, () => dialog.getByRole('button', {name: 'Confirm part 1', exact: true}).click());
  let current = segment(confirmed, MIXED);
  expect(current).toMatchObject({status: 'review', audio_hash: null, speaker: 'unknown'});
  expect(parts(current)).toEqual(parts(mixed));
  expect(only(records(confirmed, current, 'critic_disagreement'))).toMatchObject({status: 'resolved', tier: 'human_confirmation', start: first.start, end: first.end});
  expect(only(records(confirmed, current, 'unresolved_speaker'))).toMatchObject({status: 'review', start: last.start, end: last.end});
  const resumed = await resume(app, runtime, confirmed);
  expect(segment(resumed, MIXED)).toMatchObject({status: 'review', audio_hash: null});
  await runtime.control({op: 'check-rendered', unrendered: [MIXED, ...parts(mixed).map(part => part.text)]});

  dialog = await openHeld(app, MIXED);
  await dialog.getByRole('combobox', {name: 'Speaker for part 3', exact: true}).selectOption({label: 'Anna'});
  await expect(dialog.getByRole('button', {name: 'Confirm part 1', exact: true})).toBeDisabled();
  const corrected = await verdict(app, runtime, book, resumed, () => dialog.getByRole('button', {name: 'Save corrected version', exact: true}).click());
  current = segment(corrected, MIXED);
  expect(current).toMatchObject({status: 'pending', audio_hash: null, speaker});
  expect(parts(current).map(part => [part.text, part.speaker])).toEqual([[first.text, speaker], [parts(mixed)[1].text, 'narrator'], [last.text, speaker]]);
  expect(only(records(corrected, current, 'unresolved_speaker'))).toMatchObject({status: 'resolved', tier: 'human_correction'});
  expect(segment(corrected, RADIO).status).toBe('review');
  const finished = await resume(app, runtime, corrected);
  current = segment(finished, MIXED);
  expect(current.status).toBe('ready');
  expect(current.audio_hash).toBeTruthy();
  expect(current.duration).toBeCloseTo(parts(mixed).length * SECONDS, 3);
  expect(current.warning).not.toContain(SPEAKER_NOTICE);
  expect(segment(finished, RADIO)).toMatchObject({status: 'review', audio_hash: null});
  await runtime.control({op: 'check-rendered', rendered: parts(current).map(part => part.text), unrendered: [MIXED, RADIO]});
});
