import {CheckCheck, ChevronRight, Flag, History, Image, Users} from 'lucide-react';
import {translator} from '../lib/i18n';
import {DialogHeader} from './ui/dialog';

type BookAction = 'preparation' | 'narration' | 'generations' | 'illustration' | 'stressReports';

export function BookTools({language, onAction}: {language: 'en' | 'ru'; onAction: (action: BookAction) => void}) {
  const t = translator(language);
  const rows: {action: BookAction; label: string; detail: string; icon: typeof History}[] = [
    {action: 'generations', label: t('generations'), detail: language === 'ru' ? 'Слушать, экспортировать и сохранять версии' : 'Play, export and keep different versions', icon: History},
    {action: 'narration', label: t('narration'), detail: language === 'ru' ? 'Автоматически назначенные голоса и исправления' : 'Automatic casting and optional corrections', icon: Users},
    {action: 'preparation', label: t('bookPreparation'), detail: language === 'ru' ? 'Порядок глав, исходный текст и замечания' : 'Chapter order, source text and import notes', icon: CheckCheck},
    {action: 'stressReports', label: language === 'ru' ? 'Замечания об озвучке' : 'Narration feedback', detail: language === 'ru' ? 'Ударения, паузы, темп и голоса' : 'Stress, pauses, tempo, and voices', icon: Flag},
    {action: 'illustration', label: t('addImage'), detail: language === 'ru' ? 'Иллюстрации к абзацам: импорт или Codex' : 'Paragraph images: attach or generate with Codex', icon: Image},
  ];
  return <><DialogHeader title={t('bookTools')} description={language === 'ru' ? 'Дополнительные действия для открытой книги.' : 'Additional actions for the open book.'}/>
    <div className="book-tools">{rows.map(({action, label, detail, icon: Icon}) => <button key={action} onClick={() => onAction(action)}><Icon size={20}/><span><strong>{label}</strong><small>{detail}</small></span><ChevronRight size={16}/></button>)}</div>
  </>;
}
