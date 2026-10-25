"""Heading recipe integration; isolated synthetic providers and temp Store only."""
import copy,json,sys,tempfile,unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock,patch
ROOT=next(p for p in Path(__file__).resolve().parents if(p/'runtime/rimsky/store.py').is_file())
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'verification')]
from rimsky import heading_stress as heading,homograph_stress as hs,yo_restoration as yo,listener_stress
from rimsky.util import digest,write_json
from rimsky import russian_preparation as prep


class Integration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.text='Хамелеон';self.source='До. '+self.text+' После.'
        self.binding={'source_id':'p','source_text':self.source,'source_start':4,'source_end':12}
        self.data={'units':[{'text':self.text,'kind':'heading','source':self.binding}],
            'recipe':prep.config('codex_cli'),'pronunciation':{'names':{},'common':[],'proper_nouns':{}},
            'cache_dir':str(self.root),'book_sha256':'b'*64}
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(hs,'disagreements',return_value=[]))
        self.stack.enter_context(patch('rimsky.auto_pronunciation.yo_witnesses',return_value=([],[],[])))
        self.stack.enter_context(patch.object(yo,'ruaccent_yo',return_value={}))
        self.stack.enter_context(patch.object(yo,'analyze',return_value={'rows':[],'decisions':[]}))
        self.stack.enter_context(patch('rimsky.auto_pronunciation.auto_pronunciation',side_effect=lambda text,*a,**kw:(text,{})))
        self.silero=self.stack.enter_context(patch('rimsky.auto_pronunciation._silero',return_value='Хамелео́н'))
        self.ru=self.stack.enter_context(patch.object(hs,'ruaccent_stresses',return_value={('хамелеон',0):6}))
        self.stack.enter_context(patch('rimsky.proper_noun_stress._morph',return_value=Mock(word_is_known=lambda w:True)))
        self.calls=[]
    def call(self,pin,system,request,schema,*,evidence_dir):
        self.calls.append((pin,request));raw={'decisions':[{'id':i['id'],'choice':'ruaccent',
            'stressed':i['candidate_b_ruaccent'],'basis':'Synthetic heading context'}for i in request['items']]}
        folder=Path(evidence_dir);folder.mkdir(parents=True)
        write_json(folder/'request.json',{'system':system,'request':request,'schema':schema,'model':pin[1],'effort':pin[2],
            'command':['fixture-cli','--tools','','--safe-mode','--strict-mcp-config','--no-session-persistence',
                '--model',pin[1],'--effort',pin[2],'--system-prompt',system]})
        write_json(folder/'envelope.json',{'structured_output':raw});write_json(folder/'raw-answer.json',raw)
        return raw,{'fixture':'stress','cli_evidence':str(folder),'executable':'fixture-cli'}
    def prepare(self,call=None):return prep.prepare(self.data,call=call or self.call,validate_provider=lambda *a:None,model_pins={'fixture':True})
    def entry(self,result):return prep.resolve_entry(self.text,{**result,'book_sha256':'b'*64,'listener_source':self.binding})
    def test_heading_agreement_is_source_bound_and_body_does_not_run_dense(self):
        result=self.prepare();entry=self.entry(result)
        self.assertEqual('Хамелео́н',entry['automatic_text']);self.assertEqual([],self.calls)
        self.assertEqual(4,entry['source']['source_start']);self.assertIn('heading_stress',entry['module_pins'])
        self.data['units'][0]['kind']='paragraph';self.silero.side_effect=AssertionError('body dense forbidden')
        body=self.prepare();self.assertEqual('Хамелеон',self.entry(body)['automatic_text'])
        self.assertNotEqual(body['request_sha256'],result['request_sha256'])
    def test_frozen_heading_replay_never_calls_taggers_or_providers(self):
        first=self.prepare();self.silero.side_effect=AssertionError('frozen dense forbidden')
        self.ru.side_effect=AssertionError('frozen RUAccent forbidden')
        self.assertEqual(first,self.prepare(call=lambda *a,**k:(_ for _ in()).throw(AssertionError('provider forbidden'))))
    def test_disagreement_uses_only_bound_opus_high_and_replays_its_evidence(self):
        self.silero.return_value='Хаме́леон'
        result=self.prepare();self.assertEqual('Хамелео́н',self.entry(result)['automatic_text'])
        self.assertEqual(1,len(self.calls));self.assertEqual(prep.providers.STRESS_PIN,self.calls[0][0])
        self.assertEqual('russian_heading_lexical_stress_adjudication',self.calls[0][1]['task'])
        self.assertEqual(first:=result,self.prepare());self.assertEqual(1,len(self.calls))
        witness=next(self.root.rglob('heading/*/witness.json'));value=json.loads(witness.read_text('utf8'));value['source_kind']='paragraph'
        write_json(witness,value)
        with self.assertRaisesRegex(ValueError,'provider evidence changed'):self.prepare()
    def test_offline_heading_disagreement_holds_without_sending(self):
        self.data['recipe']=prep.config('local');self.silero.return_value='Хаме́леон'
        with self.assertRaisesRegex(Exception,'Offline Russian heading preparation'):self.prepare()
        self.assertEqual([],self.calls);self.assertTrue(list(self.root.rglob('heading/*/witness.json')))
    def test_missing_heading_decision_is_preserved_and_holds(self):
        self.silero.return_value='Хаме́леон'
        def invalid(pin,*a,**kw):self.calls.append(pin);return {'decisions':[]},{'fixture':True}
        with self.assertRaisesRegex(ValueError,'Missing decisions'):self.prepare(call=invalid)
        self.assertEqual([prep.providers.STRESS_PIN],self.calls)
    def test_source_mark_yo_and_names_skip_dense(self):
        for text,names in (('Хаме́леон',{}),('Ёлочки',{}),('Хамелеон',{'c':{'name':'Хамелеон','stressed':'Хамелео́н'}})):
            self.text=text;self.binding={**self.binding,'source_text':'До. '+text+' После.','source_end':4+len(text)}
            self.data['units']=[{'text':text,'kind':'heading','source':self.binding}];self.data['pronunciation']['names']=names
            self.silero.side_effect=AssertionError('protected dense forbidden')
            self.assertEqual(text,self.entry(self.prepare())['automatic_text'])
    def test_human_last_overrides_heading_automatic_without_source_change(self):
        automatic=self.entry(self.prepare())['automatic_text']
        frozen={'policy':listener_stress.POLICY,'names':{},'occurrences':[{'source_id':'p','passage_sha256':digest(self.source),
            'text_offset':4,'selected_text':self.text,'intended_stress':'Хаме́леон','report_id':'fixture-human'}]}
        frozen['sha256']=digest(frozen)
        final,_=listener_stress.apply(automatic,frozen,**self.binding)
        self.assertEqual('Хаме́леон',final);self.assertEqual('До. Хамелеон После.',self.source)


