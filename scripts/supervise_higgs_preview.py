"""Persistently monitor, resume and heal one authorized preview generation."""
from __future__ import annotations
import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import write_json,AppError,file_digest
from rimsky.cli_process import terminate_process_tree
from render_higgs_palette_preview import PAGE,MEDIA,page
from higgs_preview_music import attach_music
from higgs_trial_io import read_json

def read(path):return read_json(path)
def now():return datetime.now(timezone.utc).isoformat()

def checkpoint_activity(paths):
    """Atomic checkpoint replacement may briefly remove an enumerated path."""
    result=[]
    for path in paths:
        try:status=path.stat()
        except (FileNotFoundError,PermissionError):continue
        result.append((str(path),status.st_mtime_ns,status.st_size))
    return tuple(result)


MUSIC_FIELDS=('music','audio_with_music','music_layout','music_pending')

def copy_music_fields(destination,source):
    for key in MUSIC_FIELDS:
        if key in source:destination[key]=source[key]
        else:destination.pop(key,None)

def publish_supervision(out,state,is_alive,restarts):
    from higgs_published_timeline import publication_lock
    with publication_lock(out,blocking=False) as acquired:
        if not acquired:return False
        latest=read(out/'progress.json')
        if {k:v['wav_sha256'] for k,v in latest.get('chapters',{}).items()}!={k:v['wav_sha256'] for k,v in state.get('chapters',{}).items()}:
            return False
        for cid,row in state.get('chapters',{}).items():
            if cid in latest.get('chapters',{}):
                copy_music_fields(latest['chapters'][cid],row)
        if 'music' in state:latest['music']=state['music']
        latest['supervision']={'worker_alive':is_alive,'restarts':restarts,'updated':now(),
            'status':'running' if is_alive else 'recovering','error':state.get('error')}
        write_json(PAGE/'progress.json',latest);page()
        return True


def refresh_music(out,cid,state):
    """Never mix concurrently with a speech/timeline publication."""
    from higgs_published_timeline import publication_lock
    with publication_lock(out,blocking=False) as acquired:
        if not acquired:return False
        latest=read(out/'progress.json')
        if latest.get('chapters',{}).get(cid,{}).get('wav_sha256')!=state['chapters'][cid]['wav_sha256']:return False
        attach_music(out/cid,cid,state,PAGE,MEDIA)
        copy_music_fields(latest['chapters'][cid],state['chapters'][cid])
        latest['music']=state['music'];write_json(out/'progress.json',latest)
        return True

def alive(pid,handle=None):
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32];kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        own_handle=handle is None
        handle=handle or kernel.OpenProcess(0x1000,False,pid)
        if not handle:return False
        try:
            code=ctypes.c_uint32()
            if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):raise OSError('Could not inspect supervised worker')
            return code.value==259
        finally:
            if own_handle:kernel.CloseHandle(handle)
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False

def retryable(message):
    text=message.lower()
    return any(s in text for s in ('transport','timeout','timed out','too many requests','429','unavailable',
        'missing spoken words remain','unverified spoken words remain','unsafe audio still needs','out of memory','could not complete','stalled'))

