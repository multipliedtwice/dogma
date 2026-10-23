import {useEffect, useMemo, useState, type ReactNode} from 'react';
import {AlertTriangle, BookOpen, LoaderCircle, Pause, Pencil, Play, ShieldCheck, Users} from 'lucide-react';
import type {Block, Chapter, Character, Generation, PackVoice, Segment} from '../lib/types';
import {api} from '../lib/api';
import {aliasesOf, castGroups, characterTraits, flaggedPassages, holdQueue, holdReason, holdReasons, presentationLine, kindChoices, narratorChoices, narratorVoice, noticeViews, renderEstimate, speakerLabels, speechShares, voiceChoices} from '../lib/castReview';
import {characterName, count, localMessage, locale, operationMessage, preview, translator} from '../lib/i18n';
import {formsPreview, markWord, stressWords, withStress} from '../lib/nameStress';
import {Button} from './ui/button';
import {DialogHeader} from './ui/dialog';

type Props = {generation: Generation; chapters: Chapter[]; passages: Map<string, {chapter: Chapter; block: Block}>; language: 'en' | 'ru'; busy: boolean; blocked: string; previewing: string;
  onPreview: (segmentId: string) => void; onNavigate: (sourceId: string) => void; onRename: (character: Character) => void; onMerge: (source: Character, target: Character) => void;
  onVoice: (character: Character, voiceId: string) => void; onNameStress: (character: Character, stressed: string) => void; onKind: (character: Character, kind: string) => void; onNarrator: (chapterId: string, voiceId: string) => void; onStart: () => void; onReview: (segment: Segment) => void};
type Listed = {key: string; source: string; text?: string; note?: ReactNode};
const LISTED = 50;

