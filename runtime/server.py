from __future__ import annotations
import argparse
import base64
import contextlib
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import shutil
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from rimsky import __version__
from rimsky.audio_source import open_audio_pair
from rimsky.opening import opening_reference, verify_opening_audio
from rimsky.playback_streams import PlaybackStreams
from rimsky.playback_hls import PlaybackHLS
from rimsky.books import MAX_UPLOAD
from rimsky.export import build_export
from rimsky.jobs import Engine, Jobs
from rimsky.instance import library_lease
from rimsky.music import MusicJobs
from rimsky import recommendations
from rimsky.integrity import accepted_audio
from rimsky.packs import health, load_pack, read_verification_record, resolve_character_analysis, verified_pack
from rimsky.store import Store
from rimsky.util import AppError, atomic_write, bounded_text, inside, require_id
from rimsky.visuals import generate_visual, save_visual, visual_brief, image_preview
from rimsky.illustrations import IllustrationJobs, codex_path

LOCK_FREE = frozenset({'library', 'book', 'generation', 'generation_poll', 'visuals', 'visuals_poll', 'visual_brief',
                       'illustration_history', 'illustration_status', 'illustration_pause', 'settings', 'health',
                       'save_position', 'save_reading_position', 'pause', 'generate_visual', 'select_pack', 'export', 'cancel_export', 'pack_voices',
                       'stress_reports', 'music_cues', 'music_retry', 'recommendation_genres', 'recommendations', 'playback_streams'})


class SessionRejected(AppError):
    pass


def either(*events):
    return SimpleNamespace(is_set=lambda: any(event.is_set() for event in events))


def place(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(path))))


def pinned_packs(store):
    pinned = []
    for row in store.rows('SELECT config FROM generations ORDER BY rowid DESC'):
        with contextlib.suppress(ValueError, TypeError, KeyError, AttributeError):
            config = json.loads(row['config'])
            pinned.append((place(config['pack_path']), config['pack_fingerprint'], str(config.get('pinned_at') or '')))
    return pinned


def relocated_pack(packs, selected, pinned):
    if os.path.exists(os.path.join(os.path.expanduser(selected), 'pack.json')):
        return None
    bundled = []
    for manifest in sorted(packs.glob('*/pack.json')):
        with contextlib.suppress(AppError, OSError, ValueError):
            bundled.append(load_pack(str(manifest.parent)))
    stored = place(selected)
    mine = [(pin, fingerprint) for path, fingerprint, pin in pinned if path == stored]
    stamped = [entry for entry in mine if entry[0]]
    newest = max(stamped, key=lambda entry: entry[0])[1] if stamped else mine[0][1] if len({fingerprint for _, fingerprint in mine}) == 1 else None
    if match := next((pack['_root'] for pack in bundled if pack['_fingerprint'] == newest), None):
        return match
    folder = Path(stored)
    if folder.parent.name != 'packs' or folder.parent.parent.name != os.path.normcase(packs.resolve().parent.name):
        return None
    named = [pack['_root'] for pack in bundled if os.path.normcase(Path(pack['_root']).name) == folder.name]
    return named[0] if named else bundled[0]['_root'] if len(bundled) == 1 else None


def storage_bytes(root):
    total = 0
    for path in root.rglob('*'):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


class VerifiedFiles:
    """Remember files whose integrity check passed, keyed by path, size and mtime.

    A changed file has a different key and is verified again (fail closed).
    """
    def __init__(self,limit=4096):
        self.limit=limit
        self.lock=threading.Lock()
        self.items={}
    def known(self,stamp):
        with self.lock:
            return stamp in self.items
    def add(self,stamp):
        with self.lock:
            self.items[stamp]=True
            while len(self.items)>self.limit:
                self.items.pop(next(iter(self.items)))

VERIFIED_AUDIO=VerifiedFiles()

def verification_stamp(path,source,metadata):
    audio=os.fstat(source.fileno())
    meta=os.fstat(metadata.fileno())
    return (str(path),audio.st_size,audio.st_mtime_ns,meta.st_size,meta.st_mtime_ns)

