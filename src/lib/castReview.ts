import type {Analysis, CastingNotice, Character, Generation, PackVoice, PresentationBinding, Segment, SpeakerResolution} from './types';
import {characterName, localMessage, locale, passageProgress, statusLabel, translator} from './i18n';

type Language = 'en' | 'ru';
type Pair = readonly [string, string];
const pick = (language: Language, pair: Pair) => pair[language === 'ru' ? 1 : 0];
const number = (language: Language, value: number) => value.toLocaleString(locale(language));

const NOTICES: Record<string, Pair> = {
  shared_performance_contexts: ['{count} local dialogue context(s) still share a character performance. Voice reuse elsewhere in the book is allowed.', 'Локальных диалогов, где персонажи звучат одним исполнением: {count}. Повтор голоса в других местах книги допустим.'],
  no_compatible_profile: ['{count} character(s) have no compatible voice profile; another profile is used.', 'Персонажей без подходящего голосового профиля: {count}; используется другой профиль.'],
  displaced_for_collision: ['{count} character(s) use another profile: a compatible profile was not used to avoid a local performance collision.', 'Персонажей с другим профилем: {count}; подходящий профиль не использован, чтобы голоса в диалоге не совпали.'],
  no_collision_gain: ['{count} character(s) use an incompatible profile although a compatible profile would add no local performance collision.', 'Персонажей с неподходящим профилем: {count}, хотя подходящий профиль не создал бы совпадения голосов в диалоге.'],
  disputed_traits: ['{count} character(s) have conflicting gender or age evidence and are cast as unknown.', 'Персонажей с противоречивыми данными о поле или возрасте: {count}; при подборе голоса эти черты считаются неизвестными.'],
  pack_missing_metadata: ['No character voice profile declares {fields}; these character traits cannot guide casting.', 'Ни один голосовой профиль персонажей не указывает {fields}; эти черты персонажей не учитываются при подборе голоса.'],
  alias_unmerged_capacity: ['{count} possible alias pair(s) exceeded the model context or output limit and remain separate characters.', 'Возможных пар имён одного персонажа, превысивших контекст или лимит вывода модели: {count}; они остаются разными персонажами.'],
  alias_skipped_limit: ['{count} possible alias pair(s) were not compared because the {limit}-comparison limit was reached; they remain separate characters.', 'Возможных пар имён одного персонажа, не сравнённых из-за лимита в {limit} сравнений: {count}; они остаются разными персонажами.'],
  unknown_gender_speakers: ['{count} speaking character(s) have unknown gender and are cast by the gender balance of the book: {names}.', 'Говорящих персонажей с неизвестным полом: {count}; голоса подобраны по гендерному балансу книги: {names}.'],
  unknown_gender_unbalanced: ['{count} speaking character(s) have unknown gender; no character with a known gender speaks, so their voices follow no gender balance: {names}.', 'Говорящих персонажей с неизвестным полом: {count}; ни один персонаж с известным полом не говорит, поэтому голоса не подобраны по гендерному балансу: {names}.'],
  unresolved_speech: ['{count} speech part(s) have no resolved speaker and are held for review.', 'Реплик без определённого говорящего: {count}; они ожидают проверки.'],
  degraded_passages: ['{count} passage(s) could not be analysed for speakers and are held for review.', 'Отрывков, которые не удалось разобрать по говорящим: {count}; они ожидают проверки.'],
  no_mechanical_voice: ['{count} mechanical character(s) use character voices because the pack has no mechanical voice.', 'Механических персонажей с голосами обычных персонажей: {count}; в комплекте нет механического голоса.'],
  narrator_performance_shared: ['{count} character(s) share the narrator performance in chapters where they speak.', 'Персонажей, звучащих исполнением рассказчика в главах, где они говорят: {count}.'],
  genderless_profile_for_gendered: ['{count} character(s) with a known gender use a voice profile without a declared gender.', 'Персонажей с известным полом, озвученных профилем без указанного пола: {count}.'],
  series_voice_binding_applied: ['A reviewed series voice binding was applied to {count} character(s): {names}.', 'Проверенная привязка голоса из серии применена к персонажам ({count}): {names}.'],
  series_voice_binding_ambiguous: ['{count} character(s) match more than one reviewed series voice binding and use normal casting: {names}.', 'Персонажей, совпавших с несколькими проверенными голосами серии: {count}; для них используется обычный подбор: {names}.'],
  pace_not_supported: ['This model pack has no pace control; {count} passage(s) that need a planned pace are held instead of using a default pace.', 'В этом комплекте моделей нет управления темпом; отрывков, которым нужен запланированный темп и которые поэтому ожидают проверки: {count}.'],
  delivery_not_supported: ['This model pack has no delivery control; {count} expressive passage(s) use neutral delivery.', 'В этом комплекте моделей нет управления подачей; выразительных отрывков с нейтральной подачей: {count}.'],
};
const LEGACY: Array<[string, string]> = [['no_compatible_profile', '{count} character(s) have no voice profile matching their gender; another profile is used.']];
const FIELDS: Record<string, Pair> = {gender: ['gender', 'пол'], age_bands: ['age bands', 'возрастные группы']};
const NAMED = new Set(['no_compatible_profile', 'displaced_for_collision', 'no_collision_gain', 'disputed_traits', 'no_mechanical_voice', 'narrator_performance_shared', 'genderless_profile_for_gendered']);
const KINDS: Record<string, Pair> = {person: ['Person', 'Человек'], mechanical: ['Mechanical', 'Механический'], collective: ['Group', 'Группа']};
const TRAITS: Record<string, Pair> = {female: ['female', 'женский пол'], male: ['male', 'мужской пол'], child: ['child', 'ребёнок'], teen: ['teen', 'подросток'], adult: ['adult', 'взрослый'], elderly: ['elderly', 'пожилой']};
const DEGRADED = 'Speaker analysis failed for this passage';
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const legacyPatterns = [...Object.entries(NOTICES).map(([code, pair]): [string, string] => [code, pair[0]]), ...LEGACY].map(([code, english]) => ({
  code, keys: [...english.matchAll(/\{(\w+)\}/g)].map(match => match[1]),
  pattern: new RegExp(`^${escape(english).replace(/\\\{(\w+)\\\}/g, (_, key: string) => key === 'count' || key === 'limit' ? '(\\d+)' : '(.+?)')}$`),
}));

