"""V14: development-only reviewed punctuation/segmentation and exact PCM crops.

This version layers V13 without modifying any earlier adapter or generation.
Only takes21..23 from the explicit private combined campaign may be registered.
Default resolve writes private reports; publication requires --stage publish.
"""
from __future__ import annotations
import argparse
import copy
import importlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_historical_word_context as previous
from resolve_public_demo_pronunciation import public_safe
base=previous.base
finishing=previous.finishing
supervision=previous.supervision
SELF=Path(__file__).resolve()
RULE='public-demo-boundary-punctuation-context-resolution-adapter-v14'


def helper():return importlib.import_module('context_public_demo_boundary_recovery')


def dependencies():
    return {**helper().dependencies(),SELF.relative_to(ROOT).as_posix():base.file_digest(SELF),
            Path(previous.__file__).relative_to(ROOT).as_posix():base.file_digest(Path(previous.__file__)),
            'scripts/package_public_audiobook_demos.py':base.file_digest(ROOT/'scripts/package_public_audiobook_demos.py')}


def public_decision(decision):
    return {'passed':decision['passed'],'status':decision['status'],'resolution':decision['resolution'],
        'evidence':[{'role':e['role'],'family':e['family'],'evidence_sha256':e['sha256'],
                     'raw':e['raw'],'comparison':public_safe(e['comparison'])} for e in decision['evidence']],
        'tolerated_deviations':public_safe(decision['tolerated_deviations']),'equivalences':public_safe(decision['equivalences'])}


def public_provenance(proof):
    """Explicit public projection; never publish private specs or provider bodies."""
    experiment=proof['experiment_provenance']
    allowed=('policy','candidate_id','candidate_choice','campaign_sha256','proposal_sha256','word_campaign_sha256',
             'word_proposal_sha256','dictionary_sha256','stress_sha256','prior_context_campaign_sha256','closed_parent_sha256',
             'native_before','native_after','native_edits','source_text_unchanged','original_spoken_unchanged',
             'human_approval','acoustic_stress_approval','audio_approval','scope')
    projected={k:experiment[k] for k in allowed}
    projected['letter_digit_stress_map_sha256']=base.sha(experiment['letter_digit_stress_map'])
    projected['providers']=[{k:p[k] for k in ('provider','model','effort','request_sha256','answer_sha256','accepted','audio_approval')}
                            for p in experiment['providers']]
    result={k:proof[k] for k in ('policy','receipt_sha256','native_audio_sha256','crop_audio_sha256','target_source_span',
                                'crop','campaign_sha256','alignment_is_approval')}
    result['source_spans']=[{k:s[k] for k in ('id','source_id','start','end','text','spoken')} for s in proof['source_spans']]
    result.update(experiment_provenance=projected,full_context_evidence=public_decision(proof['full_context_decision']),
                  crop_is_entire_native_pcm=proof['crop_is_entire_native_pcm'],
                  independent_recognizer_families=['qwen','whisper'],human_approval=False,acoustic_stress_approval=False)
    # Fail rather than silently redact any accidental new private field/path.
    from package_public_audiobook_demos import clean_json
    clean_json(result)
    return result


