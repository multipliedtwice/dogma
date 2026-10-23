"""Pinned, CPU-only source-blind Russian lexical CTC witness for original demo takes.

Only witness runs inference. Source text is compared after decoding, never sent
to the model. Separate immutable ru-ctc-witness files do not change frozen gates.
"""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import render_public_audiobook_demo as renderer
import witness_public_demo_whisper_full as original
from rimsky.speech_quality import transcript_check
from rimsky.util import file_digest

MODEL='jonatasgrosman/wav2vec2-large-xlsr-53-russian'
REVISION='2329100508896c6d9b157019803ab5601e6f3406'
POLICY='public-demo-ru-wav2vec2-ctc-witness-v1'
MODEL_PATH=ROOT/'training-work/models/wav2vec2-russian-verified'
FILES={
    'pytorch_model.bin':'d1cdb1a7921de7d363f967a9b0101a713602e109dba62b6f3f9ae2e0b2df0c1c',
    'config.json':'9f02bcac826a79be1bc20cff20fb2ed830f3538ec282c65050f9195d61499deb',
    'preprocessor_config.json':'ca5999a45e98bb76ea87a461ba28a23ad32a5bb9f733b8e0f6546ff38b6c612d',
    'vocab.json':'3def5e6b6c6fcbe9a674e58b8f7f9e608742414db0188ec50bb3ac565612f0d7',
    'special_tokens_map.json':'bb7068de1150661a10b55f9e4b12a0e77af8bf91f5e45e1b58afaf1d0e17f675'}
SIZES={'pytorch_model.bin':1262093719,'config.json':1782,'preprocessor_config.json':262,
       'vocab.json':387,'special_tokens_map.json':85}
MODEL_PROVENANCE={'repository':'https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-russian','revision':REVISION,
                  'license':'Apache-2.0','retrieved':'2026-10-23',
                  'source':'Official Hugging Face API LFS weight SHA and exact revision runtime file bytes'}
_MODEL_CACHE={}


def dependencies():
    return {p.relative_to(ROOT).as_posix():file_digest(p) for p in
            [Path(__file__),Path(original.__file__),Path(renderer.__file__),ROOT/'runtime/rimsky/speech_quality.py',
             ROOT/'scripts/recover_public_audiobook_demo.py']}


def witness_config(language,torch_version=None,transformers_version=None):
    if language!='ru':
        raise ValueError('Pinned Russian XLSR lexical CTC witness supports Russian only')
    distribution=importlib.metadata.distribution('transformers')
    names=['modeling_wav2vec2.py','processing_wav2vec2.py','feature_extraction_wav2vec2.py','tokenization_wav2vec2.py']
    implementation={name:file_digest(Path(distribution.locate_file('transformers/models/wav2vec2/'+name))) for name in names}
    return {'policy':POLICY,'model':MODEL,'revision':REVISION,'files':dict(FILES),'file_sizes':dict(SIZES),
            'implementation_sha256':file_digest(Path(__file__)),'dependencies':dependencies(),
            'transformers_implementation':implementation,'torch':torch_version or importlib.metadata.version('torch'),
            'transformers':transformers_version or importlib.metadata.version('transformers'),
            'numpy':importlib.metadata.version('numpy'),'scipy':importlib.metadata.version('scipy'),
            'soundfile':importlib.metadata.version('soundfile'),'language':'ru','device':'cpu','dtype':'float32','threads':4,
            'sample_rate':16000,'max_audio_seconds':30,'truncation':'forbidden','padding':False,
            'decoder':'framewise-argmax-standard-ctc-collapse','language_model':None,'prompt':None,
            'expected_text_given_to_recognizer':False,'local_files_only':True,'training':False,'use_safetensors':False,'weights_only':True}


verifier_config=witness_config


def validate_verifier_config(config):
    expected=witness_config(config.get('language'))
    if renderer.sha(config)!=renderer.sha(expected):
        raise ValueError('Changed lexical CTC model/runtime/source-blind configuration')
    return True


