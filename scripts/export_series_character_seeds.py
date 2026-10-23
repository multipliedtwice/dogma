"""Promote a tested library's cumulative series-character overlay into app seeds.

This is an explicit release/build step.  The running app never writes its own
read-only bundled resource.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from rimsky.util import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'resources/series-characters.json')
    args = parser.parse_args()
    database = args.data_dir.resolve() / 'library.sqlite3'
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in connection.execute(
            'SELECT * FROM series_character_state ORDER BY series_id,created,character_key')]
    finally:
        connection.close()
    groups = {}
    for row in rows:
        row['aliases'] = json.loads(row['aliases'])
        row['state'] = json.loads(row['state'])
        group = groups.setdefault(row['series_id'], {'id': row['series_id'], 'name': row['series_name'],
                                                      'author': row['author'], 'characters': []})
        group['characters'].append({key: row[key] for key in
                                    ('character_key', 'canonical_name', 'aliases', 'state', 'created', 'updated')})
    write_json(args.output.resolve(), {'schema': 1, 'series': list(groups.values())})
    print(json.dumps({'output': str(args.output.resolve()), 'series': len(groups),
                      'characters': len(rows)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