def validated_boundary_context(context,unit,receipt,receipt_sha256=None):
    module=helper();spec=receipt['spec'];marker=spec.get('boundary_punctuation_context_recovery')
    if (context.assignment['language']!='Russian' or not isinstance(marker,dict) or marker.get('policy')!=module.POLICY
            or type(marker.get('attempt')) is not int or marker['attempt'] not in (1,2,3)
            or receipt.get('take')!=marker['attempt']+20 or receipt.get('unit')!=unit['id']
            or next((u for u in context.assignment['units'] if u['id']==unit['id']),None)!=unit
            or any(k in spec for k in ('recovery','context_recovery','pronunciation_recovery','ru_pronunciation_recovery',
                'word_pronunciation_recovery','word_pronunciation_supplement','word_context_recovery','historical_word_context_recovery'))
            or spec.get('source')!=unit or spec.get('generation')!=context.generation
            or spec.get('model')!=context.manifest['speech_model'] or spec.get('runner')!=context.manifest['runner']
            or spec.get('reference')!=unit['palette_reference'] or spec.get('effects')!='disabled'
            or base.sha(spec)!=receipt.get('spec_sha256') or receipt.get('quality_passed') is not True):
        raise ValueError('Boundary crop changed original source/model/reference or bounded identity')
    proof=module.validate_boundary_context_receipt(context.folder,unit,receipt)
    bound,native_spec,native=module.native_receipt(context.folder,unit,marker['attempt'])
    module.validate_repair(unit,bound['word_repair'])
    experiment=module.experiment_provenance(context.folder,unit,bound,marker['attempt'])
    chosen=bound['candidates'][marker['attempt']-1]
    if (proof.get('policy')!=module.POLICY or proof.get('crop_audio_sha256')!=receipt['audio_sha256']
            or proof.get('native_audio_sha256')!=native['audio_sha256']
            or receipt_sha256 is not None and proof.get('receipt_sha256')!=receipt_sha256
            or proof.get('target_source_span')!={k:unit[k] for k in ('id','source_id','start','end','text')}
            or proof.get('alignment_is_approval') is not False or proof.get('human_approval') is not False
            or spec['request']!=native_spec['request'] or spec['request']['text']!=chosen['synthesis_text']
            or marker['context']!=chosen or proof.get('experiment_provenance')!=experiment
            or marker.get('experiment_proposal_sha256')!=experiment['proposal_sha256']
            or proof.get('word_provenance',{}).get('repair')!=bound['word_repair']):
        raise ValueError('Boundary experiment source/native/crop/reviewer provenance differs')
    spans=chosen['source_spans']
    if sum(s['id']==unit['id'] for s in spans)!=1 or proof.get('source_spans')!=spans:
        raise ValueError('Target must occur exactly once in the unedited source window')
    for span in spans:
        frozen=next((u for u in context.assignment['units'] if u['id']==span['id']),None)
        if (frozen is None or any(span.get(k)!=frozen[k] for k in ('id','source_id','start','end','text','spoken'))
                or context.blocks[span['source_id']][span['start']:span['end']]!=span['text']):
            raise ValueError('Boundary context skipped or edited a source neighbor')
    for kind,key in [('context','full_context_decision'),('crop','target_decision')]:
        decision=proof[key]
        if (decision.get('passed') is not True or len(decision.get('evidence',[]))!=2
                or {(e['role'],e['family']) for e in decision['evidence']}!={('primary','qwen'),('witness','whisper')}):
            raise ValueError('Both independent recognizers must pass full context AND crop')
        _,expected,_,checksum=module.verification_audio(context.folder,unit,marker['attempt'],kind)
        target_words=base.word_tokens(expected)
        repair=copy.deepcopy(bound['word_repair'])
        if kind=='context':
            repair['source_stress_projection']=chosen['source_stress_projection']
            for patch in repair['patches']:patch['spoken_index']+=chosen['target_word_start']
        target={'spoken':expected};paragraph=chosen['source_text'] if kind=='context' else context.blocks[unit['source_id']]
        for item in decision['evidence']:
            path=base.checked_relative(context.folder,item['path'],item['sha256']);check=base.read(path)
            recognizer='qwen' if item['role']=='primary' else 'turbo';raw=module.check_record(check,expected,checksum,bound,recognizer)
            alignment=module.literal_target_alignment(target,raw['heard'],paragraph,repair,checksum,raw['verifier_sha256'])
            if (item['raw']!=raw or item['verifier']!=check['verifier'] or item['comparison'].get('passed') is not True
                    or item['comparison'].get('canonical_words')!=target_words or alignment is None
                    or item['comparison'].get('literal_target_alignment')!=alignment):
                raise ValueError('Original ordinary target words require literal raw independent recognition')
        if module.blind_decision(context.folder,unit,marker['attempt'],kind)!=decision:
            raise ValueError('Boundary strict original-source decision changed')
    sanitized=public_provenance(proof)
    sanitized.update(adapter_sha256=base.file_digest(SELF),implementation_sha256=base.file_digest(Path(module.__file__)),
                     actual_native_input=spec['request']['text'],expected_spoken=unit['spoken'])
    return proof['target_decision'],sanitized


