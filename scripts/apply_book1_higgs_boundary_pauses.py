"""Publish chapter and terminal-ellipsis silence for already ready Higgs clips."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from render_book1_higgs_half import DATA, OUT, PLAN, ROOT, boundary_padding, padded_audio

SKIP = {'c0005-p00001-0-28', 'c0005-p00025-0-8'}  # The performance retake already pads these.
MANIFEST = OUT / 'boundary-audio' / 'retroactive-manifest.json'


def main() -> None:
    sys.path.insert(0, str(ROOT / 'runtime'))
    from rimsky.integrity import accepted_audio, read_audio
    from rimsky.store import Store
    from rimsky.util import now, write_json

    generation_id = json.loads(PLAN.read_text(encoding='utf-8'))['generation_id']
    previous = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {}
    changes = previous.get('changes', {})
    store = Store(DATA)
    try:
        rows = store.db.execute(
            "SELECT id,spoken,audio_hash,duration FROM segments "
            "WHERE generation_id=? AND status='ready' ORDER BY ordinal",
            (generation_id,),
        ).fetchall()
        for row in rows:
            if row['id'] in SKIP or not any(boundary_padding(row['id'], row['spoken'])):
                continue
            if changes.get(row['id'], {}).get('new_audio_sha256') == row['audio_hash']:
                continue
            source = DATA / 'audio' / f"{row['audio_hash']}.wav"
            if not source.is_file():
                raise FileNotFoundError(source)
            adjusted = padded_audio(source, row['id'], row['spoken'])
            key = hashlib.sha256(adjusted.read_bytes()).hexdigest()
            target = DATA / 'audio' / f'{key}.wav'
            if not target.exists():
                try:
                    os.link(adjusted, target)
                except OSError:
                    shutil.copy2(adjusted, target)
            metadata = read_audio(target)
            write_json(target.with_suffix('.json'), {**metadata, 'warning': ''})
            accepted_audio(target)
            with store.lock, store.db:
                store.db.execute(
                    "UPDATE segments SET audio_hash=?,duration=? WHERE generation_id=? AND id=?",
                    (key, metadata['duration'], generation_id, row['id']),
                )
                store.db.execute('UPDATE generations SET updated=? WHERE id=?',
                                 (now(), generation_id))
            changes[row['id']] = {'old_audio_sha256': row['audio_hash'],
                                  'old_duration_seconds': row['duration'],
                                  'new_audio_sha256': key,
                                  'new_duration_seconds': metadata['duration'],
                                  'padding_seconds': boundary_padding(row['id'], row['spoken'])}
            print(row['id'], row['duration'], '->', metadata['duration'], flush=True)
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps({'generation_id': generation_id, 'changes': changes},
                                       ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('updated', len(changes), flush=True)
    finally:
        store.close()


if __name__ == '__main__':
    main()
