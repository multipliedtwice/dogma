from __future__ import annotations

import io
import json
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'runtime'))
from rimsky.jobs import Engine, Jobs, segment_pauses
from rimsky.books import epub, image_asset
from rimsky.export import build_export
from rimsky.integrity import accepted_audio
from server import Application
from PIL import Image
from rimsky.store import Store
from rimsky.util import AppError
from test_core import FB2, SyntheticEngine, epub_fixture, pack_fixture, write_wave


class FourthRecheck(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library')
        self.store.update_settings({'cast_review': False})
        self.book = self.store.import_data(FB2, 'fixture.fb2')
        self.pack = pack_fixture(self.root / 'pack')

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def generation(self):
        return self.store.create_generation(self.book['id'], {
            'pack_path': self.pack['_root'], 'pack_fingerprint': self.pack['_fingerprint'],
            'pack_name': self.pack['name'], 'device': 'cpu', 'align_words': False,
        })

    def run_generation(self, generation, engine=None):
        jobs = Jobs(self.store, self.root, lambda _: engine or SyntheticEngine())
        self.addCleanup(jobs.shutdown)
        jobs.start(generation['id'])
        jobs.thread.join(5)
        self.assertFalse(jobs.thread.is_alive())
        return self.store.generation(generation['id'])

    def test_shutdown_permanently_rejects_late_preparation(self):
        generation = self.generation()
        jobs = Jobs(self.store, self.root, lambda _: SyntheticEngine())
        try:
            jobs.shutdown()
            with self.assertRaisesRegex(AppError, '(?i)(closing|closed|shut)'):
                jobs.start(generation['id'])
            self.assertTrue(jobs.stop.is_set())
            self.assertIsNone(jobs.active)
            self.assertEqual('paused', self.store.generation(generation['id'])['status'])
        finally:
            jobs.shutdown()

    def protocol_engine(self, body):
        original = subprocess.Popen
        def launch(command, **options):
            return original([sys.executable, '-u', '-c', 'import sys, json, time\n' + body], **options)
        with patch('rimsky.jobs.subprocess.Popen', side_effect=launch):
            engine = Engine(self.root)
        self.addCleanup(engine.close)
        return engine

    def blocked_send(self, stop, timeout):
        engine = self.protocol_engine('time.sleep(30)')
        done = threading.Event()
        results = []
        def call():
            try:
                results.append(engine.call('analyze', {'text': 'x' * 1024 * 1024}, stop, timeout=timeout))
            except Exception as error:
                results.append(error)
            finally:
                done.set()
        caller = threading.Thread(target=call, daemon=True)
        caller.start()
        return engine, caller, done, results

    def test_inference_timeout_includes_blocked_request_write(self):
        engine, caller, done, results = self.blocked_send(threading.Event(), .2)
        try:
            completed = done.wait(2)
        finally:
            engine.close()
            caller.join(2)
        self.assertTrue(completed, 'The operation timeout did not cover a blocked stdin write')
        self.assertEqual(1, len(results))
        self.assertIsInstance(results[0], AppError)
        self.assertRegex(str(results[0]), '(?i)(limit|timeout|exceeded)')
        self.assertIsNotNone(engine.process.poll())

    def test_pause_cancels_blocked_inference_request_write(self):
        stop = threading.Event()
        engine, caller, done, results = self.blocked_send(stop, 30)
        try:
            time.sleep(.05)
            stop.set()
            completed = done.wait(2)
        finally:
            engine.close()
            caller.join(2)
        self.assertTrue(completed, 'Pause did not interrupt a blocked stdin write')
        self.assertIsInstance(results[0], AppError)
        self.assertRegex(str(results[0]), '(?i)paused')

    def test_valid_large_request_still_round_trips(self):
        engine = self.protocol_engine("for line in sys.stdin:\n request=json.loads(line)\n print(json.dumps({'id':request['id'],'result':len(request['data']['text'])}),flush=True)")
        self.assertEqual(300000, engine.call('analyze', {'text': 'я' * 300000}, threading.Event(), timeout=3))

    def test_moved_pack_with_identical_revision_can_resume(self):
        generation = self.generation()
        original_config = generation['config']
        moved = self.root / 'moved-pack'
        shutil.move(self.pack['_root'], moved)
        self.store.update_settings({'pack_path': str(moved)})
        finished = self.run_generation(generation)
        self.assertEqual('ready', finished['status'], finished['error'])
        self.assertEqual({**original_config, 'device': self.store.settings()['device']}, finished['config'])

    def test_relocation_never_substitutes_a_different_pack_revision(self):
        generation = self.generation()
        shutil.rmtree(self.pack['_root'])
        alternate = self.root / 'different-pack'
        pack_fixture(alternate)
        manifest = json.loads((alternate / 'pack.json').read_text())
        manifest['name'] = 'Different frozen revision'
        (alternate / 'pack.json').write_text(json.dumps(manifest))
        self.store.update_settings({'pack_path': str(alternate)})
        jobs = Jobs(self.store, self.root, lambda _: SyntheticEngine())
        self.addCleanup(jobs.shutdown)
        with self.assertRaises(AppError):
            jobs.start(generation['id'])
        self.assertIsNone(jobs.active)
        self.assertEqual('paused', self.store.generation(generation['id'])['status'])

    def test_relocation_still_verifies_model_file_checksums(self):
        generation = self.generation()
        alternate = self.root / 'moved-pack'
        shutil.move(self.pack['_root'], alternate)
        (alternate / 'model.gguf').write_bytes(b'Changed model bytes')
        self.store.update_settings({'pack_path': str(alternate)})
        finished = self.run_generation(generation)
        self.assertEqual('failed', finished['status'])
        self.assertIn('checksum', finished['error'].lower())
        self.assertFalse(any(s['status'] == 'ready' for s in finished['segments']))

    def plain(self, generation):
        return next(segment for segment in generation['segments'] if segment['spoken'] == 'Иван кивнул.' and not segment_pauses(segment))

    def aligned_generation(self, plain=False):
        generation = self.run_generation(self.generation())
        self.assertEqual('ready', generation['status'], generation['error'])
        segment = self.plain(generation) if plain else generation['segments'][0]
        cues = [{'text': segment['spoken'], 'start': 0, 'end': segment['duration']}]
        self.store.execute('UPDATE segments SET cues=? WHERE generation_id=? AND id=?',
                           (json.dumps(cues), generation['id'], segment['id']))
        return self.store.generation(generation['id'])

    def shared_revision(self, generation):
        revision_id = 'shared-' + generation['id']
        with self.store.lock, self.store.db:
            self.store.db.execute("INSERT INTO generations(id,book_id,name,created,updated,status,config,cast,analysis,parent_id) "
                                  "SELECT ?,book_id,name,created,updated,'paused',config,[cast],analysis,id FROM generations WHERE id=?",
                                  (revision_id, generation['id']))
            self.store.db.execute('INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken,speaker,delivery,status,audio_hash,duration,cues,warning,performance) SELECT ?,id,ordinal,chapter_id,source_id,start,end,text,spoken,speaker,delivery,status,audio_hash,duration,cues,warning,performance FROM segments WHERE generation_id=?',
                                  (revision_id, generation['id']))
        return self.store.generation(revision_id)

    def test_audio_repair_clears_timing_and_updates_shared_generations(self):
        generation = self.aligned_generation(plain=True)
        segment = self.plain(generation)
        self.assertTrue(segment['cues'])
        revision = self.shared_revision(generation)
        wav = self.store.root / 'audio' / f'{segment["audio_hash"]}.wav'
        wav.unlink()
        class LongerSpeech(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'render':
                    self.rendered += 1
                    return write_wave(Path(payload['output']), seconds=.25)
                return super().call(method, payload, stop)
        speech = LongerSpeech()
        finished = self.run_generation(revision, speech)
        self.assertEqual('ready', finished['status'], finished['error'])
        self.assertEqual(1, speech.rendered)
        for gid in (generation['id'], revision['id']):
            current = next(s for s in self.store.generation(gid)['segments'] if s['id'] == segment['id'])
            self.assertEqual(.25, current['duration'])
            self.assertEqual([], current['cues'], 'Word timing was bound to the old, missing audio')

    def test_unchanged_audio_reuse_retains_accepted_word_timing(self):
        generation = self.aligned_generation()
        segment = generation['segments'][0]
        revision = self.shared_revision(generation)
        speech = SyntheticEngine()
        finished = self.run_generation(revision, speech)
        self.assertEqual('ready', finished['status'], finished['error'])
        self.assertEqual(0, speech.rendered)
        self.assertEqual(segment['cues'], finished['segments'][0]['cues'])

    def test_application_close_rejects_a_request_already_waiting_to_start(self):
        app = Application(self.root / 'closing-library', self.root)
        book = app.store.import_data(FB2, 'fixture.fb2')
        generation = app.store.create_generation(book['id'], {
            'pack_path': self.pack['_root'], 'pack_fingerprint': self.pack['_fingerprint'],
            'pack_name': self.pack['name'], 'device': 'cpu',
        })
        app.jobs.engine_factory = lambda _: SyntheticEngine()
        held, release, shutdown_called = (threading.Event() for _ in range(3))
        errors = []
        def late_request():
            with app.mutations:
                held.set()
                release.wait(3)
                try:
                    app.jobs.start(generation['id'])
                except Exception as error:
                    errors.append(error)
        original_shutdown = app.jobs.shutdown
        def shutdown():
            original_shutdown()
            shutdown_called.set()
        request = threading.Thread(target=late_request, daemon=True)
        closer = threading.Thread(target=app.close, daemon=True)
        with patch.object(app.jobs, 'shutdown', side_effect=shutdown):
            try:
                request.start()
                self.assertTrue(held.wait(2))
                closer.start()
                self.assertTrue(shutdown_called.wait(2))
                release.set()
                request.join(3)
                closer.join(3)
                self.assertFalse(request.is_alive())
                self.assertFalse(closer.is_alive())
                self.assertEqual(1, len(errors))
                self.assertIsInstance(errors[0], AppError)
                self.assertIsNone(app.jobs.active)
            finally:
                release.set()
                request.join(3)
                closer.join(3)

    def test_interrupted_audio_repair_resumes_without_using_old_word_cues(self):
        generation = self.aligned_generation()
        segment = generation['segments'][0]
        revision = self.shared_revision(generation)
        wav = self.store.root / 'audio' / f'{segment["audio_hash"]}.wav'
        wav.unlink()
        self.store.execute("CREATE TRIGGER fail_audio_publish BEFORE UPDATE OF duration ON segments "
                           "BEGIN SELECT RAISE(ABORT, 'injected audio publication failure'); END")
        broken = self.run_generation(revision)
        self.assertEqual('failed', broken['status'])
        self.assertTrue(accepted_audio(wav)['reset_cues'])
        with self.assertRaisesRegex(AppError, 'repair'):
            build_export(self.store, generation['id'], 'srt', None, self.root)
        self.store.execute('DROP TRIGGER fail_audio_publish')
        speech = SyntheticEngine()
        recovered = self.run_generation(broken, speech)
        self.assertEqual('ready', recovered['status'], recovered['error'])
        self.assertEqual(0, speech.rendered)
        self.assertFalse(accepted_audio(wav)['reset_cues'])
        for gid in (generation['id'], revision['id']):
            self.assertEqual([], self.store.generation(gid)['segments'][0]['cues'])

    def test_audio_metadata_finalize_failure_can_resume_its_committed_repair(self):
        generation = self.aligned_generation(plain=True)
        segment = self.plain(generation)
        revision = self.shared_revision(generation)
        wav = self.store.root / 'audio' / f'{segment["audio_hash"]}.wav'
        wav.unlink()
        from rimsky.util import write_json
        def fail_finalization(path, value):
            if value.get('reset_cues') is False:
                raise OSError('injected metadata finalization failure')
            return write_json(path, value)
        with patch('rimsky.jobs.write_json', side_effect=fail_finalization):
            broken = self.run_generation(revision)
        self.assertEqual('failed', broken['status'])
        self.assertTrue(accepted_audio(wav)['reset_cues'])
        speech = SyntheticEngine()
        recovered = self.run_generation(broken, speech)
        self.assertEqual('ready', recovered['status'], recovered['error'])
        self.assertEqual(0, speech.rendered)
        self.assertFalse(accepted_audio(wav)['reset_cues'])

    def test_playback_asset_rejects_a_decodable_but_changed_audio_file(self):
        generation = self.run_generation(self.generation())
        segment = generation['segments'][0]
        path = self.store.root / 'audio' / f'{segment["audio_hash"]}.wav'
        raw = bytearray(path.read_bytes())
        raw[-2] ^= 7
        path.write_bytes(raw)
        app = Application.__new__(Application)
        app.store = self.store
        with self.assertRaisesRegex(AppError, 'checksum'):
            app.asset('audio', segment['audio_hash'])

    def test_playback_asset_rejects_unfinished_audio_repair(self):
        generation = self.run_generation(self.generation())
        segment = generation['segments'][0]
        metadata = self.store.root / 'audio' / f'{segment["audio_hash"]}.json'
        value = json.loads(metadata.read_text())
        value['reset_cues'] = True
        metadata.write_text(json.dumps(value))
        app = Application.__new__(Application)
        app.store = self.store
        with self.assertRaisesRegex(AppError, 'repair'):
            app.asset('audio', segment['audio_hash'])

    def test_request_byte_limit_is_checked_before_dispatch(self):
        engine = self.protocol_engine('sys.stdin.read()')
        with patch.object(engine.process.stdin, 'write', wraps=engine.process.stdin.write) as write:
            with self.assertRaisesRegex(AppError, '4 MiB'):
                engine.call('analyze', {'text': 'я' * (3 * 1024 * 1024)}, threading.Event(), timeout=1)
            write.assert_not_called()

    def png(self, color):
        output = io.BytesIO()
        Image.new('RGB', (16, 16), color).save(output, 'PNG')
        return output.getvalue()

    def test_epub_repeated_images_and_cover_are_decoded_once(self):
        raw = epub_fixture({
            'OEBPS/a.xhtml': b'<html><body><p>Start.</p><img src="picture.png"/><img src="./picture.png"/></body></html>',
            'OEBPS/b.xhtml': b'<html><body><p>End.</p><img src="picture.png"/></body></html>',
            'OEBPS/content.opf': b'<package><manifest><item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/><item id="cover" href="picture.png" properties="cover-image"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>',
            'OEBPS/picture.png': self.png('red'),
        })
        folder = self.root / 'images'
        folder.mkdir()
        with patch('rimsky.books.image_asset', wraps=image_asset) as decode:
            book = epub(raw, folder)
        self.assertEqual(1, decode.call_count)
        self.assertEqual(1, len(book['assets']))
        self.assertEqual(book['assets'][0]['id'], book['cover'])

    def test_decoded_book_asset_budget_is_bounded_without_double_counting(self):
        folder = self.root / 'images'
        folder.mkdir()
        assets = {}
        raw = self.png('red')
        first = image_asset(raw, folder, assets)
        size = (folder / first).stat().st_size
        with patch('rimsky.books.MAX_EXPANDED', size + 1):
            self.assertEqual(first, image_asset(raw, folder, assets))
            with self.assertRaisesRegex(AppError, '(?i)(image|asset).*(limit|budget|exceed)'):
                image_asset(self.png('blue'), folder, assets)
        self.assertEqual(1, len(list(folder.iterdir())))


if __name__ == '__main__':
    unittest.main()
