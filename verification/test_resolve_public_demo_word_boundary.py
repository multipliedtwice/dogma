"""Pure/source-bound recognition separator tests; no TTS or ASR inference."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_word_boundary as adapter


class WordBoundary(unittest.TestCase):
    def fixture(self,source=None,qwen=None,whisper=None):
        source=source or '— Насчет дров с Митрий Митричем, — и вдруг эта подлая ни с того, ни с сего за палец… '
        prefix='Он сказал. '
        unit={'id':'u1','source_id':'p1','start':len(prefix),'end':len(prefix)+len(source),'text':source,'spoken':source.strip(),
              'synthesis_text':source.strip(),'spoken_conversion':{'source':source,'expanded':source,'changes':[]}}
        paragraph=prefix+source
        raw_qwen=qwen or 'Насчет дров Смитрий Митричич, и вдруг это подлое ни с того ни с сего за палец.'
        raw_whisper=whisper or 'Насчет дров с Митримитричем, и вдруг это подлое ни с того ни с сего за палец?'
        evidence=[]
        for role,family,model,heard in [('primary','qwen','Qwen3-ASR-1.7B',raw_qwen),
                                       ('witness','whisper','openai/whisper-large-v3-turbo',raw_whisper)]:
            config={'model':model,'expected_text_given_to_recognizer':False,'local_files_only':True}
            raw=adapter.base.transcript_check(unit['spoken'],heard,'a'*64,adapter.base.sha(config))
            comparison=adapter.base.russian_equivalence(unit,heard,paragraph,'a'*64,raw['verifier_sha256'])
            evidence.append({'role':role,'family':family,'verifier':config,'raw':raw,'comparison':comparison,
                             'path':'checks/'+family+'.json','sha256':family[0]*64})
        decision={'passed':False,'status':'unresolved','resolution':'held','evidence':evidence,'equivalences':[],'tolerated_deviations':[]}
        return unit,paragraph,decision

    def test_source_anchor_exact_separator_and_existing_both_model_rules(self):
        unit,paragraph,old=self.fixture();snapshot=copy.deepcopy(old)
        decision,proof=adapter.adjudicate(unit,old,paragraph,'a'*64)
        self.assertTrue(decision['passed']);self.assertFalse(old['evidence'][0]['comparison']['passed'])
        self.assertEqual(old,snapshot);self.assertEqual(decision['evidence'],old['evidence'])
        primary=next(row for row in proof['recognizers'] if row['role']=='primary')
        witness=next(row for row in proof['recognizers'] if row['role']=='witness')
        self.assertIsNotNone(primary['split']);self.assertIsNone(witness['split'])
        split=primary['split'];raw=split['raw_heard'].encode('utf-8');offset=split['utf8_byte_offset']
        self.assertEqual(split['derived_heard'].encode('utf-8'),raw[:offset]+b' '+raw[offset:])
        self.assertIn('С митрий',split['derived_heard']);self.assertEqual(split['anchor']['name'],'Митрий')
        self.assertEqual(paragraph[split['anchor']['name_start']:split['anchor']['name_end']],'Митрий')
        self.assertFalse(proof['human_approval']);self.assertTrue(decision['tolerated_deviations'])
        self.assertTrue(any(change['rule']==adapter.BOUNDARY_RULE for change in decision['equivalences']))

    def test_missing_preposition_changed_name_letters_and_unanchored_position_never_fixed(self):
        unit,paragraph,decision=self.fixture();item=decision['evidence'][0]
        for text in (item['raw']['heard'].replace('Смитрий','Митрий'),item['raw']['heard'].replace('Смитрий','Смитрей'),
                     'Ну '+item['raw']['heard']):
            bad=copy.deepcopy(item);bad['raw']=adapter.base.transcript_check(unit['spoken'],text,'a'*64,adapter.base.sha(bad['verifier']))
            self.assertIsNone(adapter.comparison_option(unit,paragraph,bad,'a'*64),text)

    def test_lowercase_anchor_duplicate_source_anchor_and_conversion_mismatch_rejected(self):
        unit,paragraph,decision=self.fixture(source='Он с митрий сказал.',qwen='Он Смитрий сказал.',whisper='Он с митрий сказал.')
        self.assertEqual(adapter.source_anchors(unit,paragraph),[])
        # Existing homophone spacing may already pass this simple sentence.
        # Force the original comparison held to exercise NEW anchor admission.
        with patch.object(adapter.base,'russian_equivalence',return_value={'passed':False}):
            self.assertIsNone(adapter.comparison_option(unit,paragraph,decision['evidence'][0],'a'*64))
        unit,paragraph,decision=self.fixture(source='Он с Митрий и с Митрий говорил.',qwen='Он Смитрий и с Митрий говорил.',whisper='Он с Митрий и с Митрий говорил.')
        self.assertEqual(len(adapter.source_anchors(unit,paragraph)),2)
        with patch.object(adapter.base,'russian_equivalence',return_value={'passed':False}):
            self.assertIsNone(adapter.comparison_option(unit,paragraph,decision['evidence'][0],'a'*64))
        unit,paragraph,_=self.fixture();unit['spoken_conversion']['changes']=[{'source':'2','spoken':'два'}]
        self.assertEqual(adapter.source_anchors(unit,paragraph),[])

    def test_single_family_or_no_actual_split_cannot_create_new_verdict(self):
        unit,paragraph,decision=self.fixture()
        for index in (0,1):
            one={**decision,'evidence':[decision['evidence'][index]]}
            self.assertIsNone(adapter.adjudicate(unit,one,paragraph,'a'*64))
        exact=unit['spoken'];unit,paragraph,decision=self.fixture(qwen=exact,whisper=exact)
        self.assertIsNone(adapter.adjudicate(unit,decision,paragraph,'a'*64))

    def test_proof_recompute_rejects_modified_offset_anchor_audio_hash_and_source(self):
        unit,paragraph,decision=self.fixture();item=decision['evidence'][0]
        option=adapter.comparison_option(unit,paragraph,item,'a'*64);split=option['split']
        self.assertEqual(adapter.validate_boundary_proof(unit,paragraph,item,split,'a'*64),option)
        for change in ('offset','anchor','audio','derived'):
            invalid=copy.deepcopy(split)
            if change=='offset':invalid['character_offset']+=1
            elif change=='anchor':invalid['anchor']['name_start']+=1
            elif change=='audio':invalid['audio_sha256']='0'*64
            else:invalid['derived_heard']+=' extra'
            with self.assertRaisesRegex(ValueError,'Boundary proof'):
                adapter.validate_boundary_proof(unit,paragraph,item,invalid,'a'*64)
        with self.assertRaisesRegex(ValueError,'paragraph/unit coordinates'):
            adapter.source_anchors({**unit,'start':unit['start']+1},paragraph)

    def test_cued_config_or_changed_raw_audio_cannot_reach_boundary_comparison(self):
        unit,paragraph,decision=self.fixture()
        for changed in ('audio','prompt'):
            item=copy.deepcopy(decision['evidence'][0])
            if changed=='audio':item['raw']['audio_sha256']='0'*64
            else:item['verifier']['expected_text_given_to_recognizer']=True
            with self.assertRaises(ValueError):adapter.comparison_option(unit,paragraph,item,'a'*64)

    def test_context_source_edit_rejected_and_non_original_contract_delegated(self):
        unit,paragraph,decision=self.fixture();context=adapter.Context.__new__(adapter.Context)
        context.assignment={'language':'Russian','units':[copy.deepcopy(unit)]};context.generation='frozen'
        context.blocks={'p1':paragraph};context.word_boundary_dependencies={}
        receipt={'take':1,'spec':{'source':unit,'generation':'frozen'},'audio_sha256':'a'*64}
        value={'decision':decision,'source_text':unit['text'],'expected_spoken':unit['spoken'],'audio_sha256':'a'*64}
        with patch.object(adapter.previous.Context,'assess',return_value=value):
            with self.assertRaisesRegex(ValueError,'frozen source/audio'):
                context.assess({**unit,'text':'Changed source.'},receipt)
            self.assertTrue(context.assess(unit,receipt)['decision']['passed'])
            self.assertIs(context.assess(unit,{**receipt,'take':12}),value)

    def test_report_preserves_raw_counts_and_existing_disclosure_unit_count(self):
        unit,paragraph,decision=self.fixture();_,proof=adapter.adjudicate(unit,decision,paragraph,'a'*64)
        context=Mock();context.assignment={'units':[unit]};context.word_boundary_dependencies={}
        context.assess.return_value={'word_boundary_provenance':proof}
        summary={'take':1};row={'id':'u1','takes':[summary],'selected':summary}
        value={'units':[row],'resolution_adapter_sha256':'v8','raw_mismatch_check_count':41,
               'resolved_with_disclosure_unit_count':3,'disclosure':'Prior raw evidence.'}
        with patch.object(adapter.previous,'report',return_value=value),patch.object(adapter.base.renderer,'takes',return_value=[{'take':1}]):
            report=adapter.report(context)
        self.assertEqual(report['raw_mismatch_check_count'],41);self.assertEqual(report['resolved_with_disclosure_unit_count'],3)
        self.assertEqual(report['word_boundary_resolved_units'],1)
        self.assertEqual(report['ru_acoustic_adjudication_resolution_adapter_sha256'],'v8')
        before=adapter.base.sha(report);summary['word_boundary_provenance']['audio_sha256']='changed'
        self.assertNotEqual(before,adapter.base.sha(report))

    def test_new_dependency_set_is_separate_and_rechecked(self):
        context=adapter.Context.__new__(adapter.Context)
        context.dependencies={'base':'same'};context.word_boundary_dependencies={'v9':'old'}
        with patch.object(adapter.previous.Context,'recheck') as prior,patch.object(adapter,'dependencies',return_value={'v9':'changed'}):
            with self.assertRaisesRegex(ValueError,'dependencies changed'):context.recheck()
            prior.assert_called_once()
        self.assertEqual(context.dependencies,{'base':'same'})


if __name__=='__main__':unittest.main()
