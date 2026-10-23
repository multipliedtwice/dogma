"""Synthetic RU pronunciation registration/controller checks; no real jobs."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_ru_pronunciation as adapter
import pronounce_public_audiobook_ru_recovery as helper


class RuPronunciationAdapter(unittest.TestCase):
    def fixture(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);folder=Path(temporary.name)
        source='Из дровяного склада.'
        unit={'id':'u1','source_id':'p1','start':0,'end':len(source),'text':source,'spoken':source,
              'synthesis_text':'Из дровяно́го склада.','palette_reference':{'path':'C:/PRIVATE/training-work/voice.wav'}}
        context=adapter.Context.__new__(adapter.Context);context.folder=folder;context.generation='frozen'
        context.assignment={'language':'Russian','units':[unit]};context.manifest={'speech_model':{'sha256':'m'*64},'runner':{'sha256':'r'*64}}
        context.blocks={'p1':source};context.dependencies={};context.ru_pronunciation_dependencies={'v7':'pinned'}
        context.bindings={};context.english_policy=None;context.recheck=Mock()
        audio=folder/'assets/native.wav';audio.parent.mkdir();audio.write_bytes(b'immutable raw fixture')
        checksum=adapter.base.file_digest(audio)
        spec={'source':copy.deepcopy(unit),'generation':context.generation,'model':context.manifest['speech_model'],
              'runner':context.manifest['runner'],'reference':unit['palette_reference'],'effects':'disabled',
              'request':{'text':'Из дровяно́во склада.'},'ru_pronunciation_recovery':{'policy':helper.POLICY,'attempt':1}}
        receipt={'unit':unit['id'],'take':12,'spec':spec,'spec_sha256':adapter.base.sha(spec),
                 'audio':'assets/native.wav','audio_sha256':checksum,'quality_passed':True}
        path=helper.receipt_path(folder,unit,1);adapter.base.immutable(path,receipt);evidence=[]
        for role,family,recognizer in [('primary','qwen','qwen'),('witness','whisper','turbo')]:
            config={'model':family,'language':'Russian','local_files_only':True,'expected_text_given_to_recognizer':False}
            raw=adapter.base.transcript_check(source,source,checksum,adapter.base.sha(config))
            check=path.parent/('native-'+recognizer+'.json');adapter.base.immutable(check,{**raw,'verifier':config})
            comparison=adapter.base.russian_equivalence(unit,source,source,checksum,adapter.base.sha(config))
            evidence.append({'role':role,'family':family,'verifier':config,'raw':raw,'comparison':comparison,
                             'path':check.relative_to(folder).as_posix(),'sha256':adapter.base.file_digest(check)})
        decision={'passed':True,'status':'independently_exact','resolution':'independent_witness_exact',
                  'evidence':evidence,'equivalences':[],'tolerated_deviations':[]}
        proof={'policy':helper.POLICY,'receipt_sha256':adapter.base.file_digest(path),'native_audio_sha256':checksum,
               'target_decision':decision,'source_spans':[{key:unit[key] for key in ('id','source_id','start','end','text')}],
               'pronunciation_provenance':{'dictionary':{'publisher':'fixture'},'providers':[{'model':'independent'}],
                                           'palette_reference':unit['palette_reference']}}
        return context,unit,receipt,path,proof

    def test_registration_byte_exact_preserves_prior_takes_and_never_overwrites(self):
        context,unit,receipt,path,proof=self.fixture();originals={}
        for take in range(12):
            target=context.folder/'takes/u1'/('take-%02d.json'%take);target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(('protected '+str(take)).encode());originals[target]=target.read_bytes()
        with patch.object(helper,'validate_ru_pronunciation_receipt',return_value=proof):
            record=adapter.register(context,path);canonical=context.folder/record['canonical']
            self.assertEqual(canonical.read_bytes(),path.read_bytes())
            self.assertEqual(context.assess(unit,receipt)['decision'],proof['target_decision'])
            adapter.register(context,path)
            canonical.write_bytes(b'protected conflicting canonical')
            with self.assertRaisesRegex(ValueError,'Never overwrite'):adapter.register(context,path)
        for old,raw in originals.items():self.assertEqual(old.read_bytes(),raw)
        self.assertNotIn('PRIVATE',str(record['ru_pronunciation_provenance']))

    def test_source_native_stress_model_attempt_and_quality_tamper_rejected(self):
        context,unit,receipt,_,proof=self.fixture()
        variants=[]
        for key,value in [('source',{**unit,'text':'Other source'}),('request',{'text':'Из дровяного склада.'}),
                          ('model',{'sha256':'other'}),('request',{'text':'Из дровя́ново склада.'})]:
            bad=copy.deepcopy(receipt);bad['spec'][key]=value;variants.append(bad)
        bad=copy.deepcopy(receipt);bad['spec']['ru_pronunciation_recovery']['attempt']=4;variants.append(bad)
        bad=copy.deepcopy(receipt);bad['quality_passed']=False;variants.append(bad)
        for bad in variants:
            bad['spec_sha256']=adapter.base.sha(bad['spec'])
            with patch.object(helper,'validate_ru_pronunciation_receipt',return_value=proof):
                with self.assertRaises(ValueError):adapter.validated_ru_pronunciation(context,unit,bad)

    def test_both_source_checks_and_no_target_adjective_name_waiver_required(self):
        context,unit,receipt,_,proof=self.fixture()
        for index in (0,1):
            bad=copy.deepcopy(proof);bad['target_decision']['evidence'][index]['comparison']['passed']=False
            with patch.object(helper,'validate_ru_pronunciation_receipt',return_value=bad):
                with self.assertRaisesRegex(ValueError,'Both RU blind'):adapter.validated_ru_pronunciation(context,unit,receipt)
        comparison={'passed':True,'canonical_words':adapter.base.word_tokens(unit['spoken']),
                    'equivalences':[],'tolerated_deviations':[{'rule':'proper-noun-slot-v1','source_tokens':['дровяного']}]}
        bad=copy.deepcopy(proof)
        for item in bad['target_decision']['evidence']:item['comparison']=comparison
        with patch.object(helper,'validate_ru_pronunciation_receipt',return_value=bad),patch.object(adapter.base,'russian_equivalence',return_value=comparison):
            with self.assertRaisesRegex(ValueError,'adjective name waiver'):adapter.validated_ru_pronunciation(context,unit,receipt)

    def test_unmarked_receipts_delegate_and_prior_dependencies_stay_separate(self):
        context,unit,_,_,_=self.fixture()
        with patch.object(adapter.previous.Context,'assess',return_value='old unchanged') as old:
            self.assertEqual(context.assess(unit,{'spec':{}}),'old unchanged');old.assert_called_once_with(unit,{'spec':{}})
        self.assertEqual(context.dependencies,{})

    def test_report_adds_only_unregistered_extra_raw_checks(self):
        context,unit,receipt,_,proof=self.fixture()
        summary={'take':12,'raw_transcripts':[{'evidence_sha256':'already-counted'}]};row={'id':'u1','takes':[summary],'selected':summary}
        value={'units':[row],'resolution_adapter_sha256':'v6','raw_mismatch_check_count':18,'disclosure':'Prior failures.'}
        with patch.object(adapter.previous,'report',return_value=value),patch.object(adapter.base.renderer,'takes',return_value=[receipt]),\
             patch.object(context,'assess',return_value={'ru_pronunciation_provenance':{'dictionary':'public'}}),\
             patch.object(adapter,'private_history',return_value=[{'status':'spoken_text_mismatch'},{'status':'asr_exact_match'}]):
            result=adapter.report(context)
        self.assertEqual(result['raw_mismatch_check_count'],19)
        self.assertEqual(result['acoustic_adjudication_resolution_adapter_sha256'],'v6')
        self.assertEqual(row['selected']['ru_pronunciation_provenance'],{'dictionary':'public'})

    def engine_fixture(self,prepare=False):
        context,unit,_,_,_=self.fixture()
        args=SimpleNamespace(folder=[context.folder],output=context.folder/'controller',unit=['u1'],prepare_only=prepare)
        engine=Mock();engine.state={};engine.command.side_effect=lambda script,folder,flags,**kwargs:[str(script),*flags]
        held={'report':{'units':[{'id':'u1','selected':None}]}}
        passed={'report':{'units':[{'id':'u1','selected':{'take':13}}]}}
        engine.resolve.side_effect=[held] if prepare else [held,passed,passed,{'completion_valid':True,'completion_reason':'strict checked'}]
        return context,args,engine

    def test_driver_serial_checks_hold_first_strategy_then_accept_second(self):
        context,args,engine=self.engine_fixture();stages=[]
        def run(name,*unused,**kwargs):
            stages.append(name)
            if name=='ru-pronunciation-u1-1-validate':raise adapter.finishing.StageFailure('Both-check source mismatch')
        engine.run.side_effect=run
        with patch.object(adapter,'Engine',return_value=engine),patch.object(adapter,'controller_configuration',return_value={}),\
             patch.object(adapter.ownership.context_driver,'pin_configuration'),patch.object(adapter,'wait_previous'),\
             patch.object(adapter.supervision,'read',return_value=context.assignment):
            self.assertEqual(adapter.all_work(args),0)
        self.assertLess(stages.index('ru-pronunciation-u1-1-verifyqwen'),stages.index('ru-pronunciation-u1-1-verifyturbo'))
        self.assertLess(stages.index('ru-pronunciation-u1-1-verifyturbo'),stages.index('ru-pronunciation-u1-1-validate'))
        self.assertNotIn('ru-pronunciation-u1-1-register',stages)
        self.assertIn('ru-pronunciation-u1-2-register',stages)
        self.assertFalse(any('u1-3-' in stage for stage in stages))
        self.assertEqual(engine.state['ru_pronunciation_units'][str(context.folder)+'::u1']['attempts']['1']['status'],'held')

    def test_prepare_only_never_runs_gpu_and_uses_complete_asr_environment(self):
        context,args,engine=self.engine_fixture(True)
        with patch.object(adapter,'Engine',return_value=engine),patch.object(adapter,'controller_configuration',return_value={}),\
             patch.object(adapter.ownership.context_driver,'pin_configuration'),patch.object(adapter,'wait_previous') as guard:
            self.assertEqual(adapter.all_work(args),0)
        self.assertEqual([call.args[0] for call in engine.run.call_args_list],['ru-pronunciation-u1-plan','ru-pronunciation-u1-propose'])
        self.assertTrue(all(call.kwargs['asr'] for call in engine.command.call_args_list))
        self.assertEqual(engine.state['status'],'prepared');guard.assert_not_called()

    def test_gpu_guard_matches_new_helpers_and_native_orphan_but_not_cpu_or_pid_reuse(self):
        context,_,_,_,_=self.fixture();folder=context.folder;runner=folder/'audiocpp_cli.exe'
        adapter.supervision.write(folder/'manifest.json',{'runner':{'path':str(runner)}})
        def row(pid,script,stage,extra=()):
            return {'pid':pid,'ppid':0,'name':'python.exe','cwd':str(ROOT),'create_time':123,
                    'command':['python',str(ROOT/'scripts'/script),'--folder',str(folder),'--stage',stage,*extra]}
        rows=[row(11,'witness_public_demo_whisper_full.py','witness'),row(12,'witness_public_demo_whisper_full.py','plan'),
              row(13,'witness_public_demo_whisper_beam.py','witness'),row(14,'pronounce_public_audiobook_ru_recovery.py','verify'),
              row(15,'pronounce_public_audiobook_ru_recovery.py','propose'),row(16,adapter.SELF.name,'all'),
              row(17,adapter.SELF.name,'all',['--prepare-only']),row(18,'unrelated.py','witness')]
        rows.append({'pid':19,'ppid':0,'name':'audiocpp_cli.exe','cwd':str(folder),'create_time':321,
                     'command':[str(runner),'--request-sequence',str(folder/'ru-pronunciation-recovery-v1/u1/attempt-01/native-request.json')]})
        busy=adapter.previous_jobs(rows,[folder],999)
        self.assertEqual({row['pid'] for row in busy},{11,13,14,16,19})
        self.assertTrue(next(row for row in busy if row['pid']==16)['other_controller'])

    def test_config_mismatch_preserves_existing_history(self):
        context,_,_,_,_=self.fixture();path=context.folder/'controller-config.json';history=context.folder/'history.json'
        history.write_bytes(b'protected prior attempt journal')
        adapter.ownership.context_driver.pin_configuration(path,{'source':'old'})
        with self.assertRaisesRegex(ValueError,'new output namespace'):
            adapter.ownership.context_driver.pin_configuration(path,{'source':'changed'})
        self.assertEqual(history.read_bytes(),b'protected prior attempt journal')


if __name__=='__main__':unittest.main()
