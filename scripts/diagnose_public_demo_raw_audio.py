"""Development-only blind recognition of an unchanged held raw WAV.

This diagnostic never computes acceptance, changes quality, crops audio, writes
canonical evidence, or supplies expected text to either recognizer.
"""
from __future__ import annotations
import argparse,math,os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import context_public_demo_supplement_word_recovery as frozen
from rimsky.util import file_digest
SELF=Path(__file__).resolve()
POLICY='public-demo-held-raw-audio-diagnostic-v1'

def prepare(audio,receipt_path,campaign_path,recognizer):
    receipt=frozen.read(receipt_path);campaign=frozen.read(campaign_path)
    if receipt.get('quality_passed') is not False or receipt.get('audio_approval') is not False:
        raise ValueError('This diagnostic requires a preserved quality-held native receipt')
    frozen.renderer.checked(audio,receipt['audio_sha256'])
    import soundfile as sf
    info=sf.info(audio)
    if info.channels!=1 or info.duration>30:raise ValueError('Diagnostic requires whole mono audio <=30s; no truncation')
    if (campaign.get('policy')!=frozen.POLICY or campaign.get('language')!='Russian'
            or campaign['dependencies']!=frozen.dependencies()):raise ValueError('Frozen diagnostic model/code pins changed')
    # The native receipt remains untouched; no source/expected words are put in
    # the request or decoder input. Its full file hash retains local provenance.
    import importlib.metadata
    config=frozen.verifier_config(campaign,recognizer,importlib.metadata.version('torch'),importlib.metadata.version('transformers'))
    config={**config,'decoder_policy':config['policy'],'policy':POLICY,'diagnostic_implementation_sha256':file_digest(SELF),
            'acceptance':False,'expected_text_given_to_recognizer':False}
    return {'policy':POLICY,'audio':str(audio.resolve()),'audio_sha256':file_digest(audio),
        'native_receipt_sha256':file_digest(receipt_path),'native_quality_passed':False,
        'native_quality_error':receipt.get('quality_error'),'campaign_sha256':file_digest(campaign_path),
        'sample_rate':info.samplerate,'frames':info.frames,'channels':info.channels,
        'verifier':config,'diagnostic_code_sha256':file_digest(SELF),'acceptance':False,
        'human_approval':False,'expected_text_given_to_recognizer':False},campaign

def decode(path,recognizer,bound,config):
    import witness_public_demo_turbo as turbo
    turbo.wait_for_resources()
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    import torch
    torch.set_num_threads(4)
    if recognizer=='qwen':
        if frozen.renderer.inventory(frozen.ASR_ROOT)!=bound['qwen_files']:raise ValueError('Qwen inventory changed')
        from qwen_asr import Qwen3ASRModel
        from asr_batching import last_position_logits
        model=last_position_logits(Qwen3ASRModel.from_pretrained(str(frozen.ASR_ROOT),device_map='cuda:0',dtype=torch.bfloat16,
            attn_implementation='sdpa',local_files_only=True,max_inference_batch_size=1,max_new_tokens=512))
        try:return model.transcribe(str(path),language=config['decoder_language'])[0].text
        finally:del model;torch.cuda.empty_cache()
    from transformers import WhisperProcessor,WhisperForConditionalGeneration
    import soundfile as sf
    from scipy.signal import resample_poly
    model_root=turbo.verify_model();audio,rate=sf.read(path,dtype='float32')
    if audio.ndim!=1 or len(audio)>30*rate:raise ValueError('Diagnostic requires whole mono audio <=30s; no truncation')
    divisor=math.gcd(rate,16000);mono=resample_poly(audio,16000//divisor,rate//divisor)
    processor=WhisperProcessor.from_pretrained(str(model_root),local_files_only=True)
    model=WhisperForConditionalGeneration.from_pretrained(str(model_root),local_files_only=True,
        torch_dtype=torch.float16,attn_implementation='sdpa').to('cuda:0').eval()
    try:
        inputs={k:v.to('cuda:0',dtype=torch.float16) if v.is_floating_point() else v.to('cuda:0') for k,v in
            processor(mono,sampling_rate=16000,return_tensors='pt',return_attention_mask=True).items()}
        with torch.inference_mode():tokens=model.generate(**inputs,language=config['decoder_language'],task='transcribe',do_sample=False,max_new_tokens=256)
        if tokens.shape[-1]>=256:raise ValueError('Turbo diagnostic output token cap reached')
        return processor.batch_decode(tokens,skip_special_tokens=True)[0].strip()
    finally:del model;torch.cuda.empty_cache()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--audio',type=Path,required=True)
    p.add_argument('--native-receipt',type=Path,required=True);p.add_argument('--campaign',type=Path,required=True)
    p.add_argument('--recognizer',choices=['qwen','turbo'],required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();request,bound=prepare(args.audio,args.native_receipt,args.campaign,args.recognizer)
    frozen.immutable(args.output/'request.json',request)
    result=args.output/'result.json'
    if result.exists():
        saved=frozen.read(result)
        expected={'policy':POLICY,'request_sha256':file_digest(args.output/'request.json'),'audio_sha256':request['audio_sha256'],
            'verifier':request['verifier'],'native_quality_passed':False,'acceptance':False,'human_approval':False,'source_comparison_performed':False}
        if set(saved)!=set(expected)|{'heard'} or any(saved[k]!=v for k,v in expected.items()) or not isinstance(saved['heard'],str):
            raise ValueError('Diagnostic resume request/config/status changed')
        print('CACHED DIAGNOSTIC',saved['heard'],flush=True);return
    heard=decode(args.audio,args.recognizer,bound,request['verifier'])
    if prepare(args.audio,args.native_receipt,args.campaign,args.recognizer)[0]!=request:raise ValueError('Audio/receipt/config changed during diagnostic')
    frozen.immutable(result,{'policy':POLICY,'request_sha256':file_digest(args.output/'request.json'),
        'audio_sha256':request['audio_sha256'],'verifier':request['verifier'],'heard':heard,
        'native_quality_passed':False,'acceptance':False,'human_approval':False,'source_comparison_performed':False})
    print('DIAGNOSTIC ONLY',heard,flush=True)

if __name__=='__main__':main()
