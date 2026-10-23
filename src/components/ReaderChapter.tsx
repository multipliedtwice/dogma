import {memo,type MouseEvent} from 'react';
import {Bookmark, Image as ImageIcon, Pencil, Play} from 'lucide-react';
import {AssetImage, BookMarkup} from './Media';
import type {Block, Chapter, Visual} from '../lib/types';
import {cn} from '../lib/utils';
import {titleSource} from '../lib/readerIndexes';
import {speakable} from '../lib/preparation';
import {visualCaption} from '../lib/visualCaption';
import {blockKindLabel, preview, translator} from '../lib/i18n';
import {stressSelection,type StressSelection} from '../lib/stressSelection';

export type PassageAction = 'listen' | 'bookmark' | 'correct' | 'illustrate';
type PassageProps = {block: Block; ordinal: number; bookId: string; speaking: boolean; selected: boolean; bookmarked?: boolean; failed?: boolean; title?: boolean; visual?: Visual;
  language?: 'en'|'ru'; onSelect: (source: string) => void; onNavigate: (source: string) => void; onAction: (source: string, action: PassageAction) => void;
  onStressSelection: (selection: StressSelection, point:{x:number;y:number}) => void; listener?: boolean};

const Passage = memo(function Passage({block, ordinal, bookId, speaking, selected, bookmarked = false, failed = false, title = false, visual, language='en', onSelect, onNavigate, onAction, onStressSelection, listener=false}: PassageProps) {
  const ru=language==='ru';
  const reach = selected ? 0 : -1;
  const narrated = speakable(block);
  const subject = block.text ? preview(block.text, 60) : blockKindLabel(ru ? 'ru' : 'en', block.kind);
  const named = (action: string) => `${action}: ${ru ? 'отрывок' : 'passage'} ${ordinal}, ${subject}`;
  const caption = visual ? visualCaption(visual.caption, language) : '';
  return <section id={block.id} tabIndex={title ? -1 : 0} data-source-id={block.id} aria-label={title ? block.text : undefined}
    onKeyDown={event => { if (event.key === 'Enter' && event.target === event.currentTarget) { event.preventDefault(); onSelect(block.id); } }}
    className={cn('source-block', speaking && 'speaking', selected && 'selected-source', failed && 'failed-source', block.kind === 'heading' && 'source-heading')}
    onContextMenu={(event:MouseEvent<HTMLElement>)=>{const selection=stressSelection(event.currentTarget,block.text,window.getSelection());if(!selection)return;event.preventDefault();event.stopPropagation();onStressSelection(selection,{x:event.clientX,y:event.clientY});}}
    onClick={() => onSelect(block.id)}>
    <BookMarkup block={block} book={bookId} language={language} onNavigate={onNavigate}/>
    {failed && <p className="passage-flag" role="note">{translator(language)('failedPassage')}</p>}
    {visual && <figure className="paragraph-illustration" data-illustration-source={block.id}>
      <AssetImage kind="visual" id={visual.id} preview language={language} alt={caption || (ru?'Иллюстрация к этому абзацу':'Illustration for this paragraph')}/>
      <figcaption><span>{caption || (ru?'Иллюстрация к абзацу':'Paragraph illustration')}</span>
        <button type="button" onClick={event => {event.stopPropagation(); onNavigate(block.id);}}>{ru?'Показать в тексте':'Show in text'}</button>
      </figcaption>
    </figure>}
    <div className={cn('passage-actions',listener&&'listener-actions')}>
      <button tabIndex={reach} title={ru?'Слушать отсюда':'Listen from here'} aria-label={named(ru?'Слушать отрывок':'Listen to passage')} disabled={!narrated} onClick={event => {event.stopPropagation(); onAction(block.id,'listen');}}><Play size={14}/></button>
      <button tabIndex={reach} title={bookmarked?(ru?'Удалить закладку':'Remove bookmark'):(ru?'Закладка':'Bookmark')} aria-label={named(bookmarked?(ru?'Удалить закладку':'Remove bookmark'):(ru?'Добавить отрывок в закладки':'Bookmark passage'))} onClick={event => {event.stopPropagation(); onAction(block.id,'bookmark');}}><Bookmark size={14} fill={bookmarked?'currentColor':'none'}/></button>
      <button tabIndex={reach} title={ru?'Исправить озвучку':'Correct narration'} aria-label={named(ru?'Исправить отрывок':'Correct passage')} disabled={!narrated} onClick={event => {event.stopPropagation(); onAction(block.id,'correct');}}><Pencil size={14}/></button>
      <button tabIndex={reach} title={ru?'Иллюстрация':'Illustration'} aria-label={named(ru?'Иллюстрировать абзац':'Illustrate paragraph')} disabled={block.kind !== 'paragraph' || !block.text.trim()} onClick={event => {event.stopPropagation(); onAction(block.id,'illustrate');}}><ImageIcon size={14}/></button>
    </div>
  </section>;
});

export const ReaderChapter = memo(function ReaderChapter({chapter, bookId, speaking, selected, images, bookmarks, failed, language='en', onSelect, onNavigate, onAction, onStressSelection, listener=false}:
  {chapter: Chapter; bookId: string; speaking?: string; selected: string; images: Map<string, Visual[]>; bookmarks?: Set<string>; failed?: Set<string>;
   language?: 'en'|'ru'; onSelect: (source: string) => void; onNavigate: (source: string) => void; onAction: (source: string, action: PassageAction) => void;
   onStressSelection: (selection: StressSelection, point:{x:number;y:number}) => void; listener?:boolean}) {
  return <div className="book-text">{chapter.blocks.map((block, index) => <Passage key={block.id} block={block} ordinal={index + 1} bookId={bookId}
    speaking={speaking === block.id} selected={selected === block.id} bookmarked={bookmarks?.has(block.id)} failed={failed?.has(block.id)} title={titleSource(chapter) === block.id} visual={images.get(block.id)?.at(-1)} language={language}
    onSelect={onSelect} onNavigate={onNavigate} onAction={onAction} onStressSelection={onStressSelection} listener={listener}/>)}</div>;
});