def verify_model(path=MODEL_PATH):
    path=Path(path)
    if not path.is_dir():raise ValueError('Missing pinned lexical CTC model')
    entries=sorted(p for p in path.rglob('*') if p.is_file())
    rows=[(p.relative_to(path).as_posix(),p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ctime_ns,p.stat().st_ino,p.stat().st_dev) for p in entries]
    if {name:size for name,size,*_ in rows}!=SIZES:
        raise ValueError('Lexical CTC model filenames/sizes differ')
    key=(str(path.resolve()),tuple(rows),tuple(sorted(FILES.items())))
    if key not in _MODEL_CACHE:
        if renderer.inventory(path)!=FILES:raise ValueError('Lexical CTC model checksums differ')
        _MODEL_CACHE.clear();_MODEL_CACHE[key]=True
    return path


def eligible(receipt):
    return (type(receipt.get('take')) is int and receipt['take'] in range(6)
            and receipt.get('quality_passed') is True
            and not any(k in receipt.get('spec',{}) for k in ('context_recovery','pronunciation_recovery','ru_pronunciation_recovery')))


def validate_original(context,unit,receipt):
    if context.assignment['language']!='Russian':raise ValueError('Russian-only lexical CTC witness')
    return original.validate_original(context,unit,receipt)


def evidence(context,unit,receipt):
    if not eligible(receipt):return []
    paths=sorted((context.folder/'ru-ctc-witness').glob(receipt['audio_sha256']+'-*.json'))
    if not paths:return []
    validate_original(context,unit,receipt)
    found=[]
    for path in paths:
        check=renderer.read(path);config=check['verifier'];validate_verifier_config(config)
        signature=renderer.sha(config)
        if (config['language']!='ru' or context.assignment['language']!='Russian'
                or path.name!=receipt['audio_sha256']+'-'+signature+'.json'
                or renderer.read(context.folder/'verifiers'/(signature+'.json'))!=config):
            raise ValueError('Lexical CTC language/filename/verifier registry differs')
        raw=transcript_check(unit['spoken'],check['heard'],receipt['audio_sha256'],signature)
        if any(check.get(k)!=v for k,v in raw.items()):raise ValueError('Lexical CTC raw transcript/source/audio binding differs')
        found.append({'path':path.relative_to(context.folder).as_posix(),'sha256':file_digest(path),'raw':raw,
                      'verifier':config,'role':'witness','family':'wav2vec2'})
    return found


def choose_pending(context,only=(),all_takes=False):
    units={u['id']:u for u in context.assignment['units']}
    if set(only)-units.keys():raise ValueError('Unknown lexical CTC target unit')
    pending=[]
    for unit in units.values():
        if only and unit['id'] not in only:continue
        candidates=[]
        for receipt in renderer.takes(context.folder,unit):
            if not eligible(receipt):continue
            validate_original(context,unit,receipt)
            assessed=context.assess(unit,receipt)
            if assessed and not assessed['decision']['passed']:candidates.append((unit,receipt))
        pending.extend(candidates if all_takes else candidates[-1:])
    return pending


def plan(context,only=(),all_takes=False):
    import soundfile as sf
    if context.assignment['language']!='Russian':raise ValueError('Russian-only lexical CTC witness')
    config=witness_config('ru');signature=renderer.sha(config);pending=[];holds=[]
    for unit,receipt in choose_pending(context,only,all_takes):
        target=context.folder/'ru-ctc-witness'/(receipt['audio_sha256']+'-'+signature+'.json')
        if target.exists():evidence(context,unit,receipt);continue
        path=renderer.checked(context.folder/receipt['audio'],receipt['audio_sha256']);info=sf.info(path)
        if info.frames<=0 or info.channels!=1 or info.samplerate!=24000 or info.frames>30*info.samplerate:
            holds.append({'unit':unit['id'],'take':receipt['take'],'audio_sha256':receipt['audio_sha256'],
                          'reason':'whole_audio_requires_mono24k_at_most30seconds','truncated':False})
        else:pending.append((unit,receipt))
    value={'schema':1,'frozen_generation':context.generation,'verifier':config,'verifier_sha256':signature,
           'pending':[{'unit':u['id'],'take':r['take'],'audio_sha256':r['audio_sha256']} for u,r in pending],
           'holds':holds,'expected_text_given_to_recognizer':False}
    destination=context.folder/'ru-ctc-witness/plans'/(renderer.sha(value)+'.json')
    renderer.write(destination,value,immutable=True)
    print('CTC PLAN',len(pending),'pending',len(holds),'held',flush=True)
    return pending,value


