"""V10: generic dictionary/stress-bound word recovery over frozen V9.

Canonical takes 15..17 mirror private receipts exactly. Both original-source
blind recognizers must pass the helper's strict whole-unit decision, including
its narrowly reviewed historical spelling slots and ordinary-word safeguards.
"""
from __future__ import annotations
import argparse
import copy
import importlib
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_word_boundary as previous
import resolve_public_demo_ru_pronunciation as legacy
from resolve_public_demo_pronunciation import public_safe

base=previous.base
finishing=previous.finishing
supervision=previous.supervision
SELF=Path(__file__).resolve()
RULE='public-demo-word-pronunciation-resolution-adapter-v10'


def word_module():
    return importlib.import_module('pronounce_public_audiobook_word_recovery')


def dependencies():
    return {**word_module().dependencies(),**{path.relative_to(ROOT).as_posix():base.file_digest(path)
            for path in (SELF,Path(previous.__file__),Path(legacy.__file__))}}


def validated_word_pronunciation(context,unit,receipt,receipt_sha256=None):
    helper=word_module();spec=receipt['spec'];marker=spec.get('word_pronunciation_recovery')
    if (context.assignment['language']!='Russian'
            or next((assigned for assigned in context.assignment['units'] if assigned['id']==unit['id']),None)!=unit
            or receipt.get('quality_passed') is not True or not isinstance(marker,dict) or marker.get('policy')!=helper.POLICY
            or type(marker.get('attempt')) is not int or marker['attempt'] not in (1,2,3)
            or receipt.get('take')!=marker['attempt']+14 or receipt.get('unit')!=unit['id']
            or any(key in spec for key in ('recovery','context_recovery','pronunciation_recovery','ru_pronunciation_recovery'))
            or base.sha(spec)!=receipt.get('spec_sha256') or spec.get('source')!=unit
            or spec.get('generation')!=context.generation or spec.get('model')!=context.manifest['speech_model']
            or spec.get('runner')!=context.manifest['runner'] or spec.get('reference')!=unit['palette_reference']
            or spec.get('effects')!='disabled'):
        raise ValueError('Word recovery changed frozen source/model/reference or bounded take identity')
    proof=helper.validate_word_pronunciation_receipt(context.folder,unit,receipt)
    bound=helper.campaign(context.folder,unit)
    candidate=helper.repair(unit,bound['patch_unit'])
    stress=helper.validated_stress(context.folder,bound)
    provenance=proof['pronunciation_provenance']
    if (proof.get('policy')!=helper.POLICY or proof.get('native_audio_sha256')!=receipt['audio_sha256']
            or receipt_sha256 is not None and proof.get('receipt_sha256')!=receipt_sha256
            or marker.get('repair')!=candidate or provenance.get('repair')!=candidate
            or provenance.get('stress')!=stress or spec['request']['text']!=candidate['synthesis_text']
            or provenance.get('native_text_sha256')!=base.sha(spec['request']['text'])):
        raise ValueError('Word recovery native patch/stress/audio provenance cannot be reproduced')
    spans=proof.get('source_spans',[])
    if len(spans)!=1 or any(spans[0].get(key)!=unit[key] for key in ('id','source_id','start','end','text')):
        raise ValueError('Word recovery changed the original source span')
    decision=proof['target_decision']
    if (decision.get('passed') is not True or len(decision.get('evidence',[]))!=2
            or {(item['role'],item['family']) for item in decision['evidence']}!={('primary','qwen'),('witness','whisper')}):
        raise ValueError('Word recovery needs BOTH independent original-source recognition checks')
    wanted=base.word_tokens(unit['spoken']);targets={patch['spoken_index'] for patch in candidate['patches']}
    target_words={wanted[index] for index in targets}
    for patch in candidate['patches']:
        if (context.blocks[unit['source_id']][patch['source_start']:patch['source_end']]!=patch['source_surface']
                or wanted[patch['spoken_index']:patch['spoken_index']+1]!=base.word_tokens(patch['source_surface'])):
            raise ValueError('Word patch source coordinates or original spoken slot changed')
    inputs=[]
    for item in decision['evidence']:
        path=base.checked_relative(context.folder,item['path'],item['sha256']);check=base.read(path)
        recognizer='qwen' if item['role']=='primary' else 'turbo'
        raw=helper.check_record(check,unit['spoken'],receipt['audio_sha256'],bound,recognizer)
        config=item['verifier']
        comparison=item['comparison']
        forbidden=[]
        for change in comparison.get('tolerated_deviations',[]):
            start=change.get('source_index',-1);end=start+len(change.get('source_tokens',[]))
            if any(start<=index<end for index in targets) or target_words.intersection(change.get('source_tokens',[])):
                forbidden.append(change)
        if (item['raw']!=raw or check.get('verifier')!=config
                or raw!=base.transcript_check(unit['spoken'],check['heard'],receipt['audio_sha256'],base.sha(config))
                or config.get('expected_text_given_to_recognizer') is not False or config.get('local_files_only') is not True
                or comparison.get('passed') is not True or comparison.get('canonical_words')!=wanted or forbidden):
            raise ValueError('Both word checks must pass original-source rules without patched-word tolerance waiver')
        inputs.append({key:value for key,value in item.items() if key!='comparison'})
    reproduced=helper.strict_decision(unit,inputs,context.blocks[unit['source_id']],candidate)
    if reproduced!=decision or reproduced.get('passed') is not True:
        raise ValueError('Strict whole-source stress/historical-slot decision differs from stored evidence')
    sanitized=public_safe({key:proof[key] for key in ('policy','receipt_sha256','native_audio_sha256','source_spans','pronunciation_provenance')})
    sanitized.update(adapter_sha256=base.file_digest(SELF),implementation_sha256=base.file_digest(Path(helper.__file__)),
                     actual_native_input=spec['request']['text'],expected_spoken=unit['spoken'],human_approval=False,acoustic_stress_approval=False)
    return decision,sanitized


