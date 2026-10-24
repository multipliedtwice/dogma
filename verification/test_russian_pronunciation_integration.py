"""Final synthesis-only phonetics; CPU fixtures without CLI, GPU or recognizers."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import render_higgs_book_trial as trial
import prepare_public_demo_pronunciation_v4 as public
import freeze_public_audiobook_demo as freeze
import debug_higgs_one_shot as debug
from rimsky import russian_pronunciation as rules


TEXT='Никого не видел: коготь и коготки.'
MARKED='Никого́ не ви́дел: ко́готь и ко́готки.'
AUTO={'policy':'stress-rare-v4','yo_restoration':{'unbound':[]},'homograph_overrides':{'unbound':[]}}


class PhoneticIntegration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.out=Path(self.temp.name)
    def envelope(self,before=MARKED):
        after,records=rules.apply(before)
        return after,{'rule':rules.RULE,'prephonetic_source':before,'records':records,
                      'module_sha256':freeze.file_digest(Path(rules.__file__))}
    def test_private_plan_transforms_only_after_final_stress_and_binds_actual_request(self):
        unit={'id':'u1','chapter_id':'c0001','source_id':'p1','start':0,'end':len(TEXT),'text':TEXT,
              'mode':'narrator','voice':'narrator_baseline','speaker':'narrator','mood':'neutral','spoken_number_policy':'raw-numbers-v1'}
        assignment={'units':[unit],'pronunciation':{'policy':'stress-rare-v4','names':{},'common':[]}}
        path=self.out/'assignment.json';freeze.immutable(path,assignment)
        pack={'files':{'model':'model-hash'},'speech':{'model':'model'},'voices':[{}]}
        def synthesis(pack,voice,text,*args):
            return {'backend':'higgs_audio_cpp','text':text,'prompt':text,'sampling':{'seed':1},'max_tokens':512,
                    'codec_frame_rate':25,'reference':{'audio':'ref.wav','text':'Reference.'},'chunk_mode':'tag_aware','chunk_size':65536}
        with patch.object(trial,'make_packs',return_value=({'narrator_baseline':pack},{})),\
             patch.object(trial,'profile_pack',return_value=pack),patch('rimsky.recovery_decoding.execution_pack',return_value=pack),\
             patch('rimsky.auto_pronunciation.auto_pronunciation',return_value=(MARKED,copy.deepcopy(AUTO))) as stress,\
             patch('rimsky.auto_pronunciation.policy_options',return_value={}),\
             patch.object(trial,'synthesis_plan',side_effect=synthesis),patch.object(trial,'validate_voice_continuity'):
            context=trial.prepare(path,self.out/'plan',['c0001'])
        stress.assert_called_once();self.assertEqual(stress.call_args.args[0],TEXT)
        row=trial.read(context['path'])['units'][0];native=row['synthesis']
        self.assertEqual(row['text'],TEXT);self.assertEqual(row['spoken'],TEXT)
        self.assertEqual(native['text'],'Никаво́ не ви́дел: ко́готь и ко́готки.')
        self.assertEqual(native['russian_phonetics'],self.envelope()[1])
        self.assertEqual(trial.sequence_request('u1',native)['text'],native['text'])
    def test_public_assignment_keeps_source_and_applies_same_final_stage(self):
        unit={'id':'u1','chapter_id':'c0001','source_id':'p1','start':0,'end':len(TEXT),'text':TEXT,'spoken':TEXT}
        analysis={'unit':'u1','auto':[],'context':[],'regular':[],'stats':{}}
        with patch.object(public,'analyse',return_value=analysis),\
             patch.object(public,'auto_pronunciation',return_value=(MARKED,copy.deepcopy(AUTO))):
            rows,details=public.prepare_rows([unit],{},[],{'overrides':[]},self.out/'prepared')
        result=rows[0]
        self.assertEqual(result['text'],TEXT);self.assertEqual(result['spoken'],TEXT)
        self.assertEqual(result['synthesis_text'],self.envelope()[0])
        self.assertEqual(result['pronunciation']['russian_phonetics'],self.envelope()[1])
        self.assertEqual(unit['spoken'],TEXT)
    def test_freeze_keeps_strict_unrecorded_letter_gate_and_recomputes_record(self):
        after,record=self.envelope()
        freeze.validate_synthesis_pronunciation(TEXT,after,record)
        with self.assertRaises(ValueError):freeze.validate_synthesis_pronunciation(TEXT,after)
        for changed in ({'module_sha256':'0'*64},{'rule':'global-g-to-v'},
                        {'prephonetic_source':MARKED.replace('ви́дел','зна́л')},{'records':[]}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                freeze.validate_synthesis_pronunciation(TEXT,after,{**record,**changed})
        with self.assertRaises(ValueError):freeze.validate_synthesis_pronunciation(TEXT,after.replace('ко́готь','ка́воть'),record)
    def test_debug_preflight_rechecks_module_proof_and_actual_native_text(self):
        after,record=self.envelope()
        ready={'native_manifest':{'speech_model':{'sha256':'model'},'runner':{'sha256':'runner'}}}
        plan={'backend':'higgs_audio_cpp','effects_policy':'disabled','model_sha256':'model','runner_sha256':'runner',
              'reference':{},'text':after,'prompt':after,'russian_phonetics':record}
        with patch.object(debug,'validate_reference'):
            debug.validate_synthesis(plan,ready)
            for changed in ({'prompt':MARKED},{'text':MARKED},{'russian_phonetics':{**record,'module_sha256':'0'*64}}):
                with self.subTest(changed=changed),self.assertRaises(ValueError):debug.validate_synthesis({**plan,**changed},ready)


if __name__=='__main__':unittest.main()
