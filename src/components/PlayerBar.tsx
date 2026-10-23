import {ChevronDown, Headphones, LoaderCircle, LocateFixed, Pause, Play, RotateCcw, Settings2, SkipBack, SkipForward, Timer} from 'lucide-react';
import {useMemo, useRef, useSyncExternalStore, type ReactNode} from 'react';
import type {createPlaybackClock} from '../lib/playbackClock';
import type {Book, Generation, Segment, Settings} from '../lib/types';
import type {PlaybackSnapshot} from '../lib/playback';
import {chapterTimeline, timelineKeyTarget, timelineTarget} from '../lib/playerView';
import {cn, duration} from '../lib/utils';
import {translator} from '../lib/i18n';
import {preparationPercent} from '../lib/castReview';
import {AssetImage} from './Media';
import {focusIfLost} from '../lib/removalFocus';

export type PlayerBarProps = {
  book: Book;
  generation: Generation;
  player: PlaybackSnapshot & {sleepAt: number; clock?: ReturnType<typeof createPlaybackClock>};
  settings: Settings | null;
  status: string;
  error?: string;
  warning?: string;
  warningDetail?: string;
  statusAction: ReactNode;
  onToggle: () => void;
  onSkipOpening?: () => void;
  onSkip: (delta: number) => void;
  onSelect: (id: string, play: boolean, offset?: number) => void;
  onSettings: () => void;
  onHistory: () => void;
  onUpdate: (settings: Partial<Settings>) => void;
  showFollow?: boolean;
  onFollow?: () => void;
  listener?: boolean;
};

