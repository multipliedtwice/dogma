"""Supervise remaining explicit development recoveries, never production defaults.

Frozen source/model/helper inputs are checked at every stage. Native attempts
are bounded to the campaign's three choices. Only timeouts or narrowly identified
operational errors may retry a stage; recognition/quality failures never do.
No local or GitHub publication is performed by this controller.
"""
from __future__ import annotations
import argparse
import importlib.metadata
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import context_public_demo_historical_word_recovery as helper
import resolve_public_demo_historical_word_context as resolver
import finish_public_audiobook_demos as finishing
import supervise_public_audiobook_demos as supervision
import finish_public_demo_context_recovery as ownership
import finish_public_demo_word_context_recovery as prior_driver
import supplement_public_demo_word_recovery as supplement
import resolve_public_demo_word_supplement as supplement_resolver

SELF=Path(__file__).resolve()
TARGETS=('u0071','u0153')
REPORT_PREFIXES=('report','pronunciation-report','sustained-sound-report','full-witness-report','beam-witness-report',
                 'acoustic-adjudication-report','ru-pronunciation-report','ru-acoustic-adjudication-report',
                 'word-boundary-report','word-pronunciation-report','word-supplement-report','word-context-report',
                 'historical-word-context-report')


class InputBindingConflict(finishing.StageFailure):
    """A queue may not consume native attempts after its pinned inputs change."""


def validate_baseline(folder,snapshot):
    """A prior report selects work only; it cannot approve a new receipt."""
    report=snapshot['report'];checksum=supervision.digest(report)
    path=Path(snapshot['report_path']).resolve();folder=folder.resolve()
    units=supervision.read(folder/'assignment.json')['units']
    if (snapshot['report_sha256']!=checksum or path.parent not in (folder/'finishing',folder/'resolution-proposals')
            or path.name not in {prefix+'-'+checksum+'.json' for prefix in REPORT_PREFIXES}
            or supervision.read(path)!=report
            or report.get('frozen_generation')!=supervision.digest(supervision.read(folder/'frozen.json'))
            or report.get('language')!='Russian' or report.get('human_approval') is not False
            or [u['id'] for u in report['units']]!=[u['id'] for u in units]
            or len({u['id'] for u in units})!=len(units)):
        raise ValueError('Selection-only baseline source/report binding differs')
    return snapshot


def baseline(args):
    path=args.output/'selection-only-baseline.json'
    snapshot=supervision.read(path if path.exists() else args.folder[0]/'finishing/current-resolution.json')
    validate_baseline(args.folder[0],snapshot)
    helper.immutable(path,snapshot)
    return snapshot


def dependency_pins(groups):
    files={};packages={}
    for group in groups:
        for name,value in group.items():
            if name=='files':
                nested,nested_packages=dependency_pins([value]);items=nested.items()
                if nested_packages:raise ValueError('Package metadata appeared inside dependency files')
            elif name.endswith('_helper_packages'):
                for package,version in value.items():
                    if not isinstance(version,str) or package in packages and packages[package]!=version:
                        raise ValueError('Conflicting dependency package pin')
                    packages[package]=version
                continue
            else:items=[(name,value)]
            for path,checksum in items:
                target=(ROOT/path).resolve()
                if (not target.is_relative_to(ROOT) or not target.is_file() or not isinstance(checksum,str)
                        or len(checksum)!=64 or path in files and files[path]!=checksum):
                    raise ValueError('Invalid or conflicting controller file dependency')
                files[path]=checksum
    return files,packages