def parse_range(header,size):
    """Return None (serve whole), 'bad' (416) or an inclusive (start, end) pair."""
    if not header:
        return None
    unit,_,spec=header.partition('=')
    if unit.strip().lower()!='bytes' or ',' in spec:
        return None
    first,dash,last=spec.strip().partition('-')
    if not dash:
        return None
    try:
        if first=='':
            count=int(last)
            if count<=0:
                return 'bad'
            return (max(0,size-count),size-1) if size else 'bad'
        start=int(first)
        end=int(last) if last else size-1
    except ValueError:
        return None
    if start<0 or start>=size or end<start:
        return 'bad'
    return (start,min(end,size-1))

IMMUTABLE_ASSETS=frozenset({'audio','music','playback','playback_hls','playback_hls_init','playback_hls_segment'})


class Application:
    def __init__(self,data_dir,binaries,*,auto_resume=True,correction_profile=None):
        resource=Path(os.environ.get('RIMSKY_RESOURCE_DIR',str(Path(__file__).parents[1]/'resources')))
        from rimsky.listener_stress import shared_profile
        self.store=Store(data_dir, recover_interrupted=True, resources=resource,
                         correction_profile=correction_profile or shared_profile())
        self.binaries=binaries
        record=self.store.root/'pack-verification.json'
        self.pack_verification_cache=read_verification_record(record)
        self.closing=threading.Event()
        self.export_cancel=threading.Event()
        self.exporting=threading.Lock()
        self.verify_pack=lambda path,stop=None,pinned=False,provider=None: verified_pack(path,self.pack_verification_cache,self.closing if stop is None else stop,record,pinned,
                                                                                     provider or resolve_character_analysis(self.store.settings()['character_analysis']))
        self.jobs=Jobs(self.store,binaries,verified=self.verify_pack,resources=resource)
        self.illustrations=IllustrationJobs(self.store)
        self.music=MusicJobs(self.store,self.jobs,resource,engine_factory=lambda:Engine(binaries))
        self.playback_streams=PlaybackStreams(self.store,binaries,self.music)
        self.playback_hls=PlaybackHLS(self.store,binaries,self.music)
        self.mutations=threading.RLock()
        self.health_cache={}
        self.relocation=None
        selected=self.store.settings()['pack_path']
        if not selected:
            candidates=list((resource/'packs').glob('*/pack.json'))
            if len(candidates)==1:
                self.store.update_settings({'pack_path':str(candidates[0].parent)})
        else:
            self.relocation=threading.Thread(target=self.relocate,args=(resource/'packs',selected,pinned_packs(self.store)),daemon=True,name='rimsky-pack-relocation')
            self.relocation.start()
        self.auto_resume=None
        if auto_resume:
            self.auto_resume=threading.Thread(target=self.resume_interrupted,daemon=True,name='rimsky-preparation-resume')
            self.auto_resume.start()
        elif self.store.rows("SELECT id FROM listener_correction_jobs WHERE status IN ('queued','running') LIMIT 1"):
            # --no-auto-resume concerns unsolicited preparation; a persisted
            # explicit listener correction remains an owned repair request.
            self.auto_resume=threading.Thread(target=self.jobs.resume_listener_corrections,daemon=True,name='rimsky-listener-correction-resume')
            self.auto_resume.start()
        self.listener_queue_monitor=threading.Thread(target=self.monitor_listener_queues,daemon=True,name='rimsky-explicit-listener-queue-owner')
        self.listener_queue_monitor.start()

    def monitor_listener_queues(self):
        from rimsky.listener_stress_retake import resume_external_listener_queues
        while not self.closing.is_set():
            try:resume_external_listener_queues(self.store)
            except Exception:
                # Individual queue requests retain status/evidence on disk;
                # retry owner adoption on the next bounded monitor cycle.
                pass
            if self.closing.wait(15):break

    def resume_interrupted(self):
        if self.relocation:
            self.relocation.join()
        if not self.closing.is_set():
            self.jobs.resume_interrupted()

    def relocate(self,packs,selected,pinned):
        with contextlib.suppress(Exception):
            if replacement:=relocated_pack(packs,selected,pinned):
                with self.mutation():
                    if self.store.settings()['pack_path']==selected:
                        self.store.update_settings({'pack_path':replacement})

    def job_book(self,table,identifier):
        rows=self.store.rows(f'SELECT b.id,b.title FROM {table} j JOIN books b ON b.id=j.book_id WHERE j.id=?',(identifier,)) if identifier else []
        return rows[0] if rows else None
    def require_open(self):
        if self.closing.is_set():
            raise AppError('The local application is closing.')

    def cached(self,name,compute,seconds=60):
        checked,value=self.health_cache.get(name,(None,None))
        if checked is None or time.monotonic()-checked>=seconds:
            value=compute()
            self.health_cache[name]=(time.monotonic(),value)
        return value

    @contextlib.contextmanager
    def mutation(self):
        with self.mutations:
            self.require_open()
            yield

    def dispatch(self,op,data):
        self.require_open()
        store=self.store
        if op=='library':
            return store.library()
        if op=='book':
            return store.book(data['id'])
        if op=='generation':
            return store.generation(data['id'])
        if op=='stress_reports':
            from rimsky.listener_stress import snapshot,notes,_book_key
            from rimsky.listener_stress_retake import queue_status_index,report_queue_status
            book=store.book(data['book_id'])
            local=store.stress_reports(book['id'])
            # Reconcile old local receipts before reading the common profile.
            # Other volumes may share name rules, but their raw notes do not
            # belong in this source book's feedback history.
            shared=notes(store,book)
            known={row['report']['id'] for row in shared}
            if any(report['id'] not in known for report in local):
                snapshot(store,book)
                shared=notes(store,book)
            merged={row['report']['id']:dict(row['report']) for row in shared if row['book_key']==_book_key(book)}
            merged.update({report['id']:report for report in local})
            reports=sorted(merged.values(),key=lambda report:report['created'],reverse=True)
            queue_states=queue_status_index(store,book['id'])
            local_generations={row['id'] for row in store.rows('SELECT id FROM generations WHERE book_id=?',(book['id'],))}
            for report in reports:
                status=report_queue_status(store,report,queue_states) if report['generation_id'] in local_generations else None
                if status is not None:report['retake_queue']=status
            return reports
        if op=='report_stress':
            report=store.report_stress(data['generation_id'],data['segment_id'],data['source_id'],data['selected_text'],data.get('text_offset'),data.get('audio_offset'),data.get('kind','stress'),data.get('comment',''),data.get('intended_stress'))
            if report.get('kind')=='stress' and 'character_state' not in report:
                try:
                    from rimsky.series_characters import record_listener_stress
                    report['character_state']=record_listener_stress(store,report)
                except Exception as error:
                    # Character promotion is an enhancement; the source-bound
                    # report and its exact retake remain valid independently.
                    report['character_state']={'status':'held','error':str(error)}
            try:
                from rimsky.listener_stress_retake import enqueue_listener_stress_retake
                queued=enqueue_listener_stress_retake(store,report)
                if queued is None and report.get('kind')=='stress':
                    queued=self.jobs.listener_stress(report)
                if queued is not None:report['retake_queue']=queued
            except Exception as error:
                # The human report is durable even when generation cannot start.
                # Surface the queue hold without asking the listener to report it again.
                report['retake_queue']={'id':report['id'],'status':'held','error':str(error)}
            return report
        if op=='generation_poll':
            return store.generation_poll(data['id'], data.get('revision'))
        if op=='external_jobs':
            from rimsky.external_progress import external_jobs
            return {'jobs':external_jobs(Path(__file__).resolve().parents[1],ttl=20.0)}
        if op=='visuals_poll':
            return store.visuals_poll(data['book_id'], data.get('revision'))
        if op=='visuals':
            return store.visuals(data['book_id'])
        if op=='illustration_history':
            return self.illustrations.history(data['book_id'])
        if op=='illustration_status':
            return self.illustrations.status(data['id'])
        if op=='illustration_start':
            return self.illustrations.start(data['book_id'], data['source_ids'], data.get('style', 'literary'), data.get('consent'), data.get('replace_existing', False))
        if op=='illustration_resume':
            return self.illustrations.resume(data['id'], data.get('consent'), data.get('retry_failed', False))
        if op=='illustration_pause':
            return self.illustrations.pause(data['id'])
        if op=='music_cues':
            return self.music.status(data['generation_id'])
        if op=='playback_streams':
            transport = data.get('transport', 'mp3')
            if transport not in ('mp3', 'hls'):
                raise AppError('Unsupported continuous playback transport.')
            streams = self.playback_hls if transport == 'hls' else self.playback_streams
            return streams.request(data['generation_id'], data.get('music', False), data.get('music_volume', 1.),
                                   data.get('priority_segment_id'), data.get('chapter_id'))
        if op=='music_retry':
            return self.music.retry(data['generation_id'])
        if op=='settings':
            return store.settings()
        if op=='recommendation_genres':
            return recommendations.genres()
        if op=='recommendations':
            return recommendations.recommend(store,store.library(),data.get('genres',store.settings()['recommendation_genres']),stop=self.closing)
        if op=='health':
            settings=store.settings()
            result=health(settings,self.binaries,self.verify_pack)
            result['active']=active=self.jobs.active
            result['illustration_active']=running=self.illustrations.active
            result['active_book']=self.job_book('generations',active)
            result['illustration_book']=self.job_book('illustration_runs',running)
            result['storage_bytes']=self.cached('storage_bytes',lambda:storage_bytes(store.root))
            result['codex']=self.cached('codex',lambda:bool(codex_path()),seconds=5)
            result['music']=self.music.health()
            return result
        if op=='update_settings':
            previous=store.settings()['pack_path']
            updated=store.update_settings(data)
            if updated['pack_path']!=previous:
                self.pack_verification_cache.clear()
            if {'music','music_pack_path'} & set(data):
                self.music.notify()
            return updated
        if op=='select_pack':
            try:
                path = bounded_text(data.get('path'), 16000)
                pack = self.verify_pack(path, pinned=load_pack(path, pinned=True)['_fingerprint'] in {entry[1] for entry in pinned_packs(store)})
            except AppError:
                self.require_open()
                raise
            with self.mutation():
                settings = store.update_settings({'pack_path': pack['_root']})
                for root in list(self.pack_verification_cache):
                    if root != pack['_root']:
                        self.pack_verification_cache.pop(root, None)
            return {'settings': settings, 'health': self.dispatch('health', {})}
        if op=='generate':
            if self.jobs.active:
                raise AppError('Another generation is active. Pause it before creating a new one.')
            current=store.settings()
            provider=resolve_character_analysis(current['character_analysis'])
            status=health({**current,'character_analysis':provider},self.binaries,lambda path:self.verify_pack(path,provider=provider))
            if not status['ready']:
                raise AppError(status['pack_error'] or status.get('runtime_error') or 'Missing inference dependencies: '+', '.join(status['missing']))
            pack=self.verify_pack(current['pack_path'],provider=provider)
            config={'pack_path':pack['_root'],'pack_fingerprint':pack['_fingerprint'],'pack_name':pack['name'],'device':current['device'],'align_words':current['align_words'],
                    'character_analysis':provider,'speaker_tie_breaker':current['speaker_tie_breaker'],'unresolved_policy':current['unresolved_policy'],
                    'pinned_at':datetime.now(timezone.utc).isoformat(timespec='microseconds')}
            generation=store.create_generation(data['book_id'],config,data.get('name',''),data.get('chapters'))
            return self.jobs.start(generation['id'])
        if op=='repair_audio':
            return self.jobs.repair(data['id'])
        if op=='resume':
            return self.jobs.start(data['id'])
        if op=='pause':
            return self.jobs.pause(data['id'])
        if op=='start_narration':
            return self.jobs.start_narration(data['id'])
        if op=='cast_rename':
            return self.jobs.cast_rename(data['id'],data['character_id'],data['name'])
        if op=='cast_merge':
            return self.jobs.cast_merge(data['id'],data['source_id'],data['target_id'])
        if op=='cast_voice':
            return self.jobs.cast_voice(data['id'],data['character_id'],data['voice_id'])
        if op=='cast_name_stress':
            return self.jobs.cast_name_stress(data['id'],data['character_id'],bounded_text(data['stressed'],300))
        if op=='cast_narrator':
            return self.jobs.cast_narrator(data['id'],data.get('chapter_id',''),data['voice_id'])
        if op=='cast_kind':
            return self.jobs.cast_kind(data['id'],data['character_id'],data['kind'])
        if op=='pack_voices':
            return self.jobs.pack_voices(data['id'])
        if op=='revise':
            return self.jobs.revise(data['id'],data['changes'],data.get('name',''))
        if op=='rename_generation':
            store.generation(data['id'])
            store.execute('UPDATE generations SET name=? WHERE id=?',(bounded_text(data['name']),data['id']))
            return store.generation(data['id'])
        if op=='delete_generation':
            if self.jobs.active == data['id']:
                raise AppError('Pause preparation before deleting stored audio.')
            return store.delete_generation(data['id'])
        if op=='delete_book':
            if self.illustrations.active and self.illustrations.snapshot(self.illustrations.active)['book_id'] == data['id']:
                raise AppError('Stop illustration preparation before deleting this book.')
            if self.jobs.active:
                active = store.generation(self.jobs.active)
                if active['book_id'] == data['id']:
                    raise AppError('Pause preparation before deleting a book.')
            return store.delete_book(data['id'])
        if op=='save_position':
            return store.save_position(data['book_id'],data['position'])
        if op=='save_reading_position':
            return store.save_reading_position(data['book_id'],data['position'])
        if op=='bookmark':
            return store.bookmark(data['book_id'],data['source_id'],data.get('note',''))
        if op=='delete_bookmark':
            store.execute('DELETE FROM bookmarks WHERE id=?',(require_id(data['id']),))
            return True
        if op=='update_bookmark':
            return store.update_bookmark(data['id'], data.get('note', ''))
        if op=='export':
            if not self.exporting.acquire(blocking=False):
                raise AppError('Another export is in progress.')
            try:
                self.export_cancel.clear()
                return build_export(store,data['id'],data['format'],data.get('chapters'),self.binaries,either(self.closing,self.export_cancel),self.mutation,
                                    music=lambda segments:self.music.export_overlays(data['id'],segments))
            finally:
                self.exporting.release()
        if op=='cancel_export':
            self.export_cancel.set()
            return {'requested':True}
        if op=='delete_export':
            return store.delete_export(data['id'])
        if op=='visual_brief':
            return visual_brief(store,data['book_id'],data['source_id'])
        if op=='generate_visual':
            return generate_visual(store,data['book_id'],data['source_id'],data.get('api_key'),data.get('consent'),self.mutation)
        if op=='delete_visual':
            with self.illustrations.lock, store.lock:
                rows=store.rows('SELECT * FROM visuals WHERE id=?',(require_id(data['id']),))
                if any(self.illustrations.deletion_stops_active(row) for row in rows):
                    raise AppError('Wait for the running illustration preparation to finish before deleting this paragraph’s last image; deleting or stopping now would discard a paid request.','illustration_run_active')
                store.execute('DELETE FROM visuals WHERE id=?',(data['id'],))
                warnings = []
                for row in rows:
                    if not store.rows('SELECT id FROM visuals WHERE file=?',(row['file'],)):
                        warnings.extend(store.remove_files([store.root/'visuals'/row['file']]))
                    self.illustrations.visual_deleted(row)
                return {'deleted': True, 'cleanup_warnings': warnings}
        raise AppError('Unknown application operation.')

    @contextlib.contextmanager
    def open_audio(self, asset_id):
        require_id(asset_id)
        path = self.store.root/'audio'/f'{asset_id}.wav'
        with contextlib.ExitStack() as resources:
            with self.store.lock:
                opening = opening_reference(self.store, asset_id)
                if not opening and not self.store.rows("SELECT id FROM segments WHERE audio_hash=? AND status='ready' LIMIT 1", (asset_id,)):
                    raise AppError('Completed audio not found.')
                source, metadata = resources.enter_context(open_audio_pair(path))
            if opening:
                verify_opening_audio(path, opening, stream=source, metadata_stream=metadata)
                yield source
                return
            stamp = verification_stamp(path, source, metadata)
            if not VERIFIED_AUDIO.known(stamp):
                if accepted_audio(path, stream=source, metadata_stream=metadata)['reset_cues']:
                    raise AppError('Saved audio repair is incomplete. Resume preparation before playback.')
                VERIFIED_AUDIO.add(stamp)
            yield source

    @contextlib.contextmanager
    def open_asset(self, kind, asset_id, book_id=None, index=None):
        if kind in ('playback_hls', 'playback_hls_init', 'playback_hls_segment'):
            asset = {'playback_hls': 'playlist', 'playback_hls_init': 'init', 'playback_hls_segment': 'segment'}[kind]
            with self.playback_hls.open(asset_id, asset, index) as source:
                yield source, 'application/vnd.apple.mpegurl' if asset == 'playlist' else 'audio/mp4'
        elif kind == 'playback':
            with self.playback_streams.open(asset_id) as source:
                yield source, 'audio/mpeg'
        elif kind == 'audio':
            with self.open_audio(asset_id) as source:
                yield source, 'audio/wav'
        elif kind == 'music':
            with self.music.open(asset_id) as source:
                yield source, 'audio/wav'
        else:
            path, mime = self.asset(kind, asset_id, book_id)
            with path.open('rb') as source:
                yield source, mime

    def asset(self,kind,asset_id,book_id=None):
        if kind=='audio':
            with self.open_audio(asset_id):
                return self.store.root/'audio'/f'{asset_id}.wav', 'audio/wav'
        if kind=='fantlab_cover':
            return recommendations.cover(self.store,asset_id)
        if kind=='book_preview':
            file, _ = self.asset('book', asset_id, book_id)
            return image_preview(file, self.store.root/'books'/book_id/'previews'), 'image/png'
        if kind=='visual_preview':
            file, _ = self.asset('visual', asset_id)
            return image_preview(file, self.store.root/'visuals'/'previews'), 'image/png'
        if kind=='book':
            book=self.store.book(book_id)
            mime=next((a.get('mime') for a in book['assets'] if a['id']==asset_id),None)
            if mime is None:
                raise AppError('Book image not found.')
            return self.store.root/'books'/book_id/'assets'/asset_id,mime if mime in ('image/png','image/jpeg') else 'image/png'
        if kind=='visual':
            rows=self.store.rows('SELECT file FROM visuals WHERE id=?',(require_id(asset_id),))
            if rows:
                return self.store.root/'visuals'/rows[0]['file'],'image/png'
        if kind=='export':
            rows=self.store.rows('SELECT file FROM exports WHERE id=?',(require_id(asset_id),))
            if rows:
                path=self.store.root/'exports'/rows[0]['file']
                return path,mimetypes.guess_type(path)[0] or 'application/octet-stream'
        raise AppError('Asset not found.')

    def close(self):
        self.closing.set()
        if self.listener_queue_monitor is not threading.current_thread():self.listener_queue_monitor.join(timeout=8)
        self.playback_streams.shutdown()
        self.playback_hls.shutdown()
        self.jobs.shutdown()
        self.illustrations.shutdown()
        self.music.shutdown()
        if self.auto_resume is not None and self.auto_resume is not threading.current_thread():
            self.auto_resume.join(timeout=8)
        with self.mutations:
            self.store.close()


