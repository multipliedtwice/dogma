from __future__ import annotations
import base64
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
sys.path[:0] = [str(Path(__file__).parents[1] / 'runtime'), str(Path(__file__).parent)]
from test_core import FB2, complete_audio, epub_fixture
from rimsky.illustrations import IllustrationJobs, paragraph_plan, render_codex
from rimsky.util import AppError, digest, write_json
from rimsky.visuals import save_visual
from server import Application


def png(color='red', size=(32, 20)):
    out=io.BytesIO();Image.new('RGB',size,color).save(out,format='PNG');return out.getvalue()


class ParagraphIllustrations(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.app=Application(self.root/'library',self.root/'bin');self.addCleanup(self.app.close)
        self.store=self.app.store
        self.book=self.store.import_data(epub_fixture({'OEBPS/a.xhtml':b'<html><body><h1>Chapter</h1><p>First paragraph.</p><p>Second paragraph.</p><p>Third paragraph.</p></body></html>'}), 'fixture.epub')
        self.sources=[block['id'] for chapter in self.book['chapters'] for block in chapter['blocks'] if block['kind']=='paragraph']
        self.store.update_settings({'online_images':True})
        self.calls=[]
        self.app.illustrations.preflight=lambda:'controlled-codex'
        def render(brief, style, stop, root):
            self.calls.append(brief['source_id']);return png()
        self.app.illustrations.renderer=render

    def start(self, sources=None):
        return self.app.dispatch('illustration_start',{'book_id':self.book['id'],'source_ids':self.sources if sources is None else sources,'style':'ink','consent':True})

    def finished(self, run):
        self.app.illustrations.thread.join(5)
        self.assertFalse(self.app.illustrations.thread.is_alive())
        return self.app.illustrations.snapshot(run['id'])

    def test_plan_is_one_job_per_paragraph_in_canonical_order(self):
        items=paragraph_plan(self.book,self.sources[::-1])
        self.assertEqual(self.sources,[item['source_id'] for item in items])
        self.assertEqual(len(self.sources),len(items))

    def test_unknown_duplicate_empty_and_nonparagraph_inputs_are_rejected(self):
        heading=self.book['chapters'][0]['blocks'][0]['id']
        for selection in ([],['unknown'],[heading],[self.sources[0]]*2,[None]):
            with self.subTest(selection=selection),self.assertRaises(AppError):paragraph_plan(self.book,selection)

    def test_oversized_paragraph_is_not_silently_clipped(self):
        book=json.loads(json.dumps(self.book));book['chapters'][0]['blocks'][1]['text']='x'*16001
        with self.assertRaisesRegex(AppError,'truncated'):paragraph_plan(book,[self.sources[0]])

    def test_images_are_attached_to_exact_paragraphs_without_changing_text_or_audio(self):
        manifest=self.store.rows('SELECT manifest FROM books')[0]['manifest']
        run=self.finished(self.start())
        self.assertEqual('ready',run['status']);self.assertEqual(self.sources,self.calls)
        visuals=self.store.book(self.book['id'])['visuals']
        self.assertEqual(self.sources,[item['source_id'] for item in visuals])
        self.assertEqual(manifest,self.store.rows('SELECT manifest FROM books')[0]['manifest'])
        self.assertEqual([],self.store.rows('SELECT * FROM generations'))
        self.assertEqual([],list((self.store.root/'audio').iterdir()))

    def test_existing_images_are_kept_without_a_provider_call(self):
        for source in self.sources:save_visual(self.store,self.book['id'],source,png())
        self.app.illustrations.preflight=lambda:(_ for _ in ()).throw(AssertionError('provider preflight not needed'))
        result=self.finished(self.start());self.assertEqual('ready',result['status']);self.assertEqual([],self.calls)
        self.assertTrue(all(item['status']=='skipped' for item in result['items']))

    def test_missing_image_is_not_mistaken_for_reusable_output(self):
        visual=save_visual(self.store,self.book['id'],self.sources[0],png())
        (self.store.root/'visuals'/visual['file']).unlink()
        result=self.finished(self.start([self.sources[0]]));self.assertEqual('ready',result['status'])
        self.assertEqual([self.sources[0]],self.calls)

    def test_stop_after_success_keeps_first_image_and_resume_reuses_it(self):
        def render(brief,style,stop,root):
            self.calls.append(brief['source_id'])
            if len(self.calls)==1:stop.set()
            return png()
        self.app.illustrations.renderer=render
        run=self.finished(self.start());self.assertEqual('paused',run['status']);self.assertEqual(1,len(self.calls))
        self.app.dispatch('illustration_resume',{'id':run['id'],'consent':True})
        result=self.finished(run);self.assertEqual('ready',result['status']);self.assertEqual(self.sources,self.calls)

    def test_failure_does_not_automatically_retry_or_dispatch_remaining_images(self):
        fail=True
        def render(brief,style,stop,root):
            self.calls.append(brief['source_id'])
            if fail and len(self.calls)==2:raise AppError('controlled provider failure')
            return png()
        self.app.illustrations.renderer=render
        run=self.finished(self.start());self.assertEqual('failed',run['status']);self.assertEqual(2,len(self.calls))
        with self.assertRaisesRegex(AppError,'explicit approval'):
            self.app.dispatch('illustration_resume',{'id':run['id'],'consent':True})
        fail=False
        self.app.dispatch('illustration_resume',{'id':run['id'],'consent':True,'retry_failed':True})
        self.assertEqual('ready',self.finished(run)['status'])
        self.assertEqual(1,self.calls.count(self.sources[0]))

    def test_restart_marks_inflight_requests_uncertain_not_automatically_retried(self):
        run=self.finished(self.start([self.sources[0]]))
        self.store.execute('DELETE FROM visuals')
        state={key:run[key] for key in ('style','provider','items','error')};state['items'][0]['status']='running'
        self.app.illustrations._save(run['id'],'running',state)
        restarted=IllustrationJobs(self.store);self.addCleanup(restarted.shutdown)
        saved=restarted.snapshot(run['id']);self.assertEqual('paused',saved['status']);self.assertEqual('uncertain',saved['items'][0]['status'])
        with self.assertRaisesRegex(AppError,'explicit approval'):restarted.resume(run['id'],True)

    def test_permission_is_required_and_revocation_stops_further_dispatch(self):
        self.store.update_settings({'online_images':False})
        with self.assertRaises(AppError):self.start()
        self.store.update_settings({'online_images':True})
        with self.assertRaises(AppError):self.app.dispatch('illustration_start',{'book_id':self.book['id'],'source_ids':self.sources,'consent':False})
        def render(brief,style,stop,root):
            self.calls.append(brief['source_id']);self.store.update_settings({'online_images':False});return png()
        self.app.illustrations.renderer=render
        result=self.finished(self.start());self.assertEqual('failed',result['status']);self.assertEqual(1,len(self.calls))

    def test_missing_codex_does_not_create_a_run_or_use_another_provider(self):
        self.app.illustrations.preflight=lambda:(_ for _ in ()).throw(AppError('Codex missing'))
        with self.assertRaisesRegex(AppError,'Codex missing'):self.start()
        self.assertEqual([],self.store.rows('SELECT * FROM illustration_runs'))

    def test_thread_start_failure_releases_the_active_slot(self):
        with patch('rimsky.illustrations.threading.Thread.start',side_effect=RuntimeError('controlled startup failure')):
            with self.assertRaises(RuntimeError):self.start()
        self.assertIsNone(self.app.illustrations.active)
        run=self.app.illustrations.history(self.book['id'])[0];self.assertEqual('failed',run['status'])
        self.app.dispatch('illustration_resume',{'id':run['id'],'consent':True})
        self.assertEqual('ready',self.finished(run)['status'])

    def test_attachment_arriving_during_generation_is_not_overwritten(self):
        def render(brief,style,stop,root):
            save_visual(self.store,self.book['id'],brief['source_id'],png('blue'),'User attachment')
            return png('red')
        self.app.illustrations.renderer=render
        run=self.finished(self.start([self.sources[0]]))
        visuals=self.store.visuals(self.book['id'])
        self.assertEqual('ready',run['items'][0]['status']);self.assertEqual(['imported','generated-codex'],[v['kind'] for v in visuals])
        self.assertEqual('User attachment',visuals[0]['caption']);self.assertEqual(visuals[1]['id'],run['items'][0]['visual_id'])

    def test_active_book_cannot_be_deleted_and_shutdown_rejects_new_work(self):
        entered=threading.Event()
        def render(brief,style,stop,root):
            entered.set();stop.wait(3);raise AppError('stopped')
        self.app.illustrations.renderer=render
        run=self.start();self.assertTrue(entered.wait(2))
        with self.assertRaisesRegex(AppError,'Stop illustration'):self.app.dispatch('delete_book',{'id':self.book['id']})
        self.app.dispatch('illustration_pause',{'id':run['id']});self.finished(run)
        self.app.illustrations.shutdown()
        with self.assertRaisesRegex(AppError,'closing'):self.start()

    def test_invalid_image_result_is_never_published(self):
        self.app.illustrations.renderer=lambda *args:b'not an image'
        result=self.finished(self.start([self.sources[0]]))
        self.assertEqual('failed',result['status']);self.assertEqual([],self.store.visuals(self.book['id']))

    def test_saved_image_before_checkpoint_failure_is_reused(self):
        original=self.app.illustrations._save;failed=False
        def save(run_id,status,state):
            nonlocal failed
            if not failed and any(item['status']=='ready' for item in state['items']):failed=True;raise OSError('controlled commit failure')
            return original(run_id,status,state)
        self.app.illustrations._save=save
        run=self.finished(self.start());self.assertEqual('failed',run['status'])
        self.app.dispatch('illustration_resume',{'id':run['id'],'consent':True,'retry_failed':True})
        self.assertEqual('ready',self.finished(run)['status']);self.assertEqual(self.sources,self.calls)

    def test_thumbnails_are_bounded_keep_original_and_delete_with_visual(self):
        raw=png('blue',(3000,2000));visual=save_visual(self.store,self.book['id'],self.sources[0],raw)
        original=self.store.root/'visuals'/visual['file'];before=original.read_bytes()
        preview,mime=self.app.asset('visual_preview',visual['id']);self.assertEqual('image/png',mime)
        with Image.open(preview) as image:self.assertEqual((1280,853),image.size)
        self.assertEqual(before,original.read_bytes())
        self.app.dispatch('delete_visual',{'id':visual['id']});self.assertFalse(preview.exists());self.assertFalse(original.exists())

    def test_unchanged_generation_poll_does_not_load_or_return_full_generation(self):
        generation=self.store.create_generation(self.book['id'],{})
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?',(json.dumps({'complete':False,'huge_private_checkpoint':'x'*10000}),generation['id']))
        first=self.app.dispatch('generation_poll',{'id':generation['id']})
        self.assertNotIn('huge_private_checkpoint',first['generation']['analysis'])
        with patch.object(self.store,'generation',side_effect=AssertionError('unchanged data must not be loaded')):
            same=self.app.dispatch('generation_poll',{'id':generation['id'],'revision':first['revision']})
        self.assertIsNone(same['generation']);self.assertLess(len(json.dumps(same)),160)

    def test_segment_and_export_writes_invalidate_poll_but_position_does_not(self):
        generation=self.store.create_generation(self.book['id'],{});gid=generation['id']
        first=self.store.generation_poll(gid)
        self.store.execute("UPDATE segments SET warning='changed' WHERE generation_id=? AND id=?",(gid,generation['segments'][0]['id']))
        second=self.store.generation_poll(gid,first['revision']);self.assertIsNotNone(second['generation'])
        self.store.save_position(self.book['id'],{'chapter_id':self.book['chapters'][0]['id'],'offset':0})
        self.assertIsNone(self.store.generation_poll(gid,second['revision'])['generation'])
        self.store.execute('INSERT INTO exports VALUES(?,?,?,?,?)',('export',gid,'file.m4b','m4b','now'))
        self.assertIsNotNone(self.store.generation_poll(gid,second['revision'])['generation'])

    def test_failed_transaction_does_not_publish_a_revision(self):
        generation=self.store.create_generation(self.book['id'],{});revision=self.store.generation_poll(generation['id'])['revision']
        with self.assertRaises(RuntimeError):
            with self.store.lock,self.store.db:
                self.store.db.execute("UPDATE segments SET warning='rolled back' WHERE generation_id=?",(generation['id'],));raise RuntimeError('abort')
        self.assertIsNone(self.store.generation_poll(generation['id'],revision)['generation'])

    def test_visual_poll_detects_insert_delete_and_no_change(self):
        first=self.store.visuals_poll(self.book['id'])
        self.assertIsNone(self.store.visuals_poll(self.book['id'],first['revision'])['visuals'])
        visual=save_visual(self.store,self.book['id'],self.sources[0],png())
        second=self.store.visuals_poll(self.book['id'],first['revision']);self.assertEqual(1,len(second['visuals']))
        self.app.dispatch('delete_visual',{'id':visual['id']})
        self.assertEqual([],self.store.visuals_poll(self.book['id'],second['revision'])['visuals'])

    def test_bad_revisions_are_rejected(self):
        generation=self.store.create_generation(self.book['id'],{})
        for revision in ('1',True,0,-1):
            with self.assertRaises(AppError):self.store.generation_poll(generation['id'],revision)
            with self.assertRaises(AppError):self.store.visuals_poll(self.book['id'],revision)

    def test_codex_adapter_contract_uses_workspace_and_fixed_output_not_returned_paths(self):
        script=self.root/'controlled-codex'
        image=base64.b64encode(png()).decode()
        script.write_text(f'''#!{sys.executable}
import sys,json,pathlib,base64,os
args=sys.argv[1:]
assert '--sandbox' in args and args[args.index('--sandbox')+1]=='workspace-write'
assert '--ignore-user-config' in args and '--ephemeral' in args
assert not os.environ.get('OPENAI_API_KEY') and not os.environ.get('CODEX_API_KEY')
assert sys.stdin.read()
brief=json.loads(pathlib.Path('paragraph.json').read_text())
pathlib.Path('result.png').write_bytes(base64.b64decode({image!r}))
pathlib.Path('result.json').write_text(json.dumps({{'source_id':brief['source_id'],'status':'generated','file':'result.png','reason':''}}))
''');script.chmod(0o700)
        with patch.dict(os.environ,{'OPENAI_API_KEY':'must-not-pass','CODEX_API_KEY':'must-not-pass'}):
            output=render_codex({'source_id':'paragraph','passage':'Synthetic verification only.'},'ink',threading.Event(),self.root,executable=str(script))
        self.assertEqual(png(),output)

    def test_codex_adapter_refuses_wrong_anchor_outside_file_and_symlink(self):
        for mode in ('wrong-anchor','outside','symlink','missing-tool'):
            script=self.root/'controlled-codex'
            script.write_text(f'''#!{sys.executable}
import json,pathlib
source=json.loads(pathlib.Path('paragraph.json').read_text())['source_id']
mode={mode!r}
if mode=='symlink':pathlib.Path('result.png').symlink_to('/etc/passwd')
pathlib.Path('result.json').write_text(json.dumps({{'source_id':'wrong' if mode=='wrong-anchor' else source,'status':'unavailable' if mode=='missing-tool' else 'generated','file':'../outside.png' if mode=='outside' else 'result.png','reason':'no image tool'}}))
''');script.chmod(0o700)
            with self.subTest(mode=mode),self.assertRaises(AppError):render_codex({'source_id':'paragraph'},'ink',threading.Event(),self.root,executable=str(script))

    def test_codex_deadline_and_predispatch_stop_do_not_retry(self):
        script=self.root/'controlled-codex';script.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(30)\n');script.chmod(0o700)
        with self.assertRaisesRegex(AppError,'time or output'):render_codex({'source_id':'p'},'ink',threading.Event(),self.root,executable=str(script),timeout=.15)
        stop=threading.Event();stop.set()
        with self.assertRaisesRegex(AppError,'before dispatch'):render_codex({'source_id':'p'},'ink',stop,self.root,executable=str(script))

    def test_explicit_new_version_preserves_previous_image(self):
        old=save_visual(self.store,self.book['id'],self.sources[0],png('blue'))
        run=self.app.dispatch('illustration_start',{'book_id':self.book['id'],'source_ids':self.sources[:1],'consent':True,'replace_existing':True})
        self.assertEqual('ready',self.finished(run)['status'])
        visuals=self.store.visuals(self.book['id']);self.assertEqual(2,len(visuals));self.assertEqual(old['id'],visuals[0]['id']);self.assertEqual(self.sources[:1],self.calls)
        self.assertTrue((self.store.root/'visuals'/old['file']).is_file())

    def test_force_new_versions_for_a_whole_chapter_is_rejected(self):
        with self.assertRaisesRegex(AppError,'one paragraph'):
            self.app.dispatch('illustration_start',{'book_id':self.book['id'],'source_ids':self.sources,'consent':True,'replace_existing':True})
        self.assertEqual([],self.calls)

    def test_new_version_does_not_replace_a_later_manual_attachment(self):
        old=save_visual(self.store,self.book['id'],self.sources[0],png('blue'))
        def render(brief,style,stop,root):
            save_visual(self.store,self.book['id'],self.sources[0],png('green'));self.calls.append(brief['source_id']);return png('red')
        self.app.illustrations.renderer=render
        run=self.app.dispatch('illustration_start',{'book_id':self.book['id'],'source_ids':self.sources[:1],'consent':True,'replace_existing':True})
        result=self.finished(run);self.assertEqual('ready',result['items'][0]['status']);visuals=self.store.visuals(self.book['id'])
        self.assertEqual([old['id'],'imported','generated-codex'],[visuals[0]['id'],visuals[1]['kind'],visuals[2]['kind']]);self.assertEqual(visuals[2]['id'],result['items'][0]['visual_id'])

    def test_authenticated_http_prepares_previews_polls_and_deletes(self):
        import secrets
        from http.server import ThreadingHTTPServer
        from urllib.request import Request,urlopen
        from urllib.parse import urlencode
        from urllib.error import HTTPError
        from server import handler_for
        token=secrets.token_urlsafe(32)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.app,token,dev=True));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path,data=None,auth=token):
            raw=json.dumps(data).encode() if data is not None else None
            with urlopen(Request(f'http://127.0.0.1:{server.server_port}'+path,data=raw,headers={'Authorization':'Bearer '+auth,'Content-Type':'application/json'}),timeout=5) as response:return response.read()
        def api(op,data):return json.loads(request('/api',{'op':op,'data':data}))
        try:
            run=api('illustration_start',{'book_id':self.book['id'],'source_ids':self.sources,'consent':True})
            self.finished(run)
            snapshot=api('visuals_poll',{'book_id':self.book['id']});self.assertEqual(len(self.sources),len(snapshot['visuals']))
            self.assertIsNone(api('visuals_poll',{'book_id':self.book['id'],'revision':snapshot['revision']})['visuals'])
            image=snapshot['visuals'][0]
            raw=request('/asset?'+urlencode({'kind':'visual_preview','id':image['id']}))
            with Image.open(io.BytesIO(raw)) as bitmap:self.assertEqual((32,20),bitmap.size)
            self.assertEqual(True,api('delete_visual',{'id':image['id']})['deleted'])
            current=api('visuals_poll',{'book_id':self.book['id'],'revision':snapshot['revision']});self.assertEqual([],current['visuals']);self.assertEqual([image['id']],current['removed_visual_ids']);self.assertGreater(current['revision'],snapshot['revision'])
            with self.assertRaises(HTTPError):request('/asset?'+urlencode({'kind':'visual_preview','id':image['id']}))
            with self.assertRaises(HTTPError):request('/api',{'op':'illustration_history','data':{'book_id':self.book['id']}},auth='bad')
        finally:server.shutdown();server.server_close();thread.join(5)


    def test_slow_illustration_does_not_block_saved_audio_or_pause(self):
        generation=self.store.create_generation(self.book['id'],{})
        complete_audio(self.store,generation)
        saved=self.store.generation(generation['id']);key=saved['segments'][0]['audio_hash'];started=threading.Event()
        def render(brief,style,stop,root):
            started.set()
            while not stop.wait(.01):pass
            raise AppError('Controlled image request interrupted')
        self.app.illustrations.renderer=render
        run=self.start();self.assertTrue(started.wait(2))
        path,mime=self.app.asset('audio',key)
        self.assertTrue(path.is_file());self.assertEqual('audio/wav',mime)
        self.assertEqual('ready',self.app.dispatch('generation_poll',{'id':generation['id']})['generation']['status'])
        self.app.dispatch('illustration_pause',{'id':run['id']})
        self.assertEqual('paused',self.finished(run)['status'])
        self.assertEqual(saved,self.store.generation(generation['id']))


