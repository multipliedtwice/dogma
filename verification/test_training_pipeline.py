from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'training')]

from rimsky.packs import load_pack
from rimsky.util import AppError, digest, file_digest, write_json
from rimsky_training.common import checked_manifest, json_read, rows_read, rows_write
from rimsky_training.corpus import create_corpus, load_corpus, select_chapter
from rimsky_training.dataset import build_dataset, checked_rows
from rimsky_training.evaluation import REVIEW_GATES, promote_checkpoint, render_evaluation
from rimsky_training.inference import encode_dataset, local_model, repair_zero_duration_words, transcribe, validate_codes
from rimsky_training.pairing import propose_pairs, tokens, validate_transcript
from rimsky_training.runner import (FLAGS, completed_checkpoint, create_plan, execute_training,
                                    load_plan, run_process, upstream_files)


def write_tone(path, seconds=15, frequency=150):
    t = np.arange(24000 * seconds) / 24000
    sf.write(path, .1 * np.sin(2 * np.pi * (frequency * t + 9 * t * t)), 24000, subtype='PCM_16')


def fake_tokenizer(path):
    from safetensors.numpy import save_file
    path.mkdir()
    write_json(path / 'config.json', {'model_type': 'qwen3_tts_tokenizer_12hz',
               'encoder_valid_num_quantizers': 16, 'input_sample_rate': 24000, 'output_sample_rate': 24000,
               'encoder_config': {'codebook_size': 2048}})
    save_file({'encoder.fixture': np.ones((2, 2), dtype=np.float32)}, str(path / 'model.safetensors'))
    return path


def fake_model(path, kind='base'):
    from safetensors.numpy import save_file
    path.mkdir()
    write_json(path / 'config.json', {'model_type': 'qwen3_tts', 'tokenizer_type': 'qwen3_tts_tokenizer_12hz',
               'tts_model_size': '1b7', 'tts_model_type': kind,
               'talker_config': {'spk_id': {'reader': 3000}, 'spk_is_dialect': {'reader': False}, 'num_code_groups': 16,
                                 'code_predictor_config': {'vocab_size': 2048}}})
    value = 1. if kind == 'base' else 2.
    save_file({'talker.model.codec_embedding.weight': np.full((2, 2), value, dtype=np.float32),
               'talker.model.layers.0.fixture': np.full((2, 2), value, dtype=np.float32)}, str(path / 'model.safetensors'))
    fake_tokenizer(path / 'speech_tokenizer')
    return path


