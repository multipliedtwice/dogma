import json,os,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
from report_run_events import report,load


class RunEventsTests(unittest.TestCase):
    def test_events_attribute_time_stages_and_retake_causes(self):
        from rimsky import run_events
        with tempfile.TemporaryDirectory() as folder:
            log=Path(folder)/'events.jsonl';os.environ['RIMSKY_EVENT_LOG']=str(log)
            try:
                run_events.event('run.start')
                run_events.section('c0003/chunk-000');run_events.mark('take-0')
                run_events.event('native',seconds=60,generation_seconds=50,overhead_seconds=10)
                run_events.event('take.result',unit='u1',attempt=0,unsafe_start=True,unsafe_end=False,word_status='asr_exact_match',heard='a')
                run_events.event('take.result',unit='u2',attempt=0,unsafe_start=False,unsafe_end=False,word_status='asr_exact_match',heard='b')
                run_events.mark('take-1')
                run_events.event('take.result',unit='u1',attempt=1,unsafe_start=False,unsafe_end=False,word_status='asr_exact_match',heard='a')
                run_events.event('unit.final',unit='u1',take='take-1',via='primary',rules=[])
                run_events.event('publish',chapter='c0003',minutes=6.0)
            finally:os.environ.pop('RIMSKY_EVENT_LOG')
            r=report(load(log))
        self.assertEqual(r['first_takes']['causes'],{'clipped start':1,'passed':1})
        self.assertEqual(list(r['retaken_units']),['u1']);self.assertEqual(r['retaken_units']['u1']['final_take'],'take-1')
        self.assertEqual(r['higgs']['load_overhead_minutes'],round(10/60,1))
        self.assertIn('take-0',r['stage_minutes']);self.assertEqual(len(r['publish_timeline']),1)


if __name__=='__main__':unittest.main()
