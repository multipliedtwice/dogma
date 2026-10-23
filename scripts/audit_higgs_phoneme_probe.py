"""Experimental audio-only phoneme evidence; never grants word/audio approval."""
import argparse,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import file_digest,write_json,digest
MODEL='facebook/wav2vec2-xlsr-53-espeak-cv-ft'
REVISION='2c733782da5604684829819a5eb744c193fe9398'

def probe(input_path,output):
    import numpy as np
    import soundfile as sf
    import torch,transformers
    from transformers import AutoFeatureExtractor,Wav2Vec2ForCTC
    from scipy.signal import resample_poly
    model_root=ROOT/'training-work/models/wav2vec2-xlsr-phonemes'
    inputs=json.loads(input_path.read_text('utf8'));binding=file_digest(input_path)
    config={'model':MODEL,'revision':REVISION,'device':'cpu','dtype':'float32',
        'files':{p.name:file_digest(p) for p in model_root.iterdir() if p.is_file() and p.suffix in ('.json','.bin')},
        'torch':torch.__version__,'transformers':transformers.__version__,
        'implementation_sha256':file_digest(Path(__file__))}
    vocab=json.loads((model_root/'vocab.json').read_text('utf8'));labels={i:p for p,i in vocab.items()}
    extractor=AutoFeatureExtractor.from_pretrained(model_root,local_files_only=True)
    model=Wav2Vec2ForCTC.from_pretrained(model_root,local_files_only=True).eval()
    torch.set_num_threads(4)
    report={'config':config,'input_sha256':binding,'experimental_only':True,'grants_acceptance':False,'checks':{}}
    for uid,row in inputs.items():
        path=Path(row['path']);sha=file_digest(path)
        if sha!=row['receipt']['audio_sha256']:raise ValueError('Probe audio changed')
        data,rate=sf.read(path,dtype='float32',always_2d=True);mono=data.mean(axis=1)
        if rate!=16000:
            divisor=math.gcd(rate,16000);mono=resample_poly(mono,16000//divisor,rate//divisor).astype('float32')
        batch=extractor(mono,sampling_rate=16000,return_tensors='pt')
        with torch.inference_mode():probabilities=model(**batch).logits.softmax(-1)[0]
        ids=probabilities.argmax(-1).tolist();segments=[];start=0;previous=ids[0]
        step=len(mono)/16000/len(ids)
        for i,label in enumerate(ids+[None]):
            if label==previous:continue
            if previous!=model.config.pad_token_id:
                segments.append({'phone':labels[previous],'start':start*step,'end':i*step,
                    'mean_probability':float(probabilities[start:i,previous].mean())})
            previous=label;start=i
        if file_digest(path)!=sha or file_digest(input_path)!=binding:raise ValueError('Probe inputs changed during recognition')
        report['checks'][uid]={'audio_sha256':sha,'synthesis_key':row['receipt']['key'],
            'expected_text_sha256':digest(row['unit']['spoken'].encode('utf8')),
            'phones':' '.join(s['phone'] for s in segments),'segments':segments,'source':str(path)}
        write_json(output,report);print(uid,report['checks'][uid]['phones'],flush=True)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();probe(a.input,a.output)
