"""Durable DEV ownership of a frozen one-shot debug render, without audio retries.

Run with the ASR/preparation Python environment (no recognizers are called).
Only the private adapter's render stage is launched. Its physical-start guards
remain authoritative: controller resumes never authorize another take.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]
from rimsky.util import digest

SELF = Path(__file__).resolve()
ADAPTER = ROOT / 'scripts/debug_higgs_one_shot.py'
BRIDGE = ROOT / 'scripts/sync_higgs_preview_runtime.py'
POLICY = 'development-debug-one-shot-supervision-v1'
UNCHECKED = dict(post_processing='off', recognition_status='not_checked', machine_verified=False,
                 human_approval=False, production_approved=False)
HIDDEN = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def read(path):
    for attempt in range(5):
        try:
            return json.loads(Path(path).read_text('utf-8'))
        except (PermissionError, json.JSONDecodeError):
            if attempt == 4: raise
            time.sleep(.05 * (attempt + 1))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value, *, immutable=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        if read(path) != value:
            raise ValueError('Frozen supervision input changed: ' + str(path))
        return
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def ownership(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'owner.lock').open('a+b') as stream:
        stream.write(b'0'); stream.flush(); stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def identity(process):
    return {'pid': process.pid, 'create_time': process.create_time(), 'command': process.cmdline()}


def live(saved):
    """A reused PID is never an owned worker."""
    if not saved:
        return False
    import psutil
    try:
        process = psutil.Process(saved['pid'])
        return (process.is_running() and process.status() != psutil.STATUS_ZOMBIE
                and abs(process.create_time() - saved['create_time']) < .01
                and process.cmdline() == saved['command'])
    except psutil.NoSuchProcess:
        return False


def native_workers(output):
    result = []
    paths = [*(output / 'native').glob('*/worker.json'), *(output / 'sessions').glob('*/worker.json')]
    for path in paths:
        saved = read(path)
        if live(saved):
            result.append(saved)
    return result


def demo_gate(outputs):
    """Do not open a live worker JSON until its publication is complete."""
    proofs = []
    for output in outputs:
        latest = output / 'public/latest.json'
        if not latest.exists():
            return False, 'Demo publication not available: ' + str(output), []
        value = read(latest)
        total = value.get('total_units')
        if (value.get('render_complete') is not True or type(total) is not int or total <= 0
                or value.get('rendered_units') != total):
            return False, 'Demo queue still rendering: ' + str(output), []
        worker = output / 'worker.json'
        if not worker.exists():
            return False, 'Completed demo has no stopped-worker record: ' + str(output), []
        status = read(worker)
        if status.get('status', status.get('stage')) != 'stopped':
            return False, 'Completed demo worker not stopped: ' + str(output), []
        if 'pid' in status:
            if 'create_time' not in status or 'command' not in status or live(status):
                return False, 'Demo worker is live or lacks an identity: ' + str(output), []
        if native_workers(output):
            return False, 'Demo native worker still running: ' + str(output), []
        proof = {'output': str(output), 'latest_sha256': sha(latest), 'worker_sha256': sha(worker),
                 'generation': value.get('generation'), 'version': value.get('version')}
        config_path = output / 'one-shot.json'
        if config_path.exists():
            config = read(config_path)
            if (digest(config) != value.get('generation') or config.get('source_generation') != value.get('source_generation')
                    or len(config['frozen']['assignment']['units']) != total):
                raise ValueError('Completed demo publication differs from its frozen config')
            proof.update(config_sha256=sha(config_path), source_generation=config['source_generation'])
        proofs.append(proof)
    return True, '', proofs


def checked(path, checksum):
    if sha(path) != checksum:
        raise ValueError('Frozen input checksum changed: ' + str(path))
    return path


def inside(folder, relative):
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError('Prepared asset leaves generation')
    return path


def load_units(output, ready):
    if ready.get('policy') != 'development-private-higgs-one-shot-v1':
        raise ValueError('Only the frozen private one-shot adapter is supported')
    if any(type(ready.get(k)) is not type(v) or ready.get(k) != v for k, v in UNCHECKED.items()):
        raise ValueError('Preparation must explicitly preserve unchecked one-shot mode')
    units = []
    for record in ready['plans']:
        checked(inside(output, record['assignment']), record['sha256'])
        plan = checked(inside(output, record['plan']), record['plan_sha256'])
        units.extend(read(plan)['units'])
    ids = [u['id'] for u in units]
    if (not units or len(ids) != len(set(ids)) or len(units) != ready['unit_count']
            or any(not isinstance(uid, str) or Path(uid).name != uid or '/' in uid or '\\' in uid for uid in ids)
            or {c: sum(u['chapter_id'] == c for u in units) for c in ready['chapters']} != ready['chapter_counts']):
        raise ValueError('Prepared unit/chapter coverage changed')
    return units


def assess(output, ready, units, *, hash_audio=False):
    """Technical completeness only; never performs an acoustic check."""
    progress = read(output / 'progress.json') if (output / 'progress.json').exists() else {}
    if progress and (progress.get('generation') != output.name or any(
            type(progress.get(k)) is not type(v) or progress.get(k) != v for k, v in UNCHECKED.items())):
        raise ValueError('Publication changed generation or unchecked mode')
    rendered, failures, missing = [], [], []
    ending_required = False
    if 'manifest.json' in ready.get('music_files', {}):
        music_manifest = checked(output / 'scene-music-v2/manifest.json', ready['music_files']['manifest.json'])
        ending_required = any(row.get('kind') == 'ending' for row in read(music_manifest)['layout']['takes'])
    for unit in units:
        path = output / 'units' / (unit['id'] + '.json')
        if not path.exists():
            missing.append(unit['id']); continue
        receipt = read(path)
        if receipt.get('unit') != unit['id'] or receipt.get('take') != 0:
            raise ValueError('First-take receipt binding changed')
        if any(type(receipt.get(k)) is not type(v) or receipt.get(k) != v for k, v in UNCHECKED.items()):
            raise ValueError('First-take receipt changed its unchecked mode')
        if receipt.get('status') != 'rendered':
            failures.append(unit['id']); continue
        if unit['synthesis']['backend'] != 'nonverbal_asset' and receipt.get('synthesis_sha256') != digest(unit['synthesis']):
            raise ValueError('Native first take differs from frozen input')
        if hash_audio:
            checked(inside(output, receipt['audio']), receipt['audio_sha256'])
        rendered.append(unit['id'])
    pending_music, incomplete = [], []
    for cid in ready['chapters']:
        row = progress.get('chapters', {}).get(cid, {})
        if row.get('complete') is not True or row.get('units') != ready['chapter_counts'][cid]:
            incomplete.append(cid)
        music = row.get('music')
        if (row.get('music_pending') or not isinstance(music, list) or not any(x.get('kind') == 'scene_bed' for x in music)
                or ending_required and cid == ready['chapters'][-1] and not any(x.get('kind') == 'ending' for x in music)):
            pending_music.append(cid)
        elif hash_audio:
            # Use the existing bridge's source/format/hash music validation;
            # this is cached-asset integrity, not a new vocals/ASR check.
            from sync_higgs_preview_runtime import _music_publications
            _music_publications(ROOT, cid, row)
    complete = not (missing or failures or incomplete or pending_music)
    return {'complete': complete, 'rendered_units': len(rendered), 'total_units': len(units),
            'missing_units': missing, 'technical_failures': failures,
            'incomplete_chapters': incomplete, 'music_pending': pending_music}


def normalize_publication(output, assessment, *, terminal=False):
    """Only called after the adapter/native workers stop; no concurrent writer."""
    path = output / 'progress.json'
    if not path.exists():
        return
    progress = read(path)
    progress.update(UNCHECKED)
    if assessment['complete']:
        progress['status'] = 'complete'
    else:
        progress['status'] = 'technical_failure' if terminal else 'rendering'
    progress['supervision'] = {**assessment, 'policy': POLICY, **UNCHECKED}
    write(path, progress)


def activity(output):
    """Actual native/log/checkpoint movement, excluding heartbeat-only files."""
    result = []
    paths = [output / 'progress.json', *(output / 'units').glob('*.json')]
    for folder in (output / 'native').glob('*'):
        paths.extend(folder.glob('*.wav')); paths.extend(folder.glob('*.log'))
        paths.extend(folder.glob('physical-*.json'))
    for path in paths:
        try:
            stat = path.stat(); result.append((str(path), stat.st_size, stat.st_mtime_ns))
        except FileNotFoundError:
            pass
    return digest(sorted(result))


def physical_inventory(output):
    return {p.relative_to(output).as_posix(): sha(p) for p in (output / 'native').glob('*/physical-start.json')}


def find_adapter(command):
    import psutil
    found = []
    for process in psutil.process_iter(['cmdline']):
        try:
            if process.info['cmdline'] == command:
                found.append(process)
        except psutil.NoSuchProcess:
            pass
    if len(found) > 1:
        # Windows venv python.exe can be a launcher parent of the core Python
        # process with the identical argv. Accept that one ancestry chain only.
        ids = {p.pid for p in found}
        leaves = [p for p in found if not any(p.pid in {a.pid for a in q.parents()} for q in found if q.pid != p.pid)]
        if len(leaves) != 1 or not ids - {leaves[0].pid} <= {p.pid for p in leaves[0].parents()}:
            raise ValueError('Multiple unrelated adapter workers; refusing duplicate ownership')
        return identity(leaves[0])
    return identity(found[0]) if found else None


def command_for(args, ready):
    return [sys.executable, '-X', 'utf8', '-u', str(ADAPTER), '--source-generation', ready['source'],
            '--output', str(args.output), '--chapters', *ready['chapters'], '--stage', 'render',
            '--data-dir', str(args.data_dir), '--batch-size', str(args.batch_size), '--timeout', str(args.session_timeout)]


def synchronize(args):
    from sync_higgs_preview_runtime import sync_preview
    return sync_preview(workspace=ROOT, source=args.source,
                        prepared_book=args.output.parent / 'source/book.json',
                        generation_dir=args.output, data_dir=args.data_dir)


def run(args):
    folder = args.output / 'one-shot-supervision'
    with ownership(folder):
        state_path = folder / 'status.json'
        state = read(state_path) if state_path.exists() else {'launches': [], 'events': []}
        config_path = folder / 'config.json'
        process = None
        last_activity, signature = time.monotonic(), None

        def heartbeat(status, **fields):
            state.update(status=status, supervisor_pid=os.getpid(), updated_unix=time.time(), **UNCHECKED, **fields)
            write(state_path, state)

        while True:
            try:
                if not (args.output / 'ready.json').exists():
                    heartbeat('waiting_for_preparation'); time.sleep(args.poll_seconds); continue
                allowed, reason, demos = demo_gate(args.demo_output)
                if not allowed:
                    heartbeat('waiting_for_demo_queue', reason=reason); time.sleep(args.poll_seconds); continue
                ready = read(args.output / 'ready.json')
                checked(ADAPTER, ready['implementation_sha256'])
                units = load_units(args.output, ready)
                command = command_for(args, ready)
                config = {'policy': POLICY, 'ready_sha256': sha(args.output / 'ready.json'),
                          'adapter_sha256': sha(ADAPTER), 'bridge_sha256': sha(BRIDGE), 'supervisor_sha256': sha(SELF),
                          'command': command, 'demo_gate': demos, 'max_restarts': args.max_restarts,
                          'stall_seconds': args.stall_seconds, 'source': str(args.source), **UNCHECKED}
                write(config_path, config, immutable=True)
                active = find_adapter(command)
                natives = native_workers(args.output)
                current_signature = activity(args.output)
                if signature != current_signature:
                    signature, last_activity = current_signature, time.monotonic()
                assessment = assess(args.output, ready, units)
                if active or natives:
                    heartbeat('running' if active else 'waiting_for_owned_native_exit', worker=active,
                              native_workers=natives, progress=assessment,
                              activity_sha256=signature, seconds_since_progress=time.monotonic() - last_activity,
                              stalled=time.monotonic() - last_activity > args.stall_seconds)
                    # Never kill/restart an in-flight first take just because
                    # its output is quiet. The frozen adapter owns its timeout.
                    time.sleep(args.poll_seconds); continue
                if process is not None:
                    state['launches'][-1]['returncode'] = process.wait()
                    process = None
                if state['launches'] and state['launches'][-1].get('cached_publish_only'):
                    if physical_inventory(args.output) != state['launches'][-1]['physical_before']:
                        raise ValueError('Cached publication unexpectedly reserved another native session')
                assessment = assess(args.output, ready, units, hash_audio=True)
                normalize_publication(args.output, assessment)
                if (args.output / 'progress.json').exists():
                    synchronize(args)
                if assessment['complete']:
                    heartbeat('complete', worker=None, native_workers=[], progress=assessment,
                              technical_render_complete=True, music_registered=True)
                    return 0
                if assessment['technical_failures'] and not assessment['missing_units']:
                    normalize_publication(args.output, assessment, terminal=True)
                    if (args.output / 'progress.json').exists(): synchronize(args)
                    heartbeat('held', reason='Immutable first-take technical failures; no audio retry', progress=assessment,
                              worker=None, native_workers=[], technical_render_complete=False)
                    return 1
                if len(state['launches']) >= args.max_restarts + 1:
                    normalize_publication(args.output, assessment, terminal=True)
                    if (args.output / 'progress.json').exists(): synchronize(args)
                    heartbeat('held', reason='Bounded technical resumes exhausted', progress=assessment,
                              worker=None, native_workers=[], technical_render_complete=False)
                    return 1
                # Recheck queue immediately before each physical adapter launch.
                # When units already exist, render only republishes cached audio/music.
                cached_only = not assessment['missing_units']
                attempt = len(state['launches']) + 1
                entry = {'attempt': attempt, 'started_unix': time.time(), 'command': command,
                         'cached_publish_only': cached_only, 'physical_before': physical_inventory(args.output),
                         'log': f'adapter-{attempt:02d}.log'}
                state['launches'].append(entry)
                heartbeat('launching', progress=assessment)
                with (folder / entry['log']).open('ab') as log:
                    process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                               env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}, **HIDDEN)
                import psutil
                entry['identity'] = identity(psutil.Process(process.pid))
                heartbeat('running', worker=entry['identity'], progress=assessment)
                time.sleep(args.poll_seconds)
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
                # Fail closed without terminating an owned physical take. A
                # corrected operational invocation can adopt the exact worker.
                heartbeat('blocked', reason=f'{type(error).__name__}: {error}', worker=state.get('worker'))
                return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=ROOT / 'content/04 Элирм IV.fb2')
    parser.add_argument('--demo-output', type=Path, action='append', required=True)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--session-timeout', type=int, default=7200)
    parser.add_argument('--poll-seconds', type=float, default=5)
    parser.add_argument('--max-restarts', type=int, default=3)
    parser.add_argument('--stall-seconds', type=int, default=8100)
    args = parser.parse_args()
    if (not 1 <= args.batch_size <= 64 or not 60 <= args.session_timeout <= 7200
            or not .25 <= args.poll_seconds <= 60 or not 0 <= args.max_restarts <= 5
            or args.stall_seconds < args.session_timeout):
        parser.error('Invalid bounded batch, timeout, polling or restart limit')
    args.output, args.data_dir, args.source = args.output.resolve(), args.data_dir.resolve(), args.source.resolve()
    args.demo_output = [p.resolve() for p in args.demo_output]
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
