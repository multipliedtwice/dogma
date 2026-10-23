"""Book-directed, short instrumental demo cues with immutable audio-only checks."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]
from rimsky import music
from rimsky.speech_quality import word_tokens
from rimsky.util import file_digest, digest, write_json
from prepare_public_audiobook_demo import call, read
from render_public_audiobook_demo import wait_for_audio_resources

PACK = ROOT / 'resources/music/yue2-3b-q8'
ASR = ROOT / 'training-work/.venv-asr/Scripts/python.exe'
HIDDEN = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def prepare(folder):
    book = read(folder / 'prepared/book.json')
    out = folder / 'music'
    out.mkdir(exist_ok=True)
    pack = music.load_music_pack(PACK, {})
    request = music.palette_request(book, ['c0001'], music.EXCERPT_CHARS['cli']['palette'])
    palette = call(out / 'direction', 'palette', music.DIRECTION_SYSTEM['palette'], request,
                   music.palette_schema(), music.checked_palette)
    request = music.chapter_request(book, 'c0001', palette, None, music.EXCERPT_CHARS['cli']['chapter'])
    schema = music.chapter_schema(request)
    schema['properties']['scenes']['maxItems'] = 1
    schema['properties']['scenes']['items']['properties']['ambience']['enum'] = ['none']
    answer = call(out / 'direction', 'chapter', music.DIRECTION_SYSTEM['chapter'] +
                  ' This short standalone demonstration uses one sustained scene without procedural ambience; '
                  'choose its overall musical mood from the supplied context.', request,
                  schema, lambda a: music.checked_chapter(a, request))
    answer = music.checked_chapter(answer, request)
    if len(answer['scenes']) != 1 or answer['scenes'][0]['ambience'] != 'none':
        raise ValueError('Demo mixer requires its explicit scene/ambience layout to be extended for this direction')
    mood = music.BED_OF.get(answer['scenes'][0]['mood'], 'calm')
    cues = [{'id': kind, 'kind': kind, 'mood': mood,
             'style': music.compose_style(kind, mood, palette), 'seconds': music.CUES[kind]['take_seconds'],
             'seed': int(digest({'book': book['source_hash'], 'kind': kind})[:7], 16)}
            for kind in ('bed', 'ending')]
    plan = {'schema': 1, 'source_sha256': file_digest(folder / 'prepared/book.json'),
            'palette': palette, 'direction': answer, 'cues': cues,
            'pack': pack['_fingerprint'], 'implementation_sha256': file_digest(Path(__file__))}
    path = out / 'plan.json'
    if path.exists() and read(path) != plan:
        raise ValueError('Frozen demo music plan changed')
    if not path.exists():
        write_json(path, plan)
    print('MUSIC PLANNED', folder.name, mood, flush=True)


def verify(paths):
    import torch
    from asr_batching import last_position_logits
    from qwen_asr import Qwen3ASRModel
    torch.set_num_threads(4)
    root = ROOT / 'training-work/models/Qwen3-ASR-1.7B'
    config = {'model': 'Qwen3-ASR-1.7B', 'files': {p.relative_to(root).as_posix(): file_digest(p)
              for p in root.rglob('*') if p.is_file() and '.cache' not in p.parts},
              'language': 'Auto', 'device': 'cuda:0', 'dtype': 'bfloat16', 'attention': 'sdpa',
              'expected_text_given_to_recognizer': False, 'implementation_sha256': file_digest(Path(__file__)),
              'batching_sha256': file_digest(Path(__file__).with_name('asr_batching.py'))}
    model = last_position_logits(Qwen3ASRModel.from_pretrained(str(root), device_map='cuda:0',
        dtype=torch.bfloat16, attn_implementation='sdpa', local_files_only=True,
        max_inference_batch_size=1, max_new_tokens=512))
    try:
        for path in paths:
            checksum = file_digest(path)
            heard = model.transcribe(str(path), language=None)[0].text
            if checksum != file_digest(path):
                raise ValueError('Music changed during verification')
            words = word_tokens(heard)
            check = {'audio_sha256': checksum, 'verifier': config, 'verifier_sha256': digest(config),
                     'heard': heard, 'heard_words': words, 'passed': not words,
                     'status': 'vocals_detected' if words else 'no_words_detected'}
            write_json(path.with_suffix('.check.json'), check)
            print('MUSIC CHECK', path.name, check['status'], repr(heard), flush=True)
    finally:
        del model
        torch.cuda.empty_cache()


def render(folder):
    import soundfile as sf
    out = folder / 'music'
    plan = read(out / 'plan.json')
    pack = music.load_music_pack(PACK, {})
    if pack['_fingerprint'] != plan['pack'] or file_digest(folder / 'prepared/book.json') != plan['source_sha256']:
        raise ValueError('Music pack/source changed')
    selected = {}
    for cue in plan['cues']:
        for take in range(3):
            work = out / cue['kind'] / f'take-{take:02d}'
            work.mkdir(parents=True, exist_ok=True)
            raw = work / 'raw.wav'
            receipt_path = work / 'receipt.json'
            spec = music.cue_spec(pack, cue, 'cuda')
            if not receipt_path.exists():
                wait_for_audio_resources()
                print('MUSIC RENDER', folder.name, cue['kind'], take, flush=True)
                receipt = {'spec': spec, 'cue': cue, 'take': take, 'quality_passed': False}
                try:
                    music.generate(pack, spec, take, raw, lambda: False, timeout=600)
                    audio, rate = sf.read(raw, dtype='float32', always_2d=True)
                    receipt.update(quality=music.audio_checks(audio, rate, cue['seconds']),
                                   audio_sha256=file_digest(raw), quality_passed=True)
                except Exception as exc:
                    receipt['error'] = str(exc)
                write_json(receipt_path, receipt)
            receipt = read(receipt_path)
            if not receipt['quality_passed']:
                continue
            if file_digest(raw) != receipt['audio_sha256']:
                raise ValueError('Music raw take changed')
            check_path = raw.with_suffix('.check.json')
            if not check_path.exists():
                wait_for_audio_resources()
                subprocess.run([str(ASR), '-X', 'utf8', '-u', str(Path(__file__)), '--stage', 'verify',
                                '--audio', str(raw)], cwd=ROOT, check=True, **HIDDEN)
            check = read(check_path)
            if check['audio_sha256'] != receipt['audio_sha256']:
                raise ValueError('Music check binds to a different take')
            if not check['passed']:
                continue
            selected[cue['kind']] = {'path': str(raw), 'sha256': receipt['audio_sha256'],
                'check_path': str(check_path), 'check_sha256': file_digest(check_path),
                'receipt_path': str(receipt_path), 'receipt_sha256': file_digest(receipt_path)}
            break
        else:
            write_json(out / 'held.json', {'cue': cue, 'reason': 'Three bounded instrumental takes failed'})
            raise ValueError('Music remains held: ' + cue['kind'])
    result = {**selected, 'bed_rms_dbfs': -38, 'ending_rms_dbfs': -30,
              'plan_sha256': file_digest(out / 'plan.json'),
              'attribution': 'YuE2-3B by m-a-p, CC BY-NC 4.0; audio.cpp conversion.'}
    write_json(out / 'selected.json', result)
    print('MUSIC COMPLETE', folder.name, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path)
    parser.add_argument('--stage', choices=['prepare', 'render', 'verify'], required=True)
    parser.add_argument('--audio', type=Path, nargs='+')
    args = parser.parse_args()
    if args.stage == 'verify':
        verify(args.audio)
    elif args.stage == 'prepare':
        prepare(args.folder.resolve())
    else:
        render(args.folder.resolve())


if __name__ == '__main__':
    main()
