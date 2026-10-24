"""Private adapter regressions; CPU fixtures, no provider/native/ASR calls."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import numpy as np,soundfile as sf
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import debug_higgs_one_shot as m
from verification.luna_test_support import metadata_evidence


class DebugOneShot(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def test_actual_adjudicator_usage_is_pinned_not_only_requested_model(self):
        metadata={'provider':'claude_cli','model':'claude-opus-5-5','effort':'high','requested_model':'claude-opus-5-5',
                  'model_usage':{'claude-opus-5-5-20261001':{}},'cli_version':'2.0.fixture'}
        m.validate_adjudication({'metadata':{'batches':[metadata]}})
        m.validate_adjudication({'metadata':{'batches':[]}})  # Automatic-only answers need no model call.
        for changed in ({'model_usage':{'claude-haiku-5-5':{}}},{'cli_version':''},{'requested_model':'other'},{'effort':'low'}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                m.validate_adjudication({'metadata':{'batches':[metadata,{**metadata,**changed}]}})
    def test_existing_preparation_requires_exact_requested_scope(self):
        source=self.root/'old';output=self.root/'new'
        ready={'source':str(source),'chapters':['c0001']};m.write(output/'ready.json',ready)
        self.assertEqual(m.prepare(source,output,['c0001']),ready)
        for wrong_source,chapters in ((source,['c0002']),(self.root/'other',['c0001'])):
            with self.assertRaisesRegex(ValueError,'scope differs'):m.prepare(wrong_source,output,chapters)
    def test_yo_requires_all_ambiguous_luna_while_homograph_stays_opus(self):
        import build_yo_restoration as builder
        metadata=dict(zip(('provider','model','effort'),builder.PIN))|{
            'policy':builder.POLICY,'requested_model':'gpt-6-luna','model_provenance':'explicit-cli-model-selection',
            'cli_version':'fixture','executable':sys.executable,'executable_sha256':m.file_digest(Path(sys.executable)),
            'structured_call_sha256':m.file_digest(Path(builder.codex_analysis.__file__))}
        metadata=metadata_evidence(metadata,self.root/'metadata-evidence')
        answer={'policy':builder.POLICY,'metadata':{'batches':[metadata]},
                'stats':{'ambiguous':1,'ambiguous_rows':1,'ambiguous_auto':0},'decisions':[{'basis':'adjudicated'}]}
        m.validate_adjudication(answer,'yo')
        with self.assertRaises(ValueError):m.validate_adjudication(answer,'homograph')
        for change in ({'policy':'old-opus'},{'stats':{'ambiguous':1,'ambiguous_rows':0,'ambiguous_auto':1}},
                       {'decisions':[{'basis':'auto-agree'}]},
                       {'metadata':{'batches':[]}},
                       {'metadata':{'batches':[{**metadata,'model':'claude-opus-5-5'}]}}):
            with self.subTest(change=change),self.assertRaises(ValueError):m.validate_adjudication({**answer,**change},'yo')
        m.validate_adjudication({'policy':builder.POLICY,'metadata':{'batches':[]},
            'stats':{'ambiguous':0,'ambiguous_rows':0,'ambiguous_auto':0},'decisions':[]},'yo')
    def test_number_policy_precedes_lookup_and_obsolete_automatic_recipe_is_not_rebound(self):
        from render_higgs_book_trial import spoken_text
        unit={'id':'c0003-u0522','text':'В 2058-м году.','voice':'masculine_3','mood':'neutral','render_seed':7,
              'analysis_parts':[{'kind':'speech'}],'retake_number':5,'decoding_profile':'failed-recovery',
              'spoken_text_plan':{'expected_spoken':spoken_text('В 2058-м году.','grouped-digits-v2'),'native_text':'old automatic rewrite','recovery_kind':'opus_word_recovery','evidence':{'diagnosis_sha256':'old'}}}
        original=copy.deepcopy(unit);fresh,proof=m.prepare_unit(unit,'grouped-digits-v3',{'entries':{unit['spoken_text_plan']['expected_spoken']:{'spoken_text_plan':unit['spoken_text_plan']}}})
        self.assertEqual(fresh['text'],unit['text']);self.assertEqual(fresh['voice'],unit['voice']);self.assertEqual(fresh['analysis_parts'],unit['analysis_parts'])
        self.assertEqual(fresh['spoken_number_policy'],'grouped-digits-v3');self.assertNotIn('spoken_text_plan',fresh)
        self.assertNotIn('decoding_profile',fresh);self.assertEqual(fresh['retake_number'],0);self.assertEqual(unit,original)
        self.assertEqual(proof['previous_fields']['spoken_text_plan'],unit['spoken_text_plan'])
    def test_exact_listener_marks_survive_but_changed_source_does_not(self):
        unit={'id':'u','text':'Дом стоит.','spoken_text_plan':{'expected_spoken':'Дом стоит.','native_text':'Дом стои\u0301т.',
            'recovery_kind':'listener_pronunciation','evidence':{'source':'explicit_listener_instruction'}}}
        fresh,_=m.prepare_unit(unit,'grouped-digits-v3',{'entries':{}})
        self.assertEqual(fresh['spoken_text_plan'],unit['spoken_text_plan'])
        unit['text']='Другой дом стоит.'
        with self.assertRaises(ValueError):m.prepare_unit(unit,'grouped-digits-v3',{'entries':{}})
    def test_canonical_assignments_choose_only_final_wrapper_and_reject_ambiguity(self):
        root=self.root/'generation';chunk=root/'c0001/chunk-000';m.write(chunk/'assignment.json',{})
        final=chunk/'presentation-a/number-groups-v2/assignment.json';m.write(final,{})
        m.write(chunk/'take-0/assignment.json',{});m.write(chunk/'word-recovery/assignment.json',{})
        self.assertEqual(m.canonical_assignments(root,'c0001'),[final])
        m.write(chunk/'presentation-b/number-groups-v2/assignment.json',{})
        with self.assertRaises(ValueError):m.canonical_assignments(root,'c0001')
    def test_all_native_plans_reject_effects_wrong_checkpoint_and_changed_letters_not_relevant(self):
        ready={'native_manifest':{'speech_model':{'sha256':'pinned'},'runner':{'sha256':'runner'}}}
        plan={'backend':'higgs_audio_cpp','effects_policy':'disabled','model_sha256':'pinned','runner_sha256':'runner',
              'prompt':'Exact prepared text.','reference':{'audio':'fixture.wav','sha256':'ref'}}
        with patch.object(m.basic,'checked'):
            m.validate_synthesis(plan,ready)
            for key,value in [('prompt','<|prosody:pause|>Exact text.'),('model_sha256','old'),('effects_policy','enabled')]:
                with self.subTest(key=key),self.assertRaises(ValueError):m.validate_synthesis({**plan,key:value},ready)
    def test_nonverbal_old_model_audio_is_not_a_licensed_asset(self):
        plan={'backend':'nonverbal_asset','source':{'license':'CC0-1.0','source_page':'https://opengameart.org/fixture','source_sha256':'x'},
              'reference':{'audio':'resources/nonverbal/dog/fixture.flac','sha256':'x'}}
        with patch.object(m.basic,'checked'):m.validate_synthesis(plan,{})
        for source in ({'license':'model-generated'},dict(plan['source'],source_sha256='wrong')):
            with self.assertRaises(ValueError):m.validate_synthesis({**plan,'source':source},{})
    def test_reference_onset_keeps_reviewed_identity_and_checks_both_actual_files(self):
        from rimsky.reference_onset import padded
        path=self.root/'reviewed.wav';sf.write(path,np.ones(1200,dtype=np.float32)*.1,24000,subtype='PCM_16')
        reference={'audio':path.name,'sha256':m.file_digest(path),'text':'Reviewed words.'}
        derived=padded(reference,self.root,self.root/'derived')
        self.assertEqual(derived['sha256'],reference['sha256'])
        self.assertNotEqual(derived['sha256'],derived['onset_padding']['derived_sha256'])
        with patch.object(m,'ROOT',self.root):
            m.validate_reference(reference);m.validate_reference(derived)
            for changed in ({'policy':'other'},{'source_sha256':'0'*64},{'derived_sha256':'0'*64}):
                bad={**derived,'onset_padding':{**derived['onset_padding'],**changed}}
                with self.subTest(changed=changed),self.assertRaises(ValueError):m.validate_reference(bad)
            raw=path.read_bytes();path.write_bytes(raw+b'changed')
            with self.assertRaises(ValueError):m.validate_reference(derived)
            path.write_bytes(raw)
            target=self.root/derived['audio'];target.write_bytes(target.read_bytes()+b'changed')
            with self.assertRaises(ValueError):m.validate_reference(derived)
    def test_production_guard_precedes_native_process_and_failed_guard_starts_nothing(self):
        with patch.object(m.basic.assets,'wait_for_audio_resources'),patch.object(m.basic,'model_guard',side_effect=ValueError('pin')) as guard,patch.object(m.subprocess,'Popen') as native:
            with self.assertRaisesRegex(ValueError,'pin'):m.native_batch(self.root,{'native_manifest':{}},[],60)
            guard.assert_called_once();native.assert_not_called()
        self.assertFalse((self.root/'native').exists())
    def test_crash_between_start_and_worker_identity_fails_closed_before_collect(self):
        output=self.root/'generation';output.mkdir();m.write(output/'ready.json',{})
        unit={'id':'u','synthesis':{'backend':'higgs_audio_cpp'}};requests=[{'id':'u','text':'Words.'}]
        sid=m.digest({'requests':requests,'units':[unit],'ready_sha256':m.file_digest(output/'ready.json')})
        folder=output/'native'/sid;m.write(folder/'physical-start.json',{'id':sid,'units':[unit]});m.write(folder/'requests.json',{'requests':requests})
        with patch.object(m,'planned',return_value=[unit]),patch('render_higgs_book_trial.sequence_request',return_value=requests[0]),patch.object(m,'collect') as collect:
            with self.assertRaisesRegex(ValueError,'Unknown physical worker'):m.render(output,None,32,60)
            collect.assert_not_called()
    def test_progress_is_unchecked_and_whole_active_edge_pcm_is_playable(self):
        output=self.root/'generation';output.mkdir();assets=output/'assets';assets.mkdir();path=assets/'raw.wav'
        samples=np.ones(2400,dtype=np.int16)*1200;sf.write(path,samples,24000,subtype='PCM_16')
        unit={'id':'u0','chapter_id':'c0001','source_id':'p','text':'Original words.','speaker':'narrator','voice':'same','mood':'neutral','analysis_parts':[{'mode':'narrator'}]}
        m.write(output/'units/u0.json',{'status':'rendered','audio':'assets/raw.wav','audio_sha256':m.file_digest(path)})
        m.write(output/'manifest.json',{'tts_engine':{'checkpoint':'custom-voice-plus-stress-scale-v1'}})
        m.write(output/'ready.json',{})
        ready={'chapters':['c0001']};media=self.root/'media'
        with patch.object(m,'MEDIA',media),patch.object(m,'attach_cached_music'):
            m.publish(output,ready,'c0001',[unit])
        state=m.read(output/'progress.json');chapter=state['chapters']['c0001']
        self.assertEqual(state['status'],'complete');self.assertEqual(state['recognition_status'],'not_checked')
        self.assertFalse(state['machine_verified']);self.assertFalse(state['human_approval']);self.assertNotIn('word_flags',chapter)
        wav=next(media.glob('*.wav'));actual,_=sf.read(wav,dtype='int16');np.testing.assert_array_equal(samples,actual)
        timeline=m.read(output/chapter['timeline_asset']['file']);self.assertEqual(timeline[0]['text'],unit['text'])
        self.assertEqual(timeline[0]['analysis_parts'],unit['analysis_parts'])


if __name__=='__main__':unittest.main()