export function nameList(language: Language, names: string[], count = names.length): string {
  if (!names.length) return '';
  return names.join(', ') + (count > names.length ? pick(language, [' and {n} more', ' и ещё {n}']).replace('{n}', number(language, count - names.length)) : '');
}

export function noticeText(language: Language, notice: CastingNotice, names = notice.params?.names ?? []): string {
  const template = NOTICES[notice.code];
  if (!template) return '';
  const params = notice.params ?? {};
  const fields = (params.fields ?? []).map(field => FIELDS[field] ? pick(language, FIELDS[field]) : field).join(pick(language, [' or ', ' или ']));
  return pick(language, template).replace('{count}', number(language, notice.count)).replace('{limit}', number(language, params.limit ?? 256))
    .replace('{fields}', fields).replace('{names}', nameList(language, names, notice.count));
}

export function legacyNotice(warning: string): CastingNotice | null {
  for (const {code, keys, pattern} of legacyPatterns) {
    const match = pattern.exec(warning);
    if (!match) continue;
    const value = (key: string) => match[keys.indexOf(key) + 1] ?? '';
    const fields = keys.includes('fields') ? value('fields').split(' or ').map(label => label.replace(' ', '_')) : undefined;
    return {code, count: keys.includes('count') ? Number(value('count')) : fields?.length ?? 0,
      params: {...(keys.includes('limit') ? {limit: Number(value('limit'))} : {}), ...(fields ? {fields} : {})}};
  }
  return null;
}

export type NoticeView = {key: string; code: string; text: string; names: string; chapters: string[]};
export function noticeViews(language: Language, analysis: Analysis, cast: Pick<Character, 'id' | 'name'>[]): NoticeView[] {
  const current = new Map(cast.map(character => [character.id, character.name]));
  const notices = analysis.casting_notices;
  if (notices) return notices.map((notice, index) => {
    const params = notice.params ?? {};
    const names = params.ids?.length ? params.ids.map((id, at) => current.get(id) ?? params.names?.[at] ?? id) : params.names ?? [];
    const warning = analysis.casting_warnings?.length === notices.length ? analysis.casting_warnings[index] : '';
    return {key: `${notice.code}:${index}`, code: notice.code, text: noticeText(language, notice, names) || localMessage(language, warning || `${notice.code}: ${notice.count}`),
      names: NAMED.has(notice.code) ? nameList(language, names, notice.count) : '', chapters: params.chapters ?? []};
  });
  return (analysis.casting_warnings ?? []).map((warning, index) => {
    const notice = legacyNotice(warning);
    return {key: `warning:${index}`, code: notice?.code ?? '', text: notice ? noticeText(language, notice) : localMessage(language, warning), names: '', chapters: []};
  });
}

