"""Broken compact publication leaves a durable visible hold, never legacy work."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts'),str(ROOT/'verification')]
from server import Application
from rimsky.listener_stress_retake import ENGINE,record_enqueue_failure,queue_status_index
from rimsky.util import digest,write_json,now
from test_series_character_state import fb2


class EnqueueFailurePersistence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.data=self.base/'library';self.profile=self.base/'profile.sqlite3'
        self.app=Application(self.data,ROOT,auto_resume=False,correction_profile=self.profile)
        self.addCleanup(lambda:self.app.close() if self.app else None)
        self.book=self.app.store.import_data(fb2('Synthetic IV',4,'Это слово.'),'test.fb2')
        self.g=self.app.store.create_generation(self.book['id'],{})
        self.app.store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=10 WHERE generation_id=?",('d'*64,self.g['id']))
        self.segment=next(s for s in self.app.store.generation(self.g['id'])['segments']if 'слово'in s['text'])
        self.folder=self.base/('current-book-'+self.book['id']);self.folder.mkdir()
        write_json(self.folder/'current-output.json',{'policy':'current-book-output-v1'})
        write_json(self.folder/'ready.json',{'policy':'wrong-policy'})
        self.app.store.execute('UPDATE generations SET config=?,analysis=? WHERE id=?',
            (json.dumps({'engine':ENGINE}),json.dumps({'external_preview':{'generation':self.folder.name}}),self.g['id']))

    def submit(self):
        s=self.segment
        with patch.object(self.app.jobs,'listener_stress',side_effect=AssertionError('normal renderer fallback')), \
                patch('rimsky.listener_stress_retake._start_worker',side_effect=AssertionError('native worker')):
            return self.app.dispatch('report_stress',{'generation_id':self.g['id'],'segment_id':s['id'],
                'source_id':s['source_id'],'selected_text':'слово','text_offset':s['start']+s['text'].index('слово'),
                'intended_stress':'сло́во','audio_hash':'d'*64})

    def test_api_hold_survives_application_reopen_and_keeps_raw_note_immutable(self):
        report=self.submit();self.assertTrue(report['retake_queue']['durable'])
        self.assertEqual('held',report['retake_queue']['status'])
        before=self.app.store.stress_reports(self.book['id'])
        self.app.close();self.app=None
        reopened=Application(self.data,ROOT,auto_resume=False,correction_profile=self.profile)
        try:
            visible=reopened.dispatch('stress_reports',{'book_id':self.book['id']})
            self.assertEqual(1,len(visible));self.assertEqual('held',visible[0]['retake_queue']['status'])
            self.assertIn('incompatible policy',visible[0]['retake_queue']['error'])
            self.assertEqual(before,reopened.store.stress_reports(self.book['id']))
            jobs=reopened.store.rows('SELECT * FROM listener_correction_jobs')
            self.assertEqual(1,len(jobs));self.assertEqual('held',jobs[0]['status'])
            self.assertEqual([],json.loads(jobs[0]['segment_ids']))
        finally:reopened.close()

    def test_accepted_external_delivery_supersedes_failure_fallback_without_erasing_it(self):
        report=self.submit();raw=self.app.store.stress_reports(self.book['id'])[0]
        manifest={'policy':'current-book-output-v1','unit_order':[self.segment['id']],'parts':{self.segment['id']:{}}}
        manifest['sha256']=digest(manifest)
        write_json(self.folder/'current-output.json',manifest)
        write_json(self.folder/'ready.json',{'policy':'current-book-output-v1','unit_count':1})
        write_json(self.folder/'manifest.json',{})
        write_json(self.folder/'progress.json',{'generation':self.folder.name,'status':'complete'})
        write_json(self.folder/'listener-stress-queue/applied.json',{'id':'delivery','status':'applied',
            'report':raw,'created':now(),'updated':now(),'published_generation_id':self.g['id'],
            'affected_segment_ids':[self.segment['id']]})
        status=queue_status_index(self.app.store,self.book['id'])[report['id']]
        self.assertEqual('applied',status['status'])
        self.assertEqual(self.g['id'],status['published_generation_id'])
        self.assertEqual('held',self.app.store.rows('SELECT status FROM listener_correction_jobs')[0]['status'])
        self.assertEqual([raw],self.app.store.stress_reports(self.book['id']))

    def test_duplicate_failure_record_is_idempotent_and_does_not_schedule_work(self):
        report=self.submit()
        first=self.app.store.rows('SELECT * FROM listener_correction_jobs')
        record_enqueue_failure(self.app.store,report,RuntimeError('later observation'))
        self.assertEqual(first,self.app.store.rows('SELECT * FROM listener_correction_jobs'))
        self.assertEqual('held',queue_status_index(self.app.store,self.book['id'])[report['id']]['status'])

    def test_normal_runtime_retry_is_not_masked_by_prior_enqueue_failure(self):
        report=self.submit()
        self.app.store.execute('UPDATE generations SET config=? WHERE id=?',('{}',self.g['id']))
        raw=self.app.store.stress_reports(self.book['id'])[0]
        with patch.object(self.app.jobs,'_start_listener_queue') as start:
            result=self.app.jobs.listener_stress(raw)
        self.assertEqual('queued',result['status']);start.assert_called_once()
        status=queue_status_index(self.app.store,self.book['id'])[report['id']]
        self.assertEqual('queued',status['status'])
        self.assertEqual(1,len(self.app.store.rows("SELECT id FROM listener_correction_jobs WHERE id LIKE 'enqueue-failure-%' AND status='held'")))

    def test_starting_a_generation_does_not_turn_an_enqueue_hold_into_running_work(self):
        from unittest.mock import Mock
        self.submit()
        self.app.store.execute("UPDATE generations SET config='{}',analysis=?,status='paused' WHERE id=?",
            (json.dumps({'complete':True,'approved':True}),self.g['id']))
        try:
            with patch.object(self.app.jobs,'_pack',return_value={}),patch('rimsky.jobs.threading.Thread',return_value=Mock()):
                self.app.jobs.start(self.g['id'])
            held=self.app.store.rows("SELECT status FROM listener_correction_jobs WHERE id LIKE 'enqueue-failure-%'")
            self.assertEqual([{'status':'held'}],held)
        finally:
            self.app.jobs.active=None;self.app.jobs.thread=None


if __name__=='__main__':unittest.main()
