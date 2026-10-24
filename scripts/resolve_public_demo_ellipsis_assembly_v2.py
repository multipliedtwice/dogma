"""V17 append-only honest PCM-composite adapter, layered over frozen V16."""
from __future__ import annotations
import argparse
import copy
import importlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_ellipsis_assembly as previous
from resolve_public_demo_pronunciation import public_safe
base=previous.base
finishing=previous.finishing
supervision=previous.supervision
public_decision=previous.previous.public_decision
SELF=Path(__file__).resolve()
RULE='public-demo-source-ellipsis-pcm-composite-adapter-v17v2'
def helper():return importlib.import_module('assemble_public_demo_ellipsis_recovery_v2')
def dependencies():
    return {**helper().dependencies(),SELF.relative_to(ROOT).as_posix():base.file_digest(SELF),
        Path(previous.__file__).relative_to(ROOT).as_posix():base.file_digest(Path(previous.__file__))}


def public_provenance(proof):
    module=helper();partition=proof['source_partition']
    value={k:proof[k] for k in ('policy','receipt_sha256','audio_origin','assembled_audio_sha256','physical_parent_native_calls',
        'target_source_span','campaign_sha256','word_campaign_sha256','word_proposal_sha256','word_stress_sha256','gap',
        'human_approval','acoustic_stress_approval','source_and_captions_unchanged','alignment_is_approval')}
    value['source_partition']={'children':[{k:c[k] for k in ('child','source_id','start','end','text','spoken','synthesis_text')}
        for c in partition['children']],'gap':partition['gap'],'source_reconstruction_sha256':partition['source_reconstruction_sha256'],
        'native_reconstruction_sha256':partition['native_reconstruction_sha256'],'original_ellipses_preserved':True}
    value['child_proofs']=[]
    for child in proof['child_proofs']:
        item={k:child[k] for k in ('child','attempt','source_span','native_spec_sha256','native_receipt_sha256','native_audio_sha256',
            'child_crop_receipt_sha256','audio_sha256','sample_count','sample_rate','bounds','human_approval','alignment_is_approval')}
        item.update(full_native_decision=public_decision(child['full_native_decision']),crop_decision=public_decision(child['crop_decision']))
        value['child_proofs'].append(item)
    value['final_full_source_decision']=public_decision(proof['decision'])
    value['normative_evidence_sha256']=base.sha(proof['normative_evidence'])
    value['execution_inventory_at_assembly']=proof['execution_inventory_at_assembly']
    value['budgets']={'physical_natives_per_child':3,'physical_natives_total':6,'parent_assemblies':3,'final_blind_check_pairs':3}
    legacy=proof['v1_no_audio']
    value['v1_no_audio']={k:legacy[k] for k in ('policy','campaign_sha256','zero_physical_native_starts','zero_recognizer_starts','target_sha256')}
    value['v1_no_audio']['inventory_sha256']=base.sha(legacy['files'])
    value['alternative_authorizations']=[{'child':row['child'],'attempt':row['attempt'],'authorization_sha256':base.sha(row['authorization']),
        **{k:row['authorization'][k] for k in ('parent_attempt','parent_receipt_sha256','assembled_audio_sha256','retained_child_attempt',
                                             'failed_stage','missing_results_authorize_alternative','audio_approval')}} for row in proof['alternative_authorizations']]
    value['disclosure']='Separately verified source phrases joined as unchanged PCM with a frozen experiment pause; no full-parent native request ran. Original ellipses/source/captions retained. Alignment supplies coordinates, never audio or stress approval.'
    from package_public_audiobook_demos import clean_json
    clean_json(value);return value


