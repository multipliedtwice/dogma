import copy
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky import listener_stress,russian_pronunciation,spoken_pronunciation as spoken
from rimsky.util import digest


class PreparedSynthesisTests(unittest.TestCase):
    def prepared(self,automatic='ни́кого и по́том.'):
        text='никого и потом.';source='Вчера. '+text
        binding={'source_id':'p1','source_text':source,'source_start':7,'source_end':len(source)}
        frozen={'policy':listener_stress.POLICY,'names':{},'occurrences':[{
            'source_id':'p1','passage_sha256':digest(source),'text_offset':7,
            'selected_text':'никого','intended_stress':'никого́','report_id':'fixture-report'}]}
        frozen['sha256']=digest(frozen)
        spec={'policy':'russian-preparation-v1','listener_source':binding,'listener_stress':frozen}
        entry={'automatic_text':automatic,'sha256':'e'*64,'sound_sha256':'s'*64}
        resolver=Mock(return_value=entry)
        module=types.ModuleType('rimsky.russian_preparation');module.resolve_entry=resolver
        return text,spec,entry,resolver,module

    def render(self,text,spec,module):
        with patch.dict(sys.modules,{'rimsky.russian_preparation':module}),\
             patch.object(spoken,'phrase_stress',side_effect=AssertionError('automatic layers already frozen')),\
             patch.object(spoken,'_automatic',side_effect=AssertionError('no live tagger')):
            return spoken.synthesis_text(text,spec)

    def test_human_final_at_nonzero_source_offset_then_closed_phonetics(self):
        text,spec,entry,resolver,module=self.prepared()
        native,receipt=self.render(text,spec,module)
        self.assertEqual('никаво́ и по́том.',native)
        resolver.assert_called_once_with(text,spec)
        self.assertEqual('никого́ и по́том.',receipt['russian_phonetics']['prephonetic_source'])
        russian_pronunciation.validate(receipt['russian_phonetics']['prephonetic_source'],native,
                                      receipt['russian_phonetics']['records'])
        self.assertEqual('fixture-report',receipt['listener_stress']['applications'][0]['report_id'])
        self.assertEqual(entry['sha256'],receipt['prepared_pronunciation']['entry_sha256'])
        self.assertEqual('никого и потом.',text)

    def test_invalid_or_unbound_preparation_holds_without_legacy_fallback(self):
        text,spec,entry,resolver,module=self.prepared('никоѓо и потом.')
        with self.assertRaisesRegex(ValueError,'Russian vowel'):self.render(text,spec,module)
        resolver.side_effect=ValueError('Prepared source mismatch')
        with self.assertRaisesRegex(ValueError,'source mismatch'):self.render(text,spec,module)

    def test_key_ignores_diagnostic_entry_and_global_profile_but_binds_actual_sound(self):
        text,spec,entry,resolver,module=self.prepared()
        _,receipt=self.render(text,spec,module)
        original=spoken.cache_binding(receipt)
        diagnostic=copy.deepcopy(receipt)
        diagnostic['prepared_pronunciation']['entry_sha256']='d'*64
        diagnostic['listener_stress'].update(sha256='unrelated-profile',snapshot_sha256='new-global-profile')
        self.assertEqual(original,spoken.cache_binding(diagnostic))
        for field in ('prepared','human','phonetic'):
            changed=copy.deepcopy(receipt)
            if field=='prepared':changed['prepared_pronunciation']['sound_sha256']='n'*64
            elif field=='human':changed['listener_stress']['applications'][0]['report_id']='new-note'
            else:changed['russian_phonetics']['module_sha256']='new-rule'
            with self.subTest(field=field):self.assertNotEqual(original,spoken.cache_binding(changed))

    def test_existing_correct_human_choice_still_binds_note_without_edit(self):
        text,spec,entry,resolver,module=self.prepared('никого́ и по́том.')
        _,receipt=self.render(text,spec,module)
        self.assertEqual([],receipt['listener_stress']['edits'])
        self.assertTrue(spoken.cache_binding(receipt)['listener_stress']['applications'])

    def test_packaged_nonfile_modules_use_loader_code_pins(self):
        import importlib.machinery
        import marshal
        from rimsky import russian_preparation_providers as providers
        text,spec,entry,resolver,module=self.prepared()
        code=compile('packaged_fixture = True','packaged-fixture.py','exec')
        loader=Mock();loader.get_code.return_value=code
        deployed=importlib.machinery.ModuleSpec('fixture',loader,origin='embedded://no-source.py')
        with patch.object(providers.importlib.util,'find_spec',return_value=deployed):
            _,receipt=self.render(text,spec,module)
        expected=digest(marshal.dumps(code))
        self.assertEqual(expected,receipt['listener_stress']['module_sha256'])
        self.assertEqual(expected,receipt['russian_phonetics']['module_sha256'])

    def test_real_frozen_resolver_rejects_occurrence_reuse_and_tampering(self):
        from rimsky import russian_preparation as prepared
        text='кого.';source={'source_id':'p1','source_text':'Кто? кого.', 'source_start':5,'source_end':10}
        entry={'policy':prepared.POLICY,'book_sha256':'b'*64,'source':prepared.source_binding(source),
               'text_sha256':digest(text.encode('utf8')),'automatic_text':'кого́.',
               'module_pins':{'fixture':'m'*64},'model_pins':{'fixture':'w'*64}}
        entry['sound_sha256']=digest(prepared.sound_binding(entry));entry['sha256']=digest(entry)
        spec={'policy':prepared.POLICY,'book_sha256':'b'*64,'listener_source':source,
              'prepared_units':{prepared.entry_key(text,source):entry}}
        self.assertEqual('каво́.',spoken.synthesis_text(text,spec)[0])
        changed=copy.deepcopy(spec);changed['book_sha256']='other-book'
        with self.assertRaisesRegex(ValueError,'book'):spoken.synthesis_text(text,changed)
        changed=copy.deepcopy(spec);changed['listener_source']['source_id']='p2'
        with self.assertRaisesRegex(ValueError,'Missing frozen'):spoken.synthesis_text(text,changed)
        changed=copy.deepcopy(spec)
        next(iter(changed['prepared_units'].values()))['automatic_text']='ко́го.'
        with self.assertRaisesRegex(ValueError,'evidence changed'):spoken.synthesis_text(text,changed)

    def test_legacy_keys_and_no_policy_behavior_are_unchanged(self):
        self.assertEqual(('кого.',spoken.phrase_stress('кого.')[1]),spoken.synthesis_text('кого.'))
        self.assertEqual({},spoken.cache_binding({'listener_stress':{'edits':[{'x':1}]}}))
        listener={'policy':'legacy','applications':[{'source':'exact'}],'edits':[{'before':'x'}]}
        self.assertEqual({'listener_stress':{'policy':'legacy','applications':listener['applications']}},
                         spoken.cache_binding({'listener_stress':listener}))


if __name__=='__main__':unittest.main()
