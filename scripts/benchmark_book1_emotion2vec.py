"""Read-only preliminary agreement benchmark against the saved Book 1 labels.

Emotion2vec+ features are evaluated on held-out chapters with the same linear
predictor as the waveform baseline. No label, training gate or UI is modified.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / 'training-work/character-rb1/fine-tune-all'
OUT = ROOT / 'training-work/character-rb1/emotion2vec-benchmark-20260929'
MODEL = ROOT / 'training-work/models/emotion2vec-plus-base'
MODEL_REVISION = 'b318240bfe67db81a8c572ecb37ce9c3759b81c9'
BASIC_FAILURES = {'missing_or_changed_audio', 'unexpected_pcm_format', 'empty_audio',
                  'duration_mismatch', 'clipping', 'level_outlier'}
ACOUSTIC_KEYS = ['median_pitch_hz', 'pitch_range_semitones', 'active_level_dbfs',
                 'average_level_dbfs', 'level_range_db', 'internal_pause_fraction',
                 'envelope_peaks_per_second', 'periodic_fraction',
                 'pitch_start_offset_semitones', 'pitch_end_offset_semitones',
                 'pitch_ending_change_semitones']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def prepare_snapshot(out=OUT):
    protected = ROOT / 'manual-labeling/reality-benders-1/review.json'
    review_source = protected if protected.is_file() else BOARD / 'review-draft.json'
    raw = review_source.read_bytes()
    if protected.is_file() and raw != (BOARD / 'review-draft.json').read_bytes():
        raise ValueError('Protected manual labels differ from the working review. Refresh the archive before training.')
    review = json.loads(raw)
    corpus_hash = sha(BOARD / 'candidates.jsonl')
    precision, quality, acoustics = [read_json(BOARD / p) for p in
                                   ['precision-v2/status.json', 'quality-screen.json', 'audio-acoustics.json']]
    if any(data['candidates_sha256'] != corpus_hash for data in [review, precision, quality, acoustics]):
        raise ValueError('Stale review or audio measurements')
    taxonomy = read_json(ROOT / 'training/voice_families.json')
    families = {f['id'] for f in taxonomy['families']}
    aliases = taxonomy.get('legacy_aliases', {})
    mood_aliases = {'exhausted': 'tired', 'wistful': 'reminiscent', 'uncertain': 'hesitant',
                    'cheerful, excited': 'excited'}
    selected, seen_audio = [], set()
    for line in (BOARD / 'candidates.jsonl').read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        d = review['decisions'].get(row['id'], {})
        voice = (d.get('confirmed_voice_id') or row['base_voice_id_proposed']) if d.get('voice_verdict') == 'yes' else d.get('corrected_voice_id')
        mood = ', '.join(row['mood_proposed'] or ['neutral']) if d.get('mood_verdict') == 'yes' else d.get('corrected_mood')
        voice, mood = aliases.get(voice, voice), mood_aliases.get(mood, mood)
        p, q, a = [data['clips'].get(row['id'], {}) for data in [precision, quality, acoustics]]
        if (voice not in families or not mood or d.get('rejected') or d.get('boundary_start') or d.get('boundary_end')
                or row['role'] == 'mixed' or p.get('status') != 'precision_pass'
                or q.get('audio_sha256') != row['audio_sha256'] or BASIC_FAILURES.intersection(q.get('reasons', []))
                or not p.get('audio_sha256') or a.get('audio_sha256') != p['audio_sha256']):
            continue
        if p['audio_sha256'] in seen_audio:
            continue
        path = (ROOT / p['audio']).resolve()
        if not path.is_relative_to((BOARD / 'precision-v2/clips').resolve()) or sha(path) != p['audio_sha256']:
            raise ValueError('Changed exact WAV')
        seen_audio.add(p['audio_sha256'])
        check = d.get('bucket_review', {})
        verified = (check.get('verdict') == 'consistent' and check.get('voice_family_id') == voice
                    and mood_aliases.get(check.get('mood'), check.get('mood')) == mood
                    and check.get('audio_sha256') == p['audio_sha256'])
        selected.append({**row, 'voice_family': voice, 'mood': mood, 'exact_audio': p['audio'],
                         'exact_audio_sha256': p['audio_sha256'], 'step2_verified': verified, 'acoustics': a})
    out.mkdir(parents=True, exist_ok=True)
    (out / 'review-snapshot.json').write_bytes(raw)
    (out / 'benchmark-clips.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in selected), encoding='utf-8')
    return selected, {'review_sha256': hashlib.sha256(raw).hexdigest(), 'candidates_sha256': corpus_hash,
                      'review_source': review_source.relative_to(ROOT).as_posix(),
                      'review_updated_at': review.get('updated_at')}


def extract(rows, device='cpu', expected_dimension=768):
    # Python 3.12 removed stdlib distutils; initialize setuptools' compatibility
    # module when using the isolated dependency overlay.
    import setuptools  # noqa: F401
    import numpy as np
    import torch
    import soundfile as sf
    import librosa
    from funasr import AutoModel

    logging.disable(logging.INFO)
    torch.set_num_threads(4)
    if device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('Requested CUDA is unavailable')
    cache_dir = OUT / ('features-' + MODEL_REVISION[:12])
    cache_dir.mkdir(exist_ok=True)
    features, broad_counts, devices = {}, Counter(), Counter()
    model, started = None, time.monotonic()
    for i, row in enumerate(rows):
        file = cache_dir / (row['exact_audio_sha256'] + '.npz')
        if file.exists():
            with np.load(file, allow_pickle=False) as data:
                vector, scores, labels = data['features'], data['scores'], data['labels']
                feature_device = str(data['device']) if 'device' in data else 'cpu'
        else:
            if model is None:
                model = AutoModel(model=str(MODEL), device=device, ncpu=4,
                                  disable_update=True, disable_pbar=True, disable_log=True, log_level='ERROR')
                print(f'Loaded {MODEL.name} on {device}, using four CPU threads.', flush=True)
            audio, sr = sf.read(ROOT / row['exact_audio'], dtype='float32', always_2d=True)
            mono = audio.mean(axis=1)
            y = librosa.resample(mono, orig_sr=sr, target_sr=16000) if sr != 16000 else mono
            result = model.generate(input=y, granularity='utterance', extract_embedding=True,
                                    disable_pbar=True, disable_log=True)[0]
            vector = np.asarray(result['feats'], dtype=np.float32).reshape(-1)
            scores, labels = np.asarray(result.get('scores', []), dtype=np.float32), np.asarray(result.get('labels', []), dtype=str)
            if vector.shape != (expected_dimension,) or not np.isfinite(vector).all():
                raise ValueError(f'Invalid embedding for {row["id"]}: {vector.shape}')
            temporary = file.with_suffix('.npz.tmp')
            with temporary.open('wb') as stream:
                np.savez_compressed(stream, features=vector, scores=scores, labels=labels, device=np.asarray(device))
            temporary.replace(file)
            feature_device = device
        features[row['id']] = vector
        devices[feature_device] += 1
        if scores.size:
            broad_counts[str(labels[int(np.argmax(scores))])] += 1
        if (i+1) % 25 == 0 or i+1 == len(rows):
            print(f'Features {i+1}/{len(rows)}; elapsed {time.monotonic()-started:.1f}s.', flush=True)
    return features, dict(broad_counts), dict(devices)


def evaluate(rows, embeddings, class_weight='balanced'):
    import numpy as np
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, classification_report
    from sklearn.model_selection import StratifiedGroupKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from threadpoolctl import threadpool_limits

    results, predictions = {}, []
    for task, pool, label in [('narrator_mood', [r for r in rows if r['voice_family']=='narrator_baseline'], 'mood'),
                              ('voice_family', rows, 'voice_family')]:
        counts = Counter(r[label] for r in pool)
        chapters = defaultdict(set)
        for r in pool:
            chapters[r[label]].add(r['chapter_id'])
        supported = {key for key in counts if counts[key]>=10 and len(chapters[key])>=3}
        sample = [r for r in pool if r[label] in supported]
        if len(supported)<2:
            results[task] = {'status':'too_few_supported_labels'}
            continue
        y = np.asarray([r[label] for r in sample])
        groups = np.asarray([r['chapter_id'] for r in sample])
        waves = np.asarray([[r['acoustics'].get(key) if r['acoustics'].get(key) is not None else np.nan for key in ACOUSTIC_KEYS]+
                            [v if v is not None else np.nan for v in r['acoustics'].get('relative_pitch_curve_semitones', [None]*12)] for r in sample])
        rich = np.stack([embeddings[r['id']] for r in sample])
        splits = list(StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=20260929).split(waves, y, groups))
        variants = {'waveform': waves, 'emotion2vec': rich, 'combined': np.concatenate([waves, rich], axis=1)}
        outcome = {'samples': len(sample), 'classes': len(supported), 'class_counts': dict(Counter(y.tolist())),
                   'excluded_rare_classes': {key: counts[key] for key in counts if key not in supported},
                   'folds': [], 'metrics': {}}
        majority = np.empty(len(sample), dtype=object)
        for train, test in splits:
            assert not set(groups[train]).intersection(groups[test])
            majority[test] = Counter(y[train]).most_common(1)[0][0]
            outcome['folds'].append({'train_chapters':sorted(set(groups[train])), 'test_chapters':sorted(set(groups[test])),
                                     'unseen_test_labels':sorted(set(y[test])-set(y[train]))})
        outcome['metrics']['majority'] = {'top1': accuracy_score(y, majority), 'macro_f1': f1_score(y, majority, average='macro', zero_division=0),
                                          'balanced_accuracy': balanced_accuracy_score(y, majority)}
        for name, x in variants.items():
            pred = np.empty(len(sample), dtype=object)
            top3 = np.zeros(len(sample), dtype=bool)
            rankings = [None]*len(sample)
            for fold, (train, test) in enumerate(splits):
                # Same fixed predictor and regularization for every feature set.
                pipeline = make_pipeline(SimpleImputer(strategy='median', add_indicator=True), StandardScaler(),
                                         LogisticRegression(C=.1, class_weight=class_weight, max_iter=1000))
                with threadpool_limits(limits=4):
                    pipeline.fit(x[train], y[train])
                    prob = pipeline.predict_proba(x[test])
                classes = pipeline[-1].classes_
                order = np.argsort(-prob, axis=1)[:, :3]
                pred[test] = classes[order[:, 0]]
                for index, rank in zip(test, order):
                    rankings[index] = classes[rank].tolist()
                    top3[index] = y[index] in rankings[index]
                print(f'{task} / {name}: fold {fold+1}/5 complete.', flush=True)
            outcome['metrics'][name] = {'top1': accuracy_score(y, pred), 'top3': float(top3.mean()),
                                         'macro_f1': f1_score(y, pred, average='macro', zero_division=0),
                                         'balanced_accuracy': balanced_accuracy_score(y, pred),
                                         'per_class': classification_report(y, pred, output_dict=True, zero_division=0)}
            predictions.extend({'id': row['id'], 'chapter': row['chapter_id'], 'task':task, 'variant':name,
                                'saved_label':str(actual), 'top3': ranking}
                               for row, actual, ranking in zip(sample, y, rankings))
        results[task] = outcome
    return results, predictions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-clips', type=int, default=0, help='Optional extraction smoke sample, not a full benchmark')
    parser.add_argument('--device', choices=['cpu','cuda'], default='cpu')
    args = parser.parse_args()
    rows, provenance = prepare_snapshot()
    if args.max_clips:
        rows = rows[:args.max_clips]
    print(f'Snapshot: {len(rows)} unique labeled exact WAVs; {sum(r["step2_verified"] for r in rows)} Step 2 verified.', flush=True)
    features, broad_counts, devices = extract(rows, args.device)
    results, predictions = evaluate(rows, features) if not args.max_clips else ({}, [])
    report = {**provenance, 'created_at':datetime.now(timezone.utc).isoformat(), 'model':'emotion2vec/emotion2vec_plus_base',
              'model_revision':MODEL_REVISION, 'requested_extraction_device':args.device, 'cached_feature_devices':devices,
              'clips':len(rows), 'step2_verified_clips':sum(r['step2_verified'] for r in rows),
              'review_changed_during_run':sha(BOARD/'review-draft.json')!=provenance['review_sha256'],
              'method':'Five fixed chapter-held-out folds; same balanced logistic regression C=0.1; learned preprocessing fitted only on train folds.',
              'standard_nine_class_predictions':broad_counts, 'results':results,
              'limits':['Preliminary agreement with saved Step 1 labels; grouped verification is unfinished.',
                        'One book and narrator; no evidence yet for transfer to Book 2.',
                        'Rare labels excluded from scored tasks; predictor probabilities are not calibrated.',
                        'No text/context features in this comparison. Voice-family performance tests enacted voices of one narrator, not speaker identity.',
                        'No production model, label changes, or automatic approval.']}
    save_json(OUT/'report.json', report)
    (OUT/'held-out-predictions.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in predictions), encoding='utf-8')
    lines = ['# Emotion2vec preliminary Book 1 benchmark', '',
             'Agreement with saved labels, not final validated accuracy. Chapters are held out in five folds.', '',
             f'{len(rows)} unique exact WAVs; {report["step2_verified_clips"]} Step 2 verified.', '',
             '| Task | Features | Top 1 agreement | Top 3 agreement | Macro F1 |', '|---|---|---:|---:|---:|']
    for task, result in results.items():
        for name, metrics in result.get('metrics', {}).items():
            lines.append(f'| {task} | {name} | {metrics["top1"]:.1%} | '+
                         (f'{metrics["top3"]:.1%}' if 'top3' in metrics else '—')+f' | {metrics["macro_f1"]:.3f} |')
    lines += ['', '## Limits', ''] + ['- '+limit for limit in report['limits']]
    (OUT/'REPORT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({'report': str(OUT/'REPORT.md'), 'results':{k:{n:{m:v for m,v in metrics.items() if m!='per_class'}
          for n,metrics in r.get('metrics',{}).items()} for k,r in results.items()}}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
