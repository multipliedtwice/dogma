"""Contextual е/ё+stress, including two agreeing but wrong taggers; no models."""
import copy
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import prepare_public_demo_pronunciation_v4 as m

FUTURE='Как оштрафуют его, мерзавца, так он узнает у меня, что значит собака и прочий бродячий скот!'
PRESENT='Каждый день он узнает что-нибудь новое.'
HOMOGRAPH={'rule':m.hs.RULE,'answer_sha256':'h'*64,'overrides':[]}


class ContextV4(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.out=Path(self.temp.name)
    def unit(self,text=FUTURE,uid='future'):
        return {'id':uid,'source_id':'p','start':20,'end':20+len(text),'text':text,'spoken':text,'chapter_id':'c'}
    def witnesses(self,text,*args):
        words=re.findall('[А-Яа-яЁё́]+',text);return [],[w.replace('узнает','узнаёт') for w in words],words
    def ruaccent(self,text):
        return {(key,index):match[0].replace('узнает','узнаёт') for match,key,index in m.hs.occurrences(text)}
    def analysis(self,unit):
        with patch('rimsky.auto_pronunciation.yo_witnesses',side_effect=self.witnesses):return m.analyse(unit,{},[],ruaccent=self.ruaccent)
    def result(self,row,choice='e'):
        word=row['surface'] if choice=='e' else row['yo'];marked=word.replace('узнает','узна́ет')
        return {'row':row,'decision':{'id':'y0000','choice':choice,'word':word,'stressed':marked,'basis':'Context grammar.'},
            'request_sha256':'a'*64,'answer_sha256':'b'*64}
    def test_both_wrong_witnesses_agree_but_ambiguous_verbs_still_require_context(self):
        for text in (FUTURE,PRESENT):
            result=self.analysis(self.unit(text));rows=[r for r in result['context'] if r['word']=='узнает']
            self.assertEqual(len(rows),1);self.assertFalse(any(d['word']=='узнает' for d in result['auto']))
            self.assertEqual(rows[0]['witnesses']['silero'],'yo');self.assertEqual(rows[0]['witnesses']['ruaccent'],'yo')
            self.assertIsNone(rows[0]['prior_tagger_basis']);self.assertTrue(rows[0]['dictionary_forms'])
            self.assertEqual(rows[0]['source_binding']['surface'],'узнает')
    def test_future_explicit_stress_and_true_present_survive_wrong_final_silero(self):
        units=[self.unit(),self.unit(PRESENT,'present')];before=copy.deepcopy(units)
        original=m.analyse
        def analyse(unit,*args):return original(unit,{},[],ruaccent=self.ruaccent)
        def adjudicate(out,rows):return [self.result(r,'e' if r['unit']=='future' else 'yo') for r in rows]
        metadata={**dict(zip(('provider','model','effort'),m.builder.PIN)),'policy':m.builder.POLICY,
            'requested_model':'gpt-6-luna','model_provenance':'explicit-cli-model-selection','cli_version':'fixture',
            'executable':sys.executable,'executable_sha256':m.file_digest(Path(sys.executable)),
            'structured_call_sha256':m.file_digest(Path(m.builder.codex_analysis.__file__))}
        def luna(system,request,*args):
            return {'decisions':[{'id':i['id'],'choice':'yo' if 'Каждый день' in i['sentence'] else 'e',
                'word':i['candidate_yo'] if 'Каждый день' in i['sentence'] else i['candidate_e'],'basis':'Sentence meaning.'} for i in request['items']]},metadata
        def context_luna(system,request,*args):
            return {'decisions':[{'id':i['id'],'word':i['word'],'stressed':i['word'].replace('узнает','узна́ет'),'basis':'Future meaning.'}
                for i in request['items']]},{**metadata,'policy':m.contextual_stress.RULE}
        with patch('rimsky.auto_pronunciation.yo_witnesses',side_effect=self.witnesses),patch.object(m,'analyse',side_effect=analyse),\
             patch.object(m.builder,'call',side_effect=luna),patch.object(m.context_builder,'call',side_effect=context_luna),\
             patch.object(m,'context_adjudicate',side_effect=adjudicate),patch('rimsky.auto_pronunciation._silero',side_effect=lambda t,*a,**k:t.replace('узнает','узнаёт')):
            prepared,details=m.prepare_rows(units,{},[],HOMOGRAPH,self.out)
        self.assertIn('узна́ет',prepared[0]['synthesis_text']);self.assertNotIn('узнаёт',prepared[0]['synthesis_text'])
        self.assertIn('узнаёт',prepared[1]['synthesis_text']);self.assertEqual(units,before);self.assertEqual(details['context_conflicts'],0)
        for unit in prepared:self.assertEqual(unit['pronunciation']['policy'],'stress-rare-v4');self.assertFalse(unit['pronunciation']['context_stress']['audio_approval'])
    def test_explicit_source_yo_and_stress_remain_authoritative(self):
        for text in ('Он узнаёт новости.','Он узна́ет завтра.'):
            unit=self.unit(text);result=self.analysis(unit)
            self.assertFalse(any(r['word']=='узнает' for r in result['context']))
    def test_generic_finite_dictionary_guard_not_a_book_lemma_whitelist(self):
        self.assertTrue(m.finite_forms('узнает'));self.assertTrue(m.finite_forms('узнаем'));self.assertTrue(m.finite_forms('осел'))
        self.assertEqual(m.finite_forms('небо'),[]);self.assertEqual(m.finite_forms('неизвестноеслово'),[])
    def test_stressed_answer_and_source_occurrence_binding_are_strict(self):
        unit=self.unit();row=self.analysis(unit)['context'][0];request=m.context_request([row]);good=self.result(row)['decision'];answer={'decisions':[good]}
        m.validate_context(answer,request)
        for marked in ('узнает','узнаёт','у́зна́ет','узн́ает','узна́ют'):
            with self.assertRaises(ValueError):m.validate_context({'decisions':[{**good,'stressed':marked}]},request)
        result=self.result(row);changed=self.unit(FUTURE+' Потом.')
        with self.assertRaises(ValueError):m.overlay_context(changed,changed['spoken'],[result],HOMOGRAPH)
    def test_context_stress_cannot_reverse_separate_luna_yo_choice(self):
        row=self.analysis(self.unit())['context'][0]
        row['yo_decision']={'choice':'e','word':row['surface']}
        request=m.context_request([row]);m.validate_context({'decisions':[self.result(row)['decision']]},request)
        with self.assertRaisesRegex(ValueError,'frozen Luna'):
            m.validate_context({'decisions':[self.result(row,'yo')['decision']]},request)
    def test_older_homograph_disagreement_is_recorded_and_not_silently_replaced(self):
        unit=self.unit();row=self.analysis(unit)['context'][0];result=self.result(row)
        override={'sha256':m.digest(unit['spoken'].encode('utf8')),'word':'узнает','index':0,'stress':4,'choice':'ruaccent'}
        output,record=m.overlay_context(unit,unit['spoken'],[result],{**HOMOGRAPH,'overrides':[override]})
        self.assertEqual(output,unit['spoken']);self.assertEqual(len(record['conflicts']),1);self.assertEqual(record['applied'],[])
        output,record=m.overlay_context(unit,unit['spoken'],[result],{**HOMOGRAPH,'overrides':[{**override,'stress':3}]})
        self.assertIn('узна́ет',output);self.assertEqual(record['conflicts'],[])
    def test_one_context_pass_cached_with_pinned_model_and_immutable_request(self):
        row=self.analysis(self.unit())['context'][0];answer={'decisions':[self.result(row)['decision']]}
        metadata={**dict(zip(('provider','model','effort'),m.PIN)),'model_usage':{m.PIN[1]:{}},'requested_model':m.PIN[1],'cli_version':'fixture'}
        with patch.object(m.stress_builder,'call',return_value=(answer,metadata)) as call:
            first=m.context_adjudicate(self.out,[row]);second=m.context_adjudicate(self.out,[row]);self.assertEqual(first,second);call.assert_called_once()
        for path in self.out.glob('*.answer.json'):
            saved=m.read(path);saved['metadata']['model']='wrong';m.legacy.read(path)
            path.write_text(__import__('json').dumps(saved),encoding='utf8')
        with self.assertRaises(ValueError):m.context_adjudicate(self.out,[row])
    def test_reported_actual_provider_model_must_match_not_only_requested_label(self):
        metadata={**dict(zip(('provider','model','effort'),m.PIN)),'requested_model':m.PIN[1],'cli_version':'fixture','model_usage':{m.PIN[1]+'-20261023':{}}}
        self.assertEqual(m.validate_provider(metadata),[m.PIN[1]+'-20261023'])
        for usage in ({},{'claude-haiku-5-5':{}},{m.PIN[1]+'-other':{}}):
            with self.assertRaises(ValueError):m.validate_provider({**metadata,'model_usage':usage})
    def test_new_freeze_entrypoint_consumes_corrected_assignment_and_rejects_conflicts(self):
        details={'policy':'stress-rare-v4','context_policy':m.RULE,'context_conflicts':0}
        m.immutable(self.out/'pronunciation/result.json',{'assignment':{'pronunciation':details,'units':[{'synthesis_text':'Он узна́ет.'}]}})
        m.immutable(self.out/'pronunciation/binding.json',{'implementation_sha256':m.file_digest(m.SELF),'dependencies':m.dependencies()})
        with patch.object(m.legacy,'freeze') as freeze:m.freeze(self.out);freeze.assert_called_once_with(self.out,None)
        result=m.read(self.out/'pronunciation/result.json');result['assignment']['pronunciation']['context_conflicts']=1
        (self.out/'pronunciation/result.json').write_text(__import__('json').dumps(result),encoding='utf8')
        with patch.object(m.legacy,'freeze') as freeze:
            with self.assertRaises(ValueError):m.freeze(self.out)
            freeze.assert_not_called()
    def test_conflict_preserves_full_input_evidence_but_creates_no_ready_artifacts(self):
        source=self.out/'source';target=self.out/'target';unit=self.unit()
        m.immutable(source/'analysis/assigned.json',{'language':'Russian','units':[unit]})
        for name in ('prepared/book.json','analysis/structure.request.json','analysis/structure.answer.json'):m.immutable(source/name,{'fixture':name})
        m.immutable(source/'pronunciation/binding.json',{'assigned_sha256':m.file_digest(source/'analysis/assigned.json'),'book_sha256':m.file_digest(source/'prepared/book.json')})
        m.immutable(source/'pronunciation/name-lexicon.json',{'names':{},'common':[],**dict(zip(('provider','model','effort'),m.PIN))})
        m.immutable(source/'pronunciation/homograph.answer.json',{'rule':m.hs.RULE,'overrides':[],'metadata':{'batches':[]}})
        for name in ('name-stress.request.json','name-stress.answer.json','homograph.request.json'):m.immutable(source/'pronunciation'/name,{'original':name})
        paths=list((source/'pronunciation').glob('*.json'));baseline={'assignment':{'units':[unit]},'evidence_sha256':{p.relative_to(source).as_posix():m.file_digest(p) for p in paths}}
        m.immutable(source/'pronunciation/result.json',baseline)
        with patch.object(m.legacy,'verify_assignment_source',return_value={}),patch.object(m,'prepare_rows',return_value=([unit],{'context_conflicts':1})):
            with self.assertRaisesRegex(ValueError,'Conflicting stress'):m.prepare(source,target)
        self.assertTrue((target/'pronunciation/context-conflicts.json').exists());self.assertFalse((target/'assignment.json').exists());self.assertFalse((target/'pronunciation/result.json').exists())
        for original in paths:
            copied=target/'pronunciation/historical-evidence'/original.name;self.assertEqual(copied.read_bytes(),original.read_bytes())


if __name__=='__main__':unittest.main()