def handler_for(app,token,dev=False,static=None,session_aliases=()):
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args):
            return
        def allowed_origin(self):
            origin=self.headers.get('Origin')
            allowed={'tauri://localhost','http://tauri.localhost','https://tauri.localhost',f'http://127.0.0.1:{self.server.server_port}'}
            if dev:
                allowed|={'http://localhost:1420','http://127.0.0.1:1420'}
            return origin is None or origin in allowed
        def authorize(self):
            host=self.headers.get('Host','')
            if host not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}') or not self.allowed_origin():
                raise AppError('Request origin is not permitted.')
            supplied=self.headers.get('Authorization','')
            if not any(hmac.compare_digest(supplied,'Bearer '+candidate) for candidate in (token,*session_aliases)):
                raise SessionRejected('Local session authentication failed.')
        def send_headers(self,status,length,mime,extra=(),cache='no-store'):
            self.send_response(status)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(length))
            self.send_header('Cache-Control',cache)
            for key,value in extra:
                self.send_header(key,value)
            self.send_header('X-Content-Type-Options','nosniff')
            if self.allowed_origin() and self.headers.get('Origin'):
                self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
                self.send_header('Vary','Origin')
            self.end_headers()
        def result(self,result,status=200):
            raw=json.dumps(result,ensure_ascii=False,allow_nan=False).encode()
            self.send_headers(status,len(raw),'application/json; charset=utf-8')
            self.wfile.write(raw)
        def fail(self,error,status=400):
            self.close_connection=True
            code=getattr(error,'code','')
            try:
                self.result({'error':str(error)[:4000],**({'code':code,'details':error.details} if code else {})},status)
            except (BrokenPipeError,ConnectionResetError):
                pass
        def do_OPTIONS(self):
            if not self.allowed_origin():
                self.fail('Origin is not permitted.',403)
                return
            self.send_response(204)
            if self.headers.get('Origin'):
                self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
            self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type')
            self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
            self.send_header('Content-Length','0')
            self.end_headers()
        def do_POST(self):
            try:
                self.authorize()
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=MAX_UPLOAD+1024:
                    raise AppError('Request body is empty or exceeds 50 MiB.')
                self.connection.settimeout(300)
                raw=self.rfile.read(length)
                if len(raw)!=length:
                    raise AppError('Upload was interrupted.')
                url=urlsplit(self.path)
                query=parse_qs(url.query)
                if url.path=='/import':
                    with app.mutations:
                        app.require_open()
                        self.result(app.store.import_data(raw,query.get('name',['book.epub'])[0]))
                elif url.path=='/visual':
                    with app.mutations:
                        app.require_open()
                        self.result(save_visual(app.store,query['book'][0],query['source'][0],raw,query.get('caption',[''])[0]))
                elif url.path=='/api':
                    if len(raw)>4*1024*1024:
                        raise AppError('Application message is too large.')
                    request=json.loads(raw)
                    if not isinstance(request,dict) or set(request)-{'op','data'} or not isinstance(request.get('data',{}),dict):
                        raise AppError('Invalid application message.')
                    op=request.get('op')
                    if isinstance(op,str) and op in LOCK_FREE:
                        result=app.dispatch(op,request.get('data',{}))
                    else:
                        with app.mutations:
                            result=app.dispatch(op,request.get('data',{}))
                    self.result(result)
                else:
                    self.fail('Not found.',404)
            except SessionRejected as error:
                self.fail(error,401)
            except (AppError,ValueError,KeyError,TypeError,OSError) as error:
                self.fail(error)
            except Exception as error:
                self.fail(f'Operation failed: {type(error).__name__}: {error}',500)
        def do_GET(self):
            responding = False
            try:
                url=urlsplit(self.path)
                if static and url.path!='/asset':
                    file=inside(static,url.path.lstrip('/') or 'index.html')
                    if file.is_file():
                        raw=file.read_bytes()
                        responding = True
                        self.send_headers(200,len(raw),mimetypes.guess_type(file)[0] or 'application/octet-stream')
                        self.wfile.write(raw)
                        return
                    self.fail('Not found.',404)
                    return
                self.authorize()
                query=parse_qs(url.query)
                if url.path!='/asset':
                    raise AppError('Unknown resource.')
                kind=query['kind'][0]
                with app.open_asset(kind,query['id'][0],query.get('book',[None])[0],query.get('index',[None])[0]) as (source,mime):
                    length = os.fstat(source.fileno()).st_size
                    if kind not in IMMUTABLE_ASSETS:
                        responding = True
                        self.send_headers(200,length,mime)
                        shutil.copyfileobj(source,self.wfile,1024*1024)
                        return
                    # Audio and music ids are content addresses: the bytes never change.
                    etag='"'+query['id'][0]+'"'
                    cache='private, max-age=31536000, immutable'
                    validators=(('ETag',etag),('Accept-Ranges','bytes'))
                    supplied=[item.strip() for item in self.headers.get('If-None-Match','').split(',')]
                    if etag in supplied or '*' in supplied:
                        responding = True
                        self.send_response(304)
                        self.send_header('Cache-Control',cache)
                        self.send_header('ETag',etag)
                        self.send_header('X-Content-Type-Options','nosniff')
                        if self.allowed_origin() and self.headers.get('Origin'):
                            self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
                            self.send_header('Vary','Origin')
                        self.end_headers()
                        return
                    wanted=self.headers.get('Range')
                    if_range=self.headers.get('If-Range')
                    if if_range and if_range.strip()!=etag:
                        wanted=None
                    window=parse_range(wanted,length)
                    responding = True
                    if window=='bad':
                        self.send_headers(416,0,mime,validators+(('Content-Range',f'bytes */{length}'),),cache)
                        return
                    if window is None:
                        self.send_headers(200,length,mime,validators,cache)
                        shutil.copyfileobj(source,self.wfile,1024*1024)
                        return
                    start,end=window
                    self.send_headers(206,end-start+1,mime,validators+(('Content-Range',f'bytes {start}-{end}/{length}'),),cache)
                    source.seek(start)
                    remaining=end-start+1
                    while remaining>0:
                        chunk=source.read(min(1024*1024,remaining))
                        if not chunk:
                            self.close_connection = True
                            break
                        self.wfile.write(chunk)
                        remaining-=len(chunk)
            except SessionRejected as error:
                self.fail(error,401)
            except (AppError,KeyError,ValueError,OSError) as error:
                if responding:
                    self.close_connection = True
                else:
                    self.fail(error,404)
    return Handler

