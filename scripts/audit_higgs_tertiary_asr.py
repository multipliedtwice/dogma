"""Pinned, audio-only larger Whisper check for exhausted word recoveries."""
from __future__ import annotations
import argparse
import math
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import write_json,file_digest,digest
from rimsky.speech_quality import transcript_check
from higgs_trial_io import read_json as read
MODEL='openai/whisper-large-v3-turbo'
REVISION='41f01f3fe87f28c78e2fbf8b568835947dd65ed9'


def audit(paths,output):
    import torch
    import soundfile as sf
    import numpy as np
    import transformers
    from scipy.signal import resample_poly
    from huggingface_hub import snapshot_download
    from transformers import WhisperProcessor,WhisperForConditionalGeneration
    torch.set_num_threads(4)
    model_root=Path(snapshot_download(MODEL,revision=REVISION,local_files_only=True,
        allow_patterns=['*.json','merges.txt','model.safetensors']))
    config={'model':MODEL,'revision':REVISION,
        'files':{p.name:file_digest(p) for p in model_root.iterdir() if p.is_file()},
        'device':'cuda','dtype':'float16','language':'ru','task':'transcribe',
        'do_sample':False,'num_beams':3,'transformers':transformers.__version__,
        'implementation_sha256':file_digest(Path(__file__)),
        'word_comparison_sha256':file_digest(ROOT/'runtime/rimsky/speech_quality.py')}
    signature=digest(config);output.mkdir(parents=True,exist_ok=True)
    report_path=output/'report.json'
    report=read(report_path) if report_path.exists() else {'verifier':config,'checks':{}}
    if report['verifier']!=config:raise ValueError('Tertiary recognizer configuration changed; use a new output folder')
    model=None;processor=None
    for path in paths:
        selected_sha=file_digest(path)
        for uid,item in read(path).items():
            check=item['word_check']
            if check['status'] in ('asr_exact_match','nonverbal_not_spoken_text'):continue
            audio=Path(item['path']);audio_sha=file_digest(audio)
            if audio_sha!=item['receipt']['audio_sha256'] or audio_sha!=check['audio_sha256']:
                raise ValueError('Audio changed before tertiary recognition')
            expected=item['unit']['spoken'];key=digest({'audio':audio_sha,'expected':expected,'verifier':signature})
            if key in report['checks']:continue
            if model is None:
                print('TERTIARY ASR LOADING',MODEL,REVISION,flush=True)
                processor=WhisperProcessor.from_pretrained(str(model_root),local_files_only=True)
                model=WhisperForConditionalGeneration.from_pretrained(str(model_root),local_files_only=True,
                    torch_dtype=torch.float16,low_cpu_mem_usage=True).to('cuda').eval()
            data,rate=sf.read(audio,dtype='float32',always_2d=True);mono=data.mean(axis=1)
            if rate!=16000:
                divisor=math.gcd(rate,16000);mono=resample_poly(mono,16000//divisor,rate//divisor).astype(np.float32)
            if len(mono)>30*16000:raise ValueError('Tertiary audit requires a complete short sentence')
            # Expected text is used only after recognition, never as a prompt.
            inputs=processor(mono,sampling_rate=16000,return_tensors='pt',return_attention_mask=True).to('cuda')
            inputs['input_features']=inputs['input_features'].to(torch.float16)
            with torch.inference_mode():
                tokens=model.generate(**inputs,language='ru',task='transcribe',do_sample=False,
                    num_beams=3,max_new_tokens=256)
            heard=processor.batch_decode(tokens,skip_special_tokens=True)[0].strip()
            if file_digest(audio)!=audio_sha or file_digest(path)!=selected_sha:
                raise ValueError('Inputs changed during tertiary recognition')
            result=transcript_check(expected,heard,audio_sha,signature,normalize_elongations=True)
            report['checks'][key]={**result,'unit_id':uid,'synthesis_key':check['synthesis_key'],
                'primary_check':check,'selected_file':str(path.resolve()),'selected_sha256':selected_sha}
            write_json(report_path,report)
            print('TERTIARY ASR',uid,result['status'],heard,flush=True)
    write_json(report_path,report)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--selected',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();audit(a.selected,a.output)
