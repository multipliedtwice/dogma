from __future__ import annotations

import ast
import importlib.util
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from safetensors.numpy import save_file

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'training')]

from rimsky.util import AppError, file_digest, write_json
from rimsky_training.common import json_read
from rimsky_training.dataset import cut_audio
from rimsky_training.inference import encode_dataset, validate_codes, transcribe
from rimsky_training.runner import create_plan, execute_training, run_process, checkpoint_info, upstream_files
import test_training_pipeline as fixtures


class TrainingContractAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self):
        fixture = fixtures.TrainingPipeline(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_audio_ids_outside_qwen_codec_vocabulary_are_rejected(self):
        original = {'audio': '/local/a.wav', 'text': 'Проверка', 'ref_audio': '/local/r.wav'}
        for value in (2048, 3000, 60000):
            with self.subTest(value=value), self.assertRaises(AppError):
                validate_codes({**original, 'audio_codes': [[value] * 16]}, original)
        self.assertEqual([[2047] * 16], validate_codes({**original, 'audio_codes': [[2047] * 16]}, original)['audio_codes'])

    def test_wrong_tokenizer_is_rejected_before_loading_codec(self):
        fixture = self.fixture()
        dataset, _ = fixture.dataset()
        tokenizer = fixtures.fake_model(fixture.root / 'not-a-codec')
        module, calls, loads = fixture.codec_module()
        with patch.dict(sys.modules, {'qwen_tts': module}), self.assertRaises(AppError):
            encode_dataset(dataset, tokenizer, fixture.root / 'bad-encode', device='cpu')
        self.assertEqual([], loads)
        self.assertFalse((fixture.root / 'bad-encode').exists())

    def test_encoder_weights_changed_during_encoding_do_not_publish(self):
        fixture = self.fixture()
        dataset, _ = fixture.dataset()
        tokenizer = fixtures.fake_tokenizer(fixture.root / 'tokenizer')
        module, _, _ = fixture.codec_module()
        original = module.Qwen3TTSTokenizer.from_pretrained
        def load(*args, **kwargs):
            model = original(*args, **kwargs)
            encode = model.encode
            def changed(paths):
                result = encode(paths)
                (tokenizer / 'model.safetensors').write_bytes(b'changed during execution')
                return result
            model.encode = changed
            return model
        module.Qwen3TTSTokenizer.from_pretrained = load
        output = fixture.root / 'encoder-race'
        with patch.dict(sys.modules, {'qwen_tts': module}), self.assertRaises(AppError):
            encode_dataset(dataset, tokenizer, output, device='cpu')
        self.assertEqual('failed', json_read(output / 'stage.json')['status'])
        self.assertFalse((output / 'encoding.json').exists())

    def test_model_flag_alone_does_not_admit_a_non_tts_model(self):
        fixture = self.fixture()
        run, base, upstream = fixture.plan()
        plan = json_read(run / 'plan.json')
        config = json_read(base / 'config.json')
        config['model_type'] = 'qwen3'
        write_json(base / 'config.json', config)
        output = fixture.root / 'wrong-family'
        with self.assertRaises(AppError):
            create_plan(Path(plan['dataset_path']), Path(plan['encoded_path']), base, upstream, output)
        self.assertFalse(output.exists())

    def test_plan_rejects_codes_from_a_different_base_tokenizer(self):
        fixture = self.fixture()
        run, base, upstream = fixture.plan()
        plan = json_read(run / 'plan.json')
        save_file({'encoder.fixture': np.zeros((2, 2), dtype=np.float32)}, str(base / 'speech_tokenizer/model.safetensors'))
        output = fixture.root / 'wrong-codec-revision'
        with self.assertRaises(AppError):
            create_plan(Path(plan['dataset_path']), Path(plan['encoded_path']), base, upstream, output)
        self.assertFalse(output.exists())

    def test_same_codec_bytes_at_another_path_are_accepted(self):
        fixture = self.fixture()
        run, base, upstream = fixture.plan()
        plan = json_read(run / 'plan.json')
        moved = fixture.root / 'same-base-new-path'
        shutil.copytree(base, moved)
        report = create_plan(Path(plan['dataset_path']), Path(plan['encoded_path']), moved, upstream, fixture.root / 'same-plan')
        self.assertEqual('planned', report['status'])

    def test_checkpoint_with_unchanged_base_weights_is_not_a_success(self):
        fixture = self.fixture()
        run, base, _ = fixture.plan()
        def copy_only(command, cwd, log, timeout):
            out = run / 'checkpoints/checkpoint-epoch-0'
            shutil.copytree(base, out)
            config = json_read(out / 'config.json')
            config['tts_model_type'] = 'custom_voice'
            write_json(out / 'config.json', config)
            log.write_text('Controlled copied base, no training')
        with patch('rimsky_training.runner.training_preflight', return_value={}), patch('rimsky_training.runner.run_process', side_effect=copy_only):
            with self.assertRaises(AppError):
                execute_training(run)
        self.assertEqual('failed', json_read(run / 'run.json')['status'])
        self.assertNotIn('checkpoints', json_read(run / 'run.json'))

    def test_malformed_weight_file_is_not_a_checkpoint(self):
        model = fixtures.fake_model(self.root / 'checkpoint', 'custom_voice')
        (model / 'model.safetensors').write_bytes(b'not a weight file')
        with self.assertRaises(AppError):
            checkpoint_info(model, 'reader')

    def test_only_nested_tokenizer_weights_are_not_a_checkpoint(self):
        model = fixtures.fake_model(self.root / 'checkpoint', 'custom_voice')
        (model / 'model.safetensors').unlink()
        with self.assertRaises(AppError):
            checkpoint_info(model, 'reader')

    def test_missing_trained_layer_is_not_a_completed_run(self):
        fixture = self.fixture()
        run, _, _ = fixture.plan()
        def incomplete(command, cwd, log, timeout):
            (run / 'checkpoints').mkdir()
            model = fixtures.fake_model(run / 'checkpoints/checkpoint-epoch-0', 'custom_voice')
            save_file({'talker.model.codec_embedding.weight': np.ones((2, 2), dtype=np.float32)}, str(model / 'model.safetensors'))
        with patch('rimsky_training.runner.training_preflight', return_value={}), patch('rimsky_training.runner.run_process', side_effect=incomplete):
            with self.assertRaises(AppError):
                execute_training(run)
        self.assertEqual('failed', json_read(run / 'run.json')['status'])

    def test_trainer_bypassing_text_projection_is_rejected_at_planning(self):
        fixture = self.fixture()
        _, _, upstream = fixture.plan()
        path = upstream / 'finetuning/sft_12hz.py'
        with path.open('a') as handle:
            handle.write('\ndef train(model, text, codec):\n    input_text_embedding = model.talker.model.text_embedding(text)\n    input_embeddings = input_text_embedding + codec\n')
        with self.assertRaises(AppError):
            upstream_files(upstream)

    def test_projected_trainer_input_path_is_accepted(self):
        fixture = self.fixture()
        _, _, upstream = fixture.plan()
        path = upstream / 'finetuning/sft_12hz.py'
        with path.open('a') as handle:
            handle.write('\ndef train(model, text, codec):\n    input_text_embedding = model.talker.text_projection(model.talker.model.text_embedding(text))\n    input_embeddings = input_text_embedding + codec\n')
        self.assertEqual(upstream, upstream_files(upstream)[0])

    @unittest.skipIf(os.name == 'nt', 'This execution checks the POSIX process group only.')
    def test_successful_training_leader_does_not_leave_live_children(self):
        ready, result = self.root / 'ready', self.root / 'leaked'
        child = f'import pathlib,time;pathlib.Path({str(ready)!r}).write_text("ready");time.sleep(.5);pathlib.Path({str(result)!r}).write_text("leaked")'
        leader = f'import subprocess,time,pathlib,sys;p=subprocess.Popen([sys.executable,"-c",{child!r}]);\nwhile not pathlib.Path({str(ready)!r}).exists(): time.sleep(.01)\n'
        run_process([sys.executable, '-c', leader], self.root, self.root / 'training.log', 10)
        self.assertTrue(ready.exists())
        time.sleep(.7)
        self.assertFalse(result.exists())

    def test_clip_extraction_seeks_before_decoding_whole_recording(self):
        source = self.root / 'source.wav'
        fixtures.write_tone(source, seconds=15)
        commands = []
        original = subprocess.run
        def capture(command, **kwargs):
            commands.append(command)
            return original(command, **kwargs)
        with patch('rimsky_training.dataset.subprocess.run', side_effect=capture):
            cut_audio(source, self.root / 'cut.wav', 8.25, 10.25, shutil.which('ffmpeg'))
        self.assertLess(commands[0].index('-ss'), commands[0].index('-i'))
        audio, sr = sf.read(source, dtype='int16')
        result, _ = sf.read(self.root / 'cut.wav', dtype='int16')
        np.testing.assert_array_equal(audio[round(8.25 * sr):round(10.25 * sr)], result)

    def test_asr_weights_changed_during_transcription_do_not_publish(self):
        fixture = self.fixture()
        asr = fixtures.fake_model(fixture.root / 'asr')
        aligner = fixtures.fake_model(fixture.root / 'aligner')
        def infer(**kwargs):
            (asr / 'model.safetensors').write_bytes(b'changed during ASR')
            return [types.SimpleNamespace(text='Слово', time_stamps=[types.SimpleNamespace(text='Слово', start_time=1., end_time=2.)])]
        model = types.SimpleNamespace(transcribe=infer)
        qwen = types.SimpleNamespace(Qwen3ASRModel=types.SimpleNamespace(from_pretrained=lambda *args, **kwargs: model))
        torch = types.SimpleNamespace(float32='float32', float16='float16')
        out = fixture.root / 'asr-changed'
        with patch.dict(sys.modules, {'qwen_asr': qwen, 'torch': torch}), self.assertRaises(AppError):
            transcribe(fixture.corpus_path, 'track-0', asr, aligner, out)
        self.assertFalse((out / 'transcript.json').exists())
        self.assertEqual('failed', json_read(out / 'stage.json')['status'])


class TrainerPreparation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = fixtures.TrainingPipeline(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        _, _, self.upstream = self.fixture.plan()
        self.path = self.upstream / 'finetuning/sft_12hz.py'
        self.path.write_text(self.path.read_text() + '\ndef assemble(model, input_text_ids, text_embedding_mask, codec):\n    input_text_embedding = model.talker.model.text_embedding(input_text_ids) * text_embedding_mask\n    input_embeddings = input_text_embedding + codec\n    return input_embeddings\n', encoding='utf-8')

    def test_compatible_copy_preserves_original_and_records_exact_change(self):
        from rimsky_training.trainer_source import prepare_trainer
        from rimsky_training.common import tree_hashes
        before = tree_hashes(self.upstream)
        output = self.root / 'compatible'
        result = prepare_trainer(self.upstream, output)
        self.assertTrue(result['text_projection_added'])
        self.assertEqual(before, tree_hashes(self.upstream))
        self.assertEqual((self.upstream / 'LICENSE').read_bytes(), (output / 'LICENSE').read_bytes())
        self.assertEqual((self.upstream / 'finetuning/dataset.py').read_bytes(), (output / 'finetuning/dataset.py').read_bytes())
        report = json_read(output / 'rimsky-trainer-provenance.json')
        self.assertFalse(report['real_training_verified'])
        self.assertIn('rimsky-trainer-provenance.json', upstream_files(output)[1])
        source = self.path.read_text()
        target = (output / 'finetuning/sft_12hz.py').read_text()
        from rimsky_training.trainer_source import BEFORE, AFTER
        self.assertEqual(source.replace(BEFORE, AFTER), target)

    def test_projection_uses_the_output_dimension_then_masks_padding(self):
        from rimsky_training.trainer_source import corrected_projection
        source, _ = corrected_projection(self.path.read_text())
        function = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == 'assemble')
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<prepared-expression>', 'exec'), namespace)
        for text_size, output_size in ((4, 4), (4, 2)):
            with self.subTest(text_size=text_size, output_size=output_size):
                calls = []
                def projection(x):
                    calls.append(x.shape)
                    return x @ np.ones((text_size, output_size)) + 3
                talker = types.SimpleNamespace(model=types.SimpleNamespace(text_embedding=lambda ids: np.ones((1, 2, text_size))), text_projection=projection)
                value = namespace['assemble'](types.SimpleNamespace(talker=talker), [[0, 1]], np.array([[[1], [0]]]), np.zeros((1, 2, output_size)))
                self.assertEqual([(1, 2, text_size)], calls)
                np.testing.assert_array_equal(value[0, 1], np.zeros(output_size))
                np.testing.assert_array_equal(value[0, 0], np.full(output_size, text_size + 3))

    def test_already_projected_source_is_not_double_wrapped(self):
        from rimsky_training.trainer_source import corrected_projection
        first, changed = corrected_projection(self.path.read_text())
        second, changed_twice = corrected_projection(first)
        self.assertTrue(changed)
        self.assertFalse(changed_twice)
        self.assertEqual(first, second)

    def test_unknown_embedding_expression_is_not_rewritten(self):
        from rimsky_training.trainer_source import prepare_trainer
        self.path.write_text(self.path.read_text().replace('text_embedding(input_text_ids)', 'another_embedding(input_text_ids)'))
        with self.assertRaises(AppError):
            prepare_trainer(self.upstream, self.root / 'not-created')
        self.assertFalse((self.root / 'not-created').exists())

    def test_existing_output_and_nested_output_are_not_overwritten(self):
        from rimsky_training.trainer_source import prepare_trainer
        output = self.root / 'existing'
        output.mkdir()
        (output / 'keep').write_text('preserve')
        for destination in (output, self.upstream / 'nested'):
            with self.subTest(destination=str(destination)), self.assertRaises(AppError):
                prepare_trainer(self.upstream, destination)
        self.assertEqual('preserve', (output / 'keep').read_text())

    def test_prepared_trainer_source_change_invalidates_its_provenance(self):
        from rimsky_training.trainer_source import prepare_trainer
        output = self.root / 'compatible'
        prepare_trainer(self.upstream, output)
        (output / 'finetuning/dataset.py').write_text('changed')
        with self.assertRaises(AppError):
            upstream_files(output)

    def test_dataset_report_counts_decoded_hours_without_loading_models(self):
        from rimsky_training.reporting import dataset_report
        dataset, saved = self.fixture.dataset(name='report-data')
        result = dataset_report(dataset)
        self.assertEqual(2, result['partitions']['train']['clips'])
        self.assertEqual(2, result['partitions']['validation']['clips'])
        self.assertEqual(1, result['partitions']['train']['books'])
        self.assertAlmostEqual(saved['training_seconds'] / 3600, result['partitions']['train']['decoded_hours'], places=6)
        self.assertFalse(result['instruction_control_trained'])
        self.assertNotIn('quality_score', result)

    def test_recording_is_decoded_once_then_all_clips_are_cut_from_pcm(self):
        from rimsky_training.dataset import decoded_recording, cut_audio
        full_decodes, clip_sources = [], []
        def decode(*args, **kwargs):
            full_decodes.append(str(args[0]))
            return decoded_recording(*args, **kwargs)
        def cut(source, *args, **kwargs):
            clip_sources.append(str(source))
            self.assertEqual('recording.wav', source.name)
            return cut_audio(source, *args, **kwargs)
        with patch('rimsky_training.dataset.decoded_recording', side_effect=decode), patch('rimsky_training.dataset.cut_audio', side_effect=cut):
            dataset, _ = self.fixture.dataset(name='single-decode')
        self.assertEqual(2, len(full_decodes))
        self.assertEqual(4, len(clip_sources))
        self.assertEqual(2, len(set(clip_sources)))
        self.assertFalse(any(dataset.glob('.decoded-recording-*')))

    def test_compressed_recording_cuts_match_full_decode_reference(self):
        from rimsky_training.dataset import decoded_recording
        from rimsky.references import inspect_reference
        wav, mp3 = self.root / 'original.wav', self.root / 'original.mp3'
        fixtures.write_tone(wav, seconds=15)
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(wav), str(mp3)], check=True)
        expected = self.root / 'expected.wav'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(mp3),'-ss','8.123','-t','2.5','-ac','1','-ar','24000','-c:a','pcm_s16le',str(expected)],check=True)
        result = self.root / 'cut.wav'
        with decoded_recording(mp3, inspect_reference(mp3)['duration'], self.root, 'ffmpeg') as pcm:
            cut_audio(pcm, result, 8.123, 10.623, 'ffmpeg')
        aa, _ = sf.read(expected, dtype='int16')
        bb, _ = sf.read(result, dtype='int16')
        np.testing.assert_array_equal(aa, bb)
        self.assertFalse(any(self.root.glob('.decoded-recording-*')))

    def test_checkpoint_rejects_wrong_speaker_token(self):
        model = fixtures.fake_model(self.root / 'checkpoint', 'custom_voice')
        config = json_read(model / 'config.json')
        for value in (1000000, -1, True, 2999):
            config['talker_config']['spk_id']['reader'] = value
            write_json(model / 'config.json', config)
            with self.subTest(value=value), self.assertRaises(AppError):
                checkpoint_info(model, 'reader')

    @unittest.skipIf(os.name == 'nt', 'This execution checks the POSIX process group only.')
    def test_successful_training_leader_terminates_signal_ignoring_child(self):
        ready, result = self.root / 'ready', self.root / 'leaked'
        child = f'import signal,pathlib,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);pathlib.Path({str(ready)!r}).write_text("ready");time.sleep(1);pathlib.Path({str(result)!r}).write_text("leaked")'
        leader = f'import subprocess,time,pathlib,sys;p=subprocess.Popen([sys.executable,"-c",{child!r}]);\nwhile not pathlib.Path({str(ready)!r}).exists(): time.sleep(.01)\n'
        run_process([sys.executable, '-c', leader], self.root, self.root / 'training.log', 10)
        self.assertTrue(ready.exists())
        time.sleep(1.1)
        self.assertFalse(result.exists())


