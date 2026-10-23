import {memo} from 'react';
import {Image as ImageIcon} from 'lucide-react';
import type {Chapter, Visual} from '../lib/types';
import {visualCaption} from '../lib/visualCaption';
import {AssetImage, BookMarkup} from './Media';
import {Button} from './ui/button';

export const IllustratedPanel = memo(function IllustratedPanel({chapter, bookId, sourceId, images, language='en', onNavigate, onIllustrate}:
  {chapter: Chapter; bookId: string; sourceId?: string; images: Map<string, Visual[]>; language?: 'en'|'ru'; onNavigate: (id: string) => void; onIllustrate: (id: string) => void}) {
  const ru=language==='ru';
  const block = chapter.blocks.find(item => item.id === sourceId);
  const visual = sourceId ? images.get(sourceId)?.at(-1) : undefined;
  const caption = visual?.caption ? visualCaption(visual.caption, language) : '';
  return <aside className="paragraph-stage" aria-label={ru?'Иллюстрация к абзацу':'Paragraph illustration'}>
    <span className="eyebrow">{ru?'ЧТЕНИЕ С ИЛЛЮСТРАЦИЯМИ':'ILLUSTRATED READING'}</span>
    {visual ? <><AssetImage kind="visual" id={visual.id} preview eager language={language} alt={caption||(ru?'Иллюстрация к этому абзацу':'Illustration for this paragraph')}/><p>{caption}</p>
      <Button variant="outline" onClick={() => onNavigate(visual.source_id)}>{ru?'Показать абзац в тексте':'Show this paragraph in text'}</Button></>
      : block?.kind === 'figure' ? <><BookMarkup book={bookId} block={block} language={language} onNavigate={onNavigate}/>
        <Button variant="outline" onClick={() => onNavigate(block.id)}>{ru?'Показать исходный рисунок в тексте':'Show original figure in text'}</Button></>
      : <div className="stage-empty"><ImageIcon size={32}/><h3>{ru?'Для этого абзаца нет иллюстрации':'No illustration for this paragraph'}</h3>
        <p>{ru?'Каждое изображение связано со своим абзацем. Отсутствие иллюстрации не останавливает озвучку.':'Each image belongs to its own paragraph. Missing artwork never stops narration.'}</p><Button variant="outline" disabled={block?.kind !== 'paragraph'} onClick={()=>{if(block)onIllustrate(block.id);}}>{ru?'Иллюстрировать абзац':'Illustrate paragraph'}</Button></div>}
    {block?.text && <p className="stage-excerpt">{block.text.slice(0,240)}{block.text.length > 240 ? '…' : ''}</p>}
    <details className="illustration-gallery"><summary>{ru?'Иллюстрированные абзацы этой главы':'Illustrated paragraphs in this chapter'}</summary>
      <div>{chapter.blocks.filter(item => images.has(item.id)).map(item => {const image=images.get(item.id)!.at(-1)!; return <button key={item.id} onClick={() => onNavigate(item.id)} title={item.text.slice(0,180)}>
        <AssetImage kind="visual" id={image.id} preview alt="" language={language}/><span>{item.text.slice(0,90)}</span></button>;})}</div>
    </details>
  </aside>;
});
