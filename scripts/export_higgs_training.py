"""Export frozen, gated human evidence as exact WAVs for local Higgs training.

No MP3 recutting, inferred labels, delivery_proposed or Book 3 training. The
assignment benchmark's chapter split is retained for synthesis validation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import wave
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.higgs_reference import check_reference
from rimsky.util import SpeechHoldError
BOOKS = {'reality-benders-1', 'reality-benders-2-full'}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def source_audio(root, row):
    if row['book'] == 'reality-benders-1':
        return root / 'manual-labeling/reality-benders-1/audio' / (row['audio_sha256'] + '.wav')
    if row['book'] == 'reality-benders-2-full':
        # The frozen exact-ASR gate binds this specific cut, not a new MP3 cut.
        return root / 'training-work/character-rb2/full/clips' / (row['id'] + '.wav')
    raise ValueError('Only reviewed Books 1 and 2 may supply training audio.')


def verified_evidence(root, benchmark):
    manifest = read(benchmark / 'evidence-manifest.json')
    for name, expected in manifest['inputs'].items():
        if sha(root / name) != expected:
            raise ValueError(f'Frozen input changed: {name}. Rebuild evidence before training.')
    for name in ('synthesis-evidence.jsonl', 'split.json'):
        if sha(benchmark / name) != manifest['outputs'][name]:
            raise ValueError(f'Frozen output changed: {name}')
    for book in BOOKS:
        folder = root / 'manual-labeling' / book
        review_hash = sha(folder / 'review.json')
        if read(folder / 'latest.json')['review_sha256'] != review_hash:
            raise ValueError(f'Canonical review checksum mismatch: {book}')
        archive = read(folder / 'archive-manifest.json')
        if archive.get('support_snapshot_review_sha256', archive.get('review_sha256')) != review_hash:
            raise ValueError(f'Archive must be refreshed: {book}')
    evidence = rows(benchmark / 'synthesis-evidence.jsonl')
    durations = {}
    seen = set()
    for row in evidence:
        key = (row['book'], row['id'])
        if key in seen:
            raise ValueError('Duplicate training evidence.')
        seen.add(key)
        if (row['book'] not in BOOKS or row['unit'] != 'clip' or row.get('boundary_held')
                or row['voice_basis'] != 'human' or row['mood_basis'] != 'human'
                or row['voice'] == 'voice_pending' or row['mood'] == 'mood_pending'):
            raise ValueError(f'Ineligible synthesis evidence: {key}')
        path = source_audio(root, row)
        if sha(path) != row['audio_sha256']:
            raise ValueError(f'Audited WAV changed: {key}')
        with wave.open(str(path)) as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 24000:
                raise ValueError(f'Expected audited mono 24 kHz PCM16: {key}')
            durations[key] = wav.getnframes() / wav.getframerate()
    return evidence, read(benchmark / 'split.json'), durations


def export(root, benchmark, output):
    root, benchmark, output = Path(root), Path(benchmark), Path(output)
    if output.exists():
        raise ValueError('Use a fresh output directory; existing datasets are immutable.')
    evidence, split, durations = verified_evidence(root, benchmark)
    test_chapters = {book: set(value['test_chapters']) for book, value in split['books'].items()}
    pools = defaultdict(list)
    for row in evidence:
        if row['chapter_id'] not in test_chapters[row['book']]:
            pools[row['voice']].append(row)
    # Reserve two independent reviewed reference takes per family. They never
    # enter target training/validation, and no target conditions on itself.
    refs, reference_rejections = {}, []
    for voice, pool in pools.items():
        baseline = Counter(r['mood'] for r in pool).most_common(1)[0][0]
        ranked = sorted(pool, key=lambda r: (r['mood'] != baseline,
                         not 2 <= durations[(r['book'], r['id'])] <= 10,
                         abs(durations[(r['book'], r['id'])] - 5), r['book'], r['id']))
        safe=[]
        for row in ranked:
            try:check_reference(source_audio(root,row),row['audio_sha256'])
            except SpeechHoldError as exc:
                reference_rejections.append({'book':row['book'],'id':row['id'],'voice_family':voice,
                    'audio_sha256':row['audio_sha256'],'reason':str(exc),'scope':'reference_selection_only'})
                continue
            safe.append(row)
            if len(safe)==2:break
        refs[voice] = safe if len(safe)==2 and len(pool)>=3 else []
    reserved_hashes = {r['audio_sha256'] for group in refs.values() for r in group}
    train_hashes, valid_hashes = set(), set()
    prepared, excluded = {'train': [], 'validation': []}, []
    for row in evidence:
        partition = 'validation' if row['chapter_id'] in test_chapters[row['book']] else 'train'
        if row['audio_sha256'] in reserved_hashes:
            excluded.append({'book': row['book'], 'id': row['id'], 'reason': 'reserved reference'})
            continue
        if not refs.get(row['voice']):
            excluded.append({'book': row['book'], 'id': row['id'], 'reason': 'insufficient development references'})
            continue
        group_hashes = valid_hashes if partition == 'validation' else train_hashes
        group_hashes.add(row['audio_sha256'])
        reference = refs[row['voice']][0]
        prepared[partition].append({
            'id': row['book'] + ':' + row['id'], 'text': row['text'],
            'audio': 'audio/' + row['audio_sha256'] + '.wav',
            'ref_audio': 'audio/' + reference['audio_sha256'] + '.wav', 'ref_text': reference['text'],
            'metadata': {'book': row['book'], 'chapter_id': row['chapter_id'],
                         'voice_family': row['voice'], 'mood': row['mood'],
                         'voice_basis': 'human', 'mood_basis': 'human',
                         'audio_sha256': row['audio_sha256'], 'ref_audio_sha256': reference['audio_sha256'],
                         'seconds': durations[(row['book'], row['id'])]}})
    if train_hashes & valid_hashes:
        raise ValueError('Exact audio occurs in both train and validation. Resolve before training.')
    output.mkdir(parents=True)
    (output / 'audio').mkdir()
    audio_paths = {}
    for row in evidence:
        wanted = train_hashes | valid_hashes | reserved_hashes
        if row['audio_sha256'] in wanted and row['audio_sha256'] not in audio_paths:
            destination = output / 'audio' / (row['audio_sha256'] + '.wav')
            shutil.copyfile(source_audio(root, row), destination)
            if sha(destination) != row['audio_sha256']:
                raise ValueError('Exported WAV hash differs from audited bytes.')
            audio_paths[destination.relative_to(output).as_posix()] = row['audio_sha256']
    for partition, items in prepared.items():
        (output / (partition + '.jsonl')).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in items), encoding='utf-8')
    references = {voice: [{'audio': 'audio/' + r['audio_sha256'] + '.wav', 'text': r['text'],
                          'mood': r['mood'], 'reviewed': True, 'evidence_id': r['book'] + ':' + r['id'],
                          'audio_sha256': r['audio_sha256']} for r in group] for voice, group in refs.items() if group}
    write(output / 'references.json', references)
    coverage = {voice: {partition: dict(Counter(r['metadata']['mood'] for r in items if r['metadata']['voice_family'] == voice))
                       for partition, items in prepared.items()} for voice in sorted(pools)}
    report = {'schema': 1, 'trusted_evidence': len(evidence), 'counts': {p: len(r) for p, r in prepared.items()},
              'reference_families': len(references), 'coverage': coverage, 'excluded': excluded,
              'reference_rejections':reference_rejections,
              'families_without_validation': [v for v, c in coverage.items() if not c['validation']],
              'conditioning': 'Exact reviewed text plus held-out reviewed voice reference. Human mood is preserved as metadata; no unreviewed delivery or unvalidated mood tag is fed to Higgs.',
              'book3': 'evaluation only; excluded', 'assignment_split_sha256': sha(benchmark / 'split.json')}
    write(output / 'report.json', report)
    files = {p.name: sha(p) for p in output.iterdir() if p.is_file()}
    write(output / 'manifest.json', {'schema': 1, 'builder_sha256': sha(Path(__file__)),
                                    'evidence_manifest_sha256': sha(benchmark / 'evidence-manifest.json'),
                                    'files': {**files, **audio_paths}})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--benchmark', type=Path, default=ROOT / 'training-work/assignment-benchmark/m1-20261012')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = export(ROOT, args.benchmark.resolve(), args.output.resolve())
    print(json.dumps({k: result[k] for k in ('trusted_evidence', 'counts', 'reference_families', 'families_without_validation')}, indent=2))


if __name__ == '__main__':
    main()