def validated_assembly(context,unit,receipt,receipt_sha256=None):
    module=helper();spec=receipt.get('spec',{});marker=spec.get('ellipsis_phrase_assembly_v2',{})
    if (context.assignment['language']!='Russian' or marker.get('policy')!=module.POLICY
            or type(marker.get('attempt')) is not int or marker['attempt'] not in (1,2,3)
            or receipt.get('take')!=marker['attempt']+29 or receipt.get('unit')!=unit['id']
            or next((u for u in context.assignment['units'] if u['id']==unit['id']),None)!=unit
            or spec.get('source')!=unit or spec.get('generation')!=context.generation
            or spec.get('model')!=context.manifest['speech_model'] or spec.get('runner')!=context.manifest['runner']
            or spec.get('reference')!=unit['palette_reference'] or spec.get('effects')!='disabled'
            or 'request' in spec or spec.get('audio_origin')!='exact-pcm-composite'
            or set(spec)!={'source','generation','model','runner','reference','effects','audio_origin','ellipsis_phrase_assembly_v2'}
            or base.sha(spec)!=receipt.get('spec_sha256') or receipt.get('quality_passed') is not True):
        raise ValueError('Composite changed frozen source/model/reference or falsely implies a parent native request')
    proof=module.validate_ellipsis_assembly_receipt(context.folder,unit,receipt)
    bound,current=module.assembly_receipt(context.folder,unit,marker['attempt'])
    if (current!=receipt or proof.get('policy')!=module.POLICY or proof.get('assembled_audio_sha256')!=receipt['audio_sha256']
            or proof.get('physical_parent_native_calls')!=0 or proof.get('audio_origin')!='exact-pcm-composite'
            or receipt_sha256 is not None and proof.get('receipt_sha256')!=receipt_sha256
            or proof.get('target_source_span')!={k:unit[k] for k in ('id','source_id','start','end','text')}
            or proof.get('source_partition')!=module.partition(unit,bound['word_repair'])
            or proof.get('alignment_is_approval') is not False or proof.get('human_approval') is not False):
        raise ValueError('Composite source/child PCM/provenance changed')
    for child in proof['child_proofs']:
        expected=module.validate_child(context.folder,unit,child['child'],child['attempt'])
        if child!=expected:raise ValueError('Child full/crop evidence differs')
    decision=proof['decision']
    if (decision.get('passed') is not True or len(decision.get('evidence',[]))!=2
            or {(e['role'],e['family']) for e in decision['evidence']}!={('primary','qwen'),('witness','whisper')}):
        raise ValueError('Both final full-source independent recognizers required')
    for evidence in decision['evidence']:
        path=base.checked_relative(context.folder,evidence['path'],evidence['sha256']);check=base.read(path)
        recognizer='qwen' if evidence['role']=='primary' else 'turbo'
        raw=module.check_record(check,unit['spoken'],receipt['audio_sha256'],bound,recognizer)
        if evidence['raw']!=raw or evidence['verifier']!=check['verifier']:raise ValueError('Final raw transcript changed')
    recomputed=module.words.strict_decision(unit,[{k:v for k,v in e.items() if k!='comparison'} for e in decision['evidence']],
        context.blocks[unit['source_id']],bound['word_repair'])
    if recomputed!=decision or module.blind_decision(context.folder,unit,marker['attempt'],'parent')!=decision:
        raise ValueError('Final original-source decision is not reproducible')
    return decision,public_provenance(proof)


class Context(previous.Context):
    def __init__(self,folder):super().__init__(folder);self.ellipsis_assembly_v2_dependencies=dependencies()
    def recheck(self):
        super().recheck()
        if hasattr(self,'ellipsis_assembly_v2_dependencies') and dependencies()!=self.ellipsis_assembly_v2_dependencies:raise ValueError('Composite adapter inputs changed')
    def assess(self,unit,receipt):
        if 'ellipsis_phrase_assembly_v2' not in receipt.get('spec',{}):return super().assess(unit,receipt)
        self.recheck()
        if receipt.get('quality_passed') is not True:return None
        module=helper();attempt=receipt['spec']['ellipsis_phrase_assembly_v2']['attempt'];path=self.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take'])
        if path.read_bytes()!=module.receipt_path(self.folder,unit,attempt).read_bytes() or base.read(path)!=receipt:
            raise ValueError('Canonical composite must mirror private receipt byte-exactly')
        decision,proof=validated_assembly(self,unit,receipt,base.file_digest(path))
        return {'schema':1,'rule':RULE,'module_sha256':base.file_digest(SELF),'base_module_sha256':base.file_digest(Path(base.__file__)),
            'dependencies':self.dependencies,'bindings':self.bindings,'ellipsis_assembly_v2_extension_dependencies':self.ellipsis_assembly_v2_dependencies,
            'unit':unit['id'],'source_id':unit['source_id'],'source_start':unit['start'],'source_end':unit['end'],'source_text':unit['text'],
            'expected_spoken':unit['spoken'],'source_paragraph_sha256':base.sha(self.blocks[unit['source_id']]),'audio_sha256':receipt['audio_sha256'],
            'take_spec_sha256':receipt['spec_sha256'],'take_receipt_sha256':base.file_digest(path),'ellipsis_assembly_v2_provenance':proof,
            'recovery_provenance':None,'context_provenance':None,'english_source_policy':self.english_policy,'decision':decision,'human_approval':False}


