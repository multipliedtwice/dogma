"""V15 source/accepted-supplement/strict historical slot contracts; CPU only."""
import copy
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import context_public_demo_reviewed_reference_recovery as m
from rimsky.speech_quality import transcript_check

class SupplementContext(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.folder=Path(self.tmp.name)
        self.units=[];offset=0
        for uid,text in [('u0070','Ранее. '),('u0071','Он с цыгаркой. '),('u0072','Она светится.')]:
            self.units.append(dict(id=uid,source_id='p',start=offset,end=offset+len(text),text=text,spoken=text.strip(),
                synthesis_text=text.strip(),chapter_id='c',voice='same',mood='neutral',speaker='crowd',mode='speech',palette_reference={'private':'same'}))
            offset+=len(text)
        self.unit=self.units[1];self.assignment={'language':'Russian','units':self.units};self.blocks={'p':''.join(u['text'] for u in self.units)}
        self.facts=[dict(url='https://orfo.ruslang.ru/fixture',headword='цига́рка',fact='Synthetic test only',retrieval_date='2026-10-24')]
        start=self.unit['synthesis_text'].index('цыгаркой');row={k:self.unit[k] for k in ('id','source_id','start','end')}
        row.update(text_sha256=m.renderer.sha(self.unit['text']),native_before=self.unit['synthesis_text'],native_after='Он с цига́ркой.',
            patches=[dict(source_start=self.unit['start']+start,source_end=self.unit['start']+start+8,source_surface='цыгаркой',
                native_start=start,native_end=start+8,before='цыгаркой',after='цига́ркой',kind='historical-ts-spelling',dictionary_facts=self.facts)])
        self.repair=m.words.repair(self.unit,row)
    def test_only_exact_target_next_source_and_accepted_native(self):
        original=copy.deepcopy(self.assignment);c=m.candidates(self.assignment,self.blocks,self.unit,self.repair)
        self.assertEqual(len(c),1);self.assertEqual([u['id'] for u in c[0]['source_spans']],['u0071','u0072'])
        self.assertEqual(c[0]['synthesis_text'],'Он с цига́ркой. Она светится.')
        self.assertEqual(c[0]['spoken'],'Он с цыгаркой. Она светится.')
        self.assertEqual(c[0]['source_stress_projection'],'Он с цыга́ркой. Она светится.')
        self.assertEqual(original,self.assignment)
    def test_different_voice_nonadjacent_or_changed_source_rejects(self):
        for key,value in [('voice','other'),('mood','other'),('source_id','other'),('start',99),('id','u0073')]:
            bad=copy.deepcopy(self.assignment);bad['units'][2][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):m.candidates(bad,self.blocks,bad['units'][1],self.repair)
        with self.assertRaises(ValueError):m.candidates(self.assignment,{'p':'edited'},self.unit,self.repair)
    def decision(self,kind,a,b):
        windows=m.candidates(self.assignment,self.blocks,self.unit,self.repair)
        bound={'language':'Russian','word_repair':self.repair,'source_paragraph':self.blocks['p'],'candidates':windows,
            'attempts':[dict(context_id=windows[0]['id'],profile=k) for k in m.PROFILES]}
        expected=self.unit['spoken'] if kind=='crop' else windows[0]['spoken']
        directory=m.attempt_home(self.folder,self.unit,1)
        for name,text in [('qwen',a),('turbo',b)]:m.immutable(directory/(kind+'-'+name+'.json'),{'heard':text,'verifier':{}})
        with patch.object(m,'verification_audio',return_value=(bound,expected,'audio','a'*64)),patch.object(m,'check_record',
                side_effect=lambda c,*args:transcript_check(expected,c['heard'],'a'*64,'b'*64)):
            return m.blind_decision(self.folder,self.unit,1,kind)
    def test_both_original_source_historical_slots_literal_modern_required(self):
        result=self.decision('context','Он с цигаркой. Она светится.','Он с цигаркой. Она светится.')
        self.assertTrue(result['passed']);self.assertEqual(result['tolerated_deviations'],[])
        self.assertTrue(all(x['raw']['status']!='asr_exact_match' for x in result['evidence']))
        self.assertFalse(self.decision('crop','Он с сигаркой.','Он с цигаркой.')['passed'])
    def test_full_context_cannot_omit_target_or_next(self):
        self.assertFalse(self.decision('context','Она светится.','Он с цигаркой. Она светится.')['passed'])
    def test_supplement_acceptance_required_not_old_rejected_proposal(self):
        with patch.object(m.supplement,'prepared_bound',return_value=({'accepted':'supplement'},{'old':'rejected'})),patch.object(
                m.supplement,'validated_proposal',side_effect=ValueError('Substantive rejection retained')):
            with self.assertRaisesRegex(ValueError,'rejection'):m.approved_campaign(self.folder,self.unit)
    def test_wrong_runtime_versions_cannot_self_validate(self):
        bound={'language':'Russian','qwen_files':{}};config=m.verifier_config(bound,'qwen',m.importlib.metadata.version('torch'),m.importlib.metadata.version('transformers'));config['torch']='forged'
        with self.assertRaisesRegex(ValueError,'config changed'):m.check_record({'verifier':config},'source','a'*64,bound,'qwen')
    def test_namespace_and_slots_are_separate_append_only(self):
        self.assertNotEqual(m.home(self.folder,self.unit),m.supplement.home(self.folder,self.unit))
        p=self.folder/'takes/u0071/take-33.json';m.immutable(p,{'spec':{'other_marker':{}}})
        with self.assertRaises(ValueError):m.no_conflicting_context_attempts(self.folder,self.unit)
    def test_exact_pcm_coordinates_not_acoustic_approval(self):
        import numpy as np
        self.assertIs(m.crop_bounds,m.context.crop_bounds)
        audio=np.zeros(2000,dtype=np.int16);audio[300:600]=1000;audio[1000:1300]=2000
        cues=[dict(text='Он',start=.3,end=.6),dict(text='там',start=1,end=1.3)]
        bounds=m.crop_bounds(audio,1000,cues,dict(spoken='Он там',target_word_start=0,target_word_end=1))
        self.assertLessEqual(bounds['start_sample'],300);self.assertGreaterEqual(bounds['end_sample'],600)
        self.assertEqual(bounds['policy']['processing'],'Exact contiguous PCM16 sample slice; no fades, gain or resampling')
    def test_normative_proof_keeps_new_facts_old_rejection_and_both_acceptances(self):
        old={'repair':self.repair,'dictionary':self.facts};stress={'models':'approved','audio_approval':False}
        bound={'stress':stress,'old_campaign_sha256':'a'*64,'supplement_sha256':'b'*64,
            'supplement':{'facts':[{'new':'material primary evidence'}],'prior_rejection':{'provider_answer':{'sha256':'c'*64}}}}
        proposal={'providers':[]};base=m.supplement.home(self.folder,self.unit)
        m.immutable(base/'campaign.json',bound);m.immutable(base/'proposal.json',{})
        for n,(provider,model,effort) in enumerate(m.isolated.PROVIDERS):
            p=base/('answer-'+str(n)+'.json');m.immutable(p,{'metadata':dict(provider=provider,model_used=model,effort=effort)})
            proposal['providers'].append(dict(answer=p.relative_to(self.folder).as_posix(),answer_sha256=m.file_digest(p),request_sha256=str(n)*64))
        with patch.object(m.supplement,'prepared_bound',return_value=(bound,old)),patch.object(m.supplement,'validated_proposal',return_value=proposal) as approvals:
            proof=m.normative_evidence(self.folder,self.unit,old,proposal,stress)
            self.assertEqual(approvals.call_count,1)
            self.assertEqual(proof['old_rejection_sha256'],'c'*64);self.assertTrue(proof['old_rejection_retained'])
            self.assertEqual(proof['dictionary'],[*self.facts,*bound['supplement']['facts']])
            self.assertEqual([x['model'] for x in proof['providers']],['gpt-6-luna','claude-haiku-5-5'])
            self.assertFalse(proof['audio_approval']);self.assertFalse(proof['acoustic_stress_approval'])
            with self.assertRaises(ValueError):m.normative_evidence(self.folder,self.unit,old,proposal,{'different':'stress'})

if __name__=='__main__':unittest.main()
