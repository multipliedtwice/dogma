from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]

from process_listener_stress_queue import build_revision, stress_native_text, stress_native_edits
from revise_higgs_spoken_take import validate_revision
from rimsky.spoken_edits import apply_spoken_edit
from rimsky.spoken_pronunciation import yo_bound_differences
from rimsky.speech_quality import transcript_check
from rimsky.util import AppError, digest, file_digest, write_json

MAR_SRC = 'Ты предлагаешь устроить капитальный рейд мародерки?'
MAR_SYN = 'Ты предлагаешь устроить капитальный рейд мароде́рки?'
VSE_SRC = 'А этих уродов там тысячи, и все голодные.'
VSE_SYN = 'А этих уродов там тысячи, и всё голодные.'


def before_for(source, synthesis):
    return {'unit': {'id': 'c0001-u0001', 'text': source, 'start': 10, 'spoken': source,
                     'synthesis': {'text': synthesis}}}


def report_for(source, word, intended):
    return {'selected_text': word, 'intended_stress': intended, 'text_offset': 10 + source.index(word)}


class ListenerYoRetakeTest(unittest.TestCase):
    def test_yo_in_selected_word_replaces_wrong_acute(self):
        before = before_for(MAR_SRC, MAR_SYN)
        native, edits = stress_native_edits(before, report_for(MAR_SRC, 'мародерки', 'мародёрки'))
        self.assertEqual('Ты предлагаешь устроить капитальный рейд мародёрки?', native)
        start = native.index('мародёрки')
        self.assertEqual([{'start': start, 'end': start + 9, 'printed': 'мародерки', 'intended': 'мародёрки'}], edits)

    def test_spoken_yo_is_corrected_to_printed_e_with_stress(self):
        before = before_for(VSE_SRC, VSE_SYN)
        native, edits = stress_native_edits(before, report_for(VSE_SRC, 'все', 'все́'))
        self.assertEqual('А этих уродов там тысячи, и все́ голодные.', native)
        self.assertEqual(1, len(edits))
        self.assertEqual('все', edits[0]['intended'])

    def test_rejections(self):
        before = before_for(MAR_SRC, MAR_SYN)
        with self.assertRaises(AppError):  # э belongs to the names path
            stress_native_text(before, report_for(MAR_SRC, 'мародерки', 'мародэрки'))
        with self.assertRaises(AppError):  # different letter
            stress_native_text(before, report_for(MAR_SRC, 'мародерки', 'мародёрка'))
        with self.assertRaises(AppError):  # no stress at all
            stress_native_text(before, report_for(MAR_SRC, 'мародерки', 'мародерки'))
        with self.assertRaises(AppError):  # selection absent from this unit
            stress_native_text(before, {'selected_text': 'замок', 'intended_stress': 'замо́к', 'text_offset': 10})
        # a printed ё cannot be selected as е
        yo_source = 'И всё голодные.'
        with self.assertRaises(AppError):
            stress_native_text(before_for(yo_source, yo_source),
                               {'selected_text': 'все', 'intended_stress': 'все́', 'text_offset': 12})

    def test_yo_change_outside_selected_word_is_rejected(self):
        a, b = 'и все там', 'и всё там'
        edit = {'start': 2, 'end': 5, 'printed': 'все', 'intended': 'все'}
        self.assertFalse(yo_bound_differences(a, b, []))
        self.assertFalse(yo_bound_differences(a, b, [{'start': 0, 'end': 1, 'printed': 'и', 'intended': 'и'}]))
        self.assertTrue(yo_bound_differences(a, b, [edit]))
        self.assertFalse(yo_bound_differences(a, 'и вся там', [edit]))
        self.assertFalse(yo_bound_differences('и все там', 'и всё там!', [edit]))

    def _request(self, source, synthesis, word, intended):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            before = {'unit': before_for(source, synthesis)['unit'], 'receipt': {'key': 'k', 'audio_sha256': 'a' * 64}}
            write_json(folder / 'c0001/chunk-000/selected.json', {'c0001-u0001': before})
            write_json(folder / 'manifest.json', {'source_sha256': 'b' * 64})
            report = {'id': 'r', 'generation_id': 'g', 'segment_id': 'c0001-u0001', 'audio_hash': 'a' * 64,
                      **report_for(source, word, intended)}
            return build_revision(folder, {'report': report})

    def test_request_records_yo_edits_only_for_a_swap(self):
        request = self._request(MAR_SRC, MAR_SYN, 'мародерки', 'мародёрки')
        self.assertEqual('listener_pronunciation_from_synthesis', request['recovery_kind'])
        self.assertEqual(MAR_SYN, request['previous_native_text'])
        self.assertEqual(['мародёрки'], [e['intended'] for e in request['listener_yo_edits']])
        plain = self._request('Он видел замок.', 'Он видел замок.', 'замок', 'замо́к')
        self.assertEqual({'schema', 'generation', 'source_sha256', 'native_text', 'before', 'seed_offset',
                          'listener_report', 'human_audio_approval', 'recovery_kind', 'previous_native_text'},
                         set(plain))

    def test_spoken_edit_accepts_only_bound_yo(self):
        request = self._request(VSE_SRC, VSE_SYN, 'все', 'все́')
        plan = {'expected_spoken': VSE_SRC, 'native_text': request['native_text'],
                'recovery_kind': 'listener_pronunciation_from_synthesis',
                'previous_native_text': VSE_SYN, 'listener_yo_edits': request['listener_yo_edits'],
                'evidence': {'source': 'explicit_listener_instruction'}}
        self.assertEqual((request['native_text'], VSE_SRC), apply_spoken_edit(VSE_SRC, plan))
        with self.assertRaises(ValueError):
            apply_spoken_edit(VSE_SRC, {k: v for k, v in plan.items() if k != 'listener_yo_edits'})

    def _validate(self, source, synthesis, word, intended, mutate=None):
        request = self._request(source, synthesis, word, intended)
        if mutate:
            request['native_text'] = mutate(request['native_text'])
        native = request['native_text']
        old = {'id': 'line', 'spoken': source, 'synthesis': {'text': synthesis, 'prompt': synthesis,
                                                             'effects_policy': 'disabled', 'seed': 7}}
        new = copy.deepcopy(old)
        new['synthesis'].update(text=native, prompt=native)
        with tempfile.TemporaryDirectory() as work:
            wav = Path(work) / 'take.wav'
            sf.write(wav, np.concatenate([np.zeros(2000), np.full(2000, .03), np.zeros(2000)]), 24000)
            key = digest(new['synthesis'])
            sha = file_digest(wav)
            after = {'unit': new, 'path': str(wav), 'receipt': {'key': key, 'audio_sha256': sha},
                     'word_check': {**transcript_check(source, source, sha, 'asr'), 'synthesis_key': key}}
            return validate_revision({'unit': old}, after, request)

    def test_validate_revision_accepts_the_two_yo_cases(self):
        self._validate(MAR_SRC, MAR_SYN, 'мародерки', 'мародёрки')
        self._validate(VSE_SRC, VSE_SYN, 'все', 'все́')

    def test_validate_revision_rejects_unbound_yo_and_missing_edits(self):
        with self.assertRaisesRegex(ValueError, 'changed source words'):
            self._validate(MAR_SRC, MAR_SYN, 'мародерки', 'мародёрки', lambda n: n.replace('рейд', 'рёйд'))
        with self.assertRaisesRegex(ValueError, 'changed source words'):
            self._validate(MAR_SRC, MAR_SYN, 'мародерки', 'мародёрки', lambda n: n.replace('капитальный', 'капитальный.'))
        request = self._request(MAR_SRC, MAR_SYN, 'мародерки', 'мародёрки')
        del request['listener_yo_edits']
        old = {'id': 'line', 'spoken': MAR_SRC, 'synthesis': {'text': MAR_SYN}}
        with self.assertRaisesRegex(ValueError, 'changed source words'):
            validate_revision({'unit': old}, {'unit': copy.deepcopy(old)}, request)


if __name__ == '__main__':
    unittest.main()
