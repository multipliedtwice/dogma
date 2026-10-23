"""Resume a failed provisional render a bounded number of times per passage."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.request
from pathlib import Path


def snapshot(database: Path, generation_id: str) -> tuple[str, str, str, int, int]:
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True, timeout=5) as conn:
        row = conn.execute('SELECT status,error FROM generations WHERE id=?', (generation_id,)).fetchone()
        if row is None:
            raise RuntimeError('The listening generation was removed.')
        pending = conn.execute("SELECT id FROM segments WHERE generation_id=? AND status='pending' ORDER BY ordinal LIMIT 1", (generation_id,)).fetchone()
        ready, total = conn.execute("SELECT sum(status='ready'),count(*) FROM segments WHERE generation_id=?", (generation_id,)).fetchone()
    return row[0], row[1], pending[0] if pending else '', ready, total


def resume(token_log: Path, generation_id: str, port: int) -> None:
    token = json.loads(token_log.read_text(encoding='utf-8').splitlines()[0])['token']
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/api',
        data=json.dumps({'op': 'resume', 'data': {'id': generation_id}}).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if json.load(response).get('status') not in ('queued', 'rendering'):
            raise RuntimeError('The app did not queue the render.')


def watch(data_dir: Path, generation_id: str, token_log: Path, port: int) -> None:
    retries: dict[str, int] = {}
    last_progress = (-1, -1)
    while True:
        status, error, pending, ready, total = snapshot(data_dir / 'library.sqlite3', generation_id)
        if (ready, total) != last_progress:
            print(f'{ready}/{total} ready; status={status}', flush=True)
            last_progress = ready, total
        if status == 'ready':
            print('Full-book render complete.', flush=True)
            return
        if status == 'paused':
            print('Render paused; watcher stopped.', flush=True)
            return
        if status == 'failed':
            count = retries.get(pending, 0)
            if not pending or count >= 2:
                print(f'Render stopped at {pending}: {error[:500]}', flush=True)
                return
            retries[pending] = count + 1
            print(f'Retrying {pending} ({count + 1}/2): {error[:300]}', flush=True)
            try:
                resume(token_log, generation_id, port)
            except Exception as exc:
                print(f'Could not resume through the local app: {exc}', flush=True)
                return
        time.sleep(30)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('generation_id')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--token-log', type=Path, required=True)
    parser.add_argument('--port', type=int, default=1421)
    args = parser.parse_args()
    watch(args.data_dir.resolve(), args.generation_id, args.token_log.resolve(), args.port)
