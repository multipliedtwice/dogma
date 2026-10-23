import type {Book, Chapter, DeletionResult, Generation, GenerationSummary, Segment} from './types';

export function acceptGenerationRefresh(current: Generation | null, next: Generation): Generation | null {
  return current?.id === next.id && current.book_id === next.book_id && (next.revision ?? 0) >= (current.revision ?? 0) ? next : current;
}

const polledAnalysis = new Set(['complete', 'casting_warnings', 'casting_notices', 'progress', 'degraded', 'approved', 'narrator_voices']);
export function acceptGenerationPoll(current: Generation | null, next: Generation, full: boolean, removed: string[]): Generation | null {
  if (!current || acceptGenerationRefresh(current, next) !== next) return current;
  const analysis = {...Object.fromEntries(Object.entries(current.analysis ?? {}).filter(([key]) => !polledAnalysis.has(key))), ...next.analysis};
  if (full) return {...next, analysis};
  const changed = new Map(next.segments.map(segment => [segment.id, segment]));
  const deleted = new Set(removed);
  const segments = current.segments.filter(segment => !deleted.has(segment.id)).map(segment => changed.get(segment.id) ?? segment);
  const known = new Set(segments.map(segment => segment.id));
  for (const segment of next.segments) if (!known.has(segment.id)) segments.push(segment);
  segments.sort((a,b)=>a.ordinal-b.ordinal);
  return {...current,...next,analysis,segments};
}

export async function afterPlaybackSaved<T>(stop: () => Promise<unknown>, read: () => Promise<T>): Promise<T> {
  await stop();
  return read();
}

export function playbackStart(generation: Generation, sourceId: string): Segment | undefined {
  return generation.segments.find(segment => segment.source_id === sourceId);
}

export function searchKey(value: string): string {
  return value.trim().toLocaleLowerCase().replaceAll('ё', 'е');
}

export function visibleChapters(chapters: Chapter[], query: string) {
  const match = searchKey(query);
  return chapters.map((chapter, index) => ({chapter, number: index + 1}))
    .filter(({chapter}) => searchKey(chapter.title).includes(match));
}

export function generationHistory(book: Book, current: Generation | null): GenerationSummary[] {
  return book.generations.map(saved => {
    if (current?.id !== saved.id || current.book_id !== book.id) return saved;
    const ready = current.segments.filter(segment => segment.status === 'ready');
    return {...saved, name: current.name, status: current.status, error: current.error,
      updated: current.updated, config: current.config,
      progress: {total: current.segments.length, ready: ready.length,
        duration: ready.reduce((total, segment) => total + segment.duration, 0)}};
  });
}

export function playbackShortcut(event: KeyboardEvent, playable = true): 'toggle' | 'back' | 'forward' | null {
  if (event.defaultPrevented || event.repeat || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return null;
  const target = event.target;
  if (target instanceof Element && (target.closest('input, textarea, select, button, a, summary, audio, video, [role="button"], [role="link"], [role="slider"], [role="checkbox"], [role="switch"], [role="tab"], [role="menuitem"], [role="option"]')
      || target instanceof HTMLElement && target.isContentEditable)) return null;
  return event.code === 'Space' ? playable ? 'toggle' : null : event.code === 'ArrowLeft' ? 'back' : event.code === 'ArrowRight' ? 'forward' : null;
}

export function deletionNotice(result: DeletionResult, label: 'Book' | 'Generation' | 'Export', language: 'en' | 'ru' = 'en'): string {
  if (language === 'ru') {
    const subject = label === 'Book' ? 'Книга удалена' : label === 'Export' ? 'Экспорт удалён' : 'Озвучка удалена';
    return result.cleanup_warnings.length
      ? `${subject} из библиотеки. Некоторые локальные файлы удалить не удалось; очистка диска не завершена. Копии, экспортированные за пределы приложения, не изменены.`
      : `${subject} из библиотеки. Копии, экспортированные за пределы приложения, не изменены.`;
  }
  return result.cleanup_warnings.length
    ? `${label} removed from the library. Some local files could not be removed; disk cleanup is incomplete. Copies exported outside the app are unchanged.`
    : `${label} removed from the library. Copies exported outside the app are unchanged.`;
}

export function defaultGeneration(book: Pick<Book, 'generations' | 'position'>): GenerationSummary | undefined {
  return book.generations.find(saved => saved.id === book.position?.generation_id) ?? book.generations[0];
}

export function repairOutcome(pending: string, generation: Pick<Generation, 'id' | 'status'> | null): 'wait' | 'clear' | 'drop' {
  if (!pending || generation?.id !== pending) return 'drop';
  if (generation.status === 'ready' || generation.status === 'held') return 'clear';
  return ['failed', 'paused'].includes(generation.status) ? 'drop' : 'wait';
}

export function resumesChapter(generation: Pick<Generation, 'status'> | null, chapterSegments: Segment[]): boolean {
  return Boolean(generation && chapterSegments.length && (generation.status === 'paused' || generation.status === 'failed'));
}

export function generationRename(value: string | null, current: string): {name: string} | {invalid: true} | null {
  if (value === null) return null;
  const name = value.trim();
  if (!name) return {invalid: true};
  return name === current ? null : {name};
}

type Correction = Pick<Segment, 'spoken' | 'speaker' | 'delivery'>;
export function correctionChanged(original: Correction | undefined, edit: Correction): boolean {
  if (!original || !edit.spoken.trim()) return false;
  return original.spoken.trim() !== edit.spoken.trim() || original.speaker !== edit.speaker || original.delivery !== edit.delivery;
}

export function correctionRequest(original: Correction | undefined, edit: Correction & Pick<Segment, 'id'>, retake: boolean) {
  if (!original || !edit.spoken.trim() || !retake && !correctionChanged(original, edit)) return null;
  return {id: edit.id, spoken: edit.spoken.trim(), speaker: edit.speaker, delivery: edit.delivery, ...(retake ? {retake: true} : {})};
}

export function nextGenerationName(names: string[], base: string): string {
  const taken = new Set(names);
  let number = names.length + 1;
  while (taken.has(`${base} ${number}`)) number++;
  return `${base} ${number}`;
}

const revisionSuffix = / · (?:revision|исправление)(?: \d+)?$/;
export function revisionName(name: string, names: string[], language: 'en' | 'ru', maximum = 200): string {
  const suffix = language === 'ru' ? ' · исправление' : ' · revision';
  const taken = new Set(names);
  const base = name.replace(revisionSuffix, '').slice(0, maximum - suffix.length - 4);
  let candidate = base + suffix;
  for (let number = 2; taken.has(candidate); number++) candidate = `${base}${suffix} ${number}`;
  return candidate;
}
