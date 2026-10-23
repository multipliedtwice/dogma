"""V12: strict combined context plus approved-word recovery, append-only crops.

This version layers V11 without modifying any earlier adapter or generation.
Only takes18..20 from the explicit private combined campaign may be registered.
Default resolve writes private reports; publication requires --stage publish.
"""
from __future__ import annotations
import argparse
import importlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_word_supplement as previous
from resolve_public_demo_pronunciation import public_safe
base=previous.base
finishing=previous.finishing
supervision=previous.supervision
SELF=Path(__file__).resolve()
RULE='public-demo-approved-word-context-resolution-adapter-v12'


def helper():return importlib.import_module('context_public_demo_word_recovery')


def dependencies():
    return {**helper().dependencies(),SELF.relative_to(ROOT).as_posix():base.file_digest(SELF),
            Path(previous.__file__).relative_to(ROOT).as_posix():base.file_digest(Path(previous.__file__))}


def validated_word_context(context,unit,receipt,receipt_sha256=None):
    module=helper();spec=receipt['spec'];marker=spec.get('word_context_recovery')
    if (context.assignment['language']!='Russian' or not isinstance(marker,dict) or marker.get('policy')!=module.POLICY
            or type(marker.get('attempt')) is not int or marker['attempt'] not in (1,2,3)
            or receipt.get('take')!=marker['attempt']+17 or receipt.get('unit')!=unit['id']
            or next((u for u in context.assignment['units'] if u['id']==unit['id']),None)!=unit
            or any(k in spec for k in ('recovery','context_recovery','pronunciation_recovery','ru_pronunciation_recovery',
                                      'word_pronunciation_recovery','word_pronunciation_supplement'))
            or spec.get('source')!=unit or spec.get('generation')!=context.generation
            or spec.get('model')!=context.manifest['speech_model'] or spec.get('runner')!=context.manifest['runner']
            or spec.get('reference')!=unit['palette_reference'] or spec.get('effects')!='disabled'
            or base.sha(spec)!=receipt.get('spec_sha256') or receipt.get('quality_passed') is not True):
        raise ValueError('Combined word-context crop changed original source/model/reference or bounded identity')
    proof=module.validate_word_context_receipt(context.folder,unit,receipt)
    bound,native_spec,native=module.native_receipt(context.folder,unit,marker['attempt'])
    if (proof.get('policy')!=module.POLICY or proof.get('crop_audio_sha256')!=receipt['audio_sha256']
            or proof.get('native_audio_sha256')!=native['audio_sha256']
            or receipt_sha256 is not None and proof.get('receipt_sha256')!=receipt_sha256
            or proof.get('target_source_span')!={k:unit[k] for k in ('id','source_id','start','end','text')}
            or proof.get('alignment_is_approval') is not False or proof.get('human_approval') is not False
            or spec['request']!=native_spec['request']):
        raise ValueError('Combined context native/crop/source/provenance binding differs')
    spans=marker['context']['source_spans']
    if sum(span['id']==unit['id'] for span in spans)!=1:raise ValueError('Combined context must include target exactly once')
    for span in spans:
        frozen=next((u for u in context.assignment['units'] if u['id']==span['id']),None)
        if (frozen is None or any(span.get(k)!=frozen[k] for k in ('id','source_id','start','end','text','spoken'))
                or context.blocks[span['source_id']][span['start']:span['end']]!=span['text']):
            raise ValueError('Combined context has an unbound or edited source neighbor')
    if proof.get('source_spans')!=spans or proof.get('word_provenance',{}).get('repair')!=bound['word_repair']:
        raise ValueError('Combined context source spans or approved word patch changed')
    for kind,key in [('context','full_context_decision'),('crop','target_decision')]:
        decision=proof[key]
        if (decision.get('passed') is not True or len(decision.get('evidence',[]))!=2
                or {(e['role'],e['family']) for e in decision['evidence']}!={('primary','qwen'),('witness','whisper')}):
            raise ValueError('Both independent recognizers must pass full context AND crop')
        _,expected,_,checksum=module.verification_audio(context.folder,unit,marker['attempt'],kind)
        for item in decision['evidence']:
            path=base.checked_relative(context.folder,item['path'],item['sha256']);check=base.read(path)
            recognizer='qwen' if item['role']=='primary' else 'turbo'
            raw=module.check_record(check,expected,checksum,bound,recognizer)
            if (item['raw']!=raw or item['verifier']!=check['verifier'] or item['comparison'].get('passed') is not True
                    or item['comparison'].get('canonical_words')!=base.word_tokens(expected)):
                raise ValueError('Combined context audio-only evidence cannot be reproduced')
        if module.blind_decision(context.folder,unit,marker['attempt'],kind)!=decision:
            raise ValueError('Combined context strict original-source decision changed')
    sanitized=public_safe({k:proof[k] for k in ('policy','receipt_sha256','native_audio_sha256','crop_audio_sha256',
        'source_spans','target_source_span','crop','campaign_sha256','word_provenance','alignment_is_approval')})
    sanitized.update(adapter_sha256=base.file_digest(SELF),implementation_sha256=base.file_digest(Path(module.__file__)),
                     actual_native_input=spec['request']['text'],expected_spoken=unit['spoken'],human_approval=False,
                     full_context_evidence=public_safe(proof['full_context_decision']),acoustic_stress_approval=False)
    return proof['target_decision'],sanitized


