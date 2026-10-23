"""Supervisor decisions are tested against synthetic process/bind snapshots."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import supervise_local_services as services


class LocalServicesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        python=self.root/'venv/Scripts/python.exe'; python.parent.mkdir(parents=True); python.touch()
        script=self.root/'runtime/server.py'; script.parent.mkdir(); script.touch()
        (python.parent.parent/'pyvenv.cfg').write_text('home = '+str(self.root/'base'))
        self.config=self.root/'config.json'
        self.config.write_text(json.dumps({'schema':1,'services':[{'name':'runtime','python':str(python),
            'script':str(script),'args':['--data-dir',str(self.root/'library'),'--port','15443','--dev'],
            'port':15443,'health':{'path':'/api','method':'POST','body':{'op':'settings','data':{}}}}]}))
        self.now=1000; self.listeners=[]; self.processes=[]
        self.supervisor=services.Supervisor(self.root,self.config,inspect=lambda:(self.listeners,self.processes),
            health=lambda s,r:True,clock=lambda:self.now)
        self.service=self.supervisor.services[0]
        self.supervisor.launch=Mock(return_value=17)

    def process(self,*,base=False,legacy=False,pid=41):
        argv=self.service.command()
        if base: argv[0]=str(self.root/'base/python.exe')
        if legacy: argv.remove('--no-auto-resume')
        return {'ProcessId':pid,'ExecutablePath':argv[0],'CreationDate':'fixed-creation', 'argv':argv,
                'CommandLine':subprocess.list2cmdline(argv)}

    def test_once_adopts_base_interpreter_and_legacy_runtime_without_start(self):
        self.processes=[self.process(base=True,legacy=True)]
        self.listeners=[{'LocalPort':15443,'OwningProcess':41}]
        self.assertTrue(self.supervisor.cycle(repair=False))
        self.supervisor.launch.assert_not_called()
        state=self.supervisor.state['services']['runtime']
        self.assertEqual(('healthy',41),(state['status'],state['pid']))
        self.assertNotIn('CommandLine',state)
        self.assertIn('--no-auto-resume',self.service.command())

    def test_unknown_owner_and_unresponsive_owned_process_never_restart(self):
        self.listeners=[{'LocalPort':15443,'OwningProcess':98}]
        self.supervisor.cycle(repair=True)
        self.assertEqual('foreign_port_owner',self.supervisor.state['services']['runtime']['status'])
        self.processes=[self.process()]; self.listeners[0]['OwningProcess']=41
        self.supervisor.health=lambda s,r:False
        self.supervisor.cycle(repair=True)
        self.assertEqual('unresponsive_owned_process',self.supervisor.state['services']['runtime']['status'])
        self.supervisor.launch.assert_not_called()

    def test_live_process_without_port_is_not_duplicated(self):
        self.processes=[self.process()]
        self.supervisor.cycle(repair=True)
        self.supervisor.launch.assert_not_called()
        self.assertEqual('owned_process_without_listener',self.supervisor.state['services']['runtime']['status'])

    def test_restarts_are_backed_off_and_budget_survives_new_supervisor(self):
        self.supervisor.cycle(repair=False); self.supervisor.launch.assert_not_called()
        for attempt in range(5):
            self.supervisor.cycle(repair=True)
            self.assertEqual(attempt+1,self.supervisor.launch.call_count)
            self.now+=1; self.supervisor.cycle(repair=True)
            self.assertEqual(attempt+1,self.supervisor.launch.call_count)
            self.now=self.supervisor.state['services']['runtime']['next_attempt']+1
        restored=services.Supervisor(self.root,self.config,inspect=lambda:([],[]),clock=lambda:self.now)
        restored.launch=Mock()
        restored.cycle(repair=True)
        restored.launch.assert_not_called()
        self.assertEqual('restart_budget_exhausted',restored.state['services']['runtime']['status'])

    def test_racing_external_start_is_not_duplicated(self):
        self.supervisor.inspect=Mock(side_effect=[([],[]),([{'LocalPort':15443,'OwningProcess':41}],[self.process()])])
        self.supervisor.cycle(repair=True)
        self.supervisor.launch.assert_not_called()
        self.assertEqual('start_raced_adopt_next_check',self.supervisor.state['services']['runtime']['status'])

    def test_script_or_interpreter_mismatch_is_rejected(self):
        process=self.process(); process['argv'][4]=str(self.root/'evil.py')
        self.assertFalse(services.matching(self.service,process,self.root))
        process=self.process(); process['ExecutablePath']='C:/elsewhere/python.exe'
        self.assertFalse(services.matching(self.service,process,self.root))


if __name__=='__main__': unittest.main()
