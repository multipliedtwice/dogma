from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy
import soundfile
from test_core import FB2, SyntheticEngine, VariedInvalidEngine
from test_review_gate_1004 import VOICES, make_pack
from rimsky import jobs as jobs_module
from rimsky.analysis import DEGRADED_HOLD
from rimsky.integrity import read_audio
from rimsky.jobs import (ALIGNER_EXITED, ALIGNMENT_UNAVAILABLE, IMPLAUSIBLE, RENDER_FAILED, STDERR_TAIL, Engine, Jobs, applied_delivery,
                         plausible, render_key, segment_pauses)
from rimsky.store import HOLD_NOTICE, Store
from rimsky.util import AppError, EngineTransportError

ROOT = Path(__file__).resolve().parents[1]
CHANGED = 'Model pack files changed after verification. Select the pack again in Settings.'


def tone(path, seconds, rate=8000):
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b'\x10\x27' * round(seconds * rate))
    return {'warning': ''}


def worker_module():
    spec = importlib.util.spec_from_file_location('render_probe_worker', ROOT / 'runtime' / 'worker.py')
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    return worker


def torch_stub(cuda=False, mps=False):
    return types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: cuda),
                                 backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps)))


class Base(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library')
        self.addCleanup(self.store.close)
        self.store.update_settings({'cast_review': False, 'device': 'cpu'})
        self.book = self.store.import_data(FB2, 'fixture.fb2')
        self.pack = make_pack(self.root / 'pack')

    def generation(self, book=None, pack=None, **config):
        pack = pack or self.pack
        return self.store.create_generation((book or self.book)['id'], {'pack_path': pack['_root'], 'pack_fingerprint': pack['_fingerprint'],
                                                                        'pack_name': pack['name'], 'device': 'cpu', 'align_words': False, **config})

    def run_job(self, generation, factory):
        jobs = Jobs(self.store, self.root, engine_factory=factory)
        self.addCleanup(jobs.shutdown)
        jobs.start(generation['id'])
        jobs.thread.join(10)
        self.assertFalse(jobs.thread.is_alive())
        return jobs, self.store.generation(generation['id'])


class Rendering(Base):
    def test_failed_render_is_retried_once_with_a_fresh_worker(self):
        engines = []
        class Flaky(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'render' and len(engines) == 1:
                    raise AppError('Speech output is empty, invalid, silent, or too long.')
                return super().call(method, payload, stop)
        def factory(_):
            engines.append(Flaky())
            return engines[-1]
        _, done = self.run_job(self.generation(), factory)
        self.assertEqual('ready', done['status'], done['error'])
        self.assertEqual(2, len(engines))
        self.assertEqual('', done['segments'][0]['warning'])

    def test_persistent_render_failure_marks_the_passage_and_resume_retries_it(self):
        generation = self.generation()
        target = generation['segments'][2]['spoken']
        class Broken(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'render' and payload['text'] == target:
                    raise AppError('RuntimeError: decoder produced NaN')
                return super().call(method, payload, stop)
        engines = []
        def factory(_):
            engines.append(Broken())
            return engines[-1]
        _, failed = self.run_job(generation, factory)
        self.assertEqual(('failed', RENDER_FAILED), (failed['status'], failed['error']))
        marked = [segment for segment in failed['segments'] if segment['status'] == 'failed']
        self.assertEqual([(target, 'RuntimeError: decoder produced NaN')], [(segment['spoken'], segment['warning']) for segment in marked])
        self.assertEqual(['ready', 'ready'], [segment['status'] for segment in failed['segments'][:2]])
        self.assertEqual(2, len(engines))
        self.assertEqual((2, len(failed['segments'])), tuple(self.store.generation_list(self.book['id'])[0]['progress'][key] for key in ('ready', 'total')))
        healthy = SyntheticEngine()
        _, ready = self.run_job(failed, lambda _: healthy)
        self.assertEqual('ready', ready['status'], ready['error'])
        repaired = next(segment for segment in ready['segments'] if segment['id'] == marked[0]['id'])
        self.assertEqual(('ready', ''), (repaired['status'], repaired['warning']))
        self.assertEqual(len(ready['segments']) - 2, healthy.rendered)

    def test_device_preflight_failure_stops_before_analysis(self):
        message = 'The selected device is not available for speech synthesis. Choose another device in Settings and resume.'
        class NoDevice(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'probe':
                    raise AppError(message)
                return super().call(method, payload, stop)
        engine = NoDevice()
        _, done = self.run_job(self.generation(), lambda _: engine)
        self.assertEqual(('failed', message), (done['status'], done['error']))
        self.assertEqual(0, engine.analyzed)
        self.assertFalse(done['analysis'].get('complete'))

    def test_resume_adopts_the_current_device_setting(self):
        probes = []
        class Probing(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'probe':
                    probes.append(payload['device'])
                return super().call(method, payload, stop)
        generation = self.generation(device='cuda')
        self.store.update_settings({'device': 'mps'})
        _, done = self.run_job(generation, lambda _: Probing())
        self.assertEqual('ready', done['status'], done['error'])
        self.assertEqual(['mps'], probes)
        self.assertEqual('mps', done['config']['device'])

    def test_worker_probe_checks_the_device_and_speech_folder_without_loading_models(self):
        worker = worker_module()
        pack = {'_root': str(self.pack['_root']), 'speech': {'path': 'tts'}}
        with patch.dict(sys.modules, {'torch': torch_stub(cuda=False, mps=True)}):
            self.assertEqual({'device': 'auto'}, worker.probe({'pack': pack, 'device': 'auto'}))
            self.assertEqual({'device': 'mps'}, worker.probe({'pack': pack, 'device': 'mps'}))
            self.assertEqual({'device': 'cpu'}, worker.probe({'pack': pack, 'device': 'cpu'}))
            for device in ('cuda', 'tpu'):
                with self.subTest(device=device), self.assertRaisesRegex(AppError, '^' + worker.DEVICE_UNAVAILABLE + '$'):
                    worker.probe({'pack': pack, 'device': device})
            with self.assertRaisesRegex(AppError, 'Missing speech model files'):
                worker.probe({'pack': {**pack, 'speech': {'path': 'absent'}}, 'device': 'cpu'})
            stdin = io.StringIO(json.dumps({'id': 'r1', 'method': 'probe', 'data': {'pack': pack, 'device': 'cuda'}}) + '\n')
            stdout = io.StringIO()
            with patch.object(sys, 'stdin', stdin), patch.object(sys, 'stdout', stdout):
                worker.main()
        self.assertEqual({'id': 'r1', 'error': worker.DEVICE_UNAVAILABLE}, json.loads(stdout.getvalue()))
        self.assertIsNone(worker.speech_model)
        self.assertIsNone(worker.text_model)

    def test_worker_without_delivery_control_adds_no_passage_warning(self):
        worker = worker_module()
        calls = []
        def generate(**kwargs):
            calls.append(kwargs)
            return [[.1] * 24000], 24000
        model = types.SimpleNamespace(get_supported_speakers=lambda: ['ryan'], generate_custom_voice=generate)
        qwen = types.SimpleNamespace(Qwen3TTSModel=types.SimpleNamespace(from_pretrained=lambda *a, **k: model))
        pack = {'_root': str(self.pack['_root']), '_fingerprint': 'fixture', 'speech': {'path': 'tts', 'variant': 'custom'},
                'voices': [{'id': 'n', 'speaker': 'Ryan'}]}
        with patch.dict(sys.modules, {'qwen_tts': qwen}), patch.object(worker, 'device_config', return_value=('cpu', 'float32')):
            result = worker.render({'pack': pack, 'text': 'Да.', 'voice_id': 'n', 'delivery': 'angry', 'language': 'Russian',
                                    'device': 'cpu', 'output': str(self.root / 'take.wav')})
        self.assertEqual('', result['warning'])
        self.assertNotIn('instruct', calls[0])

    def implausible(self, recover):
        generation = self.generation()
        target = 'Иван кивнул.'
        attempts = []
        class Long(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'render' and payload['text'] == target:
                    attempts.append(payload['text'])
                    return tone(Path(payload['output']), 1 if recover and len(attempts) > 1 else 90)
                return super().call(method, payload, stop)
        self.assertEqual((False, True), (plausible(90, target), plausible(1, target)))
        _, done = self.run_job(generation, lambda _: Long())
        self.assertEqual('ready', done['status'], done['error'])
        self.assertEqual(2, len(attempts))
        segment = next(segment for segment in done['segments'] if segment['spoken'] == target)
        self.assertEqual([], segment_pauses(segment))
        return segment

    def test_implausible_duration_is_retried_once_then_kept_with_a_notice(self):
        segment = self.implausible(False)
        self.assertEqual(IMPLAUSIBLE, segment['warning'])
        self.assertAlmostEqual(90, segment['duration'])

    def test_plausible_retry_replaces_an_implausible_first_take(self):
        segment = self.implausible(True)
        self.assertEqual('', segment['warning'])
        self.assertAlmostEqual(1, segment['duration'])

    def test_plausibility_bounds_are_generous(self):
        self.assertTrue(plausible(.4, 'Да.'))
        self.assertTrue(plausible(24, 'Pine trail'))
        self.assertFalse(plausible(150, '— Да.'))
        self.assertTrue(plausible(28, 'я' * 350))
        self.assertFalse(plausible(1, 'я' * 350))

    def test_letterless_passage_is_short_silence_without_synthesis(self):
        raw = FB2.replace('<p>Иван кивнул.</p>'.encode(), '<p>Иван кивнул.</p><p>…</p>'.encode())
        book = self.store.import_data(raw, 'marks.fb2')
        texts = []
        class Recording(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'render':
                    texts.append(payload['text'])
                return super().call(method, payload, stop)
        _, done = self.run_job(self.generation(book), lambda _: Recording())
        self.assertEqual('ready', done['status'], done['error'])
        silent = next(segment for segment in done['segments'] if segment['spoken'] == '…')
        self.assertNotIn('…', texts)
        self.assertTrue(texts)
        audio = read_audio(self.store.root / 'audio' / f'{silent["audio_hash"]}.wav')
        self.assertEqual((.3, 24000), (audio['duration'], audio['sample_rate']))
        self.assertEqual(.3, silent['duration'])

    def test_render_key_follows_the_applied_delivery_and_synthesis_identity(self):
        quiet = make_pack(self.root / 'quiet', voices=[*VOICES, {'id': 'twin', 'name': 'Twin', 'speaker': 'Ryan', 'family': 'other'}], instruction_control=False)
        voices = {voice['id']: voice for voice in quiet['voices']}
        config = {'device': 'cpu'}
        self.assertEqual('neutral', applied_delivery(quiet, voices['n'], 'angry'))
        key = lambda voice, delivery, take=0: render_key(quiet, config, 'Да.', voices[voice], applied_delivery(quiet, voices[voice], delivery), 'Russian', take)
        self.assertEqual(key('n', 'angry'), key('n', 'neutral'))
        self.assertEqual(key('n', 'neutral'), key('twin', 'sad'))
        self.assertNotEqual(key('n', 'neutral'), key('nv', 'neutral'))
        self.assertNotEqual(key('n', 'neutral'), key('n', 'neutral', 1))
        controlled = make_pack(self.root / 'controlled')
        voices = {voice['id']: voice for voice in controlled['voices']}
        self.assertNotEqual(render_key(controlled, config, 'Да.', voices['n'], 'angry', 'Russian', 0),
                            render_key(controlled, config, 'Да.', voices['n'], 'neutral', 'Russian', 0))
        self.assertEqual(render_key(controlled, config, '◆ ◆', voices['n'], 'neutral', 'Russian', 0),
                         render_key(quiet, {'device': 'cuda'}, '* *', voices['a'], 'neutral', 'English', 3))

    def test_render_and_aligner_use_the_detected_speech_language(self):
        raw = FB2.replace(b'<lang>ru</lang>', b'<lang>en</lang>').replace('Verification book'.encode(), 'Declared English'.encode())
        book = self.store.import_data(raw, 'declared.fb2')
        languages = []
        class Recording(SyntheticEngine):
            def call(self, method, payload, stop):
                if method == 'render':
                    languages.append(payload['language'])
                return super().call(method, payload, stop)
        _, done = self.run_job(self.generation(book), lambda _: Recording())
        self.assertEqual('ready', done['status'], done['error'])
        self.assertEqual({'Russian'}, set(languages))

    def test_degraded_passage_is_held_with_the_exact_degraded_warning(self):
        engine = VariedInvalidEngine()
        _, done = self.run_job(self.generation(), lambda _: engine)
        self.assertEqual('held', done['status'], done['error'])
        self.assertEqual(0, engine.rendered)
        self.assertEqual({(f'{DEGRADED_HOLD} {HOLD_NOTICE}', 'review', None)}, {(segment['warning'], segment['status'], segment['audio_hash']) for segment in done['segments']})

    def test_pack_change_during_rendering_fails_with_the_exact_message(self):
        pack_file = Path(self.pack['_root']) / 'tts' / 'config.json'
        class Tampering(SyntheticEngine):
            def call(self, method, payload, stop):
                result = super().call(method, payload, stop)
                if method == 'render':
                    stat = pack_file.stat()
                    os.utime(pack_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
                return result
        engine = Tampering()
        _, done = self.run_job(self.generation(), lambda _: engine)
        self.assertEqual(('failed', CHANGED), (done['status'], done['error']))
        self.assertEqual(1, engine.rendered)
        self.assertEqual({('pending', None)}, {(segment['status'], segment['audio_hash']) for segment in done['segments']})

    def test_bundled_pack_with_the_pinned_fingerprint_is_found_after_relocation(self):
        resources = self.root / 'resources'
        shutil.copytree(self.pack['_root'], resources / 'packs' / 'bundled')
        config = {'pack_path': str(self.root / 'old-bundle' / 'packs' / 'bundled'), 'pack_fingerprint': self.pack['_fingerprint']}
        with self.assertRaisesRegex(AppError, 'valid pack.json'):
            Jobs(self.store, self.root)._pack(config)
        found = Jobs(self.store, self.root, resources=resources)._pack(config, verify=True)
        self.assertEqual(str((resources / 'packs' / 'bundled').resolve()), found['_root'])
        with self.assertRaises(AppError):
            Jobs(self.store, self.root, resources=resources)._pack({**config, 'pack_fingerprint': '0' * 64})


class Alignment(Base):
    def setUp(self):
        super().setUp()
        self.pack = make_pack(self.root / 'aligned-pack', aligner=True)

    def aligned(self, aligner):
        generation = self.generation(align_words=True)
        with patch.object(jobs_module, 'resolve_binary', return_value='rimsky-align'), patch.object(jobs_module, 'Engine', aligner):
            jobs, done = self.run_job(generation, lambda _: SyntheticEngine())
        return jobs, done

    def test_aligner_exit_publishes_ready_audio_and_repair_does_not_restart(self):
        calls = []
        class Process:
            code = None
            def poll(self):
                return self.code
        class Exiting:
            def __init__(self, *args, **kwargs):
                self.process = Process()
            def call(self, method, payload, stop, timeout):
                calls.append(payload['text'])
                if len(calls) == 2:
                    self.process.code = 1
                    raise EngineTransportError('Local inference process exited before completing its request.', 'exit')
                return [{'text': payload['text'], 'start': 0, 'end': .1}]
            def close(self):
                pass
        jobs, done = self.aligned(Exiting)
        self.assertEqual('ready', done['status'], done['error'])
        self.assertEqual(2, len(calls))
        self.assertEqual([[]] * (len(done['segments']) - 1), [segment['cues'] for segment in done['segments'][1:]])
        self.assertTrue(done['segments'][0]['cues'])
        self.assertEqual({ALIGNER_EXITED}, {segment['warning'] for segment in done['segments'][1:]})
        self.assertEqual(sorted({segment['audio_hash'] for segment in done['segments'][1:]}), done['analysis']['alignment_failed'])
        thread = jobs.thread
        self.assertEqual('ready', jobs.repair(done['id'])['status'])
        self.assertIs(thread, jobs.thread)

    def test_aligner_that_cannot_start_still_publishes_ready_audio(self):
        def broken(*args, **kwargs):
            raise OSError('cannot execute aligner')
        jobs, done = self.aligned(broken)
        self.assertEqual('ready', done['status'], done['error'])
        self.assertEqual({ALIGNER_EXITED}, {segment['warning'] for segment in done['segments']})
        self.assertEqual('ready', jobs.repair(done['id'])['status'])

    def test_unavailable_aligner_is_recorded_so_repair_reports_verified_audio(self):
        self.pack = make_pack(self.root / 'plain-pack')
        jobs, done = self.run_job(self.generation(align_words=True), lambda _: SyntheticEngine())
        self.assertEqual('ready', done['status'], done['error'])
        self.assertEqual({ALIGNMENT_UNAVAILABLE}, {segment['warning'] for segment in done['segments']})
        thread = jobs.thread
        self.assertEqual('ready', jobs.repair(done['id'])['status'])
        self.assertIs(thread, jobs.thread)


class Transport(unittest.TestCase):
    def engine(self, body):
        original = subprocess.Popen
        def launch(command, **options):
            return original([sys.executable, '-u', '-c', 'import sys, json, time\n' + body], **options)
        with patch('rimsky.jobs.subprocess.Popen', side_effect=launch):
            engine = Engine(Path('.'))
        self.addCleanup(engine.close)
        return engine

    def test_process_exit_error_carries_a_bounded_stderr_tail(self):
        engine = self.engine("sys.stdin.readline()\nsys.stderr.write('x' * 9000 + ' ImportError: DLL load failed\\n')\nsys.stderr.flush()")
        with self.assertRaises(EngineTransportError) as raised:
            engine.call('render', {}, threading.Event(), timeout=10)
        message = str(raised.exception)
        self.assertEqual('exit', raised.exception.code)
        self.assertTrue(message.startswith('Local inference process exited before completing its request. Process output: '), message[:120])
        self.assertTrue(message.endswith('ImportError: DLL load failed'))
        self.assertLessEqual(len(message), STDERR_TAIL + 120)

    def test_silent_exit_keeps_the_plain_message(self):
        engine = self.engine('sys.stdin.readline()')
        with self.assertRaisesRegex(EngineTransportError, '^Local inference process exited before completing its request.$'):
            engine.call('render', {}, threading.Event(), timeout=10)

    def test_reconcile_timeout_is_pair_local(self):
        class Process:
            code = None
            def poll(self):
                return self.code
        engines = []
        class Hanging:
            def __init__(self):
                self.process = Process()
            def call(self, method, payload, stop):
                self.process.code = -15
                raise EngineTransportError('Local inference exceeded its 1800-second operation limit.', 'timeout')
            def close(self):
                pass
        def factory(_):
            engines.append(Hanging())
            return engines[-1]
        jobs = Jobs(None, Path('.'), engine_factory=factory)
        jobs.engine = factory(None)
        characters = [{'id': 'a', 'name': 'Иван', 'aliases': [], 'evidence': ['p1']},
                      {'id': 'b', 'name': 'Иван Петров', 'aliases': [], 'evidence': ['p2']}]
        cast, merges, unmerged, skipped, state = jobs._reconcile({}, characters, {'p1': 'Иван пришёл.', 'p2': 'Иван Петров ушёл.'})
        self.assertEqual(({}, 1, 0), (merges, unmerged, skipped))
        self.assertEqual([['a', 'b', None]], state['reconcile']['decisions'])
        self.assertEqual(2, len(engines))


if __name__ == '__main__':
    unittest.main()