def startup_failed(error):
    message=(str(error) if isinstance(error,AppError) else f'{type(error).__name__}: {error}')[:2000] or type(error).__name__
    print(json.dumps({'protocol':1,'error':message}),flush=True)
    if isinstance(error,AppError):
        print(message,file=sys.stderr,flush=True)
    else:
        traceback.print_exception(error)
    return 1

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--dev',action='store_true')
    parser.add_argument('--parent-pipe',action='store_true')
    parser.add_argument('--static')
    parser.add_argument('--persistent-dev-session',action='store_true')
    parser.add_argument('--no-auto-resume',action='store_true',help='Serve saved work without resuming interrupted generation.')
    parser.add_argument('--version',action='store_true')
    args=parser.parse_args()
    if args.version:
        print(json.dumps({'version':__version__}),flush=True)
        return 0
    if not args.data_dir:
        parser.error('the following arguments are required: --data-dir')
    if args.persistent_dev_session and not args.dev:
        parser.error('--persistent-dev-session requires --dev')
    with contextlib.ExitStack() as resources:
        try:
            resources.enter_context(library_lease(Path(args.data_dir)))
            binaries=Path(os.environ['RIMSKY_RESOURCE_DIR'])/'runtime' if os.environ.get('RIMSKY_RESOURCE_DIR') else Path(sys.executable).parent.parent if getattr(sys,'frozen',False) else Path(__file__).parents[1]/'resources'/'runtime'
            app=resources.enter_context(contextlib.closing(Application(Path(args.data_dir),binaries,auto_resume=not args.no_auto_resume)))
            token=dev_session_token(Path(args.data_dir)) if args.persistent_dev_session else secrets.token_urlsafe(32)
            aliases=dev_session_aliases(Path(args.data_dir),token) if args.persistent_dev_session else ()
            static=Path(args.static).resolve() if args.static else None
            server=ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(app,token,args.dev,static,aliases))
        except Exception as error:
            return startup_failed(error)
        serve(args,app,server,token,static)
    return 0

