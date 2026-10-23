import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from rimsky.store import Store

OLD_SCHEMA = '''CREATE TABLE stress_reports (id TEXT PRIMARY KEY, book_id TEXT NOT NULL, generation_id TEXT NOT NULL,
 segment_id TEXT NOT NULL, chapter_id TEXT NOT NULL, source_id TEXT NOT NULL, selected_text TEXT NOT NULL,
 text_offset INTEGER, audio_offset REAL, audio_hash TEXT, passage_text TEXT NOT NULL, spoken_text TEXT NOT NULL,
 created TEXT NOT NULL);'''


class SynthesisSnapshotTests(unittest.TestCase):
    def test_old_schema_gains_nullable_column_and_keeps_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            db = sqlite3.connect(Path(temp) / 'library.sqlite3')
            db.execute(OLD_SCHEMA)
            db.commit()
            db.close()
            store = Store(Path(temp))
            try:
                columns = {row['name'] for row in store.rows('PRAGMA table_info(stress_reports)')}
                self.assertTrue({'kind', 'comment', 'intended_stress', 'synthesis_snapshot'} <= columns)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
