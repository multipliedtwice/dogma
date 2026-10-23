"""V11: an additive primary-facts review, preserving frozen V10 decisions."""
from __future__ import annotations
import argparse
import copy
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_word_pronunciation as previous
import supplement_public_demo_word_recovery as helper

base=previous.base
finishing=previous.finishing
supervision=previous.supervision
ownership=previous.legacy.ownership.context_driver
SELF=Path(__file__).resolve()
RULE='public-demo-additive-primary-word-facts-resolution-v11'


def dependencies():return {**helper.dependencies(),SELF.relative_to(ROOT).as_posix():base.file_digest(SELF),
                          Path(previous.__file__).relative_to(ROOT).as_posix():base.file_digest(Path(previous.__file__))}


def validate(context,unit,receipt,receipt_sha256):
    spec=receipt['spec'];marker=spec.get(helper.MARKER,{})
    if (context.assignment['language']!='Russian' or next((u for u in context.assignment['units'] if u['id']==unit['id']),None)!=unit
            or receipt.get('unit')!=unit['id'] or type(marker.get('attempt')) is not int or marker['attempt'] not in (1,2,3)
            or receipt.get('take')!=marker['attempt']+14 or marker.get('policy')!=helper.POLICY or receipt.get('quality_passed') is not True
            or base.sha(spec)!=receipt.get('spec_sha256') or spec.get('source')!=unit or spec.get('generation')!=context.generation
            or spec.get('model')!=context.manifest['speech_model'] or spec.get('runner')!=context.manifest['runner']
            or spec.get('reference')!=unit['palette_reference'] or spec.get('effects')!='disabled'
            or any(k in spec for k in ('recovery','context_recovery','pronunciation_recovery','ru_pronunciation_recovery','word_pronunciation_recovery'))):
        raise ValueError('Supplement changed frozen source/model/reference or the three shared native slots')
    proof=helper.validate_supplement_receipt(context.folder,unit,receipt)
    bound,old=helper.prepared_bound(context.folder,unit)
    if (proof['policy']!=helper.POLICY or proof['receipt_sha256']!=receipt_sha256 or proof['native_audio_sha256']!=receipt['audio_sha256']
            or proof['source_spans']!=[{k:unit[k] for k in ('id','source_id','start','end','text')}]
            or old['source_paragraph']!=context.blocks[unit['source_id']] or marker['repair']!=old['repair']
            or spec['request']['text']!=old['repair']['synthesis_text'] or proof['pronunciation_provenance']['repair']!=old['repair']
            or proof['pronunciation_provenance']['stress']!=bound['stress']):raise ValueError('Supplement source/candidate/stress/receipt provenance differs')
    evidence=[]
    for item in proof['target_decision']['evidence']:
        recognizer={'primary':'qwen','witness':'turbo'}.get(item['role'])
        if recognizer is None:raise ValueError('Unexpected supplementary recognizer role')
        check=base.read(base.checked_relative(context.folder,item['path'],item['sha256']))
        raw=helper.check_record(check,unit['spoken'],receipt['audio_sha256'],bound,old,recognizer)
        if raw!=item['raw'] or check['verifier']!=item['verifier']:raise ValueError('Supplement raw check changed')
        evidence.append({k:v for k,v in item.items() if k!='comparison'})
    decision=helper.original.strict_decision(unit,evidence,context.blocks[unit['source_id']],old['repair'])
    if decision!=proof['target_decision'] or decision.get('passed') is not True:
        raise ValueError('Both original-source recognizers must pass unchanged strict ordinary-word rules')
    public=previous.public_safe({k:proof[k] for k in ('policy','receipt_sha256','native_audio_sha256','source_spans','pronunciation_provenance')})
    public.update(adapter_sha256=base.file_digest(SELF),implementation_sha256=base.file_digest(Path(helper.__file__)),
                  actual_native_input=spec['request']['text'],expected_spoken=unit['spoken'],human_approval=False,acoustic_stress_approval=False)
    return decision,public