def configuration(args):
    value=ownership.configuration(args)
    context=resolver.Context(args.folder[0])
    inputs=prepared_inputs(args.folder[0],args.unit)
    value.update(controller_sha256=supervision.file_hash(SELF),v13_dependencies=resolver.dependencies(),
                 ownership_driver_sha256=supervision.file_hash(Path(prior_driver.__file__)),
                 supplement_dependencies=supplement_resolver.dependencies(),prepared_inputs=inputs,
                 python_sha256=supervision.file_hash(args.python),asr_python_sha256=supervision.file_hash(args.asr_python),
                 context_dependency_sets={k:v for k,v in vars(context).items() if k=='dependencies' or k.endswith('_dependencies')},
                 requested_units=sorted(args.unit),operational_retries=args.operational_retries,publication=False,
                 github_publication=False,processing_mode='development-explicit-recovery')
    value['timeouts']={name:getattr(args,name) for name in ('poll_seconds','stall_seconds','verifier_initial_seconds','wait_deadline_seconds')}
    value['resolve_stall_seconds']=max(args.stall_seconds,1800)
    value['selection_only_baseline_sha256']=supervision.digest(baseline(args))
    value['controller_dependencies']={Path(module.__file__).relative_to(ROOT).as_posix():supervision.file_hash(Path(module.__file__))
                                    for module in (finishing,supervision,ownership,prior_driver)}
    value['dependency_files'],value['dependency_packages']=dependency_pins([
        value['v13_dependencies'],value['supplement_dependencies'],*value['context_dependency_sets'].values(),value['controller_dependencies']])
    return value


def prepared_inputs(folder,units):
    """Pin already-reviewed inputs; this controller never creates new votes."""
    assignment,_,_=helper.isolated.frozen_context(folder);paths={}
    for uid in units:
        unit=next(u for u in assignment['units'] if u['id']==uid)
        if uid=='u0071':
            bound,old=supplement.prepared_bound(folder,unit)
            supplement.validated_proposal(folder,unit,bound,old)
            selected=[supplement.home(folder,unit)/name for name in ('campaign.json','proposal.json')]
            selected.extend(helper.words.home(folder,unit)/name for name in ('campaign.json','proposal-request.json'))
        else:
            helper.campaign(folder,unit)
            selected=[helper.home(folder,unit)/'campaign.json',
                      *[helper.words.home(folder,unit)/name for name in ('campaign.json','proposal.json')]]
        for path in selected:paths[path.relative_to(folder).as_posix()]=supervision.file_hash(path)
    return paths


def previous_jobs(rows,folders,own_pid):
    busy=prior_driver.previous_jobs(rows,folders,own_pid)
    by_pid={row['pid']:row for row in rows};ancestors=set()
    while own_pid and own_pid not in ancestors:ancestors.add(own_pid);own_pid=by_pid.get(own_pid,{}).get('ppid',0)
    wanted={folder.resolve() for folder in folders}
    for row in rows:
        if row['pid'] in ancestors or not row.get('name','').casefold().startswith(('python','pypy')):continue
        command=row.get('command') or [];cwd=Path(row['cwd'])
        matches={name for name in (SELF.name,Path(helper.__file__).name,Path(supplement.__file__).name) if any(
            Path(part).name==name and (Path(part) if Path(part).is_absolute() else cwd/part).resolve()==ROOT/'scripts'/name for part in command[1:])}
        if not matches:continue
        controller=SELF.name in matches
        if controller and ('--internal-gate' in command or '--native-gate' in command):continue
        if not controller and supervision.flag_value(command,'--stage') not in ('render','verify','align'):continue
        targets=[(Path(command[i+1]) if Path(command[i+1]).is_absolute() else cwd/command[i+1]).resolve()
                 for i,item in enumerate(command[:-1]) if item=='--folder']
        if wanted.intersection(targets):busy.append({**row,'other_controller':controller})
    # A native subprocess may outlive a crashed Python owner. Recognize only
    # this project's exact runner and request sequence inside a selected book.
    native=ROOT/'training-work/higgs-tts-3-pilot/audio-cpp-cuda/audiocpp_cli.exe'
    for row in rows:
        command=row.get('command') or []
        if row['pid'] in ancestors or not command or Path(command[0]).resolve()!=native.resolve():continue
        sequence=supervision.flag_value(command,'--request-sequence')
        if not sequence:continue
        sequence=(Path(sequence) if Path(sequence).is_absolute() else Path(row['cwd'])/sequence).resolve()
        if any(sequence.is_relative_to(folder.resolve()) for folder in folders):busy.append({**row,'other_controller':False})
    return list({row['pid']:row for row in busy}.values())


