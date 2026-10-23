"""Synthetic V10 provenance/registration/controller checks; no inference."""
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_word_pronunciation as adapter
import pronounce_public_audiobook_word_recovery as helper


class WordPronunciationAdapter(unittest.TestCase):
    def fixture(self,historical=False):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);folder=Path(temporary.name)
        source='Он цуцык добрый.' if historical else 'Он братец добрый.'
        before='цуцык' if historical else 'братец';after='цу́цик' if historical else 'бра́тец'
        prefix='Ранее. ';start=source.index(before)
        unit={'id':'u1','source_id':'p1','start':len(prefix),'end':len(prefix)+len(source),'text':source,
              'spoken':source,'synthesis_text':source,'palette_reference':{'path':'C:/PRIVATE/training-work/voice.wav'}}
        row={key:unit[key] for key in ('id','source_id','start','end')}
        row.update(text_sha256=adapter.base.sha(source),native_before=source,native_after=source.replace(before,after),
                   patches=[{'source_start':len(prefix)+start,'source_end':len(prefix)+start+len(before),'source_surface':before,
                             'native_start':start,'native_end':start+len(before),'before':before,'after':after,
                             'kind':'historical-ts-spelling' if historical else 'stress-only',
                             'dictionary_facts':[{'url':'https://orfo.ruslang.ru/abc/fixture','headword':helper.plain(after),
                                                  'fact':'Synthetic invariant fixture, not a dictionary quotation.','retrieval_date':'2026-10-23'}]}])
        candidate=helper.repair(unit,row);bound={'patch_unit':row}
        stress={'models':{'silero':'pinned','ruaccent':'pinned'},'audio_approval':False}
        context=adapter.Context.__new__(adapter.Context);context.folder=folder;context.generation='frozen'
        context.assignment={'language':'Russian','units':[unit]};context.manifest={'speech_model':{'sha256':'m'*64},'runner':{'sha256':'r'*64}}
        context.blocks={'p1':prefix+source};context.dependencies={};context.word_pronunciation_dependencies={'v10':'pinned'}
        context.bindings={};context.english_policy=None;context.recheck=Mock()
        audio=folder/'assets/native.wav';audio.parent.mkdir();audio.write_bytes(b'immutable native fixture');checksum=adapter.base.file_digest(audio)
        spec={'source':copy.deepcopy(unit),'generation':'frozen','model':context.manifest['speech_model'],'runner':context.manifest['runner'],
              'reference':unit['palette_reference'],'effects':'disabled','request':{'text':candidate['synthesis_text']},
              'word_pronunciation_recovery':{'policy':helper.POLICY,'attempt':1,'repair':copy.deepcopy(candidate)}}
        receipt={'unit':'u1','take':15,'spec':spec,'spec_sha256':adapter.base.sha(spec),'audio':'assets/native.wav',
                 'audio_sha256':checksum,'quality_passed':True}
        path=helper.receipt_path(folder,unit,1);adapter.base.immutable(path,receipt);evidence=[]
        heard=source.replace(before,helper.plain(after))
        for role,family,recognizer in [('primary','qwen','qwen'),('witness','whisper','turbo')]:
            config={'model':family,'language':'Russian','local_files_only':True,'expected_text_given_to_recognizer':False}
            raw=adapter.base.transcript_check(source,heard,checksum,adapter.base.sha(config))
            check=path.parent/('native-'+recognizer+'.json');adapter.base.immutable(check,{**raw,'verifier':config})
            evidence.append({'role':role,'family':family,'verifier':config,'raw':raw,
                             'path':check.relative_to(folder).as_posix(),'sha256':adapter.base.file_digest(check)})
        decision=helper.strict_decision(unit,evidence,prefix+source,candidate)
        self.assertTrue(decision['passed'])
        proof={'policy':helper.POLICY,'receipt_sha256':adapter.base.file_digest(path),'native_audio_sha256':checksum,
               'target_decision':decision,'source_spans':[{key:unit[key] for key in ('id','source_id','start','end','text')}],
               'pronunciation_provenance':{'dictionary':{'publisher':'fixture'},'providers':[{'model':'independent'}],
                 'repair':copy.deepcopy(candidate),'stress':stress,'native_text_sha256':adapter.base.sha(candidate['synthesis_text']),
                 'palette_reference':unit['palette_reference']}}
        return context,unit,receipt,path,proof,bound,stress

    def guards(self,fixture,proof=None):
        _,_,_,_,original,bound,stress=fixture
        return patch.multiple(helper,validate_word_pronunciation_receipt=Mock(return_value=proof or original),
                              campaign=Mock(return_value=bound),validated_stress=Mock(return_value=stress),
                              check_record=Mock(side_effect=lambda check,*args:{key:value for key,value in check.items() if key!='verifier'}))

    def test_append_only_byte_exact_registration_retains_prior_takes(self):
        fixture=self.fixture();context,unit,receipt,path,_,_,_=fixture;protected={}
        for take in range(15):
            old=context.folder/'takes/u1'/('take-%02d.json'%take);old.parent.mkdir(parents=True,exist_ok=True)
            old.write_bytes(('protected '+str(take)).encode());protected[old]=old.read_bytes()
        with self.guards(fixture):
            record=adapter.register(context,path);target=context.folder/record['canonical']
            self.assertEqual(target.read_bytes(),path.read_bytes());self.assertEqual(context.assess(unit,receipt)['decision']['passed'],True)
            adapter.register(context,path);target.write_bytes(b'conflicting canonical')
            with self.assertRaisesRegex(ValueError,'Never overwrite'):adapter.register(context,path)
        for path,raw in protected.items():self.assertEqual(path.read_bytes(),raw)
        self.assertNotIn('PRIVATE',str(record));self.assertNotIn('palette_reference',str(record))

    def test_source_model_native_stress_attempt_and_audio_tamper_fail_closed(self):
        fixture=self.fixture();context,unit,receipt,_,proof,_,_=fixture
        variants=[]
        for key,value in [('source',{**unit,'text':'Other source'}),('model',{'sha256':'other'}),
                          ('request',{'text':unit['spoken']}),('effects','emotion')]:
            bad=copy.deepcopy(receipt);bad['spec'][key]=value;variants.append(bad)
        bad=copy.deepcopy(receipt);bad['spec']['word_pronunciation_recovery']['attempt']=4;variants.append(bad)
        bad=copy.deepcopy(receipt);bad['audio_sha256']='0'*64;variants.append(bad)
        for bad in variants:
            bad['spec_sha256']=adapter.base.sha(bad['spec'])
            with self.subTest(spec=bad['spec']),self.guards(fixture):
                with self.assertRaises(ValueError):adapter.validated_word_pronunciation(context,unit,bad)
        for field in ('stress','repair'):
            invalid=copy.deepcopy(proof);invalid['pronunciation_provenance'][field]={}
            with self.guards(fixture,invalid),self.assertRaisesRegex(ValueError,'patch/stress/audio'):
                adapter.validated_word_pronunciation(context,unit,receipt)

    def test_both_recognizers_and_ordinary_target_no_name_waiver(self):
        fixture=self.fixture();context,unit,receipt,_,proof,_,_=fixture
        for index in (0,1):
            invalid=copy.deepcopy(proof);invalid['target_decision']['evidence'][index]['comparison']['passed']=False
            with self.guards(fixture,invalid),self.assertRaisesRegex(ValueError,'Both word checks'):
                adapter.validated_word_pronunciation(context,unit,receipt)
        invalid=copy.deepcopy(proof)
        invalid['target_decision']['evidence'][0]['comparison']['tolerated_deviations']=[
            {'rule':'proper-noun-slot-v1','source_index':1,'source_tokens':['братец']}]
        with self.guards(fixture,invalid),self.assertRaisesRegex(ValueError,'without patched-word tolerance'):
            adapter.validated_word_pronunciation(context,unit,receipt)
        invalid=copy.deepcopy(proof);invalid['target_decision']['evidence'].pop()
        with self.guards(fixture,invalid),self.assertRaisesRegex(ValueError,'BOTH independent'):
            adapter.validated_word_pronunciation(context,unit,receipt)

    def test_historical_bridge_recomputed_both_literal_slots_raw_retained(self):
        fixture=self.fixture(True);context,unit,receipt,_,proof,_,_=fixture;snapshot=copy.deepcopy(proof)
        with self.guards(fixture):decision,public=adapter.validated_word_pronunciation(context,unit,receipt)
        self.assertEqual(proof,snapshot);self.assertTrue(all(e['raw']['status']!='asr_exact_match' for e in decision['evidence']))
        self.assertEqual(len([r for r in decision['equivalences'] if r['rule']=='reviewed-source-bound-historical-hard-ts-spelling-v1']),2)
        self.assertEqual(public['expected_spoken'],unit['spoken']);self.assertNotEqual(public['actual_native_input'],unit['spoken'])
        invalid=copy.deepcopy(proof);invalid['target_decision']['equivalences']=[]
        with self.guards(fixture,invalid),self.assertRaisesRegex(ValueError,'differs from stored'):
            adapter.validated_word_pronunciation(context,unit,receipt)

    def test_changed_source_coordinates_and_check_bytes_rejected(self):
        fixture=self.fixture();context,unit,receipt,_,proof,_,_=fixture
        context.blocks['p1']='X'+context.blocks['p1']
        with self.guards(fixture),self.assertRaisesRegex(ValueError,'source coordinates'):
            adapter.validated_word_pronunciation(context,unit,receipt)
        context.blocks['p1']=context.blocks['p1'][1:]
        check=context.folder/proof['target_decision']['evidence'][0]['path'];check.write_bytes(b'forged check')
        with self.guards(fixture),self.assertRaises(ValueError):adapter.validated_word_pronunciation(context,unit,receipt)

    def test_unmarked_delegate_dependency_and_canonical_mirror_guards(self):
        fixture=self.fixture();context,unit,receipt,path,_,_,_=fixture
        with patch.object(adapter.previous.Context,'assess',return_value='unchanged') as prior:
            self.assertEqual(context.assess(unit,{'spec':{}}),'unchanged');prior.assert_called_once()
        context.recheck=adapter.Context.recheck.__get__(context)
        with patch.object(adapter.previous.Context,'recheck'),patch.object(adapter,'dependencies',return_value={'changed':'sha'}):
            with self.assertRaisesRegex(ValueError,'dependencies changed'):context.recheck()
        self.assertEqual(context.dependencies,{})
        context.recheck=Mock();target=context.folder/'takes/u1/take-15.json';target.parent.mkdir(parents=True);target.write_bytes(b'forged mirror')
        with self.assertRaisesRegex(ValueError,'byte-exact'):context.assess(unit,receipt)

    def test_report_discloses_extension_without_double_counting_registered_raw(self):
        fixture=self.fixture();context,unit,receipt,_,_,_,_=fixture
        summary={'take':15,'raw_transcripts':[{'evidence_sha256':'already-counted'}]};row={'id':'u1','takes':[summary],'selected':summary}
        value={'units':[row],'resolution_adapter_sha256':'v9','raw_mismatch_check_count':40,'disclosure':'Old failures.'}
        def history(ctx,known):
            self.assertIn('already-counted',known);return [{'status':'spoken_text_mismatch'},{'status':'asr_exact_match'}]
        with patch.object(adapter.previous,'report',return_value=value),patch.object(adapter.base.renderer,'takes',return_value=[receipt]),\
             patch.object(context,'assess',return_value={'word_pronunciation_provenance':{'stress':'public-pins'}}),\
             patch.object(adapter,'private_history',side_effect=history):result=adapter.report(context)
        self.assertEqual(result['raw_mismatch_check_count'],41);self.assertEqual(result['word_boundary_resolution_adapter_sha256'],'v9')
        self.assertEqual(row['selected']['word_pronunciation_provenance'],{'stress':'public-pins'})
        oldhash=adapter.base.sha(result);summary['word_pronunciation_provenance']['stress']='changed';self.assertNotEqual(oldhash,adapter.base.sha(result))

    def driver_fixture(self,prepare=False,two=True):
        fixture=self.fixture();context=fixture[0];args=SimpleNamespace(folder=[context.folder],output=context.folder/'controller',
            unit=['u1','u2'],prepare_only=prepare,patch_data=context.folder/'patch-data.json')
        args.patch_data.write_bytes(b'protected patch data');engine=Mock();engine.state={};engine.command.side_effect=lambda script,folder,flags,**kw:[str(script),*flags]
        rows=[{'id':'u1','selected':None}]+([{'id':'u2','selected':None}] if two else [])
        def snap(selected=()):return {'report':{'units':[{**r,'selected':{'take':15} if r['id'] in selected else None} for r in rows]},
                                   'completion_valid':len(selected)==len(rows),'completion_reason':'strict recomputed'}
        engine.snapshot.return_value=snap();engine.refresh.side_effect=[snap(),snap(['u1']),snap(['u1','u2'])] if two else [snap(),snap(['u1'])]
        pin={'patch_data_sha256':adapter.base.file_digest(args.patch_data),'word_recovery_dependencies':{'code':'pinned'}}
        return fixture,args,engine,pin

    def driver_guards(self,engine,pin):
        return patch.multiple(adapter,Engine=Mock(return_value=engine),controller_configuration=Mock(return_value=pin),
                              dependencies=Mock(return_value=pin['word_recovery_dependencies']),wait_previous=Mock())

    def test_serial_driver_publishes_first_success_before_next_unit_no_extra_resolve(self):
        fixture,args,engine,pin=self.driver_fixture();context=fixture[0];events=[]
        engine.run.side_effect=lambda name,*a,**kw:events.append(name)
        original=engine.refresh.side_effect
        engine.refresh.side_effect=lambda folder:(events.append('refresh') or next(original))
        with self.driver_guards(engine,pin),patch.object(adapter.legacy.ownership.context_driver,'pin_configuration'),\
             patch.object(adapter.supervision,'read',return_value={'units':[{'id':'u1'},{'id':'u2'}]}):
            self.assertEqual(adapter.all_work(args),0)
        self.assertEqual(events.count('refresh'),3)
        self.assertLess(events.index('word-recovery-u1-1-register'),events.index('word-recovery-u2-1-render'))
        middle=events.index('word-recovery-u1-1-register')+1;self.assertEqual(events[middle],'refresh')
        expected=['render','verifyqwen','verifyturbo','validate','register']
        self.assertEqual([e.rsplit('-1-',1)[1] for e in events if e.startswith('word-recovery-u1-1-')],expected)
        self.assertTrue(all(c.kwargs['asr'] for c in engine.command.call_args_list))

    def test_prepare_only_plans_with_patch_data_asr_environment_no_gpu_or_publish(self):
        fixture,args,engine,pin=self.driver_fixture(True)
        with self.driver_guards(engine,pin),patch.object(adapter.legacy.ownership.context_driver,'pin_configuration'):
            self.assertEqual(adapter.all_work(args),0)
        engine.refresh.assert_not_called();engine.snapshot.assert_called_once()
        stages=[call.args[0] for call in engine.run.call_args_list]
        self.assertEqual(stages,['word-recovery-u1-plan','word-recovery-u1-propose','word-recovery-u2-plan','word-recovery-u2-propose'])
        for call in engine.command.call_args_list:
            self.assertTrue(call.kwargs['asr']);self.assertEqual('--patch-data' in call.args[2],'plan' in call.args[2])
        self.assertEqual(engine.state['status'],'prepared')

    def test_failed_three_strategies_retained_then_next_unit_proceeds(self):
        fixture,args,engine,pin=self.driver_fixture();events=[]
        engine.refresh.side_effect=[engine.snapshot.return_value,{'report':{'units':[{'id':'u1','selected':None},{'id':'u2','selected':{'take':15}}]},
                                  'completion_valid':False,'completion_reason':'one held'}]
        def run(name,*a,**kw):
            events.append(name)
            if name.startswith('word-recovery-u1-') and name.endswith('-validate'):raise adapter.finishing.StageFailure('genuine source mismatch')
        engine.run.side_effect=run
        with self.driver_guards(engine,pin),patch.object(adapter.legacy.ownership.context_driver,'pin_configuration'),\
             patch.object(adapter.supervision,'read',return_value={'units':[{'id':'u1'},{'id':'u2'}]}):self.assertEqual(adapter.all_work(args),1)
        self.assertNotIn('word-recovery-u1-1-register',events);self.assertIn('word-recovery-u2-1-register',events)
        state=engine.state['word_recovery_units'][str(fixture[0].folder)+'::u1']
        self.assertEqual([state['attempts'][str(a)]['status'] for a in (1,2,3)],['held']*3)

    def test_new_orphan_helpers_ownership_cpu_prepare_and_pid_reuse(self):
        fixture=self.fixture();folder=fixture[0].folder
        def row(pid,script,stage,extra=()):
            return {'pid':pid,'ppid':0,'name':'python.exe','cwd':str(ROOT),'create_time':123,
                    'command':['python',str(ROOT/'scripts'/script),'--folder',str(folder),'--stage',stage,*extra]}
        rows=[row(11,'pronounce_public_audiobook_word_recovery.py','verify'),row(12,'pronounce_public_audiobook_word_recovery.py','propose'),
              row(13,'witness_public_demo_wav2vec2_ru.py','witness'),row(14,'witness_public_demo_wav2vec2_ru.py','plan'),
              row(15,adapter.SELF.name,'all'),row(16,adapter.SELF.name,'all',['--prepare-only']),row(17,'unrelated.py','all')]
        busy=adapter.previous_jobs(rows,[folder],999);self.assertEqual({r['pid'] for r in busy},{11,13,15})
        self.assertTrue(next(r for r in busy if r['pid']==15)['other_controller'])

    def test_config_mismatch_keeps_prior_history(self):
        fixture=self.fixture();path=fixture[0].folder/'controller.json';history=path.with_name('history.json');history.write_bytes(b'prior attempts')
        adapter.legacy.ownership.context_driver.pin_configuration(path,{'source':'original'})
        with self.assertRaisesRegex(ValueError,'new output namespace'):
            adapter.legacy.ownership.context_driver.pin_configuration(path,{'source':'changed'})
        self.assertEqual(history.read_bytes(),b'prior attempts')


if __name__=='__main__':unittest.main()