def audio_busy(pid,allow_audit_checkpoint=False):
    if os.name!='nt':return True  # Never guess an idle native boundary.
    import psutil
    try:children=psutil.Process(pid).children(recursive=True)
    except psutil.NoSuchProcess:return False
    for child in children:
        try:
            if child.name().lower()=='audiocpp_cli.exe':return True
            # Recognizers read immutable audio and write resumable atomic JSON
            # checkpoints. A requested code reload may stop them; native audio
            # generation must always reach its existing safe boundary first.
            if not allow_audit_checkpoint and any('audit_higgs' in part for part in child.cmdline()):return True
        except psutil.NoSuchProcess:continue
        except psutil.AccessDenied:raise AppError('Cannot establish an owned audio checkpoint boundary')
    return False

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--folder',type=Path,required=True);p.add_argument('--bank',type=Path,required=True)
    p.add_argument('--output-name',required=True);p.add_argument('--voice-descriptors',type=Path,required=True)
    p.add_argument('--spoken-corrections',type=Path,required=True);p.add_argument('--adopt-pid',type=int)
    p.add_argument('--chapters',nargs='+',default=['c0001','c0002','c0003'],help='Authorized chapter order, including delivery priority')
    p.add_argument('--poll-seconds',type=float,default=10);p.add_argument('--max-restarts',type=int,default=3)
    p.add_argument('--pronunciation-policy');p.add_argument('--name-stress',type=Path);p.add_argument('--homograph-stress',type=Path);p.add_argument('--yo-restoration',type=Path)
    p.add_argument('--hard-e',type=Path);p.add_argument('--listener-respellings',type=Path)
    p.add_argument('--reference-onset-padding',action='store_true');p.add_argument('--book-fixes',type=Path)
    p.add_argument('--preserve-frozen-direction-contract',action='store_true')
    p.add_argument('--number-policy');p.add_argument('--terminal-punctuation',action='store_true');p.add_argument('--stress-witness',action='store_true')
    p.add_argument('--one-shot',action='store_true',help='Render and publish take 0 only; retain quality flags without synthesis retries')
    args=p.parse_args();out=args.folder.resolve()/args.output_name;out.mkdir(parents=True,exist_ok=True)
    lock=(out/'supervisor.lock').open('a+b')
    if os.name=='nt':
        import msvcrt
        if lock.tell()==0:lock.write(b'0');lock.flush()
        lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise AppError('A supervisor already owns this generation')
    else:
        import fcntl
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if (out/'supervision.json').exists():
        prior=read(out/'supervision.json')
        if args.adopt_pid is None and prior.get('worker_pid') and alive(prior['worker_pid']):
            import psutil
            candidate=psutil.Process(prior['worker_pid'])
            if prior.get('worker_created') is not None and candidate.create_time()!=prior['worker_created']:
                raise AppError('Recorded worker PID belongs to a different process')
            args.adopt_pid=prior['worker_pid']
        archived=out/'supervision-history'/('snapshot-'+file_digest(out/'supervision.json')[:20]+'.json')
        write_json(archived,read(out/'supervision.json'))
    command=[sys.executable,'-X','utf8','-u',str(ROOT/'scripts/render_higgs_palette_preview.py'),
        '--folder',str(args.folder.resolve()),'--bank',str(args.bank.resolve()),'--output-name',args.output_name,
        '--voice-descriptors',str(args.voice_descriptors.resolve()),'--spoken-corrections',str(args.spoken_corrections.resolve()),
        *(['--preserve-frozen-direction-contract'] if args.preserve_frozen_direction_contract else []),
        '--heal-word-flags','--chapters',*getattr(args,'chapters',['c0001','c0002','c0003']),
        *(['--pronunciation-policy',args.pronunciation_policy,'--name-stress',str(args.name_stress.resolve())] if args.pronunciation_policy else []),
        *(['--homograph-stress',str(args.homograph_stress.resolve())] if getattr(args,'homograph_stress',None) else []),
        *(['--yo-restoration',str(args.yo_restoration.resolve())] if getattr(args,'yo_restoration',None) else []),
        *(['--hard-e',str(args.hard_e.resolve())] if getattr(args,'hard_e',None) else []),
        *(['--listener-respellings',str(args.listener_respellings.resolve())] if getattr(args,'listener_respellings',None) else []),
        *(['--book-fixes',str(args.book_fixes.resolve())] if getattr(args,'book_fixes',None) else []),
        *(['--reference-onset-padding'] if getattr(args,'reference_onset_padding',False) else []),
        *(['--number-policy',args.number_policy] if getattr(args,'number_policy',None) else []),
        *(['--terminal-punctuation'] if getattr(args,'terminal_punctuation',False) else []),
        *(['--stress-witness'] if getattr(args,'stress_witness',False) else []),
        *(['--one-shot'] if getattr(args,'one_shot',False) else [])]
    pid=args.adopt_pid;process=None;restarts=0;healing_started=False;music_versions={};music_cache={};journal=[];logs=[]
    adopted_handle=None;launch_number=max([-1]+[int(p.name.split('-')[1].split('.')[0]) for p in out.glob('supervised-*.stdout.log')])+1
    if pid is not None and os.name=='nt':
        check=subprocess.run(['powershell.exe','-NoProfile','-Command',
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}').CommandLine"],
            capture_output=True,text=True,timeout=30)
        if check.returncode or 'render_higgs_palette_preview.py' not in check.stdout or args.output_name not in check.stdout:
            raise AppError('Refusing to supervise an unrelated adopted process')
        healing_started='--heal-word-flags' in check.stdout
        kernel=ctypes.WinDLL('kernel32');kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
        kernel.OpenProcess.restype=ctypes.c_void_p;adopted_handle=kernel.OpenProcess(0x1000,False,pid)
    activity_signature=None;last_activity=time.monotonic()
    while True:
        cooperative=False
        if pid is None:
            stdout_path=out/f'supervised-{launch_number:03d}.stdout.log';stderr_path=out/f'supervised-{launch_number:03d}.stderr.log'
            stdout=stdout_path.open('wb');stderr=stderr_path.open('wb');logs=[stdout,stderr];launch_number+=1
            process=subprocess.Popen(command,cwd=ROOT,stdout=stdout,stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            pid=process.pid;healing_started=True
            journal.append({'at':now(),'event':'started','pid':pid,'attempt':restarts,'command':command,
                            'stdout_log':stdout_path.name,'stderr_log':stderr_path.name})
            print('SUPERVISOR STARTED',pid,'attempt',restarts,flush=True)
        is_alive=process.poll() is None if process else alive(pid,adopted_handle)
        state=read(out/'progress.json') if (out/'progress.json').exists() else {}
        music_request=out/'scene-music.request.json'
        music_receipt=out/'scene-music.queue-receipt.json'
        music_pending=music_request.exists() and (not music_receipt.exists() or
            read(music_receipt).get('request_sha256')!=file_digest(music_request))
        if is_alive and music_pending and (out/'scene-music-v2/manifest.json').exists() and not audio_busy(pid):
            request=read(music_request)
            if request.get('generation')!=args.output_name:raise AppError('Music request belongs to another generation')
            write_json(out/'scene-music-requests'/(file_digest(music_request)+'.json'),request)
            target=process or SimpleNamespace(pid=pid,poll=lambda:None if alive(pid,adopted_handle) else 0,kill=lambda:None)
            terminate_process_tree(target)
            cooperative=True
            journal.append({'at':now(),'event':'music_checkpoint','pid':pid})
            result='complete';music_process=None
            try:
                music_process=subprocess.Popen([sys.executable,'-X','utf8','-u',str(ROOT/'scripts/build_higgs_scene_music.py'),
                    '--generation',str(out),'--run'],cwd=ROOT,
                    stdout=(out/('music-run-'+file_digest(music_request)[:12]+'.stdout.log')).open('ab'),
                    stderr=(out/('music-run-'+file_digest(music_request)[:12]+'.stderr.log')).open('ab'),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                started=time.monotonic()
                while music_process.poll() is None:
                    write_json(out/'supervision.json',{'updated':now(),'supervisor_pid':os.getpid(),
                        'worker_pid':pid,'worker_alive':False,'music_pid':music_process.pid,
                        'status':'scene_music_rendering','restarts':restarts,'journal':journal})
                    current=read(PAGE/'progress.json')
                    if current.get('generation')==args.output_name:
                        receipts=out/'scene-music-v2/receipts.json'
                        verified=len(read(receipts)['takes']) if receipts.exists() else 0
                        current['background_job']={'kind':'scene_music','status':'rendering','verified_takes':verified,
                            'planned_takes':len(read(out/'scene-music-v2/manifest.json')['layout']['takes'])}
                        write_json(PAGE/'progress.json',current);page()
                    if time.monotonic()-started>7200:
                        terminate_process_tree(music_process);raise subprocess.TimeoutExpired(music_process.args,7200)
                    time.sleep(args.poll_seconds)
                if music_process.returncode:raise subprocess.CalledProcessError(music_process.returncode,music_process.args)
            except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
                result='failed';journal.append({'at':now(),'event':'music_failed','error':str(exc)})
                if music_process and music_process.poll() is None:terminate_process_tree(music_process)
            write_json(music_receipt,{'request_sha256':file_digest(music_request),'result':result,'updated':now()})
            is_alive=False;state=read(out/'progress.json');music_versions={}
        experiment=out/'comma-comparison.request.json'
        experiment_receipt=out/'comma-comparison.queue-receipt.json'
        experiment_pending=experiment.exists() and (not experiment_receipt.exists() or
            read(experiment_receipt).get('request_sha256')!=file_digest(experiment))
        if is_alive and experiment_pending and not audio_busy(pid):
            request=read(experiment)
            if request.get('generation')!=args.output_name:raise AppError('Comparison belongs to another generation')
            experiment_out=Path(request['folder']).resolve()
            if not experiment_out.is_relative_to(args.folder.resolve()):raise AppError('Comparison output outside book workspace')
            target=process or SimpleNamespace(pid=pid,poll=lambda:None if alive(pid,adopted_handle) else 0,kill=lambda:None)
            terminate_process_tree(target)
            cooperative=True
            journal.append({'at':now(),'event':'comparison_checkpoint','pid':pid})
            result='complete'
            comparison=None
            try:
                comparison=subprocess.Popen([sys.executable,'-X','utf8','-u',str(ROOT/'scripts/compare_higgs_commas.py'),
                    '--folder',str(experiment_out),'--run'],cwd=ROOT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                started=time.monotonic()
                while comparison.poll() is None:
                    write_json(out/'supervision.json',{'updated':now(),'supervisor_pid':os.getpid(),
                        'worker_pid':pid,'worker_alive':False,'status':'comparison_running',
                        'comparison_pid':comparison.pid,'restarts':restarts,'journal':journal})
                    if time.monotonic()-started>1800:
                        terminate_process_tree(comparison)
                        raise subprocess.TimeoutExpired(comparison.args,1800)
                    time.sleep(args.poll_seconds)
                if comparison.returncode:raise subprocess.CalledProcessError(comparison.returncode,comparison.args)
            except (OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
                result='failed';journal.append({'at':now(),'event':'comparison_failed','error':str(exc)})
                if comparison and comparison.poll() is None:terminate_process_tree(comparison)
            write_json(experiment_receipt,{'request_sha256':file_digest(experiment),'result':result,'updated':now()})
            # Resume the chapter independently of the audition's outcome.
            is_alive=False;state=read(out/'progress.json')
        pronunciation=out/'pronunciation.request.json'
        pronunciation_receipt=out/'pronunciation.queue-receipt.json'
        pronunciation_pending=pronunciation.exists() and (not pronunciation_receipt.exists() or
            read(pronunciation_receipt).get('request_sha256')!=file_digest(pronunciation) or
            read(pronunciation_receipt).get('implementation_sha256')!=file_digest(ROOT/'scripts/revise_higgs_spoken_take.py'))
        if is_alive and pronunciation_pending and not audio_busy(pid):
            request=read(pronunciation)
            if request.get('generation')!=args.output_name:raise AppError('Pronunciation request belongs to another generation')
            target=process or SimpleNamespace(pid=pid,poll=lambda:None if alive(pid,adopted_handle) else 0,kill=lambda:None)
            terminate_process_tree(target)
            cooperative=True
            journal.append({'at':now(),'event':'pronunciation_checkpoint','pid':pid})
            result='applied'
            try:
                subprocess.run([sys.executable,'-X','utf8','-u',str(ROOT/'scripts/revise_higgs_spoken_take.py'),
                    '--folder',str(out),'--request',str(pronunciation)],cwd=ROOT,check=True)
            except subprocess.CalledProcessError as exc:
                result='held'
                journal.append({'at':now(),'event':'pronunciation_held','error':str(exc)})
            write_json(pronunciation_receipt,{'request_sha256':file_digest(pronunciation),'result':result,
                'implementation_sha256':file_digest(ROOT/'scripts/revise_higgs_spoken_take.py'),'updated':now()})
            is_alive=False;state=read(out/'progress.json')
        reload_request=out/'reload.request.json'
        if reload_request.exists() and read(reload_request).get('generation')==args.output_name and read(reload_request).get('worker_pid')!=pid:
            requested=read(reload_request)
            write_json(out/'reload-history'/(file_digest(reload_request)+'.json'),requested)
            applied=out/'reload.applied.json'
            if applied.exists():write_json(out/'reload-history'/(file_digest(applied)+'.json'),read(applied))
            reload_request.replace(applied)
            journal.append({'at':now(),'event':'reload_satisfied_by_restarted_worker','old_worker':requested.get('worker_pid'),'worker':pid})
        if is_alive and reload_request.exists() and not audio_busy(pid,allow_audit_checkpoint=True):
            requested=read(reload_request)
            if requested.get('generation')!=args.output_name or requested.get('worker_pid')!=pid:
                raise AppError('Reload request belongs to another worker')
            write_json(out/'reload-history'/(file_digest(reload_request)+'.json'),requested)
            applied=out/'reload.applied.json'
            if applied.exists():write_json(out/'reload-history'/(file_digest(applied)+'.json'),read(applied))
            target=process or SimpleNamespace(pid=pid,poll=lambda:None if alive(pid,adopted_handle) else 0,kill=lambda:None)
            terminate_process_tree(target)
            cooperative=True
            journal.append({'at':now(),'event':'checkpoint_reload','pid':pid,'reason':requested['reason']})
            reload_request.replace(applied)
            is_alive=False
        active=out/state.get('active_chapter','')/f"chunk-{state.get('active_chunk',0):03d}"
        checkpoints=[*active.glob('**/receipts.json'),*active.glob('**/word-checks*.json'),*active.glob('**/report.json')]
        import psutil
        owned_activity=[]
        try:children=psutil.Process(pid).children(recursive=True)
        except psutil.NoSuchProcess:children=[]
        for child in children:
            try:
                if child.name().lower()=='audiocpp_cli.exe' or any('audit_higgs' in part for part in child.cmdline()):
                    cpu=child.cpu_times();io=child.io_counters()
                    owned_activity.append((child.pid,round(cpu.user+cpu.system,3),io.read_bytes,io.write_bytes))
            except (psutil.NoSuchProcess,psutil.AccessDenied):continue
        activity=checkpoint_activity([out/'progress.json',*sorted(out.glob('*.stdout.log')),*checkpoints])+tuple(owned_activity)
        if activity!=activity_signature:activity_signature=activity;last_activity=time.monotonic()
        if is_alive and time.monotonic()-last_activity>2700:
            journal.append({'at':now(),'event':'stalled','pid':pid,'quiet_seconds':2700})
            target=process or SimpleNamespace(pid=pid,poll=lambda:None if alive(pid,adopted_handle) else 0,
                kill=lambda:None)
            terminate_process_tree(target)
            is_alive=False;state=read(out/'progress.json')
            state.update(status='failed',error='Supervised worker stalled for 45 minutes')
            write_json(out/'progress.json',state)
        # Publish the current state with music. Never overwrite the renderer's
        # private checkpoint from this concurrent watcher.
        public=read(PAGE/'progress.json') if (PAGE/'progress.json').exists() else {}
        if public.get('generation')==args.output_name:
            for cid,row in state.get('chapters',{}).items():
                evidence=out/'scene-music-v2/receipts.json'
                signature=(row['wav_sha256'],row['complete'],file_digest(evidence) if evidence.exists() else None)
                if music_versions.get(cid)!=signature:
                    try:
                        if not refresh_music(out,cid,state):continue
                        music_versions[cid]=signature
                        music_cache[cid]={k:row[k] for k in MUSIC_FIELDS if k in row}
                        music_cache['_settings']=state['music']
                    except Exception as exc:
                        journal.append({'at':now(),'event':'music_pending','chapter':cid,'error':str(exc)})
                elif cid in music_cache:
                    copy_music_fields(row,music_cache[cid]);state['music']=music_cache['_settings']
            # Avoid replacing a newer prefix if publication raced the mix.
            publish_supervision(out,state,is_alive,restarts)
        heartbeat={'updated':now(),'supervisor_pid':os.getpid(),'worker_pid':pid,'worker_alive':is_alive,
                   'restarts':restarts,'generation_status':state.get('status'),'journal':journal}
        try:heartbeat['worker_created']=psutil.Process(pid).create_time()
        except psutil.NoSuchProcess:heartbeat['worker_created']=None
        write_json(out/'supervision.json',heartbeat)
        if is_alive:
            time.sleep(args.poll_seconds);continue
        for log in logs:log.close()
        logs=[]
        journal.append({'at':now(),'event':'exited','pid':pid,'status':state.get('status'),'error':state.get('error')})
        if cooperative:
            journal.append({'at':now(),'event':'resume_after_checkpoint','pid':pid})
            pid=None;process=None;last_activity=time.monotonic();activity_signature=None
            continue
        if state.get('status')=='complete' and (healing_started or not any(c.get('word_flags') for c in state.get('chapters',{}).values())):
            heartbeat.update(worker_alive=False,status='completed_with_flags' if any(c.get('word_flags') for c in state['chapters'].values()) else 'completed',journal=journal)
            write_json(out/'supervision.json',heartbeat)
            lock.close()
            print('SUPERVISOR FINISHED',heartbeat['status'],flush=True);return
        if state.get('status')=='held' and healing_started:
            heartbeat.update(status='sections_held_after_bounded_recovery',journal=journal)
            write_json(out/'supervision.json',heartbeat);lock.close();return
        if restarts>=args.max_restarts or (state.get('status')=='failed' and not retryable(state.get('error',''))):
            heartbeat.update(status='needs_attention',journal=journal)
            write_json(out/'supervision.json',heartbeat);lock.close();return
        restarts+=1;pid=None;process=None;last_activity=time.monotonic();activity_signature=None
        time.sleep(min(30,5*restarts))

if __name__=='__main__':main()
