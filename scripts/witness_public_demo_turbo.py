"""Isolated, local source-blind Whisper turbo witness; plan is CPU-only.

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

MODEL = 'openai/whisper-large-v3-turbo'
REVISION = '41f01f3fe87f28c78e2fbf8b568835947dd65ed9'
POLICY = 'public-demo-whisper-turbo-witness-v1'
MODEL_PATH = Path.home() / '.cache/huggingface/hub/models--openai--whisper-large-v3-turbo/snapshots' / REVISION
FILES = {
    'added_tokens.json': '3c51f66c4c21f9e126970078f11ae77a78c74aee8df606ee9daba86e467108e0',
    'config.json': 'c5b526b3e3cd64cd8940dabb45e8ba726629e22d8ed389c29b552f9140daf04a',
    'generation_config.json': 'cce11bfe3aaa6ae9e072ea2637caaec8795e68d9b67e655a5af16ee509681a4c',
    'merges.txt': '2df2990a395e35e8dfbc7511e08c12d56018d8d04691e0133e5d63b21e154dc6',
    'model.safetensors': '542566a422ae4f3fd23f1ba11add198fca01bbf82e66e6a2857b3f608b1eb9d1',
    'normalizer.json': 'bf1c507dc8724ca9cf9903640dacfb69dae2f00edee4f21ceba106a7392f26dd',
    'preprocessor_config.json': '7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711',
    'special_tokens_map.json': 'baea4ea09372eb4fca86b4e4346139fd73cb807d5087e9de0948e971739c3e74',
    'tokenizer.json': '297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd',
    'tokenizer_config.json': '844b642c73a91359722f47b35705f7174686df33d252695d8572cf9ac03a6389',
    'vocab.json': 'e2aa043ef015641d363d8288e7c241c85e36a5c761fb303598e0710233344387',
}


def verifier_config(language, torch_version=None, transformers_version=None):
    if language not in ('en', 'ru'):
        raise ValueError('Turbo witness requires explicit en or ru')
    return {'policy': POLICY, 'model': MODEL, 'revision': REVISION, 'files': dict(FILES),
            'implementation_sha256': file_digest(Path(__file__)),
            'torch': torch_version or importlib.metadata.version('torch'),
            'transformers': transformers_version or importlib.metadata.version('transformers'),
            'device': 'cuda:0', 'dtype': 'float16', 'attention': 'sdpa', 'threads': 4,
            'language': language, 'task': 'transcribe', 'do_sample': False, 'max_new_tokens': 256,
            'sample_rate': 16000, 'max_audio_seconds': 30, 'truncation': 'forbidden',
            'expected_text_given_to_recognizer': False, 'local_files_only': True}


def validate_verifier_config(config):
    expected = verifier_config(config.get('language'), config.get('torch'), config.get('transformers'))
    if config != expected or not all(isinstance(config.get(k), str) and config[k] for k in ('torch', 'transformers')):
        raise ValueError('Unsupported or changed turbo witness module/model/configuration')
    return True


def verify_model(path=MODEL_PATH):
    inventory = renderer.inventory(path)
    if inventory != FILES:
        raise ValueError('Local turbo snapshot inventory differs from exact approved revision')
    return Path(path)


def competing_extra_jobs(processes, current_pid):
    by_pid = {int(p['ProcessId']): p for p in processes}
    own, pid = set(), int(current_pid)
    while pid and pid not in own:
        own.add(pid)
        pid = int(by_pid.get(pid, {}).get('ParentProcessId', 0))
    pattern = re.compile(r'(?i)(?:witness_public_demo_turbo\.py\b.*--stage\s+witness\b|recover_public_audiobook_demo\.py\b.*--stage\s+render\b)')
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


def choose_pending(context, report, only=(), all_takes=False):
    units = {u['id']: u for u in context.assignment['units']}
    pending = []
    for row in report['units']:
        if row['selected'] or only and row['id'] not in only:
            continue
        candidates = []
        for receipt in renderer.takes(context.folder, units[row['id']]):
            if receipt.get('quality_passed') is not True:
                continue
            assessed = context.assess(units[row['id']], receipt)
            if assessed and not assessed['decision']['passed']:
                candidates.append((units[row['id']], receipt))
        pending.extend(candidates if all_takes else candidates[-1:])
    return pending


def plan(context, only=(), all_takes=False):
    import soundfile as sf
    import resolve_public_demo_recognition as resolver
    summary = resolver.report(context, only)
    candidates = choose_pending(context, summary, only, all_takes)
    language = {'English': 'en', 'Russian': 'ru'}[context.assignment['language']]
    config = verifier_config(language)
    signature = renderer.sha(config)
    pending, holds = [], []
    for unit, receipt in candidates:
        target = context.folder / 'witnesses' / (receipt['audio_sha256'] + '-' + signature + '.json')
        if target.exists():
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
    destination = context.folder / 'turbo-witness/plans' / (renderer.sha(value) + '.json')
    renderer.write(destination, value, immutable=True)
    print('TURBO PLAN', len(pending), 'pending', len(holds), 'held', str(destination), flush=True)
    return pending, value


def witness(context, pending, plan_value, model_path=MODEL_PATH):
    if not pending:
        return
    model_path = verify_model(model_path)
    wait_for_resources()
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    import torch
    import transformers
    from transformers import WhisperProcessor, WhisperForConditionalGeneration
    torch.set_num_threads(4)
    config = verifier_config(plan_value['verifier']['language'], torch.__version__, transformers.__version__)
    if config != plan_value['verifier']:
        raise ValueError('Turbo dependency versions changed after planning')
    signature = renderer.sha(config)
    renderer.write(context.folder / 'verifiers' / (signature + '.json'), config, immutable=True)
    processor = WhisperProcessor.from_pretrained(str(model_path), local_files_only=True)
    model = WhisperForConditionalGeneration.from_pretrained(str(model_path), local_files_only=True,
            torch_dtype=torch.float16, attn_implementation='sdpa').to('cuda:0').eval()
    try:
        for unit, receipt in pending:
            context.recheck()
            # Revalidate recovery/source linkage immediately before consuming
            # the audio; expected text is used only after blind decoding.
            context.assess(unit, receipt)
            path = renderer.checked(context.folder / receipt['audio'], receipt['audio_sha256'])
            audio, rate = sf.read(path, dtype='float32', always_2d=True)
            if not len(audio) or rate != 24000 or audio.shape[1] != 1 or len(audio) > 30*rate or not np.isfinite(audio).all():
                raise ValueError('Audio changed or exceeds full-context witness limit; never truncate')
            divisor = math.gcd(rate, 16000)
            mono = resample_poly(audio[:, 0], 16000//divisor, rate//divisor).astype(np.float32)
            if len(mono) > 30*16000:
                raise ValueError('Full resampled audio exceeds witness window; never truncate')
            inputs = processor(mono, sampling_rate=16000, return_tensors='pt', return_attention_mask=True)
            inputs = {k: v.to('cuda:0', dtype=torch.float16) if v.is_floating_point() else v.to('cuda:0')
                      for k,v in inputs.items()}
            with torch.inference_mode():
                tokens = model.generate(**inputs, language=config['language'], task='transcribe',
                                        do_sample=False, max_new_tokens=256)
            if tokens.shape[-1] >= 256:
                raise ValueError('Witness reached output token cap; do not certify a possibly truncated transcript')
            heard = processor.batch_decode(tokens, skip_special_tokens=True)[0].strip()
            renderer.checked(path, receipt['audio_sha256'])
            result = transcript_check(unit['spoken'], heard, receipt['audio_sha256'], signature)
            result.update(verifier=config, interpretation='Audio-only turbo witness; no human listening approval')
            target = context.folder / 'witnesses' / (receipt['audio_sha256'] + '-' + signature + '.json')
            renderer.write(target, result, immutable=True)
            print('TURBO WITNESS', unit['id'], 'take', receipt['take'], result['status'], repr(heard), flush=True)
    finally:
        del model
        torch.cuda.empty_cache()


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
