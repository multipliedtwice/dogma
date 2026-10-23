"""V8: Russian CTC/full-Whisper adjudication using existing phonology rules only.

CTC is appended after earlier decisions: a lone CTC exact cannot trigger their
any-exact shortcut. Both distinct acoustic families must canonically pass the
unchanged Russian comparison with zero tolerated name/interjection deviations.
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
import resolve_public_demo_ru_pronunciation as previous
from resolve_public_demo_full_witness import original_take
from resolve_public_demo_pronunciation import public_safe

base=previous.base
finishing=previous.finishing
supervision=previous.supervision
SELF=Path(__file__).resolve()
RULE='public-demo-ru-independent-acoustic-adjudication-v8'


def ru_ctc_module():
    return importlib.import_module('witness_public_demo_wav2vec2_ru')


def dependencies():
    return {**ru_ctc_module().dependencies(),**{path.relative_to(ROOT).as_posix():base.file_digest(path)
            for path in (SELF,Path(previous.__file__),Path(base.__file__))}}


def checked_raw(unit,item,audio_sha256,family):
    config=item['verifier'];raw=item['raw']
    if (item.get('role')!='witness' or item.get('family')!=family
            or config.get('expected_text_given_to_recognizer') is not False or config.get('local_files_only') is not True
            or raw!=base.transcript_check(unit['spoken'],raw['heard'],audio_sha256,base.sha(config))):
        raise ValueError('RU acoustic witness is not exact source-bound uncued raw evidence')
    return raw


def canonical_comparison(unit,item,paragraph,audio_sha256,family):
    raw=checked_raw(unit,item,audio_sha256,family)
    comparison=base.russian_equivalence(unit,raw['heard'],paragraph,raw['audio_sha256'],raw['verifier_sha256'])
    eligible=(comparison.get('passed') is True and comparison.get('canonical_words')==base.word_tokens(unit['spoken'])
              and comparison.get('tolerated_deviations')==[])
    return comparison,eligible


def adjudicate(unit,decision,ctc_evidence,paragraph,audio_sha256):
    compared=[]
    for item in ctc_evidence:
        comparison,eligible=canonical_comparison(unit,item,paragraph,audio_sha256,'wav2vec2')
        compared.append({**item,'comparison':comparison,'ru_adjudication_eligible':eligible})
    extended={**decision,'evidence':[*decision['evidence'],*compared]}
    if decision.get('passed') or not compared:return extended,None
    ctcs=[item for item in compared if item['ru_adjudication_eligible']]
    whispers=[]
    for item in decision['evidence']:
        if (item.get('role')=='witness' and item.get('family')=='whisper'
                and item['verifier'].get('model')=='openai/whisper-large-v3'):
            comparison,eligible=canonical_comparison(unit,item,paragraph,audio_sha256,'whisper')
            if eligible:whispers.append({**item,'ru_adjudication_comparison':comparison})
    if not ctcs or not whispers:return extended,None
    ctc=sorted(ctcs,key=lambda item:item['path'])[0];whisper=sorted(whispers,key=lambda item:item['path'])[0]
    pair=[(ctc,ctc['comparison']),(whisper,whisper['ru_adjudication_comparison'])]
    proof={'rule':RULE,'adapter_sha256':base.file_digest(SELF),'russian_comparison_module_sha256':base.file_digest(Path(base.__file__)),
           'source_span':{key:unit[key] for key in ('id','source_id','start','end','text')},
           'source_paragraph_sha256':base.sha(paragraph),'expected_spoken':unit['spoken'],'audio_sha256':audio_sha256,
           'source_text_unchanged':True,'native_input_unchanged':True,'required_tolerated_deviations':0,
           'scope':'Distinct CTC and full Whisper architectures canonically agree under unchanged Russian phonology rules; other acoustic models disagree. No new lexical rule, stress certification or human approval.',
           'human_approval':False,
           'witnesses':[{'model':item['verifier']['model'],'family':item['family'],'receipt_sha256':item['sha256'],
                         'verifier_sha256':base.sha(item['verifier']),'audio_sha256':item['raw']['audio_sha256'],
                         'heard':item['raw']['heard'],'raw_status':item['raw']['status'],'comparison':comparison}
                        for item,comparison in pair]}
    equivalents=[{**change,'evidence':item['path'],'evidence_sha256':item['sha256']}
                 for item,comparison in pair for change in comparison.get('equivalences',[])]
    resolved={**extended,'passed':True,'status':'acoustic_model_disagreement',
              'resolution':'independent_ru_ctc_full_whisper_existing_phonology_adjudication',
              'selected_evidence':ctc['path'],'prior_resolution':decision.get('resolution'),
              'equivalences':equivalents,'tolerated_deviations':[]}
    return resolved,proof


class Context(previous.Context):
    def __init__(self,folder):
        super().__init__(folder)
        self.ru_acoustic_adjudication_dependencies=dependencies()

    def recheck(self):
        super().recheck()
        if hasattr(self,'ru_acoustic_adjudication_dependencies') and dependencies()!=self.ru_acoustic_adjudication_dependencies:
            raise ValueError('RU acoustic adjudication adapter/helper dependencies changed')

    def ru_ctc_evidence(self,unit,receipt):
        helper=ru_ctc_module();items=helper.evidence(self,unit,receipt);seen=set()
        for item in items:
            config=item['verifier'];parts=Path(item['path']).parts
            if (item.get('role')!='witness' or item.get('family')!='wav2vec2' or config.get('model')!=helper.MODEL
                    or config.get('policy')!=helper.POLICY or not parts or parts[0]!='ru-ctc-witness'):
                raise ValueError('RU adjudication requires the pinned production CTC namespace/model, never diagnostic evidence')
            helper.validate_verifier_config(config)
            path=base.checked_relative(self.folder,item['path'],item['sha256']);check=base.read(path)
            raw=checked_raw(unit,item,receipt['audio_sha256'],'wav2vec2')
            if check.get('verifier')!=config or any(check.get(key)!=value for key,value in raw.items()):
                raise ValueError('RU CTC raw audio/transcript/configuration cannot be reproduced')
            identity=(item['path'],item['sha256'])
            if identity in seen:raise ValueError('Duplicate RU CTC evidence')
            seen.add(identity)
        return items

    def assess(self,unit,receipt):
        value=super().assess(unit,receipt)
        if value is None or self.assignment['language']!='Russian' or not original_take(receipt):return value
        if (next((assigned for assigned in self.assignment['units'] if assigned['id']==unit['id']),None)!=unit
                or receipt['spec']['source']!=unit or receipt['spec'].get('generation')!=self.generation
                or value['source_text']!=unit['text'] or value['expected_spoken']!=unit['spoken']
                or value['audio_sha256']!=receipt['audio_sha256']):
            raise ValueError('RU acoustic adjudication changed frozen source/audio binding')
        items=self.ru_ctc_evidence(unit,receipt)
        if not items:return value
        old={(item['path'],item['sha256']) for item in value['decision']['evidence']}
        if any((item['path'],item['sha256']) in old for item in items):
            raise ValueError('RU CTC evidence may enter raw counters only once')
        decision,proof=adjudicate(unit,value['decision'],items,self.blocks[unit['source_id']],receipt['audio_sha256'])
        helper=ru_ctc_module()
        ctc_proof={'rule':RULE,'model':helper.MODEL,'revision':helper.REVISION,'files':helper.FILES,
                   'implementation_sha256':base.file_digest(Path(helper.__file__)),'adapter_sha256':base.file_digest(SELF),
                   'check_count':len(items),'human_approval':False,
                   'evidence':[{'receipt_sha256':item['sha256'],'verifier_sha256':base.sha(item['verifier']),
                                'heard':item['raw']['heard'],'status':item['raw']['status'],'audio_sha256':item['raw']['audio_sha256']}
                               for item in items]}
        return {**value,'decision':decision,'ru_ctc_witness_provenance':ctc_proof,'ru_acoustic_adjudication_provenance':proof,
                'ru_acoustic_adjudication_extension_dependencies':self.ru_acoustic_adjudication_dependencies,
                'ru_acoustic_adjudication_adapter_sha256':base.file_digest(SELF)}


def report(context):
    value=previous.report(context);checks,mismatches=0,0
    for row in value['units']:
        unit=next(unit for unit in context.assignment['units'] if unit['id']==row['id'])
        receipts={receipt['take']:receipt for receipt in base.renderer.takes(context.folder,unit)}
        for summary in row['takes']:
            assessed=context.assess(unit,receipts[summary['take']]);proof=assessed.get('ru_ctc_witness_provenance')
            if not proof:continue
            summary['ru_ctc_witness_provenance']=public_safe(proof)
            summary['ru_acoustic_adjudication_provenance']=public_safe(assessed.get('ru_acoustic_adjudication_provenance'))
            checks+=proof['check_count'];mismatches+=sum(item['status']!='asr_exact_match' for item in proof['evidence'])
        if row.get('selected') and row['selected'].get('ru_acoustic_adjudication_provenance'):
            row['ru_acoustic_adjudication_provenance']=row['selected']['ru_acoustic_adjudication_provenance']
    accepted=sum(bool(row.get('ru_acoustic_adjudication_provenance')) for row in value['units'])
    value.update(ru_pronunciation_resolution_adapter_sha256=value['resolution_adapter_sha256'],
                 resolution_adapter_sha256=base.file_digest(SELF),ru_acoustic_adjudication_rule=RULE,
                 ru_acoustic_adjudication_extension_dependencies=context.ru_acoustic_adjudication_dependencies,
                 ru_acoustic_adjudicated_units=accepted,ru_ctc_witness_check_count=checks,ru_ctc_witness_raw_mismatch_check_count=mismatches)
    value['resolved_with_disclosure_unit_count']+=accepted
    value['disclosure']+=' Russian CTC plus full Whisper may adjudicate a model disagreement only when both canonically pass the unchanged Russian phonology rules with zero tolerated name/interjection deviations. All raw primary and witness checks remain counted once. No diagnostic transcript is promoted, new lexical equivalence introduced, acoustic stress certified or human approval implied.'
    return value


def resolve(folder,publish=False,complete=False):
    folder=Path(folder).resolve();context=Context(folder);value=report(context)
    destination=folder/'resolution-proposals'/('ru-acoustic-adjudication-report-'+base.sha(value)+'.json');base.immutable(destination,value)
    count,seconds=finishing.prefix_seconds(context,value);eligible=seconds>=300 or count==len(context.assignment['units'])
    if publish and eligible:base.publish(context,value,3)
    valid,reason=finishing.validate_completion(folder,context,value) if complete else (False,'Not checked')
    snapshot={'report':value,'report_path':str(destination),'report_sha256':base.sha(value),'prefix_units':count,
              'prefix_seconds':seconds,'publication_eligible':eligible,'completion_valid':valid,'completion_reason':reason}
    supervision.write(folder/'finishing/current-resolution.json',snapshot);return snapshot


def main():
    os.environ['PYTHONUTF8']='1';os.environ['PYTHONIOENCODING']='utf-8'
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--stage',choices=['resolve','publish','complete'],required=True)
    args=parser.parse_args();value=resolve(args.folder,args.stage=='publish',args.stage=='complete')
    print(json.dumps({key:value[key] for key in ('report_sha256','prefix_units','completion_valid','completion_reason')},ensure_ascii=False),flush=True)
    return 1 if args.stage=='complete' and not value['completion_valid'] else 0


if __name__=='__main__':raise SystemExit(main())