class LibraryMigration(unittest.TestCase):
    def setUp(self):
        from rimsky.store import Store
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        store=Store(self.root/'new');book=store.import_data(FB2,'example.fb2');generation=store.create_generation(book['id'],{'chapters':[book['chapters'][0]['id']]},'Saved')
        self.original={table:store.rows('SELECT * FROM '+table) for table in ['settings','books','generations','segments','positions','bookmarks','visuals','exports']}
        store.db.close();self.old=self.root/'old';self.old.mkdir();db=sqlite3.connect(self.old/'library.sqlite3')
        db.executescript((Path(__file__).parent/'fixtures/library-v1.sql').read_text())
        for table,rows in self.original.items():
            columns={item[1] for item in db.execute('PRAGMA table_info('+table+')')}
            for row in rows:
                row={key:value for key,value in row.items() if key in columns}
                db.execute('INSERT INTO '+table+'('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
        db.commit();db.close()

    def test_existing_schema_one_rows_survive_migration_and_reopen(self):
        from rimsky.store import Store
        for _ in range(2):
            store=Store(self.old)
            self.assertEqual(5,store.db.execute('PRAGMA user_version').fetchone()[0])
            for table,rows in self.original.items():self.assertEqual(rows,store.rows('SELECT * FROM '+table),table)
            gid=self.original['generations'][0]['id'];self.assertEqual(1,store.generation_poll(gid)['revision'])
            store.db.close()

    def test_failed_migration_rolls_back_schema_version_and_new_tables(self):
        from rimsky.store import Store
        class Failure(sqlite3.Connection):
            def execute(self,statement,*args,**kwargs):
                if 'exports_version_insert' in statement:raise sqlite3.OperationalError('controlled DDL failure')
                return super().execute(statement,*args,**kwargs)
        connect=sqlite3.connect
        with patch('rimsky.store.sqlite3.connect',side_effect=lambda *args,**kwargs:connect(*args,**kwargs,factory=Failure)):
            with self.assertRaisesRegex(sqlite3.OperationalError,'DDL failure'):Store(self.old)
        db=connect(self.old/'library.sqlite3');self.assertEqual(1,db.execute('PRAGMA user_version').fetchone()[0]);self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='illustration_runs'").fetchone());db.close()
        store=Store(self.old);self.assertEqual(5,store.db.execute('PRAGMA user_version').fetchone()[0]);store.db.close()

    def test_imported_rotated_jpeg_preserves_its_visible_orientation(self):
        from rimsky.books import image_asset
        raw=io.BytesIO();image=Image.new('RGB',(60,30),'red');metadata=Image.Exif();metadata[274]=6;image.save(raw,format='JPEG',exif=metadata)
        key=image_asset(raw.getvalue(),self.root/'images',{})
        with Image.open(self.root/'images'/key) as saved:self.assertEqual((30,60),saved.size)