class Context(previous.Context):
    def __init__(self,folder):
        super().__init__(folder);self.word_context_dependencies=dependencies()

    def recheck(self):
        super().recheck()
        if hasattr(self,'word_context_dependencies') and dependencies()!=self.word_context_dependencies:
            raise ValueError('Combined word-context adapter/helper changed')

    def assess(self,unit,receipt):
        if 'word_context_recovery' not in receipt.get('spec',{}):return super().assess(unit,receipt)
        self.recheck()
        if receipt.get('quality_passed') is not True:return None
        module=helper();marker=receipt['spec']['word_context_recovery']
        canonical=self.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take'])
        private=module.receipt_path(self.folder,unit,marker['attempt'])
        if canonical.read_bytes()!=private.read_bytes() or base.read(canonical)!=receipt:
            raise ValueError('Combined canonical crop must mirror its private receipt byte-exactly')
        decision,proof=validated_word_context(self,unit,receipt,base.file_digest(canonical))
        return {'schema':1,'rule':RULE,'module_sha256':base.file_digest(SELF),'base_module_sha256':base.file_digest(Path(base.__file__)),
                'dependencies':self.dependencies,'bindings':self.bindings,'word_context_extension_dependencies':self.word_context_dependencies,
                'unit':unit['id'],'source_id':unit['source_id'],'source_start':unit['start'],'source_end':unit['end'],
                'source_text':unit['text'],'expected_spoken':unit['spoken'],'source_paragraph_sha256':base.sha(self.blocks[unit['source_id']]),
                'audio_sha256':receipt['audio_sha256'],'take_spec_sha256':receipt['spec_sha256'],
                'take_receipt_sha256':base.file_digest(canonical),'word_context_provenance':proof,
                'recovery_provenance':None,'context_provenance':None,'english_source_policy':self.english_policy,
                'decision':decision,'human_approval':False,'scope':'Both original-source full-context/crop checks plus exact PCM target boundaries'}


def register(context,receipt_path):
    module=helper();context.recheck();path=Path(receipt_path)
    path=(path if path.is_absolute() else context.folder/path).resolve();path.relative_to(context.folder)
    original=path.read_bytes();receipt=base.read(path)
    unit=next((u for u in context.assignment['units'] if u['id']==receipt.get('unit')),None)
    attempt=receipt.get('spec',{}).get('word_context_recovery',{}).get('attempt')
    if (unit is None or type(attempt) is not int or attempt not in (1,2,3)
            or path!=module.receipt_path(context.folder,unit,attempt).resolve()):
        raise ValueError('Only explicit private combined word-context crops may register')
    _,proof=validated_word_context(context,unit,receipt,base.file_digest(path));context.recheck()
    if path.read_bytes()!=original:raise ValueError('Combined crop receipt changed during validation')
    target=context.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take']);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()!=original:raise ValueError('Never replace a differing canonical combined context crop')
    else:
        with target.open('xb') as stream:stream.write(original)
    result={'schema':1,'rule':RULE,'unit':unit['id'],'take':receipt['take'],'canonical':target.relative_to(context.folder).as_posix(),
            'canonical_receipt_sha256':base.file_digest(target),'adapter_sha256':base.file_digest(SELF),
            'extension_dependencies':context.word_context_dependencies,'word_context_provenance':proof,'human_approval':False}
    base.immutable(path.parent/'word-context-registration.json',result);return result


