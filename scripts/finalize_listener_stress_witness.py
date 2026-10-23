"""Bind an independently verified installed take back to its queue record."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from rimsky.util import file_digest, now, write_json


def read(path: Path):
    return json.loads(path.read_text('utf8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generation', type=Path, required=True)
    parser.add_argument('--queue-id', required=True)
    parser.add_argument('--report', type=Path, action='append', required=True)
    args = parser.parse_args()
    folder = args.generation.resolve()
    path = folder / 'listener-stress-queue' / f'{args.queue_id}.json'
    item = read(path); uid = item['report']['segment_id']
    applied_path = folder / 'pronunciation-revisions' / uid / 'applied.json'
    registry = read(folder / 'spoken-take-revisions.json')['units'][uid]
    applied = read(applied_path)
    if applied.get('unit_id') != uid or applied.get('new_sha256') != read(folder / 'progress.json')['chapters'][item['report']['chapter_id']]['wav_sha256']:
        raise ValueError('Installed listener-stress take is not the current chapter publication.')
    reports = []
    for raw in args.report:
        report = raw.resolve()
        if not report.is_relative_to(folder) or not report.is_file():
            raise ValueError('Independent witness escaped the generation.')
        reports.append({'path': str(report), 'sha256': file_digest(report)})
    item.update(status='applied', updated=now(), completed=now(), error='', traceback='',
                instruction=registry['instruction'], instruction_sha256=registry['instruction_sha256'],
                replacement_audio_sha256=registry['replacement']['receipt']['audio_sha256'],
                independent_word_witnesses=reports, human_audio_approval=False)
    write_json(path, item)
    print(json.dumps({'queue_id': item['id'], 'unit_id': uid, 'status': 'applied',
                      'replacement_audio_sha256': item['replacement_audio_sha256']}, indent=2))


if __name__ == '__main__':
    main()
