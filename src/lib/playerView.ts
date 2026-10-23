import type {Generation, Segment} from './types';

export function playable(segment: Segment): boolean {
  return segment.status === 'ready' && Boolean(segment.audio_hash)
    && Number.isFinite(segment.duration) && segment.duration > 0;
}

export function spaceToggles(playing: boolean, error: string, segment: Segment | null | undefined): boolean {
  return playing || Boolean(!error && segment && playable(segment));
}

export function chapterTimeline(generation: Generation, selected: Segment | null, seconds: number) {
  const current = generation.segments.find(segment => segment.id === selected?.id);
  const chapterId = current?.chapter_id ?? generation.segments[0]?.chapter_id ?? '';
  const segments = generation.segments.filter(segment => segment.chapter_id === chapterId);
  const prefix: Segment[] = [];
  for (const segment of segments) {
    if (!playable(segment)) break;
    prefix.push(segment);
  }
  const complete = segments.length > 0 && prefix.length === segments.length;
  const position = current && Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  const inPrefix = prefix.some(segment => segment.id === current?.id);
  const atPreparedEnd = prefix.length > 0 && current?.id === segments[prefix.length]?.id;
  const mode = inPrefix || atPreparedEnd ? 'chapter' : current && playable(current) ? 'passage' : 'pending';
  const seekable = mode === 'chapter' ? prefix : mode === 'passage' && current ? [current] : [];
  const duration = seekable.reduce((total, segment) => total + segment.duration, 0);
  let elapsed = 0;
  for (const segment of seekable) {
    if (segment.id === current?.id) { elapsed += Math.min(position, segment.duration); break; }
    elapsed += segment.duration;
  }
  const chapters = [...new Set(generation.segments.map(segment => segment.chapter_id))];
  const index = chapters.indexOf(chapterId);
  const adjacent = (offset: number) => generation.segments.find(segment => segment.chapter_id === chapters[index + offset]) ?? null;
  return {chapterId, segments, seekable, complete, mode, duration, elapsed,
    previous: adjacent(-1), next: adjacent(1), selectedIndex: segments.findIndex(segment => segment.id === current?.id)};
}

export function timelineTarget(segments: Segment[], seconds: number): {id: string; offset: number} | null {
  if (!Number.isFinite(seconds) || !segments.length || segments.some(segment => !playable(segment))) return null;
  let remaining = Math.max(0, seconds);
  for (let index = 0; index < segments.length; index++) {
    const segment = segments[index];
    if (remaining < segment.duration || index === segments.length - 1)
      return {id: segment.id, offset: Math.min(remaining, segment.duration)};
    remaining -= segment.duration;
  }
  return null;
}

export function timelineKeyTarget(key: string, elapsed: number, duration: number, step = 5): number | null {
  const delta = key === 'ArrowRight' || key === 'ArrowUp' ? step : key === 'ArrowLeft' || key === 'ArrowDown' ? -step : 0;
  return delta && Number.isFinite(elapsed) && Number.isFinite(duration) ? Math.min(Math.max(0, elapsed + delta), Math.max(0, duration)) : null;
}

export function sleepDeadline(choice: string, now: number): number | null {
  if (choice === 'off') return 0;
  const minutes = Number(choice);
  return [15, 30, 45, 60].includes(minutes) && Number.isFinite(now) ? now + minutes * 60000 : null;
}