def register(context,path):
    module=helper();context.recheck();path=Path(path);path=(path if path.is_absolute() else context.folder/path).resolve();path.relative_to(context.folder)
    original=path.read_bytes();receipt=base.read(path);unit=next((u for u in context.assignment['units'] if u['id']==receipt.get('unit')),None)
    attempt=receipt.get('spec',{}).get('ellipsis_phrase_assembly_v2',{}).get('attempt')
    if unit is None or type(attempt) is not int or attempt not in (1,2,3) or path!=module.receipt_path(context.folder,unit,attempt).resolve():
        raise ValueError('Only explicit private bounded composite receipts can register')
    _,proof=validated_assembly(context,unit,receipt,base.file_digest(path));context.recheck()
    if path.read_bytes()!=original:raise ValueError('Composite changed during registration')
    target=context.folder/'takes'/unit['id']/('take-%02d.json'%receipt['take']);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()!=original:raise ValueError('Never replace a different canonical composite')
    else:
        with target.open('xb') as out:out.write(original)
    value={'schema':1,'rule':RULE,'unit':unit['id'],'take':receipt['take'],'canonical':target.relative_to(context.folder).as_posix(),
        'canonical_receipt_sha256':base.file_digest(target),'adapter_sha256':base.file_digest(SELF),
        'extension_dependencies':context.ellipsis_assembly_v2_dependencies,'ellipsis_assembly_v2_provenance':proof,'human_approval':False}
    base.immutable(path.parent/'assembly-registration.json',value);return value


def private_history(context,known):
    module=helper();rows=[]
    for unit in context.assignment['units']:
        if not (module.home(context.folder,unit)/'campaign.json').exists():continue
        for child in (1,2,None):
            for attempt in (1,2,3):
                directory=module.attempt_home(context.folder,unit,attempt) if child is None else module.child_home(context.folder,unit,child,attempt)
                for kind in (('parent',) if child is None else ('context','crop')):
                    for recognizer in ('qwen','turbo'):
                        path=directory/(kind+'-'+recognizer+'.json')
                        if not path.exists() or base.file_digest(path) in known:continue
                        bound,expected,_,checksum=module.verification_audio(context.folder,unit,attempt,kind,child)
                        raw=module.check_record(base.read(path),expected,checksum,bound,recognizer)
                        rows.append({'unit':unit['id'],'child':child,'attempt':attempt,'kind':kind,'recognizer':recognizer,
                            'heard':raw['heard'],'status':raw['status'],'audio_sha256':checksum,'evidence_sha256':base.file_digest(path),'human_approval':False})
    return rows


def report(context):
    value=previous.report(context);known=set()
    for row in value['units']:
        unit=next(u for u in context.assignment['units'] if u['id']==row['id']);receipts={r['take']:r for r in base.renderer.takes(context.folder,unit)}
        for summary in row['takes']:
            known.update(e['evidence_sha256'] for e in summary['raw_transcripts'])
            if 'ellipsis_phrase_assembly_v2' in receipts[summary['take']]['spec']:
                summary['ellipsis_assembly_v2_provenance']=context.assess(unit,receipts[summary['take']])['ellipsis_assembly_v2_provenance']
    history=private_history(context,known);extra=sum(r['status']!='asr_exact_match' for r in history)
    value.update(prior_resolution_adapter_sha256=value['resolution_adapter_sha256'],resolution_adapter_sha256=base.file_digest(SELF),
        ellipsis_assembly_rule=RULE,ellipsis_assembly_v2_extension_dependencies=context.ellipsis_assembly_v2_dependencies,
        unregistered_ellipsis_assembly_history=history,raw_unregistered_ellipsis_assembly_mismatch_check_count=extra)
    value['raw_mismatch_check_count']+=extra
    value['disclosure']+=' Source-ellipsis assembly preserves selected child PCM and uses an explicitly frozen experiment pause. Each child needs independent full-native and crop checks; the assembled parent separately needs final quality and both full-source recognizers. No full-parent native call, stress certification or human listening approval is implied. '+str(extra)+' additional unregistered raw mismatches remain counted.'
    return value


def resolve(folder,publish=False,complete=False):
    folder=Path(folder).resolve();context=Context(folder);value=report(context)
    path=folder/'resolution-proposals'/('ellipsis-assembly-v2-report-'+base.sha(value)+'.json');base.immutable(path,value)
    count,seconds=finishing.prefix_seconds(context,value);eligible=seconds>=300 or count==len(context.assignment['units'])
    if publish and eligible:base.publish(context,value,3)
    valid,reason=finishing.validate_completion(folder,context,value) if complete else (False,'Not checked')
    result={'report':value,'report_path':str(path),'report_sha256':base.sha(value),'prefix_units':count,'prefix_seconds':seconds,
        'publication_eligible':eligible,'completion_valid':valid,'completion_reason':reason}
    supervision.write(folder/'finishing/current-resolution.json',result);return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--stage',choices=['register','resolve','publish','complete'],required=True);parser.add_argument('--receipt',type=Path);args=parser.parse_args()
    if args.stage=='register':
        if args.receipt is None:parser.error('--receipt required')
        register(Context(args.folder.resolve()),args.receipt);return 0
    result=resolve(args.folder,args.stage=='publish',args.stage in ('publish','complete'))
    print(json.dumps({k:result[k] for k in ('report_sha256','prefix_units','completion_valid','completion_reason')}))
    return int(args.stage in ('publish','complete') and not result['completion_valid'])
if __name__=='__main__':raise SystemExit(main())
