"""Dictionary/context stress regressions: no model/provider/GPU execution."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
from rimsky import contextual_stress as m
import build_contextual_stress as builder


class ContextStress(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.out=Path(self.temp.name)
        self.exe=self.out/'codex.exe';self.exe.write_bytes(b'fixture')
        self.metadata=dict(zip(('provider','model','effort'),builder.PIN))|{'policy':m.RULE,'requested_model':'gpt-6-luna',
            'model_provenance':'explicit-cli-model-selection','cli_version':'fixture','executable':str(self.exe),
            'executable_sha256':m.file_digest(self.exe),'structured_call_sha256':m.file_digest(Path(builder.codex_analysis.__file__))}
    def answer(self,rows,mark):
        req=m.request(rows);answer={'decisions':[{'id':i['id'],'word':i['word'],'stressed':mark,'basis':'Sentence meaning.'} for i in req['items']]}
        m.validate(answer,req)
        return {'rule':m.RULE,'dictionary_sha256':m.DICTIONARY_SHA256,'answer_sha256':'a'*64,'decisions':m.decisions_from(rows,answer)}
    def test_dictionary_detects_unmarked_and_agreeing_verbs_and_nouns(self):
        for word in ('стоит','плачу','замок','муки','атлас'):
            with self.subTest(word=word):self.assertGreaterEqual(len(m.candidates(word)),2)
        source='Здесь стоит вышеписанный человек.'
        for selected in (source,source.replace('стоит','стои́т'),source.replace('стоит','сто́ит')):
            rows=m.analyze(source,selected)
            self.assertEqual([r['word'] for r in rows],['стоит']);self.assertEqual(set(rows[0]['candidates']),{'стои́т','сто́ит'})
    def test_stands_and_costs_are_distinct_context_answers_not_book_rules(self):
        for source,mark in (('Здесь стоит человек.','стои́т'),('Билет стоит дорого.','сто́ит')):
            rows=m.analyze(source,source);block=self.answer(rows,mark)
            result,proof=m.apply(source,source,block)
            self.assertIn(mark,result);self.assertEqual(source,source.replace(mark,'стоит'))
            self.assertFalse(proof['human_approval']);self.assertFalse(proof['acoustic_approval'])
    def test_context_mark_survives_guard_and_unprotected_neighbor_is_dropped(self):
        source='Здесь стоит вышеписанный человек.';selected=source.replace('вышеписанный','вышепи́санный')
        rows=m.analyze(source,selected);block=self.answer(rows,'стои́т')
        result,proof=m.apply(source,selected,block)
        self.assertIn('стои́т вышеписанный',result)
        self.assertEqual(proof['close_mark_guard']['dropped'][0]['dropped'],'вышепи́санный')
        self.assertEqual(proof['protected_occurrences'],[{'word':'стоит','index':0}])
    def test_explicit_marks_yo_and_names_are_not_reinterpreted(self):
        for source in ('Здесь сто́ит человек.','Он узнаёт новости.'):
            self.assertEqual(m.analyze(source,source),[])
        self.assertEqual(m.analyze('Он увидел Замок.','Он увидел Замок.'),[])
        self.assertEqual(m.analyze('Стоит человек.','Стоит человек.',{'name':'Стои́т'}),[])
    def test_source_coordinates_all_occurrences_and_exact_letters_are_guarded(self):
        source='Билет стоит дорого.';rows=m.analyze(source,source);block=self.answer(rows,'сто́ит')
        for change in ({'decisions':[]},{'decisions':[dict(block['decisions'][0],start=0)]},
                       {'decisions':[dict(block['decisions'][0],stressed='ста́ит')]}):
            with self.subTest(change=change),self.assertRaises(ValueError):m.apply(source,source,{**block,**change})
        for selected in ('Билет Стоит дорого.','Билет стоит дёшево.'):
            with self.assertRaises(ValueError):m.apply(source,selected,block)
    def test_context_can_disclose_superseded_mark_but_cannot_change_yo(self):
        source='Здесь стоит человек.';selected=source.replace('стоит','сто́ит')
        result,proof=m.apply(source,selected,self.answer(m.analyze(source,selected),'стои́т'),protected={('стоит',0)})
        self.assertIn('стои́т',result);self.assertTrue(proof['applied'][0]['superseded_automatic_mark'])
        req=m.request(m.analyze('Он узнает правду.','Он узнает правду.'))
        with self.assertRaises(ValueError):m.validate({'decisions':[{'id':'c0000','word':'узнает','stressed':'узнаёт','basis':'Wrong letters.'}]},req)
    def test_v2_projects_lowercased_stress_to_exact_source_case_with_raw_evidence(self):
        source='Души.';rows=m.analyze(source,source)
        row=next(r for r in rows if r['word']=='души');request=m.request([row])
        answer={'decisions':[{'id':'c0000','word':'Души','stressed':'ду́ши','basis':'Souls, nominative plural.'}]}
        m.validate(answer,request);decision=m.decisions_from([row],answer)[0]
        self.assertEqual(decision['stressed'],'Ду́ши');self.assertEqual(decision['raw_model_stressed'],'ду́ши')
        self.assertEqual(decision['case_projection']['rule'],m.CASE_RULE);self.assertTrue(decision['case_projection']['case_changed'])
        block={'rule':m.RULE,'dictionary_sha256':m.DICTIONARY_SHA256,'answer_sha256':'a'*64,'decisions':[decision]}
        result,proof=m.apply(source,source,block)
        self.assertEqual(result,'Ду́ши.');self.assertEqual(source,'Души.')
        self.assertEqual(proof['applied'][0]['raw_model_stressed'],'ду́ши')
        for changed in ({'stressed':'ду́ше'},{'stressed':'души'},{'stressed':'д́уши'},{'word':'души'}):
            bad={'decisions':[{**answer['decisions'][0],**changed}]}
            with self.subTest(changed=changed),self.assertRaises(ValueError):m.validate(bad,request)
        bad=copy.deepcopy(block);bad['decisions'][0]['case_projection']['case_changed']=False
        with self.assertRaises(ValueError):m.apply(source,source,bad)
        with self.assertRaises(ValueError):m.project_candidate({'candidates':['Ду́ши','ДУ́ШИ']},'ду́ши')
    def test_cache_actual_evidence_and_policy_bindings_are_rechecked(self):
        inputs=[{'source':'Билет стоит дорого.','selected':'Билет стоит дорого.'}]
        answer={'decisions':[{'id':'c0000','word':'стоит','stressed':'сто́ит','basis':'Price context.'}]}
        with patch.object(builder,'call',return_value=(answer,self.metadata)) as call:
            first=builder.prepare(inputs,self.out/'prepared');second=builder.prepare(inputs,self.out/'prepared')
            self.assertEqual(first,second);call.assert_called_once()
        self.assertEqual(first,builder.block(self.out/'prepared/answer.json'))
        rp=next((self.out/'prepared/batches').glob('*.request.json'));bound=builder.read(rp)
        self.assertEqual([bound[k] for k in ('policy','provider','model','effort')],[m.RULE,*builder.PIN])
        ap=next(p for p in (self.out/'prepared/batches').glob('*.answer.json') if '.attempt-' not in p.name)
        saved=builder.read(ap);saved['metadata']['model']='claude-opus-5-5';ap.write_text(json.dumps(saved),encoding='utf8')
        with self.assertRaises(ValueError):builder.block(self.out/'prepared/answer.json')
    def test_substantive_invalid_answer_holds_without_persuasion_retry(self):
        inputs=[{'source':'Здесь стоит человек.','selected':'Здесь стоит человек.'}]
        bad={'decisions':[{'id':'c0000','word':'стоит','stressed':'стоит','basis':'Unmarked is not an answer.'}]}
        with patch.object(builder,'call',return_value=(bad,self.metadata)) as call:
            with self.assertRaises(ValueError):builder.prepare(inputs,self.out/'prepared')
            call.assert_called_once()
        with patch.object(builder,'call') as call,self.assertRaises(ValueError):builder.prepare(inputs,self.out/'prepared')
        call.assert_not_called()


if __name__=='__main__':unittest.main()
