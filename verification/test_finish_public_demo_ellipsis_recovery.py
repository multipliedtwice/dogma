"""V17 supervision owns bounded physical children and honest parent checks."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import finish_public_demo_ellipsis_recovery as m


class Supervisor(unittest.TestCase):
    def fixture(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);folder=Path(temp.name)
        args=SimpleNamespace(folder=[folder],unit=['u0153'],defer_final_report=True,output=folder/'queue',prior_handoff=folder/'prior.json',operational_retries=0)
        engine=Mock();engine.state={};pin={};controller=m.Controller(args,engine,pin);controller.recheck=Mock();controller.handoff=Mock(return_value=0)
        snapshot={'report':{'units':[{'id':'u0153','selected':None}]}};return controller,args,engine,snapshot
    def test_failed_first_native_does_not_block_remaining_child_slots_and_good_children_reuse(self):
        c,args,engine,snapshot=self.fixture();calls=[]
        def child(folder,uid,number,attempt,after_parent=None):
            calls.append((number,attempt))
            if (number,attempt)==(1,1):raise m.finishing.StageFailure('native quality failed')
            return {'child':number,'attempt':attempt,'verified':'source-bound'}
        c.child_attempt=Mock(side_effect=child);c.parent_attempt=Mock()
        with patch.object(m,'baseline',return_value=snapshot),patch.object(m.helper,'validate_child',return_value={'verified':'source-bound'}),patch.object(m.supervision,'read',return_value={'units':[{'id':'u0153'}]}):
            self.assertEqual(c.run(),0)
            self.assertEqual(calls,[(1,1),(1,2),(2,1)])
            c.parent_attempt.assert_called_once_with(args.folder[0],'u0153',1,{'1':2,'2':1})
            self.assertEqual(c.units['u0153']['status'],'registered_pending_revalidation')
            c.run();self.assertEqual(calls,[(1,1),(1,2),(2,1)])
    def test_exhausted_child_counts_three_and_other_child_independent(self):
        c,args,engine,snapshot=self.fixture();calls=[]
        def child(folder,uid,number,attempt,after_parent=None):
            calls.append((number,attempt))
            if number==1:raise m.finishing.StageFailure('source mismatch')
            return {'child':number,'attempt':attempt}
        c.child_attempt=Mock(side_effect=child);c.parent_attempt=Mock()
        with patch.object(m,'baseline',return_value=snapshot),patch.object(m.supervision,'read',return_value={'units':[{'id':'u0153'}]}):c.run()
        self.assertEqual(calls,[(1,1),(1,2),(1,3),(2,1)]);c.parent_attempt.assert_not_called()
        self.assertEqual(c.units['u0153']['status'],'held')
    def test_failed_parent_advances_unused_child2_then_distinct_parent_passes(self):
        c,args,engine,snapshot=self.fixture();calls=[]
        c.child_attempt=Mock(side_effect=lambda f,u,ch,a,after_parent=None:calls.append((ch,a,after_parent)) or {'verified':True})
        c.parent_attempt=Mock(side_effect=[m.finishing.StageFailure('source mismatch'),None])
        with patch.object(m,'baseline',return_value=snapshot),patch.object(m.helper,'failed_parent_proof',return_value={'reproduced':True}),patch.object(m.supervision,'read',return_value={'units':[{'id':'u0153'}]}):
            c.run()
        self.assertEqual(calls,[(1,1,None),(2,1,None),(2,2,1)])
        self.assertEqual(c.parent_attempt.call_args_list[0].args[-1],{'1':1,'2':1})
        self.assertEqual(c.parent_attempt.call_args_list[1].args[-1],{'1':1,'2':2})
        self.assertEqual(c.units['u0153']['verified_children']['2'],[1,2])
        self.assertEqual(c.units['u0153']['status'],'registered_pending_revalidation')
    def test_budget_exhaustion_retains_all_verified_children_without_repeating_pairs(self):
        c,args,engine,snapshot=self.fixture();calls=[]
        def child(f,u,ch,a,after_parent=None):
            calls.append((ch,a,after_parent))
            if ch==2 and a>1:raise m.finishing.StageFailure('bad child words')
            return {'verified':True}
        c.child_attempt=Mock(side_effect=child);c.parent_attempt=Mock(side_effect=m.finishing.StageFailure('bad parent words'))
        with patch.object(m,'baseline',return_value=snapshot),patch.object(m.helper,'failed_parent_proof',return_value={'reproduced':True}),patch.object(m.helper,'validate_child',return_value={'verified':True}),patch.object(m.supervision,'read',return_value={'units':[{'id':'u0153'}]}):
            c.run();c.run()
        self.assertEqual(calls,[(1,1,None),(2,1,None),(2,2,1),(2,3,1),(1,2,1),(1,3,2)])
        self.assertEqual([row.args[-1] for row in c.parent_attempt.call_args_list],[{'1':1,'2':1},{'1':2,'2':1},{'1':3,'2':1}])
        self.assertEqual(c.units['u0153']['verified_children']['1'],[1,2,3]);self.assertEqual(c.units['u0153']['verified_children']['2'],[1])
        self.assertEqual(c.units['u0153']['status'],'held')
    def test_missing_parent_results_never_authorize_unused_child_profiles(self):
        c,args,engine,snapshot=self.fixture();c.child_attempt=Mock(return_value={'verified':True});c.parent_attempt=Mock(side_effect=m.finishing.StageFailure('missing ASR'))
        with patch.object(m,'baseline',return_value=snapshot),patch.object(m.helper,'failed_parent_proof',side_effect=FileNotFoundError('missing check')),patch.object(m.supervision,'read',return_value={'units':[{'id':'u0153'}]}):c.run()
        self.assertEqual(c.child_attempt.call_count,2);self.assertEqual(c.parent_attempt.call_count,1);self.assertEqual(c.units['u0153']['status'],'held')
    def test_parent_quality_gate_precedes_both_final_recognizers_and_registration(self):
        c,args,engine,snapshot=self.fixture();c.stage=Mock()
        with patch.object(m.supervision,'read',return_value={'units':[{'id':'u0153'}]}):c.parent_attempt(args.folder[0],'u0153',1,{'1':1,'2':2})
        rows=c.stage.call_args_list;flags=[r.args[4] for r in rows]
        self.assertEqual(flags[1][:2],['--internal-gate','parent-quality'])
        self.assertEqual([f[f.index('--recognizer')+1] for f in flags if '--recognizer' in f],['qwen','turbo'])
        self.assertEqual(flags[-1][:2],['--stage','register']);self.assertTrue(all('--left-attempt' not in f or '--right-attempt' in f for f in flags))
        c.stage=Mock(side_effect=[None,m.finishing.StageFailure('unsafe parent')])
        with self.assertRaises(m.finishing.StageFailure):c.parent_attempt(args.folder[0],'u0153',1,{'1':1,'2':1})
        self.assertEqual(c.stage.call_count,2)
    def test_child_full_and_crop_have_independent_two_recognizers(self):
        c,args,engine,snapshot=self.fixture();c.stage=Mock()
        with patch.object(m.supervision,'read',return_value={'units':[{'id':'u0153'}]}),patch.object(m.helper,'validate_child',return_value={}):
            c.child_attempt(args.folder[0],'u0153',2,3)
        flags=[row.args[4] for row in c.stage.call_args_list]
        self.assertEqual([(f[f.index('--kind')+1],f[f.index('--recognizer')+1]) for f in flags if '--recognizer' in f],
            [('context','qwen'),('context','turbo'),('crop','qwen'),('crop','turbo')])
        self.assertTrue(all(f[f.index('--child')+1]=='2' and f[f.index('--attempt')+1]=='3' for f in flags))
    def test_real_argparse_internal_modes_do_not_require_operational_handoff(self):
        import subprocess,os
        c,args,engine,snapshot=self.fixture()
        for kind,child in [('native',1),('context',1),('crop',2),('parent-quality',None),('parent',None)]:
            flags=['--internal-gate',kind,'--attempt','1']+(['--child',str(child)] if child else [])
            result=subprocess.run([sys.executable,'-X','utf8',str(m.SELF),'--folder',str(args.folder[0]),'--unit','u0153',*flags],
                env={**os.environ,'CUDA_VISIBLE_DEVICES':''},capture_output=True,text=True,encoding='utf8',timeout=30)
            self.assertNotEqual(result.returncode,0);self.assertIn('FileNotFoundError',result.stderr)
            self.assertNotIn('--prior-handoff required',result.stderr);self.assertNotIn('the following arguments are required',result.stderr)
    def test_internal_stage_command_omits_external_controller_only_options(self):
        c,args,engine,snapshot=self.fixture();engine.command.return_value=['python','gate'];c.stage(args.folder[0],'u0153','gate',m.SELF,['--internal-gate','parent','--attempt','1'],semantic=True)
        self.assertEqual(engine.command.call_args.args[2],['--unit','u0153','--internal-gate','parent','--attempt','1'])


if __name__=='__main__':unittest.main()
