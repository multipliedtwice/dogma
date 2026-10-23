"""Sequential durable context/crop recovery driver; all protected helpers stay pinned.

--prepare-only runs CPU source checks and isolated text proposals, never GPU work.
The full workflow waits for old base/finisher owners to exit, then runs one explicit
context stage at a time. Failed strategies remain recorded and are never repeated
outside the three frozen proposal choices. No source unit is silently omitted.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
import context_public_audiobook_recovery as context_module
import finish_public_audiobook_demos as finishing
import supervise_public_audiobook_demos as supervision

SELF = Path(__file__).resolve()
OLD_SCRIPTS = {'finish_public_audiobook_demos.py', 'supervise_public_audiobook_demos.py', 'render_public_audiobook_demo.py',
               'context_public_audiobook_recovery.py', 'witness_public_demo_turbo.py',
               'recover_public_audiobook_demo.py', 'prepare_public_demo_music.py', SELF.name}


class OwnershipConflict(finishing.StageFailure):
    pass


def configuration(args):
    import resolve_public_demo_recognition as resolver
    inputs={}
    for folder in args.folder:
        folder=folder.resolve()
        frozen=supervision.read(folder/'frozen.json')
        inputs[str(folder)]={'frozen_generation':supervision.digest(frozen),
                             'assignment_sha256':supervision.file_hash(folder/'assignment.json'),
                             'manifest_sha256':supervision.file_hash(folder/'manifest.json'),
                             'frozen_sha256':supervision.file_hash(folder/'frozen.json'),
                             'prepared_source_sha256':supervision.file_hash(folder/'prepared/book.json')}
    return {'schema':1, 'controller_sha256':supervision.file_hash(SELF),
            'engine_sha256':supervision.file_hash(Path(finishing.__file__)),
            'context_dependencies':context_module.dependencies(),
            'resolver_dependencies':resolver.dependencies(), 'folder_inputs':inputs,
            'folders':[str(p.resolve()) for p in args.folder], 'unit_filter':sorted(set(args.unit)),
            'python':str(args.python.resolve()), 'asr_python':str(args.asr_python.resolve())}


def pin_configuration(path, value):
    """Check before Engine creation so a bad resume cannot rewrite its history."""
    if path.exists():
        if supervision.read(path) != value:
            raise ValueError('Context driver configuration/dependencies changed; retained history requires a new output namespace')
    else:
        supervision.write(path, value)


def previous_jobs(processes, folders, own_pid):
    """Only exact known project scripts/target folders count; never adopt or stop them."""
    by_pid = {p['pid']:p for p in processes}
    own = set()
    while own_pid and own_pid not in own:
        own.add(own_pid)
        own_pid = by_pid.get(own_pid, {}).get('ppid', 0)
    wanted = {p.resolve() for p in folders}
    found = []
    for row in processes:
        command = row.get('command') or []
        if row['pid'] in own or not row.get('name','').casefold().startswith(('python','pypy')):
            continue
        cwd = Path(row['cwd'])
        scripts = [(Path(part) if Path(part).is_absolute() else cwd/part).resolve()
                   for part in command[1:] if Path(part).name in OLD_SCRIPTS]
        if not any(script == ROOT/'scripts'/script.name for script in scripts):
            continue
        if supervision.flag_value(command, '--internal-stage'):
            continue  # These are CPU-only resolver/completion helpers.
        if any(script.name=='context_public_audiobook_recovery.py' for script in scripts) and supervision.flag_value(command,'--stage') not in ('render','verify','align'):
            continue
        gpu_stages={'witness_public_demo_turbo.py':('witness',), 'recover_public_audiobook_demo.py':('render',),
                    'prepare_public_demo_music.py':('render','verify')}
        if any(script.name in gpu_stages and supervision.flag_value(command,'--stage') not in gpu_stages[script.name] for script in scripts):
            continue
        if any(script.name==SELF.name for script in scripts) and ('--prepare-only' in command or '--internal-gate' in command):
            continue
        targets = []
        audio_targets=[]
        for index, item in enumerate(command[:-1]):
            if item == '--folder':
                candidate = Path(command[index+1])
                targets.append((candidate if candidate.is_absolute() else cwd/candidate).resolve())
            elif item=='--audio' and any(script.name=='prepare_public_demo_music.py' for script in scripts):
                for filename in command[index+1:]:
                    if filename.startswith('--'):
                        break
                    candidate=Path(filename)
                    audio_targets.append((candidate if candidate.is_absolute() else cwd/candidate).resolve())
        if wanted.intersection(targets) or any(path.is_relative_to(folder) for path in audio_targets for folder in wanted):
            found.append({**{k:row[k] for k in ('pid','create_time','command','cwd')},
                          'other_controller':any(script.name==SELF.name for script in scripts)})
    return found


def wait_previous(engine, args):
    import psutil
    started = time.monotonic()
    while True:
        rows = []
        for process in psutil.process_iter(['pid','ppid','name']):
            if not (process.info.get('name') or '').casefold().startswith(('python','pypy')):
                continue
            try:
                rows.append({'pid':process.pid, 'ppid':process.ppid(), 'name':process.name(), 'command':process.cmdline(),
                             'cwd':process.cwd(), 'create_time':process.create_time()})
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as error:
                raise finishing.StageFailure('Cannot verify existing Python job identity; refusing GPU overlap') from error
        busy = previous_jobs(rows, args.folder, os.getpid())
        journal = None
        if args.wait_finisher:
            try:
                previous = supervision.read(args.wait_finisher)
                pid = previous.get('supervisor_pid')
                journal = {'path':str(args.wait_finisher), 'claimed_pid':pid, 'claimed_status':previous.get('status'),
                           'actual_matching_process':next((p for p in busy if p['pid']==pid),None)}
            except (OSError, ValueError):
                journal = {'path':str(args.wait_finisher), 'status':'missing_or_unreadable'}
        engine.state['prior_owner_wait'] = {'updated_unix':time.time(), 'busy':busy, 'journal':journal}
        engine.save()
        if any(row['other_controller'] for row in busy):
            raise OwnershipConflict('Another context controller owns these folders; refusing mutual waiting or GPU overlap')
        if not busy:
            return
        if time.monotonic()-started >= args.wait_deadline_seconds:
            raise finishing.StageFailure('Existing base/finisher is still live; wait deadline reached without stopping it')
        print('WAITING for prior base/finisher ownership', [p['pid'] for p in busy], flush=True)
        time.sleep(args.poll_seconds)


def internal_gate(folder, unit_id, attempt, kind):
    assigned, _, _ = context_module.isolated.frozen_context(folder)
    unit = next(u for u in assigned['units'] if u['id']==unit_id)
    decision = context_module.blind_decision(folder, unit, attempt, kind)
    output = context_module.attempt_home(folder, unit, attempt)/('driver-'+kind+'-gate.json')
    context_module.immutable(output, {'controller_sha256':supervision.file_hash(SELF), 'decision':decision,
                                     'source_unit':unit_id, 'attempt':attempt, 'alignment_is_approval':False})
    if not decision['passed']:
        raise finishing.StageFailure('Blind '+kind+' recognition remains held; alignment/publication forbidden')


class Controller:
    def __init__(self, args, engine):
        self.args, self.engine = args, engine
        self.folders = [p.resolve() for p in args.folder]
        self.units = engine.state.setdefault('context_units', {})

    def state(self, folder, unit_id):
        return self.units.setdefault(str(folder)+'::'+unit_id, {'attempts':{}})

    def stage(self, folder, unit_id, name, script, flags, asr=False, gpu=False):
        if gpu:
            wait_previous(self.engine, self.args)
        command = self.engine.command(script, folder, ['--unit',unit_id,*flags], asr=asr)
        self.engine.run('context-'+unit_id+'-'+name, folder, command, kind='asr' if asr and gpu else ('cli' if name=='propose' else 'normal'))

    def prepare(self, folder, unit_id):
        for stage in ('plan','propose'):
            self.stage(folder, unit_id, stage, Path(context_module.__file__), ['--stage',stage],asr=True)
        self.state(folder,unit_id)['status']='prepared'
        self.engine.save()

    def attempt(self, folder, unit_id, number):
        flags = ['--attempt',str(number)]
        script = Path(context_module.__file__)
        prefix = 'a'+str(number)+'-'
        # Context source/phonemizer dependencies live in the ASR environment;
        # synthesis itself still uses the identical pinned external native runner.
        self.stage(folder,unit_id,prefix+'render',script,['--stage','render',*flags],asr=True,gpu=True)
        for recognizer in ('qwen','turbo'):
            self.stage(folder,unit_id,prefix+'full-'+recognizer,script,
                       ['--stage','verify','--kind','context','--recognizer',recognizer,*flags],asr=True,gpu=True)
        self.stage(folder,unit_id,prefix+'full-gate',SELF,['--internal-gate','context',*flags],asr=True)
        self.stage(folder,unit_id,prefix+'align',script,['--stage','align',*flags],asr=True,gpu=True)
        self.stage(folder,unit_id,prefix+'crop',script,['--stage','crop',*flags],asr=True)
        for recognizer in ('qwen','turbo'):
            self.stage(folder,unit_id,prefix+'crop-'+recognizer,script,
                       ['--stage','verify','--kind','crop','--recognizer',recognizer,*flags],asr=True,gpu=True)
        self.stage(folder,unit_id,prefix+'crop-gate',SELF,['--internal-gate','crop',*flags],asr=True)
        self.stage(folder,unit_id,prefix+'validate',script,['--stage','validate',*flags],asr=True)
        assigned = supervision.read(folder/'assignment.json')
        unit = next(u for u in assigned['units'] if u['id']==unit_id)
        receipt = context_module.attempt_home(folder,unit,number)/'crop-receipt.json'
        self.engine.run('context-'+unit_id+'-'+prefix+'register',folder,
                        self.engine.command(finishing.SCRIPTS['resolve'],folder,['--register-context',str(receipt)],asr=True))

    def finish_book(self, folder):
        self.engine.book(folder,status='context-preparing')
        snapshot = self.engine.resolve(folder)
        for row in snapshot['report']['units']:
            uid = row['id']
            if self.args.unit and uid not in self.args.unit:
                continue
            if row.get('selected'):
                key=str(folder)+'::'+uid
                if key in self.units:
                    self.units[key].update(status='verified',selected=row['selected'])
                    self.engine.save()
                continue
            state = self.state(folder,uid)
            if not all((folder/'takes'/uid/('take-%02d.json'%n)).exists() for n in range(6)):
                state.update(status='held',reason='Six isolated source-bound trials are not exhausted')
                self.engine.save()
                continue
            try:
                self.prepare(folder,uid)
            except finishing.StageFailure as error:
                state.update(status='held',reason='Context preparation failed: '+str(error))
                self.engine.save()
                continue
            if self.args.prepare_only:
                continue
            for number in (1,2,3):
                history = state['attempts'].setdefault(str(number),{})
                if history.get('status')=='held':
                    continue
                history.update(status='running',started_unix=time.time())
                self.engine.save()
                try:
                    self.attempt(folder,uid,number)
                    snapshot = self.engine.resolve(folder)
                    self.engine.publish_if_eligible(folder,snapshot)
                    selected = next(u for u in snapshot['report']['units'] if u['id']==uid).get('selected')
                    if not selected:
                        raise finishing.StageFailure('Registered crop did not pass complete source-bound resolver recomputation')
                    history.update(status='verified',finished_unix=time.time())
                    state.update(status='verified',selected=selected)
                    self.engine.save()
                    break
                except OwnershipConflict as error:
                    history.update(status='blocked_ownership',reason=str(error),finished_unix=time.time())
                    self.engine.save()
                    raise
                except finishing.StageFailure as error:
                    history.update(status='held',reason=str(error),finished_unix=time.time())
                    state.update(status='held',reason='Context attempt '+str(number)+' remains held')
                    self.engine.save()
            if state.get('status')!='verified':
                state.update(status='held',reason='Three frozen context strategies exhausted or remained held')
                self.engine.save()
        if self.args.prepare_only:
            prepared = all(s.get('status') in ('prepared','verified') for key,s in self.units.items() if key.startswith(str(folder)+'::'))
            self.engine.book(folder,status='prepared' if prepared else 'held',process_running=False)
            return prepared
        snapshot = self.engine.resolve(folder)
        self.engine.publish_if_eligible(folder,snapshot)
        result = self.engine.resolve(folder,complete=True)
        self.engine.book(folder,status='complete' if result['completion_valid'] else 'held',process_running=False,
                         reason=result['completion_reason'],report_sha256=result['report_sha256'])
        return result['completion_valid']

    def run(self):
        success = True
        for folder in self.folders:
            try:
                success = self.finish_book(folder) and success
            except Exception as error:
                success = False
                self.engine.book(folder,status='held',process_running=False,reason=str(error))
        self.engine.state['status']=('prepared' if self.args.prepare_only else 'complete') if success else 'held'
        self.engine.save()
        return 0 if success else 1


def main():
    os.environ['PYTHONUTF8']='1'
    os.environ['PYTHONIOENCODING']='utf-8'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,action='append',required=True)
    parser.add_argument('--unit',action='append',default=[])
    parser.add_argument('--output',type=Path)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--wait-finisher',type=Path)
    parser.add_argument('--python',type=Path,default=ROOT/'training-work/.venv-tts/Scripts/python.exe')
    parser.add_argument('--asr-python',type=Path,default=ROOT/'training-work/.venv-asr/Scripts/python.exe')
    parser.add_argument('--wait-deadline-seconds',type=float,default=43200)
    parser.add_argument('--poll-seconds',type=float,default=5)
    parser.add_argument('--stall-seconds',type=float,default=600)
    parser.add_argument('--verifier-initial-seconds',type=float,default=1800)
    parser.add_argument('--internal-gate',choices=['context','crop'],help=argparse.SUPPRESS)
    parser.add_argument('--attempt',type=int,choices=[1,2,3],default=1,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.internal_gate:
        if len(args.folder)!=1 or len(args.unit)!=1:
            parser.error('Internal gate requires exactly one folder/unit')
        internal_gate(args.folder[0].resolve(),args.unit[0],args.attempt,args.internal_gate)
        return 0
    if args.output is None:
        parser.error('--output is required')
    if len(set(p.resolve() for p in args.folder))!=len(args.folder) or min(args.wait_deadline_seconds,args.poll_seconds,args.stall_seconds,args.verifier_initial_seconds)<=0:
        parser.error('Distinct folders and positive timeouts are required')
    args.output=args.output.resolve()
    args.python=args.python.resolve()
    args.asr_python=args.asr_python.resolve()
    pin_configuration(args.output/'context-controller-config.json',configuration(args))
    args.stage_retries=0  # Semantic failures move to the next distinct frozen strategy.
    engine=finishing.Engine(args)
    return Controller(args,engine).run()


if __name__=='__main__':
    raise SystemExit(main())
