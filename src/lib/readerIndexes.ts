import type {Book, Chapter, Generation, Segment, Visual} from './types';

export function bookIndex(book: Book | null) {
  const sources = new Map<string, {chapter: Chapter; block: Chapter['blocks'][number]}>();
  const chapters = new Map<string, Chapter>();
  for (const chapter of book?.chapters || []) {
    chapters.set(chapter.id, chapter);
    for (const block of chapter.blocks) sources.set(block.id, {chapter, block});
  }
  return {sources, chapters};
}

export function segmentIndex(generation: Generation | null) {
  const byId = new Map<string, Segment>();
  const bySource = new Map<string, Segment[]>();
  const byChapter = new Map<string, Segment[]>();
  const progress = new Map<string, {total: number; ready: number; rendering: boolean}>();
  let ready = 0;
  for (const segment of generation?.segments || []) {
    byId.set(segment.id, segment);
    const source = bySource.get(segment.source_id) || [];
    source.push(segment); bySource.set(segment.source_id, source);
    const chapter = byChapter.get(segment.chapter_id) || [];
    chapter.push(segment); byChapter.set(segment.chapter_id, chapter);
    const state = progress.get(segment.chapter_id) || {total: 0, ready: 0, rendering: false};
    state.total++;
    if (segment.status === 'ready') { state.ready++; ready++; }
    state.rendering ||= segment.status === 'rendering';
    progress.set(segment.chapter_id, state);
  }
  return {byId, bySource, byChapter, progress, ready};
}

export function paragraphImages(visuals: Visual[]) {
  const bySource = new Map<string, Visual[]>();
  for (const visual of visuals) {
    const versions = bySource.get(visual.source_id) || [];
    versions.push(visual); bySource.set(visual.source_id, versions);
  }
  return bySource;
}

export function illustrationSources(chapters: Chapter[], selected: string, scope: 'paragraph' | 'chapter' | 'book') {
  return chapters.flatMap(chapter => chapter.blocks)
    .filter(block => block.kind === 'paragraph' && block.text.trim() && (scope !== 'paragraph' || block.id === selected))
    .map(block => block.id);
}

export function preserveVisualRevision(current: Book | null, next: Book): Book {
  return current?.id === next.id && (current.visual_revision || 0) > (next.visual_revision || 0)
    ? {...next, visuals: current.visuals, visual_revision: current.visual_revision} : next;
}

export function acceptVisualRefresh(current: Book | null, update: {id: string; revision: number; visuals: Visual[] | null}): Book | null {
  if (!current || update.id !== current.id || !Number.isSafeInteger(update.revision) || update.revision < (current.visual_revision || 0) || !update.visuals) return current;
  if (update.revision === current.visual_revision) return current;
  return {...current, visuals: update.visuals, visual_revision: update.revision};
}

export function acceptVisualPoll(current: Book | null, update: {id:string;revision:number;visuals:Visual[];full:boolean;removed_visual_ids:string[]}): Book | null {
  if (!current || update.id !== current.id || !Number.isSafeInteger(update.revision) || update.revision < (current.visual_revision || 0)) return current;
  if (update.full) return {...current,visuals:update.visuals,visual_revision:update.revision};
  const changed=new Map(update.visuals.map(visual=>[visual.id,visual]));
  const removed=new Set(update.removed_visual_ids);
  const visuals=current.visuals.filter(visual=>!removed.has(visual.id)).map(visual=>changed.get(visual.id)??visual);
  const known=new Set(visuals.map(visual=>visual.id));
  for(const visual of update.visuals)if(!known.has(visual.id))visuals.push(visual);
  visuals.sort((a,b)=>a.created.localeCompare(b.created));
  return {...current,visuals,visual_revision:update.revision};
}

export function titleSource(chapter: Pick<Chapter, 'blocks'> | null | undefined): string {
  const first = chapter?.blocks[0];
  return first?.kind === 'heading' ? first.id : '';
}