class Context(previous.Context):
    def __init__(self,folder):super().__init__(folder);self.word_supplement_dependencies=dependencies()
    def recheck(self):
        super().recheck()
        if hasattr(self,'word_supplement_dependencies') and self.word_supplement_dependencies!=dependencies():raise ValueError('Supplement dependencies changed')
    def assess(self,unit,receipt):
        if helper.MARKER not in receipt.get('spec',{}):return super().assess(unit,receipt)
        self.recheck()
        if receipt.get('quality_passed') is not True:return None
        attempt=receipt['spec'][helper.MARKER]['attempt'];canonical=self.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take'])
        private=helper.receipt_path(self.folder,unit,attempt)
        if canonical.read_bytes()!=private.read_bytes() or base.read(canonical)!=receipt:raise ValueError('Supplement canonical receipt is not its byte-exact private mirror')
        base.checked_relative(self.folder,receipt['audio'],receipt['audio_sha256'])
        decision,proof=validate(self,unit,receipt,base.file_digest(canonical))
        return {'schema':1,'rule':RULE,'module_sha256':base.file_digest(SELF),'base_module_sha256':base.file_digest(Path(base.__file__)),
          'dependencies':self.dependencies,'bindings':self.bindings,'word_supplement_extension_dependencies':self.word_supplement_dependencies,
          'unit':unit['id'],'source_id':unit['source_id'],'source_start':unit['start'],'source_end':unit['end'],'source_text':unit['text'],
          'expected_spoken':unit['spoken'],'source_paragraph_sha256':base.sha(self.blocks[unit['source_id']]),'audio_sha256':receipt['audio_sha256'],
          'take_spec_sha256':receipt['spec_sha256'],'take_receipt_sha256':base.file_digest(canonical),'word_supplement_provenance':proof,
          'context_provenance':None,'recovery_provenance':None,'english_source_policy':self.english_policy,'decision':decision,'human_approval':False,
          'scope':'Materially new primary facts after preserved rejection; candidate/stress unchanged; two new independent identity reviews and both blind source checks'}


def register(context,path):
    context.recheck();path=Path(path);path=(path if path.is_absolute() else context.folder/path).resolve();path.relative_to(context.folder)
    raw=path.read_bytes();receipt=base.read(path);unit=next((u for u in context.assignment['units'] if u['id']==receipt.get('unit')),None)
    attempt=receipt.get('spec',{}).get(helper.MARKER,{}).get('attempt')
    if unit is None or type(attempt) is not int or attempt not in (1,2,3) or path!=helper.receipt_path(context.folder,unit,attempt).resolve():raise ValueError('Supplement receipt namespace/three-slot bound changed')
    _,proof=validate(context,unit,receipt,base.file_digest(path));context.recheck()
    if path.read_bytes()!=raw:raise ValueError('Supplement receipt changed during validation')
    target=context.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take']);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()!=raw:raise ValueError('Never overwrite an existing canonical word slot')
    else:
        with target.open('xb') as stream:stream.write(raw)
    record={'schema':1,'rule':RULE,'unit':unit['id'],'take':receipt['take'],'canonical':target.relative_to(context.folder).as_posix(),
            'canonical_receipt_sha256':base.file_digest(target),'adapter_sha256':base.file_digest(SELF),'extension_dependencies':context.word_supplement_dependencies,
            'word_supplement_provenance':proof,'human_approval':False}
    base.immutable(path.parent/'supplement-registration.json',record);return record


def report(context):
    value=previous.report(context);known=set()
    for row in value['units']:
        unit=next(u for u in context.assignment['units'] if u['id']==row['id']);receipts={r['take']:r for r in base.renderer.takes(context.folder,unit)}
        for summary in row['takes']:
            known.update(item['evidence_sha256'] for item in summary['raw_transcripts'])
            if helper.MARKER in receipts[summary['take']]['spec']:summary['word_supplement_provenance']=context.assess(unit,receipts[summary['take']])['word_supplement_provenance']
        if row.get('selected') and row['selected'].get('word_supplement_provenance'):row['word_supplement_provenance']=row['selected']['word_supplement_provenance']
    history=[]
    for unit in context.assignment['units']:
        for attempt in (1,2,3):
            for recognizer in ('qwen','turbo'):
                path=helper.attempt_home(context.folder,unit,attempt)/('native-'+recognizer+'.json')
                if not path.exists() or base.file_digest(path) in known:continue
                bound,old,expected,_,audio=helper.verification_audio(context.folder,unit,attempt)
                raw=helper.check_record(base.read(path),expected,audio,bound,old,recognizer)
                history.append({'unit':unit['id'],'attempt':attempt,'recognizer':recognizer,'heard':raw['heard'],'status':raw['status'],
                                'audio_sha256':audio,'evidence_sha256':base.file_digest(path),'human_approval':False})
    extra=sum(h['status']!='asr_exact_match' for h in history)
    value.update(word_pronunciation_resolution_adapter_sha256=value['resolution_adapter_sha256'],resolution_adapter_sha256=base.file_digest(SELF),
                 word_supplement_rule=RULE,word_supplement_extension_dependencies=context.word_supplement_dependencies,
                 unregistered_word_supplement_history=history,raw_unregistered_word_supplement_mismatch_check_count=extra)
    value['raw_mismatch_check_count']+=extra
    value['disclosure']+=' A rejected lexical proposal remains immutable. A separate supplementary review used materially new primary facts with two independent first evaluations; acceptance was optional. Candidate, source and stress stayed unchanged. '+str(extra)+' unregistered supplementary raw checks remain in the total; no acoustic stress or human approval is implied.'
    return value