export function speechShares(generation: Pick<Generation, 'analysis' | 'segments'>): Map<string, number> {
  const inputs = generation.analysis.casting_inputs;
  const volumes = inputs?.length ? inputs.map(item => [item.speaker, item.characters] as const) : generation.segments.map(segment => [segment.speaker, segment.text.length] as const);
  const totals = new Map<string, number>();
  let all = 0;
  for (const [speaker, characters] of volumes) { totals.set(speaker, (totals.get(speaker) ?? 0) + characters); all += characters; }
  return new Map([...totals].map(([speaker, value]) => [speaker, all ? value / all : 0]));
}

export function castGroups(cast: Character[], shares: Map<string, number>) {
  const share = (character: Character) => shares.get(character.id) ?? 0;
  const characters = cast.filter(character => character.id !== 'narrator');
  return {narrator: cast.find(character => character.id === 'narrator') ?? null,
    speaking: characters.filter(character => share(character) > 0).sort((a, b) => share(b) - share(a) || a.name.localeCompare(b.name)),
    silent: characters.filter(character => share(character) <= 0)};
}

export function voiceChoices(character: Pick<Character, 'kind'>, voices: PackVoice[]): PackVoice[] {
  const allowed = voices.filter(voice => voice.castable !== false);
  const mechanical = allowed.filter(voice => voice.role === 'mechanical');
  return character.kind === 'mechanical' && mechanical.length ? mechanical : allowed.filter(voice => (voice.role || 'character') === 'character');
}

export function narratorChoices(voices: PackVoice[]): PackVoice[] {
  return voices.filter(voice => voice.role === 'narrator' || voice.role === 'narrator_variant');
}

export function narratorVoice(generation: Pick<Generation, 'analysis' | 'cast'>, chapterId = ''): string {
  const voices = generation.analysis.narrator_voices ?? {};
  return voices[chapterId] ?? voices[''] ?? generation.cast.find(character => character.id === 'narrator')?.voice_id ?? '';
}

export function aliasesOf(character: Pick<Character, 'id' | 'name' | 'aliases'>): string[] {
  if (character.id === 'narrator') return [];
  const seen = new Set([character.name.trim().toLocaleLowerCase()]);
  const aliases: string[] = [];
  for (const alias of character.aliases ?? []) {
    const key = alias.trim().toLocaleLowerCase();
    if (key && !seen.has(key)) { seen.add(key); aliases.push(alias.trim()); }
  }
  return aliases;
}

export function speakerLabels(language: Language, cast: Pick<Character, 'id' | 'name' | 'aliases'>[]): Map<string, string> {
  const seen = new Map<string, number>();
  return new Map(cast.map(character => {
    const aliases = aliasesOf(character);
    const label = characterName(language, character) + (aliases.length ? ` (${aliases.slice(0, 3).join(', ')}${aliases.length > 3 ? ', …' : ''})` : '');
    const count = (seen.get(label) ?? 0) + 1;
    seen.set(label, count);
    return [character.id, count > 1 ? `${label} · ${count}` : label];
  }));
}

export function kindChoices(language: Language): {value: string; label: string}[] {
  return Object.entries(KINDS).map(([value, pair]) => ({value, label: pick(language, pair)}));
}

export function characterTraits(language: Language, character: Pick<Character, 'kind' | 'gender' | 'age_band'>): string {
  const traits = [character.gender, character.age_band].map(trait => trait?.conflict ? pick(language, ['disputed', 'спорно']) : TRAITS[trait?.value ?? ''] ? pick(language, TRAITS[trait?.value ?? '']) : '').filter(Boolean);
  return [character.kind && KINDS[character.kind] ? pick(language, KINDS[character.kind]) : '', ...traits].filter(Boolean).join(' · ');
}

export function renderEstimate(segments: Pick<Segment, 'id' | 'status' | 'spoken'>[], held: Iterable<string> = []) {
  const excluded = new Set(held);
  const pending = segments.filter(segment => segment.status !== 'ready' && segment.status !== 'review' && !excluded.has(segment.id));
  return {passages: pending.length, characters: pending.reduce((total, segment) => total + segment.spoken.length, 0)};
}