export function CastPanel({generation, chapters, passages, language, busy, blocked, previewing, onPreview, onNavigate, onRename, onMerge, onVoice, onNameStress, onKind, onNarrator, onStart, onReview}: Props) {
  const ru = language === 'ru';
  const t = translator(language);
  const review = generation.status === 'review';
  const [voices, setVoices] = useState<PackVoice[] | null>(null);
  const [voiceError, setVoiceError] = useState('');
  useEffect(() => {
    if (!review) return;
    let live = true;
    api<PackVoice[]>('pack_voices', {id: generation.id}).then(next => { if (live) { setVoices(next); setVoiceError(''); } }, reason => { if (live) setVoiceError(operationMessage(reason, language)); });
    return () => { live = false; };
  }, [generation.id, review]);
  const shares = useMemo(() => speechShares(generation), [generation.analysis, generation.segments]);
  const groups = castGroups(generation.cast, shares);
  const labels = speakerLabels(language, generation.cast);
  const notices = noticeViews(language, generation.analysis, generation.cast);
  const flagged = flaggedPassages(generation, id => passages.has(id));
  const holds = holdQueue(generation);
  const estimate = renderEstimate(generation.segments, holds.flatMap(item => item.segment ? [item.segment.id] : []));
  const reasons = holdReasons(language, generation.analysis);
  const rendered = chapters.filter(chapter => generation.config.chapters.includes(chapter.id));
  const conflicts = generation.analysis.casting_conflicts ?? [];
  const percent = new Intl.NumberFormat(locale(language), {style: 'percent', maximumSignificantDigits: 2});
  const editable = review && !busy;
  const sample = (id: string) => generation.segments.find(segment => segment.speaker === id && segment.status === 'ready');
  const option = (voice: Pick<PackVoice, 'id' | 'name' | 'group'>) => <option key={voice.id} value={voice.id}>{voice.name || voice.id}{voice.group ? ` · ${voice.group}` : ''}</option>;
  const choices = (current: string, list: PackVoice[]) => list.some(voice => voice.id === current) || !current ? list : [{id: current, name: generation.cast.find(character => character.voice_id === current)?.voice_name || current, role: '', family: '', group: '', gender: '', age_bands: []}, ...list];
  const stressNames = generation.analysis.pronunciation?.names ?? {};
  const nameStressView = (character: Character) => {
    const entry = stressNames[character.id];
    if (!entry) return null;
    const label = ru ? 'Ударение в имени' : 'Name stress';
    const note = entry.source === 'listener' ? (ru ? 'выбрано вами' : 'your choice')
      : entry.confidence === 'low' ? (ru ? 'догадка, стоит проверить' : 'a guess, worth a check') : (ru ? 'автоматически' : 'automatic');
    const words = stressWords(entry.stressed);
    return <div className="name-stress">
      <span className="name-stress-label">{label} · {note}</span>
      {!words.length && <span className="name-stress-fixed">{entry.stressed}</span>}
      {words.map(word => <div key={word.index} className="name-stress-word">
        <div role="radiogroup" aria-label={`${label}: ${word.plain}`} className="toggle-group">
          {word.options.map(letter => <button key={letter} type="button" role="radio" aria-checked={letter === word.chosen}
            className={letter === word.chosen ? 'active' : ''} disabled={!review || !editable}
            onClick={() => { if (letter !== word.chosen) onNameStress(character, withStress(entry.stressed, word.index, letter)); }}>
            {markWord(word.plain, letter)}</button>)}
        </div>
        {word.chosen >= 0 && <small>{formsPreview(word.plain, word.chosen).join(' · ')}</small>}
      </div>)}
      {entry.basis && entry.source === 'model' && <small className="name-stress-basis">{entry.basis}</small>}
    </div>;
  };
  const voiceLine = (character: Character) => [character.voice_name ? `${t('voice')}: ${character.voice_name}` : character.voice_id ? (ru ? 'Голос назначен' : 'Voice assigned') : (ru ? 'Голос ещё не назначен' : 'Voice assignment pending'),
    character.group, character.family && character.family !== character.voice_id ? `${ru ? 'семейство' : 'family'} ${character.family}` : ''].filter(Boolean).join(' · ');
  const passageList = (items: Listed[]) => <ul className="cast-passages">{items.slice(0, LISTED).map(({key, source, text, note}) => { const target = passages.get(source); return <li key={key}><button type="button" onClick={() => onNavigate(source)}>{preview(text || target?.block.text || source, 90)}</button>{target && <small> · {target.chapter.title}</small>}{note}</li>; })}
    {items.length > LISTED && <li><small>{ru ? `и ещё ${items.length - LISTED}` : `and ${items.length - LISTED} more`}</small></li>}</ul>;
  const degradedNote = (reasons: string[]) => <small className="cast-reason" title={reasons.length ? reasons.map(reason => localMessage(language, reason)).join('\n') : undefined}>{t('degradedPassage')}</small>;
  const previewButton = (character: Character) => {
    const segment = sample(character.id);
    const name = characterName(language, character);
    return <Button size="icon" variant="ghost" title={segment ? (ru ? 'Прослушать без смены места прослушивания' : 'Preview without moving your listening position') : (ru ? 'Готового отрывка этого голоса пока нет' : 'No prepared passage for this voice yet')}
      aria-label={`${ru ? 'Прослушать' : 'Preview'} ${name}`} disabled={!segment} onClick={() => segment && onPreview(segment.id)}>{segment && previewing === segment.id ? <Pause/> : <Play/>}</Button>;
  };
  const row = (character: Character, index: number) => {
    const aliases = aliasesOf(character);
    const share = shares.get(character.id) ?? 0;
    const traits = characterTraits(language, character);
    const options = voices ? choices(character.voice_id || '', voiceChoices(character, voices)) : [];
    const others = generation.cast.filter(other => other.id !== 'narrator' && other.id !== character.id);
    return <div className="character-row" key={character.id}>
      <div className={'character-avatar tone-' + index % 4}>{character.name.slice(0, 1)}</div>
      <div className="character-details">
        <strong>{characterName(language, character)}</strong>
        {aliases.length > 0 && <small className="character-aliases">{ru ? 'Также: ' : 'Also: '}{aliases.join(', ')}</small>}
        <p>{[traits, share > 0 ? `${percent.format(share)} ${ru ? 'текста' : 'of text'}` : ''].filter(Boolean).join(' · ')}</p>
        <p>{voiceLine(character)}</p>
        {nameStressView(character)}
        {(character.voice_compatible === false || character.voice_reused) && <p className="character-flags">
          {character.voice_compatible === false && <span className="flag-warning">{ru ? 'Профиль голоса не соответствует чертам персонажа' : 'Voice profile does not match the character traits'}</span>}
          {character.voice_reused && <span>{ru ? 'Голос используется и другим персонажем' : 'Voice is also used by another character'}</span>}
        </p>}
        {review && <div className="character-actions">
          {share > 0 && <select aria-label={`${ru ? 'Тип персонажа:' : 'Kind for'} ${characterName(language, character)}`} value={character.kind || 'person'} disabled={!editable}
            onChange={event => onKind(character, event.target.value)}>{kindChoices(language).map(kind => <option key={kind.value} value={kind.value}>{kind.label}</option>)}</select>}
          {share > 0 && <select aria-label={`${ru ? 'Голос для' : 'Voice for'} ${characterName(language, character)}`} value={character.voice_id || ''} disabled={!editable || !voices || options.length < 2}
            onChange={event => onVoice(character, event.target.value)}>{voices ? options.map(option) : <option value={character.voice_id || ''}>{character.voice_name || character.voice_id || '—'}</option>}</select>}
          {others.length > 0 && <select aria-label={`${ru ? 'Объединить' : 'Merge'} ${characterName(language, character)}`} value="" disabled={!editable}
            onChange={event => { const target = others.find(other => other.id === event.target.value); if (target) onMerge(character, target); }}>
            <option value="">{ru ? 'Объединить с…' : 'Merge into…'}</option>{others.map(other => <option key={other.id} value={other.id}>{labels.get(other.id) ?? other.name}</option>)}</select>}
          <Button size="sm" variant="ghost" disabled={!editable} onClick={() => onRename(character)}><Pencil/>{t('rename')}</Button>
        </div>}
      </div>
      {share > 0 && previewButton(character)}
    </div>;
  };
  const narrator = groups.narrator;
  const narratorOptions = voices ? narratorChoices(voices) : [];
  return <>
    <DialogHeader title={t('narration')} description={review ? (ru ? 'Проверьте имена, голоса и отмеченные отрывки, затем запустите озвучку. До запуска речь не создаётся.' : 'Check names, voices and flagged passages, then start narration. Nothing is synthesized until you start.')
      : (ru ? 'Репертуар рассказчика используется во всей книге. Голоса разделяются в локальных диалогах, а подача меняется по отрывкам.' : 'A narrator’s repertoire can be reused across the book. Speakers are separated within local dialogue; delivery varies by passage.')}/>
    <div className="modal-scroll cast-panel">
      {review && <div className="review-summary"><div><strong>{t('reviewReady')}</strong>
        <p>{ru ? `Будет озвучено: ${count(language, estimate.passages, 'passages')} · ${count(language, estimate.characters, 'characters')}.` : `${count(language, estimate.passages, 'passages')} · ${count(language, estimate.characters, 'characters')} will be synthesized.`}</p>
        <p>{t('approvalKeepsHolds')}</p>
        {blocked && <p>{blocked}</p>}</div>
        <Button disabled={busy || Boolean(blocked)} onClick={onStart}>{busy ? <LoaderCircle className="spin"/> : <Play/>}{t('startNarration')}</Button></div>}
      {voiceError && <p className="validation-error" role="alert">{voiceError}</p>}
      {holds.length > 0 && <div className="cast-holds" role="note"><strong>{ru ? `Отрывков ждут проверки говорящего: ${holds.length}` : `Passages held for speaker review: ${holds.length}`}</strong>
        {generation.status === 'held' && <p>{t('heldGeneration')}</p>}
        {reasons && <p>{reasons}</p>}
        <details open={generation.status === 'held'}><summary>{ru ? 'Показать очередь' : 'Show review queue'}</summary><ul className="cast-passages">{holds.map(item => { const target = passages.get(item.source); return <li key={item.key}>
          <button type="button" disabled={!item.segment || busy} onClick={() => item.segment && onReview(item.segment)}>{preview(item.text || target?.block.text || item.source, 90)}</button>
          {target && <small> · {target.chapter.title}</small>}{item.reasons.length > 0 && <small> · {item.reasons.map(reason => holdReason(language, reason)).join(', ')}</small>}
          {item.critics.map((critic, index) => <small className="hold-detail" key={`critic:${index}`}>{ru ? 'Независимая проверка' : 'Critic'}: {critic.speaker ? labels.get(critic.speaker) ?? (critic.speaker === 'unknown' ? (ru ? 'не определён' : 'unresolved') : critic.speaker) : '—'}{critic.reason ? ` — ${critic.reason}` : ''}</small>)}
          {item.presentation.map((binding, index) => <small className="hold-detail" key={`presentation:${index}`}>{binding.part === null || binding.part === undefined ? (ru ? 'Весь отрывок' : 'Whole passage') : `${ru ? 'Часть' : 'Part'} ${binding.part + 1}`}: {presentationLine(language, binding, generation.cast, voices)}</small>)}</li>; })}</ul>
          <p>{t('verdictsSeparate')}</p></details>
      </div>}
      {notices.length > 0 && <div className="casting-notices" role="note">{notices.map(notice => <div className="casting-notice" key={notice.key}><AlertTriangle size={15}/><div>
        <p>{notice.text}</p>
        {notice.names && <small>{notice.names}</small>}
        {notice.code === 'shared_performance_contexts' && conflicts.length ? <ul className="cast-passages">{conflicts.slice(0, LISTED).map((conflict, index) => <li key={`${conflict.chapter_id}:${index}`}>
          <button type="button" onClick={() => conflict.source_ids[0] && onNavigate(conflict.source_ids[0])}>{conflict.shared_speakers.map(group => group.map(id => labels.get(id) ?? id).join(' = ')).join('; ')}</button>
          <small> · {chapters.find(chapter => chapter.id === conflict.chapter_id)?.title ?? conflict.chapter_id}</small></li>)}</ul>
          : notice.chapters.length > 0 && <small>{notice.chapters.map(id => chapters.find(chapter => chapter.id === id)?.title ?? id).join(', ')}</small>}
      </div></div>)}</div>}
      {(flagged.degraded.length > 0 || flagged.unresolved.length > 0) && <div className="cast-flagged">
        {flagged.degraded.length > 0 && <details><summary>{ru ? `Отрывки без анализа говорящих: ${flagged.degraded.length}` : `Passages without speaker analysis: ${flagged.degraded.length}`}</summary>{passageList(flagged.degraded.map(item => ({key: item.id, source: item.source, text: item.text, note: degradedNote(item.reasons)})))}</details>}
        {flagged.unresolved.length > 0 && <details><summary>{ru ? `Отрывки с неопределённым говорящим: ${flagged.unresolved.length}` : `Passages with an unresolved speaker: ${flagged.unresolved.length}`}</summary>{passageList(flagged.unresolved.map(id => ({key: id, source: id})))}</details>}
      </div>}
      {generation.cast.length ? <>
        {narrator && <div className="character-row"><div className="character-avatar tone-0"><BookOpen size={18}/></div><div className="character-details">
          <strong>{characterName(language, narrator)}</strong><p>{voiceLine(narrator)}</p>
          {review && narratorOptions.length > 1 && <div className="narrator-voices">
            <label className="setting-row"><span>{ru ? 'Голос по умолчанию' : 'Book default voice'}</span><select value={narratorVoice(generation)} disabled={!editable} onChange={event => onNarrator('', event.target.value)}>{choices(narratorVoice(generation), narratorOptions).map(option)}</select></label>
            {rendered.length > 1 && <details><summary>{ru ? 'Рассказчик по главам' : 'Narrator by chapter'}</summary>{rendered.map(chapter => <label className="setting-row" key={chapter.id}><span>{chapter.title}</span>
              <select aria-label={`${ru ? 'Рассказчик' : 'Narrator'}: ${chapter.title}`} value={narratorVoice(generation, chapter.id)} disabled={!editable} onChange={event => onNarrator(chapter.id, event.target.value)}>{choices(narratorVoice(generation, chapter.id), narratorOptions).map(option)}</select></label>)}</details>}
          </div>}
        </div>{previewButton(narrator)}</div>}
        {groups.speaking.map((character, index) => row(character, index + 1))}
        {groups.silent.length > 0 && <details className="cast-silent"><summary>{ru ? `Персонажи без реплик: ${groups.silent.length}` : `Characters without speech: ${groups.silent.length}`}</summary>{groups.silent.map((character, index) => row(character, index + 1))}</details>}
      </> : <div className="small-empty"><Users size={32}/><h3>{ru ? 'Автоматический подбор голосов' : 'Automatic casting'}</h3><p>{ru ? 'Персонажи и голоса появятся после локального анализа. Перед озвучкой роли можно проверить.' : 'Character identities and voices appear after local analysis. You can review the cast before narration starts.'}</p></div>}
    </div>
    <div className="info-box"><ShieldCheck size={18}/><p>{ru ? 'Поставляемые с исходным кодом комплекты голосов — только примеры конфигурации, а не записи или лицензированный голос Влада Римского.' : 'Voice packs supplied with this source are configuration examples—not recordings or a licensed voice of Vlad Rimsky.'}</p></div>
  </>;
}
