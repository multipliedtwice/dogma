"""Supervise V16 explicit development line-wrap experiments, never production defaults.

Frozen source/model/helper inputs are checked at every stage. Native attempts
are bounded to the campaign's three choices. Only timeouts or narrowly identified
operational errors may retry a stage; recognition/quality failures never do.
No local or GitHub publication is performed by this controller.
"""
from __future__ import annotations
import argparse
import copy
import importlib.metadata
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import context_public_demo_line_wrap_recovery as helper
import resolve_public_demo_line_wrap as resolver
import finish_public_audiobook_demos as finishing
import supervise_public_audiobook_demos as supervision
import finish_public_demo_context_recovery as ownership
import finish_public_demo_word_context_recovery as prior_driver
import finish_public_demo_supplement_context_recovery_v2 as previous_driver
import finish_public_demo_boundary_recovery as boundary_driver

SELF=Path(__file__).resolve()
TARGETS=('u0132','u0133')
REPORT_PREFIXES=('report','pronunciation-report','sustained-sound-report','full-witness-report','beam-witness-report',
                 'acoustic-adjudication-report','ru-pronunciation-report','ru-acoustic-adjudication-report',
                 'word-boundary-report','word-pronunciation-report','word-supplement-report','word-context-report',
                 'historical-word-context-report','line-wrap-report','supplement-word-context-report')


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


def validate_predecessor(args):
    """Actual stopped identities, not journal liveness labels, permit handoff."""
    import psutil
    path=args.prior_worker.resolve();folder=args.folder[0].resolve();worker=supervision.read(path)
    command=worker['command'];script=ROOT/'scripts/finish_public_demo_supplement_context_recovery_v2.py'
    if (path.name!='worker.json' or not path.is_relative_to(folder.parent) or str(script) not in command
            or supervision.flag_value(command,'--folder')!=str(folder)
            or supervision.flag_value(command,'--output')!=str(path.parent)
            or worker.get('controller_sha256')!=supervision.file_hash(script)):
        raise InputBindingConflict('Prior controller source/folder/namespace identity differs')
    identities=[{'pid':worker['pid'],'create_time':worker['create_time'],'command':command}]
    journal_path=path.parent/'finishing-supervision.json';journal=supervision.read(journal_path)
    for stage in journal['stages'].values():
        identities.extend(stage.get('process_tree',[]))
        identities.extend(a['identity'] for a in stage.get('attempts',[]) if a.get('identity'))
    for identity in identities:
        try:
            if supervision.live(psutil.Process(identity['pid']),identity):raise ownership.OwnershipConflict('Prior controller/stage still live')
        except psutil.NoSuchProcess:pass
    for process in psutil.process_iter(['name']):
        if not (process.info.get('name') or '').lower().startswith('python'):continue
        try:
            if process.cmdline()[1:]==command[1:]:raise ownership.OwnershipConflict('Prior Python core/launcher still live')
        except psutil.NoSuchProcess:pass
    return {'worker_path':str(path),'worker_sha256':supervision.file_hash(path),'journal_sha256':supervision.file_hash(journal_path),
        'source_controller_sha256':worker['controller_sha256'],'identities_stopped':True,'prior_status':journal.get('status'),
        'prior_status_is_approval':False,'publication':False}


