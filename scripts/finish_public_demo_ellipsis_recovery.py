"""V17 bounded child-native/PCM-composite supervisor; no automatic publication."""
from __future__ import annotations
import argparse,copy,importlib.metadata,os,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import assemble_public_demo_ellipsis_recovery_v2 as helper
import resolve_public_demo_ellipsis_assembly_v2 as resolver
import finish_public_audiobook_demos as finishing
import supervise_public_audiobook_demos as supervision
import finish_public_demo_context_recovery as ownership
import finish_public_demo_line_wrap_recovery as previous_driver
prior_driver=previous_driver.prior_driver
SELF=Path(__file__).resolve()
TARGETS=('u0153',)
REPORT_PREFIXES=previous_driver.REPORT_PREFIXES+('boundary-context-report','ellipsis-assembly-report','ellipsis-assembly-v2-report')
class InputBindingConflict(finishing.StageFailure):pass

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


class Engine(previous_driver.Engine):
    def resolve_v17(self,folder):
        original=self.args.stall_seconds
        try:
            self.args.stall_seconds=max(original,1800)
            self.run('v17-unified-source-bound-resolve',folder,self.command(Path(resolver.__file__),folder,['--stage','resolve'],asr=True))
        finally:self.args.stall_seconds=original
        return finishing.current_resolution(folder)

active_stage_pids=previous_driver.active_stage_pids
assert_stage_workers_stopped=previous_driver.assert_stage_workers_stopped


def prepared_inputs(folder,units):
    assignment,_,_=helper.isolated.frozen_context(folder);paths={}
    for uid in units:
        unit=next(u for u in assignment['units'] if u['id']==uid);helper.campaign(folder,unit)
        for path in [helper.home(folder,unit)/'campaign.json',*[helper.words.home(folder,unit)/name for name in ('campaign.json','proposal.json')]]:
            paths[path.relative_to(folder).as_posix()]=supervision.file_hash(path)
    return paths


def validate_predecessor(args):
    receipt=previous_driver.validate_handoff(args.prior_handoff,args.folder[0],require_controller_stopped=True)
    if receipt.get('publication') is not False or receipt.get('book_complete') is not False:raise InputBindingConflict('Expected terminal nonpublishing V16 handoff')
    return {'path':str(args.prior_handoff),'sha256':supervision.file_hash(args.prior_handoff),'receipt':receipt}


def configuration(args):
    value=ownership.configuration(args);context=resolver.Context(args.folder[0]);value.update(controller_sha256=supervision.file_hash(SELF),
        v17_dependencies=resolver.dependencies(),prepared_inputs=prepared_inputs(args.folder[0],args.unit),predecessor=validate_predecessor(args),
        python_sha256=supervision.file_hash(args.python),asr_python_sha256=supervision.file_hash(args.asr_python),
        context_dependency_sets={k:v for k,v in vars(context).items() if k=='dependencies' or k.endswith('_dependencies')},
        requested_units=args.unit,operational_retries=args.operational_retries,defer_final_report=args.defer_final_report,
        selection_only_baseline_sha256=supervision.digest(baseline(args)),publication=False,github_publication=False,
        processing_mode='development-only',native_budget_per_child=3,native_budget_total=6,assembly_budget=3,final_recognizer_pairs=3,
        child_selection_policy='retain-verified-then-unused-child2-before-child1-after-reproduced-parent-failure-v2',parent_selection_policy='three-distinct-verified-child-pairs-max-v2')
    value['controller_dependencies']={Path(module.__file__).relative_to(ROOT).as_posix():supervision.file_hash(Path(module.__file__))
        for module in (finishing,supervision,ownership,previous_driver,prior_driver)}
    value['dependency_files'],value['dependency_packages']=dependency_pins([value['v17_dependencies'],*value['context_dependency_sets'].values(),value['controller_dependencies']])
    value['timeouts']={name:getattr(args,name) for name in ('poll_seconds','stall_seconds','verifier_initial_seconds','wait_deadline_seconds')}
    return value


