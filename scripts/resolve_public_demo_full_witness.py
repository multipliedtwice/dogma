"""Versioned full Whisper witness adapter; synthesis and decision rules stay fixed.

Full large-v3 independently recognizes only original quality-passed takes 0..5.
The existing source-bound resolver decides whether its raw words resolve a flag.
Context and pronunciation receipts retain their separate frozen strict contracts.
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
import resolve_public_demo_pronunciation as previous

base=previous.base
finishing=previous.finishing
supervision=previous.supervision
SELF=Path(__file__).resolve()
RULE='public-demo-full-whisper-resolution-adapter-v3'


def full_module():
    return importlib.import_module('witness_public_demo_whisper_full')


def dependencies():
    helper=full_module()
    return {**helper.dependencies(),SELF.relative_to(ROOT).as_posix():base.file_digest(SELF),
            Path(previous.__file__).relative_to(ROOT).as_posix():base.file_digest(Path(previous.__file__))}


def original_take(receipt):
    spec=receipt.get('spec',{})
    return (type(receipt.get('take')) is int and 0<=receipt['take']<=5
            and not any(key in spec for key in ('context_recovery','pronunciation_recovery')))


class Context(previous.Context):
    def __init__(self,folder):
        super().__init__(folder)
        self.full_witness_dependencies=dependencies()

    def recheck(self):
        super().recheck()
        if hasattr(self,'full_witness_dependencies') and dependencies()!=self.full_witness_dependencies:
            raise ValueError('Full Whisper adapter/helper dependencies changed')

    def evidence(self,unit,receipt):
        old=super().evidence(unit,receipt)
        if not original_take(receipt) or receipt.get('quality_passed') is not True:
            return old
        helper=full_module()
        additional=helper.evidence(self,unit,receipt)
        seen={(item['path'],item['sha256']) for item in old}
        for item in additional:
            if (item.get('role')!='witness' or item.get('family')!='whisper'
                    or item['verifier'].get('model')!=helper.MODEL):
                raise ValueError('Full Whisper supplies only an independent conventional word witness')
            helper.validate_verifier_config(item['verifier'])
            path=base.checked_relative(self.folder,item['path'],item['sha256'])
            check=base.read(path)
            raw=base.transcript_check(unit['spoken'],check['heard'],receipt['audio_sha256'],base.sha(item['verifier']))
            if (item['raw']!=raw or check.get('verifier')!=item['verifier']
                    or any(check.get(k)!=v for k,v in raw.items())):
                raise ValueError('Full Whisper raw words/audio/configuration cannot be reproduced')
            identity=(item['path'],item['sha256'])
            if identity in seen:
                raise ValueError('Duplicate full Whisper evidence')
            seen.add(identity)
        return [*old,*additional]

    def assess(self,unit,receipt):
        value=super().assess(unit,receipt)
        if value is None or not original_take(receipt):
            return value
        helper=full_module()
        witnesses=[item for item in value['decision']['evidence'] if item['verifier'].get('model')==helper.MODEL]
        value={**value,'full_witness_adapter_sha256':base.file_digest(SELF),
               'full_witness_extension_dependencies':self.full_witness_dependencies,
               'full_witness_provenance':{'rule':RULE,'model':helper.MODEL,'revision':helper.REVISION,
                   'files':helper.FILES,'implementation_sha256':base.file_digest(Path(helper.__file__)),
                   'adapter_sha256':base.file_digest(SELF),'audio_sha256':receipt['audio_sha256'],
                   'source_text_unchanged':True,'native_input_unchanged':True,'human_approval':False,
                   'check_count':len(witnesses),
                   'evidence':[{'receipt_sha256':item['sha256'],'verifier_sha256':base.sha(item['verifier']),
                                'heard':item['raw']['heard'],'status':item['raw']['status']}
                               for item in witnesses]}}
        return value


def report(context):
    value=previous.report(context)
    checks,mismatches=0,0
    for row in value['units']:
        unit=next(unit for unit in context.assignment['units'] if unit['id']==row['id'])
        receipts={receipt['take']:receipt for receipt in base.renderer.takes(context.folder,unit)}
        for summary in row['takes']:
            receipt=receipts[summary['take']]
            if not original_take(receipt):
                continue
            proof=context.assess(unit,receipt)['full_witness_provenance']
            summary['full_witness_provenance']=previous.public_safe(proof)
            summary['full_witness_adapter_sha256']=base.file_digest(SELF)
            checks+=len(proof['evidence'])
            mismatches+=sum(item['status']!='asr_exact_match' for item in proof['evidence'])
        if row.get('selected') and row['selected'].get('full_witness_provenance'):
            row['full_witness_provenance']=row['selected']['full_witness_provenance']
    value.update(pronunciation_resolution_adapter_sha256=value['resolution_adapter_sha256'],
                 resolution_adapter_sha256=base.file_digest(SELF),full_witness_rule=RULE,
                 full_witness_extension_dependencies=context.full_witness_dependencies,
                 full_witness_check_count=checks,full_witness_raw_mismatch_check_count=mismatches)
    value['disclosure']+=(' Full Whisper large-v3 supplies additional source-blind word recognition for unchanged original audio. '
                          'Its raw checks are already included in witness/total counts; no pronunciation equivalence or decision rule is added.')
    return value


def resolve(folder,publish=False,complete=False):
    folder=Path(folder).resolve()
    context=Context(folder)
    value=report(context)
    destination=folder/'resolution-proposals'/('full-witness-report-'+base.sha(value)+'.json')
    base.immutable(destination,value)
    count,seconds=finishing.prefix_seconds(context,value)
    eligible=seconds>=300 or count==len(context.assignment['units'])
    if publish and eligible:
        base.publish(context,value,3)
    valid,reason=finishing.validate_completion(folder,context,value) if complete else (False,'Not checked')
    snapshot={'report':value,'report_path':str(destination),'report_sha256':base.sha(value),
              'prefix_units':count,'prefix_seconds':seconds,'publication_eligible':eligible,
              'completion_valid':valid,'completion_reason':reason}
    supervision.write(folder/'finishing/current-resolution.json',snapshot)
    return snapshot


def main():
    os.environ['PYTHONUTF8']='1'
    os.environ['PYTHONIOENCODING']='utf-8'
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--stage',choices=['resolve','publish','complete'],required=True)
    args=parser.parse_args()
    value=resolve(args.folder,args.stage=='publish',args.stage=='complete')
    print(json.dumps({k:value[k] for k in ('report_sha256','prefix_units','completion_valid','completion_reason')},ensure_ascii=False),flush=True)
    return 1 if args.stage=='complete' and not value['completion_valid'] else 0


if __name__=='__main__':
    raise SystemExit(main())