def configuration(args):
    value=ownership.configuration(args)
    context=resolver.Context(args.folder[0])
    inputs,approved,review_holds=prepared_inputs(args.folder[0],args.unit)
    predecessor=validate_predecessor(args)
    value.update(controller_sha256=supervision.file_hash(SELF),v16_dependencies=resolver.dependencies(),
                 ownership_driver_sha256=supervision.file_hash(Path(prior_driver.__file__)),
                 previous_driver_sha256=supervision.file_hash(Path(previous_driver.__file__)),prepared_inputs=inputs,
                 python_sha256=supervision.file_hash(args.python),asr_python_sha256=supervision.file_hash(args.asr_python),
                 context_dependency_sets={k:v for k,v in vars(context).items() if k=='dependencies' or k.endswith('_dependencies')},
                 requested_units=sorted(args.unit),operational_retries=args.operational_retries,publication=False,
                 github_publication=False,processing_mode='development-explicit-recovery')
    value['approved_choices']=approved;value['review_holds']=review_holds;value['defer_final_report']=args.defer_final_report
    value['predecessor']=predecessor
    value['timeouts']={name:getattr(args,name) for name in ('poll_seconds','stall_seconds','verifier_initial_seconds','wait_deadline_seconds')}
    value['resolve_stall_seconds']=max(args.stall_seconds,1800)
    value['selection_only_baseline_sha256']=supervision.digest(baseline(args))
    value['controller_dependencies']={Path(module.__file__).relative_to(ROOT).as_posix():supervision.file_hash(Path(module.__file__))
                                    for module in (finishing,supervision,ownership,prior_driver,previous_driver,boundary_driver)}
    value['dependency_files'],value['dependency_packages']=dependency_pins([
        value['v16_dependencies'],*value['context_dependency_sets'].values(),value['controller_dependencies']])
    return value


def prepared_inputs(folder,units):
    assignment,_,_=helper.isolated.frozen_context(folder);paths={};approved={};holds={}
    for uid in units:
        unit=next(u for u in assignment['units'] if u['id']==uid)
        bound=helper.campaign(folder,unit);base=helper.home(folder,unit)
        if (base/'proposal.json').exists():approved[uid]=helper.validated_proposal(folder,unit,bound)['approved_candidates']
        else:
            failures=[]
            for path in sorted(base.glob('*-attempt-*.failure.json')):
                failure=supervision.read(path)
                if failure.get('kind') not in ('candidate_rejected','answer_format_or_fact_failure','provider_or_format_failure'):continue
                request=path.with_name(path.name.replace('.failure.json','.request.json'))
                if failure.get('request_sha256')!=supervision.file_hash(request):raise InputBindingConflict('Retained review failure request changed')
                if 'answer_sha256' in failure:
                    answer=path.with_name(path.name.replace('.failure.json','.answer.json'))
                    if failure['answer_sha256']!=supervision.file_hash(answer):raise InputBindingConflict('Retained invalid/rejected answer changed')
                failures.append({'path':path.relative_to(folder).as_posix(),'sha256':supervision.file_hash(path)})
            if not failures:raise ValueError('Preparation is incomplete, not a terminal independent review hold')
            approved[uid]=[];holds[uid]={'reason':'Retained terminal evaluator rejection/format failure; no valid native proposal and no new calls',
                                      'failures':failures,'native_authorized':False}
        selected=list(sorted(base.glob('*.json')))
        selected.extend(helper.words.home(folder,unit)/name for name in ('campaign.json','proposal.json'))
        for path in selected:paths[path.relative_to(folder).as_posix()]=supervision.file_hash(path)
    return paths,approved,holds


def previous_jobs(rows,folders,own_pid):
    busy=previous_driver.previous_jobs(rows,folders,own_pid)
    by_pid={row['pid']:row for row in rows};ancestors=set()
    while own_pid and own_pid not in ancestors:ancestors.add(own_pid);own_pid=by_pid.get(own_pid,{}).get('ppid',0)
    wanted={folder.resolve() for folder in folders}
    for row in rows:
        if row['pid'] in ancestors or not row.get('name','').casefold().startswith(('python','pypy')):continue
        command=row.get('command') or [];cwd=Path(row['cwd'])
        matches={name for name in (SELF.name,Path(helper.__file__).name,'context_public_demo_supplement_word_recovery.py','finish_public_demo_supplement_context_recovery.py','finish_public_demo_supplement_context_recovery_v2.py','assemble_public_demo_ellipsis_recovery.py','context_public_demo_reviewed_reference_recovery.py') if any(
            Path(part).name==name and (Path(part) if Path(part).is_absolute() else cwd/part).resolve()==ROOT/'scripts'/name for part in command[1:])}
        if not matches:continue
        controller=bool(matches & {SELF.name,'finish_public_demo_supplement_context_recovery.py'})
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

    def resolve_v16(self,folder):
        # A complete CPU provenance pass can exceed the native worker's idle
        # allowance. Keep it bounded without inventing verifier progress.
        original=self.args.stall_seconds
        try:
            self.args.stall_seconds=max(original,1800)
            self.run('v16-unified-source-bound-resolve',folder,self.command(Path(resolver.__file__),folder,['--stage','resolve'],asr=True))
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


