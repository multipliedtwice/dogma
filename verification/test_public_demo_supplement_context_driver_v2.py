"""V15 one-unit durable supervision; no inference, provider calls or publication."""
import copy,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import finish_public_demo_supplement_context_recovery_v2 as m

class Engine:
    def __init__(self,folder,fail=None,accept=True):
        self.state={'stages':{},'books':{}};self.calls=[];self.selected=False;self.fail=fail;self.accept=accept
        m.supervision.write(folder/'assignment.json',{'units':[{'id':'u0071'}]})
    def save(self):pass
    def book(self,folder,**kw):self.state['books'].setdefault(str(folder),{}).update(kw)
    def command(self,script,folder,extra=(),asr=False):return ['asr' if asr else 'tts',str(script),'--folder',str(folder),*extra]
    def snapshot(self):return {'report':{'units':[dict(id='u0071',selected={'take':24} if self.selected else None)],
        'verified_units':154+int(self.selected),'open_recognition_flags':4-int(self.selected),'pending_units':0},'report_sha256':'fixture'}
    def run(self,name,folder,command,**kw):
        self.calls.append((name,command))
        if self.fail and name.endswith(self.fail):raise m.finishing.StageFailure('source/quality remains held')
        if name.endswith('-register') and self.accept:self.selected=True
    def resolve_v15(self,folder):self.calls.append(('resolve',[]));return self.snapshot()