def resolve(folder,publish=False,complete=False):
    folder=Path(folder).resolve();context=Context(folder);value=report(context);path=folder/'resolution-proposals'/('word-supplement-report-'+base.sha(value)+'.json')
    base.immutable(path,value);count,seconds=finishing.prefix_seconds(context,value);eligible=seconds>=300 or count==len(context.assignment['units'])
    if publish and eligible:base.publish(context,value,3)
    valid,reason=finishing.validate_completion(folder,context,value) if complete else (False,'Not checked')
    snapshot={'report':value,'report_path':str(path),'report_sha256':base.sha(value),'prefix_units':count,'prefix_seconds':seconds,
              'publication_eligible':eligible,'completion_valid':valid,'completion_reason':reason}
    supervision.write(folder/'finishing/current-resolution.json',snapshot);return snapshot


class Engine(finishing.Engine):
    def refresh(self,folder,prepare=False):
        self.run('word-supplement-refresh',folder,self.command(SELF,folder,['--stage','resolve' if prepare else 'publish'],asr=True))
        return finishing.current_resolution(folder)


def previous_jobs(rows,folders,own_pid):
    busy=previous.previous_jobs(rows,folders,own_pid);by_pid={r['pid']:r for r in rows};ancestors=set()
    while own_pid and own_pid not in ancestors:ancestors.add(own_pid);own_pid=by_pid.get(own_pid,{}).get('ppid',0)
    wanted={p.resolve() for p in folders}
    for row in rows:
        if row['pid'] in ancestors or not row.get('name','').casefold().startswith(('python','pypy')):continue
        command=row.get('command') or [];cwd=Path(row['cwd']);stage=supervision.flag_value(command,'--stage')
        scripts={SELF.name:('all',),Path(helper.__file__).name:('render','verify')}
        matches=[name for name in scripts if any(Path(p).name==name and (Path(p) if Path(p).is_absolute() else cwd/p).resolve()==ROOT/'scripts'/name for p in command[1:])]
        if not any(stage in scripts[name] for name in matches) or SELF.name in matches and '--prepare-only' in command:continue
        targets=[(Path(command[i+1]) if Path(command[i+1]).is_absolute() else cwd/command[i+1]).resolve() for i,item in enumerate(command[:-1]) if item=='--folder']
        if wanted.intersection(targets):busy.append({**row,'other_controller':SELF.name in matches})
    return busy


def wait_previous(engine,args):
    import psutil
    start=time.monotonic()
    while True:
        rows=[]
        for process in psutil.process_iter(['pid','ppid','name']):
            if not (process.info.get('name') or '').casefold().startswith(('python','pypy','audiocpp')):continue
            try:rows.append({'pid':process.pid,'ppid':process.ppid(),'name':process.name(),'command':process.cmdline(),'cwd':process.cwd(),'create_time':process.create_time()})
            except psutil.NoSuchProcess:continue
            except psutil.AccessDenied as error:raise ownership.OwnershipConflict('Cannot inspect GPU/ASR owner') from error
        busy=previous_jobs(rows,args.folder,os.getpid());engine.state['prior_owner_wait']={'busy':busy,'updated_unix':time.time()};engine.save()
        if any(r['other_controller'] for r in busy):raise ownership.OwnershipConflict('Another demo controller is live; refusing mutual waiting')
        if not busy:return
        if time.monotonic()-start>=args.wait_deadline_seconds:raise ownership.OwnershipConflict('Known GPU/ASR owner remains live at deadline')
        print('WAITING for known GPU/ASR owners',[r['pid'] for r in busy],flush=True);time.sleep(args.poll_seconds)


def configuration(args):
    pin=previous.legacy.ownership.context_driver.configuration(args)
    pin.update(supplement_dependencies=dependencies(),supplement_data_path=str(args.supplement_data.resolve()),
               supplement_data_sha256=base.file_digest(args.supplement_data),folder_dependency_sets={})
    for folder in args.folder:
        context=Context(folder);pin['folder_dependency_sets'][str(folder.resolve())]={k:v for k,v in vars(context).items() if k=='dependencies' or k.endswith('_dependencies')}
    return pin


