"""V7 source-bound Russian pronunciation receipts; all earlier adapters stay fixed.

Only the reviewed normative adjective-ending native respelling is admitted.
Both blind recognizers must pass the original source through unchanged Russian
rules. Canonical takes 12..14 are byte-exact append-only private receipt mirrors.
"""
from __future__ import annotations
import argparse
import copy
import importlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_acoustic_adjudication as previous
import resolve_public_demo_pronunciation as ownership
from resolve_public_demo_pronunciation import public_safe

base=previous.base
finishing=previous.finishing
supervision=previous.supervision
SELF=Path(__file__).resolve()
RULE='public-demo-ru-pronunciation-resolution-adapter-v7'


def ru_module():
    return importlib.import_module('pronounce_public_audiobook_ru_recovery')


def dependencies():
    return {**ru_module().dependencies(),**{path.relative_to(ROOT).as_posix():base.file_digest(path)
            for path in (SELF,Path(previous.__file__))}}


def validated_ru_pronunciation(context,unit,receipt,receipt_sha256=None):
    helper=ru_module();spec=receipt['spec'];marker=spec.get('ru_pronunciation_recovery')
    if (context.assignment['language']!='Russian'
            or next((assigned for assigned in context.assignment['units'] if assigned['id']==unit['id']),None)!=unit
            or receipt.get('quality_passed') is not True
            or not isinstance(marker,dict) or marker.get('policy')!=helper.POLICY
            or type(marker.get('attempt')) is not int or marker['attempt'] not in (1,2,3)
            or receipt.get('take')!=marker['attempt']+11 or receipt.get('unit')!=unit['id']
            or any(key in spec for key in ('recovery','context_recovery','pronunciation_recovery'))
            or base.sha(spec)!=receipt.get('spec_sha256') or spec.get('source')!=unit
            or spec.get('generation')!=context.generation or spec.get('model')!=context.manifest['speech_model']
            or spec.get('runner')!=context.manifest['runner'] or spec.get('reference')!=unit['palette_reference']
            or spec.get('effects')!='disabled'):
        raise ValueError('RU pronunciation receipt changed frozen source/model/reference or bounded identity')
    original=unit.get('synthesis_text',unit['spoken'])
    matches=list(re.finditer(r'(?<!\w)дровяно́го(?!\w)',original))
    if len(matches)!=1:
        raise ValueError('RU pronunciation requires the unique already-frozen stressed adjective')
    actual=spec['request']['text'];expected=original[:matches[0].start()]+'дровяно́во'+original[matches[0].end():]
    if (actual!=expected or len(actual)!=len(original)
            or [i for i,c in enumerate(actual) if c=='́']!=[i for i,c in enumerate(original) if c=='́']):
        raise ValueError('Only the normative single native г→в change may occur; frozen stress must remain')
    proof=helper.validate_ru_pronunciation_receipt(context.folder,unit,receipt)
    if (proof.get('policy')!=helper.POLICY or proof.get('native_audio_sha256')!=receipt['audio_sha256']
            or receipt_sha256 is not None and proof.get('receipt_sha256')!=receipt_sha256):
        raise ValueError('RU pronunciation proof differs from exact receipt/audio')
    spans=proof.get('source_spans',[])
    if len(spans)!=1 or any(spans[0].get(key)!=unit[key] for key in ('id','source_id','start','end','text')):
        raise ValueError('RU pronunciation proof changed original source span')
    decision=proof['target_decision']
    if (decision.get('passed') is not True or len(decision.get('evidence',[]))!=2
            or {(item['role'],item['family']) for item in decision['evidence']}!={('primary','qwen'),('witness','whisper')}):
        raise ValueError('RU pronunciation needs BOTH independent original-source recognition checks')
    wanted=base.word_tokens(unit['spoken'])
    for item in decision['evidence']:
        path=base.checked_relative(context.folder,item['path'],item['sha256']);check=base.read(path);config=item['verifier']
        raw=base.transcript_check(unit['spoken'],check['heard'],receipt['audio_sha256'],base.sha(config))
        comparison=base.russian_equivalence(unit,raw['heard'],context.blocks[unit['source_id']],
                                            raw['audio_sha256'],raw['verifier_sha256'])
        target_waived=any('дровяного' in base.word_tokens(' '.join(change.get('source_tokens',[])))
                          for change in comparison.get('tolerated_deviations',[]))
        if (item['raw']!=raw or check.get('verifier')!=config or any(check.get(key)!=value for key,value in raw.items())
                or config.get('expected_text_given_to_recognizer') is not False or config.get('local_files_only') is not True
                or config.get('language')!='Russian' or comparison.get('passed') is not True
                or comparison.get('canonical_words')!=wanted or comparison!=item.get('comparison') or target_waived):
            raise ValueError('Both RU blind checks must reproduce original-source Russian rules without adjective name waiver')
    sanitized=public_safe({key:proof[key] for key in ('policy','receipt_sha256','native_audio_sha256','source_spans','pronunciation_provenance')})
    sanitized.update(adapter_sha256=base.file_digest(SELF),implementation_sha256=base.file_digest(Path(helper.__file__)),
                     actual_native_input=actual,expected_spoken=unit['spoken'],human_approval=False)
    return decision,sanitized