def assert_stage_workers_stopped(stages):
    """Check actual saved identities even when a journal claims they stopped."""
    import psutil
    identities=[]
    for stage in stages.values():
        for attempt in stage.get('attempts',[]):
            identity=attempt.get('identity')
            if not identity:continue
            try:
                process=psutil.Process(identity['pid'])
                if supervision.live(process,identity):
                    raise ownership.OwnershipConflict('A saved V16 stage worker is still live; no terminal handoff')
            except psutil.NoSuchProcess:pass
            except psutil.AccessDenied as error:raise ownership.OwnershipConflict('Cannot verify stopped stage identity') from error
            identities.append(identity)
    return identities


def handoff_inventory(folder,units):
    rows=[]
    for uid in units:
        for take in range(27,30):
            path=folder/'takes'/uid/('take-%02d.json'%take)
            if not path.exists():continue
            receipt=supervision.read(path);marker=receipt.get('spec',{}).get('line_wrap_recovery',{})
            attempt=marker.get('attempt');unit=next(u for u in supervision.read(folder/'assignment.json')['units'] if u['id']==uid)
            if (type(attempt) is not int or attempt not in (1,2,3) or take!=attempt+26 or receipt.get('unit')!=uid
                    or marker.get('policy')!=helper.POLICY or path.read_bytes()!=helper.receipt_path(folder,unit,attempt).read_bytes()):
                raise ValueError('Terminal handoff contains an unbound or non-mirrored V16 canonical receipt')
            rows.append({'path':path.relative_to(folder).as_posix(),'sha256':supervision.file_hash(path)})
    return rows


def validate_handoff(path,folder,require_controller_stopped=True):
    """CPU handoff integrity only; it never substitutes for the latest unified full report."""
    import psutil
    path=Path(path).resolve();folder=Path(folder).resolve();value=supervision.read(path)
    if (value.get('policy')!='public-demo-line-wrap-controller-deferred-handoff-v1'
            or value.get('controller_sha256')!=supervision.file_hash(SELF) or value.get('folder')!=str(folder)
            or value.get('stage_workers_stopped') is not True or value.get('approval_pending') is not True
            or value.get('book_complete') is not False or value.get('publication') is not False
            or value.get('scope_complete') is not False or value.get('counts_not_recomputed') is not True
            or any(s.get('status') not in ('held','registered_pending_revalidation') for s in value['units'].values())
            or value['canonical_receipts']!=handoff_inventory(folder,list(value['units']))):
        raise ValueError('Deferred handoff source/status/canonical integrity differs')
    pin_path=path.parent/'line-wrap-recovery-controller-config.json'
    if supervision.file_hash(pin_path)!=value['configuration_sha256']:raise ValueError('Deferred controller configuration changed')
    pin=supervision.read(pin_path)
    if (pin.get('defer_final_report') is not True or pin.get('controller_sha256')!=value['controller_sha256']
            or set(value['units'])!=set(pin['requested_units'])):
        raise ValueError('Deferred mode was not explicitly pinned')
    for name,checksum in pin['prepared_inputs'].items():
        if supervision.file_hash(folder/name)!=checksum:raise ValueError('Reviewed preparation evidence changed after handoff')
    for name,checksum in pin['dependency_files'].items():
        if supervision.file_hash(ROOT/name)!=checksum:raise ValueError('Deferred controller dependency changed')
    for package,version in pin['dependency_packages'].items():
        if importlib.metadata.version(package)!=version:raise ValueError('Deferred helper package changed')
    baseline_path=path.parent/'selection-only-baseline.json'
    if supervision.digest(supervision.read(baseline_path))!=value['selection_only_baseline_sha256']:raise ValueError('Deferred baseline changed')
    for name,key in [('assignment.json','assignment_sha256'),('manifest.json','manifest_sha256'),('frozen.json','frozen_sha256'),('prepared/book.json','prepared_source_sha256')]:
        if supervision.file_hash(folder/name)!=pin['folder_inputs'][str(folder)][key]:raise ValueError('Frozen source changed after handoff')
    journal=supervision.read(path.parent/'finishing-supervision.json')
    if (journal.get('status')!='awaiting_unified_revalidation' or journal.get('terminal_handoff') is not True
            or journal.get('handoff_sha256')!=supervision.file_hash(path) or journal.get('line_wrap_recovery_units')!=value['units']
            or assert_stage_workers_stopped(journal['stages'])!=value['stopped_stage_identities']):
        raise ValueError('Deferred terminal journal/stage identities changed')
    identity=value['controller_identity']
    if require_controller_stopped:
        try:
            if supervision.live(psutil.Process(identity['pid']),identity):raise ownership.OwnershipConflict('V16 controller has not exited yet')
        except psutil.NoSuchProcess:pass
    return value


