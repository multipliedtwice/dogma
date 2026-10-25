"""Render only corrected parts into the one current debug audiobook."""
from __future__ import annotations
import copy
from pathlib import Path

import current_higgs_output as current
from rimsky import listener_stress
from rimsky.store import Store
from rimsky.util import digest, file_digest, now


def _outcomes(root, config, result):
    """Keep raw heard evidence while appending a compact delivery receipt."""
    for item in config['queue_items']:
        path = Path(item['path'])
        row = current.read(path)
        origin = row.get('origin_report') or row.get('original_report') or row['report']
        if digest(origin) != item['origin_report_sha256']:
            raise ValueError('Saved heard report changed before current publication')
        receipt = {'schema': 2, 'id': origin['id'] + '-current-' + str(result['revision']),
                   'report': origin, 'origin_report_sha256': digest(origin), 'status': 'applied',
                   'generation_id': result['generation_id'], 'published_generation_id': result['generation_id'],
                   'revision': result['revision'], 'affected_segment_ids': result['affected_ids'],
                   'affected_source_ids': result['affected_source_ids'], 'updated': now(),
                   'human_audio_approval': False, 'post_processing': 'off', 'recognition_status': 'not_checked'}
        current.write(root / 'listener-stress-queue' / (receipt['id'] + '.json'), receipt)
        if row.get('error'):
            row.setdefault('attempt_history', []).append({key: row.get(key) for key in ('status', 'error', 'updated')})
        row.update(status='applied', phase='delivered', updated=now(), completed=now(), error='',
                   published_generation_id=result['generation_id'], revision=result['revision'],
                   affected_segment_ids=result['affected_ids'], affected_source_ids=result['affected_source_ids'],
                   result=result, human_audio_approval=False)
        current.write(path, row)


def run_if_current(args, repair):
    """Return None for a legacy output, preserving historical-reader support."""
    data_dir = args.data_dir.resolve()
    store = Store(data_dir, correction_profile=listener_stress.shared_profile())
    job = root = None
    completed, failed = [], []
    try:
        requested = store.generation(args.generation_id)
        book = store.book(requested['book_id'])
        view = current.load(store, data_dir, book['id'])
        if view is None:
            return None
        generation = store.generation(view['generation_id'])
        snapshot = listener_stress.snapshot(store, book)
        notes, frozen_ids = repair.frozen_notes(snapshot, listener_stress.notes(store, book))
        units = [view['parts'][uid]['unit'] for uid in view['unit_order']]
        blocks = {b['id']: b['text'] for c in book['chapters'] for b in c['blocks']}
        root = current.output_root(data_dir, book['id'])
        item = {**current.read(args.queue_item), '_queue_item_path': str(args.queue_item.resolve())}
        origins, forced, queued = repair.pending_reports(root, item, notes, generation, units, blocks, frozen_ids, snapshot)
        changed = {}
        for unit in units:
            selected, evidence = repair.corrected_unit(unit, snapshot, blocks, unit['id'] in forced, forced.get(unit['id']))
            if evidence:
                segment = next(row for row in generation['segments'] if row['id'] == unit['id'])
                if (unit.get('render_parts') or unit['synthesis'].get('render_parts')
                        or segment['performance'].get('render_parts')):
                    raise ValueError('A part with an internal pause layout requires its layout-aware renderer')
                changed[unit['id']] = selected
        identity = digest({'source': view['source_hash'], 'inputs': {uid: unit['key'] for uid, unit in changed.items()},
                           'origins': [x['id'] for x in origins], 'listener_snapshot': snapshot['sha256']})
        job = root / 'work' / identity[:24]
        job.mkdir(parents=True, exist_ok=True)
        config = {'policy': current.POLICY, 'book_id': book['id'], 'generation_id': generation['id'],
                  'source_hash': view['source_hash'], 'affected_ids': list(changed), 'queue_items': queued,
                  'origins': origins, 'snapshot_sha256': snapshot['sha256'], 'helper_sha256': file_digest(repair.SELF)}
        repair.write(job / 'current-repair.json', config, immutable=True)
        ready = {'policy': current.POLICY, 'native_manifest': view['native_manifest']}
        repair.write(job / 'ready.json', ready, immutable=True)
        repair.write(job / 'listener-stress-snapshot.json', snapshot, immutable=True)
        repair.write(job / 'inputs.json', {'units': list(changed.values())}, immutable=True)
        if args.configure_only:
            result = {'status': 'configured', 'generation': str(root), 'job': str(job),
                      'generation_id': generation['id'], 'affected_ids': list(changed),
                      'reused': len(units) - len(changed), 'copied_audio': 0}
            current.write(args.result, result)
            return 0
        receipts = {}
        for uid, planned in changed.items():
            # Recovery reuses an existing receipt in this small job, never the
            # rest of a book tree. Only this explicitly requested part is new.
            row, effective = repair.render_unit(job, ready, planned)
            if row['status'] != 'rendered':
                failed.append(uid)
                continue
            raw = job / row['audio']
            latest = store.generation(view['generation_id'])
            published, view = current.publish_parts(store, data_dir, view,
                {uid: (effective, row, raw)}, expected_revision=latest['revision'])
            completed.append(uid)
            receipts[uid] = row
            current.write(job / 'progress.json', {'status': 'rendering', 'completed': completed,
                           'failed': failed, 'current_revision': published['revision'], **repair.d.UNCHECKED})
        latest = store.generation(view['generation_id'])
        result = {'status': 'held' if failed else 'applied', 'generation': str(root), 'generation_name': root.name,
                  'generation_id': latest['id'], 'published_generation_id': latest['id'], 'revision': latest['revision'],
                  'affected_ids': list(changed), 'affected_segment_ids': list(changed),
                  'affected_source_ids': sorted({unit['source_id'] for unit in changed.values()}),
                  'reused': len(units) - len(changed), 'rendered': len(completed), 'failures': failed,
                  'source_preserved': True, 'covered_report_ids': [x['id'] for x in origins], **repair.d.UNCHECKED}
        current.write(job / 'terminal.json', result)
        current.write(job / 'progress.json', result)
        current.write(root / 'listener-repair-terminal.json', result)
        if not failed:
            _outcomes(root, config, result)
            try:
                result['staging_cleanup'] = current.cleanup_job_audio(store, data_dir, view, job, receipts)
            except (OSError, ValueError) as error:
                result['staging_cleanup'] = {'status': 'held', 'error': str(error)}
            current.write(job / 'terminal.json', result)
            current.write(job / 'progress.json', result)
            current.write(root / 'listener-repair-terminal.json', result)
        current.write(args.result, result)
        return int(bool(failed))
    except Exception as error:
        # Successful earlier commits remain playable. A stopped replacement
        # must never leave its work receipt saying that it is still rendering.
        result = {'status': 'held', 'error': type(error).__name__ + ': ' + str(error),
                  'generation_id': args.generation_id, 'rendered_ids': completed,
                  'failures': failed, 'human_audio_approval': False,
                  'post_processing': 'off', 'recognition_status': 'not_checked'}
        for path in ([job / 'terminal.json', job / 'progress.json'] if job else []):
            try: current.write(path, result)
            except OSError: pass
        if root is not None:
            try: current.write(root / 'listener-repair-terminal.json', result)
            except OSError: pass
        current.write(args.result, result)
        return 1
    finally:
        store.close()
