"""Historical combined context gates; synthetic CPU fixtures, no model calls."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import context_public_demo_historical_word_recovery as m
from rimsky.speech_quality import transcript_check,word_tokens


class HistoricalWordContext(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)
        self.units=[];offset=0
        for index,text in enumerate(['Они пришли. ','Там цуцык спит. ','Тут светло.']):
            self.units.append({'id':'fixture'+str(index),'source_id':'p','start':offset,'end':offset+len(text),
                               'text':text,'spoken':text.strip(),'synthesis_text':text.strip(),'chapter_id':'c'})
            offset+=len(text)
        self.unit=self.units[1];self.assignment={'language':'Russian','units':self.units};self.blocks={'p':''.join(u['text'] for u in self.units)}
        self.facts=[{'url':'https://orfo.ruslang.ru/abc/fixture','headword':'цу́цик',
                     'fact':'Synthetic historical lexeme/spelling fixture, not a dictionary quotation.','retrieval_date':'2026-10-23'}]
        start=self.unit['synthesis_text'].index('цуцык')
        self.patch={'source_start':self.unit['start']+start,'source_end':self.unit['start']+start+5,'source_surface':'цуцык',
                    'native_start':start,'native_end':start+5,'before':'цуцык','after':'цу́цик',
                    'kind':'historical-ts-spelling','dictionary_facts':self.facts}
        row={k:self.unit[k] for k in ('id','source_id','start','end')}
        row.update(text_sha256=m.renderer.sha(self.unit['text']),native_before=self.unit['synthesis_text'],
                   native_after='Там цу́цик спит.',patches=[self.patch])
        self.repair=m.words.repair(self.unit,row)

    def bound(self):
        windows=m.candidates(self.assignment,self.blocks,self.unit,self.repair)
        return {'language':'Russian','word_repair':self.repair,'source_paragraph':self.blocks['p'],'candidates':windows,
                'attempts':[{'context_id':windows[i%len(windows)]['id'],'profile':list(m.PROFILES)[i]} for i in range(3)]}

    def checks(self,kind,expected,a,b):
        directory=m.attempt_home(self.folder,self.unit,1)
        for recognizer,heard in [('qwen',a),('turbo',b)]:
            m.immutable(directory/(kind+'-'+recognizer+'.json'),{'heard':heard,'verifier':{'recognizer':recognizer}})
        return lambda check,*args:transcript_check(expected,check['heard'],'a'*64,'b'*64)

    def decision(self,kind,a,b):
        bound=self.bound();expected=bound['candidates'][0]['spoken'] if kind=='context' else self.unit['spoken']
        callback=self.checks(kind,expected,a,b)
        with patch.object(m,'verification_audio',return_value=(bound,expected,'audio','a'*64)),patch.object(m,'check_record',side_effect=callback):
            return m.blind_decision(self.folder,self.unit,1,kind)

    def test_original_letter_stress_projection_separate_from_approved_native(self):
        original=copy.deepcopy(self.assignment);windows=m.candidates(self.assignment,self.blocks,self.unit,self.repair)
        self.assertEqual(windows[0]['synthesis_text'],'Они пришли. Там цу́цик спит. Тут светло.')
        self.assertEqual(windows[0]['source_stress_projection'],'Они пришли. Там цу́цык спит. Тут светло.')
        self.assertEqual(windows[0]['spoken'],'Они пришли. Там цуцык спит. Тут светло.')
        self.assertEqual(self.assignment,original)
        self.assertEqual([s['id'] for s in windows[0]['source_spans']],['fixture0','fixture1','fixture2'])

    def test_unsupported_letter_change_wrong_offsets_and_stress_only_reject(self):
        for mutate in (
            lambda r:r['patches'][0].update(after='су́цик'),
            lambda r:r['patches'][0].update(source_start=r['patches'][0]['source_start']+1),
            lambda r:r['patches'][0].update(dictionary_facts=[]),
            lambda r:r['patches'][0].update(kind='stress-only'),
            lambda r:r.update(synthesis_text='Там щенок спит.'),
        ):
            bad=copy.deepcopy(self.repair);mutate(bad)
            with self.subTest(bad=bad),self.assertRaises(ValueError):m.validate_repair(self.unit,bad)

    def test_both_full_context_modern_slots_disclosed_raw_difference(self):
        heard='Они пришли. Там цуцик спит. Тут светло.'
        result=self.decision('context',heard,heard)
        self.assertTrue(result['passed'])
        self.assertTrue(all(e['raw']['status']!='asr_exact_match' for e in result['evidence']))
        self.assertEqual(len([e for e in result['equivalences'] if e['rule']=='reviewed-source-bound-historical-hard-ts-spelling-v1']),2)
        self.assertEqual(result['tolerated_deviations'],[])

    def test_crop_modern_slots_require_both_and_no_consonant_or_ordinary_word_waiver(self):
        heard='Там цуцик спит.'
        self.assertTrue(self.decision('crop',heard,heard)['passed'])
        for a,b in [('Там суцик спит.',heard),(heard,'Там цуцык спит.'),('Там цуцик ест.',heard),('Там спит.',heard)]:
            with tempfile.TemporaryDirectory() as temp:
                old=self.folder;self.folder=Path(temp)
                try:
                    with self.subTest(a=a,b=b):self.assertFalse(self.decision('crop',a,b)['passed'])
                finally:self.folder=old

    def test_full_context_cannot_omit_target_even_when_crop_text_would_match(self):
        result=self.decision('context','Они пришли. Тут светло.','Они пришли. Там цуцик спит. Тут светло.')
        self.assertFalse(result['passed'])

    def approved(self):
        bound={'target':self.unit,'repair':self.repair,'dictionary':self.facts,'phoneme_evidence':{}}
        proposal={'providers':[]}
        original=m.words.home(self.folder,self.unit)
        m.immutable(original/'campaign.json',{'fixture':True})
        m.immutable(original/'proposal.json',{'fixture':True})
        candidate=m.words.proposal_request({**bound,'phoneme_evidence':{}})['request']['candidate']
        answer={'accepted':True,'reason':'Synthetic identical-lexeme evaluation.','synthesis_text':candidate['synthesis_text'],
                'patches':candidate['patches'],'identity_only':True,'audio_approval':False}
        for index,(provider,model,effort) in enumerate(m.isolated.PROVIDERS):
            path=self.folder/('answer-'+str(index)+'.json')
            m.immutable(path,{'answer':answer,'request_sha256':str(index)*64,'metadata':{'provider':provider,'model_used':model,'effort':effort},'audio_approval':False})
            proposal['providers'].append({'answer':path.relative_to(self.folder).as_posix(),'answer_sha256':m.file_digest(path),'request_sha256':str(index)*64})
        return bound,proposal,{'models':{'silero':'frozen','ruaccent':'frozen'},'audio_approval':False}

    def test_normative_facts_stress_and_two_original_votes_pinned(self):
        bound,proposal,stress=self.approved();proof=m.normative_evidence(self.folder,self.unit,bound,proposal,stress)
        self.assertEqual(proof['dictionary_sha256'],m.renderer.sha(self.facts));self.assertEqual(proof['stress_sha256'],m.renderer.sha(stress))
        self.assertEqual([p['model'] for p in proof['providers']],['gpt-6-luna','claude-haiku-5-5'])
        self.assertFalse(proof['audio_approval']);self.assertFalse(proof['acoustic_stress_approval'])

    def test_mismatched_normative_evidence_and_missing_review_reject(self):
        bound,proposal,stress=self.approved()
        for changed in ([],[{**self.facts[0],'fact':'Different evidence not approved by the original reviewers.'}]):
            with self.subTest(changed=changed),self.assertRaisesRegex(ValueError,'normative evidence'):
                m.normative_evidence(self.folder,self.unit,{**bound,'dictionary':changed},proposal,stress)
        for rows in (proposal['providers'][:1],proposal['providers']+[proposal['providers'][0]]):
            with self.assertRaisesRegex(ValueError,'exactly two'):m.normative_evidence(self.folder,self.unit,bound,{'providers':rows},stress)
        bad=copy.deepcopy(proposal);bad['providers'][0]['request_sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'review model'):m.normative_evidence(self.folder,self.unit,bound,bad,stress)

    def test_new_namespace_does_not_reset_existing_combined_attempts(self):
        self.assertNotEqual(m.home(self.folder,self.unit),m.predecessor.home(self.folder,self.unit))
        old=m.predecessor.attempt_home(self.folder,self.unit,1)/'native-receipt.json';m.immutable(old,{'failure':'preserved'})
        with self.assertRaisesRegex(ValueError,'do not reset'):m.no_conflicting_context_attempts(self.folder,self.unit)

    def test_actual_decoder_contract_unchanged_except_versioned_identity(self):
        bound={'language':'Russian','qwen_files':{}}
        torch=m.importlib.metadata.version('torch');transformers=m.importlib.metadata.version('transformers')
        old=m.predecessor.verifier_config(bound,'turbo',torch,transformers);new=m.verifier_config(bound,'turbo',torch,transformers)
        self.assertEqual({k:v for k,v in old.items() if k not in ('policy','implementation_sha256')},
                         {k:v for k,v in new.items() if k not in ('policy','implementation_sha256')})
        self.assertEqual(new['policy'],m.POLICY);self.assertFalse(new['expected_text_given_to_recognizer'])


if __name__=='__main__':unittest.main()
