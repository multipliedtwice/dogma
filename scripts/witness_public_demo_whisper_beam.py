"""Isolated, local source-blind full Whisper large-v3 beam search; plan is CPU-only.

Only --stage witness loads a GPU model. No synthesis or book inputs change.
Audio longer than 30 seconds is held whole; it is never silently truncated.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]
from rimsky.util import file_digest
from rimsky.speech_quality import transcript_check
import render_public_audiobook_demo as renderer

MODEL = 'openai/whisper-large-v3'
REVISION = '06f233fe06e710322aca913c1bc4249a0d71fce1'
POLICY = 'public-demo-whisper-beam-witness-v1'
MODEL_PATH = ROOT/'training-work/models/whisper-large-v3-verified'
FILES = {
    'model.safetensors':'a8e94b85976e5864ba3e9525c7e6c83b2a1eca42d4b797a0c7c24d778e40fd95',
    'config.json':'ad0e8d1e46f4d01f7861a21509e5d0f977d6cc1f367a370603c92541d819807b',
    'generation_config.json':'fbdfa70135de9b1d31553393f14e80aaeb1936ea36576b2ba864055943c09d23',
    'preprocessor_config.json':'7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711',
    'tokenizer_config.json':'844b642c73a91359722f47b35705f7174686df33d252695d8572cf9ac03a6389',
    'tokenizer.json':'6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca',
    'added_tokens.json':'3c51f66c4c21f9e126970078f11ae77a78c74aee8df606ee9daba86e467108e0',
    'special_tokens_map.json':'1c70773c078cb2ca96e0fcff113102f1d3e2b1504272c3bb63b035d4a6700d87',
    'normalizer.json':'bf1c507dc8724ca9cf9903640dacfb69dae2f00edee4f21ceba106a7392f26dd',
    'merges.txt':'2df2990a395e35e8dfbc7511e08c12d56018d8d04691e0133e5d63b21e154dc6',
    'vocab.json':'e2aa043ef015641d363d8288e7c241c85e36a5c761fb303598e0710233344387',
}
SIZES = {'model.safetensors':3087130976,'config.json':1272,'generation_config.json':3903,
         'preprocessor_config.json':340,'tokenizer_config.json':282843,'tokenizer.json':2480617,
         'added_tokens.json':34648,'special_tokens_map.json':2072,'normalizer.json':52666,
         'merges.txt':493869,'vocab.json':1036558}
MODEL_PROVENANCE = {'repository':'https://huggingface.co/openai/whisper-large-v3',
                    'revision':REVISION,'license':'Apache-2.0',
                    'metadata_source':'Official Hugging Face API plus pinned small-file URLs; reviewed 2026-10-23',
                    'weights':'model.safetensors, full float16 model, not distilled turbo'}
_MODEL_CACHE = {}


def witness_config(language, torch_version=None, transformers_version=None):
    if language not in ('en', 'ru'):
        raise ValueError('Full Whisper witness requires explicit en or ru')
    runtime_files = ['models/whisper/modeling_whisper.py','models/whisper/processing_whisper.py',
                     'models/whisper/feature_extraction_whisper.py','models/whisper/tokenization_whisper.py','generation/utils.py']
    distribution = importlib.metadata.distribution('transformers')
    runtime = {name:file_digest(Path(distribution.locate_file('transformers/'+name))) for name in runtime_files}
    return {'policy': POLICY, 'model': MODEL, 'revision': REVISION, 'files': dict(FILES),'file_sizes':dict(SIZES),
            'implementation_sha256': file_digest(Path(__file__)),
            'dependencies':dependencies(),'transformers_implementation':runtime,
            'torch': torch_version or importlib.metadata.version('torch'),
            'transformers': transformers_version or importlib.metadata.version('transformers'),
            'device': 'cuda:0', 'dtype': 'float16', 'attention': 'sdpa', 'threads': 4,
            'language': language, 'task': 'transcribe', 'do_sample': False, 'max_new_tokens': 256,
            'num_beams':3,'num_return_sequences':1,'early_stopping':False,'length_penalty':1.0,
            'sample_rate': 16000, 'max_audio_seconds': 30, 'truncation': 'forbidden',
            'expected_text_given_to_recognizer': False, 'local_files_only': True}


def validate_verifier_config(config):
    expected = witness_config(config.get('language'), config.get('torch'), config.get('transformers'))
    if (config != expected or config.get('torch') != importlib.metadata.version('torch')
            or config.get('transformers') != importlib.metadata.version('transformers')):
        raise ValueError('Unsupported or changed full Whisper witness module/model/configuration')
    return True


verifier_config = witness_config


def dependencies():
    return {p.relative_to(ROOT).as_posix():file_digest(p) for p in
            [Path(__file__),Path(renderer.__file__),ROOT/'runtime/rimsky/speech_quality.py',
             ROOT/'scripts/recover_public_audiobook_demo.py',ROOT/'scripts/witness_public_demo_whisper_full.py']}


def verify_model(path=MODEL_PATH):
    path = Path(path)
    if not path.is_dir():
        raise ValueError('Missing local full Whisper snapshot')
    entries = sorted(p for p in path.rglob('*') if p.is_file())
    stats = [(p.relative_to(path).as_posix(),p.stat().st_size,p.stat().st_mtime_ns,
              p.stat().st_ctime_ns,p.stat().st_ino,p.stat().st_dev) for p in entries]
    if {name:size for name,size,*_ in stats} != SIZES:
        raise ValueError('Local full Whisper snapshot filenames/sizes differ from exact approved revision')
    key = (str(path.resolve()),tuple(stats),tuple(sorted(FILES.items())))
    if key not in _MODEL_CACHE:
        if renderer.inventory(path) != FILES:
            raise ValueError('Local full Whisper snapshot checksums differ from exact approved revision')
        _MODEL_CACHE.clear();_MODEL_CACHE[key] = True
    return path


def competing_extra_jobs(processes, current_pid):
    by_pid = {int(p['ProcessId']): p for p in processes}
    own, pid = set(), int(current_pid)
    while pid and pid not in own:
        own.add(pid)
        pid = int(by_pid.get(pid, {}).get('ParentProcessId', 0))
    pattern = re.compile(r'(?i)(?:witness_public_demo_(?:turbo|whisper_full|whisper_beam)\.py\b.*--stage\s+witness\b|(?:recover_public_audiobook_demo|context_public_audiobook_recovery|pronounce_public_audiobook_recovery)\.py\b.*--stage\s+(?:render|verify|align)\b)')
    return [p for p in processes if int(p['ProcessId']) not in own and pattern.search(p.get('CommandLine') or '')]


def wait_for_resources():
    while True:
        renderer.wait_for_audio_resources()
        if os.name != 'nt':
            return
        command = ("$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | "
                   "Where-Object { $_.Name -in @('python.exe','pythonw.exe') } | "
                   "Select-Object ProcessId,ParentProcessId,Name,CommandLine) | ConvertTo-Json -Compress")
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                                capture_output=True, text=True, timeout=30, **renderer.HIDDEN)
        if result.returncode:
            raise RuntimeError('Cannot verify independent turbo/recovery GPU workers')
        processes = json.loads(result.stdout or '[]')
        if isinstance(processes, dict):
            processes = [processes]
        busy = competing_extra_jobs(processes, os.getpid())
        if not busy:
            return
        print('WAITING for independent turbo/recovery GPU job', [p['ProcessId'] for p in busy], flush=True)
        time.sleep(5)


def validate_original(context,unit,receipt):
    context.recheck()
    take = receipt.get('take')
    if type(take) is not int or take not in range(6):
        raise ValueError('Full witness is restricted to original standalone takes zero through five')
    if receipt.get('quality_passed') is not True:
        raise ValueError('Full witness requires quality-passed native audio')
    spec = receipt['spec']
    if (any(key in spec for key in ('context_recovery','pronunciation_recovery'))
            or spec.get('source') != unit or spec.get('generation') != context.generation
            or spec.get('request',{}).get('text') != unit.get('synthesis_text',unit['spoken'])
            or spec.get('effects') != 'disabled' or spec.get('model') != context.manifest['speech_model']
            or spec.get('runner') != context.manifest['runner'] or spec.get('reference') != unit['palette_reference']
            or receipt.get('unit') != unit['id'] or renderer.sha(spec) != receipt.get('spec_sha256')
            or next((u for u in context.assignment['units'] if u['id']==unit['id']),None) != unit
            or renderer.read(context.folder/'takes'/unit['id']/('take-%02d.json'%take)) != receipt):
        raise ValueError('Full witness take/source/frozen model/reference binding differs')
    if take>=3:
        from recover_public_audiobook_demo import validate_recovery_receipt
        validate_recovery_receipt(context.folder,unit,receipt)
    path = (context.folder/receipt['audio']).resolve();path.relative_to(context.folder.resolve())
    renderer.checked(path,receipt['audio_sha256'])
    actual = renderer.quality(path,spec['request']['max_tokens'])
    if actual != receipt.get('quality'):
        raise ValueError('Full witness native quality receipt changed')
    return path


def evidence(context,unit,receipt):
    if type(receipt.get('take')) is not int or receipt['take'] not in range(6):
        return []
    if any(key in receipt.get('spec',{}) for key in ('context_recovery','pronunciation_recovery')):
        return []
    paths = sorted((context.folder/'beam-witness').glob(receipt.get('audio_sha256','')+'-*.json'))
    if not paths:
        return []
    validate_original(context,unit,receipt)
    language = {'English':'en','Russian':'ru'}[context.assignment['language']]
    found = []
    for path in paths:
        check = renderer.read(path);config = check['verifier'];validate_verifier_config(config)
        signature = renderer.sha(config)
        if (config['language'] != language or path.name != receipt['audio_sha256']+'-'+signature+'.json'
                or renderer.read(context.folder/'verifiers'/(signature+'.json')) != config):
            raise ValueError('Full witness language/filename/verifier registry differs')
        raw = transcript_check(unit['spoken'],check['heard'],receipt['audio_sha256'],signature)
        if any(check.get(k)!=v for k,v in raw.items()):
            raise ValueError('Full witness raw transcript/audio/source hash cannot be reproduced')
        found.append({'path':path.relative_to(context.folder).as_posix(),'sha256':file_digest(path),
                      'raw':raw,'verifier':config,'role':'witness','family':'whisper'})
    return found


def choose_pending(context, only=(), all_takes=False):
    units = {u['id']: u for u in context.assignment['units']}
    if set(only)-units.keys():
        raise ValueError('Unknown full witness source unit')
    pending = []
    for unit in units.values():
        if only and unit['id'] not in only:
            continue
        candidates = []
        for receipt in renderer.takes(context.folder,unit):
            if (receipt.get('quality_passed') is not True or type(receipt.get('take')) is not int
                    or receipt['take'] not in range(6)
                    or any(k in receipt.get('spec',{}) for k in ('context_recovery','pronunciation_recovery'))):
                continue
            validate_original(context,unit,receipt)
            assessed = context.assess(unit,receipt)
            if assessed and not assessed['decision']['passed']:
                candidates.append((unit,receipt))
        pending.extend(candidates if all_takes else candidates[-1:])
    return pending


def plan(context, only=(), all_takes=False):
    import soundfile as sf
    candidates = choose_pending(context, only, all_takes)
    language = {'English': 'en', 'Russian': 'ru'}[context.assignment['language']]
    config = witness_config(language)
    signature = renderer.sha(config)
    pending, holds = [], []
    for unit, receipt in candidates:
        target = context.folder / 'beam-witness' / (receipt['audio_sha256'] + '-' + signature + '.json')
        if target.exists():
            evidence(context,unit,receipt)
            continue
        path = renderer.checked(context.folder / receipt['audio'], receipt['audio_sha256'])
        info = sf.info(path)
        row = {'unit': unit['id'], 'take': receipt['take'], 'audio': receipt['audio'],
               'audio_sha256': receipt['audio_sha256'], 'frames': info.frames, 'sample_rate': info.samplerate,
               'channels': info.channels, 'duration': info.frames/info.samplerate}
        if not info.frames or info.channels != 1 or info.samplerate != 24000 or info.frames > 30*info.samplerate:
            holds.append({**row, 'status': 'held', 'reason': 'full_audio_requires_mono_24khz_and_at_most_30_seconds',
                          'truncated': False})
        else:
            pending.append((unit, receipt))
    value = {'schema': 1, 'frozen_generation': context.generation, 'verifier': config,
             'verifier_sha256': signature, 'pending': [{'unit':u['id'], 'take':r['take'],
                                                       'audio_sha256':r['audio_sha256']} for u,r in pending],
             'holds': holds, 'expected_text_given_to_recognizer': False}
    destination = context.folder / 'beam-witness/plans' / (renderer.sha(value) + '.json')
    renderer.write(destination, value, immutable=True)
    print('BEAM WHISPER PLAN', len(pending), 'pending', len(holds), 'held', str(destination), flush=True)
    return pending, value


def witness(context, pending, plan_value, model_path=MODEL_PATH):
    if not pending:
        return
    model_path = verify_model(model_path)
    wait_for_resources()
    verify_model(model_path)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    import torch
    import transformers
    from transformers import WhisperProcessor, WhisperForConditionalGeneration
    torch.set_num_threads(4)
    config = witness_config(plan_value['verifier']['language'], torch.__version__, transformers.__version__)
    if config != plan_value['verifier']:
        raise ValueError('Turbo dependency versions changed after planning')
    signature = renderer.sha(config)
    renderer.write(context.folder / 'verifiers' / (signature + '.json'), config, immutable=True)
    processor = WhisperProcessor.from_pretrained(str(model_path), local_files_only=True)
    model = WhisperForConditionalGeneration.from_pretrained(str(model_path), local_files_only=True,
            torch_dtype=torch.float16, attn_implementation='sdpa').to('cuda:0').eval()
    verify_model(model_path)
    try:
        for unit, receipt in pending:
            context.recheck()
            # Revalidate recovery/source linkage immediately before consuming
            # the audio; expected text is used only after blind decoding.
            validate_original(context,unit,receipt)
            path = renderer.checked(context.folder / receipt['audio'], receipt['audio_sha256'])
            audio, rate = sf.read(path, dtype='float32', always_2d=True)
            if not len(audio) or rate != 24000 or audio.shape[1] != 1 or len(audio) > 30*rate or not np.isfinite(audio).all():
                raise ValueError('Audio changed or exceeds full-context witness limit; never truncate')
            divisor = math.gcd(rate, 16000)
            mono = resample_poly(audio[:, 0], 16000//divisor, rate//divisor).astype(np.float32)
            if len(mono) > 30*16000:
                raise ValueError('Full resampled audio exceeds witness window; never truncate')
            heard = decode_audio(model,processor,mono,torch,config)
            renderer.checked(path, receipt['audio_sha256'])
            result = transcript_check(unit['spoken'], heard, receipt['audio_sha256'], signature)
            result.update(verifier=config, interpretation='Audio-only full Whisper beam-search witness; no human listening approval')
            target = context.folder / 'beam-witness' / (receipt['audio_sha256'] + '-' + signature + '.json')
            renderer.write(target, result, immutable=True)
            print('BEAM WHISPER WITNESS', unit['id'], 'take', receipt['take'], result['status'], repr(heard), flush=True)
    finally:
        del model
        torch.cuda.empty_cache()


def decode_audio(model,processor,audio16k,torch,config):
    """Audio-only inference boundary: no source/unit/expected-text argument exists."""
    if not 0<len(audio16k)<=30*16000:
        raise ValueError('Full audio must fit the untruncated 30-second recognition window')
    inputs = processor(audio16k,sampling_rate=16000,return_tensors='pt',return_attention_mask=True,truncation=False)
    if not inputs or set(inputs)-{'input_features','attention_mask'}:
        raise ValueError('Unexpected non-audio recognizer inputs')
    inputs = {k:v.to('cuda:0',dtype=torch.float16) if v.is_floating_point() else v.to('cuda:0') for k,v in inputs.items()}
    with torch.inference_mode():
        tokens = model.generate(**inputs,language=config['language'],task='transcribe',prompt_ids=None,
                                do_sample=False,max_new_tokens=256,num_beams=3,num_return_sequences=1,early_stopping=False,length_penalty=1.0)
    if tokens.shape[-1]>=256:
        raise ValueError('Witness reached output token cap; do not certify a possibly truncated transcript')
    return processor.batch_decode(tokens,skip_special_tokens=True)[0].strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--stage', choices=['plan', 'witness'], default='plan')
    parser.add_argument('--unit', action='append', default=[])
    parser.add_argument('--all-takes', action='store_true')
    parser.add_argument('--model-path', type=Path, default=MODEL_PATH)
    args = parser.parse_args()
    import resolve_public_demo_recognition as resolver
    context = resolver.Context(args.folder)
    pending, value = plan(context, args.unit, args.all_takes)
    if args.stage == 'witness':
        witness(context, pending, value, args.model_path)


if __name__ == '__main__':
    main()
