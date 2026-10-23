"""Durable sequential demo finishing: base render, independent checks, bounded recovery.

No quality gates or frozen inputs change. Existing externally supervised base jobs
are observed without taking ownership. Every new child has a creation-time-bound
identity, durable log and stage receipt. Only this orchestrator's own children may
be stopped on a verified timeout. Invocation is explicit; importing starts no work.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]
import supervise_public_audiobook_demos as supervision

SCRIPTS = {name: ROOT / 'scripts' / filename for name, filename in {
    'base': 'render_public_audiobook_demo.py', 'turbo': 'witness_public_demo_turbo.py',
    'resolve': 'resolve_public_demo_recognition.py', 'recovery': 'recover_public_audiobook_demo.py'}.items()}
SELF = Path(__file__).resolve()


def stage_progress(folder, log=None):
    snapshot = supervision.progress(folder, [log] if log else [])
    paths = []
    for directory, pattern in [('checks', '*.json'), ('witnesses', '*.json'), ('takes', '*.json'),
                               ('recovery', '*.json'), ('recovery/sessions', '*.wav')]:
        for path in (folder / directory).rglob(pattern):
            try:
                stat = path.stat()
                paths.append((path.relative_to(folder).as_posix(), stat.st_size, stat.st_mtime_ns))
            except OSError:
                pass
    for path in (folder / 'recovery/sessions').rglob('*.log'):
        try:
            stat = path.stat()
            paths.append((path.relative_to(folder).as_posix(), stat.st_size, stat.st_mtime_ns))
        except OSError:
            pass
    snapshot['finishing_evidence'] = sorted(paths)
    snapshot['verifier_evidence'] = [p for p in paths if p[0].startswith(('checks/', 'witnesses/'))]
    snapshot['waiting_for_resources'] = False
    if log:
        try:
            with Path(log).open('rb') as stream:
                stream.seek(max(0, Path(log).stat().st_size-8192))
                lines = stream.read().decode('utf-8', errors='replace').replace('\x00', '').strip().splitlines()
            snapshot['waiting_for_resources'] = bool(lines and 'WAITING' in lines[-1])
        except OSError:
            pass
    return snapshot


def current_resolution(folder):
    return supervision.read(folder / 'finishing/current-resolution.json')


def base_terminal(folder):
    """A stopped/unknown-exit process is insufficient while source units remain pending."""
    try:
        expected = [u['id'] for u in supervision.read(folder / 'assignment.json')['units']]
        units = supervision.read(folder / 'state.json')['units']
        return bool(expected) and [u['id'] for u in units] == expected and all(u.get('status') in ('verified', 'held') for u in units)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def prefix_seconds(context, report):
    import soundfile as sf
    import render_public_audiobook_demo as renderer
    from rimsky.audio_join import prepare_clip, gap_seconds
    source = {u['id']: u for u in context.assignment['units']}
    seconds, count, previous, previous_edge = 0.0, 0, None, None
    for index, row in enumerate(report['units']):
        chosen = row.get('selected')
        if not chosen:
            break
        unit = source[row['id']]
        receipt = next(r for r in renderer.takes(context.folder, unit) if r['take'] == chosen['take'])
        audio, rate = sf.read(context.folder / receipt['audio'], dtype='float32')
        clip, edge = prepare_clip(audio, rate)
        descriptor = {**unit, 'sentence_index': index, 'scene': unit.get('chapter_id')}
        seconds += len(clip)/rate
        if previous:
            seconds += gap_seconds(previous, descriptor, previous_edge, edge)
        count += 1
        previous, previous_edge = descriptor, edge
    return count, seconds


def validate_completion(folder, context, report, workers_stopped=True):
    """Recomputed resolver report is authority; publication hash may differ from frozen generation."""
    if not workers_stopped:
        return False, 'Synthesis/verification workers have not stopped'
    try:
        expected = [u['id'] for u in context.assignment['units']]
        if ([u['id'] for u in report['units']] != expected or report.get('verified_units') != len(expected)
                or report.get('open_recognition_flags') or report.get('pending_units')
                or any(not u.get('selected') for u in report['units'])):
            return False, 'Recomputed source-bound report has unresolved or missing units'
        proof = supervision.read(folder / 'complete.json')
        state = supervision.read(folder / 'state.json')
        report_hash = supervision.digest(report)
        publication = supervision.digest({'frozen_generation': context.generation, 'recognition_report_sha256': report_hash})
        if (proof.get('frozen_generation') != context.generation or proof.get('generation') != publication
                or proof.get('recognition_report_sha256') != report_hash or proof.get('complete') is not True
                or proof.get('music_ready') is not True or proof.get('opening_seconds') != 8
                or proof.get('total_units') != len(expected) or proof.get('verified_units') != len(expected)
                or proof.get('open_recognition_flags') != 0 or proof.get('pending_recognition_units') != 0):
            return False, 'Published completion differs from the current frozen source/evidence'
        if ([u['id'] for u in state.get('units', [])] != expected
                or any(u.get('status') != 'verified' for u in state.get('units', []))
                or state.get('counts', {}).get('verified') != len(expected)
                or any(v for k, v in state.get('counts', {}).items() if k != 'verified')
                or state.get('generation') != publication):
            return False, 'Published state does not verify every requested source unit'
        captions = proof.get('captions', [])
        if ([c['id'] for c in captions] != expected or
                any(c.get('raw_audio_sha256') != row['selected']['audio_sha256'] for c, row in zip(captions, report['units']))):
            return False, 'Published audio choices differ from recomputed source-bound decisions'
        evidence_name = 'recognition-'+report_hash+'.json'
        if proof.get('recognition_report') != evidence_name or supervision.read(folder / 'public' / evidence_name) != report:
            return False, 'Published raw recognition disclosure is missing or changed'
        for kind in ('wav', 'mp3'):
            filename = proof[kind]
            if Path(filename).name != filename or supervision.file_hash(folder / 'public' / filename) != proof[kind+'_sha256']:
                return False, 'Published '+kind+' is missing or changed'
        context.recheck()
        return True, None
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        return False, str(error)


def resolve_internal(folder, check_complete=False):
    import resolve_public_demo_recognition as resolver
    context = resolver.Context(folder)
    report = resolver.report(context)
    report_path = folder / 'finishing' / ('report-'+resolver.sha(report)+'.json')
    resolver.immutable(report_path, report)
    count, seconds = prefix_seconds(context, report)
    valid, reason = validate_completion(folder, context, report) if check_complete else (False, 'Not checked')
    value = {'report': report, 'report_path': str(report_path), 'report_sha256': resolver.sha(report),
             'prefix_units': count, 'prefix_seconds': seconds,
             'publication_eligible': seconds >= 300 or count == len(context.assignment['units']),
             'completion_valid': valid, 'completion_reason': reason}
    supervision.write(folder / 'finishing/current-resolution.json', value)
    print('SOURCE-BOUND RESOLUTION', json.dumps({k: value[k] for k in
          ('prefix_units', 'prefix_seconds', 'publication_eligible', 'completion_valid', 'completion_reason')}), flush=True)
    print('COUNTS', json.dumps({k: report[k] for k in ('verified_units', 'open_recognition_flags', 'pending_units')}), flush=True)


class StageFailure(RuntimeError):
    pass


class Engine:
    def __init__(self, args):
        self.args = args
        self.output = args.output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.path = self.output / 'finishing-supervision.json'
        config = {'folders': [str(p.resolve()) for p in args.folder], 'python': str(args.python.resolve()),
                  'asr_python': str(args.asr_python.resolve()), 'implementation_sha256': supervision.file_hash(SELF)}
        if self.path.exists():
            self.state = supervision.read(self.path)
            if self.state['config'] != config:
                raise ValueError('Finishing supervisor inputs/implementation changed; use a new output namespace')
        else:
            self.state = {'schema': 1, 'config': config, 'books': {}, 'stages': {}}
        self.state['status'] = 'running'
        self.save()

    def save(self):
        self.state.update(supervisor_pid=__import__('os').getpid(), updated_unix=time.time())
        supervision.write(self.path, self.state)

    def book(self, folder, **fields):
        book = self.state['books'].setdefault(str(folder), {})
        if fields.get('status') in ('preparing', 'running'):
            book.pop('reason', None)
        book.update(fields)
        self.save()

    def command(self, script, folder, extra=(), asr=False):
        return [str(self.args.asr_python if asr else self.args.python), '-X', 'utf8', '-u', str(script),
                '--folder', str(folder), *extra]

    def run(self, name, folder, command, kind='normal', adopt=None):
        import psutil
        key = supervision.digest({'folder': str(folder), 'name': name, 'command': command})
        record = self.state['stages'].setdefault(key, {'name': name, 'folder': str(folder), 'command': command, 'attempts': []})
        # Completed read-only stages may be rerun to incorporate fresh evidence;
        # recovery render commands use explicit attempts and are idempotent.
        active = record['attempts'][-1] if record['attempts'] and record['attempts'][-1].get('status') == 'running' else None
        def start_child():
            stage_log = self.output / (folder.name+'-'+name+'-'+str(len(record['attempts']))+'.log')
            with stage_log.open('ab') as stream:
                stage_child = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, **supervision.HIDDEN)
            stage_process = psutil.Process(stage_child.pid)
            stage_identity = {'pid': stage_process.pid, 'create_time': stage_process.create_time(),
                              'command': command, 'folder': str(folder)}
            return stage_process, stage_identity, stage_log, stage_child
        for retry in range(self.args.stage_retries+1):
            child = None
            if adopt and retry == 0:
                process, identity, log = adopt
                owned = True  # Exact folder/all-stage identity; external owner was checked first.
            elif active:
                try:
                    process = psutil.Process(active['identity']['pid'])
                    identity = active['identity']
                    if not supervision.live(process, identity) or process.cmdline() != identity['command']:
                        raise ValueError('Previous stage process stopped')
                    log, owned = Path(active['log']), active.get('owned', True)
                except (psutil.Error, ValueError):
                    active.update(status='interrupted', finished_unix=time.time())
                    active = None
                    process, identity, log, child = start_child()
                    owned = True
            else:
                process, identity, log, child = start_child()
                owned = True
            entry = active or {'identity': identity, 'log': str(log) if log else None, 'owned': owned,
                               'started_unix': time.time(), 'status': 'running'}
            if active is None:
                record['attempts'].append(entry)
            active = None
            record['status'] = 'running'
            last, last_material, last_progress, started = None, None, time.monotonic(), time.monotonic()
            material_started, descendants, timed_out = False, [], False
            asr_started = None
            while supervision.live(process, identity):
                descendants = supervision.tree(process, identity)
                snapshot = stage_progress(folder, log)
                signature = supervision.digest(snapshot)
                material = supervision.digest(snapshot['verifier_evidence'])
                if last_material is not None and material != last_material:
                    material_started = True
                last_material = material
                if signature != last:
                    last, last_progress = signature, time.monotonic()
                elapsed, idle = time.monotonic()-started, time.monotonic()-last_progress
                record.update(process_running=True, process_tree=descendants, progress=snapshot,
                              seconds_since_progress=round(idle, 1), elapsed_seconds=round(elapsed, 1))
                self.book(folder, stage=name, status='running')
                # Verifier cold starts get up to 30 minutes before first evidence.
                # WAITING resource messages remain real progress and do not
                # consume the verifier's model-initialization allowance.
                asr_phase = kind == 'asr' or any(supervision.flag_value(p.get('command', []), '--stage') in ('verify', 'witness') for p in descendants)
                if asr_phase and asr_started is None:
                    asr_started = time.monotonic()
                elif not asr_phase:
                    asr_started = None
                limit = self.args.verifier_initial_seconds if asr_phase and not material_started else self.args.stall_seconds
                if kind == 'cli':
                    limit = max(limit, 1800)
                if snapshot['waiting_for_resources']:
                    started = time.monotonic()
                    asr_started = time.monotonic() if asr_phase else None
                timeout = idle >= limit or (asr_phase and not material_started and not snapshot['waiting_for_resources']
                                           and asr_started is not None and time.monotonic()-asr_started >= self.args.verifier_initial_seconds)
                if timeout and owned:
                    timed_out = True
                    supervision.stop_owned_tree(process, identity, descendants)
                    break
                time.sleep(self.args.poll_seconds)
            orphans = []
            for item in descendants:
                if item['pid'] == identity['pid']:
                    continue
                try:
                    member = psutil.Process(item['pid'])
                    if supervision.live(member, item):
                        orphans.append(item)
                except psutil.Error:
                    pass
            if orphans and owned:
                supervision.stop_owned_tree(process, identity, orphans)
            elif orphans:
                raise StageFailure('Observed base supervisor exited with live children; retaining external ownership')
            try:
                code = child.wait(timeout=1) if child else process.wait(timeout=0)
            except (psutil.Error, subprocess.TimeoutExpired):
                code = None
            entry.update(status='failed' if timed_out or code not in (0, None) else 'stopped',
                         exit_code=code, timed_out=timed_out, finished_unix=time.time())
            record.update(process_running=False, process_tree=[], status=entry['status'])
            self.save()
            if not timed_out and code in (0, None):
                return
            if not owned:
                raise StageFailure('Observed external base render stopped with exit '+str(code))
        raise StageFailure(name+' failed after bounded stage retries; logs and checkpoints retained')

    def wait_external_base(self, folder):
        import psutil
        path = self.args.wait_supervision or folder.parent / 'queue/supervision.json'
        if not path.exists():
            return False
        while True:
            try:
                state = supervision.read(path)
                row = next((r for r in state.get('books', []) if Path(r['folder']).resolve() == folder), None)
                owner = psutil.Process(state['supervisor_pid'])
                owner_live = owner.is_running() and any(Path(p).name == 'supervise_public_audiobook_demos.py' for p in owner.cmdline()[1:])
            except (OSError, ValueError, KeyError, psutil.Error):
                return False
            if not row or not owner_live:
                return False
            if row.get('status') in ('complete', 'held', 'failed') and not row.get('process_running'):
                if supervision.existing_renderer(folder):
                    raise StageFailure('External supervision says stopped but a base renderer remains live')
                self.book(folder, external_base_supervision=str(path), external_base_status=row['status'], process_running=False)
                return True
            self.book(folder, stage='wait-external-base', status='running', external_base_supervision=str(path),
                      external_base_status=row.get('status'), external_progress=row.get('progress'), process_running=row.get('process_running'))
            time.sleep(self.args.poll_seconds)

    def base(self, folder):
        import psutil
        if self.wait_external_base(folder):
            if base_terminal(folder):
                return
            self.book(folder, reason='External base stopped with unfinished units; resuming within bounded retries')
        adopted = None
        if self.args.adopt_pid and folder == self.args.folder[0].resolve():
            try:
                process = psutil.Process(self.args.adopt_pid)
                identity = supervision.validate_adoption(process, folder, self.args.adopt_create_time)
                adopted = process, identity, self.args.adopt_log
            except psutil.NoSuchProcess:
                pass
        if adopted is None:
            existing = supervision.existing_renderer(folder)
            if existing:
                adopted = existing[0], existing[1], self.args.adopt_log
            else:
                if base_terminal(folder):
                    self.book(folder, base_already_stopped=True, process_running=False)
                    return
        command = self.command(SCRIPTS['base'], folder, ['--stage', 'all', '--batch-size', str(self.args.batch_size)])
        self.run('base-all', folder, command, adopt=adopted)
        if base_terminal(folder):
            return
        # A non-child adoption may have no retrievable exit code. Never infer
        # successful base coverage from that absence; resume a fresh all-stage
        # worker at most stage_retries times, then hold this book explicitly.
        for retry in range(self.args.stage_retries):
            self.book(folder, reason='Stopped base has unfinished units; bounded base resume '+str(retry+1))
            self.run('base-resume-'+str(retry+1), folder, command)
            if base_terminal(folder):
                return
        raise StageFailure('Base stopped without terminal source coverage after bounded resumes; pending units remain held')

    def resolve(self, folder, complete=False):
        extra = ['--internal-stage', 'complete' if complete else 'resolve']
        self.run('validate-complete' if complete else 'resolve', folder, self.command(SELF, folder, extra, asr=True))
        return current_resolution(folder)

    def publish_if_eligible(self, folder, snapshot):
        if snapshot['publication_eligible']:
            self.run('publish', folder, self.command(SCRIPTS['resolve'], folder, ['--publish'], asr=True))


def unresolved_candidates(folder, report):
    """Only exhausted, unresolved units get a new bounded recovery campaign."""
    candidates, held = [], []
    for row in report['units']:
        if row.get('selected'):
            continue
        original = [folder / 'takes' / row['id'] / ('take-%02d.json' % n) for n in range(3)]
        if not all(p.exists() for p in original):
            held.append({'id': row['id'], 'reason': 'original_base_trials_not_exhausted'})
            continue
        recovery = sorted((folder / 'recovery' / row['id'] / 'takes').glob('take-*.json'))
        if len(recovery) >= 3:
            held.append({'id': row['id'], 'reason': 'three_explicit_recovery_takes_exhausted'})
        else:
            candidates.append({'id': row['id'], 'attempt': len(recovery)+1})
    return candidates, held


def finish_book(engine, folder):
    engine.book(folder, status='preparing')
    deadline = time.monotonic()+engine.args.preparation_deadline_seconds
    while True:
        ready, reason = supervision.readiness(folder)
        if ready:
            break
        if time.monotonic() >= deadline:
            raise StageFailure('Preparation deadline exceeded: '+str(reason))
        engine.book(folder, status='preparing', reason=reason)
        time.sleep(engine.args.poll_seconds)
    engine.base(folder)
    # All unresolved original takes are turbo-checked before any campaign pins
    # their failed evidence. Later calls select only fresh recovery candidates.
    engine.run('turbo-original-all-takes', folder,
               engine.command(SCRIPTS['turbo'], folder, ['--stage', 'witness', '--all-takes'], asr=True), kind='asr')
    snapshot = engine.resolve(folder)
    engine.publish_if_eligible(folder, snapshot)
    blocked, campaign_holds = set(), []
    for cycle in range(3):
        report = snapshot['report']
        if report['verified_units'] == len(report['units']) and not report['open_recognition_flags'] and not report['pending_units']:
            break
        candidates, holds = unresolved_candidates(folder, report)
        candidates = [row for row in candidates if row['id'] not in blocked]
        if not candidates:
            break
        ready_candidates = []
        for row in candidates:
            unit_flags = ['--unit', row['id']]
            try:
                for stage in ('plan', 'propose'):
                    # Existing campaigns/proposals are immutable and reused; the
                    # stage never starts another strategy on the same failure.
                    if (folder / 'recovery' / row['id'] / ('campaign.json' if stage == 'plan' else 'proposal.json')).exists():
                        continue
                    engine.run('recovery-'+stage+'-'+row['id'], folder,
                               engine.command(SCRIPTS['recovery'], folder, ['--stage', stage, *unit_flags]),
                               kind='cli' if stage == 'propose' else 'normal')
                ready_candidates.append(row)
            except StageFailure as error:
                blocked.add(row['id'])
                campaign_holds.append({'id': row['id'], 'reason': str(error)})
                engine.book(folder, campaign_holds=campaign_holds)
        candidates = ready_candidates
        if not candidates:
            break
        for attempt in (1, 2, 3):
            for row in candidates:
                if row['attempt'] != attempt:
                    continue
                # The native batch can stop at its first failing request. A
                # single-unit invocation prevents that failure from consuming
                # immutable recovery attempts for units it never attempted.
                try:
                    engine.run('recovery-render-'+str(attempt)+'-'+row['id'], folder,
                               engine.command(SCRIPTS['recovery'], folder,
                                              ['--stage', 'render', '--attempt', str(attempt), '--unit', row['id']]))
                except StageFailure as error:
                    blocked.add(row['id'])
                    campaign_holds.append({'id': row['id'], 'stage': 'render',
                                           'attempt': attempt, 'reason': str(error)})
                    engine.book(folder, campaign_holds=campaign_holds)
        for stage in ('verify', 'witness'):
            engine.run('recovery-'+stage+'-'+str(cycle), folder,
                       engine.command(SCRIPTS['base'], folder, ['--stage', stage], asr=True), kind='asr')
        flags = [part for row in candidates for part in ('--unit', row['id'])]
        engine.run('turbo-recovery-'+str(cycle), folder,
                   engine.command(SCRIPTS['turbo'], folder, ['--stage', 'witness', *flags], asr=True), kind='asr')
        snapshot = engine.resolve(folder)
        engine.publish_if_eligible(folder, snapshot)
    snapshot = engine.resolve(folder, complete=True)
    if snapshot['completion_valid']:
        engine.book(folder, status='complete', process_running=False, report_sha256=snapshot['report_sha256'])
        return True
    _, holds = unresolved_candidates(folder, snapshot['report'])
    engine.book(folder, status='held', process_running=False, reason=snapshot['completion_reason'], holds=holds,
                campaign_holds=campaign_holds,
                report_sha256=snapshot['report_sha256'], counts={k: snapshot['report'][k] for k in
                                                               ('verified_units', 'open_recognition_flags', 'pending_units')})
    return False


def ensure_inherited_utf8():
    """The frozen renderer starts plain Python grandchildren; encoding must inherit."""
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'


def main():
    ensure_inherited_utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--python', type=Path, default=ROOT / 'training-work/.venv-tts/Scripts/python.exe')
    parser.add_argument('--asr-python', type=Path, default=ROOT / 'training-work/.venv-asr/Scripts/python.exe')
    parser.add_argument('--adopt-pid', type=int)
    parser.add_argument('--adopt-create-time', type=float)
    parser.add_argument('--adopt-log', type=Path)
    parser.add_argument('--wait-supervision', type=Path)
    parser.add_argument('--stage-retries', type=int, default=1)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--stall-seconds', type=float, default=600)
    parser.add_argument('--verifier-initial-seconds', type=float, default=1800)
    parser.add_argument('--poll-seconds', type=float, default=5)
    parser.add_argument('--preparation-deadline-seconds', type=float, default=21600)
    parser.add_argument('--internal-stage', choices=['resolve', 'complete'], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.internal_stage:
        resolve_internal(args.folder[0].resolve(), args.internal_stage == 'complete')
        return 0
    if args.output is None:
        parser.error('--output is required for persistent finishing supervision')
    if len(set(p.resolve() for p in args.folder)) != len(args.folder):
        parser.error('Queue folders must be distinct')
    if args.stage_retries < 0 or min(args.stall_seconds, args.verifier_initial_seconds, args.poll_seconds, args.preparation_deadline_seconds) <= 0:
        parser.error('Timeouts/poll must be positive; stage retries nonnegative')
    engine = Engine(args)
    complete = True
    for folder in args.folder:
        folder = folder.resolve()
        try:
            complete = finish_book(engine, folder) and complete
        except Exception as error:
            complete = False
            engine.book(folder, status='held', process_running=False, reason=str(error))
    engine.state['status'] = 'complete' if complete else 'held'
    engine.save()
    return 0 if complete else 1


if __name__ == '__main__':
    raise SystemExit(main())
