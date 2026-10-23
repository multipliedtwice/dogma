"""Versioned beam-word witness adapter; original audio and resolution rules stay fixed.

Greedy and beam full-Whisper receipts remain separate evidence even though they
use the same model. Existing context/pronunciation contracts are unchanged.
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
import resolve_public_demo_full_witness as previous

base=previous.base
finishing=previous.finishing
supervision=previous.supervision
SELF=Path(__file__).resolve()
RULE='public-demo-beam-whisper-resolution-adapter-v4'


def beam_module():
    return importlib.import_module('witness_public_demo_whisper_beam')


def dependencies():
    helper=beam_module()
    return {**helper.dependencies(),SELF.relative_to(ROOT).as_posix():base.file_digest(SELF),
            Path(previous.__file__).relative_to(ROOT).as_posix():base.file_digest(Path(previous.__file__))}


def beam_provenance(receipt,evidence):
    helper=beam_module()
    return {'rule':RULE,'policy':helper.POLICY,'model':helper.MODEL,'revision':helper.REVISION,
            'files':helper.FILES,'implementation_sha256':base.file_digest(Path(helper.__file__)),
            'adapter_sha256':base.file_digest(SELF),'audio_sha256':receipt['audio_sha256'],
            'source_text_unchanged':True,'native_input_unchanged':True,'human_approval':False,
            'check_count':len(evidence),
            'evidence':[{'receipt_sha256':item['sha256'],'verifier_sha256':base.sha(item['verifier']),
                         'heard':item['raw']['heard'],'status':item['raw']['status'],
                         'decoding':{key:item['verifier'][key] for key in ('num_beams','do_sample') if key in item['verifier']}}
                        for item in evidence]}


class Context(previous.Context):
    def __init__(self,folder):
        super().__init__(folder)
        self.beam_witness_dependencies=dependencies()

    def recheck(self):
        super().recheck()
        if hasattr(self,'beam_witness_dependencies') and dependencies()!=self.beam_witness_dependencies:
            raise ValueError('Beam Whisper adapter/helper dependencies changed')

    def evidence(self,unit,receipt):
        old=super().evidence(unit,receipt)
        if not previous.original_take(receipt) or receipt.get('quality_passed') is not True:
            return old
        helper=beam_module()
        additional=helper.evidence(self,unit,receipt)
        seen={(item['path'],item['sha256']) for item in old}
        for item in additional:
            config=item['verifier']
            if (item.get('role')!='witness' or item.get('family')!='whisper'
                    or config.get('model')!=helper.MODEL or config.get('policy')!=helper.POLICY):
                raise ValueError('Beam Whisper must be a separately pinned independent word witness')
            helper.validate_verifier_config(config)
            path=base.checked_relative(self.folder,item['path'],item['sha256'])
            check=base.read(path)
            raw=base.transcript_check(unit['spoken'],check['heard'],receipt['audio_sha256'],base.sha(config))
            if (item['raw']!=raw or check.get('verifier')!=config or any(check.get(k)!=v for k,v in raw.items())):
                raise ValueError('Beam Whisper raw words/audio/configuration cannot be reproduced')
            identity=(item['path'],item['sha256'])
            if identity in seen:
                raise ValueError('Duplicate beam Whisper evidence')
            seen.add(identity)
        return [*old,*additional]

    def assess(self,unit,receipt):
        value=super().assess(unit,receipt)
        if value is None or not previous.original_take(receipt):
            return value
        helper=beam_module()
        evidence=value['decision']['evidence']
        beam=[item for item in evidence if item['verifier'].get('policy')==helper.POLICY]
        # Frozen v3 groups by model name. Beam uses that same model, so preserve
        # its actual greedy proof separately using the exact verifier policy.
        greedy_policy=previous.full_module().POLICY
        greedy_hashes={item['sha256'] for item in evidence if item['verifier'].get('policy')==greedy_policy}
        greedy={**value['full_witness_provenance']}
        greedy['evidence']=[item for item in greedy['evidence'] if item['receipt_sha256'] in greedy_hashes]
        greedy['check_count']=len(greedy['evidence'])
        return {**value,'full_witness_provenance':greedy,'beam_witness_provenance':beam_provenance(receipt,beam),
                'beam_witness_adapter_sha256':base.file_digest(SELF),
                'beam_witness_extension_dependencies':self.beam_witness_dependencies}


def report(context):
    value=previous.report(context)
    checks,mismatches=0,0
    for row in value['units']:
        unit=next(unit for unit in context.assignment['units'] if unit['id']==row['id'])
        receipts={receipt['take']:receipt for receipt in base.renderer.takes(context.folder,unit)}
        for summary in row['takes']:
            receipt=receipts[summary['take']]
            if not previous.original_take(receipt):
                continue
            proof=context.assess(unit,receipt)['beam_witness_provenance']
            summary['beam_witness_provenance']=previous.previous.public_safe(proof)
            summary['beam_witness_adapter_sha256']=base.file_digest(SELF)
            checks+=len(proof['evidence'])
            mismatches+=sum(item['status']!='asr_exact_match' for item in proof['evidence'])
        if row.get('selected') and row['selected'].get('beam_witness_provenance'):
            row['beam_witness_provenance']=row['selected']['beam_witness_provenance']
    value.update(full_witness_resolution_adapter_sha256=value['resolution_adapter_sha256'],
                 resolution_adapter_sha256=base.file_digest(SELF),beam_witness_rule=RULE,
                 beam_witness_extension_dependencies=context.beam_witness_dependencies,
                 beam_witness_check_count=checks,beam_witness_raw_mismatch_check_count=mismatches)
    value['disclosure']+=(' Source-blind full Whisper beam recognition is additional conventional word evidence on unchanged original audio. '
                          'Greedy and beam receipts are disclosed separately; their raw checks are already included once in witness/total counts. No decision rule is added.')
    return value


def resolve(folder,publish=False,complete=False):
    folder=Path(folder).resolve()
    context=Context(folder)
    value=report(context)
    destination=folder/'resolution-proposals'/('beam-witness-report-'+base.sha(value)+'.json')
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
    print(json.dumps({key:value[key] for key in ('report_sha256','prefix_units','completion_valid','completion_reason')},ensure_ascii=False),flush=True)
    return 1 if args.stage=='complete' and not value['completion_valid'] else 0


if __name__=='__main__':
    raise SystemExit(main())
