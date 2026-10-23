"""CPU-only synthetic contracts for the versioned pronunciation adapter."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import resolve_public_demo_pronunciation as adapter
import pronounce_public_audiobook_recovery as pronunciation


class PronunciationAdapter(unittest.TestCase):
    def fixture(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        folder=Path(temporary.name)
        unit={'id':'u1','source_id':'p1','start':0,'end':21,'text':'He enfolded his Della.',
              'spoken':'He enfolded his Della.','palette_reference':{'path':'C:/PRIVATE/training-work/voice.wav'}}
        ctx=adapter.Context.__new__(adapter.Context)
        ctx.folder,ctx.generation=folder,'immutable-fixture'
        ctx.assignment={'language':'English','title':'Fixture','units':[unit]}
        ctx.manifest={'speech_model':{'sha256':'m'*64},'runner':{'sha256':'r'*64}}
        ctx.dependencies,ctx.extension_dependencies,ctx.bindings,ctx.english_policy={},{'adapter':'pinned'},{},None
        ctx.blocks={'p1':unit['text']};ctx.recheck=Mock()
        audio=folder/'assets/native.wav';audio.parent.mkdir();audio.write_bytes(b'immutable-audio-fixture')
        audio_hash=adapter.file_digest(audio)
        spec={'source':copy.deepcopy(unit),'generation':ctx.generation,'model':ctx.manifest['speech_model'],
              'runner':ctx.manifest['runner'],'reference':unit['palette_reference'],'effects':'disabled',
              'request':{'text':'He in-folded his Della.'},
              'pronunciation_recovery':{'policy':pronunciation.POLICY,'attempt':1}}
        receipt={'unit':unit['id'],'take':9,'spec':spec,'spec_sha256':adapter.base.sha(spec),
                 'audio':'assets/native.wav','audio_sha256':audio_hash,'quality_passed':True}
        path=pronunciation.receipt_path(folder,unit,1);adapter.base.immutable(path,receipt)
        evidence=[]
        for role,family in [('primary','qwen'),('witness','whisper')]:
            config={'model':family,'expected_text_given_to_recognizer':False,'local_files_only':True}
            raw=adapter.transcript_check(unit['spoken'],unit['spoken'],audio_hash,adapter.base.sha(config))
            check=path.parent/('native-'+family+'.json');adapter.base.immutable(check,{**raw,'verifier':config})
            evidence.append({'role':role,'family':family,'verifier':config,'raw':raw,
                             'path':check.relative_to(folder).as_posix(),'sha256':adapter.file_digest(check)})
        decision=adapter.base.resolve_transcripts(unit,'English',unit['text'],evidence)
        proof={'policy':pronunciation.POLICY,'receipt_sha256':adapter.file_digest(path),
               'native_audio_sha256':audio_hash,'target_decision':decision,
               'source_spans':[{k:unit[k] for k in ('id','source_id','start','end','text')}],
               'pronunciation_provenance':{'dictionary':{'publisher':'fixture'},'providers':[{'model':'independent-fixture'}],
                                           'palette_reference':unit['palette_reference']}}
        return ctx,unit,receipt,path,proof

    def test_registration_is_byte_exact_append_only_and_preserves_prior_takes(self):
        ctx,unit,receipt,path,proof=self.fixture()
        originals={}
        for take in range(9):
            target=ctx.folder/'takes/u1'/('take-%02d.json'%take);target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(('protected prior '+str(take)).encode());originals[target]=target.read_bytes()
        with patch.object(pronunciation,'validate_pronunciation_receipt',return_value=proof):
            record=adapter.register(ctx,path)
            canonical=ctx.folder/record['canonical']
            self.assertEqual(canonical.read_bytes(),path.read_bytes())
            self.assertEqual(ctx.assess(unit,receipt)['decision'],proof['target_decision'])
            adapter.register(ctx,path)
        for old,raw in originals.items():
            self.assertEqual(old.read_bytes(),raw)
        self.assertNotIn('PRIVATE',str(record['pronunciation_provenance']))
        canonical.write_bytes(b'changed canonical fixture');before=canonical.read_bytes()
        with patch.object(pronunciation,'validate_pronunciation_receipt',return_value=proof):
            with self.assertRaisesRegex(ValueError,'never overwrite'):
                adapter.register(ctx,path)
        self.assertEqual(canonical.read_bytes(),before)

    def test_forged_native_source_and_spans_are_rejected(self):
        ctx,unit,receipt,path,proof=self.fixture()
        for key,value in [('source',{**unit,'text':'Changed source.'}),('request',{'text':'He unfolded his Della.'})]:
            bad=copy.deepcopy(receipt);bad['spec'][key]=value;bad['spec_sha256']=adapter.base.sha(bad['spec'])
            with patch.object(pronunciation,'validate_pronunciation_receipt',return_value=proof):
                with self.assertRaises(ValueError):adapter.validated_pronunciation(ctx,unit,bad)
        bad=copy.deepcopy(proof);bad['source_spans'][0]['end']+=1
        with patch.object(pronunciation,'validate_pronunciation_receipt',return_value=bad):
            with self.assertRaisesRegex(ValueError,'source span'):
                adapter.validated_pronunciation(ctx,unit,receipt)

    def test_both_blind_original_word_checks_are_required_even_with_forged_canonical_words(self):
        for index in (0,1):
            ctx,unit,receipt,path,proof=self.fixture()
            bad=copy.deepcopy(proof)
            evidence=bad['target_decision']['evidence'][index]
            config=evidence['verifier']
            raw=adapter.transcript_check(unit['spoken'],'He unfolded his Della.',receipt['audio_sha256'],adapter.base.sha(config))
            check=ctx.folder/evidence['path'];check.write_text(__import__('json').dumps({**raw,'verifier':config}),encoding='utf-8')
            evidence.update(raw=raw,sha256=adapter.file_digest(check))
            with patch.object(pronunciation,'validate_pronunciation_receipt',return_value=bad):
                with self.assertRaisesRegex(ValueError,'Both blind'):
                    adapter.validated_pronunciation(ctx,unit,receipt)
        bad=copy.deepcopy(proof);bad['target_decision']['evidence'].pop()
        with patch.object(pronunciation,'validate_pronunciation_receipt',return_value=bad):
            with self.assertRaisesRegex(ValueError,'BOTH independent'):
                adapter.validated_pronunciation(ctx,unit,receipt)

    def test_all_unmarked_receipts_delegate_without_extending_base_dependencies(self):
        ctx,unit,_,_,_=self.fixture()
        with patch.object(adapter.base.Context,'assess',return_value='unchanged') as original:
            self.assertEqual(ctx.assess(unit,{'spec':{}}),'unchanged')
            original.assert_called_once_with(unit,{'spec':{}})
        self.assertEqual(ctx.dependencies,{})
        self.assertEqual(ctx.extension_dependencies,{'adapter':'pinned'})

    def test_failed_quality_and_unbounded_fourth_take_never_register(self):
        ctx,unit,receipt,_,proof=self.fixture()
        for change in ('quality','attempt','take'):
            invalid=copy.deepcopy(receipt)
            if change=='quality':invalid['quality_passed']=False
            elif change=='attempt':invalid['spec']['pronunciation_recovery']['attempt']=4
            else:invalid['take']=12
            invalid['spec_sha256']=adapter.base.sha(invalid['spec'])
            with patch.object(pronunciation,'validate_pronunciation_receipt',return_value=proof) as validator:
                with self.assertRaises(ValueError):adapter.validated_pronunciation(ctx,unit,invalid)
                validator.assert_not_called()

    def test_report_discloses_adapter_and_retains_unregistered_failed_counts(self):
        ctx,unit,receipt,_,proof=self.fixture()
        summary={'take':9,'raw_transcripts':[{'evidence_sha256':'known'}],'context_provenance':None}
        value={'units':[{'id':'u1','takes':[summary],'selected':summary}],
               'resolution_module_sha256':'original-resolver','raw_mismatch_check_count':12,'disclosure':'Prior raw checks.'}
        with patch.object(adapter.base,'report',return_value=value),patch.object(adapter.base.renderer,'takes',return_value=[receipt]),\
             patch.object(ctx,'assess',return_value={'pronunciation_provenance':{'dictionary':'public evidence'}}),\
             patch.object(adapter,'private_history',return_value=[{'status':'spoken_text_mismatch'},{'status':'asr_exact_match'}]):
            report=adapter.report(ctx)
        self.assertEqual(report['raw_mismatch_check_count'],13)
        self.assertEqual(report['base_resolution_module_sha256'],'original-resolver')
        self.assertEqual(report['resolution_adapter_sha256'],adapter.file_digest(adapter.SELF))
        self.assertEqual(report['units'][0]['selected']['pronunciation_provenance'],{'dictionary':'public evidence'})
        before=adapter.base.sha(report);report['units'][0]['selected']['pronunciation_provenance']['dictionary']='changed'
        self.assertNotEqual(adapter.base.sha(report),before)

    def test_new_orphan_gpu_owner_guard_ignores_cpu_proposals_and_pid_reuse(self):
        folder=ROOT/'training-work/public-demos-fixture/en-gift-of-magi'
        def row(pid,script,stage):
            return {'pid':pid,'ppid':0,'name':'python.exe','cwd':str(ROOT),'create_time':123,
                    'command':['python',str(ROOT/'scripts'/script),'--folder',str(folder),'--stage',stage]}
        rows=[row(11,'pronounce_public_audiobook_recovery.py','verify'),
              row(12,'pronounce_public_audiobook_recovery.py','propose'),
              row(13,adapter.SELF.name,'all'),row(14,'unrelated.py','render')]
        busy=adapter.previous_jobs(rows,[folder],999)
        self.assertEqual({r['pid'] for r in busy},{11,13})
        self.assertTrue(next(r for r in busy if r['pid']==13)['other_controller'])

    def test_driver_checks_both_then_holds_failed_strategy_and_resumes_next(self):
        ctx,unit,_,_,_=self.fixture()
        args=SimpleNamespace(folder=[ctx.folder],output=ctx.folder/'controller',unit=['u1'])
        held={'report':{'units':[{'id':'u1','selected':None}]}}
        passed={'report':{'units':[{'id':'u1','selected':{'take':10}}]}}
        complete={'completion_valid':True,'completion_reason':'strict fixture proof'}
        engine=Mock();engine.state={}
        engine.resolve.side_effect=[held,passed,passed,complete]
        engine.command.side_effect=lambda script,folder,flags,**kwargs:[str(script),*flags]
        stages=[]
        def run(name,*unused,**kwargs):
            stages.append(name)
            if name=='pronunciation-u1-1-validate':
                raise adapter.finishing.StageFailure('First immutable strategy still says unfolded')
        engine.run.side_effect=run
        with patch.object(adapter,'Engine',return_value=engine),patch.object(adapter.context_driver,'configuration',return_value={}),\
             patch.object(adapter.context_driver,'pin_configuration'),patch.object(adapter,'extension_dependencies',return_value={}),\
             patch.object(adapter.base,'dependencies',return_value={}),patch.object(adapter,'wait_previous'),\
             patch.object(adapter.supervision,'read',return_value=ctx.assignment):
            self.assertEqual(adapter.all_work(args),0)
        self.assertLess(stages.index('pronunciation-u1-1-verifyqwen'),stages.index('pronunciation-u1-1-verifyturbo'))
        self.assertLess(stages.index('pronunciation-u1-1-verifyturbo'),stages.index('pronunciation-u1-1-validate'))
        self.assertNotIn('pronunciation-u1-1-register',stages)
        self.assertIn('pronunciation-u1-2-register',stages)
        self.assertFalse(any('u1-3-' in s for s in stages))
        attempts=engine.state['pronunciation_units'][str(ctx.folder)+'::u1']['attempts']
        self.assertEqual(attempts['1']['status'],'held')
        self.assertEqual(attempts['2']['status'],'verified')


if __name__=='__main__':unittest.main()