class Context(previous.Context):
    def __init__(self,folder):
        super().__init__(folder)
        self.ru_pronunciation_dependencies=dependencies()

    def recheck(self):
        super().recheck()
        if hasattr(self,'ru_pronunciation_dependencies') and dependencies()!=self.ru_pronunciation_dependencies:
            raise ValueError('RU pronunciation adapter/helper dependencies changed')

    def assess(self,unit,receipt):
        if 'ru_pronunciation_recovery' not in receipt.get('spec',{}):
            return super().assess(unit,receipt)
        self.recheck()
        if receipt.get('quality_passed') is not True:return None
        marker=receipt['spec']['ru_pronunciation_recovery'];helper=ru_module()
        path=self.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take'])
        mirror=helper.receipt_path(self.folder,unit,marker['attempt'])
        if path.read_bytes()!=mirror.read_bytes() or base.read(path)!=receipt:
            raise ValueError('RU pronunciation canonical take is not its byte-exact private mirror')
        base.checked_relative(self.folder,receipt['audio'],receipt['audio_sha256'])
        decision,provenance=validated_ru_pronunciation(self,unit,receipt,base.file_digest(path))
        return {'schema':1,'rule':RULE,'module_sha256':base.file_digest(SELF),
                'base_module_sha256':base.file_digest(Path(base.__file__)),'dependencies':self.dependencies,'bindings':self.bindings,
                'ru_pronunciation_extension_dependencies':self.ru_pronunciation_dependencies,
                'unit':unit['id'],'source_id':unit['source_id'],'source_start':unit['start'],'source_end':unit['end'],
                'source_text':unit['text'],'expected_spoken':unit['spoken'],'source_paragraph_sha256':base.sha(self.blocks[unit['source_id']]),
                'audio_sha256':receipt['audio_sha256'],'take_spec_sha256':receipt['spec_sha256'],'take_receipt_sha256':base.file_digest(path),
                'ru_pronunciation_provenance':provenance,'recovery_provenance':None,'context_provenance':None,
                'english_source_policy':self.english_policy,'decision':decision,'human_approval':False,
                'scope':'Explicit normative native respelling; both blind recognizers checked against original source'}


def register(context,receipt_path):
    helper=ru_module();context.recheck()
    path=Path(receipt_path);path=(path if path.is_absolute() else context.folder/path).resolve();path.relative_to(context.folder)
    original=path.read_bytes();receipt=base.read(path)
    unit=next((unit for unit in context.assignment['units'] if unit['id']==receipt.get('unit')),None)
    attempt=receipt.get('spec',{}).get('ru_pronunciation_recovery',{}).get('attempt')
    if unit is None or type(attempt) is not int or attempt not in (1,2,3) or path!=helper.receipt_path(context.folder,unit,attempt).resolve():
        raise ValueError('Only three explicit private RU pronunciation receipts may register')
    _,proof=validated_ru_pronunciation(context,unit,receipt,base.file_digest(path));context.recheck()
    if path.read_bytes()!=original:raise ValueError('RU pronunciation receipt changed during validation')
    target=context.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take']);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()!=original:raise ValueError('Never overwrite a differing canonical RU pronunciation take')
    else:
        with target.open('xb') as stream:stream.write(original)
    record={'schema':1,'rule':RULE,'unit':unit['id'],'take':receipt['take'],
            'canonical':target.relative_to(context.folder).as_posix(),'canonical_receipt_sha256':base.file_digest(target),
            'adapter_sha256':base.file_digest(SELF),'extension_dependencies':context.ru_pronunciation_dependencies,
            'ru_pronunciation_provenance':proof,'human_approval':False}
    base.immutable(path.parent/'ru-pronunciation-registration.json',record)
    return record


