"""Audio-only CPU word check for GPU contention; preserves the pinned 1.7B model."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import digest,file_digest,write_json
from rimsky.speech_quality import transcript_check
from higgs_trial_io import read_json

def read(p):return read_json(p)

def audit(folders,flags_only=False):
    import torch
    from qwen_asr import Qwen3ASRModel
    torch.set_num_threads(4)
    prefix='training-work/models/Qwen3-ASR-1.7B'
    model_path=ROOT/prefix
    files={p.relative_to(ROOT).as_posix():file_digest(p) for p in model_path.rglob('*')
           if p.is_file() and '.cache' not in p.parts}
    config={'model_files':files,'device':'cpu','dtype':'bfloat16','attention':'sdpa','language':'Russian',
            'max_new_tokens':512,'max_inference_batch_size':1,'implementation_sha256':file_digest(Path(__file__))}
    signature=digest(config)
    model=Qwen3ASRModel.from_pretrained(str(model_path),device_map='cpu',dtype=torch.bfloat16,
            attn_implementation='sdpa',local_files_only=True,max_inference_batch_size=1,max_new_tokens=512)
    for folder in folders:
        plan_path=folder/'render-plan.json';plan=read(plan_path);binding=file_digest(plan_path)
        receipts=read(folder/'receipts.json')
        if receipts['plan_sha256']!=binding:raise ValueError('Receipt/plan mismatch')
        units={u['id']:u for u in plan['units']}
        report_path=folder/'word-checks-1.7B-cpu.json'
        report=read(report_path) if report_path.exists() else {'plan_sha256':binding,'verifier':config,'checks':{}}
        if report['plan_sha256']!=binding or report['verifier']!=config:raise ValueError('Word audit binding changed')
        original=read(folder/'word-checks.json')['checks'] if flags_only else {}
        keys=[k for k in receipts['takes'] if not flags_only or original[k]['status']!='asr_exact_match']
        for key in keys:
            if units[key]['synthesis'].get('backend')!='nonverbal_asset' or key in report['checks']:continue
            take=receipts['takes'][key]
            if file_digest(folder/take['audio'])!=take['audio_sha256']:raise ValueError('Event audio changed before check')
            report['checks'][key]={'status':'nonverbal_not_spoken_text','expected_text_sha256':digest(b''),
                'audio_sha256':take['audio_sha256'],'verifier_sha256':signature,'audio':take['audio'],
                'synthesis_key':take['key'],'heard':'','word_verification_applicable':False}
        write_json(report_path,report)
        pending=[k for k in keys if k not in report['checks']]
        for i in range(0,len(pending),1):
            batch=pending[i:i+1];paths=[]
            for key in batch:
                take=receipts['takes'][key];path=folder/take['audio']
                if file_digest(path)!=take['audio_sha256']:raise ValueError('Take changed before check')
                paths.append(str(path))
            heard=model.transcribe(paths,language='Russian')
            if len(heard)!=len(batch):raise ValueError('Incomplete transcription')
            for key,result in zip(batch,heard):
                take=receipts['takes'][key]
                if file_digest(folder/take['audio'])!=take['audio_sha256']:raise ValueError('Take changed during check')
                report['checks'][key]={**transcript_check(units[key]['spoken'],result.text,take['audio_sha256'],signature),
                                       'audio':take['audio'],'synthesis_key':take['key']}
            report['peak_gpu_reserved_bytes']=0
            write_json(report_path,report)
            if i%20==0:
                exact=sum(v['status']=='asr_exact_match' for v in report['checks'].values())
                print('CPU AUDIT',folder.name,len(report['checks']),'/',len(keys),'exact',exact,flush=True)
        print('CPU AUDIT COMPLETE',folder.name,len(report['checks']),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folders',type=Path,nargs='+',required=True)
    p.add_argument('--flags-only',action='store_true');a=p.parse_args();audit([f.resolve() for f in a.folders],a.flags_only)
