"""Human stress must survive fresh generations and automatic conflicting marks."""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'verification'),str(ROOT/'scripts')]
from rimsky.listener_stress import apply,snapshot,POLICY
from rimsky.jobs import Jobs
from rimsky.store import Store
from rimsky.util import digest, uid, now
from test_series_character_state import fb2


class ListenerStressPersistenceTest(unittest.TestCase):
    def test_applied_queue_exposes_actual_published_generation_and_affected_sources(self):
        from rimsky.listener_stress_retake import _queue_status
        old={'id':'old','created':'2026-10-23','updated':'2026-10-23','status':'held',
            'report':{'id':'note','source_id':'p','segment_id':'s','text_offset':0,'generation_id':'origin'},'error':'old failure'}
        applied={**old,'id':'new','created':'2026-10-24','updated':'2026-10-24','status':'applied','error':'',
            'published_generation_id':'delivered','affected_segment_ids':['s2'],'affected_source_ids':['p']}
        status=_queue_status([old,applied],[])
        self.assertEqual('applied',status['status'])
        self.assertEqual('delivered',status['published_generation_id'])
        self.assertEqual(['s2'],status['affected_segment_ids'])
        self.assertEqual(['p'],status['affected_source_ids'])
        self.assertEqual(['origin'],status['origin_generation_ids'])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'library',resources=ROOT/'resources')
        self.book=self.store.import_data(fb2('Synthetic IV',4,'Она встретила Маришку. Потом гуляла с Маришкой.'),'test.fb2')
        self.generation=self.store.create_generation(self.book['id'],{})
        self.store.execute('UPDATE generations SET cast=? WHERE id=?',
            (json.dumps([{'id':'marishka','name':'Маришка'}]),self.generation['id']))
        self.store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=10 WHERE generation_id=?",('a'*64,self.generation['id']))

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def note(self,word='Маришкой',intended='Мари́шкой'):
        generation=self.store.generation(self.generation['id'])
        segment=next(s for s in generation['segments'] if word in s['text'])
        return self.store.report_stress(generation['id'],segment['id'],segment['source_id'],word,
            segment['start']+segment['text'].index(word),intended_stress=intended,audio_hash=segment['audio_hash'])

    def legacy_current(self, config):
        """Seed an actual pre-migration history row; new renders reuse one ID."""
        old=self.store.rows('SELECT * FROM generations WHERE id=?',(self.generation['id'],))[0]
        identifier=uid()
        row={**old,'id':identifier,'parent_id':old['id'],'created':now(),'updated':now(),
             'status':'paused','analysis':'{}','config':json.dumps({**json.loads(old['config']),**config})}
        with self.store.lock,self.store.transaction():
            self.store.db.execute(f'INSERT INTO generations({",".join(row)}) VALUES({",".join("?"for _ in row)})',tuple(row.values()))
            for segment in self.store.rows('SELECT * FROM segments WHERE generation_id=?',(old['id'],)):
                part={**segment,'generation_id':identifier,'status':'pending','audio_hash':None,'duration':0,'cues':'[]'}
                self.store.db.execute(f'INSERT INTO segments({",".join(part)}) VALUES({",".join("?"for _ in part)})',tuple(part.values()))
            self.store.set_current_generation(self.book['id'],identifier)
        return self.store.generation(identifier)

    def test_direct_store_report_promotes_family_and_fresh_generation_consumes_it(self):
        report=self.note()
        self.assertEqual('updated',report['character_state']['status'])
        new=self.store.create_generation(self.book['id'],{})
        frozen=snapshot(self.store,self.store.book(self.book['id']))
        self.assertEqual('Мари́шка',frozen['names']['маришка'])
        self.assertEqual([],frozen['occurrences'])
        native,evidence=apply('Ма́ришка увидела Мари́шку и Ма́ришкой.',frozen)
        self.assertEqual('Мари́шка увидела Мари́шку и Мари́шкой.',native)
        self.assertEqual(2,len(evidence['edits']))
        self.assertEqual(new['id'],report['generation_id'])
        self.assertEqual(1,len(self.store.stress_reports(self.book['id'])))

    def test_later_family_correction_wins_older_occurrence_across_all_inflections(self):
        self.note(intended='Ма́ришкой')
        self.note()
        frozen=snapshot(self.store,self.store.book(self.book['id']))
        self.assertEqual('Мари́шкой Мари́шку Мари́шке.',apply('Маришкой Маришку Маришке.',frozen)[0])
        self.assertEqual([],frozen['occurrences'])
        self.assertEqual(2,len(self.store.stress_reports(self.book['id'])))

    def test_context_note_only_targets_exact_source_occurrence(self):
        source='Он видел замок, потом замок.'
        offset=source.rindex('замок')
        frozen={'policy':POLICY,'names':{},'occurrences':[{'source_id':'p','passage_sha256':digest(source),
            'text_offset':offset,'selected_text':'замок','intended_stress':'замо́к','report_id':'r'}]}
        native,evidence=apply('Он видел за́мок, потом за́мок.',frozen,source_id='p',source_text=source)
        self.assertEqual('Он видел за́мок, потом замо́к.',native)
        self.assertEqual(1,len(evidence['edits']))
        self.assertEqual(source,apply(source,frozen,source_id='other',source_text=source)[0])
        changed=source+'!'
        self.assertEqual(changed,apply(changed,frozen,source_id='p',source_text=changed)[0])

    def test_context_note_maps_after_number_expansion_without_retargeting(self):
        source='В 2 часа он видел замок.'
        frozen={'policy':POLICY,'names':{},'occurrences':[{'source_id':'p','passage_sha256':digest(source),
            'text_offset':source.index('замок'),'selected_text':'замок','intended_stress':'замо́к','report_id':'r'}]}
        self.assertEqual('В два часа он видел замо́к.',apply('В два часа он видел за́мок.',frozen,source_id='p',source_text=source)[0])

    def test_runtime_invalidation_targets_current_generation_not_old_history(self):
        report=self.note()
        old=self.store.generation(self.generation['id'])
        current=self.legacy_current({})
        self.store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=10 WHERE generation_id=?",('b'*64,current['id']))
        self.store.execute("UPDATE generations SET status='ready',analysis=? WHERE id=?",(json.dumps({'complete':True}),current['id']))
        jobs=object.__new__(Jobs)
        jobs.store=self.store;jobs.lock=threading.RLock();jobs.active=None;jobs.listener_queue=[];jobs.closed=False
        def start(gid):jobs.active=gid;return self.store.generation(gid)
        with patch.object(jobs,'start',side_effect=start):
            receipt=jobs.listener_stress(report)
        self.assertEqual('running',receipt['status'])
        self.assertEqual(old['segments'],self.store.generation(old['id'])['segments'])
        updated=self.store.generation(current['id'])
        affected=[s for s in updated['segments'] if 'Маришк' in s['text']]
        self.assertTrue(all(s['status']=='pending' and s['audio_hash'] is None for s in affected))
        self.assertEqual(POLICY,updated['analysis']['pronunciation']['listener_stress']['policy'])

    def test_final_family_override_survives_conflicting_automatic_marks(self):
        from rimsky.spoken_pronunciation import synthesis_text
        spec={'policy':'auto-pronunciation-v1','names':{},'listener_stress':{'policy':POLICY,'names':{'маришка':'Мари́шка'},'occurrences':[]}}
        with patch('rimsky.spoken_pronunciation._automatic',return_value='Ма́ришкой.'):
            self.assertEqual('Мари́шкой.',synthesis_text('Маришкой.',spec)[0])

    def test_shared_profile_applies_to_independent_library_and_keeps_raw_history(self):
        self.note()
        with tempfile.TemporaryDirectory() as target:
            other=Store(Path(target)/'library',resources=ROOT/'resources',correction_profile=self.store.correction_profile)
            try:
                book=other.import_data(fb2('Synthetic IV',4,'Она встретила Маришку. Потом гуляла с Маришкой.'),'other.fb2')
                frozen=snapshot(other,book)
                self.assertEqual('Мари́шкой.',apply('Ма́ришкой.',frozen)[0])
                self.assertEqual([],other.stress_reports(book['id']))
            finally:other.close()

    def test_noncast_proper_name_seen_only_in_oblique_form_uses_lemma(self):
        self.store.execute("UPDATE generations SET cast='[]' WHERE id=?",(self.generation['id'],))
        report=self.note()
        self.assertEqual('updated',report['character_state']['status'])
        frozen=snapshot(self.store,self.book)
        self.assertEqual('Мари́шка',frozen['names']['маришка'])
        self.assertEqual('Мари́шка Мари́шкой Мари́шке.',apply('Маришка Ма́ришкой Маришке.',frozen)[0])

    def test_changed_snapshot_rejected_and_unaffected_unit_evidence_stays_stable(self):
        self.note()
        frozen=snapshot(self.store,self.book)
        clean=apply('Здесь другое предложение.',frozen)[1]['sha256']
        newer={**frozen,'names':{**frozen['names'],'гундахар':'Гунда́хар'}}
        with self.assertRaisesRegex(ValueError,'checksum'):
            apply('Маришкой.',newer)
        newer['sha256']=digest({k:v for k,v in newer.items() if k!='sha256'})
        self.assertEqual(clean,apply('Здесь другое предложение.',newer)[1]['sha256'])

    def test_sentence_initial_common_homograph_stays_source_bound(self):
        from rimsky.listener_stress import _proper_family
        report={'selected_text':'Стоит','intended_stress':'Стои́т','text_offset':0,'passage_text':'Стоит человек.'}
        self.assertIsNone(_proper_family(self.book,report))

    def test_ambiguous_or_non_noun_morphology_never_promotes_family(self):
        from rimsky.listener_stress import _proper_family
        from types import SimpleNamespace
        report={'selected_text':'Хрбром','intended_stress':'Хрбро́м','text_offset':3,'passage_text':'С Хрбром.'}
        tag=SimpleNamespace(grammemes=set(),POS='UNKN')
        morph=SimpleNamespace(parse=lambda word:[SimpleNamespace(normal_form='хрбр',tag=tag)],word_is_known=lambda word:False)
        with patch('rimsky.proper_noun_stress._morph',return_value=morph):
            self.assertIsNone(_proper_family(self.book,report))

    def test_backfill_reconstructs_each_historical_report_own_stress(self):
        from rimsky.listener_stress import notes
        first=self.note(intended='Ма́ришкой')
        second=self.note()
        self.store.correction_profile=Path(self.temp.name)/'fresh-profile.sqlite3'
        snapshot(self.store,self.book)
        rows={row['report']['id']:row for row in notes(self.store,self.book)}
        self.assertEqual('Ма́ришка',rows[first['id']]['family']['маришка'])
        self.assertEqual('Мари́шка',rows[second['id']]['family']['маришка'])

    def test_pending_resolution_upgrades_without_changing_original_note(self):
        import sqlite3
        from contextlib import closing
        self.store.execute("UPDATE generations SET cast='[]' WHERE id=?",(self.generation['id'],))
        with patch('rimsky.listener_stress._proper_family',return_value=None):
            original=self.note()
        frozen=snapshot(self.store,self.book)
        self.assertEqual('Мари́шка',frozen['names']['маришка'])
        with closing(sqlite3.connect(self.store.correction_profile)) as db:
            self.assertEqual(2,db.execute('SELECT count(*) FROM correction_resolutions WHERE report_id=?',(original['id'],)).fetchone()[0])
            raw=json.loads(db.execute('SELECT raw FROM corrections WHERE report_id=?',(original['id'],)).fetchone()[0])
        self.assertEqual('Мари́шкой',raw['intended_stress'])
        self.assertEqual(1,len(self.store.stress_reports(self.book['id'])))

    def test_nonstructured_feedback_is_shared_and_visible_as_pending(self):
        from rimsky.listener_stress import notes
        segment=next(s for s in self.store.generation(self.generation['id'])['segments'] if 'Маришкой' in s['text'])
        report=self.store.report_stress(self.generation['id'],segment['id'],segment['source_id'],'Маришкой',
            segment['start']+segment['text'].index('Маришкой'),kind='pronunciation',comment='Please check this name.')
        frozen=snapshot(self.store,self.book)
        self.assertEqual('pending',next(row['status'] for row in frozen['note_statuses'] if row['report_id']==report['id']))
        self.assertEqual('Please check this name.',notes(self.store,self.book)[0]['report']['comment'])

    def test_stale_tab_report_queues_current_external_generation(self):
        from rimsky.listener_stress_retake import enqueue_listener_stress_retake
        report=self.note()
        current=self.legacy_current({'engine':'higgs_audio_tts_q8_external_preview'})
        folder=self.store.root.parent/'fresh-external'
        folder.mkdir()
        (folder/'manifest.json').write_text('{}')
        (folder/'progress.json').write_text(json.dumps({'generation':folder.name}))
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?',
            (json.dumps({'external_preview':{'generation':folder.name}}),current['id']))
        self.store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=10 WHERE generation_id=?",('b'*64,current['id']))
        with patch('rimsky.listener_stress_retake._start_worker') as launch:
            receipt=enqueue_listener_stress_retake(self.store,report)
        self.assertGreater(receipt['targets'],0)
        items=[json.loads(path.read_text('utf8')) for path in (folder/'listener-stress-queue').glob('*.json')]
        self.assertTrue(all(item['generation_id']==current['id'] for item in items))
        self.assertTrue(all(item['report']['id']==report['id'] for item in items))
        self.assertEqual(current['id'],launch.call_args.args[2])

    def test_unpublished_current_external_target_keeps_durable_refresh_hold(self):
        from rimsky.listener_stress_retake import enqueue_listener_stress_retake,report_queue_status
        report=self.note()
        current=self.legacy_current({'engine':'higgs_audio_tts_q8_external_preview'})
        folder=self.store.root.parent/'pending-external';folder.mkdir()
        (folder/'manifest.json').write_text('{}')
        (folder/'progress.json').write_text(json.dumps({'generation':folder.name}))
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?',
            (json.dumps({'external_preview':{'generation':folder.name}}),current['id']))
        with patch('rimsky.listener_stress_retake._start_worker') as launch:
            receipt=enqueue_listener_stress_retake(self.store,report)
        self.assertEqual('held',receipt['status'])
        self.assertEqual('preparation_refresh_required',receipt['kind'])
        self.assertIn('pending preparation',receipt['error'])
        launch.assert_not_called()
        item=json.loads(next((folder/'listener-stress-queue').glob('*.json')).read_text('utf8'))
        self.assertEqual(report['generation_id'],item['report']['reported_generation_id'])
        self.assertEqual(current['id'],item['generation_id'])
        self.assertTrue(item['pending_segment_ids'])
        self.assertEqual('held',report_queue_status(self.store,report)['status'])

    def test_stale_saved_position_does_not_create_second_legacy_render(self):
        from rimsky.listener_stress_retake import enqueue_listener_stress_retake, ENGINE
        from process_listener_stress_queue import current_book_debug_base
        report=self.note()
        old=self.generation['id']
        current=self.legacy_current({'engine':ENGINE})
        folders={}
        for gid,name in ((old,'legacy-heard'),(current['id'],'current-debug')):
            folder=self.store.root.parent/name;folder.mkdir();folders[gid]=folder
            (folder/'manifest.json').write_text('{}')
            (folder/'progress.json').write_text(json.dumps({'generation':name,'status':'complete'}))
            self.store.execute('UPDATE generations SET config=?,analysis=? WHERE id=?',
                (json.dumps({'engine':ENGINE}),json.dumps({'external_preview':{'generation':name}}),gid))
        (folders[current['id']]/'ready.json').write_text(json.dumps({'plans':[{}],'native_manifest':{'model':'pinned'}}))
        self.store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=10 WHERE generation_id=?",('b'*64,current['id']))
        self.store.execute('INSERT OR REPLACE INTO positions VALUES(?,?)',(self.book['id'],json.dumps({'generation_id':old})))
        with patch('rimsky.listener_stress_retake._start_worker') as launch:
            receipt=enqueue_listener_stress_retake(self.store,report)
        self.assertEqual(1,receipt['generations'])
        self.assertTrue(all(call.args[2]==current['id'] for call in launch.call_args_list))
        self.assertFalse((folders[old]/'listener-stress-queue').exists())
        items=[json.loads(p.read_text('utf8')) for p in (folders[current['id']]/'listener-stress-queue').glob('*.json')]
        self.assertTrue(items)
        self.assertTrue(all(item['origin_report']['audio_hash']==report['audio_hash'] for item in items))
        self.assertTrue(all(item['report']['audio_hash']=='b'*64 for item in items))
        self.assertEqual((folders[current['id']],current['id']),current_book_debug_base(folders[old],self.store.root,old))

    def test_legacy_dispatch_waits_for_full_book_owner(self):
        from process_listener_stress_queue import process
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'old-note.json'
            item={'id':'note','status':'queued','report':{'book_id':self.book['id']}}
            path.write_text(json.dumps(item))
            def delivered(_seconds):path.write_text(json.dumps({**item,'status':'applied'}))
            with patch('process_listener_stress_queue._locked',return_value=None), \
                 patch('process_listener_stress_queue.time.sleep',side_effect=delivered), \
                 patch('process_listener_stress_queue.current_book_debug_base',return_value=(root,'old')), \
                 patch('process_listener_stress_queue._process_locked') as dispatch:
                process(root,root/'library','old',path)
            dispatch.assert_not_called()

    def test_legacy_dispatch_retargets_after_lock_and_preserves_prior_barrier(self):
        import contextlib
        from process_listener_stress_queue import process
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';new=root/'new';old.mkdir();new.mkdir()
            path=old/'note.json';barrier={'reason':'historic release'}
            path.write_text(json.dumps({'status':'queued','report':{'book_id':self.book['id']},'development_dispatch_barrier':barrier}))
            events=[]
            @contextlib.contextmanager
            def lock(*_):events.append('lock');yield True
            with patch('process_listener_stress_queue.wait_for_development_release',side_effect=lambda *_:events.append('barrier')), \
                 patch('process_listener_stress_queue.current_book_debug_base',side_effect=[(old,'old'),(new,'new')]), \
                 patch('process_listener_stress_queue.book_correction_lock',side_effect=lock), \
                 patch('process_listener_stress_queue.rebind_deferred_report') as rebind, \
                 patch('process_listener_stress_queue._process_locked') as dispatch:
                process(old,root/'library','old',path)
            self.assertEqual(['barrier','lock'],events)
            rebind.assert_called_once_with(new,root/'library','new',path)
            dispatch.assert_called_once_with(new,root/'library','new',path)
            result=json.loads(path.read_text())
            self.assertNotIn('development_dispatch_barrier',result)
            self.assertEqual(barrier,result['prior_dispatch_barriers'][0]['barrier'])

    def test_technical_recovery_topology_routes_to_one_shot_worker(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from process_listener_stress_queue import process
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);(folder/'units').mkdir()
            (folder/'ready.json').write_text(json.dumps({'plans':[{}],'native_manifest':{'model':'pinned'},'unit_count':1}))
            (folder/'units/s.json').write_text(json.dumps({'unit':'s','status':'rendered'}))
            (folder/'progress.json').write_text(json.dumps({'status':'complete'}))
            (folder/'manifest.json').write_text(json.dumps({'render_policy':'development-technical-missing-output-seed-recovery-v1'}))
            item=folder/'item.json';item.write_text(json.dumps({'id':'r','report':{'segment_id':'s','book_id':self.book['id']}}))
            def worker(command,**kwargs):
                result=Path(command[command.index('--result')+1]);result.write_text(json.dumps({'status':'applied','post_processing':'off'}))
            with patch('process_listener_stress_queue.subprocess.run',side_effect=worker) as run, \
                 patch('process_listener_stress_queue.latest_delivered_debug_base',return_value=(folder,'generation')):
                process(folder,folder/'library','generation',item)
            self.assertEqual(1,run.call_count)
            self.assertIn('repair_debug_listener_stress.py',run.call_args.args[0][4])
            self.assertEqual('applied',json.loads(item.read_text())['status'])

    def test_unpublished_current_external_target_defers_automatically_when_frozen(self):
        from rimsky.listener_stress_retake import enqueue_listener_stress_retake
        report=self.note()
        current=self.legacy_current({'engine':'higgs_audio_tts_q8_external_preview'})
        folder=self.store.root.parent/'rendering-external';folder.mkdir();(folder/'units').mkdir()
        (folder/'manifest.json').write_text('{}')
        (folder/'ready.json').write_text(json.dumps({'unit_count':2,'plans':[{}],'native_manifest':{'model':'pinned'}}))
        (folder/'progress.json').write_text(json.dumps({'generation':folder.name,'status':'rendering'}))
        (folder/'listener-stress-queue').mkdir()
        (folder/'listener-stress-queue/development-dispatch-barrier.json').write_text(json.dumps({'state':'awaiting_reviewed_release','reason':'reviewed development release'}))
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?',
            (json.dumps({'external_preview':{'generation':folder.name}}),current['id']))
        with patch('rimsky.listener_stress_retake._start_worker') as launch:
            receipt=enqueue_listener_stress_retake(self.store,report)
        self.assertEqual('queued',receipt['status'])
        self.assertEqual('awaiting_render_completion',receipt['phase'])
        launch.assert_called_once()
        item=json.loads((folder/'listener-stress-queue'/(report['id']+'-preparation-refresh.json')).read_text('utf8'))
        self.assertEqual(report['generation_id'],item['origin_report']['generation_id'])
        self.assertEqual(digest(item['origin_report']),item['origin_report_sha256'])
        self.assertTrue(item['development_dispatch_barrier']['required_coalescing'])

    def test_deferred_waiter_resumes_once_render_completes_and_bounds_missing_owner(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        from process_listener_stress_queue import wait_for_debug_source
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);item=folder/'item.json';item.write_text(json.dumps({'id':'report'}))
            active={'complete':False,'expected':2,'rendered':1,'technical_failures':[],
                'publication_status':'rendering','terminal_status':None,'owners':[{'pid':123,'create_time':456}],
                'activity_unix':100}
            with patch('process_listener_stress_queue.debug_source_state',side_effect=[active,{**active,'complete':True}]), \
                 patch('process_listener_stress_queue.time.sleep'):
                wait_for_debug_source(folder,{'unit_count':2},item)
            saved=json.loads(item.read_text())
            self.assertEqual('queued',saved['status'])
            self.assertEqual('awaiting_render_completion',saved['phase'])
            with patch('process_listener_stress_queue.debug_source_state',return_value={**active,'owners':[]}):
                with self.assertRaisesRegex(Exception,'no live verified owner'):
                    wait_for_debug_source(folder,{'unit_count':2},item,owner_grace_seconds=0)

    def test_deferred_rebinding_keeps_original_heard_evidence(self):
        from rimsky.listener_stress_retake import _origin
        from process_listener_stress_queue import rebind_deferred_report
        report=self.note();origin=_origin(report)
        current=self.legacy_current({})
        self.store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=10 WHERE generation_id=?",('f'*64,current['id']))
        target=next(s for s in self.store.generation(current['id'])['segments'] if s['source_id']==report['source_id'])
        path=Path(self.temp.name)/'deferred-item.json'
        path.write_text(json.dumps({'origin_report':origin,'origin_report_sha256':digest(origin),
            'report':{**report,'generation_id':current['id'],'segment_id':target['id'],'audio_hash':None}}))
        with patch('rimsky.listener_stress.shared_profile',return_value=self.store.correction_profile):
            rebind_deferred_report(Path(self.temp.name),self.store.root,current['id'],path)
            saved=json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual('ready_for_correction',saved['phase'])
        self.assertEqual('f'*64,saved['report']['audio_hash'])
        self.assertEqual('a'*64,saved['origin_report']['audio_hash'])
        self.assertEqual(report['generation_id'],saved['origin_report']['generation_id'])

    def test_deferred_queue_process_automatically_waits_rebinds_then_repairs(self):
        from process_listener_stress_queue import process
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);(folder/'units').mkdir()
            (folder/'ready.json').write_text(json.dumps({'unit_count':1,'plans':[{}],'native_manifest':{'model':'pinned'}}))
            path=folder/'item.json';path.write_text(json.dumps({'id':'saved','report':{'id':'raw-report','book_id':self.book['id']},'phase':'awaiting_render_completion'}))
            order=[]
            def wait(*args):order.append('wait')
            def rebind(*args):order.append('rebind')
            def worker(command,**kwargs):
                order.append('repair')
                Path(command[command.index('--result')+1]).write_text(json.dumps({'status':'applied'}))
            with patch('process_listener_stress_queue.debug_source_state',return_value={'complete':False}), \
                 patch('process_listener_stress_queue.wait_for_debug_source',side_effect=wait), \
                 patch('process_listener_stress_queue.rebind_deferred_report',side_effect=rebind), \
                 patch('process_listener_stress_queue.latest_delivered_debug_base',return_value=(folder,'current-generation')), \
                 patch('process_listener_stress_queue.subprocess.run',side_effect=worker):
                process(folder,folder/'library','current-generation',path)
            self.assertEqual(['wait','rebind','repair'],order)
            self.assertEqual('applied',json.loads(path.read_text())['status'])

    def test_deferred_owner_check_accepts_only_exact_known_renderer_command(self):
        from process_listener_stress_queue import debug_source_state
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);(folder/'units').mkdir()
            (folder/'progress.json').write_text(json.dumps({'status':'rendering'}))
            def process(pid,command):
                return SimpleNamespace(pid=pid,info={'cmdline':command},create_time=lambda:123.0)
            unexpected=process(101,['python',str(folder/'arbitrary.py'),'--output',str(folder)])
            allowed=process(202,['python',str(ROOT/'scripts/debug_higgs_one_shot.py'),'--output',str(folder)])
            with patch('psutil.process_iter',return_value=[unexpected,allowed]):
                state=debug_source_state(folder,{'unit_count':1})
            self.assertEqual([202],[row['pid'] for row in state['owners']])

    def test_book_correction_lock_skips_request_already_delivered_by_other_owner(self):
        from process_listener_stress_queue import book_correction_lock
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'request.json'
            item={'id':'queued','status':'queued','report':{'book_id':self.book['id']}}
            path.write_text(json.dumps(item))
            def other_owner_delivers(_seconds):
                path.write_text(json.dumps({**item,'status':'applied'}))
            with patch('process_listener_stress_queue._locked',return_value=None), \
                 patch('process_listener_stress_queue.time.sleep',side_effect=other_owner_delivers):
                with book_correction_lock(root,path) as acquired:self.assertFalse(acquired)

    def test_dispatch_rebinds_old_queue_item_to_latest_base_after_book_lock(self):
        from process_listener_stress_queue import process
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);folder=root/'old';folder.mkdir();(folder/'units').mkdir()
            latest=root/'delivered';latest.mkdir()
            (folder/'ready.json').write_text(json.dumps({'unit_count':1,'plans':[{}],'native_manifest':{}}))
            # A nonempty native manifest identifies the frozen debug topology.
            (folder/'ready.json').write_text(json.dumps({'unit_count':1,'plans':[{}],'native_manifest':{'model':'pinned'}}))
            path=folder/'request.json';path.write_text(json.dumps({'id':'pending','status':'queued','report':{'book_id':self.book['id']}}))
            rebound=[]
            def worker(command,**_kwargs):
                self.assertEqual(str(latest),command[command.index('--generation')+1])
                self.assertEqual('latest-id',command[command.index('--generation-id')+1])
                Path(command[command.index('--result')+1]).write_text(json.dumps({'status':'applied'}))
            with patch('process_listener_stress_queue.debug_source_state',return_value={'complete':True}), \
                 patch('process_listener_stress_queue.latest_delivered_debug_base',return_value=(latest,'latest-id')), \
                 patch('process_listener_stress_queue.rebind_deferred_report',side_effect=lambda *args:rebound.append(args)), \
                 patch('process_listener_stress_queue.subprocess.run',side_effect=worker):
                process(folder,root/'library','old-id',path)
            self.assertEqual((latest,root/'library','latest-id',path),rebound[0])
            self.assertEqual('latest-id',json.loads(path.read_text())['dispatch_source_generation_id'])

    def test_late_old_queue_uses_latest_verified_delivered_base(self):
        from process_listener_stress_queue import latest_delivered_debug_base
        from rimsky.listener_stress_retake import ENGINE
        root=self.store.root.parent;old=root/'old';latest=root/'latest'
        old.mkdir();latest.mkdir();(old/'listener-stress-queue').mkdir()
        current=self.legacy_current({})
        self.store.execute('UPDATE generations SET config=?,analysis=? WHERE id=?',
            (json.dumps({'engine':ENGINE}),json.dumps({'external_preview':{'generation':'latest'}}),current['id']))
        (latest/'manifest.json').write_text('{}');(latest/'ready.json').write_text('{}')
        (latest/'progress.json').write_text(json.dumps({'generation':'latest','status':'complete'}))
        (latest/'listener-repair-terminal.json').write_text(json.dumps({'status':'applied'}))
        (old/'listener-stress-queue/applied.json').write_text(json.dumps({'status':'applied',
            'published_generation_id':current['id'],'completed':'2026-10-24'}))
        self.assertEqual((latest,current['id']),latest_delivered_debug_base(old,self.store.root,self.generation['id']))

    def test_reviewed_release_permits_future_verified_notes_without_manual_allowlist(self):
        from process_listener_stress_queue import wait_for_development_release
        from rimsky.util import file_digest
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);queue=folder/'listener-stress-queue';queue.mkdir()
            (queue/'development-dispatch-barrier.json').write_text(json.dumps({'state':'awaiting_reviewed_release'}))
            origin={'id':'future-canonical-note'};sha=digest(origin)
            item=queue/'future.json';item.write_text(json.dumps({'id':'future','origin_report':origin,
                'origin_report_sha256':sha,'origin_verified_sha256':sha,'status':'queued'}))
            (queue/'dispatch-release.json').write_text(json.dumps({'generation':folder.name,'report_ids':[],
                'allow_future_canonical_reports':True,'helper_sha256':file_digest(ROOT/'scripts/repair_debug_listener_stress.py'),
                'listener_module_sha256':file_digest(ROOT/'runtime/rimsky/listener_stress.py')}))
            wait_for_development_release(folder,item)
            self.assertEqual('ready_for_correction',json.loads(item.read_text())['phase'])
            malformed=json.loads(item.read_text());malformed.pop('origin_verified_sha256');item.write_text(json.dumps(malformed))
            with self.assertRaisesRegex(Exception,'does not bind'):
                wait_for_development_release(folder,item)
            malformed.pop('origin_report_sha256');malformed.pop('origin_report');item.write_text(json.dumps(malformed))
            with self.assertRaisesRegex(Exception,'does not bind'):
                wait_for_development_release(folder,item)

    def test_coalesced_already_applied_queue_item_never_dispatches_twice(self):
        from process_listener_stress_queue import process
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);item=folder/'done.json';item.write_text(json.dumps({'status':'applied'}))
            with patch('process_listener_stress_queue.subprocess.run') as run:
                process(folder,folder/'library','generation',item)
            run.assert_not_called()

    def test_final_occurrence_override_wins_conflicting_automatic_stress(self):
        from rimsky.spoken_pronunciation import synthesis_text
        source='Он видел замок.'
        frozen={'policy':POLICY,'names':{},'occurrences':[{'source_id':'p','passage_sha256':digest(source),
            'text_offset':source.index('замок'),'selected_text':'замок','intended_stress':'замо́к','report_id':'r'}]}
        spec={'policy':'auto-pronunciation-v1','names':{},'listener_stress':frozen,
              'listener_source':{'source_id':'p','source_text':source,'source_start':0,'source_end':len(source)}}
        with patch('rimsky.spoken_pronunciation._automatic',return_value='Он видел за́мок.'):
            self.assertEqual('Он видел замо́к.',synthesis_text(source,spec)[0])

    def test_missing_morphology_cannot_invent_a_name_family(self):
        from rimsky.listener_stress import _proper_family
        with patch('rimsky.proper_noun_stress._morph',side_effect=ImportError('missing dictionary')):
            self.assertIsNone(_proper_family(self.book,{'selected_text':'Маришкой','intended_stress':'Мари́шкой',
                'text_offset':4,'passage_text':'И с Маришкой.'}))

    def test_correct_native_mark_report_still_rerenders_and_job_survives_restart(self):
        self.store.execute("UPDATE segments SET spoken=replace(spoken,'Маришкой','Мари́шкой') WHERE generation_id=?",(self.generation['id'],))
        report=self.note()
        self.store.execute("UPDATE generations SET status='ready',analysis=? WHERE id=?",(json.dumps({'complete':True}),self.generation['id']))
        def jobs_instance():
            jobs=object.__new__(Jobs);jobs.store=self.store;jobs.lock=threading.RLock();jobs.active=None
            jobs.listener_queue=[];jobs.closed=False
            return jobs
        first=jobs_instance()
        def start(gid):first.active=gid;return self.store.generation(gid)
        with patch.object(first,'start',side_effect=start):
            first.listener_stress(report)
        state=self.store.generation(self.generation['id'])
        selected=next(s for s in state['segments'] if 'Маришкой' in s['text'])
        self.assertEqual(1,state['analysis']['takes'][selected['id']])
        self.assertEqual('running',self.store.rows('SELECT status FROM listener_correction_jobs')[0]['status'])
        restarted=jobs_instance()
        def resume(gid):restarted.active=gid;return self.store.generation(gid)
        with patch.object(restarted,'start',side_effect=resume):
            self.assertEqual(state['id'],restarted.resume_listener_corrections())
        self.assertEqual(1,self.store.generation(state['id'])['analysis']['takes'][selected['id']])

    def test_family_receipt_includes_authoritative_report_even_when_no_edit_needed(self):
        report=self.note()
        frozen=snapshot(self.store,self.book)
        native,evidence=apply('Мари́шкой.',frozen)
        self.assertEqual([],evidence['edits'])
        self.assertEqual(report['id'],evidence['applications'][0]['family_evidence'][0]['report_id'])

    def test_render_key_binds_only_actual_unit_correction_evidence(self):
        from rimsky.jobs import render_key
        plan={'policy':POLICY,'applications':[{'report_id':'first','intended_stress':'Мари́шкой'}]}
        with patch('rimsky.speech_backend.is_higgs',return_value=True), \
             patch('rimsky.higgs_backend.synthesis_plan',return_value={'native':'Мари́шкой'}), \
             patch('rimsky.spoken_pronunciation.synthesis_text',return_value=('Мари́шкой',{'listener_stress':plan})):
            first=render_key({}, {}, 'Маришкой',{},'neutral','ru',0)
            plan['applications'][0]['report_id']='second'
            second=render_key({}, {}, 'Маришкой',{},'neutral','ru',0)
            self.assertNotEqual(first,second)
            plan['applications']=[]
            unaffected=render_key({}, {}, 'Маришкой',{},'neutral','ru',0)
            plan['unrelated_journal_revision']='another'
            self.assertEqual(unaffected,render_key({}, {}, 'Маришкой',{},'neutral','ru',0))

    def test_family_report_does_not_force_an_unrelated_already_correct_name(self):
        self.book=self.store.import_data(fb2('Other IV',4,'Она увидела Гундахара.</p><p>Потом гуляла с Маришкой.'),'other.fb2')
        self.generation=self.store.create_generation(self.book['id'],{})
        self.store.execute('UPDATE generations SET cast=? WHERE id=?',(json.dumps([
            {'id':'gundahar','name':'Гундахар'},{'id':'marishka','name':'Маришка'}]),self.generation['id']))
        self.store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=10 WHERE generation_id=?",('c'*64,self.generation['id']))
        self.note('Гундахара','Гунда́хара')
        self.store.execute("UPDATE segments SET spoken=replace(spoken,'Гундахара','Гунда́хара') WHERE generation_id=?",(self.generation['id'],))
        report=self.note()
        jobs=object.__new__(Jobs);jobs.store=self.store;jobs.lock=threading.RLock();jobs.active=None
        jobs.listener_queue=[];jobs.closed=False
        def start(gid):jobs.active=gid;return self.store.generation(gid)
        with patch.object(jobs,'start',side_effect=start):jobs.listener_stress(report)
        gundahar=next(s for s in self.store.generation(self.generation['id'])['segments'] if 'Гундахара' in s['text'])
        self.assertEqual('c'*64,gundahar['audio_hash'])

    def test_feedback_endpoint_shares_exact_book_history_across_applications(self):
        from server import Application
        from rimsky.spoken_pronunciation import set_resolver
        profile=Path(self.temp.name)/'application-shared-profile.sqlite3'
        first=Application(Path(self.temp.name)/'app-one',ROOT,auto_resume=False,correction_profile=profile)
        second=Application(Path(self.temp.name)/'app-two',ROOT,auto_resume=False,correction_profile=profile)
        try:
            payload=fb2('Shared IV',4,'Она гуляла с Маришкой.')
            book=first.store.import_data(payload,'source.fb2')
            generation=first.store.create_generation(book['id'],{})
            first.store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=10 WHERE generation_id=?",('d'*64,generation['id']))
            segment=next(s for s in first.store.generation(generation['id'])['segments'] if 'Маришкой' in s['text'])
            arguments=(generation['id'],segment['id'],segment['source_id'],'Маришкой',segment['start']+segment['text'].index('Маришкой'))
            old=first.store.report_stress(*arguments,intended_stress='Ма́ришкой',audio_hash='d'*64)
            latest=first.store.report_stress(*arguments,intended_stress='Мари́шкой',audio_hash='d'*64)
            # Same series, a different source book: its raw note must stay out.
            other=first.store.import_data(fb2('Shared V',5,'Она говорила с Маришкой.'),'next.fb2')
            other_generation=first.store.create_generation(other['id'],{})
            first.store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=10 WHERE generation_id=?",('e'*64,other_generation['id']))
            other_segment=next(s for s in first.store.generation(other_generation['id'])['segments'] if 'Маришкой' in s['text'])
            other_report=first.store.report_stress(other_generation['id'],other_segment['id'],other_segment['source_id'],'Маришкой',
                other_segment['start']+other_segment['text'].index('Маришкой'),intended_stress='Мари́шкой',audio_hash='e'*64)
            imported=second.store.import_data(payload,'another-filename.fb2')
            self.assertEqual([],second.store.stress_reports(imported['id']))
            visible=second.dispatch('stress_reports',{'book_id':imported['id']})
            by_id={row['id']:row for row in visible}
            self.assertEqual({old['id'],latest['id']},set(by_id))
            self.assertNotIn(other_report['id'],by_id)
            self.assertEqual('Ма́ришкой',by_id[old['id']]['intended_stress'])
            self.assertEqual('Мари́шкой',by_id[latest['id']]['intended_stress'])
            self.assertEqual(generation['id'],by_id[latest['id']]['generation_id'])
            self.assertEqual('d'*64,by_id[latest['id']]['audio_hash'])
            self.assertNotIn('retake_queue',by_id[latest['id']])
            # Local and shared copies deduplicate rather than double the list.
            self.assertEqual(2,len(first.dispatch('stress_reports',{'book_id':book['id']})))
        finally:
            first.close();second.close();set_resolver(None)


if __name__=='__main__':unittest.main()
