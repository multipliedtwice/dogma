"""V16 stress-retake selection and real internal CLI tests, with no model calls."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import finish_public_demo_line_wrap_recovery as m

class Engine:
    def __init__(self,folder,fail=None):
        self.state={'stages':{},'books':{}};self.calls=[];self.selected=False;self.fail=fail
        m.supervision.write(folder/'assignment.json',{'units':[{'id':'u0132'}]})
    def save(self):pass
    def book(self,f,**kw):self.state['books'].setdefault(str(f),{}).update(kw)
    def command(self,script,folder,extra=(),asr=False):return ['asr' if asr else 'tts',str(script),'--folder',str(folder),*extra]
    def snapshot(self):return {'report':{'units':[dict(id='u0132',selected={'take':27} if self.selected else None)],'verified_units':154+int(self.selected),'open_recognition_flags':4-int(self.selected),'pending_units':0},'report_sha256':'fixture'}
    def run(self,name,f,cmd,**kw):
        self.calls.append((name,cmd))
        if self.fail and self.fail(name):raise m.finishing.StageFailure('source mismatch or optional witness failure')
        if name.endswith('register'):self.selected=True
    def resolve_v16(self,folder):self.calls.append(('resolve',[]));return self.snapshot()

class Driver(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.folder=Path(self.tmp.name)/'ru-chameleon';self.folder.mkdir()
        self.args=SimpleNamespace(folder=[self.folder],unit=['u0132'],output=Path(self.tmp.name)/'out',prior_worker=Path(self.tmp.name)/'worker.json',operational_retries=1,defer_final_report=False)
    def make(self,fail=None):
        e=Engine(self.folder,fail);s=e.snapshot();c=m.Controller(self.args,e,{'selection_only_baseline_sha256':m.supervision.digest(s),'review_holds':{},'approved_choices':{'u0132':[m.helper.CANDIDATE]}});return c,e,s
    def execute(self,c,s,outcomes):
        with patch.object(c,'recheck'),patch.object(m,'baseline',return_value=s),patch.object(m,'wait_previous'),\
             patch.object(m.helper,'campaign',return_value={'stress_witness':{'enabled':True}}),\
             patch.object(m.helper,'validate_stress_record',side_effect=lambda f,u,a,b,required=False:outcomes[a]):return c.run()
    def clean(self):return {'status':'measured','result':{'suspect':False},'retake_trigger':False,'stress_approval':False}
    def suspect(self,a):return {'status':'measured','result':{'suspect':True},'retake_trigger':a<3,'stress_approval':False}
    def test_word_pcm_stress_validate_then_register_exactly_once(self):
        c,e,s=self.make();self.assertEqual(self.execute(c,s,{1:self.clean()}),0)
        names=[n for n,_ in e.calls];p='line-wrap-u0132-a1-'
        self.assertEqual([n.removeprefix(p) for n in names[:-2]],['render','native-gate','context-qwen','context-turbo','context-gate','align','crop','crop-qwen','crop-turbo','crop-gate','stress','validate'])
        self.assertEqual(names[-2: ],['line-wrap-u0132-selected-a1-register','resolve']);self.assertFalse(e.state['book_complete'])
    def test_suspect_earlier_valid_survives_two_later_failures(self):
        c,e,s=self.make(lambda n:n in ('line-wrap-u0132-a2-context-gate','line-wrap-u0132-a3-native-gate'))
        self.assertEqual(self.execute(c,s,{1:self.suspect(1)}),0);names=[n for n,_ in e.calls]
        self.assertLess(names.index('line-wrap-u0132-a3-native-gate'),names.index('line-wrap-u0132-selected-a1-register'))
        self.assertEqual(sum(n.endswith('register') for n in names),1);self.assertEqual(c.units['u0132']['selected_candidate']['attempt'],1)
    def test_later_unsuspected_wins_and_third_is_not_rendered(self):
        c,e,s=self.make();self.assertEqual(self.execute(c,s,{1:self.suspect(1),2:self.clean()}),0)
        names=[n for n,_ in e.calls];self.assertIn('line-wrap-u0132-selected-a2-register',names);self.assertFalse(any('-a3-' in n for n in names))
        self.assertFalse(any(n.endswith('register') for n in names[:names.index('line-wrap-u0132-a2-stress')]))
    def test_error_and_unmeasured_are_not_holds_or_retake_triggers(self):
        for status in ('error','unmeasured'):
            c,e,s=self.make();outcome={'status':status,'result':None,'retake_trigger':False,'independent_hold':False,'stress_approval':False}
            self.assertEqual(self.execute(c,s,{1:outcome}),0);self.assertEqual(sum(n.endswith('render') for n,_ in e.calls),1)
    def test_full_mismatch_never_aligns_or_measures_stress(self):
        c,e,s=self.make(lambda n:n.endswith('context-gate'));self.assertEqual(self.execute(c,s,{}),1)
        self.assertFalse(any(n.endswith(('align','stress','register')) for n,_ in e.calls));self.assertEqual(sum(n.endswith('render') for n,_ in e.calls),3)
    def test_backend_witness_failure_uses_explicit_nonholding_error_record(self):
        c,e,s=self.make(lambda n:n.endswith('-stress'));outcome={'status':'error','result':None,'retake_trigger':False}
        with patch.object(c,'stress_error') as error:self.assertEqual(self.execute(c,s,{1:outcome}),0)
        self.assertEqual(error.call_count,1);self.assertEqual(c.units['u0132']['selected_candidate']['attempt'],1)
    def test_real_subprocess_parser_every_self_gate_has_required_predecessor_arg(self):
        c,e,s=self.make()
        for flags in (['--native-gate'],['--internal-gate','context'],['--internal-gate','crop']):
            with self.subTest(flags=flags),patch.object(c,'recheck'):
                c.stage(self.folder,'u0132','parser-'+flags[-1],m.SELF,[*flags,'--attempt','1'],semantic=True)
                _,cmd=e.calls[-1];self.assertIn('--prior-worker',cmd)
                result=subprocess.run([sys.executable,'-X','utf8',*cmd[1:]],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=30,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                self.assertNotEqual(result.returncode,2,result.stderr);self.assertIn('frozen_context',result.stderr);self.assertIn('FileNotFoundError',result.stderr)
    def test_actual_predecessor_liveness_identity_and_source_are_required(self):
        self.args.prior_worker=self.folder.parent/'prior/worker.json';self.args.prior_worker.parent.mkdir()
        script=ROOT/'scripts/finish_public_demo_supplement_context_recovery_v2.py'
        worker={'pid':123,'create_time':4.,'controller_sha256':m.supervision.file_hash(script),'command':['python',str(script),'--folder',str(self.folder),'--output',str(self.args.prior_worker.parent)]}
        m.supervision.write(self.args.prior_worker,worker);m.supervision.write(self.args.prior_worker.parent/'finishing-supervision.json',{'status':'held','stages':{}})
        with patch('psutil.Process',return_value=object()),patch('psutil.process_iter',return_value=[]),patch.object(m.supervision,'live',return_value=False):self.assertTrue(m.validate_predecessor(self.args)['identities_stopped'])
        with patch('psutil.Process',return_value=object()),patch.object(m.supervision,'live',return_value=True):
            with self.assertRaises(m.ownership.OwnershipConflict):m.validate_predecessor(self.args)
        worker['controller_sha256']='0'*64;m.supervision.write(self.args.prior_worker,worker)
        with self.assertRaises(m.InputBindingConflict):m.validate_predecessor(self.args)
    def test_deferred_mode_registers_selection_then_hands_off_without_report(self):
        c,e,s=self.make();self.args.defer_final_report=True
        with patch.object(c,'handoff',return_value=0) as handoff:self.assertEqual(self.execute(c,s,{1:self.clean()}),0)
        handoff.assert_called_once_with(self.folder);self.assertFalse(any(n=='resolve' for n,_ in e.calls))
        self.assertEqual(c.units['u0132']['status'],'registered_pending_revalidation')
    def test_independent_review_rejection_consumes_zero_native_attempts(self):
        c,e,s=self.make();c.pin['review_holds']={'u0132':{'reason':'Substantive independent rejection'}}
        self.assertEqual(self.execute(c,s,{}),1);self.assertEqual(e.calls,[('resolve',[])])

if __name__=='__main__':unittest.main()
