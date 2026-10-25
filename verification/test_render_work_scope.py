"""Exercise real Store/new-request/render transitions with synthetic native PCM."""
import copy
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'verification')]
from test_production_one_shot import ProductionOneShotTests
from rimsky import russian_preparation as prep
from rimsky.store import Store
from rimsky.util import digest
from rimsky.speech_quality import transcript_check
from rimsky.util import file_digest


class RenderWorkScopeTests(unittest.TestCase):
    setUp=ProductionOneShotTests.setUp
    guarded=ProductionOneShotTests.guarded

    def book(self,extra=False,third=False,first_only=False):
        raw=('''<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info>
        <author><nickname>Fixture</nickname></author><book-title>Fixture</book-title><lang>ru</lang>
        </title-info></description><body><section><p>Первый текст.</p>'''+('<p>Еще текст.</p>'if extra else'')+'''</section><section><p>Второй текст.</p></section></body></FictionBook>''').encode()
        if third:raw=raw.replace(b'</body>',b'<section><p>Third pending text.</p></section></body>')
        store=Store(self.root/'library');self.addCleanup(store.close);book=store.import_data(raw,'fixture.fb2')
        config={'device':'cpu','resolved_speech':self.resolved,'pack_fingerprint':self.pack['_fingerprint']}
        generation=store.create_generation(book['id'],config,chapters=[book['chapters'][0]['id']]if first_only else None)
        analysis={'complete':True,'approved':True,'narrator_voices':{'':'n'}}
        store.execute('UPDATE generations SET analysis=?,cast=? WHERE id=?',
            (json.dumps(analysis),json.dumps([{'id':'narrator','voice_id':'n'}]),generation['id']))
        store.execute("UPDATE segments SET speaker='narrator',delivery='neutral' WHERE generation_id=?",(generation['id'],))
        self.jobs.store=store;self.jobs._holds=lambda *a:set()
        self.jobs._chapter_acceptance=Mock()
        self.jobs._render(generation['id'],self.pack,config|{'chapters':generation['config']['chapters']},book,'Russian')
        self.calls.clear()
        self.prepared=[]
        def prepare(gid,book,chapter,recipe,**kwargs):
            self.prepared.append(chapter);entries={}
            for segment in store.generation(gid)['segments']:
                if segment['chapter_id']!=chapter:continue
                source={'source_id':segment['source_id'],'source_text':segment['text'],'source_start':0,'source_end':len(segment['text'])}
                entry={'policy':prep.POLICY,'book_sha256':prep.book_binding(book),'source':prep.source_binding(source),
                    'text_sha256':digest(segment['spoken'].encode()),'automatic_text':segment['spoken'],
                    'module_pins':{'fixture':'m'},'model_pins':{'fixture':'w'}}
                entry['sound_sha256']=digest(prep.sound_binding(entry));entry['sha256']=digest(entry)
                entries[prep.entry_key(segment['spoken'],source)]=entry
            latest=store.generation(gid)['analysis']
            previous=latest.get('pronunciation')or{}
            spec={**previous,'policy':prep.POLICY,'book_sha256':prep.book_binding(book),
                'prepared_units':{**previous.get('prepared_units',{}),**entries}}
            store.execute('UPDATE generations SET analysis=? WHERE id=?',(json.dumps({**latest,'pronunciation':spec}),gid))
            return spec
        self.jobs._prepare_russian_chapter=prepare
        return store,book,generation,config

    def test_selected_chapter_regeneration_keeps_other_legacy_output_exact(self):
        store,book,generation,config=self.book();gid=generation['id']
        chapters=generation['config']['chapters'];old=copy.deepcopy(store.generation(gid)['segments'][0])
        current=store.create_generation(book['id'],config|{'russian_preparation':prep.config('local')},chapters=[chapters[1]])
        self.jobs._render(gid,self.pack,current['config'],store.book(book['id']),'Russian')
        self.assertEqual(self.prepared,[chapters[1]])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(store.generation(gid)['segments'][0],old)
        self.assertTrue(all(s['status']=='ready'for s in store.generation(gid)['segments']))
        self.calls.clear();self.prepared.clear()
        before=copy.deepcopy(store.generation(gid)['segments'])
        self.jobs._render(gid,self.pack,current['config'],store.book(book['id']),'Russian')
        self.assertEqual(self.prepared,[]);self.assertEqual(self.calls,[])
        self.assertEqual(store.generation(gid)['segments'],before)

    def test_pending_repair_outside_last_requested_chapter_is_still_rendered(self):
        store,book,generation,config=self.book();gid=generation['id'];chapters=generation['config']['chapters']
        current=store.create_generation(book['id'],config|{'russian_preparation':prep.config('local')},chapters=[chapters[1]])
        self.jobs._render(gid,self.pack,current['config'],store.book(book['id']),'Russian')
        self.calls.clear();self.prepared.clear();segments=store.generation(gid)['segments']
        retained=copy.deepcopy(segments[1])
        # Listener corrections and saved-audio repair both mark explicit owners
        # pending, including owners outside the last chapter request.
        store.execute("UPDATE segments SET status='pending' WHERE generation_id=? AND id=?",(gid,segments[0]['id']))
        self.jobs._render(gid,self.pack,current['config'],store.book(book['id']),'Russian')
        self.assertEqual(self.prepared,[chapters[0]])
        self.assertEqual([p['text']for method,p in self.calls if method=='render'],[segments[0]['spoken']])
        self.assertEqual(store.generation(gid)['segments'][1],retained)

    def test_partial_request_keeps_previous_zero_audio_pending_chapters(self):
        store,book,generation,config=self.book(third=True,first_only=True);gid=generation['id']
        chapters=[c['id']for c in book['chapters']]
        unfinished=store.create_generation(book['id'],config,chapters=chapters[1:])
        self.assertTrue(all(not s['audio_hash']for s in unfinished['segments']if s['chapter_id']in chapters[1:]))
        current=store.create_generation(book['id'],config|{'russian_preparation':prep.config('local')},chapters=chapters[:1])
        self.assertEqual(current['config']['chapters'],chapters)
        from rimsky.jobs import render_work_ids
        work=render_work_ids(current)
        self.assertEqual({s['chapter_id']for s in current['segments']if s['id']in work},set(chapters))
        self.jobs._render(gid,self.pack,current['config'],store.book(book['id']),'Russian')
        self.assertEqual(self.prepared,chapters)
        self.assertEqual(len(self.calls),3)
        self.assertTrue(all(s['status']=='ready'for s in store.generation(gid)['segments']))

    def test_extra_pending_part_remains_owned_without_promoting_its_ready_neighbor(self):
        store,book,generation,config=self.book(extra=True);gid=generation['id'];chapters=generation['config']['chapters']
        current=store.create_generation(book['id'],config|{'russian_preparation':prep.config('local')},chapters=[chapters[1]])
        self.jobs._render(gid,self.pack,current['config'],store.book(book['id']),'Russian')
        target,neighbor=copy.deepcopy(store.generation(gid)['segments'][:2])
        self.calls.clear();self.prepared.clear()
        store.execute("UPDATE segments SET status='pending' WHERE generation_id=? AND id=?",(gid,target['id']))
        self.jobs._render(gid,self.pack,store.generation(gid)['config'],store.book(book['id']),'Russian')
        after=store.generation(gid)
        self.assertEqual(after['config']['render_owned_parts'],[target['id']])
        self.assertEqual(after['segments'][1],neighbor)
        self.assertEqual(self.prepared,[chapters[0]])
        # Invalid target provenance must still be revalidated on the next
        # resume; its ready legacy neighbor remains outside this job's scope.
        metadata=store.root/'audio'/f"{after['segments'][0]['audio_hash']}.json"
        receipt=json.loads(metadata.read_text('utf8'));receipt.pop('synthesis',None)
        metadata.write_text(json.dumps(receipt),encoding='utf8')
        self.calls.clear();self.prepared.clear()
        self.jobs._render(gid,self.pack,after['config'],store.book(book['id']),'Russian')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.prepared,[])
        self.assertEqual(store.generation(gid)['segments'][1],neighbor)

    def test_resume_aligns_regenerated_owned_ready_part_without_touching_retained_neighbor(self):
        store,book,generation,config=self.book(extra=True);gid=generation['id'];chapters=generation['config']['chapters']
        current=store.create_generation(book['id'],config,chapters=[chapters[1]])
        self.jobs._render(gid,self.pack,current['config'],store.book(book['id']),'Russian')
        current=store.generation(gid);target=current['segments'][-1]
        for segment in current['segments']:
            store.execute('UPDATE segments SET cues=? WHERE generation_id=? AND id=?',
                (json.dumps([{'start':0.,'end':.05,'text':segment['spoken']}]),gid,segment['id']))
        before=copy.deepcopy(store.generation(gid)['segments'])
        metadata=store.root/'audio'/f"{target['audio_hash']}.json"
        receipt=json.loads(metadata.read_text('utf8'));receipt.pop('synthesis',None)
        metadata.write_text(json.dumps(receipt),encoding='utf8')
        updated={**current['config'],'align_words':True}
        store.execute('UPDATE generations SET config=? WHERE id=?',(json.dumps(updated),gid))
        self.jobs.stop=threading.Event();self.jobs.binaries=ROOT;self.jobs.engine_factory=lambda _:Mock()
        self.jobs._pack=lambda *a,**kw:self.pack
        self.jobs._call=lambda method,payload:self.resolved if method=='probe'else self.fail(method)
        self.jobs._scene_direction=lambda *a:None
        self.jobs._speech_language=lambda *a:'Russian'
        aligned=[];aligner=Mock()
        def align(method,request,*a,**kw):
            self.assertEqual(method,'align');aligned.append(request['audio'])
            return [{'start':0.,'end':.1,'text':request['text']}]
        aligner.call.side_effect=align
        self.pack['aligner']={'path':'text.gguf'}
        self.calls.clear()
        with patch('rimsky.jobs.resolve_binary',return_value='fixture-align'),patch('rimsky.jobs.Engine',return_value=aligner):
            self.assertEqual(self.jobs._prepare(gid),'ready')
        after=store.generation(gid)['segments']
        self.assertEqual(len(self.calls),1)
        self.assertEqual(aligned,[str(store.root/'audio'/f"{after[-1]['audio_hash']}.wav")])
        self.assertEqual(after[-1]['cues'],[{'start':0.,'end':.1,'text':target['spoken']}])
        self.assertEqual(after[:-1],before[:-1])

    def test_scoped_hold_recheck_keeps_retained_parts_and_global_hold_evidence(self):
        from rimsky.jobs import Jobs
        store,book,generation,config=self.book(extra=True);gid=generation['id'];chapters=generation['config']['chapters']
        current=store.create_generation(book['id'],config,chapters=[chapters[1]])
        old,backend,target=current['segments'];old=copy.deepcopy(old)
        store.execute("UPDATE segments SET status='review',warning='Existing backend hold' WHERE generation_id=? AND id=?",(gid,backend['id']))
        backend=copy.deepcopy(store.generation(gid)['segments'][1])
        record={'target_id':old['id'],'source_id':old['source_id'],'start':old['start'],'end':old['end'],
            'speaker':old['speaker'],'primary_speaker':old['speaker'],'tier':'legacy','triggers':['unresolved_speaker'],'status':'review'}
        analysis={**current['analysis'],'speaker_resolution':[record], 'backend_holds':{backend['id']:'old-proof'}}
        store.execute('UPDATE generations SET analysis=? WHERE id=?',(json.dumps(analysis),gid))
        scoped={**store.generation(gid),'segments':[target]}
        Jobs._holds(self.jobs,gid,self.pack,current['config'],scoped,
            lambda *a:(self.pack['voices'][0],None,False,self.pack['voices'][0]))
        after=store.generation(gid)
        self.assertEqual(after['segments'][0],old)
        self.assertIn(record,after['analysis']['speaker_resolution'])
        self.assertIn(old['id'],after['analysis']['speaker_review_holds']['held_ids'])
        self.assertEqual(after['segments'][1],backend)
        self.assertEqual(after['analysis']['backend_holds'],{backend['id']:'old-proof'})
        self.assertEqual(after['analysis']['speaker_review_holds']['reasons']['speaker_review'],1)
        self.assertEqual(after['analysis']['speaker_review_holds']['reasons']['neighbour'],1)

    def late_correction(self,stop=True,review=False,unrelated_review=False,returned_status='ready'):
        store,book,generation,config=self.book();gid=generation['id'];target=generation['segments'][0]
        self.jobs.lock=threading.RLock();self.jobs.stop=threading.Event();self.jobs.closed=True
        self.jobs.active=gid;self.jobs.engine=None
        def prepare(_):
            # Deterministic handoff: equivalent to a report acquiring the job
            # lock after the final ready snapshot and before _run finalizes.
            store.execute("UPDATE segments SET status=?,audio_hash=NULL WHERE generation_id=? AND id=?",('review'if review else'pending',gid,target['id']))
            if unrelated_review:
                store.execute("UPDATE segments SET status='review' WHERE generation_id=? AND id=?",(gid,generation['segments'][1]['id']))
            store.execute('INSERT INTO listener_correction_jobs(id,report_id,book_id,generation_id,segment_ids,status,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                ('j'*24,'r'*24,book['id'],gid,json.dumps([target['id']]),'queued','now','now'))
            if stop:self.jobs.stop.set()
            return returned_status
        self.jobs._prepare=prepare
        self.jobs._run(gid)
        self.assertEqual(store.generation(gid)['status'],returned_status if returned_status!='ready'else'held'if review or unrelated_review else'paused')
        self.assertEqual(store.rows('SELECT status FROM listener_correction_jobs')[0]['status'],
            'held'if review or returned_status=='held'and not stop else'queued')

    def test_correction_arriving_after_ready_return_is_queued_not_false_applied(self):
        self.late_correction()

    def test_pending_correction_target_prevents_false_applied_even_without_stop_signal(self):
        self.late_correction(stop=False)

    def test_late_correction_human_hold_settles_without_automatic_queue_loop(self):
        self.late_correction(review=True)

    def test_late_pending_correction_stays_queued_despite_unrelated_retained_hold(self):
        self.late_correction(unrelated_review=True)

    def test_late_edit_interrupts_an_already_held_worker_and_stays_queued(self):
        self.late_correction(returned_status='held',unrelated_review=True)

    def test_stable_quality_hold_without_late_edit_does_not_requeue(self):
        self.late_correction(returned_status='held',stop=False)

    def test_explicit_pause_after_ready_return_is_preserved(self):
        store,book,generation,config=self.book();gid=generation['id']
        self.jobs.lock=threading.RLock();self.jobs.stop=threading.Event();self.jobs.closed=True
        self.jobs.active=gid;self.jobs.engine=None
        self.jobs._prepare=lambda _:(self.jobs.stop.set()or'ready')
        self.jobs._run(gid)
        self.assertEqual(store.generation(gid)['status'],'paused')
        self.assertEqual(store.rows('SELECT * FROM listener_correction_jobs'),[])

    def test_delivered_correction_is_applied_despite_unrelated_generation_hold(self):
        store,book,generation,config=self.book();gid=generation['id'];target,unrelated=generation['segments']
        store.execute("UPDATE segments SET status='review' WHERE generation_id=? AND id=?",(gid,unrelated['id']))
        store.execute('INSERT INTO listener_correction_jobs(id,report_id,book_id,generation_id,segment_ids,status,created,updated) VALUES(?,?,?,?,?,?,?,?)',
            ('j'*24,'r'*24,book['id'],gid,json.dumps([target['id']]),'running','now','now'))
        self.jobs.lock=threading.RLock();self.jobs.stop=threading.Event();self.jobs.closed=True
        self.jobs.active=gid;self.jobs.engine=None;self.jobs._prepare=lambda _:'held'
        self.jobs._run(gid)
        self.assertEqual(store.generation(gid)['status'],'held')
        outcome=store.rows('SELECT status,error FROM listener_correction_jobs')[0]
        self.assertEqual(outcome,{'status':'applied','error':''})

    def test_cache_repair_keeps_previous_playable_part_during_native_replacement(self):
        store,book,generation,config=self.book(extra=True);gid=generation['id'];old=copy.deepcopy(store.generation(gid)['segments'][0])
        path=store.root/'audio'/f"{old['audio_hash']}.json"
        receipt=json.loads(path.read_text('utf8'));receipt.pop('synthesis',None)
        receipt.pop('synthesis_parts',None)
        path.write_text(json.dumps(receipt),encoding='utf8')
        native=self.jobs._guarded;observed=[]
        def guarded(method,payload):
            if method=='render':
                shown=next(s for s in store.playback_generation(gid)['segments']if s['id']==old['id'])
                observed.append(shown)
                self.assertEqual(shown['status'],'ready')
                self.assertEqual(shown['audio_hash'],old['audio_hash'])
            return native(method,payload)
        self.jobs._guarded=guarded
        self.jobs._render(gid,self.pack,store.generation(gid)['config'],store.book(book['id']),'Russian')
        self.assertEqual(len(observed),1)

    def test_explicit_postprocessing_gates_only_work_chapter(self):
        store,book,generation,config=self.book();gid=generation['id'];chapters=generation['config']['chapters']
        retained=copy.deepcopy(store.generation(gid)['segments'][0])
        current=store.create_generation(book['id'],config|{'post_processing':'on','russian_preparation':prep.config('local')},chapters=[chapters[1]])
        native=self.jobs._guarded
        def checked(method,payload):
            if method=='verify_audio':
                self.calls.append((method,copy.deepcopy(payload)))
                verifier=digest({k:v for k,v in self.pack['files'].items()if k.startswith('verifier/')})
                return transcript_check(payload['text'],payload['text'],file_digest(Path(payload['audio'])),verifier)
            return native(method,payload)
        self.jobs._guarded=checked
        def accept(gid,pack,book,config):
            for chapter in config['chapters']:
                store.execute("UPDATE segments SET status='ready' WHERE generation_id=? AND chapter_id=? AND audio_hash IS NOT NULL",(gid,chapter))
        self.jobs._chapter_acceptance=Mock(side_effect=accept)
        self.jobs._render(gid,self.pack,current['config'],store.book(book['id']),'Russian')
        self.assertEqual(self.prepared,[chapters[1]])
        self.assertEqual(self.jobs._chapter_acceptance.call_args.args[3]['chapters'],[chapters[1]])
        self.assertEqual(store.generation(gid)['segments'][0],retained)
        self.assertEqual([method for method,_ in self.calls],['render','verify_audio'])
        latest=store.generation(gid)
        store.execute('UPDATE generations SET analysis=? WHERE id=?',
            (json.dumps({**latest['analysis'],'scene_direction':{chapters[1]:{'complete':True}}}),gid))
        from rimsky.chapter_gate import music_released
        latest=store.generation(gid)
        self.assertTrue(music_released(latest,chapters[0],store.root),'Unselected old off chapter keeps its actual music policy')
        self.assertEqual(latest['analysis']['chapter_processing'][chapters[0]]['recognition_status'],'not_checked')
        self.assertFalse(music_released(latest,chapters[1],store.root),'New on output still needs its real chapter gate')

    def review(self,store,generation,change):
        gid=generation['id'];current=store.generation(gid)
        store.execute('UPDATE generations SET status=?,analysis=? WHERE id=?',
            ('review',json.dumps({**current['analysis'],'approved':False}),gid))
        self.jobs.lock=threading.RLock();self.jobs.active=None;self.jobs._pack=lambda *a:self.pack
        with patch('rimsky.jobs.cast_flags',side_effect=lambda cast,*a:cast),patch('rimsky.jobs.casting_summary',return_value={}):
            return self.jobs._review(gid,change)

    def test_changed_chapter_voice_marks_only_its_ready_owners_pending(self):
        store,book,generation,config=self.book();before=store.generation(generation['id'])
        first=before['segments'][0];second=copy.deepcopy(before['segments'][1])
        def change(generation,pack):
            return generation['cast'],{**generation['analysis'],'narrator_voices':{'':'n',first['chapter_id']:'c'}},{}
        changed=self.review(store,generation,change)
        self.assertEqual(changed['segments'][0]['status'],'pending');self.assertIsNone(changed['segments'][0]['audio_hash'])
        self.assertEqual(changed['segments'][1],second)
        playable=store.playback_generation(generation['id'])['segments'][0]
        self.assertEqual(playable['audio_hash'],first['audio_hash'])

    def test_display_only_cast_rename_does_not_invalidate_ready_audio(self):
        store,book,generation,config=self.book();before=store.generation(generation['id'])['segments']
        def change(generation,pack):
            return [{**c,'name':'Display only'}for c in generation['cast']],generation['analysis'],{}
        changed=self.review(store,generation,change)
        self.assertEqual(changed['segments'],before)

    def test_same_voice_character_merge_invalidates_frozen_source_persona_only(self):
        store,book,generation,config=self.book(extra=True);gid=generation['id']
        current=store.generation(gid);target=current['segments'][0]
        cast=[*current['cast'],{'id':'a','name':'Source','aliases':[],'voice_id':'n'},
            {'id':'b','name':'Target','aliases':[],'voice_id':'n'}]
        performance={**target['performance'],'higgs_direction':{'fixture':'source direction'},
            'character_profile':{'pitch':'low'},'voice_signature':{'character_id':'a','reference':'old'}}
        store.execute('UPDATE segments SET speaker=?,performance=? WHERE generation_id=? AND id=?',('a',json.dumps(performance),gid,target['id']))
        analysis={**current['analysis'],'character_direction':{'complete':True,'profiles':{'a':{'pitch':'low'},'b':{'pitch':'high'}}},'approved':False}
        store.execute("UPDATE generations SET cast=?,analysis=?,status='review' WHERE id=?",(json.dumps(cast),json.dumps(analysis),gid))
        before=store.generation(gid)
        self.jobs.lock=threading.RLock();self.jobs.active=None;self.jobs._pack=lambda *a:self.pack
        with patch('rimsky.jobs.cast_flags',side_effect=lambda cast,*a:cast),patch('rimsky.jobs.casting_summary',return_value={}):
            changed=self.jobs.cast_merge(gid,'a','b')
        self.assertEqual(changed['segments'][0]['speaker'],'b')
        self.assertEqual(changed['segments'][0]['status'],'pending')
        self.assertIsNone(changed['segments'][0]['audio_hash'])
        self.assertEqual(changed['segments'][1:],before['segments'][1:])
        self.assertEqual(store.playback_generation(gid)['segments'][0]['audio_hash'],target['audio_hash'])

    def test_subset_scene_checkpoint_preserves_ready_neighbors_and_resume_state(self):
        store,book,generation,config=self.book(extra=True);gid=generation['id']
        current=store.generation(gid);old,target=current['segments'][:2];old_key=old['id']+':0';target_key=target['id']+':0'
        old=copy.deepcopy(old)
        analysis={**current['analysis'],'character_direction':{'complete':True,'profiles':{'narrator':{}}},
            'scene_direction':{target['chapter_id']:{'complete':True,'by_unit':{old_key:{'old':'retained'}}}}}
        store.execute('UPDATE generations SET analysis=? WHERE id=?',(json.dumps(analysis),gid))
        store.execute("UPDATE segments SET status='pending' WHERE generation_id=? AND id=?",(gid,target['id']))
        observed=[]
        def direct(payload,infer,saved,save,**kwargs):
            observed.append(saved)
            self.assertEqual([u for turn in payload['turns']for u in turn['unit_ids']],[target_key])
            state={'complete':True,'by_unit':{target_key:{'fixture':'new'}}};save(state);return state
        with patch('rimsky.scene_direction.direct',side_effect=direct),patch('rimsky.character_direction.signature_for',return_value={}):
            self.jobs._scene_direction(gid,self.pack,store.generation(gid)['config'],book,{'analysis_mode':'local'})
            self.jobs._scene_direction(gid,self.pack,store.generation(gid)['config'],book,{'analysis_mode':'local'})
        result=store.generation(gid)
        self.assertEqual(result['segments'][0],old)
        state=result['analysis']['scene_direction'][target['chapter_id']]
        self.assertEqual(set(state['by_unit']),{old_key,target_key})
        self.assertIsNone(observed[0]);self.assertEqual(set(observed[1]['by_unit']),{target_key})

    def test_incompatible_partial_request_fails_before_any_generation_mutation(self):
        store,book,generation,config=self.book();gid=generation['id'];before=store.generation(gid)
        tables={name:store.rows('SELECT * FROM '+name)for name in ('generations','segments','audiobook_replacements','audiobook_part_receipts')}
        with self.assertRaisesRegex(Exception,'Select all currently narrated chapters'):
            store.create_generation(book['id'],config|{'pack_fingerprint':'different'},chapters=[generation['config']['chapters'][1]])
        self.assertEqual(store.generation(gid),before)
        self.assertEqual({name:store.rows('SELECT * FROM '+name)for name in tables},tables)
        complete=store.create_generation(book['id'],config|{'pack_fingerprint':'different'},chapters=generation['config']['chapters'])
        self.assertEqual(complete['id'],gid)
        self.assertTrue(all(s['status']=='pending'for s in complete['segments']))

    def test_retained_on_gate_uses_origin_pack_not_new_global_pack(self):
        from rimsky.chapter_gate import CHECKS,report,generation_binding,processing_origin,music_released
        store,book,generation,config=self.book();old=store.generation(generation['id']);chapter=old['config']['chapters'][0]
        parts=[s for s in old['segments']if s['chapter_id']==chapter]
        gate=report(generation_binding(parts,old['config']['pack_fingerprint'],store.root),
            {name:[{'status':'pass','basis':'fixture'}]for name in CHECKS},False)
        old['config']['post_processing']='on';old['analysis'].update(scene_direction={chapter:{}},chapter_acceptance={chapter:gate})
        origin=processing_origin(old,chapter)
        current=copy.deepcopy(old);current['config']['pack_fingerprint']='new-pack'
        current['analysis']['chapter_acceptance']={};current['analysis']['chapter_processing']={chapter:origin}
        self.assertTrue(music_released(current,chapter,store.root))
        current['segments'][0]['spoken']+='changed'
        self.assertFalse(music_released(current,chapter,store.root))


if __name__=='__main__':unittest.main()
