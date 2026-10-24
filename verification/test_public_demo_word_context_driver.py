"""Synthetic V12 controller ownership, gates and resume; never load a model."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import finish_public_demo_word_context_recovery as driver


class FakeEngine:
    def __init__(self,folder,fail=None):
        self.state={'stages':{},'books':{}};self.calls=[];self.selected=set();self.fail=fail;self.folder=folder
        driver.supervision.write(folder/'assignment.json',{'units':[{'id':uid} for uid in driver.TARGETS]})
    def save(self):pass
    def book(self,folder,**fields):self.state['books'].setdefault(str(folder),{}).update(fields)
    def command(self,script,folder,extra=(),asr=False):return ['asr-python' if asr else 'tts-python',str(script),'--folder',str(folder),*extra]
    def run(self,name,folder,command,**kwargs):
        self.calls.append((name,command,kwargs))
        if self.fail and self.fail(name):raise driver.finishing.StageFailure('genuine source mismatch')
        if name.endswith('-register'):self.selected.add('u0132' if 'u0132' in name else 'u0133')
    def resolve_v12(self,folder):
        self.calls.append(('resolve',[],{}))
        return {'report':{'units':[{'id':uid,'selected':{'take':18} if uid in self.selected else None} for uid in driver.TARGETS],
                          'verified_units':154+len(self.selected),'open_recognition_flags':4-len(self.selected),'pending_units':0},'report_sha256':'fixture'}


class WordContextDriver(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)/'ru-chameleon';self.folder.mkdir()
        self.args=SimpleNamespace(folder=[self.folder],unit=list(driver.TARGETS),operational_retries=1)
        self.pin={'controller_sha256':driver.supervision.file_hash(driver.SELF),'v12_dependencies':{'v12':'pinned'}}
    def controller(self,fail=None):
        engine=FakeEngine(self.folder,fail);return driver.Controller(self.args,engine,self.pin),engine
    def checks(self):return patch.object(driver.resolver,'dependencies',return_value=self.pin['v12_dependencies'])

    def test_serial_order_all_gates_before_crop_registration_and_no_publication(self):
        controller,engine=self.controller()
        with self.checks(),patch.object(driver,'wait_previous') as guard:self.assertEqual(controller.run(),0)
        names=[name for name,_,_ in engine.calls]
        prefix='word-context-u0132-a1-'
        stages=['render','native-gate','context-qwen','context-turbo','context-gate','align','crop','crop-qwen','crop-turbo','crop-gate','validate','register']
        self.assertEqual([name[len(prefix):] for name in names if name.startswith(prefix)],stages)
        self.assertEqual(guard.call_count,12)
        self.assertFalse(any('publish' in word for _,command,_ in engine.calls for word in command))
        self.assertTrue(all(command[0]=='asr-python' for _,command,_ in engine.calls if command))
        self.assertEqual(engine.state['status'],'complete');self.assertEqual(engine.state['final_counts']['open_recognition_flags'],2)

    def test_failed_full_gate_never_aligns_and_next_unit_proceeds_bounded_three(self):
        controller,engine=self.controller(lambda name:'u0132' in name and name.endswith('context-gate'))
        with self.checks(),patch.object(driver,'wait_previous'):self.assertEqual(controller.run(),1)
        names=[name for name,_,_ in engine.calls]
        self.assertEqual(sum('u0132' in name and name.endswith('render') for name in names),3)
        self.assertFalse(any('u0132' in name and name.endswith(('align','crop','register')) for name in names))
        self.assertTrue(any('u0133' in name and name.endswith('register') for name in names))
        before=len(engine.calls)
        with self.checks(),patch.object(driver,'wait_previous'):self.assertEqual(controller.run(),1)
        self.assertFalse(any(name.endswith('render') for name,_,_ in engine.calls[before:]))

    def test_failed_native_quality_stops_before_asr(self):
        controller,engine=self.controller(lambda name:name.endswith('native-gate'))
        with self.checks(),patch.object(driver,'wait_previous'):self.assertEqual(controller.run(),1)
        self.assertFalse(any(name.endswith(('qwen','turbo','align','register')) for name,_,_ in engine.calls))

    def test_exact_stage_adoption_excludes_only_matching_creation_time_and_command(self):
        _,engine=self.controller();engine.active_name='native';command=['python','helper','--folder',str(self.folder)]
        identity={'pid':77,'create_time':123.,'command':command,'folder':str(self.folder)}
        key=driver.supervision.digest({'folder':str(self.folder),'name':engine.active_name,'command':command})
        engine.state['stages'][key]={'attempts':[{'status':'running','identity':identity}]}
        rows=[{'pid':77,'ppid':1,'create_time':123.,'command':command},{'pid':78,'ppid':77,'create_time':124.,'command':['native']}]
        self.assertEqual(driver.adopted_stage_pids(engine,self.folder,command,rows),{77,78})
        rows[0]['create_time']=125.;self.assertEqual(driver.adopted_stage_pids(engine,self.folder,command,rows),set())
        rows[0]['create_time']=123.;rows[0]['command']=['unrelated'];self.assertEqual(driver.adopted_stage_pids(engine,self.folder,command,rows),set())

    def test_owner_guard_matches_orphan_verifier_and_other_controller_not_plan_or_pid_reuse(self):
        def row(pid,script,flags):return {'pid':pid,'ppid':0,'name':'python.exe','create_time':float(pid),'cwd':str(ROOT),
          'command':['python',str(ROOT/'scripts'/script),'--folder',str(self.folder),*flags]}
        rows=[row(10,Path(driver.helper.__file__).name,['--stage','verify']),row(11,Path(driver.helper.__file__).name,['--stage','plan']),
              row(12,driver.SELF.name,[]),row(13,driver.SELF.name,['--internal-gate','context']),row(14,'unrelated.py',['--stage','verify'])]
        busy=driver.previous_jobs(rows,[self.folder],999)
        self.assertEqual({r['pid'] for r in busy},{10,12});self.assertTrue(next(r for r in busy if r['pid']==12)['other_controller'])

    def test_operational_retry_never_treats_recognition_or_quality_failure_as_transient(self):
        log=self.folder/'stage.log';log.write_text('Source substitution; active boundary; held',encoding='utf-8')
        record={'attempts':[{'log':str(log),'timed_out':False}]};self.assertFalse(driver.operational_failure(record))
        log.write_text('torch.OutOfMemoryError: CUDA out of memory',encoding='utf-8');self.assertTrue(driver.operational_failure(record))
        record['attempts'][-1]['timed_out']=True;self.assertTrue(driver.operational_failure(record))

    def test_foreign_busy_row_without_controller_flag_waits_then_fails_at_deadline(self):
        _,engine=self.controller();engine.active_name='stage';self.args.wait_deadline_seconds=.1
        with patch('psutil.process_iter',return_value=[]),patch.object(driver,'previous_jobs',return_value=[{'pid':71}]),\
             patch.object(driver.time,'monotonic',side_effect=[0.,1.]):
            with self.assertRaisesRegex(driver.ownership.OwnershipConflict,'wait deadline'):
                driver.wait_previous(engine,self.args,self.folder,[])
        self.assertEqual(engine.state['resource_wait']['busy'],[{'pid':71}])

    def test_configuration_mismatch_preserves_prior_journal(self):
        config=self.folder/'controller-config.json';journal=self.folder/'finishing-supervision.json';journal.write_bytes(b'protected journal')
        driver.ownership.pin_configuration(config,{'source':'original'})
        with self.assertRaisesRegex(ValueError,'new output namespace'):driver.ownership.pin_configuration(config,{'source':'changed'})
        self.assertEqual(journal.read_bytes(),b'protected journal')

    def test_initial_resolver_failure_records_stopped_held_status(self):
        controller,engine=self.controller()
        with patch.object(engine,'resolve_v12',side_effect=driver.finishing.StageFailure('CPU validation failed')):
            with self.assertRaises(driver.finishing.StageFailure):controller.run()
        self.assertEqual(engine.state['status'],'held');self.assertFalse(engine.state['books'][str(self.folder)]['process_running'])


if __name__=='__main__':unittest.main()