def all_work(args):
    pin=configuration(args);ownership.pin_configuration(args.output/'supplement-controller-config.json',pin);engine=Engine(args);success=True
    waiting=copy.copy(args);waiting.folder=sorted({p.resolve().parent/name for p in args.folder for name in ('en-gift-of-magi','ru-chameleon')})
    def recheck():
        if dependencies()!=pin['supplement_dependencies'] or base.file_digest(args.supplement_data)!=pin['supplement_data_sha256']:raise finishing.StageFailure('Supplement code/data changed; use a new namespace')
    for folder in args.folder:
        folder=folder.resolve();dirty=False;prepared=True
        try:
            snapshot=engine.refresh(folder,args.prepare_only)
            for row in snapshot['report']['units']:
                uid=row['id']
                if uid not in args.unit or row.get('selected'):continue
                state=engine.state.setdefault('supplement_units',{}).setdefault(str(folder)+'::'+uid,{'attempts':{}})
                try:
                    for stage in ('plan','propose'):
                        recheck();flags=['--unit',uid,'--stage',stage]
                        if stage=='plan':flags+=['--supplement-data',str(args.supplement_data)]
                        engine.run('supplement-'+uid+'-'+stage,folder,engine.command(Path(helper.__file__),folder,flags,asr=True),kind='cli' if stage=='propose' else 'normal')
                except finishing.StageFailure as error:state.update(status='held',reason=str(error));engine.save();success=False;prepared=False;continue
                if args.prepare_only:state['status']='prepared';engine.save();continue
                for attempt in (1,2,3):
                    history=state['attempts'].setdefault(str(attempt),{})
                    if history.get('status')=='held':continue
                    try:
                        for stage,recognizer in [('render',None),('verify','qwen'),('verify','turbo'),('validate',None)]:
                            recheck()
                            if stage in ('render','verify'):wait_previous(engine,waiting)
                            flags=['--unit',uid,'--stage',stage,'--attempt',str(attempt)]
                            if recognizer:flags+=['--recognizer',recognizer]
                            dirty=True;engine.run('supplement-'+uid+'-'+str(attempt)+'-'+stage+(recognizer or ''),folder,
                                engine.command(Path(helper.__file__),folder,flags,asr=True),kind='asr' if stage in ('render','verify') else 'normal')
                        unit=next(u for u in supervision.read(folder/'assignment.json')['units'] if u['id']==uid)
                        engine.run('supplement-'+uid+'-'+str(attempt)+'-register',folder,engine.command(SELF,folder,['--stage','register','--receipt',str(helper.receipt_path(folder,unit,attempt))],asr=True))
                        snapshot=engine.refresh(folder);dirty=False
                        if not next(r for r in snapshot['report']['units'] if r['id']==uid).get('selected'):raise finishing.StageFailure('Registered supplement did not pass recomputation')
                        history['status']='verified';state['status']='verified';engine.save();break
                    except ownership.OwnershipConflict:raise
                    except finishing.StageFailure as error:history.update(status='held',reason=str(error));state['status']='held';engine.save()
            if args.prepare_only:engine.book(folder,status='prepared' if prepared else 'held',process_running=False);continue
            if dirty:snapshot=engine.refresh(folder)
            engine.book(folder,status='complete' if snapshot['completion_valid'] else 'held',process_running=False,reason=snapshot['completion_reason']);success=success and snapshot['completion_valid']
        except Exception as error:success=False;engine.book(folder,status='held',process_running=False,reason=str(error))
    engine.state['status']=('prepared' if args.prepare_only else 'complete') if success else 'held';engine.save();return 0 if success else 1


def main():
    os.environ.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8');parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True,action='append');parser.add_argument('--stage',choices=['register','resolve','publish','complete','all'],required=True)
    parser.add_argument('--unit',action='append',default=[]);parser.add_argument('--receipt',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--supplement-data',type=Path);parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--python',type=Path,default=ROOT/'training-work/.venv-tts/Scripts/python.exe');parser.add_argument('--asr-python',type=Path,default=ROOT/'training-work/.venv-asr/Scripts/python.exe')
    parser.add_argument('--wait-finisher',type=Path);parser.add_argument('--wait-deadline-seconds',type=float,default=43200)
    parser.add_argument('--poll-seconds',type=float,default=5);parser.add_argument('--stall-seconds',type=float,default=600);parser.add_argument('--verifier-initial-seconds',type=float,default=1800)
    args=parser.parse_args()
    if args.stage=='all':
        if args.output is None or args.supplement_data is None or not args.unit:parser.error('--output, --supplement-data and --unit required')
        args.output=args.output.resolve();args.supplement_data=args.supplement_data.resolve();args.python=args.python.resolve();args.asr_python=args.asr_python.resolve();args.stage_retries=0
        return all_work(args)
    if len(args.folder)!=1:parser.error('CPU adapter stages require one folder')
    if args.stage=='register':
        if args.receipt is None:parser.error('--receipt required')
        register(Context(args.folder[0].resolve()),args.receipt);return 0
    snapshot=resolve(args.folder[0],args.stage=='publish',args.stage in ('publish','complete'))
    return 1 if args.stage=='complete' and not snapshot['completion_valid'] else 0


if __name__=='__main__':raise SystemExit(main())
