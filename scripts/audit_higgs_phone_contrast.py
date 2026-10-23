"""Acoustic phone-contrast witness for disputed spans of flagged takes.

Recognizers are language models: they rewrite rare wording into common words
(Гласу -> глазу) whatever the audio says. This witness never spells anything.
An espeak-trained phoneme model (wav2vec2-xlsr-53-espeak-cv-ft) decodes the
take; for each span where the primary transcript still disagrees after the
documented spelling rules, it compares the decoded phones with the source
reading and with the source carrying only that span swapped. The source is
supported only when EVERY disputed span fits better by more than THRESHOLD
phone edits.

Calibration, 2026-10-15, 1,958 injected word errors on verified takes:
margin > 2 accepted 0 errors (true readings confirmed 582/1958); margin > 1
accepted 12, so 1 is not used. Stress-only differences (и сданные/изданные)
cannot be decided and stay flagged.

The report uses the secondary-witness format so publication can reproduce it;
`heard` holds the source text the audio was judged to support, and every
check carries its per-span margins. It is not a transcript or listening approval.
"""
from __future__ import annotations
import argparse
import difflib
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.util import write_json,file_digest,digest
from rimsky.speech_quality import transcript_check,spoken_word_tokens
from higgs_trial_io import read_json as read

THRESHOLD=2
MODEL_DIR=ROOT/'training-work/models/wav2vec2-xlsr-phonemes'
CLASS={'a':'A','ɑ':'A','ʌ':'A','ə':'A','ɐ':'A','o':'A','ɔ':'A','æ':'A','ɚ':'A',
       'i':'I','ɪ':'I','e':'I','ɛ':'I','u':'U','ʊ':'U','ɨ':'Y','y':'Y'}
_MODEL=None


def _model():
    global _MODEL
    if _MODEL is None:
        import json,torch
        from transformers import AutoFeatureExtractor,Wav2Vec2ForCTC
        extractor=AutoFeatureExtractor.from_pretrained(MODEL_DIR,local_files_only=True)
        model=Wav2Vec2ForCTC.from_pretrained(MODEL_DIR,local_files_only=True).eval()
        # Witnesses run after Higgs has exited, so the GPU is free; release() frees it again.
        device='cuda' if torch.cuda.is_available() else 'cpu';model.to(device)
        vocab=json.loads((MODEL_DIR/'vocab.json').read_text('utf8'))
        _MODEL=(extractor,model,{i:p for p,i in vocab.items()},device)
    return _MODEL


def release():
    global _MODEL
    if _MODEL is not None:
        _MODEL=None
        import gc,torch
        gc.collect()
        if torch.cuda.is_available():torch.cuda.empty_cache()


def decode(wav):
    import math,numpy as np,soundfile as sf,torch
    from scipy.signal import resample_poly
    extractor,model,labels,device=_model()
    data,rate=sf.read(wav,dtype='float32',always_2d=True);mono=data.mean(axis=1)
    if rate!=16000:
        g=math.gcd(rate,16000);mono=resample_poly(mono,16000//g,rate//g).astype('float32')
    batch=extractor(mono,sampling_rate=16000,return_tensors='pt')
    with torch.inference_mode():ids=model(batch.input_values.to(device)).logits.argmax(-1)[0].tolist()
    out=[];prev=None
    for i in ids:
        if i!=prev and i!=model.config.pad_token_id:out.append(labels[i])
        prev=i
    return [CLASS.get(p.replace('ː','').rstrip('0123456789.'),p.replace('ː','').rstrip('0123456789.')) for p in out if p.strip('ː0123456789.')]


def reference(words):
    from rimsky.spoken_homophones import phrase
    return [CLASS.get(p,p) if k=='V' else p for k,p,_ in phrase(' '.join(words))]


def distance(a,b):
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        prev,row[0]=row[0],i
        for j,y in enumerate(b,1):
            prev,row[j]=row[j],min(row[j]+1,row[j-1]+1,prev+(x!=y))
    return row[-1]


def disputed_spans(spoken,heard):
    from render_higgs_book_trial import spoken_text
    from rimsky.word_verification import final_devoicing_equivalence
    from rimsky.spoken_homophones import homophone_equivalence,elongation_equivalence
    try:heard=spoken_text(heard)
    except ValueError:pass
    source=spoken_word_tokens(spoken)
    normalized,_=final_devoicing_equivalence(spoken,heard);words=spoken_word_tokens(normalized)
    words,_=elongation_equivalence(spoken,source,words);words,_=homophone_equivalence(source,words)
    return source,[(a,b,words[x:y]) for tag,a,b,x,y in difflib.SequenceMatcher(None,source,words,autojunk=False).get_opcodes() if tag!='equal']


def judge(wav,spoken,heard):
    source,spans=disputed_spans(spoken,heard)
    if not spans:return None
    decoded=decode(wav);base=distance(decoded,reference(source));rows=[]
    for a,b,alternative in spans:
        swapped=source[:a]+list(alternative)+source[b:]
        rows.append({'source':' '.join(source[a:b]),'recognizer':' '.join(alternative),
                     'margin':distance(decoded,reference(swapped))-base})
    return {'supported':all(r['margin']>THRESHOLD for r in rows),'spans':rows,'decoded_phones':len(decoded)}


def audit(selected_paths,output):
    config={'model':'facebook/wav2vec2-xlsr-53-espeak-cv-ft','files':{p.name:file_digest(p) for p in MODEL_DIR.iterdir() if p.is_file()},
            'language':'ru','method':'per-span phone-contrast margin against the primary transcript','threshold':THRESHOLD,
            'calibration':{'injected_errors':1958,'false_accepts':0,'true_confirmations':582,'date':'2026-10-15'},
            'heard_semantics':'source text supported by the audio; not a recognizer transcript',
            'independent_recognizer':True,'implementation_sha256':file_digest(Path(__file__))}
    signature=digest(config);output.mkdir(parents=True,exist_ok=True);report_path=output/'report.json'
    report=read(report_path) if report_path.exists() else {'verifier':config,'checks':{},'rejected':{}}
    if report['verifier']!=config:raise ValueError('Phone-contrast witness changed; use a new output folder')
    for path in selected_paths:
        for uid,row in read(path).items():
            check=row['word_check']
            if check['status'] in ('asr_exact_match','nonverbal_not_spoken_text'):continue
            wav=Path(row['path']);sha=file_digest(wav)
            if sha!=row['receipt']['audio_sha256'] or sha!=check['audio_sha256']:raise ValueError('Witness audio changed')
            spoken=row['unit']['spoken'];key=digest({'audio':sha,'expected':spoken,'verifier':signature})
            if key in report['checks'] or key in report['rejected']:continue
            heard=check.get('heard_raw',check.get('heard',''))
            verdict=judge(wav,spoken,heard) if heard else None
            if not verdict:continue
            entry={'unit_id':uid,'synthesis_key':check['synthesis_key'],'primary_check':check,'phone_contrast':verdict,
                   'selected_file':str(Path(path).resolve()),'selected_sha256':file_digest(path)}
            if verdict['supported']:
                report['checks'][key]={**transcript_check(spoken,spoken,sha,signature,normalize_elongations=True),**entry}
            else:
                report['rejected'][key]={'audio_sha256':sha,**entry}
            # One line per judged take keeps progress visible to the supervisor's stall watchdog.
            print('PHONE CONTRAST','supports' if verdict['supported'] else 'judged',uid,[r['margin'] for r in verdict['spans']],flush=True)
            write_json(report_path,report)
    write_json(report_path,report)
    release()
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--selected',type=Path,nargs='+',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();audit(a.selected,a.output)