def internal_gate(folder,uid,attempt,kind,child=None):
    assigned,_,_=helper.isolated.frozen_context(folder);unit=next(u for u in assigned['units'] if u['id']==uid)
    if kind=='native':
        receipt=helper.native_receipt(folder,unit,child,attempt)[-1];passed=receipt.get('quality_passed') is True;value={'quality_passed':passed}
    elif kind=='parent-quality':
        _,receipt=helper.assembly_receipt(folder,unit,attempt);passed=receipt.get('quality_passed') is True;value={'quality_passed':passed}
    else:
        decision=helper.blind_decision(folder,unit,attempt,kind,child);passed=decision['passed'];value={'decision':decision}
    directory=helper.attempt_home(folder,unit,attempt) if child is None else helper.child_home(folder,unit,child,attempt)
    helper.immutable(directory/('ellipsis-controller-'+kind+'-gate.json'),{'controller_sha256':supervision.file_hash(SELF),
        'source_unit':uid,'child':child,'attempt':attempt,'kind':kind,'audio_approval':False,**value})
    if not passed:raise finishing.StageFailure('Strict '+kind+' gate remains held')


class Controller:
    def __init__(self,args,engine,pin):
        self.args,self.engine,self.pin=args,engine,pin;self.units=engine.state.setdefault('ellipsis_recovery_units',{})
    def recheck(self):
        if (supervision.file_hash(SELF)!=self.pin['controller_sha256'] or resolver.dependencies()!=self.pin['v17_dependencies']
                or supervision.file_hash(self.args.python)!=self.pin['python_sha256'] or supervision.file_hash(self.args.asr_python)!=self.pin['asr_python_sha256']):
            raise InputBindingConflict('V17 implementation/interpreter changed')
        for path,checksum in self.pin['dependency_files'].items():
            if supervision.file_hash(ROOT/path)!=checksum:raise InputBindingConflict('Frozen V17 dependency changed')
        for package,version in self.pin['dependency_packages'].items():
            if importlib.metadata.version(package)!=version:raise InputBindingConflict('Frozen V17 package changed')
        if validate_predecessor(self.args)!=self.pin['predecessor']:raise InputBindingConflict('Stopped V16 handoff changed')
        folder=self.args.folder[0]
        for name,checksum in self.pin['prepared_inputs'].items():
            if supervision.file_hash(folder/name)!=checksum:raise InputBindingConflict('Prepared word/source/assembly campaign changed')
        inputs=self.pin['folder_inputs'][str(folder.resolve())]
        for name,key in [('assignment.json','assignment_sha256'),('manifest.json','manifest_sha256'),('frozen.json','frozen_sha256'),('prepared/book.json','prepared_source_sha256')]:
            if supervision.file_hash(folder/name)!=inputs[key]:raise InputBindingConflict('Frozen generation changed')
    def stage(self,folder,uid,name,script,flags,gpu=False,semantic=False):
        self.recheck();extra=flags if script==Path(resolver.__file__) else ['--unit',uid,*flags]
        command=self.engine.command(script,folder,extra,asr=True);self.engine.active_name=name
        key=supervision.digest({'folder':str(folder),'name':name,'command':command})
        while True:
            if gpu:wait_previous(self.engine,self.args,folder,command)
            try:
                self.engine.run(name,folder,command,kind='asr' if gpu and ('verify' in flags or 'align' in flags) else 'normal');return
            except finishing.StageFailure:
                record=self.engine.state.get('stages',{}).get(key,{});used=record.get('operational_retry_count',0)
                if semantic or used>=self.args.operational_retries or not operational_failure(record):raise
                # A physical-start marker prevents repeated native/ASR calls even
                # when this operational retry re-enters the same exact command.
                record['operational_retry_count']=used+1;self.engine.save()
    def child_attempt(self,folder,uid,child,attempt,after_parent=None):
        prefix='ellipsis-'+uid+'-c'+str(child)+'-a'+str(attempt)+'-';flags=['--child',str(child),'--attempt',str(attempt)];script=Path(helper.__file__)
        self.stage(folder,uid,prefix+'render',script,['--stage','render',*flags,*(['--after-parent',str(after_parent)] if after_parent is not None else [])],gpu=True)
        self.stage(folder,uid,prefix+'native-gate',SELF,['--internal-gate','native',*flags],semantic=True)
        for kind in ('context','crop'):
            for recognizer in ('qwen','turbo'):
                self.stage(folder,uid,prefix+kind+'-'+recognizer,script,['--stage','verify','--kind',kind,'--recognizer',recognizer,*flags],gpu=True)
            self.stage(folder,uid,prefix+kind+'-gate',SELF,['--internal-gate',kind,*flags],semantic=True)
            if kind=='context':
                self.stage(folder,uid,prefix+'align',script,['--stage','align',*flags],gpu=True)
                self.stage(folder,uid,prefix+'crop',script,['--stage','crop',*flags],semantic=True)
        self.stage(folder,uid,prefix+'validate',script,['--stage','validate-child',*flags],semantic=True)
        unit=next(u for u in supervision.read(folder/'assignment.json')['units'] if u['id']==uid)
        return helper.validate_child(folder,unit,child,attempt)
    def parent_attempt(self,folder,uid,attempt,children):
        prefix='ellipsis-'+uid+'-parent-a'+str(attempt)+'-';flags=['--attempt',str(attempt)];script=Path(helper.__file__)
        self.stage(folder,uid,prefix+'assemble',script,['--stage','assemble','--left-attempt',str(children['1']),'--right-attempt',str(children['2']),*flags],semantic=True)
        self.stage(folder,uid,prefix+'quality',SELF,['--internal-gate','parent-quality',*flags],semantic=True)
        for recognizer in ('qwen','turbo'):
            self.stage(folder,uid,prefix+recognizer,script,['--stage','verify','--kind','parent','--recognizer',recognizer,*flags],gpu=True)
        self.stage(folder,uid,prefix+'words',SELF,['--internal-gate','parent',*flags],semantic=True)
        self.stage(folder,uid,prefix+'validate',script,['--stage','validate',*flags],semantic=True)
        unit=next(u for u in supervision.read(folder/'assignment.json')['units'] if u['id']==uid)
        self.stage(folder,uid,prefix+'register',Path(resolver.__file__),['--stage','register','--receipt',str(helper.receipt_path(folder,unit,attempt))],semantic=True)
    def handoff(self,folder):
        import psutil
        self.recheck();wait_previous(self.engine,self.args,folder,[]);stopped=assert_stage_workers_stopped(self.engine.state.get('stages',{}))
        canonical=[]
        for uid in self.args.unit:
            for take in (30,31,32):
                path=folder/'takes'/uid/('take-%02d.json'%take)
                if path.exists():canonical.append({'path':path.relative_to(folder).as_posix(),'sha256':supervision.file_hash(path)})
        value={'schema':1,'policy':'public-demo-ellipsis-controller-deferred-handoff-v1','controller_sha256':supervision.file_hash(SELF),
            'configuration_sha256':supervision.file_hash(self.args.output/'ellipsis-controller-config.json'),'folder':str(folder),
            'units':copy.deepcopy(self.units),'canonical_receipts':canonical,'stage_workers_stopped':True,'stopped_stage_identities':stopped,
            'controller_identity':{'pid':os.getpid(),'create_time':psutil.Process().create_time(),'command':psutil.Process().cmdline()},
            'controller_exit_pending':True,'book_complete':False,'scope_complete':False,'publication':False,'approval_pending':True,'counts_not_recomputed':True}
        helper.immutable(self.args.output/'deferred-handoff.json',value)
        self.engine.state.update(status='awaiting_unified_revalidation',book_complete=False,scope_complete=False,publication=False,
            terminal_handoff=True,counts_not_recomputed=True,handoff_sha256=supervision.file_hash(self.args.output/'deferred-handoff.json'))
        self.engine.book(folder,status='awaiting_unified_revalidation',process_running=False,stage_workers_stopped=True);self.engine.save();return 0
    def try_child(self,folder,uid,child,state,after_parent=None):
        key=str(child)
        for attempt in (1,2,3):
            history=state['children'][key]['attempts'].setdefault(str(attempt),{})
            if history.get('status') in ('held','independently_verified_child'):continue
            history.update(status='running',started_unix=time.time(),after_parent=after_parent);state['status']='running';self.engine.save()
            try:
                proof=self.child_attempt(folder,uid,child,attempt,after_parent=after_parent)
                history.update(status='independently_verified_child',proof_sha256=supervision.digest(proof),finished_unix=time.time())
                state['selected_children'][key]=attempt
                state.setdefault('verified_children',{}).setdefault(key,[]).append(attempt)
                self.engine.save();return True
            except (ownership.OwnershipConflict,InputBindingConflict):raise
            except finishing.StageFailure as error:
                history.update(status='held',reason=str(error),finished_unix=time.time());self.engine.save()
        return False

    def run(self):
        folder=self.args.folder[0]
        try:
            self.recheck();snapshot=baseline(self.args)
            for uid in self.args.unit:
                state=self.units.setdefault(uid,{'children':{'1':{'attempts':{}},'2':{'attempts':{}}},'selected_children':{},'verified_children':{},'assemblies':{}})
                if next(u for u in snapshot['report']['units'] if u['id']==uid).get('selected'):
                    state.update(status='registered_pending_revalidation',reason='Prior selection only; latest unified approval still required');continue
                if state.get('status')=='registered_pending_revalidation':continue
                self.engine.book(folder,status='running',unit=uid,process_running=False)
                unit=next(u for u in supervision.read(folder/'assignment.json')['units'] if u['id']==uid)
                for child in (1,2):
                    if str(child) in state['selected_children']:
                        helper.validate_child(folder,unit,child,state['selected_children'][str(child)])
                    else:self.try_child(folder,uid,child,state)
                if set(state['selected_children'])!={'1','2'}:
                    state.update(status='held',reason='At least one phrase exhausted its three physical native profiles');self.engine.save();continue
                while True:
                    pair=[state['selected_children']['1'],state['selected_children']['2']]
                    previous=next(((int(n),h) for n,h in state['assemblies'].items() if h.get('pair')==pair),None)
                    if previous is None:
                        free=next((n for n in (1,2,3) if str(n) not in state['assemblies']),None)
                        if free is None:state.update(status='held',reason='Three distinct parent assemblies exhausted');break
                        number=free;history=state['assemblies'].setdefault(str(number),{'pair':pair,'status':'running'})
                    else:number,history=previous
                    if history.get('status')=='registered_pending_revalidation':state['status']='registered_pending_revalidation';break
                    if history.get('status')!='held':
                        try:
                            self.parent_attempt(folder,uid,number,dict(state['selected_children']))
                            history.update(status='registered_pending_revalidation');state.update(status='registered_pending_revalidation');self.engine.save();break
                        except (ownership.OwnershipConflict,InputBindingConflict):raise
                        except finishing.StageFailure as error:
                            history.update(status='held',reason=str(error));state.update(status='held',reason='Final parent source/quality gate failed');self.engine.save()
                    if len(state['assemblies'])>=3:state.update(status='held',reason='Three distinct parent assemblies exhausted; verified children retained');break
                    advanced=False;authorization_blocked=False
                    for child in (2,1):
                        available=next((n for n in (1,2,3) if state['children'][str(child)]['attempts'].get(str(n),{}).get('status') not in ('held','independently_verified_child')),None)
                        if available is None:continue
                        try:helper.failed_parent_proof(folder,unit,number,child,available)
                        except (ValueError,KeyError,FileNotFoundError) as error:
                            state.update(status='held',reason='No reproduced quality/dual-ASR failure authorizes another child: '+str(error));authorization_blocked=True;break
                        if self.try_child(folder,uid,child,state,after_parent=number):advanced=True;break
                    if authorization_blocked or not advanced:
                        if not authorization_blocked:state.update(status='held',reason='All unused child profiles exhausted; every verified child and failed parent retained')
                        break
                self.engine.save()
            if self.args.defer_final_report:return self.handoff(folder)
            self.recheck();result=self.engine.resolve_v17(folder);self.recheck()
            success=True
            for uid in self.args.unit:
                selected=next(u for u in result['report']['units'] if u['id']==uid).get('selected');self.units[uid].update(status='verified' if selected else 'held',selected=selected);success=success and bool(selected)
            self.engine.state.update(status='complete' if success else 'held',scope_complete=success,book_complete=False,publication=False,
                final_counts={k:result['report'][k] for k in ('verified_units','open_recognition_flags','pending_units')})
            self.engine.book(folder,status='targets_verified' if success else 'held',process_running=False);self.engine.save();return 0 if success else 1
        except Exception as error:
            self.engine.state.update(status='held',error=str(error),book_complete=False,publication=False)
            self.engine.book(folder,status='held',process_running=False,reason=str(error));self.engine.save();raise


