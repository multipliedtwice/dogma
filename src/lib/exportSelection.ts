import type {Book, Generation} from './types';

export type ExportScope = 'all' | 'chapter' | 'ready';
const reasons = {
  otherBook: ['Choose a generation of this book.', 'Выберите озвучку этой книги.'],
  inconsistent: ['Saved chapter selection is inconsistent. Check and repair this generation.', 'Сохранённый выбор глав противоречив. Проверьте и восстановите эту озвучку.'],
  chapterMissing: ['The current chapter is not in this generation.', 'Текущей главы нет в этой озвучке.'],
  noneReady: ['No completed chapters are available yet.', 'Завершённых глав пока нет.'],
  unfinished: ['Finish these chapters, or select completed chapters only.', 'Завершите эти главы или выберите только завершённые.'],
} as const;

export function exportSelection(book: Book, generation: Generation, scope: ExportScope, chapterId: string, language: 'en' | 'ru' = 'en') {
  const text = (key: keyof typeof reasons) => reasons[key][language === 'ru' ? 1 : 0];
  if (generation.book_id !== book.id) return {chapters: [], readyCount: 0, totalCount: 0, reason: text('otherBook')};
  const included = new Set(generation.config.chapters);
  const ordered = book.chapters.filter(chapter => included.has(chapter.id));
  const counts = new Map<string, {total: number; ready: number}>();
  for (const segment of generation.segments) {
    const count = counts.get(segment.chapter_id) ?? {total: 0, ready: 0};
    count.total++;
    if (segment.status === 'ready' && segment.audio_hash && Number.isFinite(segment.duration) && segment.duration > 0) count.ready++;
    counts.set(segment.chapter_id, count);
  }
  const complete = (id: string) => {
    const count = counts.get(id);
    return Boolean(count?.total && count.total === count.ready);
  };
  const ready = ordered.filter(chapter => complete(chapter.id));
  const selected = scope === 'chapter' ? ordered.filter(chapter => chapter.id === chapterId)
    : scope === 'ready' ? ready : ordered;
  const reason = ordered.length !== included.size ? text('inconsistent')
    : !selected.length ? scope === 'chapter' ? text('chapterMissing') : text('noneReady')
    : selected.some(chapter => !complete(chapter.id)) ? text('unfinished') : '';
  return {chapters: selected.map(chapter => chapter.id), readyCount: ready.length, totalCount: ordered.length, reason};
}
