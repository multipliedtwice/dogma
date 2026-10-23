"""Synthetic-only source-bound orthography and independent evidence gates."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resolve_demo', ROOT / 'scripts/resolve_public_demo_recognition.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def unit(source):
    expanded, changes = demo.preparation.english_numbers(source)
    return {'text': source, 'spoken': expanded, 'spoken_conversion': {
        'source': source, 'expanded': expanded, 'changes': changes}}


def evidence(expected, heard, family='qwen'):
    return {'role': 'primary' if family == 'qwen' else 'witness', 'family': family,
            'path': family + '.json', 'raw': demo.transcript_check(expected, heard, 'audio-fixture', family)}


class RecognitionResolution(unittest.TestCase):
    def test_recorded_titles_need_two_independent_equivalent_transcripts(self):
        u = unit('Mr. Rowan greeted Mrs. Grey at Mme. Alba’s shop.')
        heard = u['text']
        raw = evidence(u['spoken'], heard)
        result = demo.resolve_transcripts(u, 'English', u['text'], [raw])
        self.assertFalse(result['passed'])
        result = demo.resolve_transcripts(u, 'English', u['text'], [raw, evidence(u['spoken'], heard, 'whisper')])
        self.assertTrue(result['passed'])
        self.assertEqual(result['status'], 'source_bound_spoken_equivalence')
        self.assertEqual(raw['raw']['status'], 'spoken_text_mismatch')
        self.assertEqual(len(result['equivalences']), 6)

    def test_extra_word_or_arbitrary_title_does_not_normalize(self):
        u = unit('Mr. Rowan arrived.')
        for heard in ['King Rowan arrived.', 'Mr. Rowan quickly arrived.', 'Mr. Robin arrived.']:
            self.assertFalse(demo.english_equivalence(u, heard)['passed'])

    def test_number_equivalence_is_exact_recorded_source_surface(self):
        u = unit('She paid $2.01 and kept 42 cents.')
        self.assertTrue(demo.english_equivalence(u, u['text'])['passed'])
        for heard in ['She paid $2.02 and kept 42 cents.', 'He paid $2.01 and kept 42 cents.',
                      'She paid two dollars and one cent and kept 43 cents.']:
            self.assertFalse(demo.english_equivalence(u, heard)['passed'])

    def test_forged_conversion_cannot_create_a_new_equivalence(self):
        u = unit('Mr. Rowan arrived.')
        u['spoken_conversion']['changes'][0]['spoken'] = 'King'
        u['spoken_conversion']['expanded'] = u['spoken'] = 'King Rowan arrived.'
        with self.assertRaisesRegex(ValueError, 'deterministic source rule'):
            demo.english_equivalence(u, 'Mr. Rowan arrived.')

    def test_independent_exact_can_resolve_primary_failure_without_rewriting_it(self):
        u = unit('Her gift.')
        raw = evidence(u['spoken'], 'A gift.')
        result = demo.resolve_transcripts(u, 'English', u['text'], [raw, evidence(u['spoken'], 'Her gift.', 'whisper')])
        self.assertTrue(result['passed'])
        self.assertEqual(result['status'], 'independently_exact')
        self.assertEqual(raw['raw']['heard'], 'A gift.')

    def test_english_names_get_no_russian_tolerance(self):
        u = unit('The mage arrived.')
        result = demo.resolve_transcripts(u, 'English', u['text'], [
            evidence(u['spoken'], 'The magic arrived.'), evidence(u['spoken'], 'The magic arrived.', 'whisper')])
        self.assertFalse(result['passed'])

    def test_english_cast_name_slots_bind_exact_source_occurrence(self):
        from public_demo_english_agreement import build_policy
        book = {'chapters':[{'blocks':[{'id':'p1','text':'The clerk met Elodia arrived.'}]}]}
        assignment = {'cast':{'elodia':{'name':'Madame Elodia'}, 'narrator':{'name':'Narrator'}}}
        policy = build_policy(book, assignment, 'synthetic-book-hash')
        self.assertEqual(policy['allowed_names'], ['elodia'])
        u = unit('Elodia arrived.')
        u.update(source_id='p1', start=len('The clerk met '))
        result = demo.resolve_transcripts(u, 'English', u['text'], [
            evidence(u['spoken'], 'Eloria arrived.'), evidence(u['spoken'], 'Eloria arrived.', 'whisper')], policy)
        self.assertTrue(result['passed'])
        self.assertEqual(result['status'], 'permitted_tolerance')
        self.assertEqual(result['tolerated_deviations'][0]['rule'], 'proper-noun-slot-v1')
        for heard in ['Arrived.', 'Eloria has arrived.', 'Eloria departed.']:
            self.assertFalse(demo.english_equivalence(u, heard, policy)['passed'])
        self.assertFalse(demo.english_equivalence(unit('Her bag.'), 'Hair bag.', policy)['passed'])
        self.assertFalse(demo.english_equivalence(unit('Her bag.'), 'Here bag.', policy)['passed'])
        for wrong in [{**u,'start':0}, {**u,'source_id':'p2'}]:
            self.assertFalse(demo.english_equivalence(wrong, 'Eloria arrived.', policy)['passed'])

    def test_name_slots_reject_heard_pronouns_titles_and_initial_adjective(self):
        from public_demo_english_agreement import build_policy
        text = 'He saw Jim. He saw Della. He saw Young.'
        book = {'chapters':[{'blocks':[{'id':'p1','text':text}, {'id':'p2','text':'Young man left.'}]}]}
        assignment = {'cast':{'jim':{'name':'James Young'},'della':{'name':'Della Young'}}}
        policy = build_policy(book, assignment, 'synthetic-book-hash')
        for source,heard in [('Jim.','Him.'),('Jim.','Her.'),('Della.','Her.'),('Della.','He.'),('Della.','Madame.')]:
            u = unit(source)
            u.update(source_id='p1',start=text.index(source[:-1]))
            self.assertFalse(demo.english_equivalence(u,heard,policy)['passed'],(source,heard))
        u = unit('Young man left.')
        u.update(source_id='p2',start=0)
        self.assertIn('young',policy['allowed_names'])
        self.assertFalse(demo.english_equivalence(u,'Yard man left.',policy)['passed'])

    def test_source_title_period_does_not_erase_following_name_occurrence(self):
        from public_demo_english_agreement import build_policy
        text = 'She met Mme. Sofronie at the shop.'
        book = {'chapters':[{'blocks':[{'id':'p1','text':text}]}]}
        assignment = {'cast':{'shopkeeper':{'name':'Madame Sofronie'}}}
        policy = build_policy(book,assignment,'synthetic-book-hash')
        u = unit('Sofronie at the shop.')
        u.update(source_id='p1',start=text.index('Sofronie'))
        result = demo.english_equivalence(u,'Sofroni at the shop.',policy)
        self.assertTrue(result['passed'])
        self.assertEqual(result['tolerated_deviations'][0]['source_occurrence']['start'],text.index('Sofronie'))
        wrong = {**u,'start':0}
        self.assertFalse(demo.english_equivalence(wrong,'Sofroni at the shop.',policy)['passed'])

    def test_strict_stressed_phone_equivalence_and_exact_letter_boundaries(self):
        for source, heard in [('She practised.', 'She practiced.'), ('Madame arrived.', 'Madam arrived.'),
                              ('The airshaft was empty.', 'The air shaft was empty.')]:
            result = demo.english_equivalence(unit(source), heard)
            self.assertTrue(result['passed'], (source,heard))
            self.assertTrue(result['equivalences'])
        for source,heard in [('The therapist arrived.', 'The rapist arrived.'),
                             ('It was nowhere.', 'It was now here.'),
                             ('The airshaft was empty.', 'The air shaft was very empty.'),
                             ('The airshaft was empty.', 'The shaft was empty.'),
                             ('Her bag was empty.', 'Hair bag was empty.')]:
            self.assertFalse(demo.english_equivalence(unit(source), heard)['passed'], (source,heard))

    def test_russian_name_slot_uses_full_original_paragraph_context(self):
        from rimsky import name_tolerance
        # This dictionary word is a proper-name slot only because the original
        # paragraph capitalizes it mid-sentence; the extracted unit starts here.
        u = {'spoken': 'Сталь кивнула.'}
        source = 'Она увидела Сталь кивнула.'
        result = demo.russian_equivalence(u, 'Стол кивнула.', source, 'a', 'v')
        self.assertTrue(result['passed'])
        self.assertEqual(result['tolerated_deviations'][0]['rule'], name_tolerance.RULE)
        self.assertFalse(demo.russian_equivalence(u, 'Кивнула.', source, 'a', 'v')['passed'])
        self.assertFalse(demo.russian_equivalence(u, 'Стол ушла.', source, 'a', 'v')['passed'])

    def test_shared_interjection_rule_is_disclosed_and_unknown_growl_stays_held(self):
        result = demo.russian_equivalence({'spoken': 'Хо-хо!'}, 'Ха-ха!', 'Хо-хо!', 'a', 'v')
        self.assertTrue(result['passed'])
        self.assertEqual(result['tolerated_deviations'][0]['rule'], 'interjection-kind-v1')
        self.assertFalse(demo.russian_equivalence({'spoken': 'Ррр!'}, 'Грр!', 'Ррр!', 'a', 'v')['passed'])

    def test_shared_russian_unstressed_particle_rule_keeps_real_errors_held(self):
        u = {'spoken':'На площади ни души.','synthesis_text':'На площади ни души́.'}
        heard = 'На площади не души.'
        result = demo.russian_equivalence(u,heard,u['spoken'],'audio-fixture','verifier-fixture')
        self.assertTrue(result['passed'])
        self.assertEqual(result['phonological_equivalences'][0]['kind'],'unstressed_particle')
        self.assertIn('phonological_provenance',result['phonological_equivalences'][0])
        primary = evidence(u['spoken'],heard)
        self.assertFalse(demo.resolve_transcripts(u,'Russian',u['spoken'],[primary])['passed'])
        self.assertTrue(demo.resolve_transcripts(u,'Russian',u['spoken'],[
            primary,evidence(u['spoken'],heard,'whisper')])['passed'])
        for bad in ['На площади души.','На большой площади не души.']:
            self.assertFalse(demo.russian_equivalence(u,bad,u['spoken'],'a','v')['passed'])
        self.assertFalse(demo.russian_equivalence({'spoken':'Было тихо.','synthesis_text':'Бы́ло тихо.'},
                                                  'Была тихо.','Было тихо.','a','v')['passed'])

    def test_raw_evidence_status_and_verifier_are_recomputed(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            context = demo.Context.__new__(demo.Context)
            context.folder = folder
            context.frozen = {'implementation_sha256': demo.file_digest(Path(demo.renderer.__file__))}
            config = {'model': 'Qwen3-ASR-1.7B', 'expected_text_given_to_recognizer': False,
                      'local_files_only': True, 'implementation_sha256': context.frozen['implementation_sha256'],
                      'files': {'synthetic-model-fixture': 'a'*64}}
            signature = demo.sha(config)
            demo.immutable(folder / 'verifiers' / (signature+'.json'), config)
            raw = demo.transcript_check('The gift.', 'A gift.', 'audio-fixture', signature)
            raw['verifier'] = config
            path = folder / 'checks/audio-fixture.json'
            demo.immutable(path, raw)
            observed = context.evidence({'spoken': 'The gift.'}, {'audio_sha256': 'audio-fixture'})
            self.assertEqual(observed[0]['raw']['status'], 'spoken_text_mismatch')
            raw['status'] = 'asr_exact_match'
            path.write_text(demo.json.dumps(raw), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'cannot be reproduced'):
                context.evidence({'spoken': 'The gift.'}, {'audio_sha256': 'audio-fixture'})

    def test_turbo_requires_exact_current_module_model_revision_and_inventory(self):
        import witness_public_demo_turbo as turbo
        config = turbo.verifier_config('en', 'synthetic-torch-version', 'synthetic-transformers-version')
        self.assertEqual(demo.verifier_family(config, 'original-renderer-hash', 'witness'), 'whisper')
        for field, value in [('implementation_sha256', '0'*64), ('revision', '0'*40),
                             ('model', 'some-unapproved-whisper')]:
            changed = {**config, field: value}
            with self.assertRaises(ValueError):
                demo.verifier_family(changed, 'original-renderer-hash', 'witness')
        changed = {**config, 'files': {**config['files'], 'model.safetensors': '0'*64}}
        with self.assertRaises(ValueError):
            demo.verifier_family(changed, 'original-renderer-hash', 'witness')
        with self.assertRaises(ValueError):
            demo.verifier_family(config, 'original-renderer-hash', 'primary')

    def test_resolved_publication_keeps_bookmark_generation_but_new_asset_version(self):
        def page(generation, version):
            return '<script type="application/json" id="playback-config">' + demo.json.dumps({
                'generation': generation, 'version': version, 'captions': [{'text': '<source>'}]}) + '</script>'
        first = demo.stable_playback_config(page('old-report-id', 'mix-v1'), 'frozen-book')
        second = demo.stable_playback_config(page('new-report-id', 'mix-v2'), 'frozen-book')
        config1 = demo.json.loads(demo.re.search(r'>(.*?)</script>', first)[1])
        config2 = demo.json.loads(demo.re.search(r'>(.*?)</script>', second)[1])
        self.assertEqual(config1['generation'], config2['generation'])
        self.assertEqual(config2['generation'], 'frozen-book')
        self.assertEqual(config1['version'], 'mix-v1')
        self.assertEqual(config2['version'], 'mix-v2')
        self.assertEqual(config2['captions'][0]['text'], '<source>')


if __name__ == '__main__':
    unittest.main()
