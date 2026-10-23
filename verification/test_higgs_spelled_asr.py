import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from audit_higgs_spelled_asr import digit_token_ids
from higgs_exhausted_word_recovery import verify_candidates
from rimsky.speech_quality import transcript_check
from rimsky.util import write_json,file_digest,digest
import numpy as np
import soundfile as sf


class SpelledNumberTests(unittest.TestCase):
    def test_vocabulary_restriction_is_universal_and_keeps_special_tokens(self):
        class Tokenizer:
            all_special_ids=[9]
            def get_vocab(self):return {v:k for k,v in {1:'12',2:'три',3:' １８',4:'девять',9:'<|0.00|>'}.items()}
            def decode(self,ids,**kwargs):return {1:'12',2:'три',3:' １８',4:'девять',9:'<|0.00|>'}[ids[0]]
        self.assertEqual(digit_token_ids(Tokenizer()),[1,3])

    def test_wrong_number_or_case_is_not_repaired_from_the_expected_sentence(self):
        for heard,exact in [('с восемнадцати лет',True),('с семнадцати лет',False),('с восемнадцать лет',False)]:
            with self.subTest(heard=heard),tempfile.TemporaryDirectory() as work:
                out=Path(work);wav=out/'line.wav'
                sf.write(wav,np.r_[np.zeros(2400),np.full(2400,.03),np.zeros(2400)],24000)
                sha=file_digest(wav);expected='с восемнадцати лет';synthesis={'text':expected,'prompt':expected};key=digest(synthesis)
                unit={'id':'line','text':expected,'spoken':expected,'speaker':'actor','voice':'male','mood':'calm',
                    'synthesis':synthesis,'palette_reference':{'audio_sha256':'reference'},
                    'palette_assignment':{'allowed_reference_sha256':['reference']}}
                selected={'line':{'unit':unit,'path':str(wav),'receipt':{'key':key,'audio_sha256':sha},
                    'word_check':{**transcript_check(expected,'с восемнадцать лет',sha,'primary'),'synthesis_key':key}}}
                write_json(out/'secondary-asr-old/report.json',{'verifier':{'language':'ru'},'checks':{'one':{
                    **transcript_check(expected,'с 18 лет',sha,'old'),'unit_id':'line','synthesis_key':key}}})
                def recognise(command,**kwargs):
                    self.assertIn('audit_higgs_spelled_asr.py',str(command[4]))
                    # The subprocess receives only selected-file paths; audit
                    # recognition itself never receives an expected transcript.
                    output=Path(command[command.index('--output')+1])
                    witness={**transcript_check(expected,heard,sha,'independent'),'unit_id':'line','synthesis_key':key}
                    write_json(output/'report.json',{'verifier':{'model':'audio-only','language':'ru'},'checks':{'one':witness}})
                with patch('render_higgs_palette_preview.wait_for_audio_resources'),\
                     patch('higgs_exhausted_word_recovery.subprocess.run',side_effect=recognise):
                    result=verify_candidates(out,{'units':[unit]},selected,spell_numbers=True)
                self.assertEqual(result['line']['word_check']['status']=='asr_exact_match',exact)
                self.assertNotEqual(selected['line']['word_check']['status'],'asr_exact_match')


if __name__=='__main__':unittest.main()
