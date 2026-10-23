"""V6: source-bound CTC plus full Whisper adjudication of acoustic disagreement.

Two distinct acoustic families must recover every ordinary word literally.
Only the existing frozen source/cast proper-noun slots may differ in spelling.
CTC evidence is appended after the earlier resolver so CTC exact alone cannot
trigger its any-exact shortcut. Raw primary disagreement remains disclosed.
"""
from __future__ import annotations
import argparse
import importlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import resolve_public_demo_sustained_sound as previous
import public_demo_english_agreement as english
from resolve_public_demo_pronunciation import public_safe

base=previous.base
finishing=previous.finishing
supervision=previous.supervision
SELF=Path(__file__).resolve()
RULE='public-demo-independent-acoustic-word-adjudication-v6'


def ctc_module():
    return importlib.import_module('witness_public_demo_wav2vec2')


def dependencies():
    return {**ctc_module().dependencies(),**{path.relative_to(ROOT).as_posix():base.file_digest(path)
            for path in (SELF,Path(previous.__file__),Path(english.__file__))}}


def strict_name_comparison(unit,heard,policy):
    wanted,actual=base.word_tokens(unit['spoken']),base.word_tokens(heard)
    failed={'passed':False,'canonical_words':actual,'equivalences':[],'tolerated_deviations':[]}
    if not wanted or len(wanted)!=len(actual):
        return failed
    changes=[]
    for index,(source,recognized) in enumerate(zip(wanted,actual)):
        if source==recognized:
            continue
        choices=[change for source_end,heard_end,change in english.options(unit,wanted,actual,index,index,policy)
                 if source_end==index+1 and heard_end==index+1 and change.get('rule')=='proper-noun-slot-v1'
                 and change.get('source_tokens')==[source] and change.get('heard_tokens')==[recognized]]
        if not choices:
            return failed
        changes.append(choices[0])
    return {'passed':True,'canonical_words':wanted,'equivalences':[],'tolerated_deviations':changes}


def adjudicate(unit,decision,ctc_evidence,policy,audio_sha256):
    """Append raw CTC evidence; new acceptance needs BOTH acoustic families."""
    for item in ctc_evidence:
        config=item['verifier'];raw=item['raw']
        if (item.get('role')!='witness' or item.get('family')!='wav2vec2'
                or config.get('expected_text_given_to_recognizer') is not False or config.get('local_files_only') is not True
                or raw!=base.transcript_check(unit['spoken'],raw['heard'],audio_sha256,base.sha(config))):
            raise ValueError('Adjudication requires exact source-bound uncued CTC evidence')
    compared=[{**item,'comparison':strict_name_comparison(unit,item['raw']['heard'],policy)} for item in ctc_evidence]
    extended={**decision,'evidence':[*decision['evidence'],*compared]}
    if decision.get('passed') or not compared:
        return extended,None
    ctcs=[item for item in compared if item['comparison']['passed']]
    whispers=[]
    for item in decision['evidence']:
        if (item.get('role')=='witness' and item.get('family')=='whisper'
                and item['verifier'].get('model')=='openai/whisper-large-v3'):
            config=item['verifier'];raw=item['raw']
            if (config.get('expected_text_given_to_recognizer') is not False or config.get('local_files_only') is not True
                    or raw!=base.transcript_check(unit['spoken'],raw['heard'],audio_sha256,base.sha(config))):
                raise ValueError('Full Whisper adjudication witness differs from original source/audio')
            comparison=strict_name_comparison(unit,item['raw']['heard'],policy)
            if comparison['passed']:
                whispers.append({**item,'adjudication_comparison':comparison})
    if not ctcs or not whispers:
        return extended,None
    ctc=sorted(ctcs,key=lambda item:item['path'])[0]
    whisper=sorted(whispers,key=lambda item:item['path'])[0]
    pairs=[(ctc,ctc['comparison']),(whisper,whisper['adjudication_comparison'])]
    proof={'rule':RULE,'adapter_sha256':base.file_digest(SELF),'source_text':unit['text'],'expected_spoken':unit['spoken'],
           'source_span':{key:unit[key] for key in ('id','source_id','start','end','text')},
           'ordinary_words':'Literal source word equality; no homophone, word-boundary or ordinary-word substitution rule.',
           'permitted_name_rule':'proper-noun-slot-v1','english_policy_sha256':base.sha(policy),
           'english_name_helper_sha256':base.file_digest(Path(english.__file__)),
           'source_text_unchanged':True,'native_input_unchanged':True,'human_approval':False,
           'scope':'Two source-blind distinct acoustic architectures agree on ordinary words; other raw models disagree. Not exact global recognition or acoustic stress certification.',
           'witnesses':[{'model':item['verifier']['model'],'family':item['family'],'receipt_sha256':item['sha256'],
                         'verifier_sha256':base.sha(item['verifier']),'audio_sha256':item['raw']['audio_sha256'],
                         'heard':item['raw']['heard'],'raw_status':item['raw']['status'],'comparison':comparison}
                        for item,comparison in pairs]}
    deviations=[{**change,'evidence':item['path'],'evidence_sha256':item['sha256']}
                for item,comparison in pairs for change in comparison['tolerated_deviations']]
    resolved={**extended,'passed':True,'status':'acoustic_model_disagreement',
              'resolution':'independent_ctc_full_whisper_source_bound_adjudication',
              'selected_evidence':ctc['path'],'prior_resolution':decision.get('resolution'),
              'equivalences':[],'tolerated_deviations':deviations}
    return resolved,proof


