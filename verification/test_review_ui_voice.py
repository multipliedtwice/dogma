from __future__ import annotations

import json
import math
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).parents[1] / 'runtime'), str(Path(__file__).parent)]
from test_core import FB2, SyntheticEngine, pack_fixture, write_wave
from rimsky.integrity import validate_coverage
from rimsky.jobs import Jobs
from rimsky.packs import load_pack
from rimsky.store import Store
from rimsky.util import AppError, file_digest, write_json
from server import Application


def clone_pack(root, raw=None, suffix='wav'):
    pack = pack_fixture(root)
    audio = root / ('reference.' + suffix)
    if raw is None:
        write_wave(audio, seconds=.3)
    else:
        audio.write_bytes(raw)
    pack['speech']['variant'] = 'clone'
    pack['speech']['instruction_control'] = False
    for voice in pack['voices']:
        voice.pop('speaker', None)
        voice['references'] = {'neutral': {'audio': audio.name, 'text': 'Controlled transcript fixture.'}}
    pack['files'][audio.name] = file_digest(audio)
    write_json(root / 'pack.json', pack)
    return pack


class InterruptedAnalysis(SyntheticEngine):
    def __init__(self):
        super().__init__()
        self.block = True
        self.accepted = []

    def call(self, method, payload, stop):
        if method == 'analyze' and self.block and self.analyzed:
            raise AppError('Controlled second-batch interruption')
        result = super().call(method, payload, stop)
        if method == 'analyze':
            self.accepted.extend(source['id'] for source in payload['segments'])
        return result