export function PlayerBar({book, generation, player: control, settings, status, error = '', warning = '', warningDetail = '', statusAction, onToggle, onSkip, onSkipOpening, onSelect, onSettings, onHistory, onUpdate, showFollow=false, onFollow, listener=false}: PlayerBarProps) {
  const live = useSyncExternalStore(control.clock?.subscribe || (() => () => {}), control.clock?.getSnapshot || (() => control));
  const player = {...control, ...live};
  const t = translator(settings?.language || 'en');
  const ru = settings?.language === 'ru';
  const current = useMemo(() => generation.segments.find(segment => segment.id === player.segment?.id) ?? null, [generation.segments, player.segment?.id]);
  const base = useMemo(() => chapterTimeline(generation, player.segment, 0), [generation.segments, player.segment?.id]);
  const bookTimeline = {...base, elapsed: base.elapsed + (current && base.seekable.some(segment => segment.id === current.id) ? Math.min(Math.max(0, player.position), current.duration) : 0)};
  const timeline = player.opening ? {...bookTimeline, elapsed: player.opening.position, duration: player.opening.duration} : bookTimeline;
  const openingLabel = ru ? 'Заставка Dogma' : 'Dogma intro';
  const chapter = book.chapters.find(item => item.id === timeline.chapterId);
  const play = player.playing || player.waiting;
  const progress = useMemo(() => preparationPercent(generation), [generation.segments, generation.status, generation.analysis]);
  const waitingForRender = !player.opening && player.waiting && current?.status !== 'ready' && current?.status !== 'review' && generation.status === 'rendering';
  const shownStatus = waitingForRender ? (ru ? 'Ожидание озвучки этого отрывка…' : 'Waiting for this passage to be generated…')
    : player.premixed && player.waiting ? (ru ? 'Буферизация аудио…' : 'Buffering audio…') : status;
  const bufferedUntil = player.premixed && !player.opening && Number.isFinite(player.bufferedSeconds)
    ? Math.min(timeline.duration, timeline.elapsed + Math.max(0, player.bufferedSeconds || 0)) : timeline.elapsed;
  const playedPercent = timeline.duration ? timeline.elapsed / timeline.duration * 100 : 0;
  const bufferedPercent = timeline.duration ? bufferedUntil / timeline.duration * 100 : 0;
  const clockLabel = player.opening ? openingLabel : timeline.mode === 'passage' ? t('passageTimeline') : timeline.complete ? t('chapterTimeline') : t('preparedTimeline');
  const seek = (seconds: number) => {
    if (player.opening) return;
    const target = timelineTarget(timeline.seekable, seconds);
    if (target) onSelect(target.id, play, target.offset);
  };
  const playButton = useRef<HTMLButtonElement>(null);
  const select = (segment: Segment | null) => { if (!segment) return; onSelect(segment.id, play, 0); focusIfLost(() => playButton.current); };
  return <footer className="player-dock" aria-label={t('audiobookPlayer')}>
    <div className="dock-main">
      <button className="dock-book" onClick={onHistory} disabled={listener} title={listener?generation.name:t('generations')} aria-label={`${t('generations')}: ${generation.name}`}>
        <span className="dock-cover">{book.cover ? <AssetImage kind="book" id={book.cover} book={book.id} alt="" language={ru ? 'ru' : 'en'}/> : <Headphones size={22}/>}</span>
        <span className="dock-book-label"><strong>{book.title}</strong><span>{generation.name}<ChevronDown size={12}/></span></span>
      </button>
      <div className="dock-transport" role="group" aria-label={t('playbackControls')}>
        <button aria-label={t('previousChapter')} title={t('previousChapter')} disabled={!timeline.previous} onClick={() => select(timeline.previous)}><SkipBack size={18}/></button>
        <button aria-label={t('back15')} title={t('back15')} disabled={!current} onClick={() => onSkip(-15)}><RotateCcw size={20}/><span>15</span></button>
        <button ref={playButton} className="dock-play" aria-label={play ? t('pause') : t('play')} title={play ? t('pause') : t('play')} disabled={!current && !player.opening} onClick={onToggle}>{player.waiting ? <LoaderCircle size={23} className="spin"/> : player.playing ? <Pause size={21} fill="currentColor"/> : <Play size={21} fill="currentColor"/>}</button>
        <button aria-label={t('forward15')} title={t('forward15')} disabled={!current} onClick={() => onSkip(15)}><RotateCcw size={20} className="flip"/><span>15</span></button>
        <button aria-label={t('nextChapter')} title={t('nextChapter')} disabled={!timeline.next} onClick={() => select(timeline.next)}><SkipForward size={18}/></button>
      </div>
      <div className="dock-options">
        <select aria-label={t('playbackSpeed')} title={t('playbackSpeed')} value={settings?.playback_rate || 1} onChange={event => onUpdate({playback_rate: Number(event.target.value)})}>{[.75, 1, 1.25, 1.5, 1.75, 2].map(rate => <option key={rate} value={rate}>{rate}×</option>)}</select>
        {showFollow && <button className="follow-toggle" aria-label={ru ? 'Следить' : 'Follow'} title={ru ? 'Следить' : 'Follow'} onClick={onFollow}><LocateFixed size={19}/></button>}
        <button aria-label={t('playbackSettings')} title={player.sleepAt ? t('sleepActive') : t('playbackSettings')} onClick={onSettings}>{player.sleepAt ? <Timer size={19}/> : <Settings2 size={19}/>}</button>
      </div>
    </div>
    <div className="dock-timeline-heading"><span title={chapter?.title}>{player.opening ? openingLabel : chapter?.title || t('chapters')}</span><small>{player.opening ? <button onClick={() => {onSkipOpening?.(); focusIfLost(() => playButton.current);}}>{ru ? 'Пропустить заставку' : 'Skip intro'}</button> : player.premixed && (player.bufferedSeconds || 0) > 0 ? `${ru ? 'В буфере' : 'Buffered'} ${duration(player.bufferedSeconds || 0)}` : clockLabel}</small></div>
    <div className="dock-timeline">
      <span>{duration(timeline.elapsed)}</span>
      <input type="range" min="0" max={Math.max(.01, timeline.duration)} step=".05" value={timeline.elapsed} disabled={Boolean(player.opening) || !timeline.seekable.length}
        aria-label={clockLabel} aria-valuetext={`${duration(timeline.elapsed)} / ${duration(timeline.duration)}`}
        style={{background: `linear-gradient(to right, hsl(var(--primary)) 0%, hsl(var(--primary)) ${playedPercent}%, hsl(var(--muted-foreground) / .45) ${playedPercent}%, hsl(var(--muted-foreground) / .45) ${bufferedPercent}%, hsl(var(--border)) ${bufferedPercent}%)`}}
        onKeyDown={event => {const target = timelineKeyTarget(event.key, timeline.elapsed, timeline.duration); if (target === null) return; event.preventDefault(); seek(target);}}
        onChange={event => seek(Number(event.target.value))}/>
      <span>{timeline.duration ? duration(timeline.duration) : '—:—'}</span>
    </div>
    <div className="dock-status"><span title={shownStatus}>{shownStatus}</span><span className="dock-status-action">{statusAction}</span></div>
    {(error || warning) && <p className={cn('dock-note', error ? 'dock-error' : 'dock-warning')} role={error ? 'alert' : 'note'} title={error || warningDetail || warning}>{error || warning}</p>}
    {generation.status !== 'ready' && <progress className="dock-preparation" value={progress} max={100} aria-label={ru ? 'Подготовка аудио' : 'Audio preparation'}/>}
  </footer>;
}