class Context(previous.Context):
    def __init__(self,folder):
        super().__init__(folder)
        self.acoustic_adjudication_dependencies=dependencies()

    def recheck(self):
        super().recheck()
        if hasattr(self,'acoustic_adjudication_dependencies') and dependencies()!=self.acoustic_adjudication_dependencies:
            raise ValueError('Acoustic adjudication adapter/helper dependencies changed')

    def ctc_evidence(self,unit,receipt):
        helper=ctc_module();items=helper.evidence(self,unit,receipt);seen=set()
        for item in items:
            config=item['verifier']
            if (item.get('role')!='witness' or item.get('family')!='wav2vec2'
                    or config.get('model')!=helper.MODEL or config.get('policy')!=helper.POLICY):
                raise ValueError('CTC adjudication requires the pinned distinct acoustic family')
            helper.validate_verifier_config(config)
            path=base.checked_relative(self.folder,item['path'],item['sha256'])
            check=base.read(path)
            raw=base.transcript_check(unit['spoken'],check['heard'],receipt['audio_sha256'],base.sha(config))
            if item['raw']!=raw or check.get('verifier')!=config or any(check.get(k)!=v for k,v in raw.items()):
                raise ValueError('CTC raw audio/transcript/configuration cannot be reproduced')
            identity=(item['path'],item['sha256'])
            if identity in seen:raise ValueError('Duplicate CTC evidence')
            seen.add(identity)
        return items

    def assess(self,unit,receipt):
        value=super().assess(unit,receipt)
        if (value is None or self.assignment['language']!='English'
                or not previous.previous.previous.original_take(receipt)):
            return value
        if (next((assigned for assigned in self.assignment['units'] if assigned['id']==unit['id']),None)!=unit
                or receipt['spec']['source']!=unit or receipt['spec'].get('generation')!=self.generation
                or value['source_text']!=unit['text'] or value['expected_spoken']!=unit['spoken']
                or value['audio_sha256']!=receipt['audio_sha256']):
            raise ValueError('Acoustic adjudication changed the frozen source/audio binding')
        items=self.ctc_evidence(unit,receipt)
        if not items:return value
        identities={(item['path'],item['sha256']) for item in value['decision']['evidence']}
        if any((item['path'],item['sha256']) in identities for item in items):
            raise ValueError('CTC evidence must enter raw counters only once')
        decision,proof=adjudicate(unit,value['decision'],items,self.english_policy,receipt['audio_sha256'])
        ctc_proof={'rule':RULE,'model':ctc_module().MODEL,'revision':ctc_module().REVISION,'files':ctc_module().FILES,
                   'implementation_sha256':base.file_digest(Path(ctc_module().__file__)),
                   'adapter_sha256':base.file_digest(SELF),'check_count':len(items),'human_approval':False,
                   'evidence':[{'receipt_sha256':item['sha256'],'verifier_sha256':base.sha(item['verifier']),
                                'heard':item['raw']['heard'],'status':item['raw']['status'],'audio_sha256':item['raw']['audio_sha256']}
                               for item in items]}
        return {**value,'decision':decision,'ctc_witness_provenance':ctc_proof,'acoustic_adjudication_provenance':proof,
                'acoustic_adjudication_extension_dependencies':self.acoustic_adjudication_dependencies,
                'acoustic_adjudication_adapter_sha256':base.file_digest(SELF)}


