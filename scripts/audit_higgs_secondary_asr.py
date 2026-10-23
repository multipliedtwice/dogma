"""Independent, audio-only Whisper check for persistent Qwen disagreements."""
from __future__ import annotations
import argparse
import json
import math
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import write_json,file_digest,digest
from rimsky.speech_quality import transcript_check
MODEL='openai/whisper-small'
REVISION='973afd24965f72e36ca33b3055d56a652f456b4d'
# GPU float16 since 2026-10-16: on CPU float32 it cost about 6 s per clip (13.7 min of
# witness time in a 12-minute clip). The device/dtype are part of the verifier config.
DEVICE='cuda'
DTYPE='float16'

def read(p):return json.loads(Path(p).read_text('utf-8'))
def output_folder(section):
    """Where a section's independent Whisper report lives (the worker's independent_check and the audit prewarm)."""
    return section/('secondary-asr-v2-'+digest({'asr':file_digest(Path(__file__)),
        'comparison':file_digest(ROOT/'runtime/rimsky/speech_quality.py')})[:16])


def audit(paths,output):
    import torch
    import soundfile as sf
    import numpy as np
    import transformers
    from scipy.signal import resample_poly
    from huggingface_hub import snapshot_download
    from transformers import WhisperProcessor,WhisperForConditionalGeneration
    torch.set_num_threads(4)
    model_root=Path(snapshot_download(MODEL,revision=REVISION,allow_patterns=[
        '*.json','merges.txt','model.safetensors']))
    config={'model':MODEL,'revision':REVISION,'files':{p.name:file_digest(p) for p in model_root.iterdir() if p.is_file()},
            'device':DEVICE if torch.cuda.is_available() else 'cpu','dtype':DTYPE if torch.cuda.is_available() else 'float32',
            'language':'ru','task':'transcribe','do_sample':False,
            'transformers':transformers.__version__,'implementation_sha256':file_digest(Path(__file__)),
            'word_comparison_sha256':file_digest(ROOT/'runtime/rimsky/speech_quality.py')}
    signature=digest(config);output.mkdir(parents=True,exist_ok=True)
    report_path=output/'report.json'
    report=read(report_path) if report_path.exists() else {'verifier':config,'checks':{}}
    if report['verifier']!=config:raise ValueError('Secondary recognizer configuration changed; use a new output folder')
    model=None;processor=None
    for path in paths:
        for uid,item in read(path).items():
            check=item['word_check']
            if check['status'] in ('asr_exact_match','nonverbal_not_spoken_text'):continue
            audio=Path(item['path']);audio_sha=file_digest(audio)
            if audio_sha!=item['receipt']['audio_sha256'] or audio_sha!=check['audio_sha256']:
                raise ValueError('Audio changed before secondary recognition')
            expected=item['unit']['spoken'];key=digest({'audio':audio_sha,'expected':expected,'verifier':signature})
            if key in report['checks']:continue
            if model is None:
                print('SECONDARY ASR LOADING',MODEL,REVISION,flush=True)
                processor=WhisperProcessor.from_pretrained(str(model_root),local_files_only=True)
                model=WhisperForConditionalGeneration.from_pretrained(str(model_root),local_files_only=True,
                    torch_dtype=getattr(torch,config['dtype'])).to(config['device']).eval()
            audio_data,rate=sf.read(audio,dtype='float32',always_2d=True)
            mono=audio_data.mean(axis=1)
            if rate!=16000:
                divisor=math.gcd(rate,16000);mono=resample_poly(mono,16000//divisor,rate//divisor).astype(np.float32)
            if len(mono)>30*16000:raise ValueError('Secondary sentence audit needs a short sentence, not a truncated clip')
            # No book words, source context or primary transcript reach this recognizer.
            inputs={k:(v.to(config['device'],dtype=getattr(torch,config['dtype'])) if v.is_floating_point() else v.to(config['device']))
                    for k,v in processor(mono,sampling_rate=16000,return_tensors='pt',return_attention_mask=True).items()}
            with torch.inference_mode():
                tokens=model.generate(**inputs,language='ru',task='transcribe',do_sample=False,max_new_tokens=256)
            heard=processor.batch_decode(tokens,skip_special_tokens=True)[0].strip()
            if file_digest(audio)!=audio_sha:raise ValueError('Audio changed during secondary recognition')
            result=transcript_check(expected,heard,audio_sha,signature,normalize_elongations=True)
            report['checks'][key]={**result,'unit_id':uid,'synthesis_key':check['synthesis_key'],
                 'primary_check':check,'selected_file':str(path.resolve()),'selected_sha256':file_digest(path)}
            write_json(report_path,report)
            print('SECONDARY ASR',uid,result['status'],heard,flush=True)
    write_json(report_path,report)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--selected',type=Path,nargs='+',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();audit(a.selected,a.output)