def private_history(context,known):
    helper=ru_module();history=[]
    for unit in context.assignment['units']:
        for attempt in (1,2,3):
            for recognizer in ('qwen','turbo'):
                path=helper.receipt_path(context.folder,unit,attempt).parent/('native-'+recognizer+'.json')
                if not path.exists() or base.file_digest(path) in known:continue
                bound,expected,_,audio=helper.verification_audio(context.folder,unit,attempt,'native')
                raw=helper.check_record(base.read(path),expected,audio,bound,recognizer)
                history.append({'unit':unit['id'],'attempt':attempt,'recognizer':recognizer,'heard':raw['heard'],
                                'status':raw['status'],'audio_sha256':audio,'evidence_sha256':base.file_digest(path),'human_approval':False})
    return history


def report(context):
    value=previous.report(context);known=set()
    for row in value['units']:
        unit=next(unit for unit in context.assignment['units'] if unit['id']==row['id'])
        receipts={receipt['take']:receipt for receipt in base.renderer.takes(context.folder,unit)}
        for summary in row['takes']:
            known.update(item['evidence_sha256'] for item in summary['raw_transcripts'])
            if 'ru_pronunciation_recovery' in receipts[summary['take']]['spec']:
                summary['ru_pronunciation_provenance']=context.assess(unit,receipts[summary['take']])['ru_pronunciation_provenance']
        if row.get('selected') and row['selected'].get('ru_pronunciation_provenance'):
            row['ru_pronunciation_provenance']=row['selected']['ru_pronunciation_provenance']
    history=private_history(context,known);extra=sum(item['status']!='asr_exact_match' for item in history)
    value.update(acoustic_adjudication_resolution_adapter_sha256=value['resolution_adapter_sha256'],
                 resolution_adapter_sha256=base.file_digest(SELF),ru_pronunciation_rule=RULE,
                 ru_pronunciation_extension_dependencies=context.ru_pronunciation_dependencies,
                 unregistered_ru_pronunciation_history=history,raw_unregistered_ru_pronunciation_mismatch_check_count=extra)
    value['raw_mismatch_check_count']+=extra
    value['disclosure']+=' Russian pronunciation input may explicitly apply the documented normative adjective ending with frozen stress retained; dictionary and two independent text proposals are disclosed separately from both original-source blind audio checks. '+str(extra)+' additional unregistered RU pronunciation raw mismatches are retained and included in the total. No human listening approval is implied.'
    return value


def resolve(folder,publish=False,complete=False):
    folder=Path(folder).resolve();context=Context(folder);value=report(context)
    destination=folder/'resolution-proposals'/('ru-pronunciation-report-'+base.sha(value)+'.json');base.immutable(destination,value)
    count,seconds=finishing.prefix_seconds(context,value);eligible=seconds>=300 or count==len(context.assignment['units'])
    if publish and eligible:base.publish(context,value,3)
    valid,reason=finishing.validate_completion(folder,context,value) if complete else (False,'Not checked')
    snapshot={'report':value,'report_path':str(destination),'report_sha256':base.sha(value),'prefix_units':count,
              'prefix_seconds':seconds,'publication_eligible':eligible,'completion_valid':valid,'completion_reason':reason}
    supervision.write(folder/'finishing/current-resolution.json',snapshot);return snapshot


class Engine(finishing.Engine):
    def resolve(self,folder,complete=False):
        self.run('ru-pronunciation-complete' if complete else 'ru-pronunciation-resolve',folder,
                 self.command(SELF,folder,['--stage','complete' if complete else 'resolve'],asr=True))
        return finishing.current_resolution(folder)

    def publish_if_eligible(self,folder,snapshot):
        if snapshot['publication_eligible']:
            self.run('ru-pronunciation-publish',folder,self.command(SELF,folder,['--stage','publish'],asr=True))


def controller_configuration(args):
    pin=ownership.context_driver.configuration(args)
    pin['ru_controller_dependencies']=dependencies()
    pin['folder_dependency_sets']={}
    for folder in args.folder:
        context=Context(folder)
        pin['folder_dependency_sets'][str(folder.resolve())]={key:getattr(context,key) for key in (
            'dependencies','extension_dependencies','full_witness_dependencies','beam_witness_dependencies',
            'sustained_sound_dependencies','acoustic_adjudication_dependencies','ru_pronunciation_dependencies')}
    return pin


