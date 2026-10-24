"""Remaining recovery supervision uses synthetic receipts; no models or jobs."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import finish_public_demo_remaining_recovery as driver


class FakeEngine:
    def __init__(self,folder,fail=None,accept=True):
        self.state={'stages':{},'books':{}};self.calls=[];self.selected=set();self.fail=fail;self.accept=accept
        driver.supervision.write(folder/'assignment.json',{'units':[{'id':uid} for uid in driver.TARGETS]})
    def save(self):pass
    def book(self,folder,**fields):self.state['books'].setdefault(str(folder),{}).update(fields)
    def command(self,script,folder,extra=(),asr=False):return ['asr-python' if asr else 'tts-python',str(script),'--folder',str(folder),*extra]
    def snapshot(self):
        return {'report':{'units':[{'id':uid,'selected':{'take':15 if uid=='u0071' else 18} if uid in self.selected else None} for uid in driver.TARGETS],
                          'verified_units':154+len(self.selected),'open_recognition_flags':4-len(self.selected),'pending_units':0},'report_sha256':'fixture'}
    def run(self,name,folder,command,**kwargs):
        self.calls.append((name,command,kwargs))
        if self.fail and self.fail(name):raise driver.finishing.StageFailure('genuine source mismatch')
        if name.endswith('-register') and self.accept:self.selected.add('u0071' if 'u0071' in name else 'u0153')
    def resolve_v13(self,folder):
        self.calls.append(('resolve',[],{}));return self.snapshot()


class RemainingController(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/'ru-chameleon';self.folder.mkdir()
        self.args=SimpleNamespace(folder=[self.folder],unit=list(driver.TARGETS),operational_retries=1,output=Path(self.temp.name)/'queue')
    def make(self,fail=None,accept=True):
        engine=FakeEngine(self.folder,fail,accept);snapshot=engine.snapshot()
        controller=driver.Controller(self.args,engine,{'selection_only_baseline_sha256':driver.supervision.digest(snapshot)})
        return controller,engine,snapshot
    def execute(self,controller,snapshot):
        with patch.object(controller,'recheck'),patch.object(driver,'baseline',return_value=snapshot),patch.object(driver,'wait_previous'):
            return controller.run()

    def test_both_paths_are_serial_all_gates_before_register_one_final_report(self):
        controller,engine,snapshot=self.make()
        self.assertEqual(self.execute(controller,snapshot),0)
        names=[name for name,_,_ in engine.calls]
        self.assertNotEqual(names[0],'resolve');self.assertEqual(names[-1],'resolve');self.assertEqual(names.count('resolve'),1)
        expected={'supplement-u0071-a1-':['render','native-gate','qwen','turbo','word-gate','validate','register'],
                  'historical-context-u0153-a1-':['render','native-gate','context-qwen','context-turbo','context-gate','align','crop','crop-qwen','crop-turbo','crop-gate','validate','register']}
        for prefix,stages in expected.items():self.assertEqual([n[len(prefix):] for n in names if n.startswith(prefix)],stages)
        registers=[command for name,command,_ in engine.calls if name.endswith('-register')]
        self.assertIn(str(Path(driver.supplement_resolver.__file__)),registers[0])
        self.assertIn(str(Path(driver.resolver.__file__)),registers[1])
        self.assertFalse(any('publish' in part for _,command,_ in engine.calls for part in command))
        self.assertTrue(all(command[0]=='asr-python' for _,command,_ in engine.calls if command))
        self.assertTrue(engine.state['scope_complete']);self.assertFalse(engine.state['book_complete'])
        self.assertEqual(engine.state['final_counts']['open_recognition_flags'],2)

    def test_native_failure_stops_asr_but_other_unit_proceeds_and_resume_keeps_cap(self):
        controller,engine,snapshot=self.make(lambda n:'u0071' in n and n.endswith('native-gate'))
        self.assertEqual(self.execute(controller,snapshot),1)
        names=[n for n,_,_ in engine.calls]
        self.assertEqual(sum('u0071' in n and n.endswith('render') for n in names),3)
        self.assertFalse(any('u0071' in n and n.endswith(('qwen','turbo','register')) for n in names))
        self.assertIn('historical-context-u0153-a1-register',names)
        before=len(engine.calls);self.assertEqual(self.execute(controller,snapshot),1)
        self.assertFalse(any(n.endswith('render') for n,_,_ in engine.calls[before:]))

    def test_full_mismatch_never_aligns_crop_mismatch_never_registers(self):
        for failure in ('context-gate','crop-gate'):
            with self.subTest(failure=failure):
                controller,engine,snapshot=self.make(lambda n:n.endswith(failure))
                self.assertEqual(self.execute(controller,snapshot),1)
                names=[n for n,_,_ in engine.calls if 'u0153' in n]
                self.assertEqual(sum(n.endswith('render') for n in names),3)
                self.assertFalse(any(n.endswith('register') for n in names))
                if failure=='context-gate':self.assertFalse(any(n.endswith(('align','crop')) for n in names))

    def test_successful_registration_without_final_selection_remains_held(self):
        controller,engine,snapshot=self.make(accept=False)
        self.assertEqual(self.execute(controller,snapshot),1)
        self.assertFalse(engine.state['scope_complete'])
        self.assertTrue(all(s['status']=='held' for s in controller.units.values()))
        self.assertTrue(all(s['attempts']['1']['status']=='registered_pending_revalidation' for s in controller.units.values()))
        self.assertEqual(sum(n.endswith('render') for n,_,_ in engine.calls),2)

    def test_binding_or_ownership_failure_aborts_without_consuming_other_attempts(self):
        for error in (driver.InputBindingConflict('changed'),driver.ownership.OwnershipConflict('busy')):
            controller,engine,snapshot=self.make()
            with patch.object(controller,'recheck'),patch.object(driver,'baseline',return_value=snapshot),\
                 patch.object(controller,'attempt',side_effect=error):
                with self.assertRaises(type(error)):controller.run()
            self.assertEqual(controller.units['u0071']['attempts']['1']['status'],'blocked_ownership')
            self.assertNotIn('u0153',controller.units);self.assertEqual(engine.state['status'],'held')

    def test_cached_selected_only_skips_work_until_full_revalidation(self):
        controller,engine,snapshot=self.make();snapshot['report']['units'][0]['selected']={'old':'proof'}
        controller.pin['selection_only_baseline_sha256']=driver.supervision.digest(snapshot)
        self.assertEqual(self.execute(controller,snapshot),1)
        self.assertFalse(any('u0071' in n for n,_,_ in engine.calls))
        self.assertEqual(controller.units['u0071']['status'],'held')

    def test_immutable_baseline_rejects_tamper_and_does_not_follow_mutable_latest(self):
        FakeEngine(self.folder)
        driver.supervision.write(self.folder/'frozen.json',{'frozen':'one'})
        report={'units':[{'id':uid,'selected':None} for uid in driver.TARGETS],
                'language':'Russian','human_approval':False,'frozen_generation':driver.supervision.digest({'frozen':'one'})}
        # Actual V12 baseline uses this namespace, not finishing/report-SHA.
        checksum=driver.supervision.digest(report);path=self.folder/'resolution-proposals'/('word-context-report-'+checksum+'.json')
        driver.supervision.write(path,report);snapshot={'report':report,'report_sha256':checksum,'report_path':str(path)}
        driver.supervision.write(self.folder/'finishing/current-resolution.json',snapshot)
        self.assertEqual(driver.baseline(self.args),snapshot)
        driver.supervision.write(self.folder/'finishing/current-resolution.json',{'different':'new report'})
        self.assertEqual(driver.baseline(self.args),snapshot)
        for key,value in [('report_sha256','forged'),('report_path',str(self.folder.parent/'outside.json'))]:
            with self.assertRaises((ValueError,OSError)):driver.validate_baseline(self.folder,{**snapshot,key:value})
        driver.supervision.write(self.folder/'frozen.json',{'frozen':'two'})
        with self.assertRaises(ValueError):driver.baseline(self.args)

    def test_new_and_prior_owner_and_orphan_native_guards(self):
        def row(pid,script,flags):return {'pid':pid,'ppid':0,'name':'python.exe','create_time':float(pid),'cwd':str(ROOT),
            'command':['python',str(ROOT/'scripts'/script),'--folder',str(self.folder),*flags]}
        rows=[row(10,Path(driver.helper.__file__).name,['--stage','verify']),row(11,Path(driver.supplement.__file__).name,['--stage','render']),
              row(12,driver.SELF.name,[]),row(13,driver.SELF.name,['--internal-gate','context']),
              row(14,Path(driver.prior_driver.__file__).name,[]),row(15,Path(driver.helper.__file__).name,['--stage','plan'])]
        native=ROOT/'training-work/higgs-tts-3-pilot/audio-cpp-cuda/audiocpp_cli.exe'
        rows.append({'pid':16,'ppid':0,'name':'audiocpp_cli.exe','cwd':str(ROOT),'command':[str(native),'--request-sequence',str(self.folder/'req.json')]})
        busy=driver.previous_jobs(rows,[self.folder],999)
        self.assertEqual({r['pid'] for r in busy},{10,11,12,14,16})
        self.assertEqual({r['pid'] for r in busy if r.get('other_controller')},{12,14})

    def test_pid_reuse_and_changed_command_never_report_live_stage(self):
        process=SimpleNamespace(pid=77,cmdline=lambda:['exact'])
        identity={'pid':77,'create_time':123.,'command':['exact']}
        stage={'status':'running','attempts':[{'status':'running','identity':identity}]}
        with patch('psutil.Process',return_value=process),patch.object(driver.supervision,'live',return_value=True):
            self.assertEqual(driver.active_stage_pids(stage),{77})
            process.cmdline=lambda:['forged'];self.assertEqual(driver.active_stage_pids(stage),set())
        with patch('psutil.Process',return_value=process),patch.object(driver.supervision,'live',return_value=False):
            self.assertEqual(driver.active_stage_pids(stage),set())

    def test_semantic_failure_not_operational_and_timeout_is(self):
        log=self.folder/'stage.log';log.write_text('source mismatch; active audio edge',encoding='utf-8')
        record={'attempts':[{'log':str(log),'timed_out':False}]};self.assertFalse(driver.operational_failure(record))
        record['attempts'][-1]['timed_out']=True;self.assertTrue(driver.operational_failure(record))

    def test_changed_prepared_evidence_blocks_before_new_native_attempt(self):
        controller,engine,_=self.make()
        self.args.python=self.args.asr_python=driver.SELF
        for name in ('manifest.json','frozen.json','prepared/book.json','reviewed-proposal.json'):
            driver.supervision.write(self.folder/name,{'immutable':name})
        controller.pin={'controller_sha256':driver.supervision.file_hash(driver.SELF),'v13_dependencies':{},'supplement_dependencies':{},
            'ownership_driver_sha256':driver.supervision.file_hash(Path(driver.prior_driver.__file__)),
            'python_sha256':driver.supervision.file_hash(driver.SELF),'asr_python_sha256':driver.supervision.file_hash(driver.SELF),
            'prepared_inputs':{'reviewed-proposal.json':driver.supervision.file_hash(self.folder/'reviewed-proposal.json')},
            'dependency_packages':{},
            'dependency_files':{driver.SELF.relative_to(ROOT).as_posix():driver.supervision.file_hash(driver.SELF)},
            'folder_inputs':{str(self.folder.resolve()):{key:driver.supervision.file_hash(self.folder/name) for name,key in
                [('assignment.json','assignment_sha256'),('manifest.json','manifest_sha256'),('frozen.json','frozen_sha256'),('prepared/book.json','prepared_source_sha256')]}}}
        with patch.object(driver.resolver,'dependencies',return_value={}),patch.object(driver.supplement_resolver,'dependencies',return_value={}):
            controller.recheck()
            controller.pin['dependency_packages']={'example-package':'1.2.3'}
            with patch.object(driver.importlib.metadata,'version',return_value='changed'):
                with self.assertRaisesRegex(driver.InputBindingConflict,'package changed'):controller.recheck()
            controller.pin['dependency_packages']={}
            driver.supervision.write(self.folder/'reviewed-proposal.json',{'different':'unreviewed'})
            with self.assertRaises(driver.InputBindingConflict):controller.recheck()
        self.assertFalse(engine.calls)

    def test_nested_file_and_package_dependencies_are_both_pinned(self):
        name=driver.SELF.relative_to(ROOT).as_posix();checksum=driver.supervision.file_hash(driver.SELF)
        files,packages=driver.dependency_pins([{'files':{name:checksum},'sustained_sound_helper_packages':{'example-package':'1.2.3'}},{name:checksum}])
        self.assertEqual(files,{name:checksum});self.assertEqual(packages,{'example-package':'1.2.3'})
        with self.assertRaises(ValueError):driver.dependency_pins([{'files':{name:checksum}},{name:'0'*64}])
        with self.assertRaises(ValueError):driver.dependency_pins([{'unrecognized-metadata':{'value':'not a file'}}])

    def test_one_operational_retry_preserves_exact_command_semantic_gate_never_retries(self):
        controller,engine,_=self.make();folder=self.folder;flags=['--stage','verify'];name='retry-fixture'
        command=engine.command(driver.SELF,folder,['--unit','u0071',*flags],asr=True)
        key=driver.supervision.digest({'folder':str(folder),'name':name,'command':command})
        engine.state['stages'][key]={'attempts':[{'timed_out':True}]}
        with patch.object(controller,'recheck'),patch.object(engine,'run',side_effect=[driver.finishing.StageFailure('timeout'),None]) as run:
            controller.stage(folder,'u0071',name,driver.SELF,flags)
            self.assertEqual(run.call_count,2);self.assertEqual(run.call_args_list[0],run.call_args_list[1])
        self.assertEqual(engine.state['stages'][key]['operational_retry_count'],1)
        with patch.object(controller,'recheck'),patch.object(engine,'run',side_effect=driver.finishing.StageFailure('held')) as run:
            with self.assertRaises(driver.finishing.StageFailure):controller.stage(folder,'u0071',name,driver.SELF,flags,semantic=True)
            self.assertEqual(run.call_count,1)

    def test_book_stage_activity_is_derived_and_stale_false_latch_cleared(self):
        engine=driver.Engine.__new__(driver.Engine)
        engine.state={'books':{str(self.folder):{'process_running':False}},'stages':{'s':{'folder':str(self.folder)}}}
        with patch.object(driver,'active_stage_pids',return_value={77}),patch.object(driver.finishing.Engine,'save'):
            engine.save()
        book=engine.state['books'][str(self.folder)];self.assertTrue(book['stage_active']);self.assertTrue(book['process_running'])
        self.assertEqual(book['stage_active_pids'],[77])
        with patch.object(driver,'active_stage_pids',return_value=set()),patch.object(driver.finishing.Engine,'save'):engine.save()
        self.assertFalse(book['stage_active']);self.assertFalse(book['process_running'])

    def test_exact_saved_stage_identity_only_adopts_real_matching_process_tree(self):
        _,engine,_=self.make();engine.active_name='render';command=['python','helper','--folder',str(self.folder)]
        identity={'pid':77,'create_time':123.,'command':command,'folder':str(self.folder)}
        key=driver.supervision.digest({'folder':str(self.folder),'name':engine.active_name,'command':command})
        engine.state['stages'][key]={'attempts':[{'status':'running','identity':identity}]}
        rows=[{'pid':77,'ppid':1,'create_time':123.,'command':command},{'pid':78,'ppid':77,'create_time':124.,'command':['native']}]
        self.assertEqual(driver.adopted_stage_pids(engine,self.folder,command,rows),{77,78})
        rows[0]['create_time']=125.;self.assertEqual(driver.adopted_stage_pids(engine,self.folder,command,rows),set())
        rows[0]['create_time']=123.;rows[0]['command']=['other'];self.assertEqual(driver.adopted_stage_pids(engine,self.folder,command,rows),set())


if __name__=='__main__':unittest.main()
