import {Check} from 'lucide-react';
import type {Settings} from '../lib/types';
import {translator} from '../lib/i18n';
import {cn} from '../lib/utils';
import {Toggle} from './ui/toggle';

export function ReadingPreferences({settings, update, showHeading = true}: {settings: Settings | null; update: (value: Partial<Settings>) => void; showHeading?: boolean}) {
  const t = translator(settings?.language || 'en');
  return <div className="settings-section">{showHeading && <h3>{t('appearance')}</h3>}<div className="theme-options">{(['light','sepia','dark'] as const).map(theme=><button key={theme} className={cn('theme-option',theme,settings?.theme===theme&&'chosen')} aria-pressed={settings?.theme===theme} onClick={()=>void update({theme})}><span>Aa</span>{t(theme)}{settings?.theme===theme&&<Check size={14}/>}</button>)}</div><label className="setting-row"><span>{t('fontSize')}</span><input type="range" min="14" max="32" value={settings?.font_size} onChange={e=>void update({font_size:Number(e.target.value)})}/><small>{settings?.font_size}px</small></label><label className="setting-row"><span>{t('lineHeight')}</span><input type="range" min="1.3" max="2.5" step=".1" value={settings?.line_height} onChange={e=>void update({line_height:Number(e.target.value)})}/><small>{settings?.line_height}</small></label><label className="setting-row"><span>{settings?.language==='ru'?'Язык интерфейса':'Interface language'}</span><select value={settings?.language} onChange={e=>void update({language:e.target.value as 'en'|'ru'})}><option value="en">English</option><option value="ru">Русский</option></select></label><Toggle label={t('follow')} checked={settings?.follow||false} onChange={follow=>void update({follow})}/></div>;
}
