"""CPU fixtures for separate native output capture; no speech or providers."""
import json,sys,tempfile,types,unittest,wave
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import generate_public_demo_current as m

class Capture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.session=self.root/'sessions'/'fixture';self.session.mkdir(parents=True)
        m.write(self.session/'physical-start.json',{'capture_policy':'separate-native-stdout-stderr-v1'})

    def test_interrupted_stopped_worker_binds_stderr_and_detects_tamper(self):
        m.write(self.session/'worker.json',{'pid':1,'create_time':0,'command':['fixture']})
        errors=self.session/'native.stderr.log';errors.write_bytes(b'max_tokens reached before EOC\n')
        with patch.object(m,'live',return_value=False):terminal=m.terminal_session(self.session)
        self.assertEqual(terminal['stderr_sha256'],m.file_digest(errors))
        self.assertIn('max_tokens',m.stderr_text(self.session,terminal))
        errors.write_bytes(b'changed')
        with self.assertRaises(ValueError):m.stderr_text(self.session,terminal)

    def test_unknown_worker_stays_fail_closed(self):
        (self.session/'native.stderr.log').write_bytes(b'backend startup')
        with self.assertRaisesRegex(ValueError,'Unknown physical worker'):m.terminal_session(self.session)

    def test_capture_policy_cannot_be_dropped_from_result(self):
        with self.assertRaisesRegex(ValueError,'capture policy'):m.stderr_text(self.session,{'returncode':0})

    def test_completed_stdout_ignores_only_separately_bound_stderr(self):
        ident='u0000-technical-0';wav=self.session/'native'/(ident+'.wav');wav.parent.mkdir()
        with wave.open(str(wav),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(b'\0\0'*2400)
        spec={'source':{'id':'u0000'},'attempt':0,'request':{'id':ident}}
        m.write(self.session/'request.json',{'requests':[spec['request']]})
        m.write(self.session/'physical-start.json',{'capture_policy':'separate-native-stdout-stderr-v1','specs':[spec],
            'sequence_sha256':m.file_digest(self.session/'request.json')})
        (self.session/'native.log').write_text(f'audio_out={wav}\nmetrics[{ident}].audio_duration_ms=100\n')
        errors=self.session/'native.stderr.log';errors.write_text('CUDA warmup diagnostic\n')
        m.write(self.session/'physical-result.json',{'returncode':0,'error':None,'capture_policy':'separate-native-stdout-stderr-v1',
            'stderr_sha256':m.file_digest(errors)})
        m.collect(self.root,self.session)
        self.assertEqual(m.read(self.root/'attempts/u0000/0.json')['status'],'rendered')

    def test_watchdog_ignores_heartbeats_but_sees_actual_native_changes(self):
        output=self.root/'output';job='a'*64;session='b'*64
        m.write(output/'current-job.json',{'job':job})
        m.write(output/'progress.json',{'stage':'native','session':session,'counts':{'rendered':1},'log_bytes':100,'updated_unix':1})
        a=m.observed_progress(output,20)
        m.write(output/'progress.json',{'stage':'native','session':session,'counts':{'rendered':1},'log_bytes':100,'updated_unix':200})
        self.assertEqual(a,m.observed_progress(output,20))
        folder=output/'jobs'/job/'sessions'/session;folder.mkdir(parents=True)
        (folder/'native.log').write_bytes(b'actual stdout')
        b=m.observed_progress(output,20);self.assertNotEqual(a,b)
        (folder/'native.stderr.log').write_bytes(b'actual stderr')
        self.assertNotEqual(b,m.observed_progress(output,20))

if __name__=='__main__':unittest.main()
