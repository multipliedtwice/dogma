"""Independent audio-only transcription for a rendered chapter candidate.

ASR disagreements are review flags, not human verdicts or automatic label edits.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import digest,file_digest,write_json
from rimsky.speech_quality import transcript_check
from higgs_trial_io import read_json

def read(p): return read_json(p)

def audit(folder,follow=False):
    import torch
    from qwen_asr import Qwen3ASRModel
    torch.set_num_threads(6)
    prefix='training-work/models/Qwen3-ASR-0.6B'
    model_path=ROOT/prefix
    files={p.relative_to(ROOT).as_posix():file_digest(p) for p in model_path.rglob('*')
           if p.is_file() and '.cache' not in p.parts}
    signature=digest(files)
    model=Qwen3ASRModel.from_pretrained(str(model_path),device_map='cpu',dtype=torch.float32,
            attn_implementation='sdpa',local_files_only=True,max_inference_batch_size=4)
    plan_path=folder/'render-plan.json'
    while not plan_path.exists():
        if not follow: raise ValueError('No render plan')
        time.sleep(3)
    while follow and not (folder/'receipts.json').exists():
        time.sleep(1)
    plan=read(plan_path); binding=file_digest(plan_path)
    result_path=folder/'word-checks.json'
    report=read(result_path) if result_path.exists() else {'plan_sha256':binding,'verifier_files':files,'checks':{},
            'limits':'Independent ASR flags words; it does not approve identity, pronunciation or artistic quality.'}
    if report['plan_sha256']!=binding or report['verifier_files']!=files:
        raise ValueError('Word audit binding changed')
    units={r['id']:r for r in plan['units']}
    while True:
        if file_digest(plan_path)!=binding: raise ValueError('Render plan changed during audit')
        receipt=read(folder/'receipts.json') if (folder/'receipts.json').exists() else {'takes':{}}
        pending=[(key,row) for key,row in receipt['takes'].items() if key not in report['checks']]
        stale=[];ready=[]
        for key,row in pending:
            actual=file_digest(folder/row['audio'])
            if actual!=row['audio_sha256']:
                if not follow:raise ValueError('Audio changed before audit')
                stale.append({'id':key,'expected':row['audio_sha256'],'actual':actual})
            else:ready.append((key,row))
        if stale!=report.get('stale_takes',[]):
            report['stale_takes']=stale
            write_json(result_path,report)
        pending=ready
        for i in range(0,len(pending),4):
            batch=pending[i:i+4]
            paths=[]
            for key,row in batch:
                path=folder/row['audio']
                if file_digest(path)!=row['audio_sha256']: raise ValueError('Audio changed before audit')
                paths.append(str(path))
            heard=model.transcribe(paths,language='Russian')
            if len(heard)!=len(batch): raise ValueError('Incomplete ASR batch')
            for (key,row),result in zip(batch,heard):
                if file_digest(folder/row['audio'])!=row['audio_sha256']: raise ValueError('Audio changed during audit')
                check=transcript_check(units[key]['spoken'],result.text,row['audio_sha256'],signature)
                report['checks'][key]={**check,'audio':row['audio'],'synthesis_key':row['key']}
            write_json(result_path,report)
            total=len(report['checks']); exact=sum(c['status']=='asr_exact_match' for c in report['checks'].values())
            print('AUDIT',total,'/',len(units),'exact',exact,flush=True)
        if len(report['checks'])==len(units) and not stale or not follow: break
        time.sleep(3)
    return {'audited':len(report['checks']),'exact':sum(c['status']=='asr_exact_match' for c in report['checks'].values())}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);p.add_argument('--follow',action='store_true')
    a=p.parse_args();print(json.dumps(audit(a.folder.resolve(),a.follow)))
