"""Run the independent Whisper witness over held listener-stress takes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]

from audit_higgs_secondary_asr import audit
from rimsky.util import file_digest, write_json


def read(path: Path):
    return json.loads(path.read_text('utf8'))


def snapshots(folder: Path, unit_ids: list[str], output: Path) -> list[Path]:
    result = []
    snapshot_dir = output / 'snapshots'
    for uid in unit_ids:
        for take in sorted((folder / 'pronunciation-revisions' / uid).glob('take-*')):
            plans = take / 'render-plan.json'
            receipts = take / 'receipts.json'
            checks = sorted(take.glob('word-checks*.json'))
            if not plans.is_file() or not receipts.is_file() or not checks:
                continue
            plan = read(plans); receipt = read(receipts).get('takes', {}).get(uid)
            check = read(checks[0]).get('checks', {}).get(uid)
            unit = next((row for row in plan.get('units', []) if row.get('id') == uid), None)
            if not unit or not receipt or not check or check.get('status') == 'asr_exact_match':
                continue
            audio = take / receipt['audio']
            if file_digest(audio) != receipt['audio_sha256'] or check.get('audio_sha256') != receipt['audio_sha256']:
                raise ValueError('Held listener-stress audio changed: ' + str(audio))
            document = {uid: {'unit': unit, 'receipt': receipt, 'path': str(audio.resolve()),
                              'word_check': check}}
            path = snapshot_dir / f'{uid}-{take.name}.json'
            if path.exists() and read(path) != document:
                raise ValueError('Frozen listener-stress witness snapshot changed: ' + str(path))
            if not path.exists():
                write_json(path, document)
            result.append(path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generation', type=Path, required=True)
    parser.add_argument('--unit-id', action='append', required=True)
    args = parser.parse_args()
    folder = args.generation.resolve()
    output = folder / 'secondary-asr-listener-stress-whisper'
    paths = snapshots(folder, args.unit_id, output)
    if not paths:
        raise ValueError('No held listener-stress takes were found.')
    audit(paths, output)


if __name__ == '__main__':
    main()
