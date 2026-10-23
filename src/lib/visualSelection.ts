import type {Block, Chapter, Visual} from './types';

export function visualForPassage(chapter: Chapter | undefined, visuals: Visual[], sourceId?: string):
  {kind: 'visual'; visual: Visual} | {kind: 'figure'; block: Block} | null {
  if (!chapter) return null;
  const index = chapter.blocks.findIndex(block => block.id === sourceId);
  if (index < 0) return null;
  let result: ReturnType<typeof visualForPassage> = null;
  for (const block of chapter.blocks.slice(0, index + 1)) {
    if (block.kind === 'figure') result = {kind: 'figure', block};
    const visual = visuals.filter(item => item.source_id === block.id).at(-1);
    if (visual) result = {kind: 'visual', visual};
  }
  return result;
}