def previous_jobs(rows,folders,own_pid):
    busy=ownership.previous_jobs(rows,folders,own_pid)
    by_pid={row['pid']:row for row in rows};ancestors=set()
    while own_pid and own_pid not in ancestors:
        ancestors.add(own_pid);own_pid=by_pid.get(own_pid,{}).get('ppid',0)
    wanted={folder.resolve() for folder in folders}
    runners=set()
    for folder in wanted:
        if (folder/'manifest.json').exists():
            runners.add(Path(supervision.read(folder/'manifest.json')['runner']['path']).resolve())
    scripts={SELF.name:('all',),'pronounce_public_audiobook_ru_recovery.py':('render','verify'),
             'witness_public_demo_whisper_full.py':('witness',),'witness_public_demo_whisper_beam.py':('witness',)}
    for row in rows:
        if row['pid'] in ancestors:continue
        command=row.get('command') or [];cwd=Path(row['cwd'])
        if row.get('name','').casefold().startswith('audiocpp') and command:
            binary=Path(command[0]);binary=(binary if binary.is_absolute() else cwd/binary).resolve()
            sequence=supervision.flag_value(command,'--request-sequence')
            if binary in runners and sequence:
                path=Path(sequence);path=(path if path.is_absolute() else cwd/path).resolve()
                if any(path.is_relative_to(folder) for folder in wanted):busy.append({**row,'other_controller':False})
            continue
        if not row.get('name','').casefold().startswith(('python','pypy')):continue
        matches=[name for name in scripts if any(Path(part).name==name and (Path(part) if Path(part).is_absolute() else cwd/part).resolve()==ROOT/'scripts'/name for part in command[1:])]
        if not any(supervision.flag_value(command,'--stage') in scripts[name] for name in matches):continue
        if SELF.name in matches and '--prepare-only' in command:continue
        targets=[]
        for index,item in enumerate(command[:-1]):
            if item=='--folder':
                target=Path(command[index+1]);targets.append((target if target.is_absolute() else cwd/target).resolve())
        if wanted.intersection(targets):busy.append({**row,'other_controller':SELF.name in matches})
    return busy


def wait_previous(engine,args):
    import psutil
    started=time.monotonic()
    while True:
        rows=[]
        for process in psutil.process_iter(['pid','ppid','name']):
            if not (process.info.get('name') or '').casefold().startswith(('python','pypy','audiocpp')):continue
            try:
                rows.append({'pid':process.pid,'ppid':process.ppid(),'name':process.name(),'command':process.cmdline(),
                             'cwd':process.cwd(),'create_time':process.create_time()})
            except psutil.NoSuchProcess:continue
            except psutil.AccessDenied as error:
                raise ownership.context_driver.OwnershipConflict('Cannot inspect Python GPU owner identity') from error
        busy=previous_jobs(rows,args.folder,os.getpid());journal=None
        if args.wait_finisher:
            try:
                old=supervision.read(args.wait_finisher);pid=old.get('supervisor_pid')
                journal={'path':str(args.wait_finisher),'claimed_pid':pid,'claimed_status':old.get('status'),
                         'actual_matching_process':next((row for row in busy if row['pid']==pid),None)}
            except (OSError,ValueError):journal={'status':'missing_or_unreadable','path':str(args.wait_finisher)}
        engine.state['prior_owner_wait']={'updated_unix':time.time(),'busy':busy,'journal':journal};engine.save()
        if any(row['other_controller'] for row in busy):
            raise ownership.context_driver.OwnershipConflict('Another demo controller owns these books; refusing mutual waiting')
        if not busy:return
        if time.monotonic()-started>=args.wait_deadline_seconds:
            raise ownership.context_driver.OwnershipConflict('Existing GPU owner remains live; refusing overlap at deadline')
        print('WAITING for prior demo GPU owners',[row['pid'] for row in busy],flush=True);time.sleep(args.poll_seconds)


