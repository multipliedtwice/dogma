import {useRef, useState} from 'react';
import {Download, FileAudio, LoaderCircle, Trash2} from 'lucide-react';
import type {Book, ExportFile, Generation} from '../lib/types';
import {exportSelection, type ExportScope} from '../lib/exportSelection';
import {Button} from './ui/button';
import {DialogHeader} from './ui/dialog';
import {locale, translator} from '../lib/i18n';
import type {RemoveAction} from './AskDialog';

type Props = {
  removeAction: RemoveAction;
  book: Book;
  generation: Generation;
  chapterId: string;
  busy: boolean;
  exporting: boolean;
  language: 'en' | 'ru';
  audioTools: boolean;
  onExport: (format: string, chapters: string[]) => void;
  onSave: (file: ExportFile) => void;
  onDelete: (file: ExportFile) => Promise<unknown>;
  onCancel: () => void;
};

export function ExportPanel({book, generation, chapterId, busy, exporting, language, audioTools, removeAction, onExport, onSave, onDelete, onCancel}: Props) {
  const ru = language === 'ru';
  const t = translator(language);
  const exportButton = useRef<HTMLButtonElement>(null);
  const formatSelect = useRef<HTMLSelectElement>(null);
  const [format, setFormat] = useState('m4b');
  const [currentChapter] = useState(chapterId);
  const [scope, setScope] = useState<ExportScope>(() => exportSelection(book, generation, 'all', chapterId, language).reason ? 'ready' : 'all');
  const selection = exportSelection(book, generation, scope, currentChapter, language);
  const toolsMissing = !audioTools && !['vtt', 'srt'].includes(format) ? (ru ? 'Для экспорта аудио нужны FFmpeg и FFprobe. Субтитры можно экспортировать без них.' : 'Audio export requires FFmpeg and FFprobe. Subtitles can be exported without them.') : '';
  return <>
    <DialogHeader title={ru?'Экспорт сохранённого аудио':'Export saved audio'} description={ru?'Используется готовое аудио; речь не создаётся повторно.':'Uses existing audio. Speech is not generated again.'}/>
    <div className="modal-body">
      <label>{ru?'Формат':'Format'}<select ref={formatSelect} value={format} onChange={event => setFormat(event.target.value)}>
        <option value="m4b">{ru?'M4B · аудиокнига с главами':'M4B · audiobook with chapters'}</option>
        <option value="mp3">{ru?'MP3 · один аудиофайл':'MP3 · single audio file'}</option>
        <option value="chapters">{ru?'Главы MP3 и субтитры · ZIP':'MP3 chapters + subtitles · ZIP'}</option>
        <option value="vtt">{ru?'Субтитры WebVTT':'WebVTT subtitles'}</option><option value="srt">{ru?'Субтитры SRT':'SRT subtitles'}</option>
      </select></label>
      <label>{ru?'Главы':'Chapters'}<select value={scope} onChange={event => setScope(event.target.value as ExportScope)}>
        <option value="all">{ru?'Все главы в этой озвучке':'All chapters in this generation'} · {selection.totalCount}</option>
        <option value="ready">{ru?'Только завершённые главы':'Completed chapters only'} · {selection.readyCount}</option>
        <option value="chapter">{ru?'Только текущая глава':'Current chapter only'} · {book.chapters.find(chapter => chapter.id === currentChapter)?.title || (ru?'Недоступно':'Not available')}</option>
      </select></label>
      <p className="helper" role="status">{toolsMissing || selection.reason || (ru?`Выбрано завершённых глав: ${selection.chapters.length}. Перед экспортом сохранённое аудио будет проверено.`:`${selection.chapters.length} completed chapter${selection.chapters.length === 1 ? '' : 's'} selected. Saved audio is checked again before export.`)}</p>
      <div className="dialog-actions">{exporting && <Button variant="outline" onClick={onCancel}>{t('cancelExport')}</Button>}<Button ref={exportButton} disabled={busy || Boolean(toolsMissing) || Boolean(selection.reason) || !selection.chapters.length}
        onClick={() => onExport(format, selection.chapters)}>{busy ? <LoaderCircle className="spin"/> : <Download/>}{ru?'Экспортировать':'Export'}</Button></div>
      {generation.exports.length > 0 && <details className="export-history"><summary>{ru?'Предыдущие экспорты':'Previous exports'} ({generation.exports.length})</summary>
        {generation.exports.map(item => <div className="saved-export" key={item.id}><FileAudio size={17}/>
          <span>{item.format.toUpperCase()} · {item.created && new Date(item.created).toLocaleString(locale(language))}</span>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => onSave(item)}>{ru?'Сохранить ещё раз':'Save again'}</Button>
          <Button size="icon" variant="ghost" disabled={busy} aria-label={ru?'Удалить сохранённый экспорт':'Delete saved export'} onClick={() => void removeAction({title: t('deleteExportTitle'), message: t('deleteExportMessage'), confirm: t('delete'), destructive: true}, '.saved-export', () => [exportButton.current, formatSelect.current], () => onDelete(item))}><Trash2/></Button>
        </div>)}
      </details>}
    </div>
  </>;
}
