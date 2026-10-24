"""V14 fixed-choice/deferred handoff orchestration, no model or worker launch."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import finish_public_demo_boundary_recovery as driver


class FakeEngine:
    def __init__(self,folder,fail=None):
        self.folder=folder;self.state={'stages':{},'books':{}};self.calls=[];self.selected=set();self.fail=fail
        for name in ('assignment.json','manifest.json','frozen.json','prepared/book.json'):
            value={'units':[{'id':uid} for uid in driver.TARGETS]} if name=='assignment.json' else {'fixture':name}
            driver.supervision.write(folder/name,value)
        driver.supervision.write(folder/'finishing/current-resolution.json',self.snapshot())
    def save(self):driver.supervision.write(self.folder.parent/'queue/finishing-supervision.json',self.state)
    def book(self,folder,**fields):self.state['books'].setdefault(str(folder),{}).update(fields)
    def command(self,script,folder,extra=(),asr=False):return ['asr-python',str(script),'--folder',str(folder),*extra]
    def snapshot(self):
        report={'language':'Russian','human_approval':False,'frozen_generation':driver.supervision.digest(driver.supervision.read(self.folder/'frozen.json')),
            'units':[{'id':uid,'selected':{'take':21} if uid in self.selected else None} for uid in driver.TARGETS],
            'verified_units':154+len(self.selected),'open_recognition_flags':4-len(self.selected),'pending_units':0}
        checksum=driver.supervision.digest(report);path=self.folder/'resolution-proposals'/('boundary-context-report-'+checksum+'.json')
        driver.helper.immutable(path,report)
        return {'report':report,'report_sha256':checksum,'report_path':str(path)}
    def run(self,name,folder,command,**kwargs):
        self.calls.append((name,command))
        if self.fail and self.fail(name):raise driver.finishing.StageFailure('source/quality mismatch')
        if name.endswith('-register'):
            uid=next(uid for uid in driver.TARGETS if uid in name);attempt=int(name.split('-a')[1].split('-')[0]);self.selected.add(uid)
            unit={'id':uid};receipt={'unit':uid,'take':attempt+20,'spec':{'boundary_punctuation_context_recovery':{'policy':driver.helper.POLICY,'attempt':attempt}}}
            private=driver.helper.receipt_path(folder,unit,attempt);driver.helper.immutable(private,receipt)
            canonical=folder/'takes'/uid/('take-%02d.json'%(attempt+20));canonical.parent.mkdir(parents=True,exist_ok=True);canonical.write_bytes(private.read_bytes())
    def resolve_v14(self,folder):self.calls.append(('resolve',[]));return self.snapshot()


class BoundaryController(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='boundary-controller-test-',dir=ROOT/'training-work');self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/'book';self.folder.mkdir();self.output=Path(self.temp.name)/'queue';self.output.mkdir()
        self.args=SimpleNamespace(folder=[self.folder],unit=list(driver.TARGETS),output=self.output,operational_retries=1,defer_final_report=True)
    def make(self,fail=None):
        engine=FakeEngine(self.folder,fail);snapshot=driver.baseline(self.args)
        pin={'controller_sha256':driver.supervision.file_hash(driver.SELF),'selection_only_baseline_sha256':driver.supervision.digest(snapshot),
            'approved_choices':{uid:list(driver.helper.CHOICES) for uid in driver.TARGETS},'defer_final_report':self.args.defer_final_report,
            'review_holds':{},
            'requested_units':list(driver.TARGETS),
            'prepared_inputs':{},'dependency_files':{},'dependency_packages':{},
            'folder_inputs':{str(self.folder):{key:driver.supervision.file_hash(self.folder/name) for name,key in
                [('assignment.json','assignment_sha256'),('manifest.json','manifest_sha256'),('frozen.json','frozen_sha256'),('prepared/book.json','prepared_source_sha256')]}}}
        driver.supervision.write(self.output/'boundary-recovery-controller-config.json',pin)
        return driver.Controller(self.args,engine,pin),engine
    def run_controller(self,controller):
        with patch.object(controller,'recheck'),patch.object(driver,'wait_previous'):return controller.run()

    def test_deferred_mode_registers_strict_order_without_any_report_or_false_approval(self):
        controller,engine=self.make();self.assertEqual(self.run_controller(controller),0)
        names=[n for n,_ in engine.calls];self.assertNotIn('resolve',names)
        steps=['render','native-gate','context-qwen','context-turbo','context-gate','align','crop','crop-qwen','crop-turbo','crop-gate','validate','register']
        for uid in driver.TARGETS:
            prefix='boundary-context-'+uid+'-a1-';self.assertEqual([n[len(prefix):] for n in names if n.startswith(prefix)],steps)
            self.assertEqual(controller.units[uid]['status'],'registered_pending_revalidation')
        self.assertEqual(engine.state['status'],'awaiting_unified_revalidation');self.assertFalse(engine.state['scope_complete'])
        self.assertFalse(engine.state['book_complete']);self.assertFalse(engine.state['publication']);self.assertTrue(engine.state['counts_not_recomputed'])
        self.assertFalse(any('publish' in part for _,command in engine.calls for part in command))
        proof=driver.validate_handoff(self.output/'deferred-handoff.json',self.folder,require_controller_stopped=False)
        self.assertTrue(proof['approval_pending']);self.assertEqual(len(proof['canonical_receipts']),2)

    def test_rejected_choices_keep_their_fixed_slots_and_never_render(self):
        controller,engine=self.make();controller.pin['approved_choices']['u0132']=[driver.helper.CHOICES[1]]
        controller.pin['approved_choices']['u0133']=[]
        driver.supervision.write(self.output/'boundary-recovery-controller-config.json',controller.pin)
        self.assertEqual(self.run_controller(controller),0)
        names=[n for n,_ in engine.calls];self.assertEqual([n for n in names if n.endswith('render')],['boundary-context-u0132-a2-render'])
        self.assertEqual(controller.units['u0132']['attempts']['1']['status'],'rejected_by_independent_review')
        self.assertEqual(controller.units['u0133']['status'],'held')
        self.assertTrue((self.folder/'takes/u0132/take-22.json').is_file())
        self.assertFalse((self.folder/'takes/u0132/take-21.json').exists())

    def test_native_failure_is_bounded_and_does_not_block_other_unit(self):
        controller,engine=self.make(lambda n:'u0132' in n and n.endswith('native-gate'))
        self.assertEqual(self.run_controller(controller),0)
        names=[n for n,_ in engine.calls]
        self.assertEqual(sum('u0132' in n and n.endswith('render') for n in names),3)
        self.assertFalse(any('u0132' in n and n.endswith(('qwen','turbo','register')) for n in names))
        self.assertIn('boundary-context-u0133-a1-register',names);self.assertEqual(controller.units['u0132']['status'],'held')

    def test_terminal_invalid_or_rejected_review_authorizes_zero_native_attempts(self):
        controller,engine=self.make();controller.pin['review_holds']['u0132']={'reason':'Permanent exact-echo failure and substantive rejection.','native_authorized':False}
        controller.pin['approved_choices']['u0132']=[];driver.supervision.write(self.output/'boundary-recovery-controller-config.json',controller.pin)
        self.assertEqual(self.run_controller(controller),0)
        self.assertFalse(any('u0132' in n for n,_ in engine.calls));self.assertEqual(controller.units['u0132']['attempts'],{})
        self.assertEqual(controller.units['u0132']['status'],'held');self.assertTrue(any('u0133' in n for n,_ in engine.calls))

    def test_normal_mode_uses_one_full_v14_report_and_holds_book_completion(self):
        self.args.defer_final_report=False;controller,engine=self.make()
        self.assertEqual(self.run_controller(controller),0)
        self.assertEqual(sum(n=='resolve' for n,_ in engine.calls),1);self.assertEqual(engine.calls[-1][0],'resolve')
        self.assertTrue(engine.state['scope_complete']);self.assertFalse(engine.state['book_complete'])
        self.assertEqual(engine.state['final_counts']['open_recognition_flags'],2)

    def test_handoff_canonical_or_input_tamper_rejects_and_controller_must_really_exit(self):
        controller,engine=self.make();self.run_controller(controller);path=self.output/'deferred-handoff.json'
        with self.assertRaises(driver.ownership.OwnershipConflict):driver.validate_handoff(path,self.folder)
        canonical=self.folder/'takes/u0132/take-21.json';canonical.write_bytes(b'changed bytes')
        with self.assertRaises((ValueError,OSError)):driver.validate_handoff(path,self.folder,require_controller_stopped=False)

    def test_stale_stopped_journal_cannot_hide_a_live_stage_worker(self):
        identity={'pid':99,'create_time':123.,'command':['actual']}
        stages={'s':{'status':'failed','attempts':[{'status':'failed','identity':identity}]}}
        with patch('psutil.Process',return_value=object()),patch.object(driver.supervision,'live',return_value=True):
            with self.assertRaises(driver.ownership.OwnershipConflict):driver.assert_stage_workers_stopped(stages)

    def test_v15_owner_is_recognized_and_defer_mode_is_pinned(self):
        command=['python',str(ROOT/'scripts/finish_public_demo_supplement_context_recovery.py'),'--folder',str(self.folder)]
        row={'pid':77,'ppid':0,'name':'python.exe','create_time':123.,'cwd':str(ROOT),'command':command}
        busy=driver.previous_jobs([row],[self.folder],999)
        self.assertEqual([r['pid'] for r in busy],[77]);self.assertTrue(busy[0]['other_controller'])
        pin=self.output/'mode.json';driver.ownership.pin_configuration(pin,{'defer_final_report':True})
        with self.assertRaises(ValueError):driver.ownership.pin_configuration(pin,{'defer_final_report':False})


if __name__=='__main__':unittest.main()
