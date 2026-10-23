"""Synthetic source windows, alignment/crop guards and closed proposal tests."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('context_recovery', ROOT / 'scripts/context_public_audiobook_recovery.py')
context_recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context_recovery)


class ContextRecoveryGates(unittest.TestCase):
    def source_fixture(self):
        texts = ['Out of his trance Jim seemed quickly to wake. ', 'He enfolded his Della. ',
                 'For ten seconds let us regard with discreet scrutiny some inconsequential object in the other direction. ']
        paragraph = ''.join(texts)
        units, cursor = [], 0
        for index, text in enumerate(texts):
            units.append({'id': 'u'+str(index), 'source_id': 'p1', 'start': cursor, 'end': cursor+len(text),
                          'text': text, 'spoken': text.strip(), 'chapter_id': 'c1'})
            cursor += len(text)
        return {'units': units}, {'p1': paragraph}, units[1]

    def test_three_neighbor_context_is_source_exact_and_target_slot_preserved(self):
        assigned, blocks, target = self.source_fixture()
        candidates = context_recovery.context_candidates(assigned, blocks, target)
        both = next(c for c in candidates if len(c['source_spans']) == 3)
        self.assertEqual(both['source_text'], blocks['p1'])
        self.assertEqual(both['synthesis_text'], ' '.join(u['spoken'] for u in assigned['units']))
        words = context_recovery.word_tokens(both['spoken'])
        self.assertEqual(words[both['target_word_start']:both['target_word_end']], ['he', 'enfolded', 'his', 'della'])
        self.assertLessEqual(both['estimated_seconds'], 30)

    def test_single_sentence_paragraph_can_use_exact_cross_paragraph_context(self):
        assigned, _, target = self.source_fixture()
        blocks = {}
        for index, unit in enumerate(assigned['units']):
            unit.update(source_id='p'+str(index), start=0, end=len(unit['text']))
            blocks[unit['source_id']] = unit['text']
        candidates = context_recovery.context_candidates(assigned, blocks, target)
        both = next(c for c in candidates if len(c['source_spans']) == 3)
        self.assertEqual([s['source_id'] for s in both['source_spans']], ['p0', 'p1', 'p2'])
        self.assertEqual(both['source_text'], '\n\n'.join(unit['text'] for unit in assigned['units']))
        assigned['units'][0]['chapter_id'] = 'other-chapter'
        assigned['units'][2]['chapter_id'] = 'other-chapter'
        with self.assertRaisesRegex(ValueError, 'No source-adjacent'):
            context_recovery.context_candidates(assigned, blocks, target)

    def test_source_word_omission_and_changed_conversions_are_rejected(self):
        assigned, blocks, target = self.source_fixture()
        assigned['units'][0]['text'] = 'Changed source.'
        with self.assertRaisesRegex(ValueError, 'exact source span'):
            context_recovery.context_candidates(assigned, blocks, target)
        assigned, blocks, target = self.source_fixture()
        assigned['units'][0]['spoken'] = 'Added custom words.'
        assigned['units'][2]['spoken'] = 'More added custom words.'
        with self.assertRaisesRegex(ValueError, 'No source-adjacent'):
            context_recovery.context_candidates(assigned, blocks, target)

    def test_crop_uses_only_quiet_gaps_and_refuses_active_speech(self):
        import numpy as np
        rate = 24000
        audio = np.zeros(rate*2, dtype=np.int16)
        cues = [{'text':'Before.', 'start':.1, 'end':.3}, {'text':'He', 'start':.6, 'end':.7},
                {'text':'enfolded', 'start':.7, 'end':.9}, {'text':'his', 'start':.9, 'end':1.0},
                {'text':'Della.', 'start':1.0, 'end':1.15}, {'text':'After.', 'start':1.5, 'end':1.7}]
        for cue in cues:
            audio[round(cue['start']*rate):round(cue['end']*rate)] = 2000
        context = {'spoken': 'Before. He enfolded his Della. After.', 'target_word_start':1, 'target_word_end':5}
        bounds = context_recovery.crop_bounds(audio, rate, cues, context)
        self.assertLess(bounds['start_sample'], .6*rate)
        self.assertGreater(bounds['end_sample'], 1.15*rate)
        self.assertGreaterEqual(bounds['start_sample'], .3*rate)
        self.assertLessEqual(bounds['end_sample'], 1.5*rate)
        audio[:] = 2000
        with self.assertRaisesRegex(ValueError, 'No quiet sentence boundary'):
            context_recovery.crop_bounds(audio, rate, cues, context)
        bad = copy.deepcopy(cues)
        bad[2]['text'] = 'unfolded'
        with self.assertRaisesRegex(ValueError, 'alignment changed words'):
            context_recovery.crop_bounds(audio, rate, bad, context)

    def test_alignment_itself_never_approves_words_or_partial_target_cue(self):
        import numpy as np
        cues = [{'text':'Before He', 'start':.1, 'end':.5}, {'text':'enfolded.', 'start':.8, 'end':1.2}]
        context = {'spoken':'Before He enfolded.', 'target_word_start':1, 'target_word_end':3}
        with self.assertRaisesRegex(ValueError, 'splits an aligner cue'):
            context_recovery.crop_bounds(np.zeros(48000, np.int16), 24000, cues, context)
        with self.assertRaises(Exception):
            context_recovery.validate_cues(cues, 'Before He unfolded.', 2)
        self.assertIn('only', context_recovery.__doc__.lower())

    def test_proposal_cannot_invent_context_or_unbounded_attempt(self):
        assigned, blocks, target = self.source_fixture()
        candidates = context_recovery.context_candidates(assigned, blocks, target)
        bound = {'target': target, 'candidates': candidates}
        valid = {'attempts': [{'context_id': candidates[0]['id'], 'profile':profile}
                              for profile in ('careful', 'steady', 'alternate')],
                 'reason':'Read the source context naturally.', 'source_quote':target['text'].strip()}
        context_recovery.validate_proposal(valid, bound)
        bad = copy.deepcopy(valid)
        bad['attempts'][0]['context_id'] = 'invented-source'
        with self.assertRaises(ValueError):
            context_recovery.validate_proposal(bad, bound)
        repeated = copy.deepcopy(valid)
        repeated['attempts'][2] = repeated['attempts'][0]
        with self.assertRaisesRegex(ValueError, 'distinct context/profile'):
            context_recovery.validate_proposal(repeated, bound)
        with self.assertRaisesRegex(ValueError, 'bounded to three'):
            context_recovery.attempt_home(Path('fixture'), target, 4)

    def test_same_paragraph_context_retains_real_offsets_and_english_policy(self):
        assigned, blocks, target = self.source_fixture()
        prefix = 'An earlier sentence. '
        blocks['p1'] = prefix+blocks['p1']
        for unit in assigned['units']:
            unit['start'] += len(prefix)
            unit['end'] += len(prefix)
        chosen = next(c for c in context_recovery.context_candidates(assigned, blocks, target)
                      if len(c['source_spans']) == 3)
        policy = {'source_evidence': {'della': [{'source_id':'p1', 'start':73, 'end':78, 'source':'Della'}]}}
        source = SimpleNamespace(blocks=blocks, english_policy=policy)
        synthetic, paragraph, actual_policy = context_recovery.context_comparison_unit(chosen, source)
        self.assertEqual(synthetic['source_id'], 'p1')
        self.assertEqual(synthetic['start'], len(prefix))
        self.assertEqual(synthetic['end'], assigned['units'][-1]['end'])
        self.assertEqual(blocks['p1'][synthetic['start']:synthetic['end']], synthetic['text'])
        self.assertIs(actual_policy, policy)
        self.assertEqual(paragraph, blocks['p1'])
        # Mixed source IDs cannot inherit another paragraph's character slots.
        changed = copy.deepcopy(chosen)
        changed['source_spans'][-1]['source_id'] = 'another-source'
        synthetic, _, actual_policy = context_recovery.context_comparison_unit(changed, source)
        self.assertEqual(synthetic['source_id'], 'private-context')
        self.assertIsNone(actual_policy)

    def test_provider_payload_excludes_private_palette_paths_and_verifier_inventory(self):
        assigned, blocks, target = self.source_fixture()
        target['palette_reference'] = {'path':'C:/training-work/private.wav', 'text':'PRIVATE TRAINING SENTENCE'}
        target['manual-labeling'] = 'C:/manual-labeling/answers.json'
        bound = {'language':'English', 'target':target,
                 'candidates':context_recovery.context_candidates(assigned, blocks, target),
                 'parents':[{'sha256':'a'*64, 'receipt':{'quality_passed':False, 'quality_error':'Missing C:/training-work/private.wav'},
                             'checks':[{'raw':{'heard':'He unfolded his Della.', 'status':'spoken_text_mismatch',
                                               'verifier':{'model':'Qwen3-ASR-1.7B', 'private':'C:/manual-labeling/secret.json'}}}]}]}
        with patch.object(context_recovery, 'file_digest', return_value='b'*64):
            payload = context_recovery.proposal_request(Path('fixture'), target, bound)
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in ('palette_reference', 'voice_ref', 'training-work', 'manual-labeling', 'PRIVATE TRAINING'):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(payload['request']['target']['spoken'], target['spoken'])
        self.assertEqual(payload['request']['failures'][0]['transcripts'][0]['heard'], 'He unfolded his Della.')

    def test_russian_context_preserves_existing_stress_and_pins_recognizer_languages(self):
        texts = ['Рядом лежали дрова. ', 'Он взял два полена. ', 'Потом вернулся домой. ']
        natives = ['Ря́дом лежа́ли дрова́.', 'Он взя́л два поле́на.', 'Пото́м верну́лся домо́й.']
        units, offset = [], 0
        for index, (text, native) in enumerate(zip(texts, natives)):
            units.append({'id':'r'+str(index), 'source_id':'ru-p', 'start':offset, 'end':offset+len(text),
                          'text':text, 'spoken':text.strip(), 'synthesis_text':native, 'chapter_id':'c1'})
            offset += len(text)
        assigned = {'language':'Russian', 'units':units}
        blocks = {'ru-p':''.join(texts)}
        chosen = next(c for c in context_recovery.context_candidates(assigned, blocks, units[1])
                      if len(c['source_spans']) == 3)
        self.assertEqual(chosen['synthesis_text'], ' '.join(natives))
        self.assertEqual(chosen['spoken_conversion']['policy'], 'grouped-digits-v3')
        synthetic, _, policy = context_recovery.context_comparison_unit(chosen, SimpleNamespace(blocks=blocks, english_policy=None))
        self.assertEqual(synthetic['synthesis_text'], ' '.join(natives))
        self.assertIsNone(policy)
        bound = {'language':'Russian', 'qwen_files':{'model.safetensors':'a'*64}}
        qwen = context_recovery.verifier_config(bound, 'qwen', 'torch-test', 'transformers-test')
        turbo = context_recovery.verifier_config(bound, 'turbo', 'torch-test', 'transformers-test')
        self.assertEqual((qwen['language'],qwen['decoder_language']), ('Russian','Russian'))
        self.assertEqual((turbo['language'],turbo['decoder_language']), ('Russian','ru'))
        self.assertEqual(qwen['attention'], 'sdpa')
        self.assertEqual(turbo['threads'], 4)
        self.assertEqual(qwen['batching_sha256'], context_recovery.file_digest(ROOT/'scripts/asr_batching.py'))
        check = context_recovery.transcript_check(units[1]['spoken'], units[1]['spoken'], 'c'*64, context_recovery.renderer.sha(qwen))
        check['verifier'] = qwen
        context_recovery.check_record(check, units[1]['spoken'], 'c'*64, bound, 'qwen')
        check['verifier'] = {**qwen, 'batching_sha256':'forged'}
        with self.assertRaisesRegex(ValueError, 'unapproved decoding'):
            context_recovery.check_record(check, units[1]['spoken'], 'c'*64, bound, 'qwen')

    def test_alignment_runtime_provenance_has_explicit_sdpa_and_dependency_hash(self):
        bound = {'language':'Russian', 'aligner_files':{'model.safetensors':'a'*64}}
        config = context_recovery.alignment_config(bound, 'torch-test', 'transformers-test')
        self.assertEqual((config['language'],config['attention'],config['threads'],config['dtype'],config['device']),
                         ('Russian','sdpa',4,'float16','cuda:0'))
        self.assertEqual(len(config['aligner_implementation_sha256']),64)
        self.assertFalse(config['alignment_is_audio_approval'])
        dependencies = context_recovery.dependencies()
        self.assertIn('scripts/asr_batching.py',dependencies)
        self.assertIn('runtime/rimsky/spoken_numbers.py',dependencies)


if __name__ == '__main__':
    unittest.main()
