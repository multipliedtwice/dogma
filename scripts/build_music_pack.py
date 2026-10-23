"""Assemble a YuE2 music pack under resources/music/ from local downloads.

Files are hard-linked when possible (same volume), otherwise copied. The
manifest pins every file's SHA-256, the runner version and the generation
settings; the app refuses a pack whose files no longer match.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from rimsky.util import file_digest, write_json  # noqa: E402

DEFAULT_MODEL = ROOT / 'training-work/music/models/Yue2-3B-GGUF'
DEFAULT_RUNNER = ROOT / 'training-work/higgs-tts-3-pilot/audio-cpp-cuda'
DEFAULT_VERIFIER = ROOT / 'training-work/models/Qwen3-ASR-1.7B'
ATTRIBUTION = ('Music generated with YuE2-3B by Multimodal Art Projection (m-a-p), '
               'CC BY-NC 4.0; audio.cpp GGUF conversion (audio-cpp/Yue2-3B-GGUF).')


def link(source: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.stat().st_size == source.stat().st_size and file_digest(target) == file_digest(source):
            return
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=DEFAULT_MODEL)
    parser.add_argument('--runner', type=Path, default=DEFAULT_RUNNER, help='audio.cpp folder with audiocpp_cli')
    parser.add_argument('--verifier', type=Path, default=DEFAULT_VERIFIER, help='Qwen3-ASR model folder')
    parser.add_argument('--output', type=Path, default=ROOT / 'resources/music/yue2-3b-q8')
    parser.add_argument('--model-gguf', default='yue2-3b-q8_0.gguf')
    parser.add_argument('--vae-gguf', default='yue2-vae-f16.gguf')
    args = parser.parse_args()
    out = args.output.resolve()
    executable = 'audiocpp_cli.exe' if os.name == 'nt' else 'audiocpp_cli'
    runner = args.runner / executable
    version = subprocess.run([str(runner), '--version'], cwd=args.runner, capture_output=True, text=True, check=True).stdout.splitlines()[0].strip()
    plan = {}
    for path in args.runner.iterdir():
        if path.is_file() and (path.name == executable or path.suffix.lower() in ('.dll', '.so', '.dylib') or path.name == 'LICENSE'):
            plan[f'runner/{path.name}'] = path
    for name in ('sidecars/yue2-generation-config.json', 'sidecars/yue2-model-config.json', 'sidecars/yue2-qwen.tiktoken',
                 'sidecars/yue2-vae-config.json', args.model_gguf, args.vae_gguf):
        plan[f'model/{name}'] = args.model / name
    for path in args.verifier.rglob('*'):
        if path.is_file() and '.cache' not in path.parts:
            plan[f'verifier/{args.verifier.name}/{path.relative_to(args.verifier).as_posix()}'] = path
    missing = [str(path) for path in plan.values() if not path.is_file()]
    if missing:
        raise SystemExit('Missing source files:\n' + '\n'.join(missing))
    files = {}
    for relative, source in sorted(plan.items()):
        link(source, out / relative)
        files[relative] = file_digest(out / relative)
        print('pinned', relative, flush=True)
    write_json(out / 'music-pack.json', {
        'format': 'rimsky-music-pack', 'schema': 1, 'name': 'YuE2 3B Q8_0', 'family': 'yue2',
        'runner': f'runner/{executable}', 'runner_version': version,
        'model_dir': 'model', 'model_gguf': args.model_gguf, 'vae_gguf': args.vae_gguf,
        'generation': {'cot': 'off', 'num_inference_steps': 8,
                       # Sized for <=24 s takes; keeps CPU commit near 16 GB instead of 27 GB.
                       'session': {'ar_prefill_graph_arena_mb': 512, 'ar_decode_graph_arena_mb': 256,
                                   'nar_graph_arena_mb': 1536, 'vae_graph_arena_mb': 1024,
                                   'model_weight_context_mb': 4608, 'vae_weight_context_mb': 512}},
        'verifier': {'backend': 'qwen3_asr', 'path': f'verifier/{args.verifier.name}'},
        'license': {'id': 'CC-BY-NC-4.0', 'attribution': ATTRIBUTION,
                    'source': 'https://huggingface.co/audio-cpp/Yue2-3B-GGUF',
                    'revision': 'cca74d8980c00f30ab8d52248a367c01ad633ec2'},
        'files': files})
    print('MUSIC PACK READY', out, len(files), 'files', flush=True)


if __name__ == '__main__':
    main()