export function flaggedPassages(generation: Pick<Generation, 'analysis' | 'segments'>, known: (sourceId: string) => boolean) {
  const failures = new Map<string, string[]>();
  for (const item of generation.analysis.degraded ?? []) failures.set(item.source, [...failures.get(item.source) ?? [], item.reason].filter(Boolean));
  const unknown = generation.segments.filter(segment => segment.speaker === 'unknown' && known(segment.source_id));
  const degraded = unknown.filter(segment => failures.has(segment.id) || segment.warning.includes(DEGRADED))
    .map(segment => ({id: segment.id, source: segment.source_id, text: segment.text, reasons: failures.get(segment.id) ?? []}));
  const excluded = new Set(degraded.map(item => item.source));
  return {degraded, unresolved: [...new Set(unknown.filter(segment => !excluded.has(segment.source_id)).map(segment => segment.source_id))]};
}

const preparing = (generation: Pick<Generation, 'status' | 'analysis'>) => ['analyzing', 'casting'].includes(generation.status) || generation.status === 'queued' && !generation.analysis.complete;

export function stageProgress(generation: Pick<Generation, 'status' | 'analysis'>) {
  const progress = generation.analysis.progress;
  return progress && preparing(generation) && ['analysis', 'reconcile', 'casting'].includes(progress.stage) && progress.total > 0 ? progress : null;
}

export function preparationStatus(language: Language, generation: Pick<Generation, 'status' | 'analysis'> & {segments?: Pick<Segment, 'status'>[]}, ready: number, total: number): string {
  const t = translator(language);
  if (generation.status === 'ready') return passageProgress(language, ready, total, 'saved');
  if (generation.status === 'review') return t('reviewReady');
  if (generation.status === 'held') return `${statusLabel(language, generation.status)} · ${ready}/${total} · ${heldLabel(language, heldCount(generation))}`;
  const stage = stageProgress(generation);
  if (stage) return stage.stage === 'casting' ? t('casting') : `${t(stage.stage === 'analysis' ? 'analyzing' : 'reconciling')} · ${number(language, stage.done)}/${number(language, stage.total)}`;
  const label = statusLabel(language, generation.status);
  return preparing(generation) ? label : `${label} · ${ready}/${total}`;
}

export function preparationPercent(generation: Pick<Generation, 'status' | 'analysis' | 'segments'>): number {
  const stage = stageProgress(generation);
  if (stage) return 100 * Math.min(stage.done, stage.total) / stage.total;
  const ready = generation.segments.filter(segment => segment.status === 'ready').length;
  return generation.segments.length ? 100 * ready / generation.segments.length : 0;
}

const HOLD_REASONS: Record<string, Pair> = {
  speaker_review: ['speaker needs review', 'говорящий требует проверки'],
  neighbour: ['next to a held passage', 'рядом с удержанным отрывком'],
  degraded_analysis: ['speaker analysis failed', 'не удалось разобрать говорящих'],
  unresolved_speaker: ['speaker unknown', 'говорящий не определён'],
  neighbor_only_evidence: ['only neighbouring text supports the speaker', 'говорящего подтверждает только соседний текст'],
  backend_limit: ['too long for the speech model; text was not shortened', 'слишком длинно для речевой модели; текст не сокращался'],
  performance_capability: ['the model pack cannot perform the planned pace', 'комплект моделей не может исполнить запланированный темп'],
  mixed_spoken_edit: ['spoken-text edit in a mixed sentence', 'изменён произносимый текст смешанного предложения'],
  critic_disagreement: ['an independent critic pass disagreed', 'независимая проверка не согласилась'],
  critic_unavailable: ['the independent critic pass was unavailable', 'независимая проверка была недоступна'],
  descriptor_identity: ['identity rests on a description, not a name', 'личность определена по описанию, а не по имени'],
  local_fallback_review: ['local model result needs review', 'результат локальной модели требует проверки'],
  presentation_review: ['presentation voice needs review', 'голос подачи требует проверки'],
  render_degraded: ['provisional take: planned delivery controls or the character voice were missing; render a new take', 'черновой дубль: не было запланированной подачи или голоса персонажа; нужен новый дубль'],
};

export function holdReason(language: Language, code: string): string {
  return HOLD_REASONS[code] ? pick(language, HOLD_REASONS[code]) : code;
}