def dev_session_token(data_dir: Path):
    path = data_dir.resolve() / '.dev-session-token'
    if path.exists():
        token = path.read_text(encoding='ascii').strip()
        if not (40 <= len(token) <= 128 and all(char.isalnum() or char in '-_' for char in token)):
            raise AppError('The saved local session token is invalid.')
        return token
    token = secrets.token_urlsafe(32)
    atomic_write(path, (token + '\n').encode('ascii'))
    return token

def dev_session_aliases(data_dir: Path, primary: str):
    aliases = []
    for path in sorted(data_dir.glob('runtime.stdout*.log'), key=lambda item: item.stat().st_mtime, reverse=True)[:12]:
        try:
            prior = json.loads(path.read_text(encoding='utf-8').splitlines()[0])['token']
        except (OSError, ValueError, IndexError, KeyError, TypeError):
            continue
        if isinstance(prior, str) and 40 <= len(prior) <= 128 and all(char.isalnum() or char in '-_' for char in prior) and prior != primary and prior not in aliases:
            aliases.append(prior)
    return tuple(aliases)

def serve(args,app,server,token,static):
    server.daemon_threads=True
    ready={'port':server.server_port,'token':token,'protocol':1}
    print(json.dumps(ready),flush=True)
    if args.dev:
        target=server.server_port if static else 1420
        print(f'Open http://127.0.0.1:{target}/#port={server.server_port}&token={token}',file=sys.stderr,flush=True)
    def stop(*_):
        app.closing.set()
        app.jobs.stop.set()
        app.illustrations.stop.set()
        threading.Thread(target=server.shutdown,daemon=True).start()
    if args.parent_pipe:
        def parent_watch():
            sys.stdin.read()
            stop()
        threading.Thread(target=parent_watch,daemon=True).start()
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        server.serve_forever(poll_interval=.2)
    finally:
        server.server_close()

if __name__=='__main__':
    sys.exit(main())
