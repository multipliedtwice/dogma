"""Audio-only English verification of Latin-script passages in a Russian book."""
import argparse,sys,math,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import write_json,file_digest,digest
from rimsky.speech_quality import transcript_check
from higgs_trial_io import read_json as read
from audit_higgs_tertiary_asr import MODEL,REVISION

def english(text):
    return len(re.findall('[A-Za-z]',text))>=3 and not re.search('[А-Яа-яЁё]',text)

def audit(selected_path,output):
    import torch,numpy as np,soundfile as sf,transformers
    from scipy.signal import resample_poly
    from huggingface_hub import snapshot_download
    from transformers import WhisperProcessor,WhisperForConditionalGeneration
    torch.set_num_threads(4)
    root=Path(snapshot_download(MODEL,revision=REVISION,local_files_only=True,allow_patterns=['*.json','merges.txt','model.safetensors']))
    config={'model':MODEL,'revision':REVISION,'files':{p.name:file_digest(p) for p in root.iterdir() if p.is_file()},
        'device':'cuda','dtype':'float16','language':'en','task':'transcribe','do_sample':False,'num_beams':3,
        'transformers':transformers.__version__,'implementation_sha256':file_digest(Path(__file__)),
        'word_comparison_sha256':file_digest(ROOT/'runtime/rimsky/speech_quality.py')}
    signature=digest(config);report_path=output/'report.json'
    report=read(report_path) if report_path.exists() else {'verifier':config,'checks':{}}
    if report['verifier']!=config:raise ValueError('English verifier changed')
    selected_sha=file_digest(selected_path);model=None;processor=None
    for uid,row in read(selected_path).items():
        expected=row['unit']['spoken'];check=row['word_check']
        if not english(expected) or check['status'] in ('asr_exact_match','nonverbal_not_spoken_text'):continue
        audio=Path(row['path']);sha=file_digest(audio)
        if sha!=row['receipt']['audio_sha256'] or sha!=check['audio_sha256']:raise ValueError('English audit audio changed')
        key=digest({'audio':sha,'expected':expected,'verifier':signature})
        if key in report['checks']:continue
        if model is None:
            processor=WhisperProcessor.from_pretrained(str(root),local_files_only=True)
            model=WhisperForConditionalGeneration.from_pretrained(str(root),local_files_only=True,
                torch_dtype=torch.float16,low_cpu_mem_usage=True).to('cuda').eval()
        x,rate=sf.read(audio,dtype='float32',always_2d=True);mono=x.mean(axis=1)
        if rate!=16000:
            divisor=math.gcd(rate,16000);mono=resample_poly(mono,16000//divisor,rate//divisor).astype(np.float32)
        if len(mono)>30*16000:raise ValueError('English audit needs a complete short passage')
        inputs=processor(mono,sampling_rate=16000,return_tensors='pt',return_attention_mask=True).to('cuda')
        inputs['input_features']=inputs['input_features'].to(torch.float16)
        # Only a language hint reaches the recognizer; never the expected words.
        with torch.inference_mode():tokens=model.generate(**inputs,language='en',task='transcribe',do_sample=False,num_beams=3,max_new_tokens=256)
        heard=processor.batch_decode(tokens,skip_special_tokens=True)[0].strip()
        if file_digest(audio)!=sha or file_digest(selected_path)!=selected_sha:raise ValueError('English audit inputs changed')
        result=transcript_check(expected,heard,sha,signature,normalize_elongations=True)
        report['checks'][key]={**result,'unit_id':uid,'synthesis_key':check['synthesis_key'],
            'selected_file':str(selected_path.resolve()),'selected_sha256':selected_sha}
        write_json(report_path,report);print('ENGLISH ASR',uid,result['status'],heard,flush=True)
    write_json(report_path,report)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--selected',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();audit(a.selected,a.output)