class Context(previous.Context):
    def __init__(self,folder):
        super().__init__(folder);self.word_pronunciation_dependencies=dependencies()

    def recheck(self):
        super().recheck()
        if hasattr(self,'word_pronunciation_dependencies') and dependencies()!=self.word_pronunciation_dependencies:
            raise ValueError('Word recovery adapter/helper dependencies changed')

    def assess(self,unit,receipt):
        if 'word_pronunciation_recovery' not in receipt.get('spec',{}):return super().assess(unit,receipt)
        self.recheck()
        if receipt.get('quality_passed') is not True:return None
        marker=receipt['spec']['word_pronunciation_recovery'];helper=word_module()
        path=self.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take'])
        mirror=helper.receipt_path(self.folder,unit,marker['attempt'])
        if path.read_bytes()!=mirror.read_bytes() or base.read(path)!=receipt:
            raise ValueError('Word canonical take is not its byte-exact private receipt mirror')
        base.checked_relative(self.folder,receipt['audio'],receipt['audio_sha256'])
        decision,proof=validated_word_pronunciation(self,unit,receipt,base.file_digest(path))
        return {'schema':1,'rule':RULE,'module_sha256':base.file_digest(SELF),'base_module_sha256':base.file_digest(Path(base.__file__)),
                'dependencies':self.dependencies,'bindings':self.bindings,'word_pronunciation_extension_dependencies':self.word_pronunciation_dependencies,
                'unit':unit['id'],'source_id':unit['source_id'],'source_start':unit['start'],'source_end':unit['end'],
                'source_text':unit['text'],'expected_spoken':unit['spoken'],'source_paragraph_sha256':base.sha(self.blocks[unit['source_id']]),
                'audio_sha256':receipt['audio_sha256'],'take_spec_sha256':receipt['spec_sha256'],'take_receipt_sha256':base.file_digest(path),
                'word_pronunciation_provenance':proof,'recovery_provenance':None,'context_provenance':None,
                'english_source_policy':self.english_policy,'decision':decision,'human_approval':False,
                'scope':'Explicit dictionary/stress-reviewed native word patches; both recognizers checked against original source'}