def decode_audio(audio16k,processor,model,torch):
    """Audio-only model boundary; no source, expected token or language-model input."""
    import numpy as np
    if not 0<len(audio16k)<=30*16000 or not np.isfinite(audio16k).all():
        raise ValueError('Whole lexical CTC audio exceeds window or is invalid')
    inputs=processor(audio16k,sampling_rate=16000,return_tensors='pt',padding=False)
    if not inputs or set(inputs)-{'input_values','attention_mask'}:raise ValueError('Unexpected non-audio CTC model input')
    with torch.inference_mode():logits=model(**inputs).logits
    if not torch.isfinite(logits).all():raise ValueError('Non-finite CTC logits')
    ids=torch.argmax(logits,dim=-1)
    return processor.batch_decode(ids)[0]


def witness(context,pending,plan_value,model_path=MODEL_PATH):
    if not pending:return
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',CUDA_VISIBLE_DEVICES='')
    model_path=verify_model(model_path)
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    import torch
    import transformers
    from transformers import Wav2Vec2Processor,Wav2Vec2ForCTC
    torch.set_num_threads(4)
    config=witness_config('ru',torch.__version__,transformers.__version__)
    if renderer.sha(config)!=renderer.sha(plan_value['verifier']):raise ValueError('CTC runtime changed since plan')
    signature=renderer.sha(config)
    renderer.write(context.folder/'verifiers'/(signature+'.json'),config,immutable=True)
    processor=Wav2Vec2Processor.from_pretrained(str(model_path),local_files_only=True)
    model=Wav2Vec2ForCTC.from_pretrained(str(model_path),local_files_only=True,torch_dtype=torch.float32,use_safetensors=False,weights_only=True).to('cpu').eval()
    verify_model(model_path)
    for unit,receipt in pending:
        path=validate_original(context,unit,receipt)
        audio,rate=sf.read(path,dtype='float32',always_2d=True)
        if rate!=24000 or audio.shape[1]!=1 or not 0<len(audio)<=30*rate or not np.isfinite(audio).all():
            raise ValueError('CTC native audio changed/invalid; never truncate')
        divisor=math.gcd(rate,16000)
        mono=resample_poly(audio[:,0],16000//divisor,rate//divisor).astype(np.float32)
        heard=decode_audio(mono,processor,model,torch)
        renderer.checked(path,receipt['audio_sha256'])
        raw=transcript_check(unit['spoken'],heard,receipt['audio_sha256'],signature)
        result={**raw,'verifier':config,'interpretation':'Independent source-blind lexical CTC; no human listening approval'}
        target=context.folder/'ru-ctc-witness'/(receipt['audio_sha256']+'-'+signature+'.json')
        renderer.write(target,result,immutable=True)
        print('CTC WITNESS',unit['id'],'take',receipt['take'],repr(heard),raw['status'],flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--unit',action='append',default=[])
    parser.add_argument('--stage',choices=['plan','witness'],default='plan')
    parser.add_argument('--all-takes',action='store_true')
    parser.add_argument('--model-path',type=Path,default=MODEL_PATH)
    args=parser.parse_args()
    import resolve_public_demo_recognition as resolver
    context=resolver.Context(args.folder.resolve());pending,value=plan(context,args.unit,args.all_takes)
    if args.stage=='witness':witness(context,pending,value,args.model_path)


if __name__=='__main__':main()
