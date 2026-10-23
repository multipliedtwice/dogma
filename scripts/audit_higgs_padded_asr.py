"""Silence-padded recognition witness for flagged takes.

A recognizer often drops the first phoneme of a clip that starts with speech
and no silence (тихо heard as ихо, кисло as исла). This witness hears the exact
take with 0.4 s of digital silence added before and after, recognizer input
only; the audio itself is never changed. Measured on 123 flagged short first
takes (2026-10-16): padding newly matched 12 and lost 3 that matched raw, so it
is an additional witness that can only add a pass, never replace the primary.
Same weights as the primary recognizer, different input framing. Writes the
secondary-witness format consumed by higgs_secondary_verification.
Run with training-work/.venv-asr.
"""
from __future__ import annotations
import argparse
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import write_json,file_digest,digest
from rimsky.speech_quality import transcript_check
from higgs_trial_io import read_json as read
from audit_higgs_orthography_witness import flagged,entry,GLOSSARY_MODEL

PAD_SECONDS=0.4


def padded_witness(selected_paths,output,model=None):
    import numpy as np
    import soundfile as sf
    import torch
    import asr_batching  # stubs the unused aligner tokenizer before qwen_asr loads
    from qwen_asr import Qwen3ASRModel
    model_path=ROOT/GLOSSARY_MODEL
    from higgs_file_checks import persistent_sha
    files={p.relative_to(ROOT).as_posix():persistent_sha(p) for p in model_path.rglob('*') if p.is_file() and '.cache' not in p.parts}
    config={'model_files':files,'device':'cuda:0','dtype':'bfloat16','attention':'sdpa','language':'ru','max_new_tokens':512,
            'input':f'exact take with {PAD_SECONDS} s digital silence before and after; recognizer input only',
            'independent_recognizer':'same weights as primary, different input framing',
            'implementation_sha256':file_digest(Path(__file__)),'batching_sha256':file_digest(Path(__file__).with_name('asr_batching.py')),
            'word_comparison_sha256':file_digest(ROOT/'runtime/rimsky/speech_quality.py')}
    signature=digest(config);output.mkdir(parents=True,exist_ok=True);report_path=output/'report.json'
    report=read(report_path) if report_path.exists() else {'verifier':config,'checks':{}}
    if report['verifier']!=config:raise ValueError('Padded witness changed; use a new output folder')
    pending=[]
    for path,uid,row,check,audio,sha in flagged(selected_paths):
        expected=row['unit']['spoken'];key=digest({'audio':sha,'expected':expected,'verifier':signature})
        if key not in report['checks'] and all(p[0]!=key for p in pending):pending.append((key,path,uid,check,audio,sha,expected))
    if not pending:write_json(report_path,report);return report
    from asr_batching import last_position_logits
    model=model or last_position_logits(Qwen3ASRModel.from_pretrained(str(model_path),device_map='cuda:0',dtype=torch.bfloat16,attn_implementation='sdpa',
                                        local_files_only=True,max_inference_batch_size=32,max_new_tokens=512))
    with tempfile.TemporaryDirectory() as folder:
        from asr_batching import transcribe as batched,BATCH
        def framed(item):
            key,audio=item[0],item[4];target=Path(folder)/(key+'.wav')
            if not target.exists():
                x,rate=sf.read(audio,dtype='float32');pad=np.zeros(round(PAD_SECONDS*rate),dtype='float32')
                sf.write(target,np.concatenate([pad,x,pad]),rate)
            return target
        for n,((key,path,uid,check,audio,sha,expected),result) in enumerate(batched(model,pending,framed,language='Russian'),1):
            if file_digest(audio)!=sha:raise ValueError('Audio changed during padded recognition')
            report['checks'][key]=entry(transcript_check(expected,result.text,sha,signature,normalize_elongations=True),uid,check,path)
            print('PADDED ASR',uid,report['checks'][key]['status'],result.text,flush=True)
            if n%BATCH==0 or n==len(pending):write_json(report_path,report)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--selected',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();padded_witness(a.selected,a.output)
