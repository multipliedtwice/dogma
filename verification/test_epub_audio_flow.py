from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_core import FB2, SyntheticEngine, complete_audio, pack_fixture
from rimsky.analysis import DEGRADED_HOLD, UNRESOLVED_WARNING
from rimsky.export import build_export, run_encoder
from rimsky.integrity import validate_coverage
from rimsky.jobs import Jobs
from rimsky.store import PREVIEW_NOTICE, Store
from rimsky.util import AppError


class EpubAudioFlow(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library')
        self.store.update_settings({'cast_review': False})
        self.book = self.store.import_data(FB2, 'flow.fb2')
        self.pack = pack_fixture(self.root / 'pack')

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def generation(self, **config):
        return self.store.create_generation(self.book['id'], {
            'pack_path': self.pack['_root'], 'pack_fingerprint': self.pack['_fingerprint'],
            'pack_name': self.pack['name'], 'device': 'cpu', 'align_words': False, **config,
        })

    def test_pause_after_successful_render_preserves_completed_audio(self):
        class PauseAfterRender(SyntheticEngine):
            def call(self, method, payload, stop):
                result = super().call(method, payload, stop)
                if method == 'render':
                    stop.set()
                return result
        generation = self.generation()
        first = PauseAfterRender()
        jobs = Jobs(self.store, self.root, lambda _: first)
        jobs.start(generation['id'])
        jobs.thread.join(10)
        paused = self.store.generation(generation['id'])
        self.assertEqual('paused', paused['status'])
        self.assertEqual('ready', paused['segments'][0]['status'])
        self.assertTrue(paused['segments'][0]['audio_hash'])
        second = SyntheticEngine()
        jobs.engine_factory = lambda _: second
        jobs.start(generation['id'])
        jobs.thread.join(10)
        done = self.store.generation(generation['id'])
        self.assertEqual('ready', done['status'], done['error'])
        self.assertEqual(len(done['segments']) - 1, second.rendered)
        validate_coverage(self.book, done['config']['chapters'], done['segments'])
        jobs.shutdown()

    def test_successful_but_truncated_encoder_output_is_not_published(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        def shortened(command, stop=None, **options):
            if '-show_streams' in command:
                return run_encoder(command, stop, **options)
            return run_encoder(command[:-1] + ['-t', '0.08', command[-1]], stop, **options)
        with patch('rimsky.export.run_encoder', side_effect=shortened):
            with self.assertRaises(AppError):
                build_export(self.store, generation['id'], 'm4b', None, self.root)
        self.assertEqual([], self.store.rows('SELECT * FROM exports'))
        self.assertEqual([], list((self.store.root / 'exports').iterdir()))
        self.assertEqual([], list((self.store.root / 'work').iterdir()))

    def test_encoder_success_with_non_audio_bytes_is_not_published(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        def invalid_output(command, stop=None, **options):
            if '-show_streams' in command:
                return run_encoder(command, stop, **options)
            Path(command[-1]).write_bytes(b'not an audio container' * 40)
            return subprocess.CompletedProcess(command, 0, b'', b'')
        with patch('rimsky.export.run_encoder', side_effect=invalid_output):
            with self.assertRaises(AppError):
                build_export(self.store, generation['id'], 'mp3', None, self.root)
        self.assertEqual([], self.store.rows('SELECT * FROM exports'))
        self.assertEqual([], list((self.store.root / 'exports').iterdir()))

    def unknown_correction(self, **config):
        generation = self.generation(**config)
        engine = SyntheticEngine()
        jobs = Jobs(self.store, self.root, lambda _: engine)
        self.addCleanup(jobs.shutdown)
        jobs.start(generation['id'])
        jobs.thread.join(10)
        done = self.store.generation(generation['id'])
        self.assertEqual('ready', done['status'], done['error'])
        rendered = engine.rendered
        revised = self.store.revise(done['id'], [{'id': done['segments'][0]['id'], 'speaker': 'unknown'}])
        jobs.start(revised['id'])
        jobs.thread.join(10)
        return engine, rendered, done, self.store.generation(revised['id'])

    def test_unknown_speaker_correction_is_held_with_its_unresolved_warning(self):
        engine, rendered, done, corrected = self.unknown_correction()
        self.assertEqual('held', corrected['status'], corrected['error'])
        held = corrected['segments'][0]
        self.assertEqual(('unknown', 'review', None), (held['speaker'], held['status'], held['audio_hash']))
        self.assertIn('Unresolved speaker', held['warning'])
        self.assertEqual(rendered, engine.rendered)

    def test_unknown_speaker_correction_preview_keeps_the_narrator_fallback_warning(self):
        engine, rendered, done, corrected = self.unknown_correction(unresolved_policy='render_with_warning')
        self.assertEqual('held', corrected['status'], corrected['error'])
        preview = corrected['segments'][0]
        self.assertEqual(('unknown', 'ready'), (preview['speaker'], preview['status']))
        self.assertIn(UNRESOLVED_WARNING, preview['warning'])
        self.assertIn(PREVIEW_NOTICE, preview['warning'])

    def test_capacity_failure_splits_targets_without_dropping_text_or_cast(self):
        from test_core import CastEngine
        from rimsky import util
        capacity_error = getattr(util, 'AnalysisCapacityError', AppError)
        class LimitedEngine(CastEngine):
            def __init__(self):
                super().__init__()
                self.attempted = []
            def call(self, method, payload, stop):
                if method == 'analyze':
                    self.attempted.append([source['id'] for source in payload['segments']])
                    if len(payload['segments']) > 1:
                        raise capacity_error('Controlled analysis output limit')
                return super().call(method, payload, stop)
        generation = self.generation()
        engine = LimitedEngine()
        jobs = Jobs(self.store, self.root, lambda _: engine)
        jobs.start(generation['id'])
        jobs.thread.join(10)
        done = self.store.generation(generation['id'])
        jobs.shutdown()
        self.assertEqual('ready', done['status'], done['error'])
        validate_coverage(self.book, done['config']['chapters'], done['segments'])
        self.assertEqual({'Narrator', 'Маша', 'Иван'}, {item['name'] for item in done['cast']})
        base_count = len(generation['segments'])
        self.assertEqual(base_count, len(done['analysis']['completed_sources']))
        self.assertEqual(len(done['analysis']['casting_inputs']), sum(len(segment['performance']['analysis_parts']) for segment in done['segments']))
        self.assertTrue(any(segment['performance']['internal_transition'] for segment in done['segments']))
        self.assertLessEqual(len(engine.attempted), 2 * base_count)
        self.assertGreater(len(engine.render_voices), 1)

    def test_split_analysis_resumes_without_repeating_committed_sources(self):
        from rimsky import util
        capacity_error = getattr(util, 'AnalysisCapacityError', AppError)
        class LimitedEngine(SyntheticEngine):
            def __init__(self, fail=False):
                super().__init__()
                self.accepted = []
                self.fail = fail
            def call(self, method, payload, stop):
                if method == 'analyze':
                    if len(payload['segments']) > 1:
                        raise capacity_error('Controlled capacity limit')
                    if self.fail and self.accepted:
                        raise AppError('Controlled unrelated runtime failure')
                    self.accepted.extend(source['id'] for source in payload['segments'])
                return super().call(method, payload, stop)
        generation = self.generation()
        first = LimitedEngine(fail=True)
        jobs = Jobs(self.store, self.root, lambda _: first)
        jobs.start(generation['id'])
        jobs.thread.join(10)
        checkpoint = self.store.generation(generation['id'])
        self.assertEqual('failed', checkpoint['status'])
        self.assertEqual(1, len(checkpoint['analysis'].get('completed_sources', [])))
        second = LimitedEngine()
        jobs.engine_factory = lambda _: second
        jobs.start(generation['id'])
        jobs.thread.join(10)
        done = self.store.generation(generation['id'])
        jobs.shutdown()
        self.assertEqual('ready', done['status'], done['error'])
        self.assertFalse(set(first.accepted) & set(second.accepted))
        self.assertEqual(len(generation['segments']), len(first.accepted) + len(second.accepted))
        self.assertEqual(len(generation['segments']), len(done['analysis']['casting_inputs']))
        validate_coverage(self.book, done['config']['chapters'], done['segments'])

    def test_single_target_capacity_failure_degrades_the_passage_instead_of_failing(self):
        from rimsky import util
        capacity_error = getattr(util, 'AnalysisCapacityError', AppError)
        class NoCapacity(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'analyze':
                    self.analyzed += 1
                    raise capacity_error('Registry itself does not fit')
                return super().call(method, payload, stop)
        generation = self.generation()
        engine = NoCapacity()
        jobs = Jobs(self.store, self.root, lambda _: engine)
        jobs.start(generation['id'])
        jobs.thread.join(10)
        done = self.store.generation(generation['id'])
        jobs.shutdown()
        self.assertEqual('held', done['status'], done['error'])
        self.assertEqual(10, engine.analyzed)
        self.assertEqual(0, engine.rendered)
        self.assertEqual([{'source': s['id'], 'reason': 'Registry itself does not fit'} for s in generation['segments']], done['analysis']['degraded'])
        self.assertTrue(all(s['speaker'] == 'unknown' and s['warning'].startswith(DEGRADED_HOLD) and (s['status'], s['audio_hash']) == ('review', None)
                            for s in done['segments']))
        self.assertEqual({'degraded'}, {item['tier'] for item in done['analysis']['speaker_resolution']})
        validate_coverage(self.book, done['config']['chapters'], done['segments'])

    def test_repeated_identical_invalid_annotations_trip_the_circuit_breaker(self):
        generation = self.generation()
        engine = SyntheticEngine(invalid=True)
        calls = []
        original = engine.call
        def call(method, payload, stop):
            if method == 'analyze':
                calls.append(([s['id'] for s in payload['segments']], payload['retry_error']))
            return original(method, payload, stop)
        engine.call = call
        jobs = Jobs(self.store, self.root, lambda _: engine)
        jobs.start(generation['id'])
        jobs.thread.join(10)
        done = self.store.generation(generation['id'])
        jobs.shutdown()
        self.assertEqual('failed', done['status'])
        self.assertIn('validation failures became systemic', done['error'])
        self.assertLessEqual(len(calls), 8)
        self.assertFalse(done['analysis'].get('complete', False))

    def test_repeated_typed_validation_failures_trip_the_circuit_breaker(self):
        from rimsky.util import AnalysisInvalidError
        class AlwaysInvalid(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'analyze':
                    self.analyzed += 1
                    raise AnalysisInvalidError('Speech structure did not cover every numbered unit.')
                return super().call(method, payload, stop)
        engine = AlwaysInvalid()
        jobs = Jobs(self.store, self.root, lambda _: engine)
        generation = self.generation()
        jobs.start(generation['id'])
        jobs.thread.join(10)
        done = self.store.generation(generation['id'])
        jobs.shutdown()
        self.assertEqual('failed', done['status'])
        self.assertIn('validation failures became systemic', done['error'])
        self.assertLessEqual(engine.analyzed, 9)
        self.assertEqual(5, done['analysis']['validation_guard']['consecutive'])
        self.store.execute("UPDATE generations SET status='paused',error='' WHERE id=?", (generation['id'],))
        resumed_engine = AlwaysInvalid()
        resumed = Jobs(self.store, self.root, lambda _: resumed_engine)
        resumed.start(generation['id'])
        resumed.thread.join(10)
        after = self.store.generation(generation['id'])
        resumed.shutdown()
        self.assertEqual('failed', after['status'])
        self.assertGreater(len(after['analysis']['degraded_retry_attempted']), 0)
        self.assertLessEqual(resumed_engine.analyzed, 10)
        # Even when every passage is permanently bad, each resume advances and
        # each degraded passage is retried at most once. The book eventually
        # reaches review instead of failing on the same first passages forever.
        for _ in range(4):
            if after['status'] == 'review':
                break
            self.store.execute("UPDATE generations SET status='paused',error='' WHERE id=?", (generation['id'],))
            another_engine = AlwaysInvalid()
            another = Jobs(self.store, self.root, lambda _: another_engine)
            another.start(generation['id'])
            another.thread.join(10)
            after = self.store.generation(generation['id'])
            another.shutdown()
        self.assertIn(after['status'], ('review', 'held'))
        self.assertTrue(after['analysis']['complete'])

    def test_invalid_structure_answer_holds_only_its_passage_for_every_provider(self):
        from rimsky.util import AnalysisInvalidError
        class InvalidStructure(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'analyze' and payload['segments'][0]['id'] == generation['segments'][0]['id']:
                    raise AnalysisInvalidError('Speech structure did not cover every numbered unit.')
                return super().call(method, payload, stop)
        for provider in ('local', 'codex_cli', 'claude_cli'):
            with self.subTest(provider=provider):
                generation = self.generation(character_analysis=provider)
                jobs = Jobs(self.store, self.root, lambda _: InvalidStructure())
                jobs.start(generation['id'])
                jobs.thread.join(10)
                done = self.store.generation(generation['id'])
                jobs.shutdown()
                self.assertEqual('held', done['status'], done['error'])
                self.assertTrue(done['analysis']['complete'])
                degraded = {item['source'] for item in done['analysis']['degraded']}
                self.assertEqual({generation['segments'][0]['id']}, degraded)
                self.assertTrue(any(segment['status'] == 'ready' for segment in done['segments'][1:]))

    def test_unknown_speaker_correction_refuses_a_complete_export_without_another_render(self):
        for config in ({}, {'unresolved_policy': 'render_with_warning'}):
            with self.subTest(config=config):
                engine, rendered, done, corrected = self.unknown_correction(**config)
                before = engine.rendered
                with self.assertRaises(AppError) as refused:
                    build_export(self.store, corrected['id'], 'm4b', None, self.root)
                self.assertEqual('held_segments', refused.exception.code)
                self.assertEqual(before, engine.rendered)
                self.assertEqual([], self.store.rows('SELECT * FROM exports'))
                self.assertEqual([segment['audio_hash'] for segment in done['segments'][2:]],
                                 [segment['audio_hash'] for segment in corrected['segments'][2:]])

    def test_audio_export_requires_probe_but_subtitles_do_not(self):
        from rimsky.packs import resolve_binary
        generation = self.generation()
        complete_audio(self.store, generation)
        with patch('rimsky.export.resolve_binary', side_effect=lambda name, root: None if name == 'ffprobe' else resolve_binary(name, root)):
            with self.assertRaisesRegex(AppError, 'FFprobe'):
                build_export(self.store, generation['id'], 'm4b', None, self.root)
            subtitle = build_export(self.store, generation['id'], 'srt', None, self.root)
        self.assertEqual('srt', subtitle['format'])
        self.assertEqual(1, len(self.store.rows('SELECT * FROM exports')))

    def test_wrong_export_chapter_metadata_is_rejected(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        def changed_metadata(command, stop=None, **options):
            if '-show_streams' not in command and '-map_chapters' in command:
                command = list(command)
                command[command.index('-map_chapters') + 1] = '-1'
            return run_encoder(command, stop, **options)
        with patch('rimsky.export.run_encoder', side_effect=changed_metadata):
            with self.assertRaisesRegex(AppError, 'manifest'):
                build_export(self.store, generation['id'], 'm4b', None, self.root)
        self.assertEqual([], self.store.rows('SELECT * FROM exports'))
        self.assertEqual([], list((self.store.root / 'work').iterdir()))

    def test_failed_chapter_zip_validation_leaves_no_partial_archive(self):
        generation = self.generation()
        complete_audio(self.store, generation)
        outputs = []
        def shortened(command, stop=None, **options):
            if '-show_streams' not in command:
                outputs.append(command[-1])
                if len(outputs) == 2:
                    command = command[:-1] + ['-t', '0.02', command[-1]]
            return run_encoder(command, stop, **options)
        with patch('rimsky.export.run_encoder', side_effect=shortened):
            with self.assertRaisesRegex(AppError, 'manifest'):
                build_export(self.store, generation['id'], 'chapters', None, self.root)
        self.assertEqual(2, len(outputs))
        self.assertEqual([], self.store.rows('SELECT * FROM exports'))
        self.assertEqual([], list((self.store.root / 'exports').iterdir()))
        self.assertEqual([], list((self.store.root / 'work').iterdir()))

    def test_worker_emits_typed_capacity_errors_and_no_fabricated_result(self):
        import contextlib
        import importlib.util
        import io
        from rimsky import util
        capacity_error = getattr(util, 'AnalysisCapacityError', AppError)
        spec = importlib.util.spec_from_file_location('flow_worker_protocol', Path(__file__).resolve().parents[1] / 'runtime/worker.py')
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        output = io.TextIOWrapper(io.BytesIO(), encoding='cp1252')
        request = json.dumps({'id': 'capacity-request', 'method': 'analyze', 'data': {'text': 'следователя'}}, ensure_ascii=False) + '\n'
        def reject(data):
            self.assertEqual('следователя', data['text'])
            raise capacity_error('Controlled capacity limit')
        with patch.object(worker, 'analyze', side_effect=reject), \
             patch('sys.stdin', io.TextIOWrapper(io.BytesIO(request.encode('utf-8')), encoding='cp1252')), contextlib.redirect_stdout(output):
            worker.main()
        reply = json.loads(output.buffer.getvalue().decode('utf-8'))
        self.assertEqual({'id', 'error', 'error_code'}, set(reply))
        self.assertEqual('capacity-request', reply['id'])
        self.assertEqual('analysis_capacity', reply['error_code'])

    def test_real_child_capacity_reply_keeps_runtime_available_for_smaller_requests(self):
        import sys
        import threading
        from rimsky.jobs import Engine
        from rimsky import util
        capacity_error = getattr(util, 'AnalysisCapacityError', AppError)
        script = self.root / 'rimsky-infer'
        script.write_text('#!' + sys.executable + '\nimport sys,json\nfor line in sys.stdin:\n r=json.loads(line)\n answer={"id":r["id"],"error":"Controlled limit","error_code":"analysis_capacity"} if r["method"]=="large" else {"id":r["id"],"result":True}\n print(json.dumps(answer),flush=True)\n')
        script.chmod(0o755)
        engine = Engine(self.root)
        try:
            with self.assertRaises(capacity_error):
                engine.call('large', {}, threading.Event(), timeout=5)
            self.assertIsNone(engine.process.poll())
            self.assertIs(True, engine.call('small', {}, threading.Event(), timeout=5))
        finally:
            engine.close()

    def test_unknown_structured_error_code_is_not_a_capacity_retry_signal(self):
        import sys
        import threading
        from rimsky.jobs import Engine
        script = self.root / 'rimsky-infer'
        script.write_text('#!' + sys.executable + '\nimport sys,json\nfor line in sys.stdin:\n r=json.loads(line)\n print(json.dumps({"id":r["id"],"error":"Failure","error_code":"ignore_everything"}),flush=True)\n')
        script.chmod(0o755)
        engine = Engine(self.root)
        try:
            with self.assertRaisesRegex(AppError, 'Invalid or uncorrelated'):
                engine.call('analyze', {}, threading.Event(), timeout=5)
            self.assertIsNotNone(engine.process.poll())
        finally:
            engine.close()

    def test_real_analysis_wrapper_marks_output_truncation_as_capacity_failure(self):
        import importlib.util
        from unittest.mock import MagicMock
        from rimsky import util
        spec = importlib.util.spec_from_file_location('flow_worker_analysis', Path(__file__).resolve().parents[1] / 'runtime/worker.py')
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        model = MagicMock()
        model.tokenize.return_value = [1] * 200
        model.create_chat_completion.return_value = {'choices': [{'finish_reason': 'length', 'message': {'content': '{'}}]}
        with patch.object(worker, 'load_text', return_value=model):
            with self.assertRaises(getattr(util, 'AnalysisCapacityError', AppError)):
                worker.analyze({'pack': {**self.pack, 'text_model': {**self.pack['text_model'], 'analysis_mode': 'legacy'}}, 'device': 'cpu', 'cast': [], 'segments': []})
        self.assertEqual(1, model.create_chat_completion.call_count)

    def test_paused_invalid_speech_is_not_accepted_as_completed_audio(self):
        class InvalidSpeech(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'render':
                    Path(payload['output']).write_bytes(b'invalid')
                    stop.set()
                    return {'warning': ''}
                return super().call(method, payload, stop)
        generation = self.generation()
        engine = InvalidSpeech()
        jobs = Jobs(self.store, self.root, lambda _: engine)
        jobs.start(generation['id'])
        jobs.thread.join(10)
        done = self.store.generation(generation['id'])
        jobs.shutdown()
        self.assertEqual('paused', done['status'])
        self.assertTrue(all(segment['status'] != 'ready' for segment in done['segments']))
        self.assertEqual([], list((self.store.root / 'audio').glob('*.json')))
