"""Build a conservative voice proposal overlay from protected series labels.

Human review files are read-only.  The generated overlay changes only the labels
shown for clips which do not already have a saved listener decision.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]
from rimsky.presentation_voice import resolve_presentation_voice
from review_label_resolution import resolved_review_speaker

BOOK1 = ROOT / 'manual-labeling/reality-benders-1'
CAST = ROOT / 'training-work/character-rb1/benchmark/reviewed_cast_v3.json'
PREDICTOR = ROOT / 'training-work/character-rb1/label-predictor-3a743ea3d029-large-v2'
BOARD = ROOT / 'training-work/character-rb2-full/fine-tune-all'
ARCHIVE = ROOT / 'manual-labeling/reality-benders-2-full'
TITLES = {'герд', 'лэнг'}
PRESENTATION_PROFILE = ROOT / 'training/narrator_presentation_profiles/vlad-rimsky.json'
VOICE_FAMILIES = ROOT / 'training/voice_families.json'
SPEAKER_OVERRIDES = ARCHIVE / 'metadata/speaker-overrides.json'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip()]


def reviewed_speaker_overrides(path: Path, candidates_sha256: str) -> dict:
    """Load explicit listener corrections without rewriting frozen model output."""
    if not path.is_file():
        return {}
    document = load(path)
    if (document.get('schema') != 1 or
            document.get('candidates_sha256') != candidates_sha256 or
            not isinstance(document.get('clips'), dict)):
        raise ValueError('Speaker corrections do not match this candidate set')
    allowed = {'character_id', 'character_display', 'speaker_override_basis'}
    result = {}
    for clip_id, correction in document['clips'].items():
        if (not isinstance(correction, dict) or
                not isinstance(correction.get('character_id'), str) or
                not correction['character_id'].strip() or
                set(correction) - allowed):
            raise ValueError(f'Invalid reviewed speaker correction for {clip_id}')
        result[clip_id] = dict(correction)
    return result


def apply_speaker_overrides(candidates: list[dict], overrides: dict) -> list[dict]:
    known = {row['id'] for row in candidates}
    unknown = set(overrides) - known
    if unknown:
        raise ValueError(f'Speaker corrections reference unknown clips: {sorted(unknown)}')
    return [{**row, **overrides.get(row['id'], {})} for row in candidates]


def apply_reviewed_speakers(candidates: list[dict], review: dict) -> list[dict]:
    decisions = review.get('decisions', {})
    output = []
    for row in candidates:
        decision = decisions.get(row['id'], {})
        speaker = resolved_review_speaker(
            row.get('character_id'), decision.get('speaker_verdict'),
            decision.get('corrected_character_id'))
        output.append({**row, **({'character_id': speaker,
                                 'character_display': speaker}
                                if speaker and speaker != row.get('character_id') else {})})
    return output


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')


def normalized_name(value: str) -> str:
    value = unicodedata.normalize('NFKC', value).casefold().replace('ё', 'е')
    value = value.replace('0', 'о')
    words = re.findall(r'[a-zа-я]+', value)
    while words and words[0] in TITLES:
        words.pop(0)
    return ' '.join(words)


def character_profiles(label_rows, cast, minimum=3, purity=.8, chapters=2):
    counts = defaultdict(Counter)
    seen_chapters = defaultdict(set)
    for row in label_rows:
        character = row.get('character_id')
        if character:
            counts[character][row['voice_family']] += 1
            seen_chapters[character].add(row['chapter_id'])
    cast_by_id = {item['id']: item for item in cast['characters']}
    profiles = {}
    aliases = defaultdict(set)
    for character, families in counts.items():
        family, count = families.most_common(1)[0]
        total = sum(families.values())
        stable = count >= minimum and count / total >= purity and len(seen_chapters[character]) >= chapters
        profile = {'character_id': character, 'voice_family': family,
                   'support': total, 'matching_family': count,
                   'purity': count / total, 'chapters': len(seen_chapters[character]),
                   'stable': stable, 'family_counts': dict(families)}
        profiles[character] = profile
        item = cast_by_id.get(character)
        if item:
            for label in [item['name'], *item.get('aliases', [])]:
                if normalized := normalized_name(label):
                    aliases[normalized].add(character)
    # The reviewed cast explicitly records this source spelling variation.
    if 'rb1_timor_ankhu' in profiles:
        aliases[normalized_name('Тумор-Анху Ла-Фин')].add('rb1_timor_ankhu')
    return profiles, aliases


def match_character(name: str, aliases, profiles):
    matches = aliases.get(normalized_name(name), set())
    stable = [profiles[key] for key in matches if profiles[key]['stable']]
    return stable[0] if len(stable) == 1 else None


def presentation_voice(row, assigned, profile, voices):
    channel = row.get('presentation_channel_proposed', 'direct')
    register = row.get('content_register_proposed', 'prose')
    performance = {
        'presentation_channel': channel,
        'content_register': register,
        'context_confidence': 'high',
        # The frozen context pass required exact evidence for every non-direct
        # channel before the channel reached this board.
        'context_evidence': [{'id': row['source_id'], 'quote': row['source_text'][:80]}]
            if channel != 'direct' or register != 'prose' else [],
    }
    return resolve_presentation_voice(performance, voices, assigned, profile)


def explicit_review_voice(decision, proposed):
    if decision.get('voice_verdict') == 'yes':
        return decision.get('confirmed_voice_id') or proposed
    if decision.get('voice_verdict') == 'no':
        return decision.get('corrected_voice_id')
    return None


def channel_character_profiles(candidates, review, minimum=2, purity=.8):
    """Learn recurring presentation performances from explicit Book 2 moves.

    Playback acceptance is intentionally ignored. Reviewed clips still keep
    their own decision; these profiles only improve suggestions elsewhere.
    """
    counts = defaultdict(Counter)
    for row in candidates:
        channel = row.get('presentation_channel_proposed', 'direct')
        character = row.get('character_id')
        if channel == 'direct' or not character or character == 'unknown':
            continue
        decision = review.get('decisions', {}).get(row['id'], {})
        voice = explicit_review_voice(decision, row.get('base_voice_id_proposed'))
        if voice:
            counts[(channel, character)][voice] += 1
    profiles = {}
    for key, families in counts.items():
        family, support = families.most_common(1)[0]
        total = sum(families.values())
        if support >= minimum and support / total >= purity:
            profiles[key] = {'voice_family': family, 'support': total,
                             'matching_family': support, 'purity': support / total,
                             'family_counts': dict(families)}
    return profiles


def benchmark(label_rows, evaluation, heldout, profiles):
    # Leave-one-chapter-out measures whether a character's other appearances
    # predict the held chapter. It never uses the target chapter's labels.
    loo_correct = loo_total = 0
    for row in label_rows:
        character = row.get('character_id')
        if not character:
            continue
        other = Counter(item['voice_family'] for item in label_rows
                        if item.get('character_id') == character
                        and item['chapter_id'] != row['chapter_id'])
        if not other:
            continue
        loo_total += 1
        loo_correct += other.most_common(1)[0][0] == row['voice_family']

    by_id = {row['id']: row for row in label_rows}
    voice_predictions = {item['id']: item for item in evaluation
                         if item['task'] == 'voice_family' and item['id'] in by_id}
    # Recreate priors without either reserved chapter.
    training = [row for row in label_rows if row['chapter_id'] not in heldout]
    train_profiles, _ = character_profiles(training, load(CAST))
    compared = []
    for clip_id, prediction in voice_predictions.items():
        row = by_id[clip_id]
        character = row.get('character_id')
        prior = train_profiles.get(character)
        compared.append({'actual': row['voice_family'],
                         'audio': prediction['top3'][0]['label'],
                         'prior': prior['voice_family'] if prior and prior['stable'] else None})
    character_cases = [item for item in compared if item['prior']]
    hybrid = [item['prior'] or item['audio'] for item in compared]
    per_audio_label = {}
    for label in sorted({item['audio'] for item in compared}):
        subset = [item for item in compared if item['audio'] == label]
        per_audio_label[label] = {'predictions': len(subset),
            'correct': sum(item['actual'] == label for item in subset),
            'precision': sum(item['actual'] == label for item in subset) / len(subset)}
    return {
        'leave_one_chapter_out_character_prior': {
            'clips': loo_total, 'correct': loo_correct,
            'accuracy': loo_correct / loo_total if loo_total else None},
        'reserved_chapters': sorted(heldout),
        'reserved_all_audio': {'clips': len(compared),
            'correct': sum(item['audio'] == item['actual'] for item in compared),
            'accuracy': sum(item['audio'] == item['actual'] for item in compared) / len(compared)},
        'reserved_stable_character_prior': {'clips': len(character_cases),
            'correct': sum(item['prior'] == item['actual'] for item in character_cases),
            'accuracy': sum(item['prior'] == item['actual'] for item in character_cases) / len(character_cases)},
        'reserved_prior_then_audio_fallback': {'clips': len(compared),
            'correct': sum(value == item['actual'] for value, item in zip(hybrid, compared)),
            'accuracy': sum(value == item['actual'] for value, item in zip(hybrid, compared)) / len(compared)},
        'reserved_audio_precision_by_predicted_family': per_audio_label,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                        help='Install the proposal overlay after writing the report.')
    args = parser.parse_args()
    latest = load(BOOK1 / 'latest.json')
    manifest = load(BOOK1 / 'archive-manifest.json')
    if latest['review_sha256'] != manifest['support_snapshot_review_sha256']:
        raise ValueError('Book 1 derived labels do not match the protected review')
    label_rows = rows(BOOK1 / 'labels.jsonl')
    if any(row['review_sha256'] != latest['review_sha256'] for row in label_rows):
        raise ValueError('Book 1 label rows are stale')
    cast = load(CAST)
    profiles, aliases = character_profiles(label_rows, cast)
    presentation_profile = load(PRESENTATION_PROFILE)
    voices = [{'id': item['id'], 'tags': []}
              for item in load(VOICE_FAMILIES)['families']]
    predictor_report = load(PREDICTOR / 'report.json')
    evaluation = rows(PREDICTOR / 'evaluation-predictions.jsonl')
    metrics = benchmark(label_rows, evaluation,
                        set(predictor_report['heldout_chapters']), profiles)

    summary = load(BOARD / 'summary.json')
    candidates = rows(BOARD / 'candidates.jsonl')
    if sha(BOARD / 'candidates.jsonl') != summary['candidates_sha256']:
        raise ValueError('Book 2 candidates changed')
    speaker_overrides = reviewed_speaker_overrides(
        SPEAKER_OVERRIDES, summary['candidates_sha256'])
    candidates = apply_speaker_overrides(candidates, speaker_overrides)
    book2_review = load(ARCHIVE / 'review.json')
    candidates = apply_reviewed_speakers(candidates, book2_review)
    local_channel_profiles = channel_character_profiles(candidates, book2_review)
    existing = load(BOARD / 'voice-proposal-overrides.json')
    opening = {clip_id: value for clip_id, value in existing.get('clips', {}).items()
               if value.get('base_voice_id_proposed') == 'narrator_opening'}
    # Presentation identity is independent from the stable plot character. Keep
    # manually curated disguise/impersonation labels when rebuilding proposals.
    presentation_identity = {
        clip_id: {key: value[key] for key in ('character_display', 'performance_persona')
                  if key in value}
        for clip_id, value in existing.get('clips', {}).items()
        if any(key in value for key in ('character_display', 'performance_persona'))
    }
    overrides = {}
    reasons = Counter()
    matched_characters = {}
    for row in candidates:
        clip_id = row['id']
        if clip_id in opening:
            overrides[clip_id] = opening[clip_id]
            reasons['opening_structure'] += 1
            continue
        prediction = row['prediction']['tasks']['voice_family']['top3'][0]
        channel = row.get('presentation_channel_proposed', 'direct')
        register = row.get('content_register_proposed', 'prose')
        # Fixed narrator-specific registers (game UI and recordings) do not
        # depend on identifying the ordinary speaker family first.
        fixed_voice, fixed_warning = presentation_voice(
            row, 'narrator_baseline', presentation_profile, voices)
        rule = (presentation_profile.get('registers', {}).get(register)
                if register != 'prose' else None) or presentation_profile.get('channels', {}).get(channel, {})
        if rule.get('mode') == 'fixed' and fixed_voice != 'narrator_baseline':
            profile_status = 'provisional' if fixed_warning else 'reviewed'
            proposal = {
                'base_voice_id_proposed': fixed_voice,
                'base_voice_proposal_basis':
                    f'Vlad Rimsky {profile_status} presentation profile: {(register if register != "prose" else channel).replace("_", " ")} '
                    f'uses {fixed_voice}.',
            }
            if fixed_warning:
                proposal.update({
                    'voice_prefill_review': True,
                    'voice_prefill_alternative': prediction['label'],
                    'voice_prefill_audio_score': prediction['score'],
                    'voice_prefill_review_priority': 3,
                    'voice_prefill_review_reason': fixed_warning,
                })
                reasons['fixed_presentation_review'] += 1
            overrides[clip_id] = proposal
            reasons[f'presentation_{register if register != "prose" else channel}'] += 1
            continue
        local_profile = local_channel_profiles.get((channel, row.get('character_id')))
        if local_profile:
            overrides[clip_id] = {
                'base_voice_id_proposed': local_profile['voice_family'],
                'base_voice_proposal_basis':
                    f'Book 2 listener chose {local_profile["voice_family"]} for '
                    f'{local_profile["matching_family"]}/{local_profile["support"]} '
                    f'{channel.replace("_", " ")} clips from this character.',
            }
            reasons['book2_channel_character_prior'] += 1
            continue
        if row.get('role') == 'narration' or row.get('character_id') == 'narrator':
            narrator_voice, narrator_warning = presentation_voice(
                row, 'narrator_baseline', presentation_profile, voices)
            overrides[clip_id] = {'base_voice_id_proposed': narrator_voice,
                'base_voice_proposal_basis': 'Structural narrator role from the context pass.'}
            if narrator_warning:
                overrides[clip_id].update({
                    'voice_prefill_review': True,
                    'voice_prefill_alternative': prediction['label'],
                    'voice_prefill_audio_score': prediction['score'],
                    'voice_prefill_review_priority': 3,
                    'voice_prefill_review_reason': narrator_warning,
                })
                reasons['narrator_presentation_review'] += 1
            reasons['narrator_structure'] += 1
            continue
        profile = match_character(row.get('character_id', ''), aliases, profiles)
        if profile:
            selected, presentation_warning = presentation_voice(
                row, profile['voice_family'], presentation_profile, voices)
            proposal = {'base_voice_id_proposed': selected,
                'base_voice_proposal_basis':
                    f"Series character prior from {profile['matching_family']}/{profile['support']} "
                    f"Book 1 clips across {profile['chapters']} chapters."
                    + (f' Vlad Rimsky {"provisional" if presentation_warning else "reviewed"} '
                       f'presentation profile maps {channel.replace("_", " ")} '
                       f'to {selected}.' if selected != profile['voice_family'] else '')}
            open_register_question = bool(presentation_warning)
            strong_disagreement = (selected == profile['voice_family'] and
                                    prediction['label'] != profile['voice_family'] and
                                    prediction['score'] >= .95)
            if open_register_question or strong_disagreement:
                proposal.update({
                    'voice_prefill_review': True,
                    'voice_prefill_alternative': prediction['label'],
                    'voice_prefill_audio_score': prediction['score'],
                    'voice_prefill_review_priority':
                        4 if profile['character_id'] == 'rb1_kirill' else
                        3 if open_register_question else 2,
                    'voice_prefill_review_reason':
                        (presentation_warning
                         if open_register_question else
                         f'Strong disagreement: the recurring character prior says '
                         f'{profile["voice_family"]}, while the audio model suggests '
                         f'{prediction["label"]} at {prediction["score"]:.3f}.')
                })
                reasons['series_character_prior_review_flag'] += 1
            overrides[clip_id] = proposal
            matched_characters[row['character_id']] = profile['character_id']
            reasons['series_character_prior'] += 1
            continue
        if rule.get('mode') == 'mapped':
            overrides[clip_id] = {
                'base_voice_id_proposed': 'voice_pending',
                'base_voice_proposal_basis':
                    f'The {channel.replace("_", " ")} register depends on the speaker family, '
                    'which is not stable yet.',
                'voice_prefill_review': True,
                'voice_prefill_alternative': prediction['label'],
                'voice_prefill_audio_score': prediction['score'],
                'voice_prefill_review_priority': 3,
                'voice_prefill_review_reason':
                    f'Choose the ordinary speaker family before applying the reviewed '
                    f'{channel.replace("_", " ")} register.',
            }
            reasons['presentation_speaker_unknown'] += 1
            continue
        if prediction['label'] == 'mechanical_stats':
            overrides[clip_id] = {'base_voice_id_proposed': 'mechanical_stats',
                'base_voice_proposal_basis': 'Book 1 audio model mechanical-stats family; 36/36 '
                    'chapter-separated development predictions were correct.'}
            reasons['validated_mechanical_audio'] += 1
        else:
            overrides[clip_id] = {'base_voice_id_proposed': 'voice_pending',
                'base_voice_proposal_basis': 'No stable series character prior and this audio-only '
                    'voice family is not reliable enough for a confident prefill.'}
            reasons['abstained'] += 1

    for clip_id, identity in presentation_identity.items():
        if clip_id in overrides:
            overrides[clip_id].update(identity)
    out = ROOT / 'training-work/character-rb2-full/series-voice-prefill-v2'
    report = {'schema': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
        'book1_review_sha256': latest['review_sha256'],
        'book2_candidates_sha256': summary['candidates_sha256'],
        'policy': {'stable_character_minimum_clips': 3,
                   'stable_character_minimum_chapters': 2,
                   'stable_character_minimum_purity': .8,
                   'fallback': 'opening, structural game UI, structural narrator, '
                               'narrator-specific presentation profile, validated mechanical '
                               'stats, otherwise abstain',
                   'review_warning': 'flag strong audio/prior disagreements and '
                                     'presentation mappings without a reviewed speaker family',
                   'presentation_profile': str(PRESENTATION_PROFILE.relative_to(ROOT)).replace('\\', '/'),
                   'mood_policy': 'unchanged; this pass evaluates voice family only'},
        'benchmark': metrics, 'stable_character_profiles':
            {key: value for key, value in profiles.items() if value['stable']},
        'book2_channel_character_profiles': {
            f'{channel}|{character}': value
            for (channel, character), value in local_channel_profiles.items()
        },
        'book2': {'clips': len(candidates), 'proposal_reasons': dict(reasons),
                  'matched_characters': matched_characters,
                  'voice_review_flags': sum(bool(value.get('voice_prefill_review'))
                                            for value in overrides.values())},
        'human_reviews_changed': False}
    save(out / 'report.json', report)
    overlay = {'schema': 2, 'candidates_sha256': summary['candidates_sha256'],
        'description': 'Conservative voice prefills: structure and stable series character priors; '
                       'audio-only families abstain unless validated.',
        'book1_review_sha256': latest['review_sha256'],
        'policy_report': str((out / 'report.json').relative_to(ROOT)).replace('\\', '/'),
        'voices': existing.get('voices', {}), 'clips': overrides}
    save(out / 'voice-proposal-overrides.json', overlay)
    if args.apply:
        save(BOARD / 'voice-proposal-overrides.json', overlay)
        metadata = ARCHIVE / 'metadata/voice-proposal-overrides.json'
        save(metadata, overlay)
        archive_manifest = load(ARCHIVE / 'archive-manifest.json')
        archive_manifest['metadata'][metadata.name] = sha(metadata)
        save(ARCHIVE / 'archive-manifest.json', archive_manifest)
    print(json.dumps({'report': str(out / 'report.json'), 'applied': args.apply,
                      'benchmark': metrics, 'book2': report['book2']},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
