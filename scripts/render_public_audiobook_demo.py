"""Isolated, durable bilingual demo renderer. Never writes the corpus or app library.

Inputs are frozen assignment.json and manifest.json in --folder. Run ``all`` in
the TTS environment; verification runs in the pinned ASR Python subprocess.
All recognition is audio-only. A public complete.json is emitted only when every
unit is independently checked and the approved opening and requested music exist.
"""
from __future__ import annotations
import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PLAYER_SOURCE = Path(__file__).with_name('public_demo_player.js')
sys.path.insert(0, str(ROOT / 'runtime'))
from rimsky.production_higgs_model import resolve_production_higgs_model
from rimsky.speech_quality import transcript_check, word_tokens
from rimsky.util import file_digest

HIDDEN = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
DEFAULT_SAMPLING = dict(temperature=.8, top_k=50, top_p=.95, repetition_penalty=1.1,
                        do_sample=True, max_tokens=512, text_chunk_mode='tag_aware', text_chunk_size=65536)


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def write(path, value, immutable=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        if read(path) != value:
            raise ValueError('Immutable record differs: ' + str(path))
        return
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    os.replace(temporary, path)


def checked(path, expected):
    path = Path(path)
    if not path.is_file() or file_digest(path) != expected:
        raise ValueError('Missing or changed pinned file: ' + str(path))
    return path


def load(folder):
    assignment, manifest = read(folder / 'assignment.json'), read(folder / 'manifest.json')
    if assignment.get('language') not in ('Russian', 'English') or not assignment.get('units'):
        raise ValueError('Assignment requires explicit Russian/English and nonempty units')
    ids = set()
    for unit in assignment['units']:
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', unit['id']) or unit['id'] in ids:
            raise ValueError('Unsafe or duplicate unit id')
        ids.add(unit['id'])
        if not unit.get('text', '').strip() or not unit.get('spoken', '').strip():
            raise ValueError('Empty source/synthesis unit')
        sound_tags = r'(?:emotion|prosody|pitch|speed|vocal|style|effect)'
        if re.search(r'(?:\[\s*|<\|\s*)' + sound_tags, unit.get('synthesis_text', unit['spoken']), re.I):
            raise ValueError('Disabled sound-changing tag')
        ref = unit['palette_reference']
        if ref.get('reviewed') is not True or not ref.get('text'):
            raise ValueError('A reviewed performance reference is required')
    frozen = {'assignment': assignment, 'manifest': manifest,
              'implementation_sha256': file_digest(Path(__file__)),
              'player_sha256': file_digest(PLAYER_SOURCE),
              'join_sha256': file_digest(ROOT / 'runtime/rimsky/audio_join.py'),
              'comparison_sha256': file_digest(ROOT / 'runtime/rimsky/speech_quality.py')}
    # Implementation upgrades must be explicit: preserve this run and start a new folder.
    write(folder / 'frozen.json', frozen, immutable=True)
    return assignment, manifest, sha(frozen)


def takes(folder, unit):
    return [read(p) for p in sorted((folder / 'takes' / unit['id']).glob('take-*.json'))]


def audit(folder, unit, receipt):
    if not receipt.get('quality_passed'):
        return None
    checked(folder / receipt['audio'], receipt['audio_sha256'])
    primary = folder / 'checks' / (receipt['audio_sha256'] + '.json')
    if not primary.exists():
        return None
    check = read(primary)
    for candidate in [check, *[read(p) for p in sorted((folder / 'witnesses').glob(receipt['audio_sha256'] + '-*.json'))]]:
        if sha(candidate['verifier']) != candidate['verifier_sha256']:
            raise ValueError('Verifier signature differs')
        recomputed = transcript_check(unit['spoken'], candidate['heard'], receipt['audio_sha256'], candidate['verifier_sha256'])
        if any(candidate.get(k) != v for k, v in recomputed.items()):
            raise ValueError('Recognition evidence is not bound to this source/take')
        if recomputed['status'] == 'asr_exact_match':
            return {**candidate, 'passed': True, 'resolution': 'primary_exact' if candidate == check else 'independent_witness_exact',
                    'primary_check_sha256': file_digest(primary)}
    return {**check, 'passed': False}


def selected(folder, unit):
    for receipt in reversed(takes(folder, unit)):
        decision = audit(folder, unit, receipt)
        if decision and decision['passed']:
            return receipt, decision
    return None


def checkpoint(folder, assignment, generation, max_takes):
    units = []
    for unit in assignment['units']:
        history = takes(folder, unit)
        pick = selected(folder, unit)
        if pick:
            status = 'verified'
        elif not history:
            status = 'pending'
        elif history[-1].get('quality_passed') and audit(folder, unit, history[-1]) is None:
            status = 'rendered'
        else:
            status = 'held' if len(history) >= max_takes else 'retry_pending'
        units.append({'id': unit['id'], 'status': status, 'take_count': len(history),
                      'selected_audio': pick[0]['audio'] if pick else None})
    value = {'schema': 1, 'generation': generation, 'updated_unix': time.time(), 'pid': os.getpid(),
             'units': units, 'counts': {k: sum(u['status'] == k for u in units)
                                      for k in ('pending', 'rendered', 'verified', 'held', 'retry_pending')}}
    write(folder / 'state.json', value)
    print('PROGRESS', json.dumps(value['counts']), flush=True)
    return value


def quality(path, max_tokens=None):
    import numpy as np
    import soundfile as sf
    from rimsky.audio_join import boundary_check, prepare_clip
    audio, rate = sf.read(path, dtype='float32', always_2d=True)
    if rate != 24000 or audio.shape[1] != 1 or not len(audio) or not np.isfinite(audio).all():
        raise ValueError('Invalid native speech format')
    mono = audio[:, 0]
    result = dict(sample_rate=rate, channels=1, duration=len(mono)/rate,
                  peak=float(np.abs(mono).max()), rms=float(np.sqrt(np.mean(mono**2))),
                  clipped_fraction=float(np.mean(np.abs(mono) >= .999)), boundary=boundary_check(mono, rate))
    if result['rms'] < .0001 or result['clipped_fraction'] > .001 or not .15 <= result['duration'] <= 45:
        raise ValueError('Silent/clipped/overlong speech: ' + json.dumps(result))
    if max_tokens and result['duration']*25 >= (max_tokens-8)*.98:
        raise ValueError('Reached synthesis token cap; possible truncated speech')
    prepare_clip(mono, rate)
    return result


def competing_audio_jobs(processes, current_pid):
    """Ignore our supervisor/venv launchers, retain independent GPU users."""
    by_pid = {int(p['ProcessId']): p for p in processes}
    own_chain = set()
    pid = int(current_pid)
    while pid and pid not in own_chain:
        own_chain.add(pid)
        pid = int(by_pid.get(pid, {}).get('ParentProcessId', 0))
    patterns = [
        r'(?i)(?:^|[\\/\s])(music_[\w-]+|run_higgs_stress_pilot|train_higgs_local|evaluate_higgs_stress_preference)\.py(?:\s|$)',
        r'(?i)(?:^|[\\/\s])(audit_higgs_[\w-]+|run_(?:listener|higgs)_stress_secondary_asr|measure_stress_witness|revise_higgs_spoken_take|revise_book1_reported_stress|regenerate_higgs_book_trial)\.py(?:\s|$)',
        r'(?i)(?:render_public_audiobook_demo|prepare_public_demo_music|build_dogma_intros)\.py\b.*--stage\s+(?:render|verify|witness|speech|music)\b',
    ]
    return [p for p in processes if int(p['ProcessId']) not in own_chain and
            (str(p.get('Name', '')).casefold() == 'audiocpp_cli.exe' or
             any(re.search(pattern, p.get('CommandLine') or '') for pattern in patterns))]


def wait_for_audio_resources():
    """Do not overlap native speech with the user's independent audio work."""
    if os.name != 'nt':
        return
    command = ("$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process | "
               "Where-Object { $_.Name -in @('python.exe','pythonw.exe','audiocpp_cli.exe') } | "
               "Select-Object ProcessId,ParentProcessId,Name,CommandLine) | ConvertTo-Json -Compress")
    while True:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                                capture_output=True, text=True, timeout=30, **HIDDEN)
        if result.returncode:
            raise RuntimeError('Could not verify shared audio resource availability')
        processes = json.loads(result.stdout or '[]')
        if isinstance(processes, dict):
            processes = [processes]
        busy = competing_audio_jobs(processes, os.getpid())
        if not busy:
            return
        print('WAITING for independent music/training/ASR/audio jobs', [p['ProcessId'] for p in busy], flush=True)
        time.sleep(5)