def adopted_stage_pids(engine,folder,command,rows):
    return prior_driver.adopted_stage_pids(engine,folder,command,rows)


def wait_previous(engine,args,folder,command):
    import psutil
    started=time.monotonic();folders=[folder.parent/name for name in ('en-gift-of-magi','ru-chameleon')]
    while True:
        rows=[]
        for process in psutil.process_iter(['pid','ppid','name']):
            if not (process.info.get('name') or '').casefold().startswith(('python','pypy','audiocpp')):continue
            try:rows.append({'pid':process.pid,'ppid':process.ppid(),'name':process.name(),'command':process.cmdline(),
                             'cwd':process.cwd(),'create_time':process.create_time()})
            except psutil.NoSuchProcess:continue
            except psutil.AccessDenied as error:raise ownership.OwnershipConflict('Cannot verify an audio-process identity') from error
        adopted=adopted_stage_pids(engine,folder,command,rows)
        busy=[r for r in previous_jobs(rows,folders,os.getpid()) if r['pid'] not in adopted]
        engine.state['resource_wait']={'updated_unix':time.time(),'busy':busy,'adopting_exact_stage_pids':sorted(adopted)};engine.save()
        if any(r.get('other_controller',False) for r in busy):raise ownership.OwnershipConflict('Another demo controller is live; refusing mutual waiting')
        if not busy:return
        if time.monotonic()-started>=args.wait_deadline_seconds:raise ownership.OwnershipConflict('Known audio owner remains live at the wait deadline')
        print('WAITING for prior audio owners',[r['pid'] for r in busy],flush=True);time.sleep(args.poll_seconds)


def operational_failure(record):
    return prior_driver.operational_failure(record)


class Engine(finishing.Engine):
    def save(self):
        # The inherited engine updates stage liveness on every poll. Derive the
        # book field from the actual saved process identity, never a stale latch.
        for folder,book in self.state.get('books',{}).items():
            pids=set()
            for stage in self.state.get('stages',{}).values():
                if stage.get('folder')==folder:pids.update(active_stage_pids(stage))
            book.update(stage_active=bool(pids),stage_active_pids=sorted(pids),process_running=bool(pids))
        super().save()

    def resolve_v13(self,folder):
        # A complete CPU provenance pass can exceed the native worker's idle
        # allowance. Keep it bounded without inventing verifier progress.
        original=self.args.stall_seconds
        try:
            self.args.stall_seconds=max(original,1800)
            self.run('v13-unified-source-bound-resolve',folder,self.command(Path(resolver.__file__),folder,['--stage','resolve'],asr=True))
        finally:self.args.stall_seconds=original
        return finishing.current_resolution(folder)


def active_stage_pids(stage):
    import psutil
    attempts=stage.get('attempts',[])
    if stage.get('status')!='running' or not attempts or attempts[-1].get('status')!='running':return set()
    identity=attempts[-1]['identity']
    try:
        process=psutil.Process(identity['pid'])
        if supervision.live(process,identity) and process.cmdline()==identity['command']:return {process.pid}
    except psutil.Error:pass
    return set()