class TrainingConditioning(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TrainingPipeline(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_evaluation_records_the_trained_auto_prefix_explicitly(self):
        output, calls = self.fixture.evaluation()
        result = json_read(output / 'evaluation.json')
        self.assertEqual('Russian', result['baseline_language'])
        self.assertEqual('Auto', result['candidate_language'])
        for path, arguments in calls:
            self.assertEqual('Auto' if 'checkpoint-epoch' in path else 'Russian', arguments['language'])

    def test_new_pack_keeps_evaluated_conditioning_and_refuses_unknown_modes(self):
        from rimsky_training.evaluation import promote_checkpoint, REVIEW_GATES
        from rimsky.packs import load_pack
        output, _ = self.fixture.evaluation()
        evaluation = json_read(output / 'evaluation.json')
        review = self.fixture.root / 'accepted-review.json'
        write_json(review, {'schema': 1, 'accepted': True, 'evaluation_revision': evaluation['revision'],
                   'reviewer': 'controlled fixture', 'notes': 'Synthetic flow, not acoustic approval', **{gate: True for gate in REVIEW_GATES}})
        text = self.fixture.root / 'text.gguf'; text.write_bytes(b'GGUF-controlled')
        license = self.fixture.root / 'text-license'; license.write_text('Generated fixture')
        out = self.fixture.root / 'new-pack'
        promote_checkpoint(output, review, text, license, out)
        pack = load_pack(str(out), verify=True)
        self.assertEqual('auto', pack['speech']['language_mode'])
        self.assertEqual('auto', pack['training']['language_conditioning'])
        value = json_read(out / 'pack.json')
        value['speech']['language_mode'] = 'unknown'
        write_json(out / 'pack.json', value)
        with self.assertRaises(AppError):
            load_pack(str(out), verify=True)

    def test_worker_preserves_old_language_mode_and_uses_new_pack_auto(self):
        spec = importlib.util.spec_from_file_location('controlled_worker_conditioning', ROOT / 'runtime/worker.py')
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        calls = []
        def generate(**kwargs):
            calls.append(kwargs)
            return [np.sin(np.arange(24000) / 23) * .1], 24000
        model = types.SimpleNamespace(get_supported_speakers=lambda: ['reader'], generate_custom_voice=generate)
        qwen = types.SimpleNamespace(Qwen3TTSModel=types.SimpleNamespace(from_pretrained=lambda *a, **k: model))
        pack = {'_root': str(self.fixture.root), '_fingerprint': 'fixture', 'speech': {'path': '.', 'variant': 'custom'},
                'voices': [{'id': 'narrator', 'speaker': 'reader'}]}
        with patch.dict(sys.modules, {'qwen_tts': qwen}), patch.object(worker, 'device_config', return_value=('cpu', 'float32')):
            for mode in (None, 'book', 'auto'):
                if mode is not None:
                    pack['speech']['language_mode'] = mode
                result = worker.render({'pack': pack, 'text': 'Проверка.', 'voice_id': 'narrator',
                                       'delivery': 'neutral', 'language': 'Russian', 'device': 'cpu',
                                       'output': str(self.fixture.root / f'{mode}.wav')})
                self.assertEqual(24000, result['samples'])
        self.assertEqual(['Russian', 'Russian', 'Auto'], [entry['language'] for entry in calls])

    def test_worker_rejects_speech_at_the_12_5_hz_generation_cap(self):
        spec = importlib.util.spec_from_file_location('controlled_worker_cap', ROOT / 'runtime/worker.py')
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        samples = []
        def generate(**kwargs):
            return [np.sin(np.arange(samples[-1]) / 23) * .1], 24000
        model = types.SimpleNamespace(get_supported_speakers=lambda: ['reader'], generate_custom_voice=generate)
        qwen = types.SimpleNamespace(Qwen3TTSModel=types.SimpleNamespace(from_pretrained=lambda *a, **k: model))
        pack = {'_root': str(self.fixture.root), '_fingerprint': 'fixture', 'speech': {'path': '.', 'variant': 'custom', 'max_new_tokens': 128},
                'voices': [{'id': 'narrator', 'speaker': 'reader'}]}
        request = {'pack': pack, 'text': 'Проверка.', 'voice_id': 'narrator', 'delivery': 'neutral', 'language': 'Russian', 'device': 'cpu'}
        with patch.dict(sys.modules, {'qwen_tts': qwen}), patch.object(worker, 'device_config', return_value=('cpu', 'float32')):
            samples.append(128 * 1920)
            with self.assertRaisesRegex(AppError, 'generation cap'):
                worker.render({**request, 'output': str(self.fixture.root / 'capped.wav')})
            samples.append(128 * 1920 * 9 // 10)
            self.assertEqual(samples[-1], worker.render({**request, 'output': str(self.fixture.root / 'shorter.wav')})['samples'])

    def test_checkpoint_rejects_missing_or_wrong_dialect_flag(self):
        model = fixtures.fake_model(self.fixture.root / 'dialect-checkpoint', 'custom_voice')
        value = json_read(model / 'config.json')
        for flag in (None, True, 'russian'):
            value['talker_config']['spk_is_dialect'] = {'reader': flag}
            write_json(model / 'config.json', value)
            with self.subTest(flag=flag), self.assertRaises(AppError):
                checkpoint_info(model, 'reader')


if __name__ == '__main__':
    unittest.main()
