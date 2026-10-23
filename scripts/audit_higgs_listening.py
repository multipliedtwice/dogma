"""Independently transcribe generated listening samples, with exact input hashes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
import worker
from rimsky.util import file_digest, write_json


def audit(folder):
    plan_path, result_path = folder / 'plan.json', folder / 'results.json'
    plan = json.loads(plan_path.read_text('utf-8'))
    results = json.loads(result_path.read_text('utf-8'))
    if results['plan_sha256'] != file_digest(plan_path):
        raise ValueError('Listening plan and rendered results do not match.')
    cases = {c['id']: c for c in plan['cases']}
    prefix = 'training-work/models/Qwen3-ASR-0.6B'
    files = {p.relative_to(ROOT).as_posix(): file_digest(p) for p in (ROOT / prefix).rglob('*') if p.is_file() and '.cache' not in p.parts}
    pack = {'_root': str(ROOT), 'speech': {'verifier': {'backend': 'qwen3_asr', 'path': prefix}}, 'files': files}
    checked = []
    for row in results['results']:
        if not row.get('audio'):
            continue
        audio = folder / row['audio']
        if file_digest(audio) != row['audio_sha256']:
            raise ValueError('Rendered take changed before word verification.')
        result = worker.verify_audio({'pack': pack, 'audio': str(audio), 'text': cases[row['case_id']]['text'], 'language': 'Russian'})
        checked.append({'case_id': row['case_id'], 'variant': row['variant'], 'audio': row['audio'], **result})
        write_json(folder / 'word-checks.json', {'plan_sha256': file_digest(plan_path), 'results_sha256': file_digest(result_path),
                                               'verifier_files': files, 'checks': checked,
                                               'limits': 'ASR agreement checks words, not voice identity, pronunciation detail or artistic quality.'})
        print(row['case_id'], row['variant'], result['status'], flush=True)
    if file_digest(result_path) != json.loads((folder / 'word-checks.json').read_text('utf-8'))['results_sha256']:
        raise ValueError('Render results changed during verification.')
    return {'checked': len(checked), 'exact': sum(c['status'] == 'asr_exact_match' for c in checked)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.folder.resolve())))


if __name__ == '__main__':
    main()
