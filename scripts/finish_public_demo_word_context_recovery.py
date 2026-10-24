"""Supervise the explicit V12 development recovery for two Russian demo units.

Frozen source/model/helper inputs are checked at every stage. Native attempts
are bounded to the campaign's three choices. Only timeouts or narrowly identified
operational errors may retry a stage; recognition/quality failures never do.
No local or GitHub publication is performed by this controller.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import context_public_demo_word_recovery as helper
import resolve_public_demo_word_context as resolver
import finish_public_audiobook_demos as finishing
import supervise_public_audiobook_demos as supervision
import finish_public_demo_context_recovery as ownership

SELF=Path(__file__).resolve()
TARGETS=('u0132','u0133')


def configuration(args):
    value=ownership.configuration(args)
    context=resolver.Context(args.folder[0])
    value.update(controller_sha256=supervision.file_hash(SELF),v12_dependencies=resolver.dependencies(),
                 context_dependency_sets={k:v for k,v in vars(context).items() if k=='dependencies' or k.endswith('_dependencies')},
                 requested_units=sorted(args.unit),operational_retries=args.operational_retries,publication=False)
    return value


def previous_jobs(rows,folders,own_pid):
    busy=resolver.previous.previous_jobs(rows,folders,own_pid)
    by_pid={row['pid']:row for row in rows};ancestors=set()
    while own_pid and own_pid not in ancestors:ancestors.add(own_pid);own_pid=by_pid.get(own_pid,{}).get('ppid',0)
    wanted={folder.resolve() for folder in folders}
    for row in rows:
        if row['pid'] in ancestors or not row.get('name','').casefold().startswith(('python','pypy')):continue
        command=row.get('command') or [];cwd=Path(row['cwd'])
        matches={name for name in (SELF.name,Path(helper.__file__).name) if any(
            Path(part).name==name and (Path(part) if Path(part).is_absolute() else cwd/part).resolve()==ROOT/'scripts'/name for part in command[1:])}
        if not matches:continue
        controller=SELF.name in matches
        if controller and ('--internal-gate' in command or '--native-gate' in command):continue
        if not controller and supervision.flag_value(command,'--stage') not in ('render','verify','align'):continue
        targets=[(Path(command[i+1]) if Path(command[i+1]).is_absolute() else cwd/command[i+1]).resolve()
                 for i,item in enumerate(command[:-1]) if item=='--folder']
        if wanted.intersection(targets):busy.append({**row,'other_controller':controller})
    return busy


def adopted_stage_pids(engine,folder,command,rows):
    """Exclude only a saved active stage whose *actual* full identity matches."""
    key=supervision.digest({'folder':str(folder),'name':engine.active_name,'command':command})
    attempts=engine.state.get('stages',{}).get(key,{}).get('attempts',[])
    if not attempts or attempts[-1].get('status')!='running':return set()
    identity=attempts[-1]['identity'];actual=next((r for r in rows if r['pid']==identity['pid']),None)
    if (actual is None or actual['create_time']!=identity['create_time'] or actual['command']!=identity['command']
            or actual['command']!=command or identity['folder']!=str(folder)):return set()
    owned={actual['pid']}
    while True:
        children={r['pid'] for r in rows if r['ppid'] in owned and r['create_time']>=identity['create_time']}
        if children<=owned:return owned
        owned|=children


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
    attempts=record.get('attempts',[])
    if not attempts:return False
    latest=attempts[-1]
    if latest.get('timed_out') is True:return True
    try:
        with Path(latest['log']).open('rb') as stream:
            stream.seek(max(0,Path(latest['log']).stat().st_size-12000));tail=stream.read().decode('utf-8',errors='replace')
    except (OSError,KeyError):return False
    return any(token in tail for token in ('torch.OutOfMemoryError: CUDA out of memory','[WinError 1455]',
                                          'Resource temporarily unavailable','subprocess.TimeoutExpired:'))


class Engine(finishing.Engine):
    def resolve_v12(self,folder):
        self.run('v12-source-bound-resolve',folder,self.command(Path(resolver.__file__),folder,['--stage','resolve'],asr=True))
        return finishing.current_resolution(folder)


def internal_gate(folder,unit_id,attempt,kind=None):
    assigned,_,_=helper.isolated.frozen_context(folder);unit=next(u for u in assigned['units'] if u['id']==unit_id)
    directory=helper.attempt_home(folder,unit,attempt)
    if kind is None:
        _,_,receipt=helper.native_receipt(folder,unit,attempt)
        passed=receipt.get('quality_passed') is True
        value={'quality_passed':passed,'native_receipt_sha256':supervision.file_hash(directory/'native-receipt.json')}
        name='native'
    else:
        decision=helper.blind_decision(folder,unit,attempt,kind);passed=decision['passed'];value={'decision':decision};name=kind
    helper.immutable(directory/('word-controller-'+name+'-gate.json'),{'controller_sha256':supervision.file_hash(SELF),
        'source_unit':unit_id,'attempt':attempt,'alignment_is_approval':False,**value})
    if not passed:raise finishing.StageFailure(name+' source/quality gate remains held')
    print('VERIFIED',unit_id,'attempt',attempt,name,'gate',flush=True)


class Controller:
    def __init__(self,args,engine,pin):
        self.args,self.engine,self.pin=args,engine,pin
        self.units=engine.state.setdefault('word_context_units',{})

    def recheck(self):
        if supervision.file_hash(SELF)!=self.pin['controller_sha256'] or resolver.dependencies()!=self.pin['v12_dependencies']:
            raise finishing.StageFailure('Controller or immutable helper inputs changed; use a new namespace')

    def stage(self,folder,uid,name,script,flags,gpu=False,semantic=False):
        self.recheck();command=self.engine.command(script,folder,['--unit',uid,*flags] if script!=Path(resolver.__file__) else flags,asr=True)
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
        prefix='word-context-'+uid+'-a'+str(number)+'-';flags=['--attempt',str(number)];script=Path(helper.__file__)
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

    def run(self):
        folder=self.args.folder[0].resolve();dirty=False
        try:
            snapshot=self.engine.resolve_v12(folder)
            for uid in self.args.unit:
                state=self.units.setdefault(uid,{'attempts':{}})
                row=next(u for u in snapshot['report']['units'] if u['id']==uid)
                if row.get('selected'):
                    state.update(status='verified',selected=row['selected']);self.engine.save();continue
                self.engine.book(folder,status='preparing',unit=uid,process_running=False)
                try:self.stage(folder,uid,'word-context-'+uid+'-plan',Path(helper.__file__),['--stage','plan'],semantic=True)
                except finishing.StageFailure as error:
                    state.update(status='held',reason='Preparation failed: '+str(error));self.engine.save();continue
                for number in (1,2,3):
                    history=state['attempts'].setdefault(str(number),{})
                    if history.get('status')=='held':continue
                    history.update(status='running',started_unix=time.time());state['status']='running';self.engine.save()
                    try:
                        dirty=True;self.attempt(folder,uid,number)
                        snapshot=self.engine.resolve_v12(folder);dirty=False
                        selected=next(u for u in snapshot['report']['units'] if u['id']==uid).get('selected')
                        if not selected:raise finishing.StageFailure('Registered crop failed complete resolver recomputation')
                        history.update(status='verified',finished_unix=time.time());state.update(status='verified',selected=selected);self.engine.save();break
                    except ownership.OwnershipConflict as error:
                        history.update(status='blocked_ownership',reason=str(error));self.engine.save();raise
                    except finishing.StageFailure as error:
                        history.update(status='held',reason=str(error),finished_unix=time.time());state.update(status='held',reason='Bounded strategy remains held');self.engine.save()
                        print('HELD',uid,'attempt',number,str(error),flush=True)
                if state.get('status')!='verified':state.update(status='held',reason='Three source-bound strategies exhausted');self.engine.save()
            if dirty:snapshot=self.engine.resolve_v12(folder)
            success=all(self.units.get(uid,{}).get('status')=='verified' for uid in self.args.unit)
            self.engine.state.update(status='complete' if success else 'held',target_scope=list(self.args.unit),publication=False,
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
    args.output=args.output.resolve();args.python=args.python.resolve();args.asr_python=args.asr_python.resolve();args.stage_retries=0
    pin=configuration(args);ownership.pin_configuration(args.output/'word-context-controller-config.json',pin)
    return Controller(args,Engine(args),pin).run()


if __name__=='__main__':raise SystemExit(main())