export function heldCount(generation: {segments?: Pick<Segment, 'status'>[]; progress?: {held?: number}}): number {
  return generation.segments ? generation.segments.filter(segment => segment.status === 'review').length : generation.progress?.held ?? 0;
}

export function heldLabel(language: Language, count: number): string {
  return pick(language, ['held: {n}', 'удержано: {n}']).replace('{n}', number(language, count));
}

export function holdReasons(language: Language, analysis: Pick<Analysis, 'speaker_review_holds'>): string {
  return Object.entries(analysis.speaker_review_holds?.reasons ?? {}).filter(([, count]) => count > 0)
    .map(([code, count]) => `${holdReason(language, code)}: ${number(language, count)}`).join(' · ');
}

export type HeldItem = {key: string; source: string; segment: Segment | null; text: string; reasons: string[]; critics: {speaker: string; reason: string}[]; presentation: PresentationBinding[]};
const holdCodes = (item: {triggers?: string[]; reason?: string}) => [...item.triggers ?? [], item.reason ?? ''].filter(code => HOLD_REASONS[code]);
const overlaps = (resolution: Pick<SpeakerResolution, 'source_id' | 'start' | 'end'>, segment: Pick<Segment, 'source_id' | 'start' | 'end'>) =>
  resolution.source_id === segment.source_id && segment.start < resolution.end && segment.end > resolution.start;
const criticOf = (resolution: SpeakerResolution) => resolution.critic_speaker || resolution.critic_reason ? {speaker: resolution.critic_speaker ?? '', reason: resolution.critic_reason ?? ''} : undefined;
const addReasons = (item: HeldItem, reasons: string[], critic?: {speaker: string; reason: string}) => {
  for (const reason of reasons) if (!item.reasons.includes(reason)) item.reasons.push(reason);
  if (critic && !item.critics.some(known => known.speaker === critic.speaker && known.reason === critic.reason)) item.critics.push(critic);
};
const heldItem = (segment: Segment | null, key: string, source: string, presentation: PresentationBinding[]): HeldItem => ({key, source, segment, text: segment?.text ?? '', reasons: [], critics: [], presentation});

export function holdQueue(generation: Pick<Generation, 'analysis' | 'segments'>): HeldItem[] {
  const open = (generation.analysis.speaker_resolution ?? []).filter(resolution => resolution.status === 'review');
  const computed = generation.analysis.speaker_review_holds;
  if (computed?.held_ids) {
    const held = new Set(computed.held_ids);
    const flagged = new Set(computed.flagged_ids ?? []);
    return generation.segments.filter(segment => held.has(segment.id)).map(segment => {
      const covering = flagged.has(segment.id) ? open.filter(resolution => overlaps(resolution, segment)) : [];
      const presentation = covering.some(resolution => resolution.triggers.includes('presentation_review')) ? segment.performance?.presentation_binding ?? [] : [];
      const item = heldItem(segment, segment.id, segment.source_id, presentation);
      for (const resolution of covering) { const codes = holdCodes(resolution); addReasons(item, codes.length ? codes : ['speaker_review'], criticOf(resolution)); }
      if (!flagged.has(segment.id)) addReasons(item, ['neighbour']);
      else if (!item.reasons.length) addReasons(item, ['speaker_review']);
      return item;
    });
  }
  const items: HeldItem[] = [];
  const bySegment = new Map<string, HeldItem>();
  const start = (segment: Segment | null, key: string, source: string): HeldItem => {
    const item = heldItem(segment, key, source, segment?.performance?.presentation_binding ?? []);
    items.push(item);
    if (segment) bySegment.set(segment.id, item);
    return item;
  };
  open.forEach((resolution, index) => {
    const segment = generation.segments.find(value => overlaps(resolution, value)) ?? null;
    const item = (segment && bySegment.get(segment.id)) || start(segment, segment?.id ?? `${resolution.source_id}:${resolution.start}:${index}`, resolution.source_id);
    const codes = holdCodes(resolution);
    addReasons(item, codes.length ? codes : ['speaker_review'], criticOf(resolution));
  });
  for (const segment of generation.segments) if (segment.status === 'review' && !bySegment.has(segment.id)) {
    const item = start(segment, segment.id, segment.source_id);
    addReasons(item, item.presentation.length ? ['presentation_review'] : []);
  }
  return items;
}

export function voiceName(id: string, cast: Pick<Character, 'voice_id' | 'voice_name'>[], voices: Pick<PackVoice, 'id' | 'name'>[] | null = null): string {
  return voices?.find(voice => voice.id === id)?.name || cast.find(character => character.voice_id === id)?.voice_name || id;
}