class TrainingPipeline(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        entries = []
        self.sentences = [
            ['Спокойный рассказчик читает длинную первую фразу.', 'Затем путешественник открывает старую дверь комнаты.'],
            ['Новый герой рассматривает далекие берега реки.', 'Вечерний ветер приносит приятный запах леса.']]
        for i in range(2):
            name = f'book-{i}'
            book = self.root / f'{name}.fb2'
            paragraphs = ''.join(f'<p>{text}</p>' for text in self.sentences[i])
            book.write_text(f'<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info><book-title>{name}</book-title><lang>ru</lang></title-info></description><body><section><title><p>Глава {i}</p></title>{paragraphs}</section></body></FictionBook>', encoding='utf-8')
            audio = self.root / f'{name}.wav'
            write_tone(audio, frequency=150 + i * 100)
            entries.append({'id': name, 'path': str(book), 'recordings': [{'id': f'track-{i}', 'path': str(audio)}]})
        self.recipe = {'schema': 1, 'narrator': {'id': 'reader', 'name': 'Synthetic verification fixture'},
                       'rights': {'training_authorized': True, 'basis': 'Generated verification material; no human voice', 'redistribution_authorized': False},
                       'books': entries}
        self.recipe_path = self.root / 'recipe.json'
        write_json(self.recipe_path, self.recipe)
        self.corpus_path = self.root / 'corpus'
        create_corpus(self.recipe_path, self.corpus_path)
        self.corpus_root, self.corpus = load_corpus(self.corpus_path)
        self.review_paths = []
        self.rows = []
        for i in range(2):
            book_id = f'book-{i}'
            entry = next(item for item in self.corpus['books'] if item['id'] == book_id)
            book = json_read(self.corpus_root / entry['manifest'])
            chapter = next(item for item in book['chapters'] if any(block['text'] == self.sentences[i][0] for block in item['blocks']))
            words = []
            for j, sentence in enumerate(self.sentences[i]):
                for index, (word, _, _) in enumerate(tokens(sentence)):
                    start = 1 + j * 6 + index * .5
                    words.append({'text': word, 'start': start, 'end': start + .45})
            transcript = self.root / f'transcript-{i}.json'
            write_json(transcript, {'schema': 1, 'recording_sha256': self.corpus['recordings'][i]['sha256'], 'words': words})
            proposals = self.root / f'paired-{i}'
            propose_pairs(self.corpus_path, f'track-{i}', chapter['id'], transcript, proposals)
            rows = rows_read(proposals / 'review.jsonl')
            self.assertEqual(2, len(rows))
            for row in rows:
                row.update(performance='narration', delivery='neutral')
                row['review'] = {'accepted': True, 'reviewer': 'controlled-fixture', 'text_checked': True,
                                 'boundaries_checked': True, 'speaker_checked': True, 'clean_speech': True}
            reviews = self.root / f'reviewed-{i}.jsonl'
            rows_write(reviews, rows)
            self.rows.extend(rows)
            self.review_paths.append(reviews)

    def dataset(self, name='dataset', **kwargs):
        destination = self.root / name
        args = {'reference_id': self.rows[0]['id'], 'performance': 'narration', 'heldout_books': ['book-1']}
        args.update(kwargs)
        result = build_dataset(self.corpus_path, self.review_paths, destination, **args)
        return destination, result

    def codec_module(self, fail_at=None):
        calls = []
        loads = []
        def encode(paths):
            calls.append(paths)
            if len(calls) == fail_at:
                raise AppError('Controlled codec interruption')
            code = types.SimpleNamespace(cpu=lambda: types.SimpleNamespace(tolist=lambda: [[1] * 16] * 24))
            return types.SimpleNamespace(audio_codes=[code])
        def load(*args, **kwargs):
            loads.append((args, kwargs))
            return types.SimpleNamespace(encode=encode)
        module = types.SimpleNamespace(Qwen3TTSTokenizer=types.SimpleNamespace(from_pretrained=load))
        return module, calls, loads

    def encoded(self):
        dataset, _ = self.dataset()
        tokenizer = fake_tokenizer(self.root / 'tokenizer')
        module, _, _ = self.codec_module()
        encoded = self.root / 'encoded'
        with patch.dict(sys.modules, {'qwen_tts': module}):
            encode_dataset(dataset, tokenizer, encoded, device='cpu')
        return dataset, encoded

    def plan(self):
        dataset, encoded = self.encoded()
        upstream = self.root / 'upstream'
        (upstream / 'finetuning').mkdir(parents=True)
        trainer = 'import argparse\np=argparse.ArgumentParser()\n' + ''.join(f'p.add_argument({flag!r})\n' for flag in sorted(FLAGS))
        (upstream / 'finetuning' / 'sft_12hz.py').write_text(trainer)
        (upstream / 'finetuning' / 'dataset.py').write_text('class TTSDataset: pass\n')
        (upstream / 'LICENSE').write_text('Synthetic trainer interface fixture, not upstream code')
        base = fake_model(self.root / 'base')
        run = self.root / 'run'
        create_plan(dataset, encoded, base, upstream, run, epochs=1)
        return run, base, upstream

    def completed_run(self):
        run, base, upstream = self.plan()
        def fake_execute(command, cwd, log, timeout):
            (run / 'checkpoints').mkdir()
            fake_model(run / 'checkpoints' / 'checkpoint-epoch-0', 'custom_voice')
            log.write_text('Controlled process output, no optimization performed')
        with patch('rimsky_training.runner.training_preflight', return_value={'controlled': True}), patch('rimsky_training.runner.run_process', side_effect=fake_execute):
            execute_training(run)
        return run, run / 'checkpoints' / 'checkpoint-epoch-0'

    def evaluation(self):
        run, checkpoint = self.completed_run()
        calls = []
        def load(path, **kwargs):
            def render(**data):
                calls.append((path, data))
                samples = np.sin(np.arange(48000) / (31. if path == str(checkpoint) else 37.)) * .1
                return [samples], 24000
            return types.SimpleNamespace(create_voice_clone_prompt=lambda **kwargs: 'controlled-prompt',
                                         generate_voice_clone=render, generate_custom_voice=render)
        torch = types.SimpleNamespace(float32='float32', float16='float16', bfloat16='bfloat16',
                                       manual_seed=lambda value: None)
        qwen = types.SimpleNamespace(Qwen3TTSModel=types.SimpleNamespace(from_pretrained=load))
        output = self.root / 'evaluation'
        with patch.dict(sys.modules, {'torch': torch, 'qwen_tts': qwen}):
            render_evaluation(run, checkpoint, output, count=2)
        return output, calls

    def test_corpus_preserves_source_and_records_multiple_books_for_one_narrator(self):
        self.assertEqual(2, len(self.corpus['books']))
        self.assertEqual(2, len(self.corpus['recordings']))
        for entry in self.corpus['recordings']:
            self.assertEqual(file_digest(self.corpus_root / entry['path']), entry['sha256'])
        self.assertEqual('reader', self.corpus['narrator']['id'])

    def test_no_implicit_training_authorization_or_overwrite(self):
        recipe = copy.deepcopy(self.recipe)
        recipe['rights']['training_authorized'] = False
        write_json(self.recipe_path, recipe)
        with self.assertRaises(AppError):
            create_corpus(self.recipe_path, self.root / 'refused')
        self.assertFalse((self.root / 'refused').exists())
        write_json(self.recipe_path, self.recipe)
        with self.assertRaises(AppError):
            create_corpus(self.recipe_path, self.corpus_path)

    def test_modified_corpus_audio_is_refused(self):
        path = self.corpus_root / self.corpus['recordings'][0]['path']
        path.write_bytes(b'changed')
        with self.assertRaises(AppError):
            load_corpus(self.corpus_path)

    def test_alignment_proposals_are_never_automatically_approved(self):
        row = rows_read(self.root / 'paired-0/review.jsonl')[0]
        self.assertIs(False, row['review']['accepted'])
        self.assertEqual('unreviewed', row['performance'])
        self.assertTrue(json_read(self.root / 'paired-0/pairing-report.json')['unmatched_sources'])

    def test_one_asr_word_error_keeps_a_phrase_as_unreviewed_proposal(self):
        document = json_read(self.root / 'transcript-0.json')
        document['words'][2]['text'] = 'ошибка'
        transcript = self.root / 'transcript-imperfect.json'
        write_json(transcript, document)
        output = self.root / 'paired-imperfect'
        propose_pairs(self.corpus_path, 'track-0', self.rows[0]['chapter_id'], transcript, output)
        proposals = rows_read(output / 'review.jsonl')
        candidate = next(row for row in proposals if row['source_id'] == self.rows[0]['source_id'])
        self.assertEqual('phrase', candidate['alignment_kind'])
        self.assertLess(candidate['asr_anchor_ratio'], 1)
        self.assertFalse(candidate['review']['accepted'])
        self.assertIn('ошибка', candidate['asr_text'])

    def test_zero_length_word_can_use_a_gap_before_the_next_word(self):
        words = [{'text': 'в', 'start': 1., 'end': 1.},
                 {'text': 'комнате', 'start': 1.12, 'end': 1.6}]
        self.assertEqual(1, repair_zero_duration_words(words))
        self.assertEqual(1.12, words[0]['end'])
        self.assertEqual(1.12, words[1]['start'])

    def test_transcript_audio_mismatch_and_invalid_times_fail(self):
        recording = self.corpus['recordings'][0]
        good = json_read(self.root / 'transcript-0.json')
        for change in ('hash', 'nan', 'overlap'):
            document = copy.deepcopy(good)
            if change == 'hash': document['recording_sha256'] = '0' * 64
            elif change == 'nan': document['words'][0]['end'] = float('nan')
            else: document['words'][1]['start'] = 0
            with self.assertRaises(AppError):
                validate_transcript(document, recording)

    def test_unreviewed_or_partially_reviewed_clips_do_not_train(self):
        rows = copy.deepcopy(self.rows)
        for row in rows: row['review']['accepted'] = False
        self.assertEqual([], checked_rows(self.corpus_root, self.corpus, rows))
        rows[0]['review']['accepted'] = True
        rows[0]['review']['boundaries_checked'] = False
        with self.assertRaises(AppError):
            checked_rows(self.corpus_root, self.corpus, rows)

    def test_wrong_narrator_or_source_text_is_rejected(self):
        for field, value in [('narrator_id', 'another-reader'), ('source_text', 'Invented words'), ('text_end', 999999)]:
            rows = copy.deepcopy(self.rows)
            rows[0][field] = value
            with self.assertRaises(AppError):
                checked_rows(self.corpus_root, self.corpus, rows)

    def test_website_watermark_cannot_be_accepted_as_training_text(self):
        rows = copy.deepcopy(self.rows[:1])
        _, chapter = select_chapter(self.corpus_root, self.corpus,
                                    rows[0]['book_id'], rows[0]['chapter_id'])
        chapter = copy.deepcopy(chapter)
        source = next(block for block in chapter['blocks']
                      if block['id'] == rows[0]['source_id'])
        source['text'] = 'Book watermark: Knigolub.net'
        with patch('rimsky_training.dataset.select_chapter', return_value=(None, chapter)):
            with self.assertRaisesRegex(AppError, 'Website watermark'):
                checked_rows(self.corpus_root, self.corpus, rows)

    def test_adaptation_requires_explicit_spoken_text_exception(self):
        rows = copy.deepcopy(self.rows)
        rows[0]['spoken_text'] = 'Другие произнесенные слова'
        with self.assertRaises(AppError):
            checked_rows(self.corpus_root, self.corpus, rows)
        rows[0]['text_exception'] = 'Controlled fixture representing a reviewed spoken adaptation'
        self.assertEqual(rows[0]['spoken_text'], checked_rows(self.corpus_root, self.corpus, rows)[0]['spoken_text'])

    def test_overlap_and_nonfinite_clip_bounds_are_rejected(self):
        rows = copy.deepcopy(self.rows)
        rows[1]['audio_start'] = rows[0]['audio_start']
        with self.assertRaises(AppError):
            checked_rows(self.corpus_root, self.corpus, rows)
        rows = copy.deepcopy(self.rows)
        rows[0]['audio_start'] = float('nan')
        with self.assertRaises(AppError):
            checked_rows(self.corpus_root, self.corpus, rows)

    def test_dataset_has_constant_train_only_reference_and_real_24khz_clips(self):
        path, report = self.dataset()
        self.assertEqual((2, 2), (report['training_clips'], report['validation_clips']))
        train, validation = rows_read(path / 'train.raw.jsonl'), rows_read(path / 'validation.raw.jsonl')
        self.assertEqual(1, len({row['ref_audio'] for row in train + validation}))
        self.assertIn(train[0]['ref_audio'], {row['audio'] for row in train})
        self.assertNotIn(train[0]['ref_audio'], {row['audio'] for row in validation})
        for row in train + validation:
            info = sf.info(path / row['audio'])
            self.assertEqual((24000, 1, 'PCM_16'), (info.samplerate, info.channels, info.subtype))
        checked_manifest(path, 'dataset.json')

    def test_heldout_reference_is_refused(self):
        with self.assertRaises(AppError):
            self.dataset(reference_id=self.rows[-1]['id'])
        self.assertFalse((self.root / 'dataset').exists())

    def test_empty_training_or_validation_group_is_refused(self):
        with self.assertRaises(AppError):
            self.dataset(heldout_books=['book-0', 'book-1'])
        with self.assertRaises(AppError):
            self.dataset(heldout_books=[])

    def test_failed_clip_encoding_publishes_no_dataset(self):
        with patch('rimsky_training.dataset.cut_audio', side_effect=AppError('fixture encoding error')):
            with self.assertRaises(AppError):
                self.dataset()
        self.assertFalse((self.root / 'dataset').exists())

    def test_codec_resume_reuses_committed_row_without_reencoding(self):
        dataset, _ = self.dataset()
        tokenizer = fake_tokenizer(self.root / 'tokenizer')
        output = self.root / 'encoded'
        failing, first_calls, _ = self.codec_module(fail_at=2)
        with patch.dict(sys.modules, {'qwen_tts': failing}):
            with self.assertRaises(AppError):
                encode_dataset(dataset, tokenizer, output, device='cpu')
        self.assertEqual('failed', json_read(output / 'stage.json')['status'])
        working, calls, loads = self.codec_module()
        with patch.dict(sys.modules, {'qwen_tts': working}):
            result = encode_dataset(dataset, tokenizer, output, device='cpu', resume=True)
        self.assertEqual(2, result['rows'])
        self.assertEqual(1, len(calls))
        self.assertTrue(loads[0][1]['local_files_only'])
        self.assertEqual('complete', json_read(output / 'stage.json')['status'])

    def test_encoded_dataset_tampering_is_refused(self):
        run, _, _ = self.plan()
        plan = json_read(run / 'plan.json')
        encoded = Path(plan['encoded_path']) / 'train.codes.jsonl'
        encoded.write_text('{}\n')
        with self.assertRaises(AppError):
            load_plan(run)

    def test_training_plan_does_not_launch_or_load_models(self):
        with patch('rimsky_training.runner.training_preflight') as preflight, patch('rimsky_training.runner.run_process') as execute:
            run, _, _ = self.plan()
        preflight.assert_not_called()
        execute.assert_not_called()
        plan = json_read(run / 'plan.json')
        self.assertEqual('reader', plan['speaker'])
        self.assertIn('--train_jsonl', plan['command'])
        self.assertFalse(plan['optimizer_resume_supported'])
        self.assertFalse(plan['instruction_control_trained'])
        self.assertEqual('planned', json_read(run / 'run.json')['status'])

    def test_changed_upstream_source_cannot_execute(self):
        run, _, upstream = self.plan()
        with (upstream / 'finetuning/sft_12hz.py').open('a') as handle:
            handle.write('\nprint("changed")\n')
        with patch('rimsky_training.runner.run_process') as execute:
            with self.assertRaises(AppError):
                execute_training(run)
        execute.assert_not_called()

    def test_missing_training_hardware_leaves_plan_unstarted(self):
        run, _, _ = self.plan()
        with patch('rimsky_training.runner.training_preflight', side_effect=AppError('No CUDA')):
            with self.assertRaises(AppError):
                execute_training(run)
        self.assertEqual('planned', json_read(run / 'run.json')['status'])
        self.assertFalse((run / 'checkpoints').exists())

    def test_failed_optimizer_is_not_resumed_or_promoted(self):
        run, _, _ = self.plan()
        with patch('rimsky_training.runner.training_preflight', return_value={}), patch('rimsky_training.runner.run_process', side_effect=AppError('out of memory')):
            with self.assertRaises(AppError):
                execute_training(run)
        self.assertEqual('failed', json_read(run / 'run.json')['status'])
        with self.assertRaises(AppError):
            execute_training(run)

    def test_local_process_receives_offline_environment_and_timeout_kills_it(self):
        log = self.root / 'process.log'
        run_process([sys.executable, '-c', 'import os;print(os.environ["HF_HUB_OFFLINE"])'], self.root, log, 10)
        self.assertEqual('1', log.read_text().strip())
        with self.assertRaises(AppError):
            run_process([sys.executable, '-c', 'import time;time.sleep(30)'], self.root, log, .2)

    def test_evaluation_is_heldout_and_does_not_claim_instruction_control(self):
        output, calls = self.evaluation()
        manifest = checked_manifest(output, 'evaluation.json')
        self.assertEqual(2, len(manifest['samples']))
        self.assertFalse(manifest['quality_reviewed'])
        self.assertTrue(all('instruct' not in call[1] for call in calls))
        self.assertTrue(all(sample['text'] in self.sentences[1] for sample in manifest['samples']))
        self.assertTrue(all((output / sample['target']).is_file() for sample in manifest['samples']))

    def test_publish_requires_explicit_review_and_preserves_player_model_modes(self):
        output, _ = self.evaluation()
        evaluation = json_read(output / 'evaluation.json')
        review_path = self.root / 'review.json'
        review = {'schema': 1, 'evaluation_revision': evaluation['revision'], 'accepted': False,
                  'reviewer': 'controlled fixture', **{gate: True for gate in REVIEW_GATES},
                  'notes': 'Control-flow fixture only, not a trained narrator acceptance'}
        write_json(review_path, review)
        text = self.root / 'text.gguf'; text.write_bytes(b'GGUF-controlled-format-fixture')
        license = self.root / 'text-license.txt'; license.write_text('Generated fixture')
        pack = self.root / 'published'
        with self.assertRaises(AppError):
            promote_checkpoint(output, review_path, text, license, pack)
        self.assertFalse(pack.exists())
        review['accepted'] = True
        write_json(review_path, review)
        result = promote_checkpoint(output, review_path, text, license, pack)
        loaded = load_pack(str(pack), verify=True)
        self.assertFalse(loaded['speech']['instruction_control'])
        self.assertEqual('custom', loaded['speech']['variant'])
        self.assertEqual({'reader'}, {voice['speaker'] for voice in loaded['voices']})
        self.assertEqual(1, result['distinct_baseline_performances'])
        self.assertFalse(result['redistribution_authorized'])
        self.assertFalse((pack / 'clips').exists())

    def test_changed_evaluation_audio_cannot_be_approved(self):
        output, _ = self.evaluation()
        evaluation = json_read(output / 'evaluation.json')
        (output / evaluation['samples'][0]['candidate']).write_bytes(b'replaced')
        with self.assertRaises(AppError):
            checked_manifest(output, 'evaluation.json')

    def test_model_hub_identifiers_and_invalid_code_frames_are_refused(self):
        with self.assertRaises(AppError):
            local_model(Path('Qwen/Qwen3-TTS-12Hz-1.7B-Base'))
        original = {'audio': '/local/a.wav', 'text': 'a', 'ref_audio': '/local/r.wav'}
        for code in ([[True] * 16], [[-1] * 16], [[1] * 15]):
            with self.assertRaises(AppError):
                validate_codes({**original, 'audio_codes': code}, original)

    def test_training_cli_requires_explicit_execute_flag(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/train_narrator.py'), 'train', '--run', str(self.root)],
                                capture_output=True, timeout=15)
        self.assertNotEqual(0, result.returncode)
        self.assertIn(b'--execute', result.stderr)


    def test_known_different_narrator_in_recording_recipe_is_refused(self):
        recipe = copy.deepcopy(self.recipe)
        recipe['books'][0]['recordings'][0]['narrator_id'] = 'another-narrator'
        write_json(self.recipe_path, recipe)
        with self.assertRaises(AppError):
            create_corpus(self.recipe_path, self.root / 'mixed-corpus')
        self.assertFalse((self.root / 'mixed-corpus').exists())

    def test_heldout_chapter_selection_is_explicit_and_whole(self):
        chapters = [f'{row["book_id"]}:{row["chapter_id"]}' for row in self.rows if row['book_id'] == 'book-1']
        path, result = self.dataset(heldout_books=[], heldout_chapters=[chapters[0]])
        self.assertEqual(2, result['validation_clips'])
        self.assertEqual([chapters[0]], result['heldout_chapters'])

    def test_source_changed_during_clip_extraction_prevents_dataset_publication(self):
        from rimsky_training.dataset import cut_audio
        count = 0
        def cut(*args, **kwargs):
            nonlocal count
            result = cut_audio(*args, **kwargs)
            count += 1
            if count == 4:
                path = self.corpus_root / self.corpus['recordings'][0]['path']
                with path.open('ab') as handle:
                    handle.write(b'changed')
            return result
        with patch('rimsky_training.dataset.cut_audio', side_effect=cut):
            with self.assertRaises(AppError):
                self.dataset()
        self.assertFalse((self.root / 'dataset').exists())

    def test_asr_adapter_uses_local_models_and_resumes_saved_windows(self):
        asr, aligner = fake_model(self.root / 'asr'), fake_model(self.root / 'aligner')
        calls, loads = [], []
        document = json_read(self.root / 'transcript-0.json')
        stamps = [types.SimpleNamespace(text=word['text'], start_time=word['start'], end_time=word['end']) for word in document['words']]
        def transcribe_fake(**kwargs):
            calls.append(kwargs)
            return [types.SimpleNamespace(text=' '.join(word['text'] for word in document['words']), time_stamps=stamps)]
        def load(*args, **kwargs):
            loads.append((args, kwargs))
            return types.SimpleNamespace(transcribe=transcribe_fake)
        qwen = types.SimpleNamespace(Qwen3ASRModel=types.SimpleNamespace(from_pretrained=load))
        torch = types.SimpleNamespace(float32='float32', float16='float16')
        out = self.root / 'asr-result'
        with patch.dict(sys.modules, {'qwen_asr': qwen, 'torch': torch}):
            result = transcribe(self.corpus_path, 'track-0', asr, aligner, out)
            replay = transcribe(self.corpus_path, 'track-0', asr, aligner, out, resume=True)
        self.assertEqual(1, len(calls))
        self.assertEqual(1, len(loads))
        self.assertTrue(loads[0][1]['local_files_only'])
        self.assertTrue(loads[0][1]['forced_aligner_kwargs']['local_files_only'])
        self.assertTrue(calls[0]['return_time_stamps'])
        self.assertNotIn('context', calls[0])
        self.assertEqual(result, replay)
        self.assertFalse(json_read(out / 'transcript.json')['reviewed'])

    def test_asr_missing_timing_fails_without_publishing_transcript(self):
        asr, aligner = fake_model(self.root / 'asr'), fake_model(self.root / 'aligner')
        model = types.SimpleNamespace(transcribe=lambda **kwargs: [types.SimpleNamespace(text='Recognized but unaligned', time_stamps=None)])
        qwen = types.SimpleNamespace(Qwen3ASRModel=types.SimpleNamespace(from_pretrained=lambda *args, **kwargs: model))
        torch = types.SimpleNamespace(float32='float32', float16='float16')
        output = self.root / 'asr-failed'
        with patch.dict(sys.modules, {'qwen_asr': qwen, 'torch': torch}):
            with self.assertRaises(AppError):
                transcribe(self.corpus_path, 'track-0', asr, aligner, output)
        self.assertEqual('failed', json_read(output / 'stage.json')['status'])
        self.assertFalse((output / 'transcript.json').exists())

    def test_release_guard_does_not_distribute_private_trained_packs(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('training_release_guard', ROOT / 'scripts/check_release.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        evaluation, _ = self.evaluation()
        value = json_read(evaluation / 'evaluation.json')
        review = self.root / 'approved-review.json'
        write_json(review, {'schema': 1, 'evaluation_revision': value['revision'], 'accepted': True,
                            'reviewer': 'controlled-fixture', 'notes': 'Structure check, not a human voice',
                            **{gate: True for gate in REVIEW_GATES}})
        text = self.root / 'text.gguf'; text.write_bytes(b'GGUF-controlled fixture')
        licence = self.root / 'text-licence'; licence.write_text('fixture')
        release = self.root / 'release'
        pack = release / 'resources' / 'packs' / 'candidate'
        promote_checkpoint(evaluation, review, text, licence, pack)
        issues = module.release_issues(release, require_acceptance=False)
        self.assertTrue(any('redistribution authorization' in issue for issue in issues))


if __name__ == '__main__':
    unittest.main()