def render(folder, assignment, manifest, generation, args):
    wait_for_audio_resources()
    pin = resolve_production_higgs_model(ROOT)  # Full deployed hash before every native synthesis session.
    frozen = manifest['speech_model']
    if (pin.sha256 != frozen['sha256'] or pin.checkpoint != frozen['checkpoint']
            or file_digest(pin.provenance_path) != frozen['provenance_sha256']
            or file_digest(pin.manifest_path) != frozen['manifest_sha256']
            or pin.provenance != frozen['provenance']):
        raise ValueError('Frozen production model provenance changed')
    runner = checked(manifest['runner']['path'], manifest['runner']['sha256'])
    if {p.name: file_digest(p) for p in sorted(runner.parent.glob('*.dll'))} != manifest['runner']['dlls']:
        raise ValueError('Runner DLL inventory changed')
    if any(k not in DEFAULT_SAMPLING for k in manifest.get('sampling', {})):
        raise ValueError('Unknown or disabled synthesis sampling parameter')
    pending = []
    for index, unit in enumerate(assignment['units']):
        if args.unit and unit['id'] not in args.unit:
            continue
        history = takes(folder, unit)
        if selected(folder, unit) or len(history) >= args.max_takes:
            continue
        if history and history[-1].get('quality_passed') and audit(folder, unit, history[-1]) is None:
            continue  # Never overwrite an unverified take.
        ref = unit['palette_reference']
        reference = checked(ref.get('path', ref.get('audio')), ref['audio_sha256'])
        take = len(history)
        synthesis_text = unit.get('synthesis_text', unit['spoken'])
        token_cap = min(2048, math.ceil((6 + .35*sum(ch.isalpha() for ch in synthesis_text))*25)+8)
        request = {**DEFAULT_SAMPLING, **manifest.get('sampling', {}), 'id': unit['id'] + f'-t{take}',
                   'text': synthesis_text, 'voice_ref': str(reference), 'reference_text': ref['text'],
                   'max_tokens': token_cap,
                   'seed': (unit.get('seed', manifest.get('seed', 202610230) + index * 100) + take) % 2**31}
        spec = dict(request=request, generation=generation, language=assignment['language'],
                    source=unit, model=frozen, runner=manifest['runner'], reference=ref, effects='disabled')
        pending.append((unit, take, request, spec))
        if len(pending) >= args.batch_size:
            break
    if not pending:
        return 0
    sequence = {'requests': [row[2] for row in pending]}
    sequence_id = sha({'sequence': sequence, 'specifications': [r[3] for r in pending]})
    sequence_path = folder / 'requests' / (sequence_id + '.json')
    write(sequence_path, sequence, immutable=True)
    staging = folder / 'native' / sequence_id
    staging.mkdir(parents=True, exist_ok=True)
    missing = [r[2] for r in pending if not (staging / (r[2]['id'] + '.wav')).is_file()]
    if missing and len(missing) != len(pending):
        # A killed supervisor may leave a partial native batch. Resume only its
        # missing requests; previously generated files remain exact evidence.
        resumed = {'requests': missing}
        sequence_path = folder / 'requests' / (sequence_id + '-resume-' + sha(resumed) + '.json')
        write(sequence_path, resumed, immutable=True)
    command = [str(runner), '--task', 'tts', '--family', 'higgs_audio_tts', '--model', str(pin.path),
               '--backend', 'cuda', '--device', '0', '--threads', '4', '--request-sequence', str(sequence_path),
               '--out-dir', str(staging), '--out-format', 'pcm16', '--metrics']
    write(folder / 'requests' / (sequence_id + '-' + file_digest(sequence_path) + '.session.json'),
          {'command': command, 'specs': [r[3] for r in pending], 'sequence_sha256': file_digest(sequence_path)}, immutable=True)
    log_path = folder / 'requests' / (sequence_id + '.log')
    error = None
    print('RENDER', [r[0]['id'] for r in pending], flush=True)
    try:
        # On restart, reuse a fully produced native batch; never regenerate its valid files.
        if missing:
            with log_path.open('a', encoding='utf-8') as log:
                process = subprocess.Popen(command, cwd=runner.parent, stdout=log, stderr=subprocess.STDOUT, **HIDDEN)
                write(folder / 'worker.json', {'pid': process.pid, 'stage': 'render', 'started_unix': time.time(), 'log': str(log_path)})
                result = process.wait(timeout=max(900, len(pending) * 180))
                if result:
                    error = 'Native runner exit ' + str(result)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        error = 'Native runner timed out'
    finally:
        for unit, take, request, spec in pending:
            source = staging / (request['id'] + '.wav')
            receipt = dict(schema=1, unit=unit['id'], take=take, spec=spec, spec_sha256=sha(spec), quality_passed=False)
            if source.is_file():
                digest = file_digest(source)
                target = folder / 'assets' / (digest + '.wav')
                target.parent.mkdir(exist_ok=True)
                if not target.exists():
                    shutil.copyfile(source, target)
                receipt.update(audio=target.relative_to(folder).as_posix(), audio_sha256=digest)
                try:
                    receipt['quality'] = quality(target, request['max_tokens'])
                    receipt['quality_passed'] = True
                except Exception as problem:
                    receipt['quality_error'] = str(problem)
            if error:
                receipt['generation_error'] = error
            write(folder / 'takes' / unit['id'] / f'take-{take:02d}.json', receipt, immutable=True)
            checkpoint(folder, assignment, generation, args.max_takes)
        write(folder / 'worker.json', {'stage': 'render', 'status': 'stopped', 'finished_unix': time.time(),
                                      'error': error, 'log': str(log_path)})
    return len(pending)


