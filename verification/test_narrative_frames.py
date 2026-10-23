import copy
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.narrative_frames import request_for,checked_ranges,reconcile,requested_voice,adjudicate
from rimsky.util import AnalysisInvalidError,SpeechHoldError

class NarrativeFramesTests(unittest.TestCase):
    def setUp(self):
        self.book={'chapters':[{'id':'c1','title':'Глава 1','blocks':[
            {'id':'p1','kind':'paragraph','text':'Старый охотник перевернулся у костра.'},
            {'id':'p2','kind':'separator','text':'* * *'},
            {'id':'p3','kind':'paragraph','text':'Я отвлёкся от таблицы параметров.'}]},
            {'id':'c2','title':'Глава 2','blocks':[{'id':'p4','kind':'paragraph','text':'Я посмотрел на спутников.'}]}]}
        self.request=request_for(self.book,'c1')
        def frame(first,last,role,register,quote,perspective):
            return {'first':first,'last':last,'role':role,'register':register,'perspective':perspective,
                'focal_identity':'герой','relation_to_main':'другая перспектива','confidence':'high','reason':'По контексту.',
                'evidence':[{'id':first,'quote':quote}]}
        self.answer={'ranges':[frame('p1','p2','opening_vignette','opening','Старый охотник','third_person'),
                               frame('p3','p3','main_story','ordinary','Я отвлёкся','first_person')]}

    def test_opening_ends_inside_chapter_one(self):
        report=reconcile(self.answer,copy.deepcopy(self.answer),self.request)
        self.assertEqual('opening',report['by_source']['p1']['register'])
        self.assertEqual('ordinary',report['by_source']['p3']['register'])
        self.assertEqual('checked_proposal',report['by_source']['p3']['status'])

    def test_source_formatting_and_whole_readout_route_survive_sentence_casting(self):
        from rimsky.jobs import routed_voice
        from rimsky.cli_analysis import structure
        from unittest.mock import Mock
        book=copy.deepcopy(self.book)
        book['chapters'][0]['blocks'][0]['formatting']={'full':['emphasis','strong']}
        request=request_for(book,'c1')
        self.assertEqual(request['targets'][0]['formatting'],{'full':['emphasis','strong']})
        answer=copy.deepcopy(self.answer)
        answer['ranges'][0].update(role='system_readout',register='mechanical')
        report=reconcile(answer,answer,request)
        frame=report['by_source']['p1']
        pack={'voices':[{'id':'normal','family':'narrator_baseline'},
                        {'id':'stats','family':'mechanical_stats'}]}
        analysis={'narrative_frames':report,'narrator_voices':{'c1':'normal'}}
        voice,*_=routed_voice(pack,analysis,{}, {'speaker':'wrong_character'}, {'source_id':'p1','chapter_id':'c1'})
        self.assertEqual('stats',voice['id'])
        infer=Mock(side_effect=AssertionError('A confirmed display bullet must not be reclassified as dialogue'))
        fixed=structure({'segments':[{'id':'piece','source_id':'p1','text':'- Сила 100 (ячеек — 7);'}],
            'narrative_frames':{'p1':frame}},infer)
        self.assertTrue(fixed['piece']);self.assertTrue(all(r['mode']=='narrator' for r in fixed['piece']))
        infer.assert_not_called()
        with self.assertRaises(SpeechHoldError):requested_voice(frame,[pack['voices'][0]],'normal')

    def test_chapter_heading_never_determines_opening(self):
        alternative=copy.deepcopy(self.answer)
        alternative['ranges'][0].update(role='main_story',register='ordinary')
        report=reconcile(alternative,copy.deepcopy(alternative),self.request)
        self.assertEqual('ordinary',report['by_source']['p1']['register'])

    def test_missing_reordered_and_overlapping_ranges_are_rejected(self):
        for ranges in (self.answer['ranges'][:1],self.answer['ranges'][::-1],self.answer['ranges']*2):
            with self.subTest(ranges=ranges),self.assertRaises(AnalysisInvalidError):checked_ranges({'ranges':ranges},self.request)

    def test_prose_from_another_section_does_not_prove_a_frame(self):
        bad=copy.deepcopy(self.answer);bad['ranges'][0]['evidence']=[{'id':'p4','quote':'Я посмотрел'}]
        with self.assertRaisesRegex(AnalysisInvalidError,'own range'):checked_ranges(bad,self.request)
        bad['ranges'][0]['evidence']=[{'id':'p1','quote':'древний охотник'}]
        with self.assertRaisesRegex(AnalysisInvalidError,'exact'):checked_ranges(bad,self.request)

    def test_heading_alone_does_not_prove_an_opening_register(self):
        request=copy.deepcopy(self.request);request['targets'][0]['kind']='heading'
        with self.assertRaisesRegex(AnalysisInvalidError,'heading or separator'):checked_ranges(self.answer,request)

    def test_critic_disagreement_requires_adjudication(self):
        critic=copy.deepcopy(self.answer);critic['ranges'][0].update(role='main_story',register='ordinary')
        result=reconcile(self.answer,critic,self.request)
        self.assertEqual('needs_adjudication',result['by_source']['p1']['status'])
        with self.assertRaises(SpeechHoldError):requested_voice(result['by_source']['p1'],[], 'baseline')

    def test_missing_opening_voice_never_falls_back_silently(self):
        frame=reconcile(self.answer,self.answer,self.request)['by_source']['p1']
        with self.assertRaises(SpeechHoldError):requested_voice(frame,[{'id':'n','family':'narrator_baseline'}],'n')
        self.assertEqual('open',requested_voice(frame,[{'id':'open','family':'narrator_opening'}],'n'))

    def test_automatic_adjudication_has_its_own_provenance(self):
        critic=copy.deepcopy(self.answer);critic['ranges'][0].update(role='interlude')
        report=reconcile(self.answer,critic,self.request)
        final=adjudicate(report,self.answer,self.request)
        self.assertEqual('adjudicated_proposal',final['by_source']['p1']['status'])
        self.assertEqual('checked_proposal',final['by_source']['p3']['status'])
        self.assertEqual(self.answer,final['adjudication'])

if __name__=='__main__':unittest.main()