class SupplementDriver(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.folder=Path(self.tmp.name)/'ru-chameleon';self.folder.mkdir()
        self.args=SimpleNamespace(folder=[self.folder],unit=['u0071'],output=Path(self.tmp.name)/'queue',operational_retries=1,prior_handoff=(Path(self.tmp.name)/'deferred-handoff.json').resolve())
    def make(self,fail=None,accept=True):
        e=Engine(self.folder,fail,accept);s=e.snapshot();c=m.Controller(self.args,e,{'selection_only_baseline_sha256':m.supervision.digest(s)})
        return c,e,s
    def execute(self,c,s):
        with patch.object(c,'recheck'),patch.object(m,'baseline',return_value=s),patch.object(m,'wait_previous'):return c.run()
    def test_full_crop_validation_registration_then_one_unified_report(self):
        c,e,s=self.make();self.assertEqual(self.execute(c,s),0)
        names=[n for n,_ in e.calls];prefix='supplement-context-u0071-a1-'
        self.assertEqual([n.removeprefix(prefix) for n in names[:-1]],['render','native-gate','context-qwen','context-turbo','context-gate','align','crop','crop-qwen','crop-turbo','crop-gate','validate','register'])
        self.assertEqual(names[-1],'resolve');self.assertTrue(all(cmd[0]=='asr' for _,cmd in e.calls if cmd))
        self.assertFalse(any('publish' in cmd for _,cmd in e.calls));self.assertTrue(e.state['scope_complete']);self.assertFalse(e.state['book_complete'])
        self.assertEqual(e.state['final_counts']['open_recognition_flags'],3)
    def test_full_fail_never_aligns_crop_fail_never_registers_only_three_takes(self):
        for stage in ('native-gate','context-gate','crop-gate'):
            c,e,s=self.make(stage);self.assertEqual(self.execute(c,s),1);names=[n for n,_ in e.calls]
            self.assertEqual(sum(n.endswith('render') for n in names),3);self.assertFalse(any(n.endswith('register') for n in names))
            if stage in ('native-gate','context-gate'):self.assertFalse(any(n.endswith('align') for n in names))
            if stage=='native-gate':self.assertFalse(any(n.endswith('qwen') for n in names))
            before=len(e.calls);self.assertEqual(self.execute(c,s),1);self.assertEqual([n for n,_ in e.calls[before:]],['resolve'])
    def test_registration_is_not_approval_and_resume_does_not_generate_more(self):
        c,e,s=self.make(accept=False);self.assertEqual(self.execute(c,s),1)
        self.assertEqual(c.units['u0071']['attempts']['1']['status'],'registered_pending_revalidation')
        before=len(e.calls);self.assertEqual(self.execute(c,s),1);self.assertEqual([n for n,_ in e.calls[before:]],['resolve'])
    def test_ownership_conflict_does_not_consume_three_strategy_budget(self):
        c,e,s=self.make()
        with patch.object(c,'recheck'),patch.object(m,'baseline',return_value=s),patch.object(c,'attempt',side_effect=m.ownership.OwnershipConflict('other owner')):
            with self.assertRaises(m.ownership.OwnershipConflict):c.run()
        self.assertEqual(c.units['u0071']['attempts']['1']['status'],'blocked_ownership');self.assertEqual(len(c.units['u0071']['attempts']),1)
    def test_other_controller_and_orphan_gpu_helpers_seen_cpu_plan_ignored(self):
        def row(i,name,stage=None):return dict(pid=i,ppid=0,name='python.exe',create_time=float(i),cwd=str(ROOT),
            command=['python',str(ROOT/'scripts'/name),'--folder',str(self.folder),*(['--stage',stage] if stage else [])])
        rows=[row(1,'finish_public_demo_boundary_recovery.py'),row(2,'finish_public_demo_remaining_recovery.py'),
            row(3,m.SELF.name),row(4,'context_public_demo_historical_word_recovery.py','verify'),
            row(5,'context_public_demo_boundary_recovery.py','align'),row(6,Path(m.helper.__file__).name,'render'),row(7,Path(m.helper.__file__).name,'plan'),row(8,'finish_public_demo_supplement_context_recovery.py')]
        busy=m.previous_jobs(rows,[self.folder],999);self.assertEqual({r['pid'] for r in busy},{1,2,3,4,5,6,8})
        self.assertEqual({r['pid'] for r in busy if r.get('other_controller')},{1,2,3,8})
    def test_binding_change_stops_before_native_and_retains_history(self):
        c,e,s=self.make();c.units['u0071']={'attempts':{'1':{'status':'held','reason':'preserved'}}};old=copy.deepcopy(c.units)
        with patch.object(c,'recheck',side_effect=m.InputBindingConflict('changed')):
            with self.assertRaises(m.InputBindingConflict):c.run()
        self.assertEqual(c.units,old);self.assertEqual(e.calls,[])
    def test_prior_handoff_requires_full_source_bound_stopped_controller_validation(self):
        self.args.prior_handoff=(Path(self.tmp.name)/'deferred-handoff.json').resolve()
        with patch.object(m.boundary_controller,'validate_handoff',return_value={'workers_stopped':True}) as check:
            self.assertEqual(m.validate_prior_handoff(self.args),{'workers_stopped':True})
            check.assert_called_once_with(self.args.prior_handoff,self.folder,require_controller_stopped=True)
        with patch.object(m.boundary_controller,'validate_handoff',side_effect=ValueError('live or forged prior queue')):
            with self.assertRaisesRegex(ValueError,'prior queue'):m.validate_prior_handoff(self.args)
    def test_real_internal_gate_subprocesses_parse_generated_commands_before_missing_fixture(self):
        import os,subprocess
        c,e,_=self.make()
        # No manifest/frozen/model files exist: all subprocesses must stop at
        # frozen source loading, after real argparse, before any GPU/model API.
        for flags in (['--native-gate'],['--internal-gate','context'],['--internal-gate','crop']):
            with self.subTest(flags=flags),patch.object(c,'recheck'):
                c.stage(self.folder,'u0071','argparse-smoke-'+flags[-1],m.SELF,[*flags,'--attempt','3'],semantic=True)
                _,command=e.calls[-1]
                self.assertIn('--prior-handoff',command)
                self.assertEqual(command[command.index('--prior-handoff')+1],str(self.args.prior_handoff))
                result=subprocess.run([sys.executable,'-X','utf8',*command[1:]],cwd=ROOT,capture_output=True,text=True,
                    encoding='utf-8',timeout=30,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                self.assertNotEqual(result.returncode,2,result.stderr)
                self.assertIn('frozen_context',result.stderr)
                self.assertIn('FileNotFoundError',result.stderr)
                self.assertNotIn('the following arguments are required',result.stderr)
    def test_existing_quality_passing_a3_native_is_reused_without_synthesis(self):
        unit={'id':'u0071'};path=m.helper.attempt_home(self.folder,unit,3)/'native-receipt.json'
        receipt={'quality_passed':True,'audio_sha256':'a'*64};m.helper.immutable(path,receipt);before=path.read_bytes()
        with patch.object(m.helper,'campaign',return_value={}),patch.object(m.helper,'native_spec',return_value={'request':{}}),\
             patch.object(m.helper,'native_receipt',return_value=({}, {}, receipt)) as validate,\
             patch.object(m.helper,'resolve_production_higgs_model',side_effect=AssertionError('No new synthesis session')) as pin,\
             patch.object(m.helper.subprocess,'run',side_effect=AssertionError('No new native process')) as native:
            m.helper.render(self.folder,unit,3)
            validate.assert_called_once_with(self.folder,unit,3);pin.assert_not_called();native.assert_not_called()
        self.assertEqual(path.read_bytes(),before)

if __name__=='__main__':unittest.main()
