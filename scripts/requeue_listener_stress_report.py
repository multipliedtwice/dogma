"""Promote and requeue one existing immutable listener stress report."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from rimsky.listener_stress_retake import enqueue_listener_stress_retake
from rimsky.series_characters import record_listener_stress
from rimsky.store import Store
from rimsky.util import now, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--report-id', required=True)
    parser.add_argument('--resources', type=Path, default=ROOT / 'resources')
    parser.add_argument('--retry-existing', action='store_true',
                        help='Requeue matching held/interrupted propagated items before starting the owner.')
    parser.add_argument('--supersede-existing', action='store_true',
                        help='Keep matching failed/interrupted evidence held and enqueue a new implementation-bound item.')
    args = parser.parse_args()
    if args.retry_existing and args.supersede_existing:
        raise SystemExit('Choose either --retry-existing or --supersede-existing.')
    store = Store(args.data_dir, resources=args.resources)
    try:
        rows = store.rows('SELECT * FROM stress_reports WHERE id=?', (args.report_id,))
        if len(rows) != 1:
            raise SystemExit('Stress report not found.')
        report = rows[0]
        if report.get('kind') != 'stress' or not report.get('intended_stress'):
            raise SystemExit('The report has no explicit intended stress.')
        report['character_state'] = record_listener_stress(store, report)
        if args.retry_existing or args.supersede_existing:
            generation = store.generation(report['generation_id'])
            name = (generation.get('analysis', {}).get('external_preview') or {}).get('generation')
            queue_dir = (store.root.parent / name / 'listener-stress-queue') if name else None
            character_key = report['character_state'].get('character_key')
            for path in queue_dir.glob('*.json') if queue_dir and queue_dir.is_dir() else ():
                try:
                    item = json.loads(path.read_text('utf8'))
                except (OSError, ValueError, TypeError):
                    continue
                if character_key:
                    if (item.get('series_character') or {}).get('character_key') != character_key:
                        continue
                elif (item.get('report') or {}).get('id') != report['id']:
                    # Occurrence-only corrections have no shared character key;
                    # retry only this immutable report, never every such item.
                    continue
                eligible = ('held', 'running', 'queued') if args.supersede_existing else ('held', 'running')
                if item.get('status') not in eligible:
                    continue
                history = [*(item.get('attempt_history') or []), {
                    'status': item.get('status'), 'error': item.get('error', ''),
                    'updated': item.get('updated'),
                    ('superseded' if args.supersede_existing else 'requeued'): now()}]
                item.update(status='held' if args.supersede_existing else 'queued', updated=now(),
                            error=('Superseded by a newer immutable retake implementation.'
                                   if args.supersede_existing else ''), attempt_history=history)
                write_json(path, item)
        queue = enqueue_listener_stress_retake(store, report)
        print(json.dumps({'report_id': report['id'], 'character_state': report['character_state'],
                          'retake_queue': queue}, ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == '__main__':
    main()