export function presentationLine(language: Language, binding: PresentationBinding, cast: Pick<Character, 'voice_id' | 'voice_name'>[], voices: Pick<PackVoice, 'id' | 'name'>[] | null = null): string {
  return [`${pick(language, ['voice', 'голос'])}: ${voiceName(binding.voice, cast, voices)}`,
    binding.channel ? `${pick(language, ['channel', 'канал'])}: ${binding.channel}` : '',
    binding.register ? `${pick(language, ['register', 'регистр'])}: ${binding.register}` : ''].filter(Boolean).join(' · ');
}

export function segmentHeld(generation: Pick<Generation, 'analysis'>, segment: Pick<Segment, 'id' | 'status' | 'source_id' | 'start' | 'end'>): boolean {
  const held = generation.analysis.speaker_review_holds?.held_ids;
  if (held) return held.includes(segment.id);
  if (segment.status === 'review') return true;
  return (generation.analysis.speaker_resolution ?? []).some(item => item.status === 'review' && overlaps(item, segment));
}

const SPEAKER_CONFIRMATION_BLOCKED = new Set(['neighbour', 'backend_limit', 'presentation_review', 'performance_capability', 'mixed_spoken_edit', 'render_degraded']);
export function speakerConfirmationAvailable(item: Pick<HeldItem, 'reasons'> | undefined): boolean {
  return !item || item.reasons.some(reason => !SPEAKER_CONFIRMATION_BLOCKED.has(reason));
}

export function mixedSegment(segment: Pick<Segment, 'performance'> | null | undefined): boolean {
  return Boolean(segment?.performance?.internal_transition);
}

type Shown = Pick<Segment, 'id' | 'spoken' | 'speaker' | 'delivery' | 'performance'>;
const partsOf = (segment: Pick<Segment, 'performance'> | null | undefined) => segment?.performance?.analysis_parts ?? [];

export function partVerdicts(segment: Pick<Segment, 'performance'> | null | undefined): boolean {
  return mixedSegment(segment) && partsOf(segment).length > 1;
}

export function partRequests(original: Shown | undefined, edit: Shown): {id: string; part: number; speaker: string}[] {
  if (!original || original.id !== edit.id || !partVerdicts(original)) return [];
  const before = partsOf(original);
  return partsOf(edit).flatMap((part, index) => before[index] && part.speaker !== before[index].speaker ? [{id: original.id, part: index, speaker: part.speaker}] : []);
}

const wholeChanged = (original: Shown, edit: Shown) => original.spoken.trim() !== edit.spoken.trim() || original.speaker !== edit.speaker || original.delivery !== edit.delivery;

export function confirmSpeakerRequest(original: Shown | undefined, edit: Shown): {id: string; confirm_speaker: true} | null {
  if (!original || original.id !== edit.id || !original.speaker || original.speaker === 'unknown') return null;
  if (wholeChanged(original, edit) || partRequests(original, edit).length) return null;
  return {id: original.id, confirm_speaker: true};
}

export function confirmPartRequest(original: Shown | undefined, edit: Shown, index: number): {id: string; part: number; confirm_speaker: true} | null {
  const part = partsOf(original)[index];
  if (!original || original.id !== edit.id || !partVerdicts(original) || !part || !part.speaker || part.speaker === 'unknown') return null;
  if (wholeChanged(original, edit) || partRequests(original, edit).length) return null;
  return {id: original.id, part: index, confirm_speaker: true};
}

export function confirmPresentationRequest(original: Shown | undefined, edit: Shown, part: number | null): {id: string; confirm_presentation: true; part?: number} | null {
  if (!original || original.id !== edit.id || wholeChanged(original, edit) || partRequests(original, edit).length) return null;
  if (!(original.performance?.presentation_binding ?? []).some(binding => (binding.part ?? null) === part)) return null;
  return part === null ? {id: original.id, confirm_presentation: true} : {id: original.id, part, confirm_presentation: true};
}

export function withPartSpeaker<T extends Pick<Segment, 'performance'>>(segment: T, index: number, speaker: string): T {
  const performance = segment.performance;
  if (!performance?.analysis_parts?.[index]) return segment;
  return {...segment, performance: {...performance, analysis_parts: performance.analysis_parts.map((part, at) => at === index ? {...part, speaker} : part)}};
}