def register(context,receipt_path):
    helper=word_module();context.recheck();path=Path(receipt_path)
    path=(path if path.is_absolute() else context.folder/path).resolve();path.relative_to(context.folder)
    original=path.read_bytes();receipt=base.read(path)
    unit=next((unit for unit in context.assignment['units'] if unit['id']==receipt.get('unit')),None)
    attempt=receipt.get('spec',{}).get('word_pronunciation_recovery',{}).get('attempt')
    if unit is None or type(attempt) is not int or attempt not in (1,2,3) or path!=helper.receipt_path(context.folder,unit,attempt).resolve():
        raise ValueError('Only three explicit private word recovery receipts may register')
    _,proof=validated_word_pronunciation(context,unit,receipt,base.file_digest(path));context.recheck()
    if path.read_bytes()!=original:raise ValueError('Word receipt changed during validation')
    target=context.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take']);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()!=original:raise ValueError('Never overwrite a differing canonical word recovery take')
    else:
        with target.open('xb') as stream:stream.write(original)
    record={'schema':1,'rule':RULE,'unit':unit['id'],'take':receipt['take'],'canonical':target.relative_to(context.folder).as_posix(),
            'canonical_receipt_sha256':base.file_digest(target),'adapter_sha256':base.file_digest(SELF),
            'extension_dependencies':context.word_pronunciation_dependencies,'word_pronunciation_provenance':proof,'human_approval':False}
    base.immutable(path.parent/'word-pronunciation-registration.json',record);return record


def private_history(context,known):
    helper=word_module();history=[]
    for unit in context.assignment['units']:
        for attempt in (1,2,3):
            for recognizer in ('qwen','turbo'):
                path=helper.receipt_path(context.folder,unit,attempt).parent/('native-'+recognizer+'.json')
                if not path.exists() or base.file_digest(path) in known:continue
                bound,expected,_,audio=helper.verification_audio(context.folder,unit,attempt,'native')
                raw=helper.check_record(base.read(path),expected,audio,bound,recognizer)
                history.append({'unit':unit['id'],'attempt':attempt,'recognizer':recognizer,'heard':raw['heard'],'status':raw['status'],
                                'audio_sha256':audio,'evidence_sha256':base.file_digest(path),'human_approval':False})
    return history


def report(context):
    value=previous.report(context);known=set()
    for row in value['units']:
        unit=next(unit for unit in context.assignment['units'] if unit['id']==row['id'])
        receipts={receipt['take']:receipt for receipt in base.renderer.takes(context.folder,unit)}
        for summary in row['takes']:
            known.update(item['evidence_sha256'] for item in summary['raw_transcripts'])
            if 'word_pronunciation_recovery' in receipts[summary['take']]['spec']:
                summary['word_pronunciation_provenance']=context.assess(unit,receipts[summary['take']])['word_pronunciation_provenance']
        if row.get('selected') and row['selected'].get('word_pronunciation_provenance'):
            row['word_pronunciation_provenance']=row['selected']['word_pronunciation_provenance']
    history=private_history(context,known);extra=sum(item['status']!='asr_exact_match' for item in history)
    value.update(word_boundary_resolution_adapter_sha256=value['resolution_adapter_sha256'],resolution_adapter_sha256=base.file_digest(SELF),
                 word_pronunciation_rule=RULE,word_pronunciation_extension_dependencies=context.word_pronunciation_dependencies,
                 unregistered_word_pronunciation_history=history,raw_unregistered_word_pronunciation_mismatch_check_count=extra)
    value['raw_mismatch_check_count']+=extra
    value['disclosure']+=' Dictionary-backed stress additions and reviewed historical hard-ц spelling patches are explicit native inputs, with frozen source/captions retained. Stress engine/Opus provenance and two lexical identity reviews are disclosed separately from both blind original-source audio checks. '+str(extra)+' unregistered word-recovery raw mismatches are retained in the total; no acoustic stress or human listening approval is implied.'
    return value


def resolve(folder,publish=False,complete=False):
    folder=Path(folder).resolve();context=Context(folder);value=report(context)
    destination=folder/'resolution-proposals'/('word-pronunciation-report-'+base.sha(value)+'.json');base.immutable(destination,value)
    count,seconds=finishing.prefix_seconds(context,value);eligible=seconds>=300 or count==len(context.assignment['units'])
    if publish and eligible:base.publish(context,value,3)
    valid,reason=finishing.validate_completion(folder,context,value) if complete else (False,'Not checked')
    snapshot={'report':value,'report_path':str(destination),'report_sha256':base.sha(value),'prefix_units':count,
              'prefix_seconds':seconds,'publication_eligible':eligible,'completion_valid':valid,'completion_reason':reason}
    supervision.write(folder/'finishing/current-resolution.json',snapshot);return snapshot