def main():
    os.environ.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8');parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,action='append',required=True);parser.add_argument('--unit',action='append',default=[])
    parser.add_argument('--output',type=Path);parser.add_argument('--prior-handoff',type=Path)
    parser.add_argument('--python',type=Path,default=ROOT/'training-work/.venv-asr/Scripts/python.exe');parser.add_argument('--asr-python',type=Path,default=ROOT/'training-work/.venv-asr/Scripts/python.exe')
    parser.add_argument('--poll-seconds',type=float,default=5);parser.add_argument('--stall-seconds',type=float,default=600)
    parser.add_argument('--verifier-initial-seconds',type=float,default=1800);parser.add_argument('--wait-deadline-seconds',type=float,default=43200)
    parser.add_argument('--defer-final-report',action='store_true');parser.add_argument('--operational-retries',type=int,choices=[0,1],default=1)
    parser.add_argument('--internal-gate',choices=['native','context','crop','parent-quality','parent'],help=argparse.SUPPRESS)
    parser.add_argument('--child',type=int,choices=[1,2],help=argparse.SUPPRESS);parser.add_argument('--attempt',type=int,choices=[1,2,3],help=argparse.SUPPRESS);args=parser.parse_args()
    if len(args.folder)!=1:parser.error('Exactly one demo folder required')
    args.folder=[args.folder[0].resolve()]
    if not args.unit:args.unit=list(TARGETS)
    if args.unit!=list(TARGETS):parser.error('Only the explicit u0153 development target')
    if args.internal_gate:
        if args.attempt is None or (args.internal_gate in ('native','context','crop') and args.child is None) or (args.internal_gate in ('parent-quality','parent') and args.child is not None):
            parser.error('Explicit matching child/parent gate identity required')
        internal_gate(args.folder[0],args.unit[0],args.attempt,args.internal_gate,args.child);return 0
    if args.output is None or args.prior_handoff is None:parser.error('--output and --prior-handoff required for controller only')
    if min(args.poll_seconds,args.stall_seconds,args.verifier_initial_seconds,args.wait_deadline_seconds)<=0:parser.error('Timeouts must be positive')
    args.output=args.output.resolve();args.prior_handoff=args.prior_handoff.resolve();args.python=args.python.resolve();args.asr_python=args.asr_python.resolve();args.stage_retries=0
    pin=configuration(args);ownership.pin_configuration(args.output/'ellipsis-controller-config.json',pin)
    if args.defer_final_report and (args.output/'deferred-handoff.json').exists():
        validate_handoff(args.output/'deferred-handoff.json',args.folder[0],require_controller_stopped=True);return 0
    return Controller(args,Engine(args),pin).run()


