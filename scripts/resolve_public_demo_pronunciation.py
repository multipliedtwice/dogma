"""Versioned pronunciation adapter and bounded driver; original resolvers stay immutable.

Only explicitly marked takes 09..11 use the new source-bound pronunciation proof.
Every other receipt delegates to the original Context unchanged. Independent Qwen
and turbo must recover the ORIGINAL spoken words; unfolded is never enfolded.
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
import resolve_public_demo_recognition as base
import finish_public_audiobook_demos as finishing
import finish_public_demo_context_recovery as context_driver
import supervise_public_audiobook_demos as supervision
from rimsky.speech_quality import transcript_check, word_tokens
from rimsky.util import file_digest

SELF=Path(__file__).resolve()
RULE='public-demo-pronunciation-adapter-v1'


def pronunciation_module():
    return importlib.import_module('pronounce_public_audiobook_recovery')


def extension_dependencies():
    module=pronunciation_module()
    return {**(module.dependencies() if hasattr(module,'dependencies') else {}),
            'scripts/resolve_public_demo_pronunciation.py':file_digest(SELF),
            'scripts/pronounce_public_audiobook_recovery.py':file_digest(Path(module.__file__))}


def public_safe(value):
    excluded={'palette_reference','reference_text','voice_ref','original_spec','request','spec','native_path','model_path'}
    if isinstance(value,dict):
        return {k:public_safe(v) for k,v in value.items() if k not in excluded}
    if isinstance(value,list):
        return [public_safe(v) for v in value]
    if isinstance(value,str) and (re.search(r'[A-Za-z]:[\\/]',value) or 'manual-labeling/' in value.replace('\\','/') or 'training-work/' in value.replace('\\','/')):
        return '[private local path omitted]'
    return value


def validated_pronunciation(context,unit,receipt,receipt_sha256=None):
    module=pronunciation_module()
    spec=receipt['spec']
    marker=spec.get('pronunciation_recovery')
    if (receipt.get('quality_passed') is not True or not isinstance(marker,dict) or marker.get('policy')!=module.POLICY or spec.get('recovery') or spec.get('context_recovery')
            or type(marker.get('attempt')) is not int or marker['attempt'] not in (1,2,3)
            or receipt.get('take')!=marker['attempt']+8 or receipt.get('unit')!=unit['id']
            or base.sha(spec)!=receipt.get('spec_sha256') or spec.get('source')!=unit or spec.get('generation')!=context.generation
            or spec.get('model')!=context.manifest['speech_model'] or spec.get('runner')!=context.manifest['runner']
            or spec.get('reference')!=unit['palette_reference'] or spec.get('effects')!='disabled'):
        raise ValueError('Pronunciation take changed frozen source/model/reference or bounded identity')
    original=unit.get('synthesis_text',unit['spoken'])
    if len(re.findall(r'\benfolded\b',original))!=1 or spec['request']['text']!=re.sub(r'\benfolded\b','in-folded',original):
        raise ValueError('Pronunciation native input must preserve every source word except explicit enfolded→in-folded spelling')
    proof=module.validate_pronunciation_receipt(context.folder,unit,receipt)
    if (proof.get('policy')!=module.POLICY or proof.get('native_audio_sha256')!=receipt['audio_sha256']
            or receipt_sha256 is not None and proof.get('receipt_sha256')!=receipt_sha256):
        raise ValueError('Pronunciation proof differs from the exact raw audio/receipt')
    spans=proof.get('source_spans',[])
    required={k:unit[k] for k in ('id','source_id','start','end','text')}
    if len(spans)!=1 or any(spans[0].get(k)!=v for k,v in required.items()):
        raise ValueError('Pronunciation source span is not the original frozen passage')
    decision=proof['target_decision']
    if (decision.get('passed') is not True or len(decision.get('evidence',[]))!=2 or
            {(e['role'],e['family']) for e in decision['evidence']}!={('primary','qwen'),('witness','whisper')}):
        raise ValueError('Pronunciation requires BOTH independent primary and witness canonical matches')
    expected=word_tokens(unit['spoken'])
    protected=expected.index('enfolded')
    for evidence in decision['evidence']:
        path=base.checked_relative(context.folder,evidence['path'],evidence['sha256'])
        candidate=base.read(path)
        config=evidence['verifier']
        raw=transcript_check(unit['spoken'],candidate['heard'],receipt['audio_sha256'],base.sha(config))
        comparison=evidence.get('comparison',{})
        if (candidate.get('verifier')!=config or evidence['raw']!=raw or any(candidate.get(k)!=v for k,v in raw.items())
                or config.get('expected_text_given_to_recognizer') is not False or config.get('local_files_only') is not True
                or comparison.get('passed') is not True or comparison.get('canonical_words')!=expected
                or len(raw['heard_words'])!=len(expected) or raw['heard_words'][protected]!='enfolded' or 'unfolded' in raw['heard_words']):
            raise ValueError('Both blind recognizers must recover original enfolded; unfolded is never waived')
    sanitized={k:proof[k] for k in ('policy','receipt_sha256','native_audio_sha256','source_spans','pronunciation_provenance')}
    sanitized=public_safe(sanitized)
    sanitized.update(adapter_sha256=file_digest(SELF),pronunciation_module_sha256=file_digest(Path(module.__file__)),
                     actual_native_input=spec['request']['text'],expected_spoken=unit['spoken'],human_approval=False)
    return decision,sanitized


class Context(base.Context):
    def __init__(self,folder):
        super().__init__(folder)
        # Base Context.recheck requires its own dependency dictionary unchanged.
        self.extension_dependencies=extension_dependencies()

    def recheck(self):
        super().recheck()
        if hasattr(self,'extension_dependencies') and extension_dependencies()!=self.extension_dependencies:
            raise ValueError('Versioned pronunciation adapter dependencies changed')

    def assess(self,unit,receipt):
        if not receipt.get('spec',{}).get('pronunciation_recovery'):
            return super().assess(unit,receipt)
        self.recheck()
        if receipt.get('quality_passed') is not True:
            return None
        path=self.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take'])
        mirror=pronunciation_module().receipt_path(self.folder,unit,receipt['spec']['pronunciation_recovery']['attempt'])
        if path.read_bytes()!=mirror.read_bytes() or base.read(path)!=receipt:
            raise ValueError('Canonical pronunciation take is not its byte-exact private receipt mirror')
        base.checked_relative(self.folder,receipt['audio'],receipt['audio_sha256'])
        decision,provenance=validated_pronunciation(self,unit,receipt,file_digest(path))
        return {'schema':1,'rule':RULE,'module_sha256':file_digest(SELF),'base_module_sha256':file_digest(Path(base.__file__)),
                'dependencies':self.dependencies,'extension_dependencies':self.extension_dependencies,'bindings':self.bindings,
                'unit':unit['id'],'source_id':unit['source_id'],'source_start':unit['start'],'source_end':unit['end'],
                'source_text':unit['text'],'expected_spoken':unit['spoken'],'source_paragraph_sha256':base.sha(self.blocks[unit['source_id']]),
                'audio_sha256':receipt['audio_sha256'],'take_spec_sha256':receipt['spec_sha256'],'take_receipt_sha256':file_digest(path),
                'pronunciation_provenance':provenance,'recovery_provenance':None,'context_provenance':None,
                'english_source_policy':self.english_policy,'decision':decision,'human_approval':False,
                'scope':'Versioned explicit pronunciation input; both blind recognizers must retain the original spoken verb'}


def register(context,receipt_path):
    module=pronunciation_module()
    context.recheck()
    path=Path(receipt_path)
    path=path.resolve() if path.is_absolute() else (context.folder/path).resolve()
    path.relative_to(context.folder)
    original=path.read_bytes()
    receipt=base.read(path)
    unit=next((u for u in context.assignment['units'] if u['id']==receipt.get('unit')),None)
    if unit is None:
        raise ValueError('Pronunciation receipt has no exact frozen source unit')
    attempt=receipt.get('spec',{}).get('pronunciation_recovery',{}).get('attempt')
    if type(attempt) is not int or attempt not in (1,2,3) or path!=module.receipt_path(context.folder,unit,attempt).resolve():
        raise ValueError('Only three explicit private pronunciation receipts may be registered')
    _,provenance=validated_pronunciation(context,unit,receipt,file_digest(path))
    context.recheck()
    if path.read_bytes()!=original:
        raise ValueError('Pronunciation receipt changed during validation')
    target=context.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take'])
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()!=original:
            raise ValueError('Canonical pronunciation take already differs; never overwrite it')
    else:
        with target.open('xb') as stream:
            stream.write(original)
    record={'schema':1,'rule':RULE,'take':receipt['take'],'unit':unit['id'],
            'canonical':target.relative_to(context.folder).as_posix(),'canonical_receipt_sha256':file_digest(target),
            'adapter_sha256':file_digest(SELF),'extension_dependencies':context.extension_dependencies,
            'pronunciation_provenance':provenance,'human_approval':False}
    base.immutable(path.parent/'pronunciation-registration.json',record)
    return record


def private_history(context,known_hashes):
    """Retain every unregistered context/pronunciation mismatch without double counting canonical evidence."""
    import context_public_audiobook_recovery as contextual
    rows=[]
    for unit in context.assignment['units']:
        for attempt in (1,2,3):
            entries=[(contextual,kind,recognizer,contextual.attempt_home(context.folder,unit,attempt)/(kind+'-'+recognizer+'.json'))
                     for kind in ('context','crop') for recognizer in ('qwen','turbo')]
            module=pronunciation_module()
            entries.extend((module,'native',recognizer,module.receipt_path(context.folder,unit,attempt).parent/('native-'+recognizer+'.json'))
                           for recognizer in ('qwen','turbo'))
            for worker,kind,recognizer,path in entries:
                if not path.exists() or file_digest(path) in known_hashes:
                    continue
                bound,expected,_,audio=worker.verification_audio(context.folder,unit,attempt,kind)
                check=base.read(path)
                raw=worker.check_record(check,expected,audio,bound,recognizer)
                rows.append({'unit':unit['id'],'attempt':attempt,'scope':'private-context-'+kind if worker is contextual else 'pronunciation',
                             'recognizer':recognizer,'heard':raw['heard'],'status':raw['status'],'audio_sha256':audio,
                             'evidence_sha256':file_digest(path),'human_approval':False})
    return rows


def report(context,only=()):
    value=base.report(context,only)
    known=set()
    for row in value['units']:
        unit=next(u for u in context.assignment['units'] if u['id']==row['id'])
        receipts={r['take']:r for r in base.renderer.takes(context.folder,unit)}
        for summary in row['takes']:
            known.update(e['evidence_sha256'] for e in summary['raw_transcripts'])
            known.update(e['sha256'] for e in (summary.get('context_provenance') or {}).get('full_context_decision',{}).get('evidence',[]))
            receipt=receipts[summary['take']]
            if receipt['spec'].get('pronunciation_recovery'):
                assessed=context.assess(unit,receipt)
                summary.update(pronunciation_provenance=assessed['pronunciation_provenance'],
                               resolution_adapter_sha256=file_digest(SELF),extension_dependencies=context.extension_dependencies)
        if row.get('selected') and row['selected'].get('pronunciation_provenance'):
            row['pronunciation_provenance']=row['selected']['pronunciation_provenance']
    history=private_history(context,known)
    extra=sum(item['status']!='asr_exact_match' for item in history)
    value.update(base_resolution_module_sha256=value['resolution_module_sha256'],resolution_adapter_sha256=file_digest(SELF),
                 resolution_extension_dependencies=context.extension_dependencies,pronunciation_rule=RULE,
                 unregistered_recovery_history=history,raw_unregistered_recovery_mismatch_check_count=extra)
    value['raw_mismatch_check_count']+=extra
    value['disclosure']+=(' Explicit pronunciation inputs and their dictionary/independent proposal evidence are disclosed by the versioned adapter; unfolded is never treated as enfolded. '
                          +str(extra)+' additional raw mismatches from unregistered private recovery checks are included in the total and retained below.')
    return value


def resolve(folder,publish=False,complete=False):
    context=Context(folder)
    value=report(context)
    destination=folder/'resolution-proposals'/('pronunciation-report-'+base.sha(value)+'.json')
    base.immutable(destination,value)
    count,seconds=finishing.prefix_seconds(context,value)
    eligible=seconds>=300 or count==len(context.assignment['units'])
    if publish and eligible:
        base.publish(context,value,3)
    valid,reason=finishing.validate_completion(folder,context,value) if complete else (False,'Not checked')
    result={'report':value,'report_path':str(destination),'report_sha256':base.sha(value),'prefix_units':count,'prefix_seconds':seconds,
            'publication_eligible':eligible,'completion_valid':valid,'completion_reason':reason}
    supervision.write(folder/'finishing/current-resolution.json',result)
    return result


class Engine(finishing.Engine):
    def resolve(self,folder,complete=False):
        self.run('pronunciation-complete' if complete else 'pronunciation-resolve',folder,
                 self.command(SELF,folder,['--stage','complete' if complete else 'resolve'],asr=True))
        return finishing.current_resolution(folder)

    def publish_if_eligible(self,folder,snapshot):
        if snapshot['publication_eligible']:
            self.run('pronunciation-publish',folder,self.command(SELF,folder,['--stage','publish'],asr=True))


def previous_jobs(rows,folders,own_pid):
    busy=context_driver.previous_jobs(rows,folders,own_pid)
    by_pid={r['pid']:r for r in rows}
    own=set()
    while own_pid and own_pid not in own:
        own.add(own_pid)
        own_pid=by_pid.get(own_pid,{}).get('ppid',0)
    wanted={p.resolve() for p in folders}
    for row in rows:
        if row['pid'] in own or not row.get('name','').casefold().startswith(('python','pypy')):
            continue
        command=row.get('command') or []
        cwd=Path(row['cwd'])
        paths={(Path(p) if Path(p).is_absolute() else cwd/p).resolve() for p in command[1:]}
        driver=SELF in paths and supervision.flag_value(command,'--stage')=='all'
        native=ROOT/'scripts/pronounce_public_audiobook_recovery.py' in paths and supervision.flag_value(command,'--stage') in ('render','verify')
        if not (driver or native):
            continue
        targets=[]
        for index,item in enumerate(command[:-1]):
            if item=='--folder':
                p=Path(command[index+1]);targets.append((p if p.is_absolute() else cwd/p).resolve())
        if wanted.intersection(targets):
            busy.append({**row,'other_controller':driver})
    return busy


def wait_previous(engine,args):
    import psutil
    started=time.monotonic()
    while True:
        rows=[]
        for process in psutil.process_iter(['pid','ppid','name']):
            if not (process.info.get('name') or '').casefold().startswith(('python','pypy')):
                continue
            try:
                rows.append({'pid':process.pid,'ppid':process.ppid(),'name':process.name(),'command':process.cmdline(),
                             'cwd':process.cwd(),'create_time':process.create_time()})
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied as error:
                raise context_driver.OwnershipConflict('Cannot verify Python job identity; refusing GPU overlap') from error
        busy=previous_jobs(rows,args.folder,os.getpid())
        journal=None
        if args.wait_finisher:
            try:
                prior=supervision.read(args.wait_finisher)
                pid=prior.get('supervisor_pid')
                journal={'path':str(args.wait_finisher),'claimed_pid':pid,'claimed_status':prior.get('status'),
                         'actual_matching_process':next((row for row in busy if row['pid']==pid),None)}
            except (OSError,ValueError):
                journal={'path':str(args.wait_finisher),'status':'missing_or_unreadable'}
        engine.state['prior_owner_wait']={'updated_unix':time.time(),'busy':busy,'journal':journal}
        engine.save()
        if any(row['other_controller'] for row in busy):
            raise context_driver.OwnershipConflict('Another demo controller owns these folders')
        if not busy:
            return
        if time.monotonic()-started>=args.wait_deadline_seconds:
            raise context_driver.OwnershipConflict('Prior GPU owner is live; deadline reached without stopping it')
        print('WAITING for prior audiobook GPU owners',[p['pid'] for p in busy],flush=True)
        time.sleep(args.poll_seconds)


def all_work(args):
    module=pronunciation_module()
    source_config=context_driver.configuration(args)
    pin={'adapter_sha256':file_digest(SELF),'extension_dependencies':extension_dependencies(),
         'base_dependencies':base.dependencies(),'engine_sha256':file_digest(Path(finishing.__file__)),
         'source_config':source_config}
    context_driver.pin_configuration(args.output/'pronunciation-controller-config.json',pin)
    engine=Engine(args)
    success=True
    wait_args=copy.copy(args)
    wait_args.folder=sorted({f.resolve().parent/name for f in args.folder for name in ('en-gift-of-magi','ru-chameleon')})
    for folder in args.folder:
        folder=folder.resolve()
        try:
            snapshot=engine.resolve(folder)
            for row in snapshot['report']['units']:
                uid=row['id']
                if args.unit and uid not in args.unit or row.get('selected'):
                    continue
                state=engine.state.setdefault('pronunciation_units',{}).setdefault(str(folder)+'::'+uid,{'attempts':{}})
                try:
                    for stage in ('plan','propose'):
                        engine.run('pronunciation-'+uid+'-'+stage,folder,
                                   engine.command(Path(module.__file__),folder,['--unit',uid,'--stage',stage],asr=True),kind='cli' if stage=='propose' else 'normal')
                except finishing.StageFailure as error:
                    state.update(status='held',reason=str(error))
                    engine.save()
                    continue
                for attempt in (1,2,3):
                    history=state['attempts'].setdefault(str(attempt),{})
                    if history.get('status')=='held':
                        continue
                    try:
                        for stage,recognizer in [('render',None),('verify','qwen'),('verify','turbo'),('validate',None)]:
                            if stage in ('render','verify'):
                                wait_previous(engine,wait_args)
                            flags=['--unit',uid,'--stage',stage,'--attempt',str(attempt)]
                            if recognizer:
                                flags+=['--recognizer',recognizer]
                            engine.run('pronunciation-'+uid+'-'+str(attempt)+'-'+stage+(recognizer or ''),folder,
                                       engine.command(Path(module.__file__),folder,flags,asr=True),kind='asr' if stage in ('render','verify') else 'normal')
                        unit=next(u for u in supervision.read(folder/'assignment.json')['units'] if u['id']==uid)
                        engine.run('pronunciation-'+uid+'-'+str(attempt)+'-register',folder,
                                   engine.command(SELF,folder,['--stage','register','--receipt',str(module.receipt_path(folder,unit,attempt))],asr=True))
                        snapshot=engine.resolve(folder)
                        engine.publish_if_eligible(folder,snapshot)
                        if not next(u for u in snapshot['report']['units'] if u['id']==uid).get('selected'):
                            raise finishing.StageFailure('Registered pronunciation does not pass source-bound recomputation')
                        history.update(status='verified',finished_unix=time.time())
                        state['status']='verified'
                        engine.save()
                        break
                    except context_driver.OwnershipConflict:
                        raise
                    except finishing.StageFailure as error:
                        history.update(status='held',reason=str(error),finished_unix=time.time())
                        state['status']='held'
                        engine.save()
            snapshot=engine.resolve(folder)
            engine.publish_if_eligible(folder,snapshot)
            result=engine.resolve(folder,True)
            engine.book(folder,status='complete' if result['completion_valid'] else 'held',process_running=False,reason=result['completion_reason'])
            success=result['completion_valid'] and success
        except Exception as error:
            success=False
            engine.book(folder,status='held',process_running=False,reason=str(error))
    engine.state['status']='complete' if success else 'held'
    engine.save()
    return 0 if success else 1


def main():
    os.environ['PYTHONUTF8']='1'
    os.environ['PYTHONIOENCODING']='utf-8'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,action='append',required=True)
    parser.add_argument('--stage',choices=['register','resolve','publish','complete','all'],required=True)
    parser.add_argument('--receipt',type=Path)
    parser.add_argument('--unit',action='append',default=[])
    parser.add_argument('--output',type=Path)
    parser.add_argument('--python',type=Path,default=ROOT/'training-work/.venv-tts/Scripts/python.exe')
    parser.add_argument('--asr-python',type=Path,default=ROOT/'training-work/.venv-asr/Scripts/python.exe')
    parser.add_argument('--wait-finisher',type=Path)
    parser.add_argument('--wait-deadline-seconds',type=float,default=43200)
    parser.add_argument('--poll-seconds',type=float,default=5)
    parser.add_argument('--stall-seconds',type=float,default=600)
    parser.add_argument('--verifier-initial-seconds',type=float,default=1800)
    args=parser.parse_args()
    if args.stage=='all':
        if args.output is None:
            parser.error('--output is required for durable all-stage work')
        args.output=args.output.resolve()
        args.python=args.python.resolve()
        args.asr_python=args.asr_python.resolve()
        args.stage_retries=0
        return all_work(args)
    if len(args.folder)!=1:
        parser.error('CPU adapter stages require one folder')
    folder=args.folder[0].resolve()
    if args.stage=='register':
        if args.receipt is None:
            parser.error('--receipt is required')
        register(Context(folder),args.receipt)
    else:
        result=resolve(folder,args.stage=='publish',args.stage=='complete')
        print(json.dumps({k:result[k] for k in ('report_sha256','prefix_units','completion_valid','completion_reason')},ensure_ascii=False),flush=True)
        if args.stage=='complete' and not result['completion_valid']:
            return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