class Engine(finishing.Engine):
    def snapshot(self,folder):
        self.run('word-recovery-snapshot',folder,self.command(SELF,folder,['--stage','resolve'],asr=True))
        return finishing.current_resolution(folder)

    def refresh(self,folder):
        # One recomputation also publishes the eligible prefix and checks final
        # completion with this very Context/report. No second full-book pass.
        self.run('word-recovery-refresh',folder,self.command(SELF,folder,['--stage','publish'],asr=True))
        return finishing.current_resolution(folder)


def controller_configuration(args):
    pin=legacy.ownership.context_driver.configuration(args)
    pin.update(word_recovery_dependencies=dependencies(),patch_data_path=str(args.patch_data.resolve()),
               patch_data_sha256=base.file_digest(args.patch_data),folder_dependency_sets={})
    for folder in args.folder:
        context=Context(folder)
        pin['folder_dependency_sets'][str(folder.resolve())]={key:value for key,value in vars(context).items()
                                                            if key=='dependencies' or key.endswith('_dependencies')}
    return pin


def previous_jobs(rows,folders,own_pid):
    busy=legacy.previous_jobs(rows,folders,own_pid);by_pid={row['pid']:row for row in rows};ancestors=set()
    while own_pid and own_pid not in ancestors:
        ancestors.add(own_pid);own_pid=by_pid.get(own_pid,{}).get('ppid',0)
    wanted={folder.resolve() for folder in folders}
    scripts={SELF.name:('all',),'pronounce_public_audiobook_word_recovery.py':('render','verify'),
             'witness_public_demo_wav2vec2.py':('witness',),'witness_public_demo_wav2vec2_ru.py':('witness',)}
    for row in rows:
        if row['pid'] in ancestors or not row.get('name','').casefold().startswith(('python','pypy')):continue
        command=row.get('command') or [];cwd=Path(row['cwd'])
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
            except psutil.AccessDenied as error:raise legacy.ownership.context_driver.OwnershipConflict('Cannot inspect demo GPU/ASR owner identity') from error
        busy=previous_jobs(rows,args.folder,os.getpid());journal=None
        if args.wait_finisher:
            try:
                old=supervision.read(args.wait_finisher);pid=old.get('supervisor_pid')
                journal={'path':str(args.wait_finisher),'claimed_pid':pid,'claimed_status':old.get('status'),
                         'actual_matching_process':next((row for row in busy if row['pid']==pid),None)}
            except (OSError,ValueError):journal={'path':str(args.wait_finisher),'status':'missing_or_unreadable'}
        engine.state['prior_owner_wait']={'updated_unix':time.time(),'busy':busy,'journal':journal};engine.save()
        if any(row['other_controller'] for row in busy):
            raise legacy.ownership.context_driver.OwnershipConflict('Another demo controller is live; refusing mutual waiting. Resume this namespace after it stops.')
        if not busy:return
        if time.monotonic()-started>=args.wait_deadline_seconds:
            raise legacy.ownership.context_driver.OwnershipConflict('Prior demo GPU/ASR owner remains live; refusing overlap at deadline')
        print('WAITING for prior demo GPU/ASR owners',[row['pid'] for row in busy],flush=True);time.sleep(args.poll_seconds)


