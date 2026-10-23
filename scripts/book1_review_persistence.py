"""Atomic, concurrent edits for the two local corpus review steps."""
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from manual_labeling_archive import mirror_review

LOCK = threading.RLock()
MISSING = object()
AUDIT_FIELDS = {'reviewed_at', 'review_origin'}


class ReviewConflict(ValueError):
    def __init__(self, conflicts):
        super().__init__('Another tab changed the same labels')
        self.conflicts = conflicts


def save_review(folder, incoming, patch=False):
    with LOCK:
        summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8'))
        if (not isinstance(incoming, dict) or incoming.get('schema') != 1
                or incoming.get('candidates_sha256') != summary['candidates_sha256']):
            raise ValueError('Review does not match current candidates')
        entries = incoming.get('changes' if patch else 'decisions')
        valid_ids = {json.loads(line)['id'] for line in
                     (folder / 'candidates.jsonl').read_text(encoding='utf-8').splitlines() if line}
        if not isinstance(entries, dict) or not set(entries).issubset(valid_ids):
            raise ValueError('Unknown clip or invalid decisions')
        target = folder / 'review-draft.json'
        current = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {
            'schema': 1, 'candidates_sha256': summary['candidates_sha256'], 'decisions': {}}
        if current.get('candidates_sha256') != summary['candidates_sha256']:
            raise ValueError('Existing review does not match candidates')
        decisions = dict(current['decisions'])
        conflicts = []
        for cid, entry in entries.items():
            if not isinstance(entry, dict):
                raise ValueError('Invalid decision')
            existing = decisions.get(cid, {})
            if not patch:
                # Old open tabs send complete snapshots. Never erase newer patch edits.
                if existing.get('_revision', 0) > entry.get('_revision', 0):
                    fields = [name for name in set(existing) | set(entry) if name != '_revision'
                              and existing.get(name, MISSING) != entry.get(name, MISSING)]
                    if fields:
                        conflicts.append({'id': cid, 'fields': sorted(fields), 'current': existing})
                    continue
                decisions[cid] = entry
                continue
            before, after = entry.get('before'), entry.get('after')
            if before is not None and not isinstance(before, dict) or not isinstance(after, dict):
                raise ValueError('Patch must contain a decision; deletion is not supported')
            before = before or {}
            merged = dict(existing)
            fields = []
            for name in set(before) | set(after):
                if name == '_revision' or before.get(name, MISSING) == after.get(name, MISSING):
                    continue
                desired = after.get(name, MISSING)
                found = existing.get(name, MISSING)
                if (name not in AUDIT_FIELDS and found != before.get(name, MISSING)
                        and found != desired):
                    fields.append(name)
                elif desired is MISSING:
                    merged.pop(name, None)
                else:
                    merged[name] = desired
            if fields:
                conflicts.append({'id': cid, 'fields': sorted(fields), 'current': existing})
            else:
                merged['_revision'] = existing.get('_revision', 0) + 1
                decisions[cid] = merged
        if conflicts:
            raise ReviewConflict(conflicts)
        result = {**current, 'schema': 1, 'reviewer': incoming.get('reviewer') or current.get('reviewer', 'local-listener'),
                  'updated_at': datetime.now(timezone.utc).isoformat(), 'decisions': decisions}
        handle, temporary = tempfile.mkstemp(prefix='.review-draft-', suffix='.json', dir=folder)
        try:
            with os.fdopen(handle, 'w', encoding='utf-8') as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
            os.replace(temporary, target)
            mirror_review(folder,target.read_bytes())
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return result
