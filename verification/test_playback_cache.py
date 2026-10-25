from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rimsky.playback_cache import collect, reading


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'maps').mkdir()
        store = SimpleNamespace(lock=threading.RLock(),
            rows=lambda *args: [{'book_id': 'book'}], current_generation_id=lambda book: 'current')
        self.manager = SimpleNamespace(folder=self.root, condition=threading.Condition(),
            store=store, cache_readers={}, queue=[], active=None,
            _plan=lambda *args: {'key': 'map-current', 'chunks': [{'key': 'a' * 64}]})

    def old(self, path):
        os.utime(path, (time.time() - 7200,) * 2)

    def map(self, name, gid, keys):
        path = self.root / 'maps' / (name + '.json')
        path.write_text(json.dumps({'generation_id': gid, 'music': False, 'music_volume': 1,
                                  'key': name, 'chunks': keys}))
        self.old(path)
        return path

    def chunk(self, key, hls=False):
        path = self.root / (key + '.json')
        path.write_text('{}')
        self.old(path)
        if hls:
            data = self.root / key
            data.mkdir()
            (data / 'init.mp4').write_bytes(b'init')
            (data / 'f000000.m4s').write_bytes(b'fragment')
        else:
            data = self.root / (key + '.mp3')
            data.write_bytes(b'encoded')
        self.old(data)
        return data

    def test_current_cold_assets_survive_old_maps_and_stale_output_is_collected(self):
        current = self.chunk('a' * 64)
        obsolete = self.chunk('b' * 64, hls=True)
        oldmap = self.map('old', 'old-generation', ['b' * 64])
        currentmap = self.map('map-current', 'current', ['a' * 64])
        source = self.root / 'source.wav'
        source.write_bytes(b'protected PCM')
        dry = collect(self.manager, execute=False)
        self.assertTrue(obsolete.exists())
        self.assertIn(str(obsolete), dry['paths'])
        result = collect(self.manager)
        self.assertEqual(result, dry)
        self.assertTrue(current.exists())
        self.assertTrue(currentmap.exists())
        self.assertFalse(obsolete.exists())
        self.assertFalse(oldmap.exists())
        self.assertEqual(source.read_bytes(), b'protected PCM')

    def test_queued_active_readers_and_recent_buffered_clients_are_protected(self):
        paths = {letter: self.chunk(letter * 64) for letter in 'bcde'}
        self.manager.queue = [{'key': 'b' * 64}]
        self.manager.active = {'key': 'c' * 64}
        os.utime(paths['e'], None)
        with reading(self.manager, 'd' * 64):
            self.old(self.root / ('d' * 64 + '.json'))
            self.assertEqual(collect(self.manager)['paths'], [])
        self.assertFalse(self.manager.cache_readers)
        collect(self.manager)
        self.assertFalse(paths['d'].exists())
        for letter in 'bce':
            self.assertTrue(paths[letter].exists())

    def test_current_plan_chunks_survive_even_when_map_points_to_prior_revision(self):
        current = self.chunk('a' * 64)
        obsolete = self.chunk('b' * 64)
        self.map('old-revision', 'current', ['b' * 64])
        collect(self.manager)
        self.assertTrue(current.exists())
        self.assertFalse(obsolete.exists())

    def test_unrecognized_metadata_defers_cleanup(self):
        obsolete = self.chunk('b' * 64)
        (self.root / 'maps' / 'bad.json').write_text('invalid')
        self.assertIn('deferred', collect(self.manager))
        self.assertTrue(obsolete.exists())

    def test_superseded_volume_variants_of_same_book_do_not_accumulate(self):
        old = self.chunk('b' * 64)
        current = self.chunk('a' * 64)
        previous = self.map('previous-volume', 'current', ['b' * 64])
        value = json.loads(previous.read_text())
        value['music_volume'] = .5
        previous.write_text(json.dumps(value))
        self.old(previous)
        newest = self.map('map-current', 'current', ['a' * 64])
        os.utime(newest, (time.time() - 3600,) * 2)
        self.manager._plan = lambda gid, music, volume: {
            'key': 'map-current' if volume == 1 else 'previous-volume',
            'chunks': [{'key': ('a' if volume == 1 else 'b') * 64}]}
        collect(self.manager)
        self.assertTrue(current.exists())
        self.assertFalse(old.exists())
        self.assertFalse(previous.exists())

    def test_request_publishes_newest_map_before_collector_can_select_settings(self):
        from rimsky.playback_streams import PlaybackStreams

        old_variant = self.chunk('b' * 64)
        current_variant = self.chunk('a' * 64)
        previous = self.map('map-new', 'current', ['b' * 64])
        old_value = json.loads(previous.read_text())
        old_value['music_volume'] = .5
        previous.write_text(json.dumps(old_value))
        self.old(previous)
        self.map('map-old', 'current', ['a' * 64])

        manager = object.__new__(PlaybackStreams)
        manager.folder = self.root
        manager.condition = self.manager.condition
        manager.store = self.manager.store
        manager.queue, manager.queued, manager.contexts, manager.desired = [], set(), {}, {}
        manager.active = None
        manager.cache_readers = {}
        manager.cache_collected_at = time.monotonic()
        manager._plan = lambda gid, music, volume: {
            'key': 'map-new' if volume == .5 else 'map-old',
            'chunks': [{'key': ('b' if volume == .5 else 'a') * 64, 'blocked': None,
                        'spec': {'chapter_id': 'chapter', 'parts': []}}]}
        manager._record = lambda key: ({'status': 'ready'}
            if (self.root / (key + '.json')).is_file() else None)
        manager._public = lambda plan: {'chunks': [
            {'status': 'ready' if manager._record(chunk['key']) else 'pending'}
            for chunk in plan['chunks']]}

        entered_write = threading.Event()
        allow_write = threading.Event()
        collector_done = threading.Event()
        results = {}
        from rimsky.playback_streams import _write as real_write

        def delayed_write(path, value):
            if Path(path).name == 'map-new.json':
                entered_write.set()
                if not allow_write.wait(3):
                    raise AssertionError('timed out waiting to publish the requested map')
            real_write(path, value)

        with patch('rimsky.playback_streams._write', side_effect=delayed_write):
            request = threading.Thread(target=lambda: results.setdefault(
                'request', manager.request('current', music=False, music_volume=.5)))
            request.start()
            self.assertTrue(entered_write.wait(3))
            collector = threading.Thread(target=lambda: (
                results.setdefault('collect', collect(self.manager)), collector_done.set()))
            collector.start()
            time.sleep(.1)
            self.assertFalse(collector_done.is_set(), 'collector must wait for map publication')
            allow_write.set()
            request.join(3)
            collector.join(3)

        self.assertFalse(request.is_alive())
        self.assertFalse(collector.is_alive())
        self.assertEqual(results['request']['chunks'][0]['status'], 'ready')
        self.assertTrue(old_variant.exists())
        self.assertTrue(current_variant.exists())
        self.assertNotIn(str(old_variant), results['collect']['paths'])


if __name__ == '__main__':
    unittest.main()
