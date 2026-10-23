from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'training')]

from rimsky.util import AppError, digest, file_digest, write_json
from rimsky_training import common, inference, pairing, runner
import test_training_pipeline as fixtures


class TrainingAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self):
        fixture = fixtures.TrainingPipeline(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_fresh_stage_cannot_take_over_a_directory_created_during_admission(self):
        output = self.root / 'stage'
        rival = {'schema': 1, 'contract': {'owner': 'other'}, 'status': 'complete'}
        mkdir = Path.mkdir
        injected = False
        def race(path, *args, **kwargs):
            nonlocal injected
            if path == output and not injected:
                injected = True
                mkdir(output)
                write_json(output / 'stage.json', rival)
                (output / 'keep.txt').write_text('existing result')
            return mkdir(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', race):
            with self.assertRaises(AppError):
                with common.stage_workspace(output, {'owner': 'new'}):
                    self.fail('Competing output must not be admitted.')
        self.assertEqual(rival, common.json_read(output / 'stage.json'))
        self.assertEqual('existing result', (output / 'keep.txt').read_text())

    def test_explicit_stage_resume_still_preserves_compatible_results(self):
        output = self.root / 'stage'
        contract = {'owner': 'same'}
        with common.stage_workspace(output, contract):
            (output / 'keep.txt').write_text('accepted result')
        with common.stage_workspace(output, contract, resume=True):
            self.assertEqual('accepted result', (output / 'keep.txt').read_text())
        with self.assertRaises(AppError):
            with common.stage_workspace(output, {'owner': 'other'}, resume=True):
                pass
        self.assertEqual('complete', common.json_read(output / 'stage.json')['status'])

    def test_hyphenated_russian_and_apostrophe_tokens_match_aligner_units(self):
        for source, aligned in [('«Кто-то» сказал: «Из-за двери!»', 'Ктото сказал Изза двери'),
                                ("Don't leave", "Don't leave"), ('Сине-зелёный луч', 'Синезелёный луч')]:
            self.assertEqual([entry[0] for entry in pairing.tokens(source)],
                             [entry[0] for entry in pairing.tokens(aligned)])
        self.assertEqual([('ктото', 1, 7)], pairing.tokens('«Кто-то»'))
        self.assertEqual([], pairing.tokens('— … ***'))

    def test_transcript_comparison_treats_hyphen_and_space_compounds_alike(self):
        words = lambda text: [entry[0] for entry in pairing.compound_tokens(text)]
        for heard in ('колонны ноги шли', 'Колонны-ноги шли', 'колонны‑ноги шли'):
            self.assertEqual(words('Колонны-ноги шли.'), words(heard))
        text = '«Кто-то» из-за двери'
        components = pairing.compound_tokens(text)
        self.assertEqual([('кто', 1, 4), ('то', 5, 7), ('из', 9, 11), ('за', 12, 14), ('двери', 15, 20)],
                         components)
        self.assertEqual([text[start:end].casefold() for _, start, end in components],
                         [word for word, _, _ in components])
        self.assertEqual([], pairing.compound_tokens('— … ***'))

    def test_timestamp_units_still_join_compounds_like_the_aligner(self):
        pairing.validate_timestamp_text('Кто-то пришёл', [{'text': 'Ктото'}, {'text': 'пришёл'}])
        with self.assertRaises(AppError):
            pairing.validate_timestamp_text('Кто-то пришёл', [{'text': 'Кто'}, {'text': 'то'}, {'text': 'пришёл'}])

    def test_transcript_comparison_keeps_genuine_word_mismatches(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        from audit_book1_clip_transcripts import compare
        self.assertEqual([], compare('Колонны-ноги шли.', 'колонны ноги шли')['edits'])
        for expected, heard in (('назойливую муху', 'назуливо муху'),
                                ('шёл неторопливо', 'шёл не торопливо'),
                                ('колонны-ноги шли', 'колонныноги шли')):
            with self.subTest(heard=heard):
                self.assertTrue(compare(expected, heard)['edits'])

    def test_independent_asr_words_must_all_have_matching_timestamps(self):
        fixture = self.fixture()
        asr = fixtures.fake_model(fixture.root / 'asr-audit')
        aligner = fixtures.fake_model(fixture.root / 'aligner-audit')
        stamps = [types.SimpleNamespace(text='выходите', start_time=1., end_time=2.)]
        model = types.SimpleNamespace(transcribe=lambda **kwargs: [types.SimpleNamespace(text='Не выходите.', time_stamps=stamps)])
        qwen = types.SimpleNamespace(Qwen3ASRModel=types.SimpleNamespace(from_pretrained=lambda *args, **kwargs: model))
        torch = types.SimpleNamespace(float32='float32', float16='float16')
        output = fixture.root / 'asr-audit-result'
        with patch.dict(sys.modules, {'qwen_asr': qwen, 'torch': torch}):
            with self.assertRaises(AppError):
                inference.transcribe(fixture.corpus_path, 'track-0', asr, aligner, output)
        self.assertFalse((output / 'transcript.json').exists())
        window = common.json_read(output / 'windows' / '000000.json')
        self.assertEqual(([], '', 'Не выходите.'), (window['words'], window['text'], window['raw_text']))
        self.assertTrue(window['rejected_reason'])
        self.assertEqual('failed', common.json_read(output / 'stage.json')['status'])

    def test_resumed_asr_checkpoint_with_missing_words_is_not_published(self):
        fixture = self.fixture()
        asr = fixtures.fake_model(fixture.root / 'asr-resume')
        aligner = fixtures.fake_model(fixture.root / 'aligner-resume')
        stamps = [types.SimpleNamespace(text='выходите', start_time=1., end_time=2.)]
        model = types.SimpleNamespace(transcribe=lambda **kwargs: [types.SimpleNamespace(text='Выходите.', time_stamps=stamps)])
        qwen = types.SimpleNamespace(Qwen3ASRModel=types.SimpleNamespace(from_pretrained=lambda *args, **kwargs: model))
        torch = types.SimpleNamespace(float32='float32', float16='float16')
        output = fixture.root / 'asr-resume-result'
        with patch.dict(sys.modules, {'qwen_asr': qwen, 'torch': torch}):
            inference.transcribe(fixture.corpus_path, 'track-0', asr, aligner, output)
            (output / 'transcript.json').unlink()
            path = output / 'windows' / '000000.json'
            checkpoint = common.json_read(path)
            checkpoint['text'] = 'Не выходите.'
            checkpoint['sha256'] = digest({k: v for k, v in checkpoint.items() if k != 'sha256'})
            write_json(path, checkpoint)
            with self.assertRaises(AppError):
                inference.transcribe(fixture.corpus_path, 'track-0', asr, aligner, output, resume=True)
        self.assertFalse((output / 'transcript.json').exists())

    def test_jsonl_publication_stays_atomic_when_a_producer_fails(self):
        output = self.root / 'records.jsonl'
        output.write_text('{"saved":true}\n')
        def broken():
            yield {'new': True}
            raise AppError('controlled interruption')
        with self.assertRaises(AppError):
            common.rows_write(output, broken())
        self.assertEqual('{"saved":true}\n', output.read_text())
        self.assertEqual([output], list(self.root.iterdir()))

    def test_streaming_rows_keep_utf8_and_refuse_invalid_or_oversized_records(self):
        path = self.root / 'rows.jsonl'
        common.rows_write(path, ({'text': f'Строка {i}'} for i in range(3)))
        self.assertEqual(3, len(list(common.iter_rows(path))))
        with patch.object(common, 'MAX_ROW_BYTES', 8):
            with self.assertRaises(AppError):
                list(common.iter_rows(path))
        path.write_text('{"bad":NaN}\n')
        with self.assertRaises(AppError):
            list(common.iter_rows(path))

    def test_large_encoded_file_is_not_subject_to_small_manifest_json_cap(self):
        dataset = self.root / 'dataset'; dataset.mkdir()
        encoded = self.root / 'encoded'; encoded.mkdir()
        (dataset / 'clip.wav').write_bytes(b'not used for learned inference in this structural check')
        count = 190
        rows = [{'audio': 'clip.wav', 'ref_audio': 'clip.wav', 'text': f'Controlled example {i}'} for i in range(count)]
        common.rows_write(dataset / 'train.raw.jsonl', rows)
        codes = [[2047] * 16] * 4000
        target = encoded / 'train.codes.jsonl'
        with target.open('w', encoding='utf-8') as handle:
            for row in rows:
                handle.write(json.dumps({**row, 'audio': str(dataset / 'clip.wav'), 'ref_audio': str(dataset / 'clip.wav'), 'audio_codes': codes}) + '\n')
        self.assertGreater(target.stat().st_size, 64 * 1024 * 1024)
        contract = {'dataset_revision': 'fixture', 'dataset_path': str(dataset)}
        write_json(encoded / 'stage.json', {'status': 'complete', 'contract': contract, 'contract_sha256': digest(contract)})
        write_json(encoded / 'encoding.json', {'contract': contract, 'sha256': file_digest(target), 'rows': count})
        self.assertEqual(target, runner.validate_encoding(dataset, {'revision': 'fixture'}, encoded))


if __name__ == '__main__':
    unittest.main()