class Context(previous.Context):
    def __init__(self,folder):
        super().__init__(folder);self.boundary_context_dependencies=dependencies()

    def recheck(self):
        super().recheck()
        if hasattr(self,'boundary_context_dependencies') and dependencies()!=self.boundary_context_dependencies:
            raise ValueError('Combined word-context adapter/helper changed')

    def assess(self,unit,receipt):
        if 'boundary_punctuation_context_recovery' not in receipt.get('spec',{}):return super().assess(unit,receipt)
        self.recheck()
        if receipt.get('quality_passed') is not True:return None
        module=helper();marker=receipt['spec']['boundary_punctuation_context_recovery']
        canonical=self.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take'])
        private=module.receipt_path(self.folder,unit,marker['attempt'])
        if canonical.read_bytes()!=private.read_bytes() or base.read(canonical)!=receipt:
            raise ValueError('Combined canonical crop must mirror its private receipt byte-exactly')
        decision,proof=validated_boundary_context(self,unit,receipt,base.file_digest(canonical))
        return {'schema':1,'rule':RULE,'module_sha256':base.file_digest(SELF),'base_module_sha256':base.file_digest(Path(base.__file__)),
                'dependencies':self.dependencies,'bindings':self.bindings,'boundary_context_extension_dependencies':self.boundary_context_dependencies,
                'unit':unit['id'],'source_id':unit['source_id'],'source_start':unit['start'],'source_end':unit['end'],
                'source_text':unit['text'],'expected_spoken':unit['spoken'],'source_paragraph_sha256':base.sha(self.blocks[unit['source_id']]),
                'audio_sha256':receipt['audio_sha256'],'take_spec_sha256':receipt['spec_sha256'],
                'take_receipt_sha256':base.file_digest(canonical),'boundary_context_provenance':proof,
                'recovery_provenance':None,'context_provenance':None,'english_source_policy':self.english_policy,
                'decision':decision,'human_approval':False,'scope':'Both original-source full-context/crop checks plus exact PCM target boundaries'}


def register(context,receipt_path):
    module=helper();context.recheck();path=Path(receipt_path)
    path=(path if path.is_absolute() else context.folder/path).resolve();path.relative_to(context.folder)
    original=path.read_bytes();receipt=base.read(path)
    unit=next((u for u in context.assignment['units'] if u['id']==receipt.get('unit')),None)
    attempt=receipt.get('spec',{}).get('boundary_punctuation_context_recovery',{}).get('attempt')
    if (unit is None or type(attempt) is not int or attempt not in (1,2,3)
            or path!=module.receipt_path(context.folder,unit,attempt).resolve()):
        raise ValueError('Only explicit private combined word-context crops may register')
    _,proof=validated_boundary_context(context,unit,receipt,base.file_digest(path));context.recheck()
    if path.read_bytes()!=original:raise ValueError('Combined crop receipt changed during validation')
    target=context.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take']);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()!=original:raise ValueError('Never replace a differing canonical combined context crop')
    else:
        with target.open('xb') as stream:stream.write(original)
    result={'schema':1,'rule':RULE,'unit':unit['id'],'take':receipt['take'],'canonical':target.relative_to(context.folder).as_posix(),
            'canonical_receipt_sha256':base.file_digest(target),'adapter_sha256':base.file_digest(SELF),
            'extension_dependencies':context.boundary_context_dependencies,'boundary_context_provenance':proof,'human_approval':False}
    base.immutable(path.parent/'boundary-context-registration.json',result);return result


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
            if 'boundary_punctuation_context_recovery' in receipts[summary['take']]['spec']:
                summary['boundary_context_provenance']=context.assess(unit,receipts[summary['take']])['boundary_context_provenance']
    history=private_history(context,known);extra=sum(r['status']!='asr_exact_match' for r in history)
    value.update(historical_word_context_resolution_adapter_sha256=value['resolution_adapter_sha256'],resolution_adapter_sha256=base.file_digest(SELF),
                 boundary_context_rule=RULE,boundary_context_extension_dependencies=context.boundary_context_dependencies,
                 unregistered_boundary_context_history=history,raw_unregistered_boundary_context_mismatch_check_count=extra)
    value['raw_mismatch_check_count']+=extra
    value['disclosure']+=' This development experiment changes only an independently reviewed native boundary mark and/or inserts a declared internal word hyphen. Original source words and captions stay unchanged. Two text opinions are hypotheses, not audio approval. Both whole-context and PCM-crop Qwen/Whisper checks must literally retain the ordinary repaired words; no missing-consonant or name waiver is introduced. '+str(extra)+' additional unregistered raw mismatches remain counted. Alignment provides coordinates only; no acoustic stress or human listening approval is implied.'
    return value


def resolve(folder,publish=False,complete=False):
    folder=Path(folder).resolve();context=Context(folder);value=report(context)
    destination=folder/'resolution-proposals'/('boundary-context-report-'+base.sha(value)+'.json');base.immutable(destination,value)
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
    return 1 if args.stage in ('publish','complete') and not value['completion_valid'] else 0


if __name__=='__main__':raise SystemExit(main())
