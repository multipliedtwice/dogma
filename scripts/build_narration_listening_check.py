"""Blind Calm/Normal consistency check, then a labeled comparison page (Book 2).

  check    choose about 20 Calm and 20 Normal narrator clips, balanced within
           chapters that contain both, in random order. The served manifest
           holds only clip audio and text; saved labels, suggestions and bucket
           hints stay in an unserved key file.
  compare  after every blind item has an answer, publish same-paragraph
           Calm/Normal pairs with their saved labels for open listening.
  report   agreement of the fresh blind answers with the saved labels,
           separately for Calm and Normal, with 95% intervals.

Fresh answers live in their own file and never change canonical labels or
playback acceptance. Nothing here applies a pass/fail threshold.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze_book2_narration_style as style  # noqa: E402
from serve_label_pilot import LISTENING, listening_answers, listening_progress  # noqa: E402

ROOT = style.ROOT
BOARD = ROOT / 'training-work/character-rb2-full'
SERVED = BOARD / 'fine-tune-all'
PER_LABEL = 20


def audio_url(row: dict) -> str:
    return '/' + Path(row['exact_audio']).as_posix().removeprefix('training-work/')


def eligible(seed: int):
    rows, features, decisions, narrator, mood = style.load()
    clips = [cid for cid in narrator if mood(cid) in ('calm', 'neutral')
             and not (decisions.get(cid, {}).get('split_review') or {}).get('enabled')
             and (ROOT / rows[cid]['exact_audio']).is_file()
             and 1.5 <= features[cid]['duration_seconds'] <= 15]
    random.Random(seed).shuffle(clips)
    return rows, mood, clips


def choose(rows, mood, clips, per_label: int, seed: int) -> list[str]:
    """Alternate Calm and Normal picks within chapters that hold both, then fill."""
    by_chapter = defaultdict(lambda: {'calm': [], 'neutral': []})
    for cid in clips:
        by_chapter[rows[cid]['chapter_id']][mood(cid)].append(cid)
    mixed = [ch for ch, labels in sorted(by_chapter.items()) if labels['calm'] and labels['neutral']]
    random.Random(seed).shuffle(mixed)
    chosen = {'calm': [], 'neutral': []}
    while any(len(chosen[k]) < per_label for k in chosen) and any(
            by_chapter[ch]['calm'] and by_chapter[ch]['neutral'] for ch in mixed):
        for ch in mixed:
            pool = by_chapter[ch]
            if pool['calm'] and pool['neutral'] and len(chosen['calm']) < per_label and len(chosen['neutral']) < per_label:
                chosen['calm'].append(pool['calm'].pop())
                chosen['neutral'].append(pool['neutral'].pop())
    for label in chosen:
        rest = [cid for ch in sorted(by_chapter) for cid in by_chapter[ch][label]]
        chosen[label] += rest[:per_label - len(chosen[label])]
    picked = chosen['calm'] + chosen['neutral']
    random.Random(seed + 1).shuffle(picked)
    return picked


def build_check(seed: int, per_label: int, replace: bool) -> dict:
    folder = SERVED / LISTENING
    if (folder / 'manifest.json').exists() and not replace:
        raise SystemExit('A listening check already exists; pass --replace to start a new experiment.')
    rows, mood, clips = eligible(seed)
    picked = choose(rows, mood, clips, per_label, seed)
    experiment = f"calm-normal-{time.strftime('%Y%m%d-%H%M%S')}"
    manifest = {'schema': 1, 'experiment_id': experiment, 'answers': ['calm', 'neutral', 'uncertain', 'neither'],
                'items': [{'item': n, 'clip_id': cid, 'audio': audio_url(rows[cid]), 'text': rows[cid]['source_text'].strip()}
                          for n, cid in enumerate(picked, 1)]}
    key = {'schema': 1, 'experiment_id': experiment, 'created': time.strftime('%Y-%m-%dT%H:%M:%S'), 'seed': seed,
           'inputs': {name: style.sha(path) for name, path in style.INPUTS.items() if name != 'embeddings'},
           'items': {cid: {'saved_label': mood(cid), 'chapter_id': rows[cid]['chapter_id'],
                           'source_id': rows[cid]['source_id'], 'audio_sha256': rows[cid]['exact_audio_sha256']}
                     for cid in picked}}
    folder.mkdir(parents=True, exist_ok=True)
    (BOARD / 'listening-check').mkdir(parents=True, exist_ok=True)
    (BOARD / 'listening-check' / f'key-{experiment}.json').write_text(json.dumps(key, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    shutil.copyfile(HERE / 'narration_listening_check.html', folder / 'index.html')
    (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return {'experiment_id': experiment, 'items': len(picked), 'labels': dict(Counter(mood(c) for c in picked)),
            'chapters': len({rows[c]['chapter_id'] for c in picked}), 'page': f'/character-rb2-full/fine-tune-all/{LISTENING}'}


def build_compare(pairs: int, seed: int) -> dict:
    progress = listening_progress(BOARD)
    manifest = json.loads((SERVED / LISTENING / 'manifest.json').read_text(encoding='utf-8'))
    missing = len(manifest['items']) - len(progress['answered'])
    if missing:
        raise SystemExit(f'Answer the blind check first: {missing} item(s) remain.')
    rows, mood, clips = eligible(seed)
    by_paragraph = defaultdict(lambda: {'calm': [], 'neutral': []})
    for cid in clips:
        by_paragraph[(rows[cid]['source_id'], rows[cid]['paragraph_text'])][mood(cid)].append(cid)
    mixed = [labels for _, labels in sorted(by_paragraph.items()) if labels['calm'] and labels['neutral']]
    random.Random(seed).shuffle(mixed)
    published = [{'chapter_id': rows[labels['calm'][0]]['chapter_id'],
                  'takes': sorted(({'label': label, 'clip_id': cid, 'audio': audio_url(rows[cid]),
                                    'text': rows[cid]['source_text'].strip(), 'start': rows[cid]['text_start']}
                                   for label, cid in (('calm', labels['calm'][0]), ('neutral', labels['neutral'][0]))),
                                  key=lambda take: take['start'])} for labels in mixed[:pairs]]
    folder = SERVED / 'listening-compare'
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / 'narration_listening_compare.html', folder / 'index.html')
    (folder / 'manifest.json').write_text(json.dumps({'schema': 1, 'after_experiment': manifest['experiment_id'],
                                                      'pairs': published}, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return {'pairs': len(published), 'page': '/character-rb2-full/fine-tune-all/listening-compare/'}


def wilson(hits: int, total: int) -> list[float] | None:
    if not total:
        return None
    z, p = 1.96, hits / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(max(0., centre - half), 3), round(min(1., centre + half), 3)]


def report() -> dict:
    manifest = json.loads((SERVED / LISTENING / 'manifest.json').read_text(encoding='utf-8'))
    experiment = manifest['experiment_id']
    key = json.loads((BOARD / 'listening-check' / f'key-{experiment}.json').read_text(encoding='utf-8'))
    latest = {}
    for row in listening_answers(BOARD, experiment):
        latest[row['clip_id']] = row['answer']
    result = {}
    for label in ('calm', 'neutral'):
        clips = [cid for cid, item in key['items'].items() if item['saved_label'] == label]
        answers = Counter(latest[cid] for cid in clips if cid in latest)
        decided = answers['calm'] + answers['neutral']
        result[label] = {'clips': len(clips), 'answered': sum(answers.values()), 'fresh_answers': dict(answers),
                         'same_as_saved': answers[label], 'agreement_of_all_answered': answers[label] / sum(answers.values()) if answers else None,
                         'agreement_of_all_answered_95ci': wilson(answers[label], sum(answers.values())),
                         'agreement_when_calm_or_normal_chosen': answers[label] / decided if decided else None,
                         'agreement_when_calm_or_normal_chosen_95ci': wilson(answers[label], decided)}
    doc = {'schema': 1, 'experiment_id': experiment, 'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'by_saved_label': result, 'limits': 'Agreement is reported per saved label with uncertainty; no pass/fail '
                                               'threshold is applied. Uncertain and Neither answers are kept, not dropped.'}
    path = BOARD / 'listening-check' / f'report-{experiment}.json'
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return {'report': str(path), **result}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('stage', choices=('check', 'compare', 'report'))
    parser.add_argument('--seed', type=int, default=20261010)
    parser.add_argument('--per-label', type=int, default=PER_LABEL)
    parser.add_argument('--pairs', type=int, default=12)
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    result = (build_check(args.seed, args.per_label, args.replace) if args.stage == 'check'
              else build_compare(args.pairs, args.seed) if args.stage == 'compare' else report())
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
