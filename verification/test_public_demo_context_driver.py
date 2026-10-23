"""Mocked context-driver stages; no real plan, provider, model or GPU is used."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('context_driver',ROOT/'scripts/finish_public_demo_context_recovery.py')
driver=importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class FakeEngine:
    def __init__(self,folders,fail_full=False):
        self.state={'books':{}}
        self.calls=[]
        self.selected={str(folder):False for folder in folders}
        self.fail_full=fail_full
        for folder in folders:
            driver.supervision.write(folder/'assignment.json',{'units':[{'id':'u1'}]})
            for take in range(6):
                driver.supervision.write(folder/'takes/u1'/('take-%02d.json'%take),{'fixture':True})

    def save(self):
        pass

    def book(self,folder,**fields):
        self.state['books'].setdefault(str(folder),{}).update(fields)

    def command(self,script,folder,extra=(),asr=False):
        return ['asr-python' if asr else 'tts-python',str(script),'--folder',str(folder),*extra]

    def run(self,name,folder,command,**kwargs):
        self.calls.append((name,folder,command,kwargs))
        if self.fail_full and name.endswith('full-gate'):
            raise driver.finishing.StageFailure('Actual source verb mismatch')
        if name.endswith('register'):
            self.selected[str(folder)]=True

    def resolve(self,folder,complete=False):
        selected=self.selected[str(folder)]
        return {'report':{'units':[{'id':'u1','selected':{'audio_sha256':'a'*64} if selected else None}],
                          'verified_units':int(selected),'open_recognition_flags':int(not selected),'pending_units':0},
                'report_sha256':'fixture','completion_valid':complete and selected,
                'completion_reason':None if selected else 'Still held','publication_eligible':selected}

    def publish_if_eligible(self,folder,snapshot):
        if snapshot['publication_eligible']:
            self.calls.append(('publish',folder,[],{}))


class ContextDriverGates(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.folders=[self.root/'en',self.root/'ru']
        self.args=SimpleNamespace(folder=self.folders,unit=[],prepare_only=False)

    def controller(self,fail=False):
        engine=FakeEngine(self.folders,fail)
        return driver.Controller(self.args,engine),engine

    def test_stage_order_requires_both_blind_checks_before_alignment_and_registration(self):
        controller,engine=self.controller()
        with patch.object(driver,'wait_previous') as guard:
            self.assertEqual(controller.run(),0)
        names=[name for name,folder,_,_ in engine.calls if folder==self.folders[0]]
        expected=['context-u1-plan','context-u1-propose','context-u1-a1-render','context-u1-a1-full-qwen',
                  'context-u1-a1-full-turbo','context-u1-a1-full-gate','context-u1-a1-align','context-u1-a1-crop',
                  'context-u1-a1-crop-qwen','context-u1-a1-crop-turbo','context-u1-a1-crop-gate',
                  'context-u1-a1-validate','context-u1-a1-register','publish','publish']
        self.assertEqual(names,expected)
        self.assertEqual(guard.call_count,12) # Six GPU stages per book, always serialized.
        self.assertTrue(all(command[0]=='asr-python' for name,_,command,_ in engine.calls
                            if name.endswith(('plan','propose','render'))))
        self.assertTrue(all(engine.state['books'][str(folder)]['status']=='complete' for folder in self.folders))

    def test_full_context_failure_never_calls_align_crop_or_register_and_continues_other_book(self):
        controller,engine=self.controller(fail=True)
        with patch.object(driver,'wait_previous'):
            self.assertEqual(controller.run(),1)
        names=[name for name,_,_,_ in engine.calls]
        self.assertFalse(any(name.endswith(('align','crop','validate','register')) for name in names))
        self.assertEqual(sum(name.endswith('render') for name in names),6)
        self.assertEqual(sum(name.endswith('full-gate') for name in names),6)
        self.assertEqual(engine.state['status'],'held')
        self.assertEqual(len(engine.state['context_units']),2)
        # A resumed controller keeps failed-strategy history and does not synthesize a fourth take.
        before=len(engine.calls)
        with patch.object(driver,'wait_previous'):
            self.assertEqual(controller.run(),1)
        self.assertFalse(any(name.endswith('render') for name,_,_,_ in engine.calls[before:]))

    def test_prepare_only_has_no_gpu_checks_or_registration(self):
        self.args.prepare_only=True
        controller,engine=self.controller()
        with patch.object(driver,'wait_previous',side_effect=AssertionError('GPU guard must not run')):
            self.assertEqual(controller.run(),0)
        self.assertEqual([name for name,_,_,_ in engine.calls],
                         ['context-u1-plan','context-u1-propose']*2)
        self.assertTrue(all(command[0]=='asr-python' for _,_,command,_ in engine.calls))
        self.assertEqual(engine.state['status'],'prepared')

    def test_config_mismatch_preserves_sidecar_and_supervision_history(self):
        path=self.root/'queue/context-controller-config.json'
        driver.pin_configuration(path,{'controller_sha256':'first','dependencies':{'module':'first'}})
        journal=path.parent/'finishing-supervision.json'
        journal.write_bytes(b'protected prior attempt history')
        before=path.read_bytes(),journal.read_bytes()
        with self.assertRaisesRegex(ValueError,'retained history'):
            driver.pin_configuration(path,{'controller_sha256':'changed','dependencies':{'module':'first'}})
        self.assertEqual((path.read_bytes(),journal.read_bytes()),before)

    def test_prior_owner_scan_checks_exact_scripts_folders_and_ignores_cpu_helpers_and_self(self):
        def process(pid,script,extra=(),folder=None,ppid=1):
            return {'pid':pid,'ppid':ppid,'name':'python.exe','cwd':str(ROOT),'create_time':float(pid),
                    'command':['python',str(script),'--folder',str(folder or self.folders[0]),*extra]}
        rows=[process(10,driver.finishing.SELF),
              process(20,driver.finishing.SELF,['--internal-stage','resolve']),
              process(30,driver.ROOT/'scripts/render_public_audiobook_demo.py',['--stage','all'],self.folders[1]),
              process(40,self.root/'finish_public_audiobook_demos.py'),
              process(50,driver.SELF),
              process(60,driver.finishing.SELF,folder=self.root/'unrelated'),
              process(70,Path(driver.context_module.__file__),['--stage','verify']),
              process(80,Path(driver.context_module.__file__),['--stage','propose'])]
        busy=driver.previous_jobs(rows,self.folders,50)
        self.assertEqual([row['pid'] for row in busy],[10,30,70])

    def test_orphan_turbo_recovery_and_music_gpu_workers_block_but_cpu_modes_do_not(self):
        def row(pid,name,flags):
            return {'pid':pid,'ppid':1,'name':'python.exe','cwd':str(ROOT),'create_time':float(pid),
                    'command':['python',str(ROOT/'scripts'/name),*flags]}
        folder=['--folder',str(self.folders[0])]
        rows=[row(101,'witness_public_demo_turbo.py',[*folder,'--stage','witness']),
              row(102,'recover_public_audiobook_demo.py',[*folder,'--stage','render']),
              row(103,'prepare_public_demo_music.py',['--stage','verify','--audio',str(self.folders[1]/'music/raw.wav')]),
              row(104,'prepare_public_demo_music.py',[*folder,'--stage','render']),
              row(105,'witness_public_demo_turbo.py',[*folder,'--stage','plan']),
              row(106,'recover_public_audiobook_demo.py',[*folder,'--stage','propose']),
              row(107,'prepare_public_demo_music.py',[*folder,'--stage','prepare']),
              row(108,'totally_unrelated.py',folder)] # PID reuse/stale journal cannot make this an owner.
        busy=driver.previous_jobs(rows,self.folders,50)
        self.assertEqual([item['pid'] for item in busy],[101,102,103,104])

    def test_second_controller_fails_closed_without_mutual_waiting(self):
        controller,engine=self.controller()
        command=['python',str(driver.SELF),'--folder',str(self.folders[0])]
        fake=SimpleNamespace(info={'name':'python.exe'},pid=99991,ppid=lambda:1,name=lambda:'python.exe',
                             cmdline=lambda:command,cwd=lambda:str(ROOT),create_time=lambda:123.0)
        self.args.wait_finisher=None
        self.args.wait_deadline_seconds=10
        self.args.poll_seconds=.001
        with patch('psutil.process_iter',return_value=[fake]),patch.object(driver.time,'sleep',side_effect=AssertionError('No mutual wait')):
            with self.assertRaisesRegex(driver.OwnershipConflict,'Another context controller'):
                driver.wait_previous(engine,self.args)
        self.assertTrue(engine.state['prior_owner_wait']['busy'][0]['other_controller'])


if __name__=='__main__':
    unittest.main()
