"""Describe what each human mood label in the full Book 2 review looks and sounds like.

Read-only. For every mood with enough explicit human choices on whole clips it
reports who carries it (voice family, character), text cues (punctuation,
length, distinctive words) and
how the clip sounds relative to the SAME voice family's calm/neutral baseline,
so a mood is not confused with a voice. Split parts are excluded because they
have no separate audio measurements. Statistics describe the labels; they do
not define or replace them.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze_book2_narration_style as style  # noqa: E402

OUT = style.ROOT / 'training-work/character-rb2-full/mood-label-analysis'
MIN_CLIPS = 15
WORD = re.compile(r"[а-яёa-z]+(?:-[а-яёa-z]+)*", re.I)
SOUND = {'level_db': 'louder', 'pitch_st': 'higher pitch', 'pitch_range_st': 'wider pitch movement',
         'level_range_db': 'more dynamic loudness', 'articulation_syl_per_s': 'faster articulation',
         'pause_share': 'more pausing', 'final_word_lengthening': 'more drawn-out last word',
         'ending_change_st': 'rising phrase ending',
         # Share of active frames with a periodic pitch track. Breathiness, noise or
         # creak can all lower it; it is not a direct breathiness measure.
         'periodic_fraction': 'higher periodic-frame share'}


def voice_and_speaker(rows, decisions, proposals):
    def voice(cid):
        decision = decisions.get(cid, {})
        if decision.get('voice_verdict') == 'no' and decision.get('corrected_voice_id'):
            return decision['corrected_voice_id']
        return (proposals.get(cid) or {}).get('base_voice_id_proposed') or rows[cid].get('base_voice_id_proposed')

    def speaker(cid):
        decision = decisions.get(cid, {})
        if decision.get('speaker_verdict') == 'no' and decision.get('corrected_character_id'):
            return decision['corrected_character_id']
        return rows[cid].get('character_id') or 'narrator'
    return voice, speaker


def log_odds(focus: Counter, rest: Counter, prior: Counter, top: int = 10) -> list[str]:
    """Monroe et al. weighted log-odds with an informative Dirichlet prior."""
    n_f, n_r, a0 = sum(focus.values()), sum(rest.values()), sum(prior.values())
    scores = {}
    for word, count in focus.items():
        if count < 3:
            continue
        a = prior[word]
        delta = (math.log((count + a) / (n_f + a0 - count - a)) - math.log((rest[word] + a) / (n_r + a0 - rest[word] - a)))
        scores[word] = delta / math.sqrt(1 / (count + a) + 1 / (rest[word] + a))
    return [word for word, _ in sorted(scores.items(), key=lambda item: -item[1])[:top]]


def describe(min_clips: int) -> dict:
    rows, features, decisions, _, _ = style.load()
    proposals = json.loads(style.INPUTS['voice_proposals'].read_text(encoding='utf-8'))['clips']
    voice, speaker = voice_and_speaker(rows, decisions, proposals)
    mood = {cid: style.canonical_review_mood(d['corrected_mood']) for cid, d in decisions.items()
            if d.get('mood_verdict') == 'no' and d.get('corrected_mood') and cid in rows
            and style.canonical_review_mood(d['corrected_mood']) not in (None, 'mood_pending')}
    clips = [cid for cid in mood if cid in features]
    words, _ = style.alignment_words(rows, clips)
    measures = {}
    for cid in clips:
        f, row = features[cid], rows[cid]
        timed = style.pace(row, words.get(row['recording_sha256'], [])) or {}
        measures[cid] = {'level_db': f.get('active_level_dbfs'),
                         'pitch_st': 12 * math.log2(f['median_pitch_hz']) if f.get('median_pitch_hz') else None,
                         'pitch_range_st': f.get('pitch_range_semitones'), 'level_range_db': f.get('level_range_db'),
                         'articulation_syl_per_s': timed.get('articulation_syl_per_s'), 'pause_share': timed.get('pause_share'),
                         'final_word_lengthening': timed.get('final_word_lengthening'),
                         'ending_change_st': f.get('pitch_ending_change_semitones'), 'periodic_fraction': f.get('periodic_fraction')}
    # Each voice family's own calm/neutral delivery is its baseline.
    baseline, pooled = {}, {}
    for family in {voice(c) for c in clips}:
        base = [c for c in clips if voice(c) == family and mood[c] in ('calm', 'neutral')]
        if len(base) < 8:
            pooled[family] = sum(voice(c) == family for c in clips)
            base = [c for c in clips if mood[c] in ('calm', 'neutral')]
        baseline[family] = {key: (np.nanmean([measures[c][key] if measures[c][key] is not None else np.nan for c in base]),
                                  np.nanstd([measures[c][key] if measures[c][key] is not None else np.nan for c in base]) or 1.)
                            for key in SOUND}
    z = {cid: {key: ((measures[cid][key] - baseline[voice(cid)][key][0]) / baseline[voice(cid)][key][1]
                     if measures[cid][key] is not None else np.nan) for key in SOUND} for cid in clips}
    tokens = {cid: [w.lower() for w in WORD.findall(rows[cid]['source_text'])] for cid in clips}
    prior = Counter(w for cid in clips for w in tokens[cid])
    counts = Counter(mood[c] for c in clips)
    report = {}
    for label, total in counts.most_common():
        if total < min_clips:
            continue
        mine = [c for c in clips if mood[c] == label]
        others = [c for c in clips if mood[c] != label]
        text = [rows[c]['source_text'].rstrip() for c in mine]
        sound = {key: float(np.nanmean([z[c][key] for c in mine])) for key in SOUND}
        centre = np.array([sound[key] for key in SOUND])
        typical = sorted(mine, key=lambda c: float(np.nansum((np.array([z[c][k] for k in SOUND]) - centre) ** 2)))[:4]
        report[label] = {
            'clips': total,
            'narrator_share': sum(voice(c) in ('narrator_baseline', 'narrator_opening') for c in mine) / total,
            'voice_families': dict(Counter(voice(c) for c in mine).most_common(4)),
            'characters': dict(Counter(speaker(c) for c in mine if speaker(c) != 'narrator').most_common(5)),
            'text': {'ends_exclamation': sum(t.endswith('!') for t in text) / total,
                     'ends_question': sum(t.endswith('?') for t in text) / total,
                     'ends_ellipsis': sum(t.endswith(('…', '...')) for t in text) / total,
                     'median_words': float(np.median([len(tokens[c]) for c in mine]))},
            'distinctive_words': log_odds(Counter(w for c in mine for w in tokens[c]),
                                          Counter(w for c in others for w in tokens[c]), prior),
            'sound_vs_own_voice_baseline_z': {key: round(value, 2) for key, value in sound.items()},
            'clear_sound_differences': [f"{SOUND[key] if value > 0 else 'opposite of ' + SOUND[key]} ({value:+.2f} sd)"
                                        for key, value in sorted(sound.items(), key=lambda item: -abs(item[1])) if abs(value) >= .3],
            'typical_examples': [{'clip_id': c, 'voice': voice(c), 'speaker': speaker(c), 'text': rows[c]['source_text'].strip()[:160]}
                                 for c in typical]}
    return {'schema': 1, 'created': time.strftime('%Y-%m-%dT%H:%M:%S'), 'script_sha256': style.sha(Path(__file__)),
            'inputs': {name: style.sha(path) for name, path in style.INPUTS.items() if name != 'embeddings'},
            'labelled_whole_clips': len(clips), 'moods_described': len(report), 'min_clips': min_clips,
            'rare_moods': {label: n for label, n in counts.items() if n < min_clips}, 'moods': report,
            'pooled_baseline_families': pooled, 'pooled_baseline_clips': sum(pooled.values()),
            'limits': 'Descriptive averages of human choices, without uncertainty intervals or chapter controls. '
                      'Sound is compared with each voice family\'s own calm/neutral clips, or a pooled baseline for the '
                      'families listed; small groups are noisy. Punctuation and words accompany moods; they do not '
                      'determine them. Book 2 whole clips only: Book 1 and split parts are excluded, so counts do not '
                      'measure overall rarity.'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--min-clips', type=int, default=MIN_CLIPS)
    args = parser.parse_args()
    report = describe(args.min_clips)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"mood-profiles-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(str(path))


if __name__ == '__main__':
    main()
