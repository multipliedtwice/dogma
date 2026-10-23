import type {Block, Book, Chapter} from './types';

export function speakable(block: Pick<Block, 'kind' | 'text'>): boolean {
  return Boolean(block.text) && !['figure', 'separator', 'note'].includes(block.kind);
}

export function hasNarratableText(chapter: Chapter): boolean {
  return chapter.blocks.some(speakable);
}

export function defaultNarrationChapters(book: Book): string[] {
  return book.chapters.filter(chapter => chapter.default_narration !== false && hasNarratableText(chapter)).map(chapter => chapter.id);
}

export function narrationSelection(book: Book, scope: string, chapterId: string, selected: string[]): string[] {
  const requested = scope === 'all' ? defaultNarrationChapters(book)
    : scope === 'everything' ? book.chapters.filter(hasNarratableText).map(chapter => chapter.id)
    : scope === 'chapter' ? [chapterId]
    : scope === 'selected' ? selected : [];
  const chosen = new Set(requested);
  return book.chapters.filter(chapter => chosen.has(chapter.id) && hasNarratableText(chapter)).map(chapter => chapter.id);
}

export function sourceChapter(book: Book, sourceId: string): Chapter | undefined {
  return book.chapters.find(chapter => chapter.blocks.some(block => block.id === sourceId));
}
