import type {Settings} from '../lib/types';
import type {MusicStatus} from '../lib/music';
import {translator} from '../lib/i18n';
import {sleepDeadline} from '../lib/playerView';
import {Toggle} from './ui/toggle';
import {Button} from './ui/button';
import {DialogHeader} from './ui/dialog';

function musicSummary(status: MusicStatus | null, ru: boolean) {
  if (!status) return '';
  if (!status.available) return status.pack_error || (ru ? 'Музыкальный пакет не установлен.' : 'No music pack is installed.');
  const ready = status.cues.filter(cue => cue.status === 'ready').length;
  const failed = status.cues.filter(cue => cue.status === 'failed').length;
  if (!status.cues.length) return ru ? 'Музыка создаётся после завершения озвучки.' : 'Music is created after narration finishes.';
  const base = ru ? `Готово музыкальных фрагментов: ${ready} из ${status.cues.length}.` : `${ready} of ${status.cues.length} music takes ready.`;
  return failed ? base + (ru ? ` Не прошли проверку: ${failed}.` : ` ${failed} failed checks.`) : base;
}

export function PlaybackSettings({settings, sleepAt, onSleep, update, music = null, onRetryMusic, attribution = '', opening = false}: {settings: Settings | null; sleepAt: number; onSleep: (value: number) => void; update: (value: Partial<Settings>) => void; music?: MusicStatus | null; onRetryMusic?: () => void; attribution?: string; opening?: boolean}) {
  const t = translator(settings?.language || 'en');
  const ru = settings?.language === 'ru';
  const musicOn = settings?.music ?? true;
  const failed = music?.cues.some(cue => cue.status === 'failed');
  return <>
    <DialogHeader title={t('playbackSettings')} description={ru ? 'Скорость и громкость меняются без повторной озвучки.' : 'Speed and volume changes use saved audio, without regenerating narration.'}/>
    <div className="modal-body">
      <label className="setting-row"><span>{t('playbackSpeed')}</span><select value={settings?.playback_rate || 1} onChange={event => update({playback_rate: Number(event.target.value)})}>{[.75, 1, 1.25, 1.5, 1.75, 2].map(value => <option key={value} value={value}>{value}×</option>)}</select></label>
      <label className="setting-row"><span>{t('volume')}</span><input type="range" min="0" max="1" step=".05" value={settings?.volume ?? .85} onChange={event => update({volume: Number(event.target.value)})}/><small>{Math.round((settings?.volume ?? .85) * 100)}%</small></label>
      <label className="setting-row"><span>{t('sleep')}</span><select value={sleepAt ? 'active' : 'off'} onChange={event => { const next = sleepDeadline(event.target.value, Date.now()); if (next !== null) onSleep(next); }}>
        <option value="off">{t('off')}</option>{sleepAt > 0 && <option value="active" disabled>{ru ? 'Пауза в' : 'Pause at'} {new Date(sleepAt).toLocaleTimeString(ru ? 'ru' : 'en', {hour: '2-digit', minute: '2-digit'})}</option>}
        {[15, 30, 45, 60].map(value => <option key={value} value={value}>{value} {ru ? 'мин' : 'min'}</option>)}
      </select></label>
      <Toggle label={t('follow')} checked={settings?.follow || false} onChange={follow => update({follow})}/>
      <p className="helper">{ru ? 'Текст книги следует за озвучкой. Точное движение по словам доступно при сохранённых проверенных таймкодах.' : 'The book text follows the narration. Precise word-level movement requires saved, validated alignment timestamps.'}</p>
      <Toggle label={t('music')} checked={musicOn} onChange={music => update({music})}/>
      <label className="setting-row"><span>{t('musicVolume')}</span><input type="range" min="0" max="1" step=".05" disabled={!musicOn} value={settings?.music_volume ?? 1} onChange={event => update({music_volume: Number(event.target.value)})}/><small>{Math.round((settings?.music_volume ?? 1) * 100)}%</small></label>
      <p className="helper">
        {musicOn
          ? (ru ? 'Тихая фоновая музыка и звуки окружения по настроению каждой сцены, с плавными переходами между ними. Озвучка не меняется. ' : 'Quiet background music and ambience that follow the mood of each scene and crossfade between them. Narration is unchanged. ') + musicSummary(music, ru)
          : (ru ? 'Фоновая музыка выключена: она не играет, не создаётся и не попадает в экспорт.' : 'Background music is off: it does not play, is not generated and is not exported.')}
        {opening && <> {ru ? 'Фирменная заставка Dogma всегда звучит с музыкой, в том числе в экспорте.' : 'The Dogma opening always includes music, including in exports.'}</>}
        {musicOn && failed && onRetryMusic && <> <Button variant="outline" size="sm" onClick={onRetryMusic}>{ru ? 'Повторить' : 'Retry'}</Button></>}
        {(musicOn || opening) && attribution && <><br/><small>{attribution}</small></>}
      </p>
    </div>
  </>;
}
