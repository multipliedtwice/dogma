"""Explicit listener stress choices become source-bound, deduplicated retakes."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]

from rimsky.listener_stress_retake import enqueue_listener_stress_retake
from rimsky.speech_quality import transcript_check
from rimsky.util import AppError, file_digest, write_json
from process_listener_stress_queue import build_revision, stress_native_text, reuse_applied, next_seed_offset, replacement_environment
from revise_higgs_spoken_take import tolerate_name_check
from server import Application


class FakeStore:
    def __init__(self, root, generation):
        self.root = root
        self._generation = generation

    def generation(self, generation_id):
        if generation_id != self._generation['id']:
            raise AssertionError('unexpected generation')
        return self._generation


class ListenerStressRetakeTest(unittest.TestCase):
    def test_retake_uses_current_pin_not_the_heard_model_override(self):
        pinned = ROOT / 'current-production.gguf'
        with patch.dict('process_listener_stress_queue.os.environ', {'RIMSKY_HIGGS_MODEL': 'heard-old.gguf'}), \
             patch('process_listener_stress_queue.resolve_production_higgs_model',
                   return_value=types.SimpleNamespace(path=pinned)):
            environment = replacement_environment()
        self.assertEqual(environment['RIMSKY_HIGGS_MODEL'], str(pinned))

    def test_revision_keeps_the_heard_model_in_immutable_before_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            selected = folder / 'c0001/chunk-000/selected.json'
            selected.parent.mkdir(parents=True)
            before = {'unit': {'id': 'c0001-u0001', 'text': 'замок', 'start': 0,
                               'synthesis': {'text': 'замок', 'model_sha256': 'heard-model'}},
                      'receipt': {'key': 'old-key', 'audio_sha256': 'a' * 64}}
            write_json(selected, {'c0001-u0001': before})
            write_json(folder / 'manifest.json', {'source_sha256': 'b' * 64})
            report = {'id': 'report', 'generation_id': 'generation', 'segment_id': 'c0001-u0001',
                      'audio_hash': 'a' * 64, 'selected_text': 'замок',
                      'intended_stress': 'замо́к', 'text_offset': 0}
            request = build_revision(folder, {'report': report})
            self.assertEqual(request['before'], before)
            self.assertEqual(request['before']['unit']['synthesis']['model_sha256'], 'heard-model')

    def test_runtime_returns_the_queue_receipt_with_the_durable_report(self):
        report = {'id': 'r' * 24, 'kind': 'stress'}
        class ReportStore:
            def report_stress(self, *args):
                return dict(report)
        app = object.__new__(Application)
        app.store = ReportStore()
        app.closing = threading.Event()
        data = {'generation_id': 'g', 'segment_id': 's', 'source_id': 'p',
                'selected_text': 'замок', 'intended_stress': 'замо́к'}
        receipt = {'id': report['id'], 'status': 'queued', 'existing': False}
        with patch('rimsky.listener_stress_retake.enqueue_listener_stress_retake', return_value=receipt):
            saved = app.dispatch('report_stress', data)
        self.assertEqual(receipt, saved['retake_queue'])

    def test_chosen_stress_is_applied_to_the_exact_repeated_occurrence(self):
        source = 'Он видел замок, потом замок.'
        second = source.rindex('замок')
        before = {'unit': {'text': source, 'start': 100,
                           'spoken': source, 'synthesis': {'text': source}}}
        report = {'selected_text': 'замок', 'intended_stress': 'замо́к',
                  'text_offset': 100 + second}
        expected = source[:second] + 'замо́к' + source[second + len('замок'):]
        self.assertEqual(expected, stress_native_text(before, report))

    def test_stress_choice_requires_an_exact_source_offset(self):
        word = 'замок'
        before = {'unit': {'text': word, 'start': 0,
                           'spoken': word, 'synthesis': {'text': word}}}
        with self.assertRaisesRegex(AppError, 'offset'):
            stress_native_text(before, {'selected_text': word,
                                        'intended_stress': 'за́мок',
                                        'text_offset': None})

    def test_compound_name_component_keeps_the_prefix(self):
        source = 'Ар-Хакон посмотрел на орка.'
        before = {'unit': {'text': source, 'start': 0,
                           'spoken': source, 'synthesis': {'text': 'Ар-Ха́кон посмотрел на орка́.'}}}
        report = {'selected_text': 'Хакон', 'intended_stress': 'Хако́н',
                  'text_offset': source.index('Хакон')}
        self.assertEqual('Ар-Хако́н посмотрел на орка́.', stress_native_text(before, report))

    def test_name_spelling_disagreement_is_disclosed_not_retreated(self):
        check = transcript_check('Сказал Гундахар.', 'Сказал Гундакар.', 'a' * 64, 'primary')
        check['synthesis_key'] = 'take'
        resolved = tolerate_name_check(check, 'Сказал Гундахар.')
        self.assertEqual('asr_exact_match', resolved['status'])
        self.assertEqual('proper-noun-slot-v1', resolved['tolerated_deviations'][0]['rule'])
        self.assertEqual('Сказал Гундакар.', resolved['heard_raw'])
        swallowed = transcript_check('Сказал Гундахар.', 'Сказал.', 'b' * 64, 'primary')
        swallowed['synthesis_key'] = 'take'
        self.assertEqual('spoken_text_mismatch', tolerate_name_check(swallowed, 'Сказал Гундахар.')['status'])

    def test_enqueue_is_durable_and_deduplicates_the_same_heard_take(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            data_dir = parent / 'production-listener-library'
            data_dir.mkdir()
            external = parent / 'generation-one'
            external.mkdir()
            (external / 'manifest.json').write_text('{}', encoding='utf-8')
            (external / 'progress.json').write_text(
                json.dumps({'generation': external.name}), encoding='utf-8')
            generation = {
                'id': 'g' * 24,
                'config': {'engine': 'higgs_audio_tts_q8_external_preview'},
                'analysis': {'external_preview': {'generation': external.name}},
                'segments': [{'id': 'c0001-u0001', 'audio_hash': 'a' * 64,
                              'performance': {'external_preview': {'published_start': 1.0}}}],
            }
            report = {
                'id': 'r' * 24, 'book_id': 'b' * 32, 'generation_id': generation['id'],
                'segment_id': 'c0001-u0001', 'chapter_id': 'c0001', 'source_id': 'p1',
                'selected_text': 'замок', 'text_offset': 10, 'audio_offset': 1.2,
                'audio_hash': 'a' * 64, 'passage_text': 'замок', 'spoken_text': 'замок',
                'created': '2026-10-21T00:00:00Z', 'kind': 'stress', 'comment': '',
                'intended_stress': 'замо́к',
            }
            store = FakeStore(data_dir, generation)
            with patch('rimsky.listener_stress_retake.subprocess.Popen') as launch:
                first = enqueue_listener_stress_retake(store, report)
                second = enqueue_listener_stress_retake(store, {**report, 'id': 's' * 24})
            self.assertEqual('queued', first['status'])
            self.assertFalse(first['existing'])
            self.assertTrue(second['existing'])
            self.assertEqual(first['id'], second['id'])
            saved = json.loads((external / 'listener-stress-queue' / f"{report['id']}.json").read_text('utf-8'))
            self.assertEqual(report['intended_stress'], saved['report']['intended_stress'])
            self.assertFalse(saved['human_audio_approval'])
            self.assertEqual(2, launch.call_count)

    def test_late_duplicate_reuses_applied_audio_and_failed_seeds_advance(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);queue=folder/'listener-stress-queue';instructions=queue/'instructions'
            instructions.mkdir(parents=True);(folder/'pronunciation-retry-reports').mkdir()
            report={'generation_id':'g','segment_id':'c0001-u0001','selected_text':'Имя',
                    'intended_stress':'Имя\u0301','text_offset':0}
            applied={'id':'old','status':'applied','report':report,'instruction':'old.json',
                     'instruction_sha256':'a'*64}
            current={'id':'new','status':'queued','report':report}
            old_path=queue/'old.json';new_path=queue/'new.json'
            write_json(old_path,applied);write_json(new_path,current)
            write_json(folder/'spoken-take-revisions.json',{'units':{'c0001-u0001':{
                'replacement':{'receipt':{'audio_sha256':'b'*64}}}}})
            self.assertTrue(reuse_applied(folder,new_path,current))
            reused=json.loads(new_path.read_text('utf8'))
            self.assertEqual('applied',reused['status']);self.assertEqual('old',reused['reused_from'])

            held={'id':'held','status':'held','report':{**report,'segment_id':'c0001-u0002'}}
            retry={'id':'retry','status':'queued','report':{**report,'segment_id':'c0001-u0002'}}
            held_path=queue/'held.json';retry_path=queue/'retry.json';write_json(held_path,held);write_json(retry_path,retry)
            instruction=instructions/'held.json';write_json(instruction,{'seed_offset':4})
            write_json(folder/'pronunciation-retry-reports'/f'{file_digest(instruction)}.json',
                       {'attempts':[{'status':'held'} for _ in range(3)]})
            self.assertEqual(7,next_seed_offset(folder,retry_path,retry))


if __name__ == '__main__':
    unittest.main()
