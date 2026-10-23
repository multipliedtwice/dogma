"""Describe explicit Book 2 narrator mood choices without modifying the corpus.

This is an exploratory comparison, not a classifier or a training approval.
Existing waveform measurements are reused only when their exact WAV hashes match.
Enabled mixed parents are excluded; their children need separate measurements.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics

from review_label_resolution import resolved_review_mood

ROOT = Path(__file__).resolve().parents[1]
METRICS = ('median_pitch_hz', 'pitch_range_semitones', 'active_level_dbfs',
           'average_level_dbfs', 'level_range_db', 'internal_pause_fraction',
           'envelope_peaks_per_second', 'pitch_ending_change_semitones')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def analyze(root):
    board = root / 'training-work/character-rb2-full/fine-tune-all'
    archive = root / 'manual-labeling/reality-benders-2-full'
    paths = {
        'review': archive / 'review.json', 'latest': archive / 'latest.json',
        'candidates': board / 'candidates.jsonl', 'summary': board / 'summary.json',
        'acoustics': board / 'audio-acoustics.json',
        'precision': board / 'precision-v2/status.json',
        'voice_overlay': board / 'voice-proposal-overrides.json',
    }
    raw = {name: path.read_bytes() for name, path in paths.items()}
    data = {name: json.loads(value) for name, value in raw.items() if name != 'candidates'}
    digest = sha(raw['candidates'])
    assert data['latest']['review_sha256'] == sha(raw['review']), 'Review changed during capture; rerun'
    for name in ('review', 'latest', 'summary', 'acoustics', 'precision', 'voice_overlay'):
        assert data[name]['candidates_sha256'] == digest, f'{name} belongs to another board'
    rows = [json.loads(line) for line in raw['candidates'].splitlines() if line]
    choices = data['review']['decisions']
    narrator = []
    excluded_splits = 0
    for original in rows:
        row = {**original, **data['voice_overlay']['clips'].get(original['id'], {})}
        choice = choices.get(row['id'], {})
        if choice.get('rejected'):
            continue
        if choice.get('split_review', {}).get('enabled'):
            excluded_splits += 1
            continue
        if choice.get('voice_verdict') == 'yes':
            voice = (choice.get('confirmed_voice_id') or row.get('original_voice_id_proposed')
                     or row.get('base_voice_id_proposed'))
        elif choice.get('voice_verdict') == 'no':
            voice = choice.get('corrected_voice_id')
        else:
            voice = row.get('base_voice_id_proposed')
        speaker = (choice.get('corrected_character_id')
                   if choice.get('speaker_verdict') in ('no', 'corrected') else None)
        is_narration = speaker == 'narrator' if speaker else row['role'] == 'narration'
        if voice != 'narrator_baseline' or not is_narration:
            continue
        mood = resolved_review_mood(', '.join(row.get('mood_proposed') or ['neutral']),
                                   choice.get('mood_verdict'), choice.get('corrected_mood'))
        narrator.append({**row, 'human_mood': mood,
                         'mood_verdict': choice.get('mood_verdict'),
                         'review_origin': choice.get('review_origin')})
    target = [row for row in narrator if row['human_mood'] in ('calm', 'neutral')]
    paragraphs = defaultdict(list)
    for row in target:
        paragraphs[row['source_id']].append(row)
    contrasts = [group for group in paragraphs.values()
                 if len({row['human_mood'] for row in group}) == 2]
    measured = defaultdict(list)
    chapter_measurements = defaultdict(lambda: defaultdict(list))
    measured_audio = []
    for row in target:
        feature = data['acoustics']['clips'].get(row['id'], {})
        precision = data['precision']['clips'].get(row['id'], {})
        if (feature.get('duration_seconds', 0) < 3 or not feature.get('audio_sha256')
                or feature['audio_sha256'] != precision.get('audio_sha256')):
            continue
        path = (root / precision['audio']).resolve()
        assert path.is_relative_to(root.resolve()), 'Audio path outside workspace'
        assert sha(path.read_bytes()) == feature['audio_sha256'], f'Changed audio: {row["id"]}'
        measured[row['human_mood']].append(feature)
        chapter_measurements[row['chapter_id']][row['human_mood']].append(feature)
        measured_audio.append({'id': row['id'], 'audio_sha256': feature['audio_sha256']})
    statistics_by_mood = {}
    for mood in ('calm', 'neutral'):
        group = measured[mood]
        metrics = {}
        for key in METRICS:
            values = [feature[key] for feature in group
                      if isinstance(feature.get(key), (int, float))
                      and ('pitch' not in key or feature.get('periodic_fraction', 0) >= .45)]
            metrics[key] = {'n': len(values), 'median': statistics.median(values) if values else None}
        statistics_by_mood[mood] = {'clips': len(group), 'metrics': metrics}
    audio_predictions = Counter()
    context_predictions = Counter()
    for row in target:
        top = row.get('prediction', {}).get('tasks', {}).get('mood', {}).get('top3') or [{}]
        audio_predictions[(row['human_mood'], top[0].get('label'))] += 1
        context_predictions[(row['human_mood'], (row.get('context_hint') or {}).get('mood'))] += 1
    pairs = lambda counts: [{'human': human, 'proposed': proposed, 'clips': count}
                           for (human, proposed), count in counts.most_common()]
    chapters = defaultdict(list)
    for row in target:
        chapters[row['chapter_id']].append(row)
    chapter_comparisons = []
    for chapter, group in sorted(chapters.items()):
        counts = Counter(row['human_mood'] for row in group)
        differences = {}
        for key in METRICS:
            values = {mood: [feature[key] for feature in chapter_measurements[chapter][mood]
                             if isinstance(feature.get(key), (int, float))
                             and ('pitch' not in key or feature.get('periodic_fraction', 0) >= .45)]
                      for mood in ('calm', 'neutral')}
            if all(values.values()):
                differences[key] = {
                    'calm_n': len(values['calm']), 'neutral_n': len(values['neutral']),
                    'calm_minus_neutral_median': statistics.median(values['calm'])
                                               - statistics.median(values['neutral']),
                }
        chapter_comparisons.append({
            'chapter_id': chapter, 'printed_title': group[0]['chapter_title'],
            'explicit_counts': dict(counts),
            'recording_ids': sorted({row['recording_id'] for row in group}),
            'within_chapter_metric_differences': differences,
        })
    provenance = Counter((row['human_mood'], row['mood_verdict'], row['review_origin'])
                         for row in target)
    return {
        'schema': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'Unsplit, non-rejected narrator prose with currently resolved narrator_baseline family; '
                 'moods require explicit human choices; passive playback and model-only moods excluded',
        'inputs': {name: {'path': str(paths[name].relative_to(root)), 'sha256': sha(value)}
                   for name, value in raw.items()},
        'limits': ['Reviewed subset, not a random sample or complete-book census.',
                   'Unconfirmed voice families and structural roles can still be model proposals.',
                   'Boundary-held clips remain descriptive examples, never training approval.',
                   'Acoustics are existing measurements, not a new listening evaluation.',
                   'Sound-peak rate is a pace proxy, not a word or syllable rate.',
                   'No causal explanation of the actor\'s choices or acceptance thresholds inferred.'],
        'excluded_enabled_mixed_parents': excluded_splits,
        'narrator_prose_clips': len(narrator),
        'explicit_mood_counts': dict(Counter(row['human_mood'] or 'unresolved' for row in narrator)),
        'calm_normal_paragraphs': len(paragraphs), 'paragraphs_with_both': len(contrasts),
        'calm_normal_choice_provenance': [
            {'mood': mood, 'verdict': verdict, 'origin': origin, 'clips': count}
            for (mood, verdict, origin), count in provenance.items()],
        'chapter_comparisons': chapter_comparisons,
        'acoustic_cohorts': statistics_by_mood, 'verified_measured_audio': measured_audio,
        'audio_model_comparison': pairs(audio_predictions),
        'frozen_text_context_comparison': pairs(context_predictions),
        'within_paragraph_examples': [{
            'source_id': group[0]['source_id'], 'chapter': group[0]['chapter_title'],
            'paragraph': group[0]['paragraph_text'],
            'clips': [{'id': row['id'], 'text': row['source_text'],
                       'human_mood': row['human_mood'], 'audio_sha256': row['audio_sha256']}
                      for row in sorted(group, key=lambda row: row['text_start'])],
        } for group in contrasts[:8]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = analyze(args.root.resolve())
    output = args.output or args.root / ('training-work/character-rb2-full/narration-style-analysis/'
                                      + datetime.now(timezone.utc).strftime('report-%Y%m%d-%H%M%S.json'))
    assert not output.exists(), 'Refusing to replace an existing report'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(output)
    print(json.dumps({'moods': report['explicit_mood_counts'],
                      'paragraphs_with_both': report['paragraphs_with_both'],
                      'acoustic_clips': {key: value['clips'] for key, value in report['acoustic_cohorts'].items()}},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
