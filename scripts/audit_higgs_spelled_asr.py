"""Audio-only numeric verification with digit output disabled, not case guessed."""
from __future__ import annotations
import argparse,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import write_json,file_digest,digest
from rimsky.speech_quality import transcript_check
from higgs_trial_io import read_json as read
from audit_higgs_tertiary_asr import MODEL,REVISION


def digit_token_ids(tokenizer):
    """The same vocabulary restriction for every recording, without source words."""
    special=set(tokenizer.all_special_ids)
    return sorted(i for i in tokenizer.get_vocab().values() if i not in special
        and any(c.isdecimal() for c in tokenizer.decode([i],clean_up_tokenization_spaces=False)))


def audit(paths,output):
    import torch,numpy as np,soundfile as sf,transformers
    from scipy.signal import resample_poly
    from huggingface_hub import snapshot_download
    from transformers import WhisperProcessor,WhisperForConditionalGeneration
    torch.set_num_threads(4)
    root=Path(snapshot_download(MODEL,revision=REVISION,local_files_only=True,
        allow_patterns=['*.json','merges.txt','model.safetensors']))
    processor=WhisperProcessor.from_pretrained(str(root),local_files_only=True)
    suppressed=sorted(set(processor.tokenizer.get_vocab().values()) & set(digit_token_ids(processor.tokenizer)))
    config={'model':MODEL,'revision':REVISION,'files':{p.name:file_digest(p) for p in root.iterdir() if p.is_file()},
        'device':'cuda','dtype':'float16','language':'ru','task':'transcribe','do_sample':False,'num_beams':3,
        'transformers':transformers.__version__,'implementation_sha256':file_digest(Path(__file__)),
        'word_comparison_sha256':file_digest(ROOT/'runtime/rimsky/speech_quality.py'),
        'numeric_output':'suppress-every-digit-bearing-non-special-token-v1',
        'digit_token_ids_sha256':digest(suppressed),'digit_token_count':len(suppressed)}
    signature=digest(config);report_path=output/'report.json'
    report=read(report_path) if report_path.exists() else {'verifier':config,'checks':{}}
    if report['verifier']!=config:raise ValueError('Spelled-number verifier changed; use a new output folder')
    model=None
    for path in paths:
        selected_sha=file_digest(path)
        for uid,row in read(path).items():
            expected=row['unit']['spoken'];check=row['word_check']
            if check['status'] in ('asr_exact_match','nonverbal_not_spoken_text'):continue
            audio=Path(row['path']);sha=file_digest(audio)
            if sha!=row['receipt']['audio_sha256'] or sha!=check['audio_sha256']:
                raise ValueError('Spelled-number audit audio changed')
            key=digest({'audio':sha,'expected':expected,'verifier':signature})
            if key in report['checks']:continue
            if model is None:
                print('SPELLED NUMBER ASR LOADING',MODEL,REVISION,flush=True)
                model=WhisperForConditionalGeneration.from_pretrained(str(root),local_files_only=True,
                    torch_dtype=torch.float16,low_cpu_mem_usage=True).to('cuda').eval()
            x,rate=sf.read(audio,dtype='float32',always_2d=True);mono=x.mean(axis=1)
            if rate!=16000:
                divisor=math.gcd(rate,16000);mono=resample_poly(mono,16000//divisor,rate//divisor).astype(np.float32)
            if len(mono)>30*16000:raise ValueError('Spelled-number audit requires a complete short passage')
            inputs=processor(mono,sampling_rate=16000,return_tensors='pt',return_attention_mask=True).to('cuda')
            inputs['input_features']=inputs['input_features'].to(torch.float16)
            # Keep the model's ordinary suppression and add only the universal
            # digit restriction. No expected text, names or context reach ASR.
            suppress=sorted(set(model.generation_config.suppress_tokens or [])|set(suppressed))
            with torch.inference_mode():
                tokens=model.generate(**inputs,language='ru',task='transcribe',do_sample=False,
                    num_beams=3,max_new_tokens=256,suppress_tokens=suppress)
            heard=processor.batch_decode(tokens,skip_special_tokens=True)[0].strip()
            if any(c.isdecimal() for c in heard):raise ValueError('Digit restriction failed')
            if file_digest(audio)!=sha or file_digest(path)!=selected_sha:raise ValueError('Spelled-number audit inputs changed')
            result=transcript_check(expected,heard,sha,signature,normalize_elongations=True)
            report['checks'][key]={**result,'unit_id':uid,'synthesis_key':check['synthesis_key'],
                'selected_file':str(path.resolve()),'selected_sha256':selected_sha}
            write_json(report_path,report);print('SPELLED NUMBER ASR',uid,result['status'],heard,flush=True)
    write_json(report_path,report)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--selected',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();audit(a.selected,a.output)
