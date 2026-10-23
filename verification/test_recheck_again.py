from __future__ import annotations

import json
import queue
import sqlite3
import tempfile
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import unittest
from pathlib import Path

from test_core import FB2, complete_audio, pack_fixture, write_wave
from rimsky.analysis import cast_voices
from rimsky.books import extract_blocks, xml
from rimsky.export import build_export
from rimsky.jobs import Engine, Jobs
from rimsky.packs import load_pack
from rimsky.store import Store
from rimsky.util import AppError, file_digest, write_json
from server import Application, handler_for
from test_lifecycle import request


class SecondRecheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'library')
        self.book = self.store.import_data(FB2, 'fixture.fb2')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def generation(self):
        return self.store.create_generation(self.book['id'], {'device': 'cpu', 'pack_name': 'Verification'})

    def test_cast_separates_copresent_speakers_before_preferring_tag_match(self):
        tags = [f'attribute{i}' for i in range(30)]
        voices = [
            {'id': 'n', 'role': 'narrator'},
            {'id': 'a', 'tags': tags},
            {'id': 'b', 'tags': []},
        ]
        characters = [{'id': key, 'name': key, 'description': ' '.join(tags)} for key in ('one', 'two')]
        segments = [{'speaker': key, 'chapter_id': 'c1', 'source_id': 'p1', 'ordinal': 0, 'characters': 20} for key in ('one', 'two')]
        result = cast_voices(characters, voices, segments)
        self.assertEqual({c['voice_id'] for c in result if c['id'] != 'narrator'}, {'a', 'b'})
        self.assertFalse(any(c.get('voice_reused') for c in result))

    def test_pack_allows_shared_custom_speaker_under_different_profile_ids(self):
        pack = pack_fixture(self.root / 'pack')
        pack['voices'][2]['speaker'] = 'aIdEn'
        write_json(self.root / 'pack' / 'pack.json', pack)
        loaded = load_pack(str(self.root / 'pack'))
        self.assertEqual(loaded['voices'][2]['speaker'], 'aIdEn')

    def test_pack_rejects_whitespace_around_custom_speaker(self):
        pack = pack_fixture(self.root / 'pack')
        pack['voices'][1]['speaker'] = ' Aiden '
        write_json(self.root / 'pack' / 'pack.json', pack)
        with self.assertRaises(AppError):
            load_pack(str(self.root / 'pack'))

    def test_failed_statement_rolls_back_instead_of_leaking_into_next_save(self):
        original = self.store.settings()
        self.store.execute("CREATE TABLE audit_fixture(value TEXT)")
        self.store.execute("""CREATE TRIGGER audit_fixture_abort BEFORE UPDATE ON settings BEGIN
            INSERT INTO audit_fixture VALUES('must roll back');
            SELECT RAISE(FAIL, 'injected write failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.update_settings({'theme': 'dark'})
        self.store.execute('DROP TRIGGER audit_fixture_abort')
        self.assertEqual(self.store.rows('SELECT * FROM audit_fixture'), [])
        self.assertEqual(self.store.settings(), original)
        self.assertFalse(self.store.db.in_transaction)

    def test_failed_generation_delete_preserves_its_saved_exports(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        exported = build_export(self.store, generation['id'], 'srt', None, self.root)
        path = self.store.root / 'exports' / exported['file']
        original = path.read_bytes()
        self.store.execute("""CREATE TRIGGER deny_generation_delete BEFORE DELETE ON generations
            BEGIN SELECT RAISE(ABORT, 'injected delete failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.delete_generation(generation['id'])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(self.store.generation(generation['id'])['exports']), 1)

    def test_mixed_inline_content_stays_one_readable_passage(self):
        blocks = extract_blocks(xml('<div>До <em>важного</em> слова.</div>'.encode()), 'c1', lambda _: None)
        self.assertEqual([b['text'] for b in blocks], ['До важного слова.'])
        self.assertIn('<em>важного</em>', blocks[0]['html'])

    def test_block_and_inline_runs_keep_source_order(self):
        blocks = extract_blocks(xml('<div>До <em>начала</em>.<p>Середина.</p>После <strong>конца</strong>.</div>'.encode()), 'c1', lambda _: None)
        self.assertEqual([b['text'] for b in blocks], ['До начала.', 'Середина.', 'После конца.'])

    def test_nested_lists_do_not_flatten_item_boundaries(self):
        blocks = extract_blocks(xml(b'<ul><li>First</li><li>Second <em>item</em></li></ul>'), 'c1', lambda _: None)
        self.assertEqual([b['text'] for b in blocks], ['First', 'Second item'])

    def test_clone_pack_allows_reused_neutral_recordings(self):
        folder = self.root / 'clone-pack'
        pack = pack_fixture(folder)
        pack['speech'] = {'path': 'tts', 'variant': 'clone', 'instruction_control': False}
        for index, voice in enumerate(pack['voices']):
            filename = f'voice-{index}.wav'
            write_wave(folder / filename, seconds=.1 if index == 0 else .2)
            voice.pop('speaker', None)
            voice['references'] = {'neutral': {'audio': filename, 'text': 'Reference text'}}
            pack['files'][filename] = file_digest(folder / filename)
        write_json(folder / 'pack.json', pack)
        loaded = load_pack(str(folder), verify=True)
        self.assertEqual(len(loaded['voices']), 3)

    def protocol_engine(self, body):
        original = subprocess.Popen
        fixture = "import sys, json, time\nprint('protocol-fixture-ready', flush=True)\n" + body
        def launch(command, **options):
            process = original([sys.executable, '-u', '-c', fixture], **options)
            ready = queue.Queue()
            threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
            try:
                self.assertEqual('protocol-fixture-ready\n', ready.get(timeout=10))
            except Exception:
                process.kill()
                process.communicate()
                raise
            return process
        with patch('rimsky.jobs.subprocess.Popen', side_effect=launch):
            engine = Engine(self.root)
        self.addCleanup(engine.close)
        return engine

    def test_engine_rejects_response_without_request_identity(self):
        engine = self.protocol_engine("request=json.loads(sys.stdin.readline())\nprint(json.dumps({'result': 'unbound'}), flush=True)\ntime.sleep(2)")
        with self.assertRaisesRegex(AppError, '(?i)(uncorrelated|invalid)'):
            engine.call('analyze', {}, threading.Event(), timeout=1)

    def test_engine_rejects_nonobject_response_without_waiting_for_timeout(self):
        engine = self.protocol_engine("sys.stdin.readline()\nprint('[1,2]',flush=True)\ntime.sleep(2)")
        with self.assertRaisesRegex(AppError, '(?i)invalid'):
            engine.call('analyze', {}, threading.Event(), timeout=1)

    def test_engine_rejects_ambiguous_success_error_envelope(self):
        engine = self.protocol_engine("request=json.loads(sys.stdin.readline())\nprint(json.dumps({'id':request['id'], 'result':True, 'error':'ambiguous'}),flush=True)\ntime.sleep(2)")
        with self.assertRaisesRegex(AppError, '(?i)invalid'):
            engine.call('analyze', {}, threading.Event(), timeout=1)

    def test_engine_cancelled_before_call_does_not_write_a_request(self):
        engine = self.protocol_engine('sys.stdin.read()')
        stop = threading.Event()
        stop.set()
        with patch.object(engine.process.stdin, 'write', wraps=engine.process.stdin.write) as write:
            with self.assertRaisesRegex(AppError, 'paused'):
                engine.call('render', {}, stop, timeout=1)
            write.assert_not_called()

    def test_engine_reuses_live_process_and_closes_owned_pipes(self):
        body = "for line in sys.stdin:\n request=json.loads(line)\n print(json.dumps({'id':request['id'],'result':request['data']}),flush=True)"
        engine = self.protocol_engine(body)
        for value in (1, 2):
            self.assertEqual(engine.call('echo', {'value': value}, threading.Event(), timeout=2), {'value': value})
        engine.close()
        self.assertIsNotNone(engine.process.poll())
        self.assertTrue(engine.process.stdin.closed)
        self.assertTrue(engine.process.stdout.closed)
        self.assertFalse(engine.reader.is_alive())

    def test_engine_exit_is_reported_without_waiting_for_operation_timeout(self):
        engine = self.protocol_engine('sys.stdin.readline()')
        with self.assertRaisesRegex(AppError, 'exited'):
            engine.call('analyze', {}, threading.Event(), timeout=2)

    def test_runtime_cleanup_failure_releases_active_generation(self):
        generation = self.generation()
        jobs = Jobs(self.store, self.root)
        jobs.active = generation['id']
        class BrokenCleanup:
            def close(self):
                raise OSError('injected cleanup failure')
        jobs.engine = BrokenCleanup()
        with patch.object(jobs, '_prepare', return_value=None):
            jobs._run(generation['id'])
        self.assertIsNone(jobs.active)
        self.assertIsNone(jobs.engine)
        result = self.store.generation(generation['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('cleanup', result['error'])

    def test_export_can_be_cancelled_before_any_output_is_published(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        stop = threading.Event()
        stop.set()
        with self.assertRaisesRegex(AppError, 'cancelled'):
            build_export(self.store, generation['id'], 'srt', None, self.root, stop)
        self.assertEqual(list((self.store.root/'exports').iterdir()), [])
        self.assertEqual(self.store.generation(generation['id'])['exports'], [])

    def test_closing_during_encoding_reaps_the_child_process(self):
        from rimsky.export import run_encoder
        stop = threading.Event()
        original = subprocess.Popen
        children = []
        def launch(command, **options):
            child = original(command, **options)
            children.append(child)
            stop.set()
            return child
        with patch('rimsky.export.subprocess.Popen', side_effect=launch):
            with self.assertRaisesRegex(AppError, 'cancelled'):
                run_encoder([sys.executable, '-c', 'import time; time.sleep(30)'], stop)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed)
        self.assertTrue(children[0].stderr.closed)

    def test_managed_encoder_produces_real_m4b_with_cancellation_available(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        exported = build_export(self.store, generation['id'], 'm4b', None, self.root, threading.Event())
        self.assertGreater(exported['size'], 128)
        self.assertEqual(len(self.store.generation(generation['id'])['exports']), 1)

    def test_pause_does_not_wait_for_a_long_mutating_request(self):
        app = Application(self.root/'pause-library', self.root)
        token = 'verification-only-local-session'
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(app, token, True))
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        result = []
        done = threading.Event()
        app.jobs.active = 'fixture-generation'
        def pause_request():
            try:
                result.append(request({'port': server.server_port, 'token': token}, 'pause', {'id':'fixture-generation'}))
            except Exception as error:
                result.append(error)
            finally:
                done.set()
        client = threading.Thread(target=pause_request, daemon=True)
        try:
            with app.mutations:
                client.start()
                responsive = done.wait(2)
            client.join(timeout=6)
            self.assertTrue(responsive, 'Pause was blocked by the mutation lock')
            self.assertEqual(result, [{'requested':True}])
            self.assertTrue(app.jobs.stop.is_set())
        finally:
            server.shutdown()
            server.server_close()
            app.close()
            serving.join(timeout=2)

    def test_application_close_drains_mutation_before_closing_database(self):
        app = Application(self.root/'closing-library', self.root)
        closed = threading.Event()
        errors = []
        def close():
            try:
                app.close()
            except Exception as error:
                errors.append(error)
            finally:
                closed.set()
        with app.mutations:
            thread = threading.Thread(target=close, daemon=True)
            thread.start()
            self.assertTrue(app.closing.wait(2))
            self.assertFalse(closed.is_set())
            self.assertIsInstance(app.store.settings(), dict)
        thread.join(timeout=3)
        self.assertTrue(closed.is_set())
        self.assertEqual(errors, [])
        with self.assertRaisesRegex(AppError, 'closing'):
            app.dispatch('library', {})

    def test_failed_book_delete_preserves_all_generations_and_files(self):
        app = Application(self.root/'deletion-library', self.root)
        try:
            book = app.store.import_data(FB2, 'book.fb2')
            histories = [app.store.create_generation(book['id'], {'device':'cpu'}) for _ in range(2)]
            exports = []
            for generation in histories:
                complete_audio(app.store, generation)
                exports.append(build_export(app.store, generation['id'], 'srt', None, self.root))
            oldest = histories[0]['id']
            app.store.execute(f"""CREATE TRIGGER deny_oldest BEFORE DELETE ON generations
                WHEN OLD.id='{oldest}' BEGIN SELECT RAISE(ABORT, 'injected book delete failure'); END""")
            with self.assertRaises(sqlite3.IntegrityError):
                app.dispatch('delete_book', {'id':book['id']})
            self.assertEqual(len(app.store.book(book['id'])['generations']), 2)
            self.assertTrue(all((app.store.root/'exports'/item['file']).is_file() for item in exports))
            self.assertTrue((app.store.root/'books'/book['id']/'source.fb2').is_file())
        finally:
            app.close()

    def test_successful_book_delete_keeps_other_books_shared_audio(self):
        other = self.store.import_data(FB2.replace(b'<book-title>', b'<book-title>Other '), 'other.fb2')
        one = self.generation()
        two = self.store.create_generation(other['id'], {'device':'cpu'})
        complete_audio(self.store, one)
        complete_audio(self.store, two)
        expected = {s['audio_hash'] for s in self.store.generation(two['id'])['segments']}
        self.store.delete_book(self.book['id'])
        self.assertEqual(len(self.store.library()), 1)
        self.assertTrue(all((self.store.root/'audio'/f'{key}.wav').exists() for key in expected))
        self.assertFalse((self.store.root/'books'/self.book['id']).exists())


if __name__ == '__main__':
    unittest.main()