def internal_gate(folder,unit_id,attempt,kind=None):
    assigned,_,_=helper.isolated.frozen_context(folder);unit=next(u for u in assigned['units'] if u['id']==unit_id)
    selected=supplement if unit_id=='u0071' else helper
    directory=selected.attempt_home(folder,unit,attempt)
    if kind is None:
        receipt=selected.native_receipt(folder,unit,attempt)[-1]
        passed=receipt.get('quality_passed') is True
        value={'quality_passed':passed,'native_receipt_sha256':supervision.file_hash(directory/'native-receipt.json')}
        name='native'
    elif unit_id=='u0071':
        if kind!='word':raise ValueError('Supplement uses only the whole original-word target gate')
        receipt=selected.native_receipt(folder,unit,attempt)[-1]
        proof=selected.validate_supplement_receipt(folder,unit,receipt)
        decision=proof['target_decision'];passed=decision['passed'];value={'decision':decision};name='word'
    else:
        if kind not in ('context','crop'):raise ValueError('Historical context requires full and crop gates')
        decision=helper.blind_decision(folder,unit,attempt,kind);passed=decision['passed'];value={'decision':decision};name=kind
    helper.immutable(directory/('remaining-controller-'+name+'-gate.json'),{'controller_sha256':supervision.file_hash(SELF),
        'source_unit':unit_id,'attempt':attempt,'alignment_is_approval':False,**value})
    if not passed:raise finishing.StageFailure(name+' source/quality gate remains held')
    print('VERIFIED',unit_id,'attempt',attempt,name,'gate',flush=True)