def all_work(args):
    helper=ru_module()
    ownership.context_driver.pin_configuration(args.output/'ru-pronunciation-controller-config.json',controller_configuration(args))
    engine=Engine(args);success=True;wait_args=copy.copy(args)
    wait_args.folder=sorted({folder.resolve().parent/name for folder in args.folder for name in ('en-gift-of-magi','ru-chameleon')})
    for folder in args.folder:
        folder=folder.resolve();book_prepared=True
        try:
            snapshot=engine.resolve(folder)
            for row in snapshot['report']['units']:
                uid=row['id']
                if (args.unit and uid not in args.unit) or row.get('selected'):continue
                state=engine.state.setdefault('ru_pronunciation_units',{}).setdefault(str(folder)+'::'+uid,{'attempts':{}})
                try:
                    for stage in ('plan','propose'):
                        engine.run('ru-pronunciation-'+uid+'-'+stage,folder,
                                   engine.command(Path(helper.__file__),folder,['--unit',uid,'--stage',stage],asr=True),
                                   kind='cli' if stage=='propose' else 'normal')
                except finishing.StageFailure as error:
                    state.update(status='held',reason=str(error));engine.save();success=False;book_prepared=False;continue
                if args.prepare_only:
                    state.update(status='prepared',finished_unix=time.time());engine.save();continue
                for attempt in (1,2,3):
                    history=state['attempts'].setdefault(str(attempt),{})
                    if history.get('status')=='held':continue
                    try:
                        for stage,recognizer in [('render',None),('verify','qwen'),('verify','turbo'),('validate',None)]:
                            if stage in ('render','verify'):wait_previous(engine,wait_args)
                            flags=['--unit',uid,'--stage',stage,'--attempt',str(attempt)]
                            if recognizer:flags+=['--recognizer',recognizer]
                            engine.run('ru-pronunciation-'+uid+'-'+str(attempt)+'-'+stage+(recognizer or ''),folder,
                                       engine.command(Path(helper.__file__),folder,flags,asr=True),
                                       kind='asr' if stage in ('render','verify') else 'normal')
                        unit=next(unit for unit in supervision.read(folder/'assignment.json')['units'] if unit['id']==uid)
                        engine.run('ru-pronunciation-'+uid+'-'+str(attempt)+'-register',folder,
                                   engine.command(SELF,folder,['--stage','register','--receipt',str(helper.receipt_path(folder,unit,attempt))],asr=True))
                        snapshot=engine.resolve(folder);engine.publish_if_eligible(folder,snapshot)
                        if not next(row for row in snapshot['report']['units'] if row['id']==uid).get('selected'):
                            raise finishing.StageFailure('Registered RU pronunciation did not pass source-bound recomputation')
                        history.update(status='verified',finished_unix=time.time());state['status']='verified';engine.save();break
                    except ownership.context_driver.OwnershipConflict:raise
                    except finishing.StageFailure as error:
                        history.update(status='held',reason=str(error),finished_unix=time.time());state['status']='held';engine.save()
            if args.prepare_only:
                engine.book(folder,status='prepared' if book_prepared else 'held',process_running=False,
                            reason='CPU preparation only; no GPU stages started' if book_prepared else 'Some requested CPU preparations failed; evidence retained');continue
            snapshot=engine.resolve(folder);engine.publish_if_eligible(folder,snapshot);result=engine.resolve(folder,True)
            engine.book(folder,status='complete' if result['completion_valid'] else 'held',process_running=False,reason=result['completion_reason'])
            success=bool(result['completion_valid']) and success
        except Exception as error:
            success=False;engine.book(folder,status='held',process_running=False,reason=str(error))
    engine.state['status']=('prepared' if args.prepare_only else 'complete') if success else 'held';engine.save()
    return 0 if success else 1


def main():
    os.environ['PYTHONUTF8']='1';os.environ['PYTHONIOENCODING']='utf-8'
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--folder',type=Path,required=True,action='append')
    parser.add_argument('--stage',choices=['register','resolve','publish','complete','all'],required=True);parser.add_argument('--receipt',type=Path)
    parser.add_argument('--unit',action='append',default=[]);parser.add_argument('--output',type=Path);parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--python',type=Path,default=ROOT/'training-work/.venv-tts/Scripts/python.exe')
    parser.add_argument('--asr-python',type=Path,default=ROOT/'training-work/.venv-asr/Scripts/python.exe')
    parser.add_argument('--wait-finisher',type=Path);parser.add_argument('--wait-deadline-seconds',type=float,default=43200)
    parser.add_argument('--poll-seconds',type=float,default=5);parser.add_argument('--stall-seconds',type=float,default=600)
    parser.add_argument('--verifier-initial-seconds',type=float,default=1800)
    args=parser.parse_args()
    if args.stage=='all':
        if args.output is None:parser.error('--output is required for durable all-stage work')
        args.output=args.output.resolve();args.python=args.python.resolve();args.asr_python=args.asr_python.resolve();args.stage_retries=0
        return all_work(args)
    if len(args.folder)!=1:parser.error('CPU adapter stages require one folder')
    folder=args.folder[0].resolve()
    if args.stage=='register':
        if args.receipt is None:parser.error('--receipt is required')
        register(Context(folder),args.receipt);return 0
    value=resolve(folder,args.stage=='publish',args.stage=='complete')
    print(json.dumps({key:value[key] for key in ('report_sha256','prefix_units','completion_valid','completion_reason')},ensure_ascii=False),flush=True)
    return 1 if args.stage=='complete' and not value['completion_valid'] else 0


if __name__=='__main__':raise SystemExit(main())