class ReaderVoiceAudit(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = Application(self.root / 'library', self.root / 'bin')
        self.store = self.app.store
        self.store.update_settings({'cast_review': False})
        self.addCleanup(self.app.close)
        self.book = self.store.import_data(FB2, 'fixture.fb2')
        self.pack = pack_fixture(self.root / 'pack')
        self.config = {'pack_path': self.pack['_root'], 'pack_fingerprint': self.pack['_fingerprint'],
                       'pack_name': self.pack['name'], 'device': 'cpu', 'align_words': False}

    def generation(self, book=None):
        return self.store.create_generation((book or self.book)['id'], self.config)

    def wait(self):
        self.app.jobs.thread.join(10)
        self.assertFalse(self.app.jobs.thread.is_alive())

    def large_book(self):
        text = '<FictionBook><description><title-info><book-title>Controlled ordering</book-title></title-info></description><body><section><title><p>Глава</p></title>'
        text += ''.join(f'<p>{i}: '+('Синтетический текст для проверки порядка. ' * 5)+'</p>' for i in range(90))
        return self.store.import_data((text+'</section></body></FictionBook>').encode(), 'ordering.fb2')

    def interrupt(self):
        book = self.large_book()
        generation = self.generation(book)
        engine = InterruptedAnalysis()
        self.app.jobs.engine_factory = lambda _: engine
        self.app.jobs.start(generation['id'])
        self.wait()
        paused = self.store.generation(generation['id'])
        self.assertEqual('failed', paused['status'])
        self.assertGreater(len(paused['analysis'].get('completed_sources', [])), 1)
        return book, paused, engine

    def test_partially_analyzed_book_stays_in_canonical_reading_order(self):
        book, generation, _ = self.interrupt()
        validate_coverage(book, generation['config']['chapters'], generation['segments'])
        expected = [block['id'] for chapter in book['chapters'] for block in chapter['blocks']]
        self.assertEqual(expected, [segment['source_id'] for segment in generation['segments']])

    def test_resume_keeps_accepted_analysis_without_repeating_sources(self):
        book, generation, engine = self.interrupt()
        accepted = list(engine.accepted)
        engine.block = False
        self.app.jobs.start(generation['id'])
        self.wait()
        final = self.store.generation(generation['id'])
        self.assertEqual('ready', final['status'], final['error'])
        validate_coverage(book, final['config']['chapters'], final['segments'])
        self.assertEqual(len(engine.accepted), len(set(engine.accepted)))
        self.assertEqual(accepted, engine.accepted[:len(accepted)])

    def test_legacy_partial_ordinals_are_normalized_before_next_analysis(self):
        book, generation, engine = self.interrupt()
        accepted = list(engine.accepted)
        completed = set(generation['analysis']['completed_sources'])
        for index, segment in enumerate(generation['segments']):
            if segment['id'] not in completed:
                self.store.execute('UPDATE segments SET ordinal=? WHERE generation_id=? AND id=?',
                                   (index, generation['id'], segment['id']))
        original_call = engine.call
        def inspect(method, payload, stop):
            if method == 'analyze':
                current = self.store.generation(generation['id'])
                validate_coverage(book, current['config']['chapters'], current['segments'])
            return original_call(method, payload, stop)
        engine.call = inspect
        engine.block = False
        self.app.jobs.start(generation['id'])
        self.wait()
        final = self.store.generation(generation['id'])
        self.assertEqual('ready', final['status'], final['error'])
        self.assertEqual(accepted, engine.accepted[:len(accepted)])

    def test_generation_reads_one_consistent_revision(self):
        generation = self.generation()
        observed = threading.Event()
        committed = threading.Event()
        original_rows = self.store.rows
        def writer():
            self.assertTrue(observed.wait(2))
            with self.store.lock, self.store.db:
                self.store.db.execute('UPDATE generations SET status=? WHERE id=?', ('rendering', generation['id']))
                self.store.db.execute('UPDATE segments SET speaker=? WHERE generation_id=?', ('new-speaker', generation['id']))
            committed.set()
        worker = threading.Thread(target=writer)
        worker.start()
        def rows(query, params=()):
            result = original_rows(query, params)
            if query.startswith('SELECT * FROM generations'):
                observed.set()
                committed.wait(.08)
            return result
        with patch.object(self.store, 'rows', side_effect=rows):
            snapshot = self.store.generation(generation['id'])
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual('paused', snapshot['status'])
        self.assertTrue(all(segment['speaker'] == 'narrator' for segment in snapshot['segments']))
        self.assertEqual('rendering', self.store.generation(generation['id'])['status'])

    def test_failed_pack_selection_retains_previous_settings(self):
        self.store.update_settings({'pack_path': self.pack['_root']})
        candidate = self.root / 'candidate'
        pack_fixture(candidate)
        (candidate / 'model.gguf').write_bytes(b'changed without resealing')
        before = self.store.settings()
        with self.assertRaises(AppError):
            self.app.dispatch('select_pack', {'path': str(candidate)})
        self.assertEqual(before, self.store.settings())
        self.assertIsNone(self.app.jobs.active)

    def test_explicit_pack_selection_checks_and_applies_without_inference(self):
        candidate = self.root / 'candidate'
        pack_fixture(candidate)
        with patch.object(self.app.jobs, 'start', side_effect=AssertionError('No job should start')):
            result = self.app.dispatch('select_pack', {'path': str(candidate)})
        self.assertEqual(str(candidate), result['settings']['pack_path'])
        self.assertEqual(str(candidate), self.store.settings()['pack_path'])
        self.assertEqual(self.pack['name'], result['health']['pack']['name'])
        self.assertIsNone(self.app.jobs.active)

    def test_health_and_generation_reject_a_pack_tampered_after_selection(self):
        candidate = self.root / 'candidate'
        pack_fixture(candidate)
        with patch('rimsky.packs.resolve_binary', return_value='binary'):
            self.assertTrue(self.app.dispatch('select_pack', {'path': str(candidate)})['health']['ready'])
            model = candidate / 'model.gguf'
            original = model.read_bytes()
            model.write_bytes(bytes([original[0] ^ 1]) + original[1:])
            status = self.app.dispatch('health', {})
            self.assertFalse(status['ready'])
            self.assertIn('checksum mismatch', status['pack_error'])
            before = len(self.store.book(self.book['id'])['generations'])
            with self.assertRaisesRegex(AppError, 'checksum mismatch'):
                self.app.dispatch('generate', {'book_id': self.book['id']})
            self.assertEqual(before, len(self.store.book(self.book['id'])['generations']))

    def test_verified_clone_pack_rejects_non_audio_reference(self):
        candidate = self.root / 'clone'
        clone_pack(candidate, b'not audio')
        with self.assertRaisesRegex(AppError, 'reference'):
            load_pack(str(candidate), verify=True)

    def test_verified_clone_pack_rejects_truncated_reference(self):
        candidate = self.root / 'clone'
        pack = clone_pack(candidate)
        audio = candidate / 'reference.wav'
        audio.write_bytes(audio.read_bytes()[:44])
        pack['files'][audio.name] = file_digest(audio)
        write_json(candidate / 'pack.json', pack)
        with self.assertRaisesRegex(AppError, 'reference'):
            load_pack(str(candidate), verify=True)

    def test_verified_clone_pack_rejects_silent_reference(self):
        candidate = self.root / 'clone'
        pack = clone_pack(candidate)
        audio = candidate / 'reference.wav'
        with wave.open(str(audio), 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000)
            stream.writeframes(b'\0' * 4800)
        pack['files'][audio.name] = file_digest(audio)
        write_json(candidate / 'pack.json', pack)
        with self.assertRaisesRegex(AppError, 'silent'):
            load_pack(str(candidate), verify=True)

    def test_shared_valid_reference_is_accepted_and_decoded_once(self):
        from rimsky.references import inspect_reference
        candidate = self.root / 'clone'
        clone_pack(candidate)
        with patch('rimsky.references.inspect_reference', wraps=inspect_reference) as inspect:
            result = load_pack(str(candidate), verify=True)
        self.assertEqual(3, len(result['voices']))
        self.assertEqual(1, inspect.call_count)

    def test_readiness_probe_does_not_decode_voice_audio(self):
        candidate = self.root / 'clone'
        clone_pack(candidate)
        with patch('rimsky.references.inspect_reference', side_effect=AssertionError('Only explicit verification decodes references')):
            load_pack(str(candidate))

    def test_reference_failure_does_not_select_candidate_pack(self):
        self.store.update_settings({'pack_path': self.pack['_root']})
        candidate = self.root / 'clone'
        clone_pack(candidate, b'invalid MP3 file', 'mp3')
        before = self.store.settings()
        with self.assertRaises(AppError):
            self.app.dispatch('select_pack', {'path': str(candidate)})
        self.assertEqual(before, self.store.settings())

    def test_reference_failure_stops_preparation_before_analyze_or_render(self):
        candidate = self.root / 'clone'
        clone_pack(candidate, b'not audio')
        pack = load_pack(str(candidate))
        generation = self.store.create_generation(self.book['id'], {**self.config,
            'pack_path': str(candidate), 'pack_fingerprint': pack['_fingerprint']})
        with patch.object(self.app.jobs, 'engine_factory', side_effect=AssertionError('No inference should start')):
            self.app.jobs.start(generation['id'])
            self.wait()
        self.assertEqual('failed', self.store.generation(generation['id'])['status'])
        self.assertIn('reference', self.store.generation(generation['id'])['error'])

    def test_health_survives_temporary_file_disappearing_during_scan(self):
        temporary = self.store.root / '.ephemeral'
        temporary.write_bytes(b'temporary')
        stat = Path.stat
        calls = 0
        def disappearing(path, *args, **kwargs):
            nonlocal calls
            if path == temporary and kwargs.get('follow_symlinks', True):
                calls += 1
                if calls == 2:
                    temporary.unlink()
            return stat(path, *args, **kwargs)
        with patch.object(Path, 'stat', disappearing):
            result = self.app.dispatch('health', {})
        self.assertGreater(result['storage_bytes'], 0)
        self.assertFalse(temporary.exists())

    def test_complete_generation_cast_and_ordinals_are_not_rewritten_on_replay(self):
        engine = SyntheticEngine()
        self.app.jobs.engine_factory = lambda _: engine
        generation = self.generation()
        self.app.jobs.start(generation['id']); self.wait()
        before = self.store.generation(generation['id'])
        result = self.app.jobs.start(generation['id'])
        self.assertEqual(before, result)


if __name__ == '__main__':
    unittest.main()