def all_work(args):
    helper=word_module();pin=controller_configuration(args)
    legacy.ownership.context_driver.pin_configuration(args.output/'word-recovery-controller-config.json',pin)
    engine=Engine(args);success=True;wait_args=copy.copy(args)
    wait_args.folder=sorted({folder.resolve().parent/name for folder in args.folder for name in ('en-gift-of-magi','ru-chameleon')})
    def recheck_inputs():
        if base.file_digest(args.patch_data)!=pin['patch_data_sha256'] or dependencies()!=pin['word_recovery_dependencies']:
            raise finishing.StageFailure('Controller word patch data or code changed; a new namespace is required')
    for folder in args.folder:
        folder=folder.resolve();book_prepared=True;dirty=False
        try:
            snapshot=engine.snapshot(folder) if args.prepare_only else engine.refresh(folder)
            for row in snapshot['report']['units']:
                uid=row['id']
                if (args.unit and uid not in args.unit) or row.get('selected'):continue
                state=engine.state.setdefault('word_recovery_units',{}).setdefault(str(folder)+'::'+uid,{'attempts':{}})
                try:
                    recheck_inputs()
                    for stage in ('plan','propose'):
                        flags=['--unit',uid,'--stage',stage]
                        if stage=='plan':flags+=['--patch-data',str(args.patch_data)]
                        engine.run('word-recovery-'+uid+'-'+stage,folder,engine.command(Path(helper.__file__),folder,flags,asr=True),
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
                            recheck_inputs()
                            if stage in ('render','verify'):wait_previous(engine,wait_args)
                            flags=['--unit',uid,'--stage',stage,'--attempt',str(attempt)]
                            if recognizer:flags+=['--recognizer',recognizer]
                            dirty=True
                            engine.run('word-recovery-'+uid+'-'+str(attempt)+'-'+stage+(recognizer or ''),folder,
                                       engine.command(Path(helper.__file__),folder,flags,asr=True),kind='asr' if stage in ('render','verify') else 'normal')
                        unit=next(unit for unit in supervision.read(folder/'assignment.json')['units'] if unit['id']==uid)
                        engine.run('word-recovery-'+uid+'-'+str(attempt)+'-register',folder,
                                   engine.command(SELF,folder,['--stage','register','--receipt',str(helper.receipt_path(folder,unit,attempt))],asr=True))
                        snapshot=engine.refresh(folder);dirty=False
                        if not next(row for row in snapshot['report']['units'] if row['id']==uid).get('selected'):
                            raise finishing.StageFailure('Registered word take did not pass source-bound recomputation')
                        history.update(status='verified',finished_unix=time.time());state['status']='verified';engine.save();break
                    except legacy.ownership.context_driver.OwnershipConflict:raise
                    except finishing.StageFailure as error:
                        history.update(status='held',reason=str(error),finished_unix=time.time());state['status']='held';engine.save()
            if args.prepare_only:
                engine.book(folder,status='prepared' if book_prepared else 'held',process_running=False,
                            reason='CPU preparation only; no GPU stages started' if book_prepared else 'Some CPU preparations held; evidence retained');continue
            if dirty:snapshot=engine.refresh(folder)
            engine.book(folder,status='complete' if snapshot['completion_valid'] else 'held',process_running=False,reason=snapshot['completion_reason'])
            success=bool(snapshot['completion_valid']) and success
        except Exception as error:
            success=False;engine.book(folder,status='held',process_running=False,reason=str(error))
    engine.state['status']=('prepared' if args.prepare_only else 'complete') if success else 'held';engine.save();return 0 if success else 1


def main():
    os.environ['PYTHONUTF8']='1';os.environ['PYTHONIOENCODING']='utf-8'
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--folder',type=Path,required=True,action='append')
    parser.add_argument('--stage',choices=['register','resolve','publish','complete','all'],required=True);parser.add_argument('--receipt',type=Path)
    parser.add_argument('--unit',action='append',default=[]);parser.add_argument('--output',type=Path);parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--patch-data',type=Path);parser.add_argument('--python',type=Path,default=ROOT/'training-work/.venv-tts/Scripts/python.exe')
    parser.add_argument('--asr-python',type=Path,default=ROOT/'training-work/.venv-asr/Scripts/python.exe');parser.add_argument('--wait-finisher',type=Path)
    parser.add_argument('--wait-deadline-seconds',type=float,default=43200);parser.add_argument('--poll-seconds',type=float,default=5)
    parser.add_argument('--stall-seconds',type=float,default=600);parser.add_argument('--verifier-initial-seconds',type=float,default=1800)
    args=parser.parse_args()
    if args.stage=='all':
        if args.output is None or args.patch_data is None:parser.error('--output and --patch-data are required for durable all-stage work')
        args.output=args.output.resolve();args.patch_data=args.patch_data.resolve();args.python=args.python.resolve();args.asr_python=args.asr_python.resolve();args.stage_retries=0
        return all_work(args)
    if len(args.folder)!=1:parser.error('CPU adapter stages require one folder')
    folder=args.folder[0].resolve()
    if args.stage=='register':
        if args.receipt is None:parser.error('--receipt is required')
        register(Context(folder),args.receipt);return 0
    value=resolve(folder,args.stage=='publish',args.stage in ('publish','complete'))
    print(json.dumps({key:value[key] for key in ('report_sha256','prefix_units','completion_valid','completion_reason')},ensure_ascii=False),flush=True)
    return 1 if args.stage=='complete' and not value['completion_valid'] else 0


if __name__=='__main__':raise SystemExit(main())