class Controller:
    def __init__(self,args,engine,pin):
        self.args,self.engine,self.pin=args,engine,pin
        self.units=engine.state.setdefault('remaining_recovery_units',{})

    def recheck(self):
        if (supervision.file_hash(SELF)!=self.pin['controller_sha256'] or resolver.dependencies()!=self.pin['v13_dependencies']
                or supplement_resolver.dependencies()!=self.pin['supplement_dependencies']
                or supervision.file_hash(Path(prior_driver.__file__))!=self.pin['ownership_driver_sha256']
                or supervision.file_hash(self.args.python)!=self.pin['python_sha256']
                or supervision.file_hash(self.args.asr_python)!=self.pin['asr_python_sha256']):
            raise InputBindingConflict('Controller or immutable helper inputs changed; use a new namespace')
        for name,checksum in self.pin['dependency_files'].items():
            path=(ROOT/name).resolve()
            if not path.is_relative_to(ROOT) or supervision.file_hash(path)!=checksum:
                raise InputBindingConflict('Pinned controller/resolver dependency changed')
        for package,version in self.pin['dependency_packages'].items():
            if importlib.metadata.version(package)!=version:raise InputBindingConflict('Pinned helper package changed')
        folder=self.args.folder[0]
        for name,checksum in self.pin['prepared_inputs'].items():
            if supervision.file_hash(folder/name)!=checksum:raise InputBindingConflict('Prepared recovery evidence changed; retain this queue and use a new namespace')
        inputs=self.pin['folder_inputs'][str(folder.resolve())]
        for name,key in [('assignment.json','assignment_sha256'),('manifest.json','manifest_sha256'),
                         ('frozen.json','frozen_sha256'),('prepared/book.json','prepared_source_sha256')]:
            if supervision.file_hash(folder/name)!=inputs[key]:raise InputBindingConflict('Frozen generation/source inputs changed')

    def stage(self,folder,uid,name,script,flags,gpu=False,semantic=False):
        self.recheck();command=self.engine.command(script,folder,
             flags if script in (Path(resolver.__file__),Path(supplement_resolver.__file__)) else ['--unit',uid,*flags],asr=True)
        self.engine.active_name=name
        key=supervision.digest({'folder':str(folder),'name':name,'command':command})
        while True:
            if gpu:wait_previous(self.engine,self.args,folder,command)
            print('STAGE',uid,name,flush=True)
            try:
                self.engine.run(name,folder,command,kind='asr' if gpu and ('verify' in flags or 'align' in flags) else 'normal')
                return
            except finishing.StageFailure:
                record=self.engine.state.get('stages',{}).get(key,{})
                used=record.get('operational_retry_count',0)
                if semantic or used>=self.args.operational_retries or not operational_failure(record):raise
                record['operational_retry_count']=used+1;self.engine.save()
                print('OPERATIONAL RETRY',uid,name,used+1,'same immutable attempt',flush=True)

    def attempt(self,folder,uid,number):
        if uid=='u0071':return self.supplement_attempt(folder,uid,number)
        prefix='historical-context-'+uid+'-a'+str(number)+'-';flags=['--attempt',str(number)];script=Path(helper.__file__)
        self.stage(folder,uid,prefix+'render',script,['--stage','render',*flags],gpu=True)
        self.stage(folder,uid,prefix+'native-gate',SELF,['--native-gate',*flags],semantic=True)
        for kind in ('context','crop'):
            for recognizer in ('qwen','turbo'):
                self.stage(folder,uid,prefix+kind+'-'+recognizer,script,
                           ['--stage','verify','--kind',kind,'--recognizer',recognizer,*flags],gpu=True)
            self.stage(folder,uid,prefix+kind+'-gate',SELF,['--internal-gate',kind,*flags],semantic=True)
            if kind=='context':
                self.stage(folder,uid,prefix+'align',script,['--stage','align',*flags],gpu=True)
                self.stage(folder,uid,prefix+'crop',script,['--stage','crop',*flags],semantic=True)
        self.stage(folder,uid,prefix+'validate',script,['--stage','validate',*flags],semantic=True)
        unit=next(u for u in supervision.read(folder/'assignment.json')['units'] if u['id']==uid)
        self.stage(folder,uid,prefix+'register',Path(resolver.__file__),
                   ['--stage','register','--receipt',str(helper.receipt_path(folder,unit,number))],semantic=True)

    def supplement_attempt(self,folder,uid,number):
        prefix='supplement-'+uid+'-a'+str(number)+'-';flags=['--attempt',str(number)];script=Path(supplement.__file__)
        self.stage(folder,uid,prefix+'render',script,['--stage','render',*flags],gpu=True)
        self.stage(folder,uid,prefix+'native-gate',SELF,['--native-gate',*flags],semantic=True)
        for recognizer in ('qwen','turbo'):
            self.stage(folder,uid,prefix+recognizer,script,['--stage','verify','--recognizer',recognizer,*flags],gpu=True)
        self.stage(folder,uid,prefix+'word-gate',SELF,['--internal-gate','word',*flags],semantic=True)
        self.stage(folder,uid,prefix+'validate',script,['--stage','validate',*flags],semantic=True)
        unit=next(u for u in supervision.read(folder/'assignment.json')['units'] if u['id']==uid)
        # The V11 adapter may register its own source-bound receipt, but must
        # never generate a report after V12/V13 canonical crops are present.
        self.stage(folder,uid,prefix+'register',Path(supplement_resolver.__file__),
                   ['--stage','register','--receipt',str(supplement.receipt_path(folder,unit,number))],semantic=True)

    def run(self):
        folder=self.args.folder[0].resolve()
        try:
            self.recheck();snapshot=baseline(self.args)
            if supervision.digest(snapshot)!=self.pin['selection_only_baseline_sha256']:
                raise InputBindingConflict('Selection-only baseline changed')
            for uid in self.args.unit:
                state=self.units.setdefault(uid,{'attempts':{}})
                row=next(u for u in snapshot['report']['units'] if u['id']==uid)
                if row.get('selected'):
                    state.update(status='baseline_selected_pending_revalidation');self.engine.save();continue
                if any(h.get('status') in ('registered_pending_revalidation','verified') for h in state['attempts'].values()):continue
                self.engine.book(folder,status='running',unit=uid,process_running=False)
                for number in (1,2,3):
                    history=state['attempts'].setdefault(str(number),{})
                    if history.get('status')=='held':continue
                    history.update(status='running',started_unix=time.time());state['status']='running';self.engine.save()
                    try:
                        self.attempt(folder,uid,number)
                        history.update(status='registered_pending_revalidation',finished_unix=time.time())
                        state.update(status='registered_pending_revalidation');self.engine.save();break
                    except (ownership.OwnershipConflict,InputBindingConflict) as error:
                        history.update(status='blocked_ownership',reason=str(error));self.engine.save();raise
                    except finishing.StageFailure as error:
                        history.update(status='held',reason=str(error),finished_unix=time.time());state.update(status='held',reason='Bounded strategy remains held');self.engine.save()
                        print('HELD',uid,'attempt',number,str(error),flush=True)
                if state.get('status')!='registered_pending_revalidation':state.update(status='held',reason='Three source-bound strategies exhausted');self.engine.save()
            # A single complete V13 pass sees V11/V12/V13 receipts together.
            # Cached counts or successful registration never establish approval.
            self.recheck();snapshot=self.engine.resolve_v13(folder);self.recheck()
            for uid in self.args.unit:
                state=self.units[uid];selected=next(u for u in snapshot['report']['units'] if u['id']==uid).get('selected')
                if selected:
                    state.update(status='verified',selected=selected);state.pop('reason',None)
                    for history in state['attempts'].values():
                        if history.get('status')=='registered_pending_revalidation':history['status']='verified'
                else:state.update(status='held',reason='Unified V13 report leaves target held')
            success=all(self.units.get(uid,{}).get('status')=='verified' for uid in self.args.unit)
            self.engine.state.update(status='complete' if success else 'held',scope_complete=success,book_complete=False,
                                     book_completion_status='Not published or assessed by this target-only controller',
                                     target_scope=list(self.args.unit),publication=False,github_publication=False,
                                     final_counts={k:snapshot['report'][k] for k in ('verified_units','open_recognition_flags','pending_units')})
            self.engine.book(folder,status='targets_verified' if success else 'held',process_running=False,
                             reason='Only requested targets assessed; other held units remain visible',report_sha256=snapshot['report_sha256'])
            self.engine.save();return 0 if success else 1
        except Exception as error:
            self.engine.state.update(status='held',error=str(error));self.engine.book(folder,status='held',process_running=False,reason=str(error));self.engine.save();raise