import test_runtime_russian_preparation as baseline
from rimsky import jobs as candidate_jobs
class RuntimeKindBinding(baseline.RuntimeRussianPreparationTests):
    def setUp(self):
        super().setUp()
        self.jobs._prepare_russian_chapter=candidate_jobs.Jobs._prepare_russian_chapter.__get__(self.jobs)
    def test_worker_receives_canonical_block_kind_and_resume_keeps_frozen_entries(self):
        self.book['chapters'][0]['blocks'][0]['kind']='heading'
        self.book['chapters'][0]['blocks'][1]['kind']='paragraph'
        first=self.prepare();calls=copy.deepcopy(self.calls)
        units=[u for op,data in calls if op=='prepare_russian'for u in data['units']]
        self.assertEqual(['heading','paragraph'],[u['kind']for u in units[:2]])
        self.assertNotIn('kind',units[0]['source'])
        self.calls.clear();self.assertEqual(first,self.prepare());self.assertEqual([],self.calls)
    def kind_fixture(self,kind):
        self.book['chapters'][0]['blocks']=self.book['chapters'][0]['blocks'][:1]
        self.book['chapters'][0]['blocks'][0]['kind']=kind
        self.generation['segments']=self.generation['segments'][:1]
        original=self.jobs._call
        def call(operation,data):
            result=original(operation,data)
            if operation=='prepare_russian':
                for unit in data['units']:
                    if unit.get('kind')=='heading':
                        key=baseline.prep.entry_key(unit['text'],unit['source']);entry=result['prepared_units'][key]
                        entry['automatic_text']=entry['automatic_text'].replace('\u041d\u0438\u0301\u043c\u0440\u0430\u0441','\u041d\u0438\u043c\u0440\u0430\u0301\u0441')
                        entry['heading_stress']={'fixture':True,'source_kind':'heading'}
                        entry['sound_sha256']=digest(baseline.prep.sound_binding(entry));entry['sha256']=digest({k:v for k,v in entry.items()if k!='sha256'})
                result['sha256']=digest({k:v for k,v in result.items()if k!='sha256'})
            return result
        self.jobs._call=call
    def test_paragraph_to_heading_reprepares_same_source_range(self):
        self.kind_fixture('paragraph');first=self.prepare();self.calls.clear()
        self.book['chapters'][0]['blocks'][0]['kind']='heading';second=self.prepare()
        self.assertEqual(['prepare_russian_names','prepare_russian'],[op for op,_ in self.calls])
        entry=next(iter(second['prepared_units'].values()));self.assertEqual('heading',entry['heading_stress']['source_kind'])
        self.assertIn('\u041d\u0438\u043c\u0440\u0430\u0301\u0441',entry['automatic_text']);self.assertNotEqual(first['prepared_chapters'],second['prepared_chapters'])
    def test_heading_to_paragraph_drops_heading_mark_same_source_range(self):
        self.kind_fixture('heading');self.prepare();self.calls.clear()
        self.book['chapters'][0]['blocks'][0]['kind']='paragraph';second=self.prepare()
        self.assertEqual(['prepare_russian_names','prepare_russian'],[op for op,_ in self.calls])
        entry=next(iter(second['prepared_units'].values()));self.assertNotIn('heading_stress',entry);self.assertIn('\u041d\u0438\u0301\u043c\u0440\u0430\u0441',entry['automatic_text'])
    def test_module_pin_change_forces_preparation_but_unchanged_resume_does_not(self):
        self.kind_fixture('heading')
        with patch.object(baseline.prep,'module_pins',return_value={'heading_stress':'v1'}):
            first=self.prepare();self.calls.clear();self.assertEqual(first,self.prepare());self.assertEqual([],self.calls)
        with patch.object(baseline.prep,'module_pins',return_value={'heading_stress':'v2'}):second=self.prepare()
        self.assertEqual(['prepare_russian'],[op for op,_ in self.calls]);self.assertNotEqual(first['prepared_chapters'],second['prepared_chapters'])
        self.assertEqual(first['proper_noun_preparation'],second['proper_noun_preparation'])
    def test_kind_rebuild_retains_raw_names_notes_and_final_human_authority(self):
        self.kind_fixture('paragraph');segment=self.generation['segments'][0]
        raw_names={'n':{'name':'\u041d\u0438\u043c\u0440\u0430\u0441','stressed':'\u041d\u0438\u0301\u043c\u0440\u0430\u0441','source':'listener'}}
        note={'policy':listener_stress.POLICY,'names':{},'occurrences':[{'source_id':segment['source_id'],
            'passage_sha256':digest(segment['text']),'text_offset':0,'selected_text':'\u041d\u0438\u043c\u0440\u0430\u0441\u043e\u043c',
            'intended_stress':'\u041d\u0438\u0301\u043c\u0440\u0430\u0441\u043e\u043c','report_id':'immutable-human'}]}
        note['sha256']=digest(note)
        self.generation['analysis']['pronunciation'].update(names=copy.deepcopy(raw_names),listener_stress=copy.deepcopy(note))
        self.prepare();self.book['chapters'][0]['blocks'][0]['kind']='heading';result=self.prepare()
        self.assertEqual(raw_names,result['names']);self.assertEqual(note,result['listener_stress'])
        self.assertEqual({'pn:\u041d\u0438\u043c\u0440\u0430\u0441':'\u041d\u0438\u0301\u043c\u0440\u0430\u0441'},result['proper_nouns'])
        entry=next(iter(result['prepared_units'].values()))
        final,evidence=listener_stress.apply(entry['automatic_text'],result['listener_stress'],source_id=segment['source_id'],
            source_text=segment['text'],source_start=segment['start'],source_end=segment['end'])
        self.assertTrue(evidence['applications']);self.assertIn('\u041d\u0438\u0301\u043c\u0440\u0430\u0441\u043e\u043c',final)
if __name__=='__main__':unittest.main()
