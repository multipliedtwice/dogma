import {BookOpen, CheckCircle2, Info} from 'lucide-react';
import type {Book} from '../lib/types';
import {Button} from './ui/button';
import {DialogHeader} from './ui/dialog';
import {count, localMessage} from '../lib/i18n';

export function BookPreparation({book, language, onChapter}: {book:Book; language:'en'|'ru'; onChapter:(id:string)=>void}) {
  const ru = language === 'ru';
  const report = book.preparation;
  return <>
    <DialogHeader title={ru ? 'Подготовка книги' : 'Book preparation'} description={ru ? 'Порядок глав и исходный текст проверяются локально, без речевой модели.' : 'Chapter order and source text are prepared locally, without a speech model.'}/>
    <div className="modal-scroll">
      <div className="info-box"><CheckCircle2 size={20}/><div>
        <strong>{book.title}</strong><p>{book.author} · {book.language} · {book.format.toUpperCase()}</p>
        <p>{ru ? 'Исходный файл сохранён без изменений.' : 'The original file is preserved unchanged.'}</p>
      </div></div>
      {report ? <>
        <div className="generation-stats preparation-stats">
          <span>{count(language, report.chapters.length, 'sections')}</span>
          <span>{count(language, report.narration_characters, 'narrationCharacters')}</span>
          <span>{count(language, report.base_segments, 'baseSegments')}</span>
        </div>
        <p className="helper">{ru ? 'Это границы до анализа персонажей, а не готовые голоса или оценка длительности. Состав озвучки можно изменить при создании версии.' : 'These are boundaries before character analysis, not generated voices or a duration estimate. Choose narration content when creating a generation.'}</p>
        <div className="preparation-chapters">{report.chapters.map((chapter, index) => <div className="preparation-chapter" key={chapter.id}>
          <div><strong>{index + 1}. {chapter.title}</strong>
            <p>{count(language, chapter.characters, 'characters')} · {count(language, chapter.base_segments, 'segments')}</p>
            <small>{chapter.default_narration ? (ru ? 'В озвучке по умолчанию' : 'Included by default') : (ru ? 'Для чтения; включается отдельно' : 'Reading only; select explicitly')}</small>
          </div>
          <Button size="icon" variant="ghost" title={ru ? 'Открыть раздел' : 'Read section'} aria-label={`${ru ? 'Открыть' : 'Read'} ${chapter.title}`} onClick={() => onChapter(chapter.id)}><BookOpen/></Button>
        </div>)}</div>
      </> : <p className="helper">{ru ? 'В старом импорте нет отчёта подготовки. Сохранённые главы и озвучки не изменены.' : 'This older import has no preparation report. Saved chapters and audio are unchanged.'}</p>}
      {book.warnings.length > 0 && <div className="preparation-notes"><h3><Info size={16}/>{ru ? 'Примечания импорта' : 'Import notes'}</h3>{book.warnings.map(warning => <p key={warning}>{localMessage(language, warning)}</p>)}</div>}
    </div>
  </>;
}