def inventory(path):
    path = Path(path)
    if not path.is_dir():
        raise ValueError('Missing local recognizer: ' + str(path))
    return {p.relative_to(path).as_posix(): file_digest(p) for p in sorted(path.rglob('*')) if p.is_file()}


def verify(folder, assignment, manifest, generation, args, witness=False):
    pending = []
    for unit in assignment['units']:
        for receipt in takes(folder, unit):
            if not receipt.get('quality_passed'):
                continue
            path = folder / 'checks' / (receipt['audio_sha256'] + '.json')
            if witness:
                if not path.exists() or audit(folder, unit, receipt)['passed']:
                    continue
            elif path.exists():
                continue
            checked(folder / receipt['audio'], receipt['audio_sha256'])
            pending.append((unit, receipt))
    if not pending:
        return
    wait_for_audio_resources()
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    import numpy as np
    import soundfile as sf
    import torch
    import transformers
    torch.set_num_threads(4)
    language = {'Russian': 'ru', 'English': 'en'}[assignment['language']]
    config = dict(device='cuda:0', expected_text_given_to_recognizer=False, local_files_only=True,
                  implementation_sha256=file_digest(Path(__file__)), torch=torch.__version__, transformers=transformers.__version__)
    if witness:
        from huggingface_hub import snapshot_download
        from transformers import WhisperProcessor, WhisperForConditionalGeneration
        revision = '973afd24965f72e36ca33b3055d56a652f456b4d'
        model_root = Path(snapshot_download('openai/whisper-small', revision=revision, local_files_only=True,
                                          allow_patterns=['*.json', 'merges.txt', 'model.safetensors']))
        config.update(model='openai/whisper-small', revision=revision, files=inventory(model_root), language=language,
                      dtype='float16', attention='sdpa', do_sample=False, max_new_tokens=256, sample_rate=16000)
        processor = WhisperProcessor.from_pretrained(str(model_root), local_files_only=True)
        model = WhisperForConditionalGeneration.from_pretrained(str(model_root), local_files_only=True,
                torch_dtype=torch.float16, attn_implementation='sdpa').to('cuda').eval()
    else:
        from qwen_asr import Qwen3ASRModel
        from asr_batching import last_position_logits
        model_root = Path(manifest.get('asr_model_path', ROOT / 'training-work/models/Qwen3-ASR-1.7B'))
        config.update(model='Qwen3-ASR-1.7B', files=inventory(model_root), language=assignment['language'],
                      dtype='bfloat16', attention='sdpa', max_new_tokens=512,
                      batching_sha256=file_digest(Path(__file__).with_name('asr_batching.py')))
        model = last_position_logits(Qwen3ASRModel.from_pretrained(str(model_root), device_map='cuda:0', dtype=torch.bfloat16,
                  attn_implementation='sdpa', local_files_only=True, max_inference_batch_size=1, max_new_tokens=512))
    signature = sha(config)
    write(folder / 'verifiers' / (signature + '.json'), config, immutable=True)
    try:
        for unit, receipt in pending:
            target = folder / ('witnesses' if witness else 'checks') / (receipt['audio_sha256'] + ('-' + signature if witness else '') + '.json')
            if target.exists():
                continue
            audio_path = checked(folder / receipt['audio'], receipt['audio_sha256'])
            if witness:
                from scipy.signal import resample_poly
                audio, rate = sf.read(audio_path, dtype='float32', always_2d=True)
                mono = audio.mean(axis=1)
                divisor = math.gcd(rate, 16000)
                mono = resample_poly(mono, 16000 // divisor, rate // divisor).astype(np.float32)
                if len(mono) > 30 * 16000:
                    # Never silently truncate evidence; long units stay held for a short-unit repair.
                    print('WITNESS HELD over 30s', unit['id'], flush=True)
                    continue
                inputs = {k: v.to('cuda', dtype=torch.float16) if v.is_floating_point() else v.to('cuda')
                          for k, v in processor(mono, sampling_rate=16000, return_tensors='pt', return_attention_mask=True).items()}
                with torch.inference_mode():
                    tokens = model.generate(**inputs, language=language, task='transcribe', do_sample=False, max_new_tokens=256)
                heard = processor.batch_decode(tokens, skip_special_tokens=True)[0].strip()
            else:
                heard = model.transcribe(str(audio_path), language=assignment['language'])[0].text
            checked(audio_path, receipt['audio_sha256'])
            check = transcript_check(unit['spoken'], heard, receipt['audio_sha256'], signature)
            check.update(verifier=config, interpretation='Audio-only machine check; no human listening approval')
            write(target, check, immutable=True)
            print('WITNESS' if witness else 'VERIFY', unit['id'], check['status'], repr(heard), flush=True)
            checkpoint(folder, assignment, generation, args.max_takes)
    finally:
        del model
        torch.cuda.empty_cache()


def opening(assignment, manifest):
    from rimsky.opening import load_opening_pack, MANIFEST_SHA256
    brand, folder = load_opening_pack(ROOT / 'resources')
    key = {'Russian': 'ru', 'English': 'en'}[assignment['language']]
    variant = brand['languages'][key]['variants']['music']
    source = checked(folder / variant['file'], variant['audio_hash'])
    configured = manifest['opening']
    if (configured['sha256'] != variant['audio_hash'] or configured['language'] != assignment['language']
            or configured['manifest_sha256'] != MANIFEST_SHA256):
        raise ValueError('Opening is not the frozen matching-language approved musical asset')
    return source


def music_asset(spec, kind=None):
    """Only an existing hash-bound no-vocals receipt; never accepts a boolean approval."""
    path = checked(spec['path'], spec['sha256'])
    evidence_path = checked(spec['check_path'], spec['check_sha256'])
    evidence = read(evidence_path)
    if (evidence.get('audio_sha256') != spec['sha256'] or evidence.get('status') != 'no_words_detected'
            or word_tokens(evidence.get('heard', '')) or evidence.get('passed') is not True):
        raise ValueError('Music lacks exact audio-bound instrumental verification')
    if sha(evidence['verifier']) != evidence['verifier_sha256']:
        raise ValueError('Music recognizer signature differs')
    receipt_path = checked(spec['receipt_path'], spec['receipt_sha256'])
    receipt = read(receipt_path)
    if receipt.get('audio_sha256') != spec['sha256'] or receipt.get('quality_passed') is not True:
        raise ValueError('Music lacks a matching generation/quality receipt')
    if kind and receipt.get('cue', {}).get('kind') != kind:
        raise ValueError('Music cue does not match its bed/ending role')
    style = json.dumps(receipt.get('spec', {}), ensure_ascii=False).casefold()
    if 'lute' not in style or 'flute' not in style or 'piano' in style:
        raise ValueError('Music generation provenance does not match the house instrumentation')
    return path


def publish(folder, assignment, manifest, generation, args):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from rimsky.audio_join import prepare_clip, gap_seconds
    state = checkpoint(folder, assignment, generation, args.max_takes)
    prefix = []
    for unit in assignment['units']:
        choice = selected(folder, unit)
        if not choice:
            break
        prefix.append((unit, choice))
    if not prefix:
        return False
    complete_speech = len(prefix) == len(assignment['units'])
    rate = 24000
    chunks, captions, position = [], [], 8.0
    intro = opening(assignment, manifest)
    intro_audio, intro_rate = sf.read(intro, dtype='float32', always_2d=True)
    if intro_rate != rate or intro_audio.shape != (rate * 8, 2):
        raise ValueError('Approved opening format changed')
    chunks.append(intro_audio)
    previous = edge_previous = None
    for index, (unit, (receipt, decision)) in enumerate(prefix):
        audio, sample_rate = sf.read(folder / receipt['audio'], dtype='float32')
        if sample_rate != rate:
            raise ValueError('Unexpected speech sample rate')
        clip, edge = prepare_clip(audio, rate)
        descriptor = {**unit, 'sentence_index': index, 'scene': unit.get('chapter_id')}
        gap = gap_seconds(previous, descriptor, edge_previous, edge) if previous else 0
        chunks.append(np.zeros((round(gap * rate), 2), np.float32))
        position += round(gap * rate) / rate
        captions.append({'id': unit['id'], 'source_id': unit['source_id'], 'text': unit['text'],
                         'start': position, 'end': position + len(clip)/rate,
                         'raw_audio_sha256': receipt['audio_sha256'], 'recognition_status': decision['resolution']})
        chunks.append(np.repeat(clip[:, None], 2, axis=1))
        position += len(clip)/rate
        previous, edge_previous = descriptor, edge
    if position - 8 < 300 and not complete_speech:
        return False
    audio = np.concatenate(chunks)
    music = manifest.get('music')
    music_ready = bool(music and music.get('bed') and music.get('ending'))
    if music_ready:
        def asset(spec, kind):
            data, source_rate = sf.read(music_asset(spec, kind), dtype='float32', always_2d=True)
            if source_rate != rate:
                divisor = math.gcd(source_rate, rate)
                data = resample_poly(data, rate//divisor, source_rate//divisor, axis=0)
            if data.shape[1] == 1:
                data = np.repeat(data, 2, axis=1)
            if data.shape[1] != 2 or not np.isfinite(data).all():
                raise ValueError('Invalid instrumental format')
            return data
        bed, ending = asset(music['bed'], 'bed'), asset(music['ending'], 'ending')
        # Short phrases separated by quiet gaps; speech samples and offsets never change.
        bed = bed[:20*rate].copy()
        fade = min(2*rate, len(bed)//2)
        bed[:fade] *= np.linspace(0, 1, fade)[:, None]
        bed[-fade:] *= np.linspace(1, 0, fade)[:, None]
        gain = 10 ** (float(music.get('bed_rms_dbfs', -38))/20) / max(1e-9, float(np.sqrt(np.mean(bed**2))))
        for start in range(8*rate, len(audio), 40*rate):
            count = min(len(bed), len(audio)-start)
            audio[start:start+count] += bed[:count]*gain
        if complete_speech:
            gain = 10 ** (float(music.get('ending_rms_dbfs', -30))/20) / max(1e-9, float(np.sqrt(np.mean(ending**2))))
            ending = ending.copy()
            fade_in, fade_out = min(round(1.5*rate), len(ending)), min(7*rate, len(ending))
            ending[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
            ending[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
            audio = np.concatenate([audio, ending*gain])
    peak = float(np.max(np.abs(audio)))
    if not np.isfinite(audio).all() or peak > .999:
        raise ValueError('Published mix is clipped or nonfinite; adjust source levels explicitly')
    ffmpeg = manifest.get('ffmpeg', shutil.which('ffmpeg'))
    if not ffmpeg:
        raise ValueError('FFmpeg required for playable MP3 export')
    encoder = {'path': str(ffmpeg), 'sha256': file_digest(Path(ffmpeg)),
               'version': subprocess.check_output([str(ffmpeg), '-version'], text=True, timeout=30, **HIDDEN).splitlines()[0],
               'codec': 'libmp3lame', 'bitrate': '192k', 'numpy': np.__version__, 'soundfile': sf.__version__}
    identity = sha({'generation': generation, 'captions': captions, 'music': music,
                    'complete_speech': complete_speech, 'encoding': encoder})
    public = folder / 'public'
    public.mkdir(exist_ok=True)
    wav, mp3 = public / (identity + '.wav'), public / (identity + '.mp3')
    if not wav.exists():
        sf.write(wav, audio, rate, subtype='PCM_16')
    if not mp3.exists():
        subprocess.run([str(ffmpeg), '-v', 'error', '-y', '-i', str(wav), '-c:a', 'libmp3lame', '-b:a', '192k', str(mp3)],
                       check=True, timeout=180, **HIDDEN)
    # Decode the deliverable, rather than treating a successful encoder exit as
    # proof that the published file is playable.
    subprocess.run([str(ffmpeg), '-v', 'error', '-i', str(mp3), '-f', 'null', '-'],
                   check=True, timeout=180, **HIDDEN)
    status = {'schema': 1, 'generation': generation, 'version': identity, 'complete': complete_speech and music_ready,
              'title': assignment.get('title', ''), 'language': assignment['language'],
              'verified_units': len(prefix), 'total_units': len(assignment['units']),
              'open_recognition_flags': state['counts']['held'] + state['counts']['retry_pending'],
              'music_ready': music_ready, 'opening_seconds': 8, 'speech_end': position,
              'wav': wav.name, 'wav_sha256': file_digest(wav), 'mp3': mp3.name, 'mp3_sha256': file_digest(mp3),
              'captions': captions, 'limitation': 'Machine-verified audio. No human listening approval recorded.',
              'attribution': 'Music generated with YuE2-3B, CC BY-NC 4.0.'}
    write(public / (identity + '.json'), status, immutable=True)
    write(public / 'latest.json', status)
    title = html.escape(assignment.get('title', 'Dogma demo'))
    rows = ''.join('<p data-start="%s" data-end="%s">%s</p>' % (c['start'], c['end'], html.escape(c['text'])) for c in captions)
    player_name = 'player-' + file_digest(PLAYER_SOURCE) + '.js'
    shutil.copyfile(PLAYER_SOURCE, public / player_name)
    player_config = json.dumps({'generation': generation, 'version': identity, 'captions': captions,
                                'opening_seconds': 8, 'opening_sha256': manifest['opening']['sha256']},
                               ensure_ascii=False).replace('<', '\\u003c')
    page = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+title+'</title><style>body{max-width:800px;margin:3em auto;padding:1em;font:18px/1.6 system-ui;background:#151719;color:#eee}audio{width:100%}a{color:#aee}p.active{background:#303b48}p[data-start]{cursor:pointer}</style><h1>'+title+'</h1><p>'+html.escape(status['limitation'])+'</p><p>'+('Complete' if status['complete'] else 'Verified speech preview; remaining audio or music pending')+'</p><audio id="player" controls src="'+mp3.name+'"></audio><p><a href="'+mp3.name+'" download>Download MP3</a></p>'+rows+'<footer>'+status['attribution']+'</footer><script type="application/json" id="playback-config">'+player_config+'</script><script src="'+player_name+'"></script>'
    (public / 'index.html').write_text(page, 'utf-8')
    if status['complete']:
        write(folder / 'complete.json', status, immutable=True)
    return status['complete']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--stage', choices=['render', 'verify', 'witness', 'publish', 'all'], default='all')
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--max-takes', type=int, default=3)
    parser.add_argument('--unit', action='append')
    parser.add_argument('--asr-python', type=Path, default=ROOT / 'training-work/.venv-asr/Scripts/python.exe')
    args = parser.parse_args()
    if args.batch_size < 1 or not 1 <= args.max_takes <= 3:
        parser.error('Batch size must be positive; bounded takes must be 1..3')
    folder = args.folder.resolve()
    assignment, manifest, generation = load(folder)
    if args.stage == 'all':
        while True:
            before = sha(checkpoint(folder, assignment, generation, args.max_takes)['units'])
            render(folder, assignment, manifest, generation, args)
            for stage in ('verify', 'witness'):
                command = [str(args.asr_python), str(Path(__file__)), '--folder', str(folder), '--stage', stage,
                           '--max-takes', str(args.max_takes)]
                subprocess.run(command, cwd=ROOT, check=True, **HIDDEN)
            if publish(folder, assignment, manifest, generation, args):
                break
            after = sha(checkpoint(folder, assignment, generation, args.max_takes)['units'])
            if before == after:
                break
    elif args.stage == 'render':
        render(folder, assignment, manifest, generation, args)
    elif args.stage in ('verify', 'witness'):
        verify(folder, assignment, manifest, generation, args, args.stage == 'witness')
    else:
        publish(folder, assignment, manifest, generation, args)
    checkpoint(folder, assignment, generation, args.max_takes)


if __name__ == '__main__':
    main()