def validate_handoff(path,folder,require_controller_stopped=True):
    import psutil
    path=Path(path).resolve();folder=Path(folder).resolve();value=supervision.read(path);pin=supervision.read(path.parent/'ellipsis-controller-config.json')
    if (value.get('policy')!='public-demo-ellipsis-controller-deferred-handoff-v1' or value.get('controller_sha256')!=supervision.file_hash(SELF)
            or value.get('configuration_sha256')!=supervision.file_hash(path.parent/'ellipsis-controller-config.json') or value.get('folder')!=str(folder)
            or pin.get('controller_sha256')!=value['controller_sha256'] or pin.get('defer_final_report') is not True
            or value.get('book_complete') is not False or value.get('publication') is not False or value.get('stage_workers_stopped') is not True
            or any(s.get('status') not in ('held','registered_pending_revalidation') for s in value['units'].values())):
        raise ValueError('V17 terminal handoff identity/status differs')
    journal=supervision.read(path.parent/'finishing-supervision.json')
    if journal.get('ellipsis_recovery_units')!=value['units'] or journal.get('handoff_sha256')!=supervision.file_hash(path):raise ValueError('Terminal V17 journal differs')
    assert_stage_workers_stopped(journal.get('stages',{}))
    for row in value['canonical_receipts']:helper.isolated.relative_checked(folder,row['path'],row['sha256'])
    if require_controller_stopped:
        identity=value['controller_identity']
        try:
            if supervision.live(psutil.Process(identity['pid']),identity):raise ownership.OwnershipConflict('V17 controller still live')
        except psutil.NoSuchProcess:pass
        for process in psutil.process_iter(['name']):
            if not (process.info.get('name') or '').lower().startswith('python'):continue
            try:
                if process.cmdline()[1:]==identity['command'][1:]:raise ownership.OwnershipConflict('V17 launcher/core still live')
            except psutil.NoSuchProcess:pass
    return value


if __name__=='__main__':raise SystemExit(main())
