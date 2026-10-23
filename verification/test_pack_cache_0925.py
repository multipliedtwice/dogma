from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rimsky.packs as packs
import server as runtime_server
from rimsky.util import AppError, write_json
from server import Application
from test_core import FB2, SyntheticEngine, pack_fixture

MISMATCH = 'Pack checksum mismatch: model.gguf'


class PackCache0925(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.pack = self.root / 'pack'
        pack_fixture(self.pack)
        self.files = len(json.loads((self.pack / 'pack.json').read_text('utf-8'))['files'])
        self.hashed = []
        self.delay = 0
        real = packs.file_digest

        def counting(path, stop=None):
            self.hashed.append(Path(path).name)
            time.sleep(self.delay)
            return real(path, stop)
        patcher = patch.object(packs, 'file_digest', counting)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.open()

    def tearDown(self):
        self.app.close()
        self.temporary.cleanup()

    def open(self):
        app = Application(self.root / 'library', self.root / 'bin')
        app.store.update_settings({'pack_path': str(self.pack), 'device': 'cpu', 'align_words': False, 'cast_review': False})
        return app

    def restart(self):
        self.app.close()
        self.app = self.open()

    def health(self):
        return self.app.dispatch('health', {})['pack_error']

    def record(self):
        return json.loads((self.root / 'library' / 'pack-verification.json').read_text('utf-8'))

    def shift_mtime(self, name):
        path = self.pack / name
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    def tamper(self, keep_mtime=False):
        path = self.pack / 'model.gguf'
        stat = path.stat()
        raw = bytearray(path.read_bytes())
        raw[0] ^= 1
        path.write_bytes(bytes(raw))
        self.assertEqual((stat.st_size, stat.st_ino), (path.stat().st_size, path.stat().st_ino))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + (0 if keep_mtime else 1_000_000_000)))

    def wait(self):
        if self.app.jobs.thread:
            self.app.jobs.thread.join(timeout=30)
        self.assertIsNone(self.app.jobs.active)

    def test_failed_pack_is_hashed_once_until_a_file_changes(self):
        self.tamper()
        self.assertEqual(MISMATCH, self.health())
        self.assertEqual(1, len(self.hashed))
        for _ in range(5):
            self.assertEqual(MISMATCH, self.health())
        self.assertEqual(1, len(self.hashed))
        self.shift_mtime('tts/config.json')
        self.assertEqual(MISMATCH, self.health())
        self.assertEqual(2, len(self.hashed))

    def test_concurrent_health_calls_share_one_verification(self):
        self.delay = .05
        for expected, hashes in (('', self.files), (MISMATCH, self.files + 1)):
            if expected:
                self.tamper()
            barrier = threading.Barrier(4)
            results = []

            def poll():
                barrier.wait()
                results.append(self.health())
            threads = [threading.Thread(target=poll) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            self.assertEqual([expected] * 4, results)
            self.assertEqual(hashes, len(self.hashed))

    def test_generate_resume_and_repair_reuse_the_warm_cache(self):
        real = runtime_server.health

        def ready(settings, binaries, verify=None):
            result = real(settings, binaries, verify)
            result.update(ready=not result['pack_error'], missing=[])
            return result
        with patch.object(runtime_server, 'health', ready):
            self.app.jobs.engine_factory = lambda _binaries: SyntheticEngine()
            book = self.app.store.import_data(FB2, 'fixture.fb2')
            self.assertEqual('', self.health())
            self.assertEqual(self.files, len(self.hashed))
            with self.app.mutations:
                generation = self.app.dispatch('generate', {'book_id': book['id']})
            self.wait()
            generation = self.app.store.generation(generation['id'])
            self.assertEqual('ready', generation['status'], generation['error'])
            with self.app.mutations:
                revision = self.app.dispatch('revise', {'id': generation['id'], 'changes': [{'id': generation['segments'][0]['id'], 'spoken': 'Другое.'}]})
                self.app.dispatch('resume', {'id': revision['id']})
            self.wait()
            self.assertEqual('ready', self.app.store.generation(revision['id'])['status'])
            wav = self.app.store.root / 'audio' / f"{generation['segments'][1]['audio_hash']}.wav"
            raw = bytearray(wav.read_bytes())
            raw[-1] ^= 0xFF
            wav.write_bytes(bytes(raw))
            with self.app.mutations:
                self.app.dispatch('repair_audio', {'id': generation['id']})
            self.wait()
            self.assertEqual('ready', self.app.store.generation(generation['id'])['status'])
        self.assertEqual(self.files, len(self.hashed))

    def test_job_reverifies_a_changed_pack_before_rendering(self):
        book = self.app.store.import_data(FB2, 'fixture.fb2')
        pack = packs.load_pack(str(self.pack))
        generation = self.app.store.create_generation(book['id'], {'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'], 'pack_name': pack['name'], 'device': 'cpu', 'align_words': False})
        self.assertEqual('', self.health())
        warm = len(self.hashed)
        engines = []
        self.app.jobs.engine_factory = lambda _binaries: engines.append(SyntheticEngine()) or engines[-1]
        self.tamper()
        with self.app.mutations:
            self.app.dispatch('resume', {'id': generation['id']})
        self.wait()
        failed = self.app.store.generation(generation['id'])
        self.assertEqual(('failed', MISMATCH), (failed['status'], failed['error']))
        self.assertEqual([], engines)
        self.assertEqual(warm + 1, len(self.hashed))

    def test_stat_change_rehashes_the_whole_pack(self):
        self.assertEqual('', self.health())
        self.shift_mtime('tts/config.json')
        self.assertEqual('', self.health())
        self.assertEqual(2 * self.files, len(self.hashed))

    def test_restart_trusts_the_persisted_record_only_for_identical_stats(self):
        self.assertEqual('', self.health())
        self.assertEqual([str(self.pack.resolve())], list(self.record()))
        self.restart()
        self.assertEqual('', self.health())
        self.assertEqual(self.files, len(self.hashed))
        self.restart()
        self.tamper()
        self.assertEqual(MISMATCH, self.health())
        self.assertEqual(self.files + 1, len(self.hashed))
        self.assertEqual({}, self.record())
        self.restart()
        self.assertEqual(MISMATCH, self.health())
        self.assertEqual(self.files + 2, len(self.hashed))

    def test_same_size_tamper_with_restored_mtime_is_rehashed(self):
        self.assertEqual('', self.health())
        self.restart()
        self.tamper(keep_mtime=True)
        self.assertEqual(MISMATCH, self.health())
        self.assertEqual(self.files + 1, len(self.hashed))

    def test_unreadable_record_is_ignored(self):
        self.assertEqual('', self.health())
        for garbage in (b'{', b'[]', b'{"x": 1}'):
            (self.root / 'library' / 'pack-verification.json').write_bytes(garbage)
            self.restart()
            self.assertEqual('', self.health())
        self.assertEqual(4 * self.files, len(self.hashed))

    def test_selecting_the_unchanged_pack_does_not_rehash(self):
        self.assertEqual('', self.health())
        with self.app.mutations:
            selected = self.app.dispatch('select_pack', {'path': str(self.pack)})
        self.assertEqual('', selected['health']['pack_error'])
        self.assertEqual(self.files, len(self.hashed))

    def test_waiting_job_verification_honours_pause(self):
        stopped = threading.Event()
        stopped.set()
        with packs.VERIFYING:
            with self.assertRaisesRegex(AppError, 'paused'):
                packs.verified_pack(str(self.pack), {}, stopped)
        self.assertEqual([], self.hashed)

    def test_relocated_identical_pack_is_not_rehashed(self):
        self.assertEqual('', self.health())
        moved = self.root / 'moved-pack'
        self.pack = self.pack.rename(moved)
        self.app.store.update_settings({'pack_path': str(moved)})
        self.assertEqual('', self.health())
        self.assertEqual((self.files, [str(moved.resolve())]), (len(self.hashed), list(self.record())))
        self.restart()
        self.assertEqual('', self.health())
        self.assertEqual(self.files, len(self.hashed))

    def test_copied_pack_is_verified_again(self):
        self.assertEqual('', self.health())
        copy = self.root / 'copied-pack'
        shutil.copytree(self.pack, copy)
        self.app.store.update_settings({'pack_path': str(copy)})
        self.assertEqual('', self.health())
        self.assertEqual(2 * self.files, len(self.hashed))

    def test_pack_unchanged_rejects_files_changed_after_verification(self):
        message = 'Model pack files changed after verification. Select the pack again in Settings.'
        pack = packs.verified_pack(str(self.pack), {})
        self.assertIs(pack, packs.pack_unchanged(pack))
        path = self.pack / 'model.gguf'
        stat = path.stat()
        path.write_bytes(path.read_bytes() + b'!')
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        for changed in (pack, {key: value for key, value in pack.items() if key != '_stats'}):
            with self.assertRaises(AppError) as raised:
                packs.pack_unchanged(changed)
            self.assertEqual(message, str(raised.exception))
        fresh = packs.load_pack(str(self.pack))
        self.assertIs(fresh, packs.pack_unchanged(fresh))
        self.shift_mtime('tts/config.json')
        with self.assertRaisesRegex(AppError, 'changed after verification'):
            packs.pack_unchanged(fresh)
        path.unlink()
        with self.assertRaisesRegex(AppError, 'changed after verification'):
            packs.pack_unchanged(fresh)

    def test_bundled_pack_is_found_by_single_candidate_or_fingerprint(self):
        resources = self.root / 'resources'
        self.assertIsNone(packs.bundled_pack(resources))
        first, second = resources / 'packs' / 'first', resources / 'packs' / 'second'
        shutil.copytree(self.pack, first)
        self.assertEqual(str(first.resolve()), packs.bundled_pack(resources))
        pack_fixture(second)
        manifest = json.loads((second / 'pack.json').read_text('utf-8'))
        write_json(second / 'pack.json', {**manifest, 'name': 'Second bundled pack'})
        (resources / 'packs' / 'broken').mkdir()
        (resources / 'packs' / 'broken' / 'pack.json').write_text('{', 'utf-8')
        self.assertIsNone(packs.bundled_pack(resources))
        for folder in (first, second):
            self.assertEqual(str(folder.resolve()), packs.bundled_pack(resources, packs.load_pack(str(folder))['_fingerprint']))
        self.assertIsNone(packs.bundled_pack(resources, '0' * 64))

    def test_frozen_build_without_inference_runtime_asks_for_reinstall(self):
        binaries = self.root / 'frozen-bin'
        verify = lambda path: packs.load_pack(path)
        with patch.object(sys, 'frozen', True, create=True):
            status = packs.health(self.app.store.settings(), binaries, verify)
            self.assertEqual((False, ['rimsky-infer'], 'The bundled inference runtime is missing. Reinstall the application.', ''),
                             (status['ready'], status['missing'], status['runtime_error'], status['pack_error']))
            (binaries / 'infer').mkdir(parents=True)
            (binaries / 'infer' / 'rimsky-infer').write_text('', 'utf-8')
            status = packs.health(self.app.store.settings(), binaries, verify)
            self.assertEqual(([], ''), (status['missing'], status['runtime_error']))
        self.assertEqual('', packs.health(self.app.store.settings(), self.root / 'bin', verify)['runtime_error'])


if __name__ == '__main__':
    unittest.main()
