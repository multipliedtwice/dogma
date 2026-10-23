"""Reproducible Calm-versus-Normal narration analysis for the full Book 2 board.

Read-only. Uses explicit human mood choices on narrator_baseline clips and asks
what separates Calm from Normal: chapter grouping, sequence, acoustics, pace from
word alignments, audio embeddings and book text. Every predictor is scored on
chapters held out from its training (GroupKFold by chapter), and out-of-fold
predictions are saved per clip. The report records the hash of every input, so
its numbers can be recomputed before they inform a decision. It never changes
labels; a weak or failed test means a distinction was not demonstrated by that
test, not that the labels are wrong.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import warnings
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from review_label_resolution import canonical_review_mood  # noqa: E402

BOARD = ROOT / 'training-work/character-rb2-full/fine-tune-all'
FULL = ROOT / 'training-work/character-rb2/full'
INPUTS = {'candidates': BOARD / 'candidates.jsonl', 'review': ROOT / 'manual-labeling/reality-benders-2-full/review.json',
          'voice_proposals': BOARD / 'voice-proposal-overrides.json', 'audio_features': FULL / 'audio-features.jsonl',
          'embeddings': FULL / 'features.npz'}
OUT = ROOT / 'training-work/character-rb2-full/narration-style-analysis'
VOWELS = re.compile('[аеёиоуыэюяАЕЁИОУЫЭЮЯ]')
ACOUSTIC = ['median_pitch_hz', 'pitch_range_semitones', 'active_level_dbfs', 'level_range_db', 'internal_pause_fraction',
            'envelope_peaks_per_second', 'pitch_ending_change_semitones', 'periodic_fraction']
PACE = ['articulation_syl_per_s', 'speaking_syl_per_s', 'pause_share', 'pauses_over_150ms_per_10_words', 'final_word_lengthening']
FOLDS = 5


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def jsonl(path: Path) -> dict:
    return {row['id']: row for row in map(json.loads, path.read_text(encoding='utf-8').splitlines()) if row}


def load():
    rows, features = jsonl(INPUTS['candidates']), jsonl(INPUTS['audio_features'])
    decisions = json.loads(INPUTS['review'].read_text(encoding='utf-8'))['decisions']
    proposals = json.loads(INPUTS['voice_proposals'].read_text(encoding='utf-8'))['clips']

    def voice(cid):
        decision = decisions.get(cid, {})
        if decision.get('voice_verdict') == 'no' and decision.get('corrected_voice_id'):
            return decision['corrected_voice_id']
        return (proposals.get(cid) or {}).get('base_voice_id_proposed') or rows[cid].get('base_voice_id_proposed')

    def mood(cid):
        decision = decisions.get(cid, {})
        if decision.get('mood_verdict') == 'no' and decision.get('corrected_mood'):
            return canonical_review_mood(decision['corrected_mood'])
        return None

    order = sorted(rows, key=lambda cid: (rows[cid]['recording_id'].split('-')[-1].zfill(3), rows[cid]['audio_start']))
    narrator = [cid for cid in order if voice(cid) == 'narrator_baseline' and mood(cid)]
    return rows, features, decisions, narrator, mood


def alignment_words(rows: dict, clips: list[str]) -> tuple[dict, dict]:
    needed = {rows[cid]['recording_sha256'] for cid in clips}
    words, hashes = {}, {}
    for path in sorted((FULL / 'recordings').glob('*/align.json')):
        doc = json.loads(path.read_text(encoding='utf-8'))
        if doc['source']['sha256'] in needed:
            words[doc['source']['sha256']] = sorted((w['start'], w['end'], w['text']) for win in doc['windows'] for w in win['words'])
            hashes[str(path.relative_to(ROOT))] = sha(path)
    return words, hashes


def pace(row: dict, words: list) -> dict | None:
    spoken = [w for w in words if w[0] >= row['audio_start'] - .05 and w[1] <= row['audio_end'] + .05]
    if len(spoken) < 4:
        return None
    syllables = [max(1, len(VOWELS.findall(text))) for _, _, text in spoken]
    durations = [max(end - start, .01) for start, end, _ in spoken]
    span = spoken[-1][1] - spoken[0][0]
    gaps = [b[0] - a[1] for a, b in zip(spoken, spoken[1:])]
    per_syllable = sum(durations) / sum(syllables)
    return {'articulation_syl_per_s': sum(syllables) / sum(durations), 'speaking_syl_per_s': sum(syllables) / span,
            'pause_share': max(0., 1 - sum(durations) / span),
            'pauses_over_150ms_per_10_words': 10 * sum(gap > .15 for gap in gaps) / len(spoken),
            'final_word_lengthening': durations[-1] / syllables[-1] / per_syllable}


def effect(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    return float((a.mean() - b.mean()) / np.sqrt((a.var() + b.var()) / 2))


def compare(values: dict, clips: list[str], calm: dict, keys: list[str]) -> dict:
    result = {}
    for key in keys:
        a = [values[c][key] for c in clips if calm[c]]
        b = [values[c][key] for c in clips if not calm[c]]
        result[key] = {'calm_mean': float(np.nanmean(a)), 'normal_mean': float(np.nanmean(b)), 'cohen_d': effect(a, b)}
    return result


def within(values: dict, clips: list[str], calm: dict, keys: list[str], group) -> dict:
    groups = defaultdict(lambda: ([], []))
    for cid in clips:
        groups[group(cid)][0 if calm[cid] else 1].append(cid)
    mixed = [pair for pair in groups.values() if pair[0] and pair[1]]
    result = {'groups': len(mixed)}
    for key in keys:
        diffs = np.array([np.nanmean([values[c][key] for c in a]) - np.nanmean([values[c][key] for c in b]) for a, b in mixed])
        diffs = diffs[~np.isnan(diffs)]
        result[key] = {'median_calm_minus_normal': float(np.median(diffs)), 'share_groups_calm_lower': float(np.mean(diffs < 0))}
    return result


def held_out(make, X, y, groups, clips):
    from sklearn.metrics import balanced_accuracy_score, roc_auc_score
    from sklearn.model_selection import GroupKFold
    probability = np.zeros(len(y))
    for train, test in GroupKFold(n_splits=FOLDS).split(np.zeros(len(y)), y, groups):
        model = make()
        pick = (lambda index: [X[i] for i in index]) if isinstance(X, list) else (lambda index: X[index])
        model.fit(pick(train), y[train])
        probability[test] = model.predict_proba(pick(test))[:, 1]
    return ({'auc': float(roc_auc_score(y, probability)),
             'balanced_accuracy': float(balanced_accuracy_score(y, probability >= .5)), 'clips': len(y)},
            {cid: round(float(p), 4) for cid, p in zip(clips, probability)})


def analyse() -> dict:
    from sklearn.decomposition import PCA
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    import sklearn

    rows, features, decisions, narrator, mood = load()
    clips = [cid for cid in narrator if mood(cid) in ('calm', 'neutral')]
    calm = {cid: mood(cid) == 'calm' for cid in clips}
    y = np.array([calm[c] for c in clips])
    chapter = {cid: rows[cid]['chapter_id'] for cid in clips}
    paragraph = {cid: (rows[cid]['source_id'], rows[cid]['paragraph_text']) for cid in clips}
    report = {'narrator_mood_counts': dict(Counter(mood(c) for c in narrator).most_common()),
              'calm': int(y.sum()), 'normal': int((~y).sum())}

    shares = defaultdict(list)
    for cid in clips:
        shares[chapter[cid]].append(calm[cid])
    report['calm_share_by_chapter'] = {ch: {'calm_share': float(np.mean(v)), 'clips': len(v)} for ch, v in sorted(shares.items())}
    sequence = [calm[c] for c in clips]
    report['sequence'] = {'next_clip_same_style': float(np.mean([a == b for a, b in zip(sequence, sequence[1:])])),
                          'chance': float(y.mean() ** 2 + (1 - y.mean()) ** 2)}

    acoustic = {cid: {key: (features[cid].get(key) if features[cid].get(key) is not None else np.nan) for key in ACOUSTIC} for cid in clips}
    report['acoustics'] = {'all': compare(acoustic, clips, calm, ACOUSTIC),
                           'within_chapters': within(acoustic, clips, calm, ACOUSTIC, chapter.get),
                           'within_paragraphs': within(acoustic, clips, calm, ACOUSTIC, paragraph.get)}

    words, alignment_hashes = alignment_words(rows, clips)
    timed = {cid: p for cid in clips if (p := pace(rows[cid], words.get(rows[cid]['recording_sha256'], [])))}
    timed_clips = [c for c in clips if c in timed]
    report['pace'] = {'clips': len(timed_clips), 'all': compare(timed, timed_clips, calm, PACE),
                      'within_chapters': within(timed, timed_clips, calm, PACE, chapter.get),
                      'within_paragraphs': within(timed, timed_clips, calm, PACE, paragraph.get)}

    groups = np.array([chapter[c] for c in clips])
    mixed_chapters = {ch for ch, v in shares.items() if 0 < np.mean(v) < 1}
    mixed = np.array([chapter[c] in mixed_chapters for c in clips])
    balanced = lambda: LogisticRegression(max_iter=3000, class_weight='balanced')
    tfidf = lambda: make_pipeline(TfidfVectorizer(min_df=2, sublinear_tf=True), balanced())
    text = [rows[c]['paragraph_text'] + ' || ' + rows[c]['source_text'] for c in clips]
    sentence = [rows[c]['source_text'] for c in clips]
    acoustic_matrix = np.array([[acoustic[c][k] for k in ACOUSTIC] for c in clips], float)
    embeddings = np.load(INPUTS['embeddings'], allow_pickle=True)
    index = {cid: n for n, cid in enumerate(embeddings['ids'])}
    if any(embeddings['audio_sha256'][index[c]] != rows[c]['exact_audio_sha256'] for c in clips):
        raise SystemExit('Embedding audio hashes no longer match the board clips.')
    predictors, predictions = {}, {}
    tests = [('sentence_words', lambda: tfidf(), sentence, None),
             ('paragraph_and_sentence_words', lambda: tfidf(), text, None),
             ('paragraph_and_sentence_words_mixed_chapters', lambda: tfidf(), text, mixed),
             ('acoustic_measurements', lambda: make_pipeline(SimpleImputer(), StandardScaler(), balanced()), acoustic_matrix, None)]
    for block in ('wave', 'emotion', 'timbre'):
        X = np.nan_to_num(np.array([embeddings[block][index[c]] for c in clips], float))
        centred = X.copy()
        for ch in set(groups):
            centred[groups == ch] -= centred[groups == ch].mean(0)
        model = lambda width=X.shape[1]: make_pipeline(StandardScaler(), PCA(n_components=min(32, width), random_state=0),
                                                       LogisticRegression(max_iter=3000, class_weight='balanced', C=.3))
        tests += [(f'{block}_embedding', model, X, None),
                  (f'{block}_embedding_mixed_chapters_chapter_average_removed', model, centred, mixed)]
    for name, make, X, subset in tests:
        keep = np.ones(len(y), bool) if subset is None else subset
        chosen = [c for c, k in zip(clips, keep) if k]
        data = [x for x, k in zip(X, keep) if k] if isinstance(X, list) else X[keep]
        predictors[name], predictions[name] = held_out(make, data, y[keep], groups[keep], chosen)
    report['held_out_chapter_prediction'] = predictors
    from sklearn.model_selection import GroupKFold
    report['chapter_folds_all_clips'] = {str(fold): sorted(set(groups[test])) for fold, (_, test) in
                                         enumerate(GroupKFold(n_splits=FOLDS).split(np.zeros(len(y)), y, groups))}
    report['chapter_folds_mixed_chapters'] = {str(fold): sorted(set(groups[mixed][test])) for fold, (_, test) in
                                              enumerate(GroupKFold(n_splits=FOLDS).split(np.zeros(mixed.sum()), y[mixed], groups[mixed]))}
    return {'schema': 1, 'kind': 'read_only_analysis', 'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'script': str(Path(__file__).relative_to(ROOT)), 'script_sha256': sha(Path(__file__)),
            'inputs': {name: {'path': str(path.relative_to(ROOT)), 'sha256': sha(path)} for name, path in INPUTS.items()},
            'alignment_inputs': alignment_hashes,
            'method': {'population': 'narrator_baseline clips with an explicit human mood of calm or neutral (Normal)',
                       'cross_validation': f'GroupKFold({FOLDS}) by chapter; scores are out of fold',
                       'sklearn': sklearn.__version__, 'numpy': np.__version__},
            'results': report, 'out_of_fold_calm_probability': predictions,
            'interpretation_limits': 'A failed predictor means these features did not demonstrate the distinction; it '
                                     'does not show the human labels are wrong. Chapter effects do not identify whether '
                                     'performance, recording, mastering or labeling causes them.'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, default=OUT)
    args = parser.parse_args()
    warnings.filterwarnings('ignore')
    report = analyse()
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"claude-calm-normal-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    summary = {name: round(value['auc'], 2) for name, value in report['results']['held_out_chapter_prediction'].items()}
    print(json.dumps({'report': str(path), 'calm': report['results']['calm'], 'normal': report['results']['normal'],
                      'held_out_auc': summary}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
