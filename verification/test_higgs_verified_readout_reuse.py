import copy,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import higgs_previous_verified_takes as reuse
from rimsky.util import digest,file_digest,write_json
from rimsky.speech_quality import transcript_check
from render_higgs_book_trial import spoken_text


class VerifiedReadoutReuseTests(unittest.TestCase):
    def test_verified_digit_surface_is_reused_but_changed_value_is_not(self):
        for native,accepted in [('Человек 162-го уровня.',True),('Человек 163-го уровня.',False)]:
            with self.subTest(native=native),tempfile.TemporaryDirectory() as work:
                generation=Path(work)/'audio-palette-test';take=generation/'c1/readout-repair-one'
                take.mkdir(parents=True);out=generation/'c1/chunk-001'
                write_json(generation/'manifest.json',{'source_sha256':'source'})
                ref={'audio_sha256':'reference','text':'Reviewed reference'}
                unit={'id':'line','text':'Человек 162-го уровня.','source_id':'p','start':0,'end':25,
                    'speaker':'narrator','voice':'mechanical_stats','mood':'neutral','mode':'narrator',
                    'palette_assignment':{'template_id':'template','allowed_reference_sha256':['reference']}}
                spoken=spoken_text(unit['text'])
                recipe={'expected_spoken':spoken,'native_text':native,'recovery_kind':'source_number_surface',
                    'evidence':{'native_surface_sha256':digest(native.encode('utf8'))}}
                synthesis={'backend':'higgs_audio_cpp','effects_policy':'disabled','prompt':native,'text':native,
                    'reference':{'sha256':'reference','text':ref['text']},'model_sha256':'model','runner_sha256':'runner',
                    'sampling':{'seed':1},'spoken_text_plan':recipe}
                wav=take/'line.wav';sf.write(wav,np.concatenate([np.zeros(2400),.03*np.sin(np.arange(2400)*.06),np.zeros(2400)]),24000)
                key=digest(synthesis);sha=file_digest(wav)
                old={**unit,'spoken':spoken,'render_voice':'mechanical_stats','audio':'line.wav',
                    'synthesis':synthesis,'key':key,'spoken_text_plan':recipe}
                check={**transcript_check(spoken,spoken,sha,'verifier'),'synthesis_key':key}
                row={'unit':old,'receipt':{'key':key,'audio_sha256':sha},'path':str(wav),'word_check':check}
                write_json(take/'selected.json',{'line':row});write_json(take/'render-plan.json',{'units':[old]})
                reuse._INDEX.clear();reuse._AUDITS.clear()
                with patch('higgs_file_checks.cached_sha',side_effect=['model','runner']),\
                     patch('regenerate_higgs_book_trial.cached_audit',return_value={'checks':{'line':check}}),\
                     patch('higgs_secondary_verification.cached_secondary',side_effect=lambda g,r:r):
                    found=reuse.reuse(out,{'source_sha256':'source','units':[copy.deepcopy(unit)]},
                        {'template':{'moods':{'neutral':[ref]}}})
                self.assertEqual(accepted,'line' in found)
                if accepted:self.assertEqual(recipe,found['line']['unit']['spoken_text_plan'])


if __name__=='__main__':unittest.main()
