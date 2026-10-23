import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'runtime'))
from rimsky.store import Store
from rimsky.util import AppError
from test_core import FB2


class DeltaPollEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.book = self.store.import_data(FB2, 'fixture.fb2')
        self.generation = self.store.create_generation(self.book['id'], {})
        self.gid = self.generation['id']
        self.source = self.generation['segments'][0]
        self.store.execute('DELETE FROM segments WHERE generation_id=?', (self.gid,))
        self.add_segments(0, 64)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def add_segments(self, start, count):
        cues = json.dumps([{'start': index / 10, 'end': (index + 1) / 10, 'text': 'word'} for index in range(50)])
        with self.store.lock, self.store.db:
            self.store.db.executemany(
                'INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken,cues) VALUES(?,?,?,?,?,?,?,?,?,?)',
                [(self.gid, f's{index}', index, self.source['chapter_id'], self.source['source_id'], 0, 5, 'hello', 'hello', cues) for index in range(start, start + count)])

    def add_visuals(self, start, count):
        with self.store.lock, self.store.db:
            self.store.db.executemany('INSERT INTO visuals VALUES(?,?,?,?,?,?,?)',
                                     [(f'v{index}', self.book['id'], self.source['source_id'], str(index), 'fixture.png', 'illustration', 'now') for index in range(start, start + count)])

    def measured(self, callback):
        steps = 0
        def count():
            nonlocal steps
            steps += 1
            return 0
        self.store.db.set_progress_handler(count, 1)
        try:
            result = callback()
        finally:
            self.store.db.set_progress_handler(None, 0)
        return result, steps

    def test_metadata_delta_never_loads_unchanged_segments_or_cues(self):
        revision = self.store.generation(self.gid)['revision']
        self.store.status(self.gid, 'rendering')
        original_loads = json.loads
        with patch.object(self.store, 'generation', side_effect=AssertionError('A delta must not load the full generation')):
            with patch('rimsky.store.json.loads', wraps=original_loads) as loads:
                result = self.store.generation_poll(self.gid, revision)
        self.assertEqual(3, loads.call_count)
        self.assertEqual([], result['generation']['segments'])
        self.assertEqual('rendering', result['generation']['status'])
        self.assertEqual(result['revision'], result['generation']['revision'])
        self.assertFalse(result['full'])

    def test_segment_delta_only_decodes_changed_rows_and_keeps_order_and_removals(self):
        revision = self.store.generation(self.gid)['revision']
        self.store.execute("UPDATE segments SET warning='changed' WHERE generation_id=? AND id IN ('s9','s2')", (self.gid,))
        self.store.execute("DELETE FROM segments WHERE generation_id=? AND id='s3'", (self.gid,))
        original_loads = json.loads
        with patch('rimsky.store.json.loads', wraps=original_loads) as loads:
            result = self.store.generation_poll(self.gid, revision)
        self.assertEqual(7, loads.call_count)
        self.assertEqual(['s2', 's9'], [segment['id'] for segment in result['generation']['segments']])
        self.assertTrue(all(len(segment['cues']) == 50 for segment in result['generation']['segments']))
        self.assertEqual(['s3'], result['removed_segment_ids'])
        self.assertEqual([], self.store.generation_poll(self.gid, result['revision'])['removed_segment_ids'])
        self.assertIsNone(self.store.generation_poll(self.gid, result['revision'])['generation'])

    def test_full_generation_revision_and_poll_fallbacks_match(self):
        snapshot = self.store.generation(self.gid)
        for revision in (None, snapshot['revision'] + 100):
            result = self.store.generation_poll(self.gid, revision)
            self.assertTrue(result['full'])
            self.assertEqual(snapshot['revision'], result['revision'])
            self.assertEqual(snapshot['revision'], result['generation']['revision'])
            self.assertEqual(snapshot['segments'], result['generation']['segments'])
            self.assertEqual([], result['removed_segment_ids'])

    def test_visual_delta_preserves_creation_order_and_removals(self):
        self.add_visuals(0, 64)
        revision = self.store.visuals_poll(self.book['id'])['revision']
        self.store.execute("UPDATE visuals SET caption='changed' WHERE id IN ('v9','v2')")
        self.store.execute("DELETE FROM visuals WHERE id='v3'")
        with patch.object(self.store, 'visuals', side_effect=AssertionError('A delta must not load all visuals')):
            result = self.store.visuals_poll(self.book['id'], revision)
        self.assertEqual(['v2', 'v9'], [visual['id'] for visual in result['visuals']])
        self.assertEqual(['v3'], result['removed_visual_ids'])
        self.assertFalse(result['full'])
        self.assertIsNone(self.store.visuals_poll(self.book['id'], result['revision'])['visuals'])
        full = self.store.visuals_poll(self.book['id'], result['revision'] + 1)
        self.assertTrue(full['full'])
        self.assertEqual(self.store.visuals(self.book['id']), full['visuals'])

    def test_generation_delta_database_work_scales_with_changes(self):
        def sample():
            revision = self.store.generation(self.gid)['revision']
            self.store.execute("UPDATE segments SET warning='changed' WHERE generation_id=? AND id='s2'", (self.gid,))
            result, steps = self.measured(lambda: self.store.generation_poll(self.gid, revision))
            self.assertEqual(['s2'], [segment['id'] for segment in result['generation']['segments']])
            return steps
        small = sample()
        self.add_segments(64, 4096)
        large = sample()
        self.assertLess(large, small * 3)

    def test_visual_delta_database_work_scales_with_changes(self):
        self.add_visuals(0, 64)
        def sample():
            revision = self.store.visuals_poll(self.book['id'])['revision']
            self.store.execute("UPDATE visuals SET caption='changed' WHERE id='v2'")
            result, steps = self.measured(lambda: self.store.visuals_poll(self.book['id'], revision))
            self.assertEqual(['v2'], [visual['id'] for visual in result['visuals']])
            return steps
        small = sample()
        self.add_visuals(64, 4096)
        large = sample()
        self.assertLess(large, small * 3)

    def test_position_writes_decode_only_manifest_and_preserve_saved_values(self):
        playback = {'generation_id': self.gid, 'segment_id': 's2', 'chapter_id': self.source['chapter_id'], 'source_id': self.source['source_id'], 'offset': 12}
        reading = {'chapter_id': self.source['chapter_id'], 'source_id': self.source['source_id'], 'offset': 240}
        original_loads = json.loads
        with patch.object(self.store, 'book', side_effect=AssertionError('Position writes must not load a full book')):
            with patch.object(self.store, 'generation', side_effect=AssertionError('Position writes must not load a full generation')):
                for callback, value in ((self.store.save_position, playback), (self.store.save_reading_position, reading)):
                    with patch('rimsky.store.json.loads', wraps=original_loads) as loads:
                        self.assertEqual(value, callback(self.book['id'], value))
                    self.assertEqual(1, loads.call_count)
        saved = self.store.book(self.book['id'])
        self.assertEqual(playback, saved['position'])
        self.assertEqual(reading, saved['reading_position'])
        chapter_only = {'chapter_id': self.source['chapter_id']}
        self.assertEqual(chapter_only, self.store.save_position(self.book['id'], chapter_only))
        self.assertEqual({**chapter_only, 'offset': 0}, self.store.save_reading_position(self.book['id'], {**chapter_only, 'source_id': None}))

    def test_position_database_work_does_not_scan_saved_segments(self):
        playback = {'generation_id': self.gid, 'segment_id': 's2', 'chapter_id': self.source['chapter_id'], 'source_id': self.source['source_id'], 'offset': 0}
        reading = {'chapter_id': self.source['chapter_id'], 'source_id': self.source['source_id'], 'offset': 0}
        def sample():
            return [self.measured(lambda: callback(self.book['id'], value))[1] for callback, value in ((self.store.save_position, playback), (self.store.save_reading_position, reading))]
        sample()
        small = sample()
        self.add_segments(64, 4096)
        large = sample()
        for before, after in zip(small, large):
            self.assertLess(after, before * 3)

    def test_targeted_position_lookup_retains_ownership_and_segment_validation(self):
        self.store.execute("UPDATE segments SET status='ready',duration=1 WHERE generation_id=? AND id='s2'", (self.gid,))
        value = {'generation_id': self.gid, 'segment_id': 's2', 'chapter_id': self.source['chapter_id'], 'source_id': self.source['source_id'], 'offset': 1.05}
        self.assertEqual(value, self.store.save_position(self.book['id'], value))
        other_book = self.store.import_data(FB2.replace(b'Verification book', b'Other book'), 'other.fb2')
        other_generation = self.store.create_generation(other_book['id'], {})
        cases = [
            ({'generation_id': 'missing'}, 'Generation not found'),
            ({'generation_id': other_generation['id']}, 'Generation does not belong'),
            ({'generation_id': None}, 'requires its generation'),
            ({'segment_id': 'missing'}, 'segment does not match'),
            ({'segment_id': ['s2']}, 'segment does not match'),
            ({'source_id': None}, 'segment does not match'),
            ({'offset': 1.2}, 'offset exceeds'),
            ({'offset': -1}, 'Invalid playback offset'),
        ]
        for changes, error in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(AppError, error):
                self.store.save_position(self.book['id'], {**value, **changes})
        self.assertEqual(value, self.store.book(self.book['id'])['position'])

    def test_full_generation_revision_cannot_advance_past_its_rows(self):
        revision = self.store.generation(self.gid)['revision']
        observed = threading.Event()
        committed = threading.Event()
        original_rows = self.store.rows
        def writer():
            if observed.wait(2):
                self.store.execute("UPDATE segments SET warning='next revision' WHERE generation_id=? AND id='s2'", (self.gid,))
                committed.set()
        def rows(query, parameters=()):
            result = original_rows(query, parameters)
            if query.startswith('SELECT * FROM generations'):
                observed.set()
                committed.wait(.05)
            return result
        worker = threading.Thread(target=writer)
        worker.start()
        with patch.object(self.store, 'rows', side_effect=rows):
            snapshot = self.store.generation(self.gid)
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(committed.is_set())
        self.assertEqual(revision, snapshot['revision'])
        self.assertEqual('', snapshot['segments'][2]['warning'])
        delta = self.store.generation_poll(self.gid, snapshot['revision'])
        self.assertEqual(['s2'], [segment['id'] for segment in delta['generation']['segments']])
        self.assertEqual(revision + 1, delta['revision'])


if __name__ == '__main__':
    unittest.main()
