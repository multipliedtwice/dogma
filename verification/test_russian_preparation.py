"""Packaged recipe integration; all taggers and provider executions are synthetic."""
import copy
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch,Mock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'verification')]
from rimsky import russian_preparation as prep
from rimsky import homograph_stress as hs,yo_restoration as yo
from rimsky.util import digest,write_json
from luna_test_support import evidence


class RussianPreparationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.text='Все стоит денег.';self.source='До. '+self.text+' После.'
        self.binding={'source_id':'p1','source_text':self.source,'source_start':4,'source_end':4+len(self.text)}
        self.data={'units':[{'text':self.text,'source':self.binding}],'recipe':prep.config('codex_cli'),
            'pronunciation':{'names':{},'common':[],'proper_nouns':{}},'cache_dir':str(self.root),'book_sha256':'b'*64}
        self.calls=[];self.stack=ExitStack();self.addCleanup(self.stack.close);self.addCleanup(self.temp.cleanup)
        self.stack.enter_context(patch('rimsky.proper_noun_stress.proper_noun_lexicon',return_value={}))
        self.hs_rows=[{'sha256':digest(self.text.encode()),'word':'стоит','index':0,'text':self.text,
            'sentence':self.text,'surface':'стоит','silero':'сто́ит','ruaccent':'стои́т','silero_stress':2,'ruaccent_stress':3}]
        self.yo_rows=[{'sha256':digest(self.text.encode()),'word':'все','index':0,'surface':'Все','yo':'Всё',
            'kind':'ambiguous','witnesses':{'silero':'e','ruaccent':'e','grammar':None},'sentence':self.text}]
        self.hs_mock=self.stack.enter_context(patch.object(hs,'disagreements',side_effect=lambda *a,**k:copy.deepcopy(self.hs_rows)))
        self.stack.enter_context(patch('rimsky.auto_pronunciation.yo_witnesses',return_value=([],[],[])))
        self.stack.enter_context(patch.object(yo,'ruaccent_yo',return_value={}))
        self.stack.enter_context(patch.object(yo,'analyze',side_effect=lambda *a,**k:{'rows':copy.deepcopy(self.yo_rows),'decisions':[],'stats':{}}))
        def automatic(text,names,common,**options):
            self.assertTrue(options['rare_words']);self.assertTrue(options['implicit_single'])
            self.assertEqual(options['homograph']['rule'],hs.RULE)
            decision=next(iter(options['yo']['decisions']),None)
            native=text.replace('стоит','стои́т') if self.hs_rows else text
            if decision and decision['choice']=='yo':native=native.replace('Все','Всё')
            return native,{'homograph_overrides':{'unbound':[]},'yo_restoration':{'unbound':[]}}
        self.auto=self.stack.enter_context(patch('rimsky.auto_pronunciation.auto_pronunciation',side_effect=automatic))

    def call(self,pin,system,request,schema,*,evidence_dir):
        self.calls.append((pin,copy.deepcopy(request)))
        if pin==prep.providers.STRESS_PIN:
            raw={'decisions':[{'id':i['id'],'choice':'ruaccent','stressed':i['candidate_b_ruaccent'],'basis':'case fixture'} for i in request['items']]}
            folder=Path(evidence_dir);folder.mkdir(parents=True)
            command=['fixture-cli','--tools','','--safe-mode','--strict-mcp-config','--no-session-persistence',
                '--model',pin[1],'--effort',pin[2],'--system-prompt',system]
            write_json(folder/'request.json',{'system':system,'request':request,'schema':schema,'model':pin[1],'effort':pin[2],'command':command})
            write_json(folder/'envelope.json',{'structured_output':raw})
            write_json(folder/'raw-answer.json',raw)
            return raw,{'fixture':'stress','cli_evidence':str(folder),'executable':'fixture-cli'}
        second=request['pass_id']=='pass-2'
        raw={'decisions':{i['id']:{'choice':'Все' if second else 'Всё','basis':'fixture',**({}if second else{'confidence':100})}for i in request['items']}}
        metadata=evidence(evidence_dir,system,request,schema,raw)
        return raw,metadata

    def prepare(self,**kwargs):
        return prep.prepare(self.data,call=self.call,validate_provider=lambda *a:None,model_pins={'fixture':'local-only'},**kwargs)

    def test_separate_pinned_stress_and_conditional_independent_yo(self):
        saved=self.prepare();entry=prep.resolve_entry(self.text,{**saved,'book_sha256':'b'*64,'listener_source':self.binding})
        self.assertEqual(entry['automatic_text'],'Все стои́т денег.')
        self.assertEqual([c[0]for c in self.calls],[prep.providers.STRESS_PIN,prep.providers.YO_PIN,prep.providers.YO_PIN])
        first,second=[c[1]for c in self.calls[1:]]
        self.assertEqual(first['items'][0]['baseline'],'Все')
        self.assertEqual(first['items'][0]['witnesses'],{'silero':'e','ruaccent':'e','grammar':None})
        self.assertNotIn('confidence',second['items'][0]);self.assertNotIn('first_answer',json.dumps(second))
        self.assertEqual(second['items'][0]['candidates'],list(reversed(first['items'][0]['candidates'])))
        choice=saved['evidence']['yo'][0]['selected'][0]
        self.assertEqual(choice['selected_pass'],'pass-2');self.assertFalse(choice['agreement'])

    def test_frozen_reuse_never_reruns_models_or_providers(self):
        first=self.prepare();calls=len(self.calls)
        self.hs_mock.side_effect=AssertionError('tagger must not rerun');self.auto.side_effect=AssertionError('automatic must not rerun')
        self.assertEqual(first,self.prepare());self.assertEqual(len(self.calls),calls)

    def test_offline_holds_after_witnesses_without_provider(self):
        self.data['recipe']=prep.config('local')
        with self.assertRaisesRegex(Exception,'Offline Russian preparation is held'):self.prepare()
        self.assertEqual(self.calls,[])
        held=list(self.root.rglob('held.json'));self.assertEqual(len(held),1)
        self.assertTrue((held[0].parent/'witnesses.json').is_file())

    def test_offline_deterministic_text_can_complete(self):
        self.hs_rows=[];self.yo_rows=[];self.data['recipe']=prep.config('local')
        saved=self.prepare();self.assertEqual(next(iter(saved['prepared_units'].values()))['automatic_text'],self.text)
        self.assertEqual(self.calls,[])

    def test_exact_source_offset_or_input_change_rejects_entry(self):
        saved=self.prepare()
        for binding,text in (({**self.binding,'source_start':0},self.text),(self.binding,self.text+'!'),
            ({**self.binding,'source_text':self.source+'!'},self.text)):
            with self.assertRaisesRegex(ValueError,'Missing frozen'):prep.resolve_entry(text,{**saved,'book_sha256':'b'*64,'listener_source':binding})

    def test_entry_and_external_receipt_tampering_fail_closed(self):
        saved=self.prepare();bad=copy.deepcopy(saved);next(iter(bad['prepared_units'].values()))['automatic_text']='changed'
        with self.assertRaisesRegex(ValueError,'evidence changed'):prep.resolve_entry(self.text,{**bad,'book_sha256':'b'*64,'listener_source':self.binding})
        artifact=next(self.root.rglob('attempt-0/raw-answer.json'));artifact.write_text('{}',encoding='utf8')
        with self.assertRaisesRegex(ValueError,'provider evidence changed'):self.prepare()

    def test_invalid_semantic_vote_is_preserved_and_not_retried(self):
        def invalid(pin,*args,**kwargs):
            self.calls.append(pin);Path(kwargs['evidence_dir']).mkdir(parents=True)
            return {'decisions':[]},{'fixture':'invalid'}
        with self.assertRaisesRegex(ValueError,'Missing decisions'):
            prep.prepare(self.data,call=invalid,validate_provider=lambda *a:None,model_pins={})
        self.assertEqual(self.calls,[prep.providers.STRESS_PIN])
        self.assertTrue(list(self.root.rglob('raw.json')))

    def test_invalid_pinned_config_rejected_before_models(self):
        self.data['recipe']['stress']['model']='other'
        with self.assertRaisesRegex(ValueError,'pins changed'):self.prepare()
        self.hs_mock.assert_not_called()

    def test_sound_key_ignores_diagnostic_receipts_but_tracks_choice(self):
        saved=self.prepare();entry=next(iter(saved['prepared_units'].values()));changed=copy.deepcopy(entry)
        changed['automatic_evidence']['reason']='extra diagnostics';changed['sha256']=digest({k:v for k,v in changed.items()if k!='sha256'})
        prep.validate_entry(changed);self.assertEqual(changed['sound_sha256'],entry['sound_sha256'])
        changed['automatic_text']='Все сто́ит денег.'
        with self.assertRaises(ValueError):prep.validate_entry(changed)

    def test_same_source_in_another_book_never_reuses_recipe(self):
        saved=self.prepare()
        with self.assertRaisesRegex(ValueError,'different source book'):
            prep.resolve_entry(self.text,{**saved,'book_sha256':'c'*64,'listener_source':self.binding})

    def test_book_binding_ignores_mutable_application_metadata(self):
        book={'source_hash':'s'*64,'chapters':[{'id':'c1','blocks':[{'id':'p1','text':self.text}]}]}
        changed={**book,'position':{'offset':100},'generations':[{'updated':'later'}],'visual_revision':25}
        self.assertEqual(prep.book_binding(book),prep.book_binding(changed))
        changed=copy.deepcopy(book);changed['chapters'][0]['blocks'][0]['text']+='!'
        self.assertNotEqual(prep.book_binding(book),prep.book_binding(changed))

    def test_opus_receipt_cannot_be_rebound_to_another_valid_request(self):
        def wrong(pin,system,request,schema,**kwargs):
            raw,metadata=self.call(pin,system,request,schema,**kwargs)
            if pin==prep.providers.STRESS_PIN:
                path=Path(kwargs['evidence_dir'])/'request.json';value=json.loads(path.read_text('utf8'))
                value['request']['items'][0]['sentence']='different source';write_json(path,value)
            return raw,metadata
        with self.assertRaisesRegex(ValueError,'differs from bound preparation'):
            prep.prepare(self.data,call=wrong,validate_provider=lambda *a:None,model_pins={})

    def test_opus_accepted_answer_must_match_raw_envelope(self):
        def wrong(pin,*args,**kwargs):
            raw,metadata=self.call(pin,*args,**kwargs)
            if pin==prep.providers.STRESS_PIN:write_json(Path(kwargs['evidence_dir'])/'envelope.json',{'structured_output':{'decisions':[]}})
            return raw,metadata
        with self.assertRaisesRegex(ValueError,'raw CLI envelope'):
            prep.prepare(self.data,call=wrong,validate_provider=lambda *a:None,model_pins={})

    def test_recomputed_hashes_cannot_authorize_changed_words(self):
        saved=self.prepare();entry=next(iter(saved['prepared_units'].values()))
        entry['automatic_text']='Все стои́т домик.'
        entry['sound_sha256']=digest(prep.sound_binding(entry));entry['sha256']=digest({k:v for k,v in entry.items()if k!='sha256'})
        with self.assertRaisesRegex(ValueError,'Respelling'):
            prep.resolve_entry(self.text,{**saved,'book_sha256':'b'*64,'listener_source':self.binding})

    def test_unchanged_yo_proposal_needs_only_one_opinion(self):
        self.hs_rows=[]
        def unchanged(pin,system,request,schema,*,evidence_dir):
            self.calls.append((pin,request))
            raw={'decisions':{i['id']:{'choice':'Все','basis':'unchanged','confidence':2}for i in request['items']}}
            return raw,evidence(evidence_dir,system,request,schema,raw)
        saved=prep.prepare(self.data,call=unchanged,validate_provider=lambda *a:None,model_pins={})
        self.assertEqual(len(self.calls),1)
        self.assertEqual(saved['evidence']['yo'][0]['selected'][0]['selected_pass'],'pass-1')

    def test_unknown_witness_always_gets_independent_second_opinion(self):
        self.hs_rows=[];self.yo_rows[0]['witnesses']={'silero_stress_on_e':True,'ruaccent':None}
        saved=self.prepare()
        self.assertEqual(len(self.calls),2)
        self.assertIsNone(self.calls[0][1]['items'][0]['baseline'])
        self.assertEqual(saved['evidence']['yo'][0]['selected'][0]['selected_pass'],'pass-2')

    def test_module_closure_change_invalidates_reusable_preparation(self):
        first=self.prepare();calls=len(self.calls)
        original=prep.providers.module_digest
        with patch.object(prep.providers,'module_digest',side_effect=lambda name:'new-cli-source'if name=='rimsky.cli_process'else original(name)):
            second=self.prepare()
        self.assertNotEqual(first['request_sha256'],second['request_sha256'])
        self.assertGreater(len(self.calls),calls)

    def test_recomputed_artifact_hash_does_not_accept_foreign_opus_request(self):
        from rimsky.util import file_digest
        saved=self.prepare();folder=next(self.root.glob('*/prepared.json')).parent
        vote=next((folder/'stress').glob('*/answer.json'));raw=vote.parent/'attempt-0/raw.json'
        request=vote.parent/'attempt-0/cli/request.json';value=json.loads(request.read_text('utf8'))
        value['request']['items'][0]['sentence']='another sentence';write_json(request,value)
        # Rebuild the outer inventory exactly as a copied self-consistent cache
        # would: the request-bound verifier must still reject this receipt.
        saved['artifacts'][request.relative_to(folder).as_posix()]=file_digest(request)
        saved['sha256']=digest({k:v for k,v in saved.items()if k!='sha256'});write_json(folder/'prepared.json',saved)
        with self.assertRaisesRegex(ValueError,'differs from bound preparation'):self.prepare()

    def test_missing_ruaccent_package_never_treats_workspace_as_model_directory(self):
        from types import SimpleNamespace
        with patch('importlib.util.find_spec',side_effect=lambda name:SimpleNamespace(origin=str(self.root/'silero/__init__.py'))if name=='silero_stress'else None),\
             patch.object(Path,'is_file',return_value=True),patch.object(Path,'is_dir',return_value=True),\
             patch.object(prep,'file_digest',side_effect=AssertionError('no workspace traversal or hashing')):
            with self.assertRaisesRegex(Exception,'RUAccent is not installed'):prep.local_model_pins()


if __name__=='__main__':unittest.main()