def main():
    os.environ.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8');parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,action='append',required=True);parser.add_argument('--unit',action='append',default=[])
    parser.add_argument('--output',type=Path);parser.add_argument('--python',type=Path,default=ROOT/'training-work/.venv-tts/Scripts/python.exe')
    parser.add_argument('--asr-python',type=Path,default=ROOT/'training-work/.venv-asr/Scripts/python.exe')
    parser.add_argument('--poll-seconds',type=float,default=5);parser.add_argument('--stall-seconds',type=float,default=600)
    parser.add_argument('--verifier-initial-seconds',type=float,default=1800);parser.add_argument('--wait-deadline-seconds',type=float,default=43200)
    parser.add_argument('--operational-retries',type=int,choices=[0,1],default=1)
    parser.add_argument('--internal-gate',choices=['context','crop','word'],help=argparse.SUPPRESS);parser.add_argument('--native-gate',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--attempt',type=int,choices=[1,2,3],help=argparse.SUPPRESS);args=parser.parse_args()
    if len(args.folder)!=1:parser.error('Exactly one Russian demo folder is required')
    args.folder=[args.folder[0].resolve()]
    if not args.unit:args.unit=list(TARGETS)
    if len(set(args.unit))!=len(args.unit) or any(uid not in TARGETS for uid in args.unit):parser.error('Only distinct u0071/u0153 targets are authorized')
    if args.internal_gate or args.native_gate:
        if len(args.unit)!=1 or args.attempt is None:parser.error('One explicit unit and attempt required for gate')
        internal_gate(args.folder[0],args.unit[0],args.attempt,args.internal_gate);return 0
    if args.output is None:parser.error('--output required')
    if min(args.poll_seconds,args.stall_seconds,args.verifier_initial_seconds,args.wait_deadline_seconds)<=0:parser.error('Timeouts must be positive')
    args.output=args.output.resolve();args.python=args.python.resolve();args.asr_python=args.asr_python.resolve();args.stage_retries=0
    pin=configuration(args);ownership.pin_configuration(args.output/'remaining-recovery-controller-config.json',pin)
    return Controller(args,Engine(args),pin).run()


if __name__=='__main__':raise SystemExit(main())