def private_history(context,known):
    module=helper();history=[]
    for unit in context.assignment['units']:
        for attempt in (1,2,3):
            directory=module.attempt_home(context.folder,unit,attempt)
            if not (directory/'native-receipt.json').exists():continue
            bound,_,native=module.native_receipt(context.folder,unit,attempt)
            for kind in ('context','crop'):
                for recognizer in ('qwen','turbo'):
                    path=directory/(kind+'-'+recognizer+'.json')
                    if not path.exists() or base.file_digest(path) in known:continue
                    _,expected,_,audio=module.verification_audio(context.folder,unit,attempt,kind)
                    raw=module.check_record(base.read(path),expected,audio,bound,recognizer)
                    history.append({'unit':unit['id'],'attempt':attempt,'kind':kind,'recognizer':recognizer,
                                    'heard':raw['heard'],'status':raw['status'],'audio_sha256':audio,
                                    'evidence_sha256':base.file_digest(path),'human_approval':False})
    return history


def report(context):
    value=previous.report(context);known=set()
    for row in value['units']:
        unit=next(u for u in context.assignment['units'] if u['id']==row['id'])
        receipts={r['take']:r for r in base.renderer.takes(context.folder,unit)}
        for summary in row['takes']:
            known.update(e['evidence_sha256'] for e in summary['raw_transcripts'])
            if 'word_context_recovery' in receipts[summary['take']]['spec']:
                summary['word_context_provenance']=context.assess(unit,receipts[summary['take']])['word_context_provenance']
    history=private_history(context,known);extra=sum(r['status']!='asr_exact_match' for r in history)
    value.update(word_supplement_resolution_adapter_sha256=value['resolution_adapter_sha256'],resolution_adapter_sha256=base.file_digest(SELF),
                 word_context_rule=RULE,word_context_extension_dependencies=context.word_context_dependencies,
                 unregistered_word_context_history=history,raw_unregistered_word_context_mismatch_check_count=extra)
    value['raw_mismatch_check_count']+=extra
    value['disclosure']+=' Approved stress-only word inputs may use exact adjacent frozen source context; both whole-context and target-crop blind recognizers must pass. Crops retain exact native PCM samples; alignment never approves content. '+str(extra)+' additional unregistered raw mismatches remain counted. No acoustic stress or human listening approval is implied.'
    return value


def resolve(folder,publish=False,complete=False):
    folder=Path(folder).resolve();context=Context(folder);value=report(context)
    destination=folder/'resolution-proposals'/('word-context-report-'+base.sha(value)+'.json');base.immutable(destination,value)
    count,seconds=finishing.prefix_seconds(context,value);eligible=seconds>=300 or count==len(context.assignment['units'])
    if publish and eligible:base.publish(context,value,3)
    valid,reason=finishing.validate_completion(folder,context,value) if complete else (False,'Not checked')
    snapshot={'report':value,'report_path':str(destination),'report_sha256':base.sha(value),'prefix_units':count,
              'prefix_seconds':seconds,'publication_eligible':eligible,'completion_valid':valid,'completion_reason':reason}
    supervision.write(folder/'finishing/current-resolution.json',snapshot);return snapshot


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--stage',choices=['register','resolve','publish','complete'],required=True);parser.add_argument('--receipt',type=Path)
    args=parser.parse_args();folder=args.folder.resolve()
    if args.stage=='register':
        if args.receipt is None:parser.error('--receipt is required')
        register(Context(folder),args.receipt);return 0
    value=resolve(folder,args.stage=='publish',args.stage in ('publish','complete'))
    print(json.dumps({k:value[k] for k in ('report_sha256','prefix_units','completion_valid','completion_reason')},ensure_ascii=False))
    return 1 if args.stage=='complete' and not value['completion_valid'] else 0


if __name__=='__main__':raise SystemExit(main())
