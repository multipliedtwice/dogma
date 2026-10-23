import sys,tempfile,unittest
import subprocess
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import higgs_exhausted_word_recovery as recovery
from rimsky.util import digest,file_digest,write_json
from rimsky.speech_quality import transcript_check


class ExhaustedWordRecoveryTests(unittest.TestCase):
    def test_tertiary_failure_and_absent_safe_candidates_do_not_skip_other_remedies(self):
        for empty in (False,True):
            with self.subTest(empty=empty),tempfile.TemporaryDirectory() as work:
                out=Path(work);assignment,selected=self.fixture(out)
                with patch('render_higgs_palette_preview.wait_for_audio_resources'),\
                     patch.object(recovery.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['audit'])),\
                     patch.object(recovery,'native_recovery',return_value=selected) as native,\
                     patch.object(recovery,'decoding_recovery',return_value=selected) as decoding:
                    if empty:
                        with patch.object(recovery,'candidates',return_value={}):result=recovery.recover(out,assignment,selected,groups={})
                    else:result=recovery.recover(out,assignment,selected,groups={})
                native.assert_called_once();decoding.assert_called_once()
                self.assertEqual(result,selected)

    def test_zero_output_attempt_and_primary_audit_failure_stay_unresolved(self):
        with tempfile.TemporaryDirectory() as work:
            take=Path(work)
            self.assertIsNone(recovery.audit_candidate(take))
            write_json(take/'receipts.json',{'takes':{}})
            self.assertIsNone(recovery.audit_candidate(take))
            write_json(take/'render-plan.json',{'units':[]})
            write_json(take/'receipts.json',{'takes':{'one':{}}})
            with patch('render_higgs_palette_preview.cached_audit',return_value=None),\
                 patch('render_higgs_palette_preview.wait_for_audio_resources'),\
                 patch.object(recovery.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['audit'])):
                self.assertIsNone(recovery.audit_candidate(take))
            self.assertTrue((take/'candidate-audit-failure.json').exists())

    def fixture(self,folder):
        wav=folder/'line.wav'
        sf.write(wav,np.concatenate([np.zeros(2400),np.full(2400,.03),np.zeros(2400)]),24000)
        unit={'id':'line','text':'one two','spoken':'one two','speaker':'actor','voice':'male','mood':'calm',
            'palette_reference':{'audio_sha256':'reference'},
            'palette_assignment':{'allowed_reference_sha256':['reference']},
            'synthesis':{'text':'one two','prompt':'one two'}}
        key=digest(unit['synthesis']);sha=file_digest(wav)
        row={'unit':unit,'path':str(wav),'receipt':{'key':key,'audio_sha256':sha},
            'word_check':{**transcript_check('one two','one',sha,'primary'),'synthesis_key':key}}
        assignment={'units':[unit]};return assignment,{'line':row}

    def test_exact_independent_witness_resolves_but_near_match_does_not(self):
        for heard,expected in [('one two','asr_exact_match'),('one too','spoken_text_mismatch')]:
            with self.subTest(heard=heard),tempfile.TemporaryDirectory() as work:
                out=Path(work);assignment,selected=self.fixture(out)
                def recognise(command,**kwargs):
                    report=out/'secondary-asr-large-v3-turbo/report.json';row=selected['line']
                    witness={**transcript_check('one two',heard,row['receipt']['audio_sha256'],'independent'),
                        'unit_id':'line','synthesis_key':row['receipt']['key']}
                    write_json(report,{'verifier':{'model':'audio-only'},'checks':{'take':witness}})
                with patch('render_higgs_palette_preview.wait_for_audio_resources'),patch.object(recovery.subprocess,'run',side_effect=recognise):
                    result=recovery.recover(out,assignment,selected)
                self.assertEqual(result['line']['word_check']['status'],expected)
                self.assertEqual(selected['line']['word_check']['heard'],'one')
                if heard=='one two':self.assertFalse(result['line']['word_check']['human_approval'])

    def test_changed_palette_or_native_plan_is_rejected_before_recognition(self):
        with tempfile.TemporaryDirectory() as work:
            out=Path(work);assignment,selected=self.fixture(out)
            selected['line']['unit']['palette_reference']['audio_sha256']='other'
            with self.assertRaisesRegex(ValueError,'palette'):recovery.candidates(out,assignment,selected)
            selected['line']['unit']['palette_reference']['audio_sha256']='reference'
            selected['line']['unit']['synthesis']['text']='one'
            with self.assertRaisesRegex(ValueError,'synthesis'):recovery.candidates(out,assignment,selected)

    def test_refreshed_exhausted_diagnosis_gets_content_addressed_take(self):
        with tempfile.TemporaryDirectory() as work:
            out=Path(work);assignment,_=self.fixture(out)
            first={**assignment,'units':[{**assignment['units'][0],
                'spoken_text_plan':{'expected_spoken':'one two','native_text':'one two',
                                    'evidence':{'diagnosis_sha256':'old'}}}]}
            take,path,candidate=recovery.exhausted_recovery_take(out,0,first,assignment)
            write_json(path,candidate)
            refreshed={**assignment,'units':[{**assignment['units'][0],
                'spoken_text_plan':{'expected_spoken':'one two','native_text':'one, two',
                                    'evidence':{'diagnosis_sha256':'new'}}}]}
            revised_take,revised_path,revised=recovery.exhausted_recovery_take(
                out,0,refreshed,assignment)
            self.assertEqual(take.name,'take-0')
            self.assertRegex(revised_take.name,r'^take-0-[0-9a-f]{12}$')
            self.assertNotEqual(path,revised_path)
            self.assertEqual(refreshed,revised)
            self.assertEqual(first,recovery.read(path))


if __name__=='__main__':unittest.main()