def report(context):
    value=previous.report(context);checks,mismatches=0,0
    for row in value['units']:
        unit=next(unit for unit in context.assignment['units'] if unit['id']==row['id'])
        receipts={receipt['take']:receipt for receipt in base.renderer.takes(context.folder,unit)}
        for summary in row['takes']:
            assessed=context.assess(unit,receipts[summary['take']])
            proof=assessed.get('ctc_witness_provenance')
            if not proof:continue
            summary['ctc_witness_provenance']=public_safe(proof)
            summary['acoustic_adjudication_provenance']=public_safe(assessed.get('acoustic_adjudication_provenance'))
            checks+=proof['check_count'];mismatches+=sum(item['status']!='asr_exact_match' for item in proof['evidence'])
        if row.get('selected') and row['selected'].get('acoustic_adjudication_provenance'):
            row['acoustic_adjudication_provenance']=row['selected']['acoustic_adjudication_provenance']
    accepted=sum(bool(row.get('acoustic_adjudication_provenance')) for row in value['units'])
    value.update(sustained_sound_resolution_adapter_sha256=value['resolution_adapter_sha256'],
                 resolution_adapter_sha256=base.file_digest(SELF),acoustic_adjudication_rule=RULE,
                 acoustic_adjudication_extension_dependencies=context.acoustic_adjudication_dependencies,
                 acoustic_adjudicated_units=accepted,ctc_witness_check_count=checks,ctc_witness_raw_mismatch_check_count=mismatches)
    value['resolved_with_disclosure_unit_count']+=accepted
    value['disclosure']+=' A separate CPU CTC acoustic architecture and full Whisper may adjudicate an ordinary-word disagreement only when every ordinary word is literally source-exact and differences are exclusively existing source-bound proper-noun slots. Such passages are labelled acoustic model disagreement, with raw primary/witness mismatches retained, rather than globally exact recognition or human approval.'
    return value


def resolve(folder,publish=False,complete=False):
    folder=Path(folder).resolve();context=Context(folder);value=report(context)
    destination=folder/'resolution-proposals'/('acoustic-adjudication-report-'+base.sha(value)+'.json')
    base.immutable(destination,value)
    count,seconds=finishing.prefix_seconds(context,value)
    eligible=seconds>=300 or count==len(context.assignment['units'])
    if publish and eligible:base.publish(context,value,3)
    valid,reason=finishing.validate_completion(folder,context,value) if complete else (False,'Not checked')
    snapshot={'report':value,'report_path':str(destination),'report_sha256':base.sha(value),'prefix_units':count,
              'prefix_seconds':seconds,'publication_eligible':eligible,'completion_valid':valid,'completion_reason':reason}
    supervision.write(folder/'finishing/current-resolution.json',snapshot)
    return snapshot


def main():
    os.environ['PYTHONUTF8']='1';os.environ['PYTHONIOENCODING']='utf-8'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--stage',choices=['resolve','publish','complete'],required=True)
    args=parser.parse_args();value=resolve(args.folder,args.stage=='publish',args.stage=='complete')
    print(json.dumps({key:value[key] for key in ('report_sha256','prefix_units','completion_valid','completion_reason')},ensure_ascii=False),flush=True)
    return 1 if args.stage=='complete' and not value['completion_valid'] else 0


if __name__=='__main__':raise SystemExit(main())
