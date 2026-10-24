"""V18: development-only reviewed-reference context and exact PCM crops.

This version layers V17 without modifying any earlier adapter or generation.
Only takes33..35 from the explicit private combined campaign may be registered.
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
import resolve_public_demo_ellipsis_assembly_v2 as previous
from resolve_public_demo_pronunciation import public_safe
base=previous.base
finishing=previous.finishing
supervision=previous.supervision
SELF=Path(__file__).resolve()
RULE='public-demo-approved-reviewed-reference-context-resolution-adapter-v18'


def helper():return importlib.import_module('context_public_demo_reviewed_reference_recovery')


def dependencies():
    return {**helper().dependencies(),SELF.relative_to(ROOT).as_posix():base.file_digest(SELF),
            Path(previous.__file__).relative_to(ROOT).as_posix():base.file_digest(Path(previous.__file__))}


def validated_reviewed_reference_context(context,unit,receipt,receipt_sha256=None):
    module=helper();spec=receipt['spec'];marker=spec.get('reviewed_reference_context_recovery')
    if (unit['id']!='u0071' or context.assignment['language']!='Russian' or not isinstance(marker,dict) or marker.get('policy')!=module.POLICY
            or type(marker.get('attempt')) is not int or marker['attempt'] not in (1,2,3)
            or receipt.get('take')!=marker['attempt']+32 or receipt.get('unit')!=unit['id']
            or next((u for u in context.assignment['units'] if u['id']==unit['id']),None)!=unit
            or any(k in spec for k in ('recovery','context_recovery','pronunciation_recovery','ru_pronunciation_recovery',
                                      'word_pronunciation_recovery','word_pronunciation_supplement','word_context_recovery','historical_word_context_recovery','boundary_punctuation_context_recovery','supplement_word_context_recovery','line_wrap_recovery','ellipsis_phrase_assembly_v2'))
            or spec.get('source')!=unit or spec.get('generation')!=context.generation
            or spec.get('model')!=context.manifest['speech_model'] or spec.get('runner')!=context.manifest['runner']
            or spec.get('effects')!='disabled'
            or base.sha(spec)!=receipt.get('spec_sha256') or receipt.get('quality_passed') is not True):
        raise ValueError('Combined word-context crop changed original source/model/reference or bounded identity')
    proof=module.validate_reviewed_reference_context_receipt(context.folder,unit,receipt)
    bound,native_spec,native=module.native_receipt(context.folder,unit,marker['attempt'])
    approved=module.approved_campaign(context.folder,unit)
    proposal=module.approved_proposal(context.folder,unit,approved)
    stress=module.words.validated_stress(context.folder,approved)
    normative=module.normative_evidence(context.folder,unit,approved,proposal,stress)
    module.validate_repair(unit,bound['word_repair'])
    if (proof.get('policy')!=module.POLICY or proof.get('crop_audio_sha256')!=receipt['audio_sha256']
            or proof.get('native_audio_sha256')!=native['audio_sha256']
            or receipt_sha256 is not None and proof.get('receipt_sha256')!=receipt_sha256
            or proof.get('target_source_span')!={k:unit[k] for k in ('id','source_id','start','end','text')}
            or proof.get('alignment_is_approval') is not False or proof.get('human_approval') is not False
            or spec['request']!=native_spec['request'] or spec.get('reference')!=bound['reference_override']['actual_reference']
            or marker.get('reference_override_sha256')!=base.sha(bound['reference_override'])
            or proof.get('reference_override_provenance')!=bound['reference_override']
            or bound.get('normative_evidence')!=normative
            or marker.get('normative_evidence_sha256')!=base.sha(normative)
            or proof.get('word_provenance',{}).get('normative_evidence')!=normative):
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
    reference=bound['reference_override']
    sanitized={'policy':proof['policy'],'receipt_sha256':proof['receipt_sha256'],
        'native_audio_sha256':proof['native_audio_sha256'],'crop_audio_sha256':proof['crop_audio_sha256'],
        'campaign_sha256':proof['campaign_sha256'],'adapter_sha256':base.file_digest(SELF),
        'implementation_sha256':base.file_digest(Path(module.__file__)),'reference_changed':True,
        'same_human_family_and_mood':True,'original_reference_unchanged':True,
        'dossier_sha256':reference['dossier_sha256'],'reference_bank_sha256':reference['reference_bank_sha256'],
        'original_reference_audio_sha256':reference['original']['audio_sha256'],
        'alternative_reference_audio_sha256':reference['alternative']['audio_sha256'],
        'alternative_reference_text_sha256':reference['alternative']['text_sha256'],
        'original_review_sha256':reference['original']['review_sha256'],
        'alternative_review_sha256':reference['alternative']['review_sha256'],
        'original_archive_manifest_sha256':reference['original']['archive_manifest_sha256'],
        'alternative_archive_manifest_sha256':reference['alternative']['archive_manifest_sha256'],
        'reference_override_sha256':base.sha(reference),'alignment_is_approval':False,
        'training_approval':False,'synthesis_listening_approval':False,'human_approval':False,
        'acoustic_stress_approval':False}
    return proof['target_decision'],sanitized


class Context(previous.Context):
    def __init__(self,folder):
        super().__init__(folder);self.reviewed_reference_context_dependencies=dependencies()

    def recheck(self):
        super().recheck()
        if hasattr(self,'reviewed_reference_context_dependencies') and dependencies()!=self.reviewed_reference_context_dependencies:
            raise ValueError('Combined word-context adapter/helper changed')

    def assess(self,unit,receipt):
        if 'reviewed_reference_context_recovery' not in receipt.get('spec',{}):return super().assess(unit,receipt)
        self.recheck()
        if receipt.get('quality_passed') is not True:return None
        module=helper();marker=receipt['spec']['reviewed_reference_context_recovery']
        canonical=self.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take'])
        private=module.receipt_path(self.folder,unit,marker['attempt'])
        if canonical.read_bytes()!=private.read_bytes() or base.read(canonical)!=receipt:
            raise ValueError('Combined canonical crop must mirror its private receipt byte-exactly')
        decision,proof=validated_reviewed_reference_context(self,unit,receipt,base.file_digest(canonical))
        return {'schema':1,'rule':RULE,'module_sha256':base.file_digest(SELF),'base_module_sha256':base.file_digest(Path(base.__file__)),
                'dependencies':self.dependencies,'bindings':self.bindings,'reviewed_reference_context_extension_dependencies':self.reviewed_reference_context_dependencies,
                'unit':unit['id'],'source_id':unit['source_id'],'source_start':unit['start'],'source_end':unit['end'],
                'source_text':unit['text'],'expected_spoken':unit['spoken'],'source_paragraph_sha256':base.sha(self.blocks[unit['source_id']]),
                'audio_sha256':receipt['audio_sha256'],'take_spec_sha256':receipt['spec_sha256'],
                'take_receipt_sha256':base.file_digest(canonical),'reviewed_reference_context_provenance':proof,
                'recovery_provenance':None,'context_provenance':None,'english_source_policy':self.english_policy,
                'decision':decision,'human_approval':False,'scope':'Both original-source full-context/crop checks plus exact PCM target boundaries'}


def register(context,receipt_path):
    module=helper();context.recheck();path=Path(receipt_path)
    path=(path if path.is_absolute() else context.folder/path).resolve();path.relative_to(context.folder)
    original=path.read_bytes();receipt=base.read(path)
    unit=next((u for u in context.assignment['units'] if u['id']==receipt.get('unit')),None)
    attempt=receipt.get('spec',{}).get('reviewed_reference_context_recovery',{}).get('attempt')
    if (unit is None or type(attempt) is not int or attempt not in (1,2,3)
            or path!=module.receipt_path(context.folder,unit,attempt).resolve()):
        raise ValueError('Only explicit private combined word-context crops may register')
    _,proof=validated_reviewed_reference_context(context,unit,receipt,base.file_digest(path));context.recheck()
    if path.read_bytes()!=original:raise ValueError('Combined crop receipt changed during validation')
    target=context.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take']);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()!=original:raise ValueError('Never replace a differing canonical combined context crop')
    else:
        with target.open('xb') as stream:stream.write(original)
    result={'schema':1,'rule':RULE,'unit':unit['id'],'take':receipt['take'],'canonical':target.relative_to(context.folder).as_posix(),
            'canonical_receipt_sha256':base.file_digest(target),'adapter_sha256':base.file_digest(SELF),
            'extension_dependencies':context.reviewed_reference_context_dependencies,'reviewed_reference_context_provenance':proof,'human_approval':False}
    base.immutable(path.parent/'reviewed-reference-context-registration.json',result);return result


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
            if 'reviewed_reference_context_recovery' in receipts[summary['take']]['spec']:
                summary['reviewed_reference_context_provenance']=context.assess(unit,receipts[summary['take']])['reviewed_reference_context_provenance']
    history=private_history(context,known);extra=sum(r['status']!='asr_exact_match' for r in history)
    value.update(ellipsis_v2_resolution_adapter_sha256=value['resolution_adapter_sha256'],resolution_adapter_sha256=base.file_digest(SELF),
                 reviewed_reference_context_rule=RULE,reviewed_reference_context_extension_dependencies=context.reviewed_reference_context_dependencies,
                 unregistered_reviewed_reference_context_history=history,raw_unregistered_reviewed_reference_context_mismatch_check_count=extra)
    value['raw_mismatch_check_count']+=extra
    value['disclosure']+=' This development experiment explicitly substitutes one alternative performance from the same human-reviewed voice-family and mood bucket; the frozen original reference is retained. The source, model and approved spoken words remain unchanged. The reference review does not imply approval of synthesized audio. Already independently approved historical hard-ц spelling and stress inputs may use exact adjacent frozen source context. Both whole-context and target-crop recognizers must pass the unchanged source-bound rule, with both literally decoding each modern historical slot. Crops retain exact native PCM samples; alignment never approves content. '+str(extra)+' additional unregistered raw mismatches remain counted. No acoustic stress or human listening approval is implied.'
    return value


def resolve(folder,publish=False,complete=False):
    folder=Path(folder).resolve();context=Context(folder);value=report(context)
    destination=folder/'resolution-proposals'/('reviewed-reference-context-report-'+base.sha(value)+'.json');base.immutable(destination,value)
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
    return 1 if args.stage in ('complete','publish') and not value['completion_valid'] else 0


if __name__=='__main__':raise SystemExit(main())
