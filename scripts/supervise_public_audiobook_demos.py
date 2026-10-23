"""Persistently supervise a sequential queue of isolated audiobook demo renders.

Never changes the renderer's assignments, quality gates, or take limits. Can
adopt an existing --stage all Python supervisor after validating its exact folder
and process creation time. Heartbeats distinguish active, stopped, and held work.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / 'scripts/render_public_audiobook_demo.py'
HIDDEN = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', 'utf-8')
    os.replace(temporary, path)


def flag_value(command, flag, default=None):
    try:
        return command[command.index(flag)+1]
    except (ValueError, IndexError):
        return default


def is_render_command(command, folder, cwd):
    if not command:
        return False
    scripts = [Path(part) if Path(part).is_absolute() else Path(cwd) / part
               for part in command[1:] if Path(part).name.casefold() == RENDERER.name.casefold()]
    if not any(path.resolve() == RENDERER.resolve() for path in scripts):
        return False
    if flag_value(command, '--stage', 'all') != 'all':
        return False
    value = flag_value(command, '--folder')
    if not value:
        return False
    target = Path(value)
    if not target.is_absolute():
        target = Path(cwd) / target
    return target.resolve() == Path(folder).resolve()


def validate_adoption(process, folder, expected_created=None):
    if not process.name().casefold().startswith(('python', 'pypy')):
        raise ValueError('Only the Python --stage all supervisor may be adopted; never a native child')
    created = process.create_time()
    if expected_created is not None and abs(created-expected_created) > .01:
        raise ValueError('Adopted PID creation time changed')
    if not is_render_command(process.cmdline(), folder, process.cwd()):
        raise ValueError('Adopted process is not the exact requested folder --stage all renderer')
    if not process.is_running():
        raise ValueError('Adopted supervisor is already stopped')
    return {'pid': process.pid, 'create_time': created, 'command': process.cmdline(), 'folder': str(Path(folder).resolve())}


def live(process, identity):
    import psutil
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE and abs(process.create_time()-identity['create_time']) <= .01
    except psutil.Error:
        return False


def tree(process, identity):
    import psutil
    if not live(process, identity):
        return []
    result = []
    try:
        members = [process, *process.children(recursive=True)]
    except psutil.Error:
        return []
    for member in members:
        try:
            result.append({'pid': member.pid, 'create_time': member.create_time(), 'name': member.name(),
                           'running': member.is_running(), 'command': member.cmdline()})
        except psutil.Error:
            continue
    return result


def existing_renderer(folder):
    """A restarted supervisor adopts the existing queue worker instead of duplicating it."""
    import psutil
    matches = []
    for candidate in psutil.process_iter():
        try:
            identity = validate_adoption(candidate, folder)
            matches.append((candidate, identity, candidate.ppid()))
        except (psutil.Error, ValueError):
            continue
    pids = {row[0].pid for row in matches}
    roots = [row for row in matches if row[2] not in pids]
    if len(roots) > 1:
        raise ValueError('Multiple independent workers already render this folder; refusing to start another')
    return (roots[0][0], roots[0][1]) if roots else None


def stop_owned_tree(process, identity, descendants):
    """Terminate only this adopted/launched process tree, guarding PID reuse."""
    import psutil
    members = []
    for item in reversed(descendants):
        try:
            member = psutil.Process(item['pid'])
            if abs(member.create_time()-item['create_time']) <= .01:
                members.append(member)
        except psutil.Error:
            continue
    if live(process, identity) and all(p.pid != process.pid for p in members):
        members.append(process)
    for member in members:
        try:
            member.terminate()
        except psutil.Error:
            pass
    _, survivors = psutil.wait_procs(members, timeout=10)
    for member in survivors:
        try:
            member.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(survivors, timeout=10)


def progress(folder, extra_logs=()):
    folder = Path(folder)
    state = {}
    try:
        state = read(folder / 'state.json')
    except (OSError, ValueError):
        pass
    native = [p for p in (folder / 'native').rglob('*.wav') if p.is_file()]
    logs = list((folder / 'requests').glob('*.log')) + [Path(p) for p in extra_logs]
    def stats(paths):
        rows = []
        for path in paths:
            try:
                stat = path.stat()
                rows.append({'path': str(path), 'bytes': stat.st_size, 'modified_ns': stat.st_mtime_ns})
            except OSError:
                pass
        return rows
    waves, log_stats = stats(native), stats(logs)
    return {'selected_counts': state.get('counts', {}), 'units': state.get('units', []),
            'native_wav_count': len(waves), 'native_wav_bytes': sum(p['bytes'] for p in waves),
            'native_latest_modified_ns': max((p['modified_ns'] for p in waves), default=0),
            'take_receipts': len(list((folder / 'takes').rglob('take-*.json'))), 'logs': log_stats}


def readiness(folder):
    try:
        assignment, manifest = read(folder / 'assignment.json'), read(folder / 'manifest.json')
        if not assignment.get('units') or not manifest.get('opening') or not all(manifest.get('music', {}).get(k) for k in ('bed', 'ending')):
            return False, 'Waiting for frozen assignment, approved opening and requested music'
        if manifest.get('assignment_sha256') and file_hash(folder / 'assignment.json') != manifest['assignment_sha256']:
            return False, 'Assignment hash differs from manifest'
        return True, None
    except (OSError, ValueError, KeyError) as error:
        return False, str(error)


def completion(folder, process_exited):
    """Do not confuse exit zero, playable preview, or all-verified state with completion."""
    if not process_exited:
        return False, 'Worker is still running'
    ready, reason = readiness(folder)
    if not ready:
        return False, reason
    try:
        assignment, state = read(folder / 'assignment.json'), read(folder / 'state.json')
        proof, frozen = read(folder / 'complete.json'), read(folder / 'frozen.json')
        expected = [u['id'] for u in assignment['units']]
        if len(set(expected)) != len(expected) or not expected:
            return False, 'Invalid assignment identities'
        if frozen['assignment'] != assignment or frozen['manifest'] != read(folder / 'manifest.json'):
            return False, 'Frozen assignment/manifest changed'
        generation = digest(frozen)
        if state.get('generation') != generation or proof.get('generation') != generation:
            return False, 'Completion generation differs from frozen inputs'
        units = state.get('units', [])
        if ([u['id'] for u in units] != expected or any(u.get('status') != 'verified' or not u.get('selected_audio') for u in units)
                or state.get('counts', {}).get('verified') != len(expected)
                or any(count for name, count in state.get('counts', {}).items() if name != 'verified')):
            return False, 'Not every requested unit is verified'
        if (proof.get('complete') is not True or proof.get('music_ready') is not True
                or proof.get('verified_units') != len(expected) or proof.get('total_units') != len(expected)
                or proof.get('open_recognition_flags') != 0 or proof.get('opening_seconds') != 8):
            return False, 'Completion lacks all requested audio, music, opening or zero open flags'
        if [u['id'] for u in proof.get('captions', [])] != expected:
            return False, 'Published source coverage differs'
        for kind in ('wav', 'mp3'):
            filename = proof[kind]
            if Path(filename).name != filename or file_hash(folder / 'public' / filename) != proof[kind+'_sha256']:
                return False, 'Published '+kind+' changed or is missing'
        return True, None
    except (OSError, ValueError, KeyError, TypeError) as error:
        return False, str(error)


def held(folder):
    try:
        state = read(folder / 'state.json')
        units = state.get('units', [])
        if any(u.get('status') == 'held' for u in units):
            return True
        return bool(units) and all(u.get('status') == 'verified' for u in units) and not (folder / 'complete.json').exists()
    except (OSError, ValueError):
        return False


def launch(folder, python, output, attempt):
    import psutil
    log = output / ('%02d-%s.log' % (attempt, folder.name))
    command = [str(python), '-X', 'utf8', '-u', str(RENDERER), '--folder', str(folder), '--stage', 'all']
    with log.open('ab') as stream:
        child = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, **HIDDEN)
    process = psutil.Process(child.pid)
    identity = validate_adoption(process, folder)
    return process, identity, log, child


def main():
    import psutil
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--python', type=Path, default=ROOT / 'training-work/.venv-tts/Scripts/python.exe')
    parser.add_argument('--adopt-pid', type=int)
    parser.add_argument('--adopt-create-time', type=float)
    parser.add_argument('--adopt-log', type=Path)
    parser.add_argument('--max-restarts', type=int, default=3)
    parser.add_argument('--stall-seconds', type=float, default=600)
    parser.add_argument('--preparation-deadline-seconds', type=float, default=21600)
    parser.add_argument('--poll-seconds', type=float, default=5)
    args = parser.parse_args()
    if args.max_restarts < 0 or min(args.stall_seconds, args.preparation_deadline_seconds, args.poll_seconds) <= 0:
        parser.error('Timeouts/poll must be positive; restarts nonnegative')
    folders, output = [p.resolve() for p in args.folder], args.output.resolve()
    if len(set(folders)) != len(folders):
        parser.error('Queue folders must be distinct')
    output.mkdir(parents=True, exist_ok=True)
    records = [{'folder': str(folder), 'status': 'queued', 'attempts': []} for folder in folders]
    heartbeat = output / 'supervision.json'

    def save():
        write(heartbeat, {'schema': 1, 'supervisor_pid': os.getpid(), 'updated_unix': time.time(),
                          'status': 'running' if any(r['status'] in ('running', 'preparing', 'queued', 'restarting') for r in records) else
                                    ('complete' if all(r['status'] == 'complete' for r in records) else 'held_or_failed'),
                          'books': records})

    adopted = None
    if args.adopt_pid:
        process = psutil.Process(args.adopt_pid)
        adopted = (process, validate_adoption(process, folders[0], args.adopt_create_time), args.adopt_log, None)
    save()
    for index, folder in enumerate(folders):
        record = records[index]
        record['status'] = 'preparing'
        preparation_started = time.monotonic()
        while not (index == 0 and adopted):
            ready, reason = readiness(folder)
            record.update(reason=reason, progress=progress(folder))
            if ready:
                break
            if time.monotonic()-preparation_started >= args.preparation_deadline_seconds:
                record.update(status='failed', reason='Preparation deadline exceeded: '+str(reason), process_running=False)
                break
            save()
            time.sleep(args.poll_seconds)
        if record['status'] == 'failed':
            save()
            continue
        for attempt in range(args.max_restarts+1):
            if index == 0 and attempt == 0 and adopted:
                process, identity, log, child = adopted
            else:
                try:
                    existing = existing_renderer(folder)
                    if existing:
                        process, identity = existing
                        log, child = None, None
                    else:
                        process, identity, log, child = launch(folder, args.python.resolve(), output, attempt)
                except Exception as error:
                    record['attempts'].append({'attempt': attempt, 'launch_error': str(error), 'finished_unix': time.time()})
                    record.update(status='failed', reason='Could not launch renderer: '+str(error), process_running=False)
                    save()
                    break
            entry = {'attempt': attempt, 'adopted': child is None,
                     'identity': identity, 'log': str(log) if log else None, 'started_unix': time.time()}
            record['attempts'].append(entry)
            record.update(status='running', process_running=True, reason=None)
            last_signature, last_progress = None, time.monotonic()
            descendants = []
            timed_out = False
            while live(process, identity):
                descendants = tree(process, identity)
                snapshot = progress(folder, [log] if log else [])
                signature = digest(snapshot)
                if signature != last_signature:
                    last_signature, last_progress = signature, time.monotonic()
                record.update(progress=snapshot, process_running=True, process_tree=descendants,
                              seconds_since_progress=round(time.monotonic()-last_progress, 1))
                save()
                if time.monotonic()-last_progress >= args.stall_seconds:
                    timed_out = True
                    stop_owned_tree(process, identity, descendants)
                    break
                time.sleep(args.poll_seconds)
            # A vanished supervisor with a still-running native child is failure,
            # never an invitation to start another GPU process concurrently.
            orphaned = []
            for member in descendants:
                if member['pid'] == identity['pid']:
                    continue
                try:
                    candidate = psutil.Process(member['pid'])
                    if live(candidate, member):
                        orphaned.append(member)
                except psutil.Error:
                    pass
            if orphaned:
                stop_owned_tree(process, identity, orphaned)
            try:
                exit_code = child.poll() if child else process.wait(timeout=0)
            except (psutil.Error, subprocess.TimeoutExpired):
                exit_code = None
            entry.update(finished_unix=time.time(), exit_code=exit_code, timed_out=timed_out,
                         orphaned_owned_children=orphaned)
            record.update(process_running=False, process_tree=[], progress=progress(folder, [log] if log else []))
            complete, reason = completion(folder, not live(process, identity))
            if complete:
                record.update(status='complete', reason=None)
                save()
                break
            terminal = bool(record['progress']['units']) and all(u.get('status') in ('held', 'verified') for u in record['progress']['units'])
            if held(folder) and (exit_code == 0 or terminal):
                record.update(status='held', reason='Renderer stopped with held units or incomplete final assets: '+str(reason))
                save()
                break
            if attempt >= args.max_restarts:
                record.update(status='failed', reason='Bounded supervisor restarts exhausted: '+str(reason))
                save()
                break
            record.update(status='restarting', reason='Worker stopped; resuming immutable checkpoints: '+str(reason))
            save()
    save()
    return 0 if all(r['status'] == 'complete' for r in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