def internal_gate(folder,unit_id,attempt,kind=None):
    assigned,_,_=helper.isolated.frozen_context(folder);unit=next(u for u in assigned['units'] if u['id']==unit_id)
    directory=helper.attempt_home(folder,unit,attempt)
    if kind is None:
        receipt=helper.native_receipt(folder,unit,attempt)[-1];passed=receipt.get('quality_passed') is True
        value={'quality_passed':passed,'native_receipt_sha256':supervision.file_hash(directory/'native-receipt.json')};name='native'
    else:
        if kind not in ('context','crop'):raise ValueError('Full/crop gates only')
        decision=helper.blind_decision(folder,unit,attempt,kind);passed=decision['passed'];value={'decision':decision};name=kind
    helper.immutable(directory/('line-wrap-controller-'+name+'-gate.json'),{'controller_sha256':supervision.file_hash(SELF),
        'source_unit':unit_id,'attempt':attempt,'alignment_is_approval':False,**value})
    if not passed:raise finishing.StageFailure(name+' source/quality gate remains held')
    print('VERIFIED',unit_id,'attempt',attempt,name,'gate',flush=True)


class Controller:
    def __init__(self,args,engine,pin):
        self.args,self.engine,self.pin=args,engine,pin
        self.units=engine.state.setdefault('line_wrap_recovery_units',{})

    def recheck(self):
        if (supervision.file_hash(SELF)!=self.pin['controller_sha256'] or resolver.dependencies()!=self.pin['v16_dependencies']
                or supervision.file_hash(Path(previous_driver.__file__))!=self.pin['previous_driver_sha256']
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
        if validate_predecessor(self.args)!=self.pin['predecessor']:raise InputBindingConflict('Stopped predecessor evidence changed')
        for name,checksum in self.pin['prepared_inputs'].items():
            if supervision.file_hash(folder/name)!=checksum:raise InputBindingConflict('Prepared recovery evidence changed; retain this queue and use a new namespace')
        inputs=self.pin['folder_inputs'][str(folder.resolve())]
        for name,key in [('assignment.json','assignment_sha256'),('manifest.json','manifest_sha256'),
                         ('frozen.json','frozen_sha256'),('prepared/book.json','prepared_source_sha256')]:
            if supervision.file_hash(folder/name)!=inputs[key]:raise InputBindingConflict('Frozen generation/source inputs changed')

    def stage(self,folder,uid,name,script,flags,gpu=False,semantic=False):
        self.recheck();extra=flags if script==Path(resolver.__file__) else ['--unit',uid,*flags]
        if script==SELF:extra=[*extra,'--prior-worker',str(self.args.prior_worker)]
        command=self.engine.command(script,folder,extra,asr=True)
        self.engine.active_name=name
        key=supervision.digest({'folder':str(folder),'name':name,'command':command})
        while True:
            if gpu:wait_previous(self.engine,self.args,folder,command)
            print('STAGE',uid,name,flush=True)
            try:
                self.engine.run(name,folder,command,kind='asr' if gpu and ('verify' in flags or 'align' in flags) or '--stage' in flags and 'stress' in flags else 'normal')
                return
            except finishing.StageFailure:
                record=self.engine.state.get('stages',{}).get(key,{})
                used=record.get('operational_retry_count',0)
                if semantic or used>=self.args.operational_retries or not operational_failure(record):raise
                record['operational_retry_count']=used+1;self.engine.save()
                print('OPERATIONAL RETRY',uid,name,used+1,'same immutable attempt',flush=True)

    def attempt(self,folder,uid,number):
        prefix='line-wrap-'+uid+'-a'+str(number)+'-';flags=['--attempt',str(number)];script=Path(helper.__file__)
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
        unit=next(u for u in supervision.read(folder/'assignment.json')['units'] if u['id']==uid)
        bound=helper.campaign(folder,unit)
        if bound['stress_witness']['enabled']:
            try:self.stage(folder,uid,prefix+'stress',script,['--stage','stress',*flags])
            except (ownership.OwnershipConflict,InputBindingConflict):raise
            except finishing.StageFailure as error:self.stress_error(folder,unit,number,bound,str(error))
        self.stage(folder,uid,prefix+'validate',script,['--stage','validate',*flags],semantic=True)
        outcome=helper.validate_stress_record(folder,unit,number,bound,required=bound['stress_witness']['enabled'])
        return {'attempt':number,'word_pcm_valid':True,'stress':outcome}

    def stress_error(self,folder,unit,number,bound,error):
        # Preserve lexical/PCM acceptance when the optional CPU witness fails.
        # Reproduce its strict inputs before writing an explicitly nonholding error.
        path=helper.attempt_home(folder,unit,number)/'stress-witness.json'
        if path.exists():return helper.validate_stress_record(folder,unit,number,bound,required=True)
        helper.validate_line_wrap_receipt(folder,unit,helper.read(helper.receipt_path(folder,unit,number)))
        inputs=helper.stress_inputs(folder,unit,number,bound)
        helper.immutable(path,{'inputs':inputs,'outcome':helper.stress_outcome(None,number,'Controller CPU witness stage failed: '+error),
            'cpu_isolated':True,'controller_sha256':supervision.file_hash(SELF)})
        return helper.validate_stress_record(folder,unit,number,bound,required=True)


    def handoff(self,folder):
        import psutil
        self.recheck();self.engine.active_name='terminal-handoff-resource-check'
        wait_previous(self.engine,self.args,folder,[])
        identities=assert_stage_workers_stopped(self.engine.state.get('stages',{}))
        if any(self.units[uid].get('status') not in ('held','registered_pending_revalidation') for uid in self.args.unit):
            raise ValueError('No verified/complete status is permitted in deferred mode')
        path=self.args.output/'deferred-handoff.json'
        value={'schema':1,'policy':'public-demo-line-wrap-controller-deferred-handoff-v1','controller_sha256':supervision.file_hash(SELF),
            'configuration_sha256':supervision.file_hash(self.args.output/'line-wrap-recovery-controller-config.json'),
            'folder':str(folder),'units':copy.deepcopy(self.units),'canonical_receipts':handoff_inventory(folder,self.args.unit),
            'selection_only_baseline_sha256':self.pin['selection_only_baseline_sha256'],
            'controller_identity':{'pid':os.getpid(),'create_time':psutil.Process().create_time(),'command':psutil.Process().cmdline()},
            'stopped_stage_identities':identities,'stage_workers_stopped':True,'controller_exit_pending':True,
            'approval_pending':True,'book_complete':False,'publication':False,'github_publication':False,
            'scope_complete':False,'counts_not_recomputed':True,'next_step':'Latest unified versioned report must revalidate all source/audio evidence'}
        helper.immutable(path,value)
        self.engine.state.update(status='awaiting_unified_revalidation',terminal_handoff=True,scope_complete=False,book_complete=False,
            publication=False,github_publication=False,handoff_sha256=supervision.file_hash(path),handoff_receipt=str(path),
            counts_not_recomputed=True,target_scope=list(self.args.unit))
        self.engine.book(folder,status='awaiting_unified_revalidation',process_running=False,reason='No approval until latest unified versioned report',
                         stage_workers_stopped=True,handoff_sha256=supervision.file_hash(path))
        self.engine.save();return 0

    def run(self):
        folder=self.args.folder[0].resolve()
        try:
            self.recheck();snapshot=baseline(self.args)
            if supervision.digest(snapshot)!=self.pin['selection_only_baseline_sha256']:raise InputBindingConflict('Selection-only baseline changed')
            for uid in self.args.unit:
                state=self.units.setdefault(uid,{'attempts':{},'word_valid_candidates':[]})
                row=next(u for u in snapshot['report']['units'] if u['id']==uid)
                if row.get('selected'):
                    state.update(status='registered_pending_revalidation',selection_reason='Selection-only prior report; full revalidation still required');self.engine.save();continue
                if uid in self.pin['review_holds']:
                    state.update(status='held',reason=self.pin['review_holds'][uid]['reason'],review_hold=self.pin['review_holds'][uid]);self.engine.save();continue
                if state.get('status')=='registered_pending_revalidation':continue
                self.engine.book(folder,status='running',unit=uid,process_running=False)
                for number in (1,2,3):
                    history=state['attempts'].setdefault(str(number),{})
                    if history.get('status')=='held':continue
                    if helper.CANDIDATE not in self.pin['approved_choices'][uid]:
                        history.update(status='held',reason='Fixed input lacks two qualified independent approvals');self.engine.save();continue
                    try:
                        if history.get('status')!='word_pcm_valid':
                            history.update(status='running',started_unix=time.time());state['status']='running';self.engine.save()
                            candidate=self.attempt(folder,uid,number)
                            state['word_valid_candidates']=[c for c in state['word_valid_candidates'] if c['attempt']!=number]+[candidate]
                            history.update(status='word_pcm_valid',finished_unix=time.time(),candidate=candidate);self.engine.save()
                        candidate=history['candidate']
                        # Only a measured suspicion requests another bounded profile.
                        if not candidate['stress'].get('retake_trigger',False):break
                    except (ownership.OwnershipConflict,InputBindingConflict) as error:
                        history.update(status='blocked_ownership',reason=str(error));self.engine.save();raise
                    except finishing.StageFailure as error:
                        history.update(status='held',reason=str(error),finished_unix=time.time());self.engine.save()
                        print('HELD',uid,'attempt',number,str(error),flush=True)
                selected=helper.select_word_valid_candidate(state['word_valid_candidates'])
                if selected is not None:
                    unit=next(u for u in supervision.read(folder/'assignment.json')['units'] if u['id']==uid)
                    self.stage(folder,uid,'line-wrap-'+uid+'-selected-a'+str(selected['attempt'])+'-register',Path(resolver.__file__),
                        ['--stage','register','--receipt',str(helper.receipt_path(folder,unit,selected['attempt']))],semantic=True)
                    state.update(status='registered_pending_revalidation',selected_candidate=selected,
                        selection_policy=helper.STRESS_SELECTION,stress_approval=False);self.engine.save()
                else:state.update(status='held',reason='All three source-bound native profiles failed word/PCM checks');self.engine.save()
            if self.args.defer_final_report:return self.handoff(folder)
            self.recheck();snapshot=self.engine.resolve_v16(folder);self.recheck()
            for uid in self.args.unit:
                state=self.units[uid];selected=next(u for u in snapshot['report']['units'] if u['id']==uid).get('selected')
                state.update(status='verified' if selected else 'held',selected=selected)
            success=all(self.units[uid]['status']=='verified' for uid in self.args.unit)
            self.engine.state.update(status='complete' if success else 'held',scope_complete=success,book_complete=False,
                publication=False,github_publication=False,target_scope=list(self.args.unit),
                final_counts={k:snapshot['report'][k] for k in ('verified_units','open_recognition_flags','pending_units')})
            self.engine.book(folder,status='targets_verified' if success else 'held',process_running=False,
                reason='Requested target scope only; whole-book completion remains independently checked',report_sha256=snapshot['report_sha256'])
            self.engine.save();return 0 if success else 1
        except Exception as error:
            self.engine.state.update(status='held',error=str(error));self.engine.book(folder,status='held',process_running=False,reason=str(error));self.engine.save();raise



def main():
    os.environ.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8');parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,action='append',required=True);parser.add_argument('--unit',action='append',default=[])
    parser.add_argument('--output',type=Path);parser.add_argument('--prior-worker',type=Path,required=True);parser.add_argument('--python',type=Path,default=ROOT/'training-work/.venv-tts/Scripts/python.exe')
    parser.add_argument('--asr-python',type=Path,default=ROOT/'training-work/.venv-asr/Scripts/python.exe')
    parser.add_argument('--poll-seconds',type=float,default=5);parser.add_argument('--stall-seconds',type=float,default=600)
    parser.add_argument('--verifier-initial-seconds',type=float,default=1800);parser.add_argument('--wait-deadline-seconds',type=float,default=43200)
    parser.add_argument('--defer-final-report',action='store_true',help='Terminal handoff; latest versioned owner revalidates all registrations before approval')
    parser.add_argument('--operational-retries',type=int,choices=[0,1],default=1)
    parser.add_argument('--internal-gate',choices=['context','crop'],help=argparse.SUPPRESS);parser.add_argument('--native-gate',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--attempt',type=int,choices=[1,2,3],help=argparse.SUPPRESS);args=parser.parse_args()
    if len(args.folder)!=1:parser.error('Exactly one Russian demo folder is required')
    args.folder=[args.folder[0].resolve()]
    if not args.unit:args.unit=list(TARGETS)
    if len(set(args.unit))!=len(args.unit) or any(uid not in TARGETS for uid in args.unit):parser.error('Only distinct u0132/u0133 targets are authorized')
    if args.internal_gate or args.native_gate:
        if len(args.unit)!=1 or args.attempt is None:parser.error('One explicit unit and attempt required for gate')
        internal_gate(args.folder[0],args.unit[0],args.attempt,args.internal_gate);return 0
    if args.output is None:parser.error('--output required')
    if min(args.poll_seconds,args.stall_seconds,args.verifier_initial_seconds,args.wait_deadline_seconds)<=0:parser.error('Timeouts must be positive')
    args.prior_worker=args.prior_worker.resolve();args.output=args.output.resolve();args.python=args.python.resolve();args.asr_python=args.asr_python.resolve();args.stage_retries=0
    pin=configuration(args);ownership.pin_configuration(args.output/'line-wrap-recovery-controller-config.json',pin)
    if args.defer_final_report and (args.output/'deferred-handoff.json').exists():
        validate_handoff(args.output/'deferred-handoff.json',args.folder[0]);print('RETAINED TERMINAL HANDOFF; latest unified report still required',flush=True);return 0
    return Controller(args,Engine(args),pin).run()


if __name__=='__main__':raise SystemExit(main())
