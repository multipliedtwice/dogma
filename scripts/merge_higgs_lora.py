"""Merge a local Higgs QLoRA adapter into native weights without a full RAM copy.

Processes one tensor at a time and writes small safetensor shards. Frozen codec
weights are copied unchanged. This output feeds the installed audio.cpp GGUF
converter; it does not change the base weights or the training checkpoint.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from export_higgs_training import read, sha, write


def lora_name(native):
    if native.startswith('body.') and native.endswith('.weight'):
        return 'base_model.model.model.' + native[len('body.'):-len('.weight')]
    return None


def base_sources(base: Path) -> tuple[list[tuple[Path, list[str] | None]], str, dict[str, str]]:
    """Return immutable safetensor sources for one file or a HF sharded folder."""
    if base.is_file():
        return [(base, None)], sha(base), {base.name: sha(base)}
    index_path = base / 'model.safetensors.index.json'
    if not index_path.is_file():
        raise ValueError('Base must be a safetensors file or a sharded checkpoint directory.')
    index = read(index_path)
    by_file: dict[str, list[str]] = defaultdict(list)
    for key, filename in index.get('weight_map', {}).items():
        by_file[filename].append(key)
    if not by_file:
        raise ValueError('Sharded base index has no weights.')
    files = {index_path.name: sha(index_path)}
    sources = []
    for filename in sorted(by_file):
        path = base / filename
        if not path.is_file():
            raise ValueError('Missing sharded base weight: ' + filename)
        files[filename] = sha(path)
        sources.append((path, sorted(by_file[filename])))
    fingerprint = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return sources, fingerprint, files


def merge(base, adapter, native_config, output, report_path, max_shard_bytes=256 * 1024**2):
    import torch
    from safetensors import safe_open
    from safetensors.torch import save_file
    if output.exists():
        raise ValueError('Use a new immutable merged-checkpoint directory.')
    report = read(report_path)
    checkpoint = adapter / 'adapter_model.safetensors'
    if report.get('status') != 'trained_pending_validation' or report.get('checkpoint_sha256') != sha(checkpoint):
        raise ValueError('Only a completed, hash-bound training run may be converted.')
    config = read(adapter / 'adapter_config.json')
    if (config.get('peft_type') != 'LORA' or config.get('rank_pattern') or config.get('alpha_pattern')
            or config.get('use_dora') or config.get('use_rslora') or config.get('modules_to_save') or config.get('fan_in_fan_out')):
        raise ValueError('Unsupported LoRA layout; refuse to guess a merge.')
    scaling = config['lora_alpha'] / config['r']
    output.mkdir(parents=True)
    for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'chat_template.jinja'):
        shutil.copyfile(native_config / name, output / name)
    weight_map, shards, seen, modified, bucket = {}, {}, set(), [], {}
    bucket_bytes, total = 0, 0
    def flush():
        nonlocal bucket, bucket_bytes
        if not bucket:
            return
        filename = f'model-{len(shards)+1:05d}.safetensors'
        save_file(bucket, str(output / filename), metadata={'format': 'pt'})
        shards[filename] = sha(output / filename)
        weight_map.update({key: filename for key in bucket})
        print(f'Merged shard {filename}: {bucket_bytes/2**20:.0f} MiB', flush=True)
        bucket, bucket_bytes = {}, 0
    sources, base_hash, base_files = base_sources(base)
    with safe_open(str(checkpoint), framework='pt', device='cpu') as lora:
        available = set(lora.keys())
        for source, indexed_keys in sources:
            with safe_open(str(source), framework='pt', device='cpu') as weights:
                keys = list(weights.keys()) if indexed_keys is None else indexed_keys
                if indexed_keys is not None and set(keys) != set(weights.keys()):
                    raise ValueError('Sharded base index and tensor contents disagree: ' + source.name)
                for key in keys:
                    if key.startswith('tied.head.'):
                        continue  # redundant tied copies, not codec or model parameters
                    tensor = weights.get_tensor(key)
                    prefix = lora_name(key)
                    a_name = prefix + '.lora_A.weight' if prefix else ''
                    b_name = prefix + '.lora_B.weight' if prefix else ''
                    if a_name in available or b_name in available:
                        if not {a_name, b_name} <= available:
                            raise ValueError('Incomplete LoRA pair: ' + key)
                        a, b = lora.get_tensor(a_name).float(), lora.get_tensor(b_name).float()
                        delta = (b @ a) * scaling
                        if delta.shape != tensor.shape or not torch.isfinite(delta).all():
                            raise ValueError('Invalid LoRA delta: ' + key)
                        tensor = (tensor.float() + delta).to(tensor.dtype).contiguous()
                        seen.update((a_name, b_name))
                        modified.append(key)
                        del a, b, delta
                    size = tensor.numel() * tensor.element_size()
                    if bucket and bucket_bytes + size > max_shard_bytes:
                        flush()
                    bucket[key] = tensor
                    bucket_bytes += size
                    total += size
        flush()
        if seen != available or not modified:
            raise ValueError('Adapter contains unmatched parameters or no model changes.')
    write(output / 'model.safetensors.index.json', {'metadata': {'total_size': total}, 'weight_map': weight_map})
    proof = {'schema': 1, 'kind': 'lora', 'optimizer_steps': report['optimizer_steps'],
             'dataset_sha256': report['dataset_sha256'], 'checkpoint_sha256': sha(checkpoint),
             'base_sha256': base_hash, 'base_files': base_files,
             'training_report_sha256': sha(report_path),
             'modified_tensors': modified, 'shards': shards,
             'status': 'custom_checkpoint_pending_audio_validation'}
    write(output / 'merge-provenance.json', proof)
    return proof


def convert(native, output, converter, model_spec, proof):
    if output.exists():
        raise ValueError('Refuse to overwrite an existing GGUF.')
    result = subprocess.run([str(converter.resolve()), '--input', str((native / 'model.safetensors.index.json').resolve()),
                             '--root', str(native.resolve()), '--model-spec', str(model_spec.resolve()), '--family', 'higgs_audio_tts',
                             '--output', str(output.resolve()), '--type', 'q8_0'], check=True)
    updated = {**proof, 'model_sha256': sha(output), 'converter_sha256': sha(converter), 'precision': 'q8_0'}
    write(output.with_suffix('.provenance.json'), updated)
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    base = ROOT / 'training-work/higgs-production'
    runner = ROOT / 'training-work/higgs-tts-3-pilot/audio-cpp-cuda'
    parser.add_argument('--base', type=Path, default=base / 'models/base/model.safetensors',
                        help='Single safetensors file or a sharded Hugging Face checkpoint directory.')
    parser.add_argument('--native-config', type=Path, default=base / 'models/native-config')
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gguf', type=Path)
    args = parser.parse_args()
    proof = merge(args.base, args.run / 'adapter', args.native_config, args.output, args.run / 'training-report.json')
    if args.gguf:
        proof = convert(args.output, args.gguf, runner / 'audiocpp_gguf.exe', runner / 'model_specs/higgs_audio_tts.json', proof)
    print(json.dumps({key: value for key, value in proof.items() if key not in ('modified_tensors', 'shards')}, indent=2))


if __name__ == '__main__':
    main()
