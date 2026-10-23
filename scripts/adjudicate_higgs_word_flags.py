"""Frozen, text-only Opus diagnosis; suggestions never clear an audio gate."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import write_json,file_digest
from rimsky.speech_quality import word_tokens
from rimsky.spoken_pronunciation import unmarked,validate_marks
from rimsky.analysis import mismatch
from rimsky.claude_analysis import structured_call

from rimsky.word_recovery import SYSTEM,obj,schema,validate,diagnose
from higgs_trial_io import read_json as read

def packet(folder,selected_paths):
    book=read(folder/'source/book.json')
    blocks={b['id']:b for c in book['chapters'] for b in c['blocks']}
    units=[];bindings={}
    for path in selected_paths:
        selected=read(path);bindings[str(path.resolve())]=file_digest(path)
        for uid,item in selected.items():
            check=item['word_check']
            if check['status'] in ('asr_exact_match','nonverbal_not_spoken_text'):continue
            u=item['unit'];block=blocks[u['source_id']]
            units.append({'unit_id':uid,'spoken':u['spoken'],'paragraph':block['text'],
                'heard':check['heard'],'audio_sha256':check['audio_sha256'],'synthesis_key':check['synthesis_key'],
                'voice':u.get('voice'),'mood':u.get('mood'),'prior_attempts':[]})
            # Only this unit's transcripts; preserve exact audio/plan bindings.
            for p in path.parent.rglob('word-checks-1.7B-normalized.json'):
                if uid in read(p)['checks']:
                    prior=read(p)['checks'][uid]
                    units[-1]['prior_attempts'].append({k:prior[k] for k in ('heard','audio_sha256','synthesis_key')})
    return {'source_sha256':file_digest(folder/'source/book.json'),'selected_bindings':bindings,'units':units}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--folder',type=Path,required=True);p.add_argument('--selected',type=Path,nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=diagnose(args.output,packet(args.folder,args.selected))
    print('OPUS DIAGNOSIS COMPLETE',len(result['answer']['diagnoses']),result['metadata'].get('model_usage'),flush=True)
