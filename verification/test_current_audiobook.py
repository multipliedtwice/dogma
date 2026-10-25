"""Current output replacement, CAS, export leases and actual-heard provenance."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from verification.test_core import FB2,Store,complete_audio,write_wave,write_json,file_digest,digest,AppError,build_export


class CurrentAudiobook(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=Store(self.root/'library');self.addCleanup(self.store.close)
        self.book=self.store.import_data(FB2,'fixture.fb2')
        self.config={'pack_path':'unused','pack_fingerprint':'fixture','device':'cpu'}
        self.g=self.store.create_generation(self.book['id'],self.config);complete_audio(self.store,self.g)
        self.g=self.store.generation(self.g['id']);self.old=next(s for s in self.g['segments']if 'Маша'in s['text'])
    def replacement(self,old=None):
        row=copy.deepcopy(old or self.old);key=digest(['replacement',row['id']])
        path=self.store.audio_path(key);metadata=write_wave(path,seconds=.2);metadata['sha256']=file_digest(path);write_json(path.with_suffix('.json'),metadata)
        row.update(audio_hash=key,duration=.2,status='ready',cues=[]);return row
    def pending(self):
        return self.store.revise(self.g['id'],[{'id':self.old['id'],'spoken':'Новая речь.'}])
    def test_schema_four_upgrade_keeps_source_audio_and_listener_notes(self):
        import sqlite3
        from contextlib import closing
        report=self.store.report_stress(self.g['id'],self.old['id'],self.old['source_id'],'Маша',
            kind='other',comment='Preserve this note',audio_hash=self.old['audio_hash'])
        before=self.store.generation(self.g['id']);source=self.store.book(self.book['id'])['source_hash']
        self.store.close()
        with closing(sqlite3.connect(self.root/'library/library.sqlite3')) as db,db:
            for table in ('current_audiobooks','audiobook_replacements','audiobook_part_receipts','audio_leases','export_snapshots'):
                db.execute('DROP TABLE '+table)
            db.execute('PRAGMA user_version=4')
        self.store=Store(self.root/'library');self.addCleanup(self.store.close)
        self.assertEqual(5,self.store.db.execute('PRAGMA user_version').fetchone()[0])
        self.assertEqual(before,self.store.generation(self.g['id']))
        self.assertEqual(source,self.store.book(self.book['id'])['source_hash'])
        self.assertEqual(report['id'],self.store.stress_reports(self.book['id'])[0]['id'])
        self.assertEqual(self.old['audio_hash'],next(s for s in self.store.playback_generation(self.g['id'])['segments']if s['id']==self.old['id'])['audio_hash'])
    def test_regenerate_keeps_one_identity_and_ready_fallback(self):
        new=self.store.create_generation(self.book['id'],self.config)
        self.assertEqual(new['id'],self.g['id']);self.assertEqual(len(self.store.generation_list(self.book['id'])),1)
        playable=self.store.playback_generation(new['id']);self.assertEqual(playable['segments'][0]['status'],'ready')
        self.assertEqual(self.store.book(self.book['id'])['current_generation_id'],new['id'])
    def test_revision_does_not_clone_and_old_part_is_playable_until_commit(self):
        new=self.pending();self.assertEqual(new['id'],self.g['id'])
        raw=next(s for s in new['segments']if s['id']==self.old['id']);self.assertIsNone(raw['audio_hash'])
        before=self.store.playback_generation(new['id']);old=next(s for s in before['segments']if s['id']==raw['id'])
        self.assertEqual(old['audio_hash'],self.old['audio_hash']);self.assertEqual(old['replacement_status'],'pending')
        self.assertEqual(before['preparation_progress']['ready'],len(self.g['segments'])-1)
        self.assertTrue(all(row['status']=='ready'for row in before['segments']))
        row=self.replacement(raw)
        self.store.publish_current_parts(new['id'],[row],expected_revision=before['revision'])
        current=self.store.playback_generation(new['id']);self.assertEqual(current['pending_replacements'],0)
        self.assertEqual(next(s for s in current['segments']if s['id']==row['id'])['audio_hash'],row['audio_hash'])
    def test_stale_revision_cannot_overwrite_newer_correction(self):
        revision=self.g['revision'];self.pending()
        with self.assertRaises(AppError):self.store.publish_current_parts(self.g['id'],[self.replacement()],expected_revision=revision)
        self.assertEqual(self.store.playback_generation(self.g['id'])['pending_replacements'],1)
    def test_failure_after_parts_rolls_back_audio_revision_and_current_pointer(self):
        self.pending();before=self.store.generation(self.g['id']);fallback=self.store.rows('SELECT * FROM audiobook_replacements')
        with patch.object(self.store,'complete_replacements',side_effect=RuntimeError('injected after part SQL')):
            with self.assertRaises(RuntimeError):self.store.publish_current_parts(self.g['id'],[self.replacement()],expected_revision=before['revision'],metadata={'name':'Must roll back'})
        self.assertEqual(self.store.generation(self.g['id']),before)
        self.assertEqual(self.store.rows('SELECT * FROM audiobook_replacements'),fallback)
    def test_wrong_source_range_is_rejected_without_commit(self):
        row=self.replacement();row['text']='Unrelated source'
        with self.assertRaises(AppError):self.store.publish_current_parts(self.g['id'],[row])
        self.assertEqual(self.store.generation(self.g['id']),self.g)
    def test_fault_after_pointer_write_does_not_commit_nested_helpers(self):
        self.pending();before=self.store.generation(self.g['id']);original=self.store.set_current_generation
        def fail(*args):original(*args);raise RuntimeError('after pointer SQL')
        with patch.object(self.store,'set_current_generation',side_effect=fail):
            with self.assertRaises(RuntimeError):self.store.publish_current_parts(self.g['id'],[self.replacement()],metadata={'name':'rolled back'})
        self.assertEqual(self.store.generation(self.g['id']),before)
    def test_split_part_keeps_old_whole_until_all_children_are_valid(self):
        old=self.old;mid=(old['start']+old['end'])//2
        self.store.execute('DELETE FROM segments WHERE generation_id=? AND id=?',(self.g['id'],old['id']))
        children=[]
        for index,(start,end)in enumerate(((old['start'],mid),(mid,old['end']))):
            part={**old,'id':old['id']+'-'+str(index),'ordinal':old['ordinal']+index,'start':start,'end':end,'text':old['text'][start-old['start']:end-old['start']]}
            part['spoken']=part['text'];part=self.replacement(part);children.append(part)
            self.store.publish_current_parts(self.g['id'],[part])
            shown=self.store.playback_generation(self.g['id'])['segments']
            self.assertEqual(any(s['id']==old['id']for s in shown),index==0)
        self.assertTrue(all(any(s['id']==part['id']for s in shown)for part in children))
    def test_fallback_survives_gc_then_retired_hash_does_not_pin_old_wav(self):
        self.pending();path=self.store.audio_path(self.old['audio_hash']);self.store.collect_audio();self.assertTrue(path.exists())
        self.store.publish_current_parts(self.g['id'],[self.replacement()]);self.store.collect_audio();self.assertFalse(path.exists())
        receipts=self.store.rows('SELECT * FROM audiobook_part_receipts WHERE segment_id=?',(self.old['id'],));self.assertEqual(receipts[0]['audio_hash'],self.old['audio_hash'])
    def test_old_tab_feedback_binds_retired_heard_hash_even_after_wav_gc(self):
        self.pending();self.store.publish_current_parts(self.g['id'],[self.replacement()]);self.store.collect_audio()
        report=self.store.report_stress(self.g['id'],self.old['id'],self.old['source_id'],'Маша',self.old['text'].index('Маша')+self.old['start'],kind='other',comment='Actual old clip',audio_hash=self.old['audio_hash'],audio_revision=self.g['revision'])
        self.assertEqual(report['audio_hash'],self.old['audio_hash']);self.assertEqual(report['spoken_text'],self.old['spoken']);self.assertEqual(report['heard_binding'],'retired_receipt')
    def test_unknown_hash_is_rejected_and_legacy_report_does_not_claim_current_take(self):
        kwargs={'generation_id':self.g['id'],'segment_id':self.old['id'],'source_id':self.old['source_id'],'selected_text':'Маша','kind':'other','comment':'Fixture'}
        with self.assertRaises(AppError):self.store.report_stress(**kwargs,audio_hash='a'*64)
        report=self.store.report_stress(**kwargs);self.assertIsNone(report['audio_hash']);self.assertEqual(report['heard_binding'],'unknown')
    def test_migrated_report_defaults_do_not_change_immutable_journal_evidence(self):
        from rimsky.listener_stress import record,snapshot
        with patch('rimsky.listener_stress.record'):
            raw=self.store.report_stress(self.g['id'],self.old['id'],self.old['source_id'],self.old['text'],kind='other',comment='Before heard identity columns')
        raw.pop('heard_binding');raw.pop('heard_revision')
        self.store.execute("UPDATE stress_reports SET heard_binding='legacy_unknown',heard_revision=NULL WHERE id=?",(raw['id'],))
        record(self.store,self.book,raw)
        self.assertEqual(self.store.stress_reports(self.book['id']),[raw])
        snapshot(self.store,self.book)
        self.assertEqual(self.store.stress_reports(self.book['id']),[raw])
    def test_live_export_lease_preserves_unused_source_until_release(self):
        row=self.replacement();path=self.store.audio_path(row['audio_hash'])
        with self.store.audio_lease([row['audio_hash']]):self.store.collect_audio();self.assertTrue(path.exists())
        self.store.collect_audio();self.assertFalse(path.exists())
    def test_export_snapshots_current_playable_audio_and_discloses_pending_update(self):
        self.pending();result=build_export(self.store,self.g['id'],'vtt',None,self.root)
        self.assertEqual(result['pending_updates'],1)
        snapshot=json.loads(self.store.rows('SELECT payload FROM export_snapshots WHERE export_id=?',(result['id'],))[0]['payload'])
        old=next(s for s in snapshot['parts']if s['id']==self.old['id']);self.assertEqual(old['audio_hash'],self.old['audio_hash'])
        self.assertFalse(self.store.rows('SELECT * FROM audio_leases'))
    def test_export_keeps_its_captured_audio_during_a_concurrent_part_commit_and_gc(self):
        from rimsky import export
        self.pending();replacement=self.replacement();original=export.capture_audio;replaced=[]
        def capture(source,*args,**kwargs):
            if source.stem==self.old['audio_hash']and not replaced:
                replaced.append(True);self.store.publish_current_parts(self.g['id'],[replacement]);self.store.collect_audio()
                self.assertTrue(source.exists(),'The active export lease must keep its captured old audio')
            return original(source,*args,**kwargs)
        with patch.object(export,'capture_audio',side_effect=capture):result=build_export(self.store,self.g['id'],'vtt',None,self.root)
        self.assertTrue(replaced);self.assertTrue((self.store.root/'exports'/result['file']).exists())
        self.store.collect_audio();self.assertFalse(self.store.audio_path(self.old['audio_hash']).exists())
    def test_legacy_outputs_retire_to_receipts_and_old_links_resolve_current(self):
        old=self.g['id'];current='f'*24
        self.store.execute('INSERT INTO generations(id,book_id,name,created,updated,status,config,cast,analysis) SELECT ?,book_id,name,created,updated,status,config,"cast",analysis FROM generations WHERE id=?',(current,old))
        columns='id,ordinal,chapter_id,source_id,start,end,text,spoken,speaker,delivery,status,audio_hash,duration,cues,warning,performance'
        self.store.execute(f'INSERT INTO segments(generation_id,{columns}) SELECT ?,{columns} FROM segments WHERE generation_id=?',(current,old))
        self.store.set_current_generation(self.book['id'],current)
        self.store.publish_current_parts(current,[{**self.replacement(),'generation_id':current}])
        result=self.store.retire_previous_outputs(self.book['id']);self.assertEqual(result['retired_generation_ids'],[old])
        self.assertFalse(self.store.generation(old)['segments']);self.assertEqual(self.store.generation(old)['status'],'retired')
        alias=self.store.playback_generation(old);self.assertEqual(alias['id'],current);self.assertEqual(alias['replaces_generation_id'],old)
        self.assertEqual(self.store.playback_generation_poll(old,1)['generation']['id'],current)
        self.store.collect_audio();self.assertFalse(self.store.audio_path(self.old['audio_hash']).exists())
        report=self.store.report_stress(old,self.old['id'],self.old['source_id'],'Маша',self.old['start']+self.old['text'].index('Маша'),kind='other',comment='Old tab',audio_hash=self.old['audio_hash'])
        self.assertEqual(report['generation_id'],old);self.assertEqual(report['audio_hash'],self.old['audio_hash']);self.assertEqual(report['heard_binding'],'retired_receipt')
        self.store.delete_generation(current)
        self.assertIsNone(self.store.current_generation_id(self.book['id']));self.assertIsNone(self.store.current_generation(self.book['id']))
        self.assertEqual(self.store.book(self.book['id'])['generations'],[])
    def test_another_store_cannot_publish_against_an_old_revision(self):
        other=Store(self.store.root)
        try:
            revision=self.g['revision'];other.status(self.g['id'],'paused')
            with self.assertRaises(AppError):self.store.publish_current_parts(self.g['id'],[self.replacement()],expected_revision=revision)
        finally:other.close()
    def test_retirement_refuses_an_active_old_correction(self):
        current='e'*24;old=self.g['id']
        self.store.execute('INSERT INTO generations(id,book_id,name,created,updated,status,config,cast,analysis) SELECT ?,book_id,name,created,updated,status,config,"cast",analysis FROM generations WHERE id=?',(current,old))
        self.store.set_current_generation(self.book['id'],current)
        self.store.execute("INSERT INTO listener_correction_jobs(id,report_id,book_id,generation_id,segment_ids,status,created,updated) VALUES(?,?,?,?,?,?,?,?)",('active-correction','report',self.book['id'],old,'[]','queued','now','now'))
        with self.assertRaises(AppError):self.store.retire_previous_outputs(self.book['id'])
        self.assertTrue(self.store.generation(old)['segments'])
    def test_cross_generation_or_chapter_part_cannot_be_published(self):
        for field,value in (('generation_id','0'*24),('chapter_id','foreign-chapter')):
            with self.subTest(field=field):
                row=self.replacement();row[field]=value
                with self.assertRaises(AppError):self.store.publish_current_parts(self.g['id'],[row])
        self.assertEqual(self.store.generation(self.g['id']),self.g)
    def test_retired_heard_lookup_never_substitutes_same_hash_from_current_navigation(self):
        from rimsky.current_audiobook import heard_part
        from unittest.mock import Mock
        store=Mock();store.generation.return_value={'analysis':{'retired':True}}
        store.rows.return_value=[{'payload':json.dumps({'source_id':'actual-old-source','audio_hash':'same-hash'})}]
        selected=heard_part(store,'old','part','same-hash')
        self.assertEqual(selected['source_id'],'actual-old-source');store.playback_generation.assert_not_called()


if __name__=='__main__':unittest.main()
