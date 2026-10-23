import copy,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import write_json,file_digest,digest
from higgs_trial_io import read_json as read
from higgs_edge_recovery import recover

class EdgeRecoveryTests(unittest.TestCase):
    def test_numeric_surface_stage_does_not_render_text_without_digits(self):
        from higgs_exhausted_word_recovery import number_surface_recovery
        original={'id':'line','text':'Нет чисел.'}
        selected={'line':{'unit':original,'word_check':{'status':'spoken_text_mismatch'}}}
        with tempfile.TemporaryDirectory() as tmp,patch('higgs_exhausted_word_recovery.render_candidate') as render:
            result=number_surface_recovery(Path(tmp),{'units':[original]},selected,{})
            render.assert_not_called();self.assertEqual(result,selected)
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_bound_punctuation_take_recovers_a_clipped_fragment_without_padding_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);failed=out/'take-0';failed.mkdir();wav=failed/'line.wav'
            sf.write(wav,np.r_[np.zeros(2000),np.full(2000,.1)],24000)
            write_json(failed/'receipts.json',{'takes':{'line':{'audio':'line.wav','audio_sha256':file_digest(wav),'key':'failed'}}})
            original={'id':'line','text':'Гундахар пересек рубку','speaker':'narrator','voice':'narrator_baseline','mood':'calm'}
            def render_candidate(path,take,chapter,render,guard):
                unit=copy.deepcopy(read(path)['units'][0]);unit['spoken']=unit['spoken_text_plan']['native_text'];unit['synthesis']={'text':unit['spoken']}
                key=digest(unit['synthesis']);audio=take/'line.wav';sf.write(audio,np.r_[np.zeros(2000),np.full(2000,.1),np.zeros(2000)],24000)
                write_json(take/'render-plan.json',{'units':[unit]});write_json(take/'receipts.json',{'takes':{'line':{'audio':'line.wav','audio_sha256':file_digest(audio),'key':key}}})
            def audit(take):
                receipt=read(take/'receipts.json')['takes']['line']
                return {'checks':{'line':{'status':'asr_exact_match','audio_sha256':receipt['audio_sha256'],'synthesis_key':receipt['key']}}}
            with patch('higgs_exhausted_word_recovery.render_candidate',side_effect=render_candidate),patch('higgs_exhausted_word_recovery.audit_candidate',side_effect=audit):
                result=recover(out,{'units':[original]},'chapter',{},None)
            self.assertEqual(result['line']['unit']['text'],original['text'])
            self.assertEqual(result['line']['unit']['spoken'],original['text']+',')
            self.assertFalse(result['line']['edge']['unsafe_end'])
            # Changing the failed evidence is refused, rather than using it as
            # permission to synthesize an unbound repair.
            sf.write(wav,np.zeros(4000),24000)
            with self.assertRaisesRegex(ValueError,'evidence changed'):recover(out,{'units':[original]},'chapter',{},None)

if __name__=='__main__':unittest.main()
