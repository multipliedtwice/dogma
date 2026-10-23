"""Isolated, resumable Dogma audio ident trial; never writes book/corpus data.

Run prepare/speech/music with the TTS environment, verify with .venv-asr,
then mix. Repeat speech/music/verify for rejected takes (maximum three).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from rimsky.production_higgs_model import resolve_production_higgs_model
from rimsky.speech_quality import transcript_check, word_tokens
from rimsky.util import file_digest

DEFAULT = ROOT / 'training-work/dogma-intros/20261022-v1'
RUNNER = ROOT / 'training-work/higgs-tts-3-pilot/audio-cpp-cuda/audiocpp_cli.exe'
PACK = ROOT / 'resources/music/yue2-3b-q8'
MAX_TAKES = 3
STYLES = [
    ('Lantern', 'intimate modal folk, warm plucked strings, sparse breathy flute, gentle pulse'),
    ('Citadel', 'solemn medieval processional, low strings, distant horn, measured hand drum'),
    ('Grove', 'pastoral woodland folk, bright harp, lilting wooden flute, soft shaker'),
    ('Ember', 'dark fireside ambient folk, low bowed strings, sparse lute, muted frame drum'),
    ('Starlight', 'luminous nocturnal folk, delicate dulcimer, airy wooden flute, slow spacious pulse'),
    ('Voyager', 'open horizon medieval folk, warm acoustic guitar, steady soft hand percussion'),
    ('Archive', 'contemplative chamber folk, gentle viola, intricate lute, unhurried phrasing'),
    ('Mist', 'mysterious modal ambient folk, low wooden flute, bowed psaltery, restrained percussion'),
    ('Solstice', 'hopeful medieval folk, bright mandolin, dancing flute, light hand drum'),
    ('Threshold', 'cinematic restrained medieval folk, warm cello, rising lute motif, soft heartbeat drum'),
]
HOUSE = ('instrumental only, no singing, no vocals, quiet ambient medieval folk, '
         'lute, wooden flute, soft hand percussion, ')
DESCRIPTIONS = [
    'Warm lute and breathy flute, intimate and gentle.',
    'A solemn procession with low strings and distant horn.',
    'Bright harp and lilting flute in a woodland mood.',
    'Low bowed strings and muted drum beside the fire.',
    'Delicate dulcimer and airy flute beneath the stars.',
    'Acoustic guitar and a steady pulse for an open horizon.',
    'Gentle viola and intricate lute, quiet and thoughtful.',
    'Low flute and bowed psaltery, mysterious and spacious.',
    'Bright mandolin and dancing flute, light and hopeful.',
    'Warm cello and a rising lute motif, a restrained sense of arrival.',
]


def read(path):
    return json.loads(Path(path).read_text('utf-8'))


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def write(path, value, immutable=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        if read(path) != value:
            raise ValueError('Immutable record differs: ' + str(path))
        return
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def quality(path, kind):
    import numpy as np
    import soundfile as sf
    data, rate = sf.read(path, dtype='float32', always_2d=True)
    seconds = len(data) / rate
    if not len(data) or not np.isfinite(data).all():
        raise ValueError('Empty or non-finite audio')
    checks = {'sample_rate': rate, 'channels': data.shape[1], 'duration': seconds,
              'peak': float(np.abs(data).max()), 'rms': float(np.sqrt(np.mean(data ** 2))),
              'clipped_fraction': float(np.mean(np.abs(data) >= .999)),
              'audio_sha256': file_digest(path)}
    if checks['rms'] < .0001 or checks['clipped_fraction'] > .001:
        raise ValueError('Silent or clipped audio: ' + json.dumps(checks))
    if kind == 'speech' and (rate != 24000 or data.shape[1] != 1 or not .35 <= seconds < 8):
        raise ValueError('Invalid speech rate/channels/duration: ' + json.dumps(checks))
    if kind == 'music':
        from rimsky.music import audio_checks
        checks.update(audio_checks(data, rate, 8))
    if kind == 'mix' and (rate != 24000 or data.shape[1] != 2 or abs(seconds - 8) > 1 / rate):
        raise ValueError('Invalid final ident format/duration: ' + json.dumps(checks))
    return checks


def prepare(output):
    from rimsky.music import cue_spec, load_music_pack
    pin = resolve_production_higgs_model(ROOT)
    plan_path = ROOT / 'training-work/higgs-production/listening/voice-lora-v1/plan.json'
    case = next(c for c in read(plan_path)['cases'] if c['voice'] == 'narrator_baseline')
    reference = case['reference']
    reference_path = ROOT / 'training-work/higgs-production/datasets/reviewed-20261012-v1' / reference['audio']
    if reference.get('reviewed') is not True or reference.get('mood') != 'calm':
        raise ValueError('Narrator reference is not reviewed Calm')
    if file_digest(reference_path) != reference['audio_sha256']:
        raise ValueError('Narrator reference checksum mismatch')
    pack = load_music_pack(PACK, {})
    units = []
    for i, (name, style) in enumerate(STYLES):
        language = 'Russian' if i < 5 else 'English'
        display = '\u041e\u0437\u0432\u0443\u0447\u0435\u043d\u043e \u0441 \u043f\u043e\u043c\u043e\u0449\u044c\u044e Dogma.' if i < 5 else 'Narrated with Dogma.'
        spoken = '\u041e\u0437\u0432\u0443\u0447\u0435\u043d\u043e \u0441 \u043f\u043e\u043c\u043e\u0449\u044c\u044e \u0414\u043e\u0301\u0433\u043c\u0430.' if i < 5 else display
        units.append({'id': f'{i+1:02d}-' + ('ru' if i < 5 else 'en'), 'number': i+1,
                      'name': name, 'language': language, 'display': display, 'spoken': spoken,
                      'seed': 202610220 + i * 100,
                      'music': cue_spec(pack, {'kind': 'bed', 'seed': 102610220 + i * 100,
                                              'style': HOUSE + style, 'seconds': 8}, 'cuda')})
    manifest = {'schema': 1, 'purpose': 'Dogma ident listening trial', 'units': units,
                'effects_policy': 'disabled', 'max_takes': MAX_TAKES,
                'speech_model': {'checkpoint': pin.checkpoint, 'path': str(pin.path),
                                 'sha256': pin.sha256, 'precision': pin.precision,
                                 'manifest_sha256': file_digest(pin.manifest_path),
                                 'provenance_sha256': file_digest(pin.provenance_path),
                                 'provenance': pin.provenance},
                'runner': {'path': str(RUNNER), 'sha256': file_digest(RUNNER),
                           'dlls': {p.name: file_digest(p) for p in sorted(RUNNER.parent.glob('*.dll'))},
                           'version': subprocess.check_output([str(RUNNER), '--version'], cwd=RUNNER.parent,
                                                              timeout=30, text=True).strip(),
                           'backend': 'cuda', 'device': 0, 'threads': 4},
                'reference': {**reference, 'path': str(reference_path.resolve()),
                              'selection_plan_sha256': file_digest(plan_path)},
                'music_pack': {'fingerprint': pack['_fingerprint'], 'files': pack['files'],
                               'manifest_sha256': file_digest(PACK / 'music-pack.json'),
                               'license': pack['license']},
                'mix': {'seconds': 8, 'entry_seconds': 1.5, 'music_rms_dbfs': -23,
                        'duck_db': -12, 'speech_rms_dbfs': -19, 'peak_max': .95,
                        'music_fade_in_seconds': .3, 'music_fade_out_seconds': 1.2,
                        'duck_attack_seconds': .15, 'duck_release_seconds': .45,
                        'speech_processing': 'constant gain only; no trim, fades or time stretch'}}
    write(output / 'spec.json', manifest, immutable=True)
    publish(output)


def receipts(output, unit, kind):
    folder = output / 'takes' / unit['id'] / kind
    return [read(p) for p in sorted(folder.glob('take-*.json'))]


def audit(output, receipt):
    path = output / 'checks' / (receipt['audio_sha256'] + '.json') if receipt.get('audio_sha256') else None
    if not path or not path.exists():
        return None
    primary = read(path)
    if primary.get('audio_sha256') != receipt['audio_sha256']:
        raise ValueError('Primary recognition is not bound to this audio')
    sidecar = path.with_suffix('.resolution.json')
    if not sidecar.exists():
        return primary
    resolution = read(sidecar)
    witness_path = (output / resolution['witness_file']).resolve()
    witness_path.relative_to((output / 'witnesses').resolve())
    if (resolution['primary_check_sha256'] != file_digest(path)
            or resolution['witness_file_sha256'] != file_digest(witness_path)
            or resolution['audio_sha256'] != receipt['audio_sha256']):
        raise ValueError('Recognition resolution evidence changed')
    witness_check = read(witness_path)
    if receipt.get('kind') != 'speech':
        raise ValueError('Speech witness resolution cannot approve music')
    expected = receipt['spec']['request']['text']
    recomputed = speech_resolution(expected, primary, witness_check)
    if not recomputed or resolution['decision'] != recomputed:
        raise ValueError('Recognition resolution does not satisfy the permitted rule')
    return {**primary, **recomputed, 'primary_check': primary, 'witness_check': witness_check,
            'resolution': resolution, 'interpretation': 'Machine checks with disclosed independent evidence; no human listening approval'}


def speech_resolution(source, primary, witness_check):
    """Only an exact independent witness or the existing guarded brand-name rule."""
    expected = word_tokens(source)
    for check in (primary, witness_check):
        if 'verifier' in check and sha(check['verifier']) != check['verifier_sha256']:
            raise ValueError('Recognizer verifier configuration hash differs')
        recomputed = transcript_check(source, check['heard'], check['audio_sha256'], check['verifier_sha256'])
        if (check['expected_words'] != expected or check['heard_words'] != recomputed['heard_words']
                or check['expected_text_sha256'] != recomputed['expected_text_sha256']
                or check['status'] != recomputed['status']):
            raise ValueError('Recognizer transcript/check binding differs')
    if primary['audio_sha256'] != witness_check['audio_sha256']:
        raise ValueError('Independent witness heard a different take')
    if witness_check['heard_words'] == expected:
        return {'passed': True, 'status': 'independent_witness_exact_match',
                'method': 'independent_audio_only_witness', 'tolerated_deviations': []}
    from rimsky.spoken_homophones import homophone_equivalence, synthesis_stress, phrase, dependencies
    stress = synthesis_stress(source)
    normalized = {}
    expansions = []
    phonology = {}
    for recognizer, check in [('primary', primary), ('witness', witness_check)]:
        normalized[recognizer], changes = homophone_equivalence(expected, check['heard_words'], stress=stress)
        for change in changes:
            expansions.append({**change, 'recognizer': recognizer,
                               'source_phrase': phrase(' '.join(change['source_tokens'])),
                               'heard_phrase': phrase(' '.join(change['heard_tokens']))})
        if changes:
            phonology[recognizer] = {'source_phrase': phrase(source), 'heard_phrase': phrase(check['heard']),
                                     'synthesis_stress': stress}
    orthography = {}
    if expansions:
        orthography = {'orthographic_expansions': expansions, 'orthographic_evidence': phonology,
                       'orthographic_provenance': {
                           'implementation_sha256': file_digest(ROOT / 'runtime/rimsky/spoken_homophones.py'),
                           'dependencies': dependencies()}}
        # JSON round-trip makes immutable comparisons stable for tuple phonemes.
        orthography = json.loads(json.dumps(orthography))
    if normalized['witness'] == expected:
        return {'passed': True, 'status': 'independent_witness_spoken_equivalence',
                'method': 'independent_audio_only_witness_with_source_bound_phonology',
                'tolerated_deviations': [], **orthography}
    from rimsky.name_tolerance import tolerance, proper_nouns, RULE, VOWELS
    deviations = []
    names = proper_nouns(source)
    source_words = re.findall(r'[^\W\d_][\w\u0301]*', source)
    # The selected product name may occur anywhere; no other noun is waived.
    brand_slots = {i: word for i, word in enumerate(source_words)
                   if word_tokens(word) == ['\u0434\u043e\u0433\u043c\u0430'] and '\u0434\u043e\u0433\u043c\u0430' in names}
    for recognizer, check in [('primary', primary), ('witness', witness_check)]:
        heard = normalized[recognizer]
        corrected, changes = tolerance(expected, heard, source)
        if corrected != expected or any(change['rule'] != RULE or change.get('source_index') not in brand_slots
                                        or change['source_tokens'] != [expected[change['source_index']]] for change in changes):
            return None
        if heard != expected and not changes:
            return None
        for change in changes:
            source_syllables = sum(ch in VOWELS for word in change['source_tokens'] for ch in word.casefold())
            heard_syllables = sum(ch in VOWELS for word in change['heard_tokens'] for ch in word.casefold())
            deviations.append({**change, 'recognizer': recognizer, 'source_brand': brand_slots[change['source_index']],
                               'heard_slot': list(change['heard_tokens']),
                               'guards': {'nonempty_heard_slot': bool(change['heard_tokens']),
                                          'source_syllables': source_syllables, 'heard_syllables': heard_syllables,
                                          'within_one_syllable': abs(source_syllables - heard_syllables) <= 1,
                                          'all_other_words_match': True}})
    if not deviations:
        return None
    return {'passed': True, 'status': 'asr_tolerated_brand_slot', 'method': RULE,
            'tolerated_deviations': deviations, **orthography}


def selected(output, unit, kind):
    for receipt in reversed(receipts(output, unit, kind)):
        check = audit(output, receipt)
        if check and check.get('passed') and receipt.get('quality_passed'):
            path = output / receipt['audio']
            if not path.is_file() or file_digest(path) != receipt['audio_sha256']:
                raise ValueError('Checked audio changed: ' + str(path))
            return receipt
    return None


def needs_take(output, unit, kind):
    takes = receipts(output, unit, kind)
    if selected(output, unit, kind) or len(takes) >= MAX_TAKES:
        return False
    return not takes or takes[-1].get('quality_passed') is False or audit(output, takes[-1]) is not None


def store_take(output, unit, kind, take, source, spec, error=None):
    record = {'schema': 1, 'unit': unit['id'], 'kind': kind, 'take': take,
              'spec': spec, 'spec_sha256': sha(spec), 'quality_passed': False}
    if source.is_file():
        digest = file_digest(source)
        target = output / 'assets' / (digest + '.wav')
        target.parent.mkdir(exist_ok=True)
        if not target.exists():
            shutil.copyfile(source, target)
        record.update(audio=target.relative_to(output).as_posix(), audio_sha256=digest)
        try:
            record['quality'] = quality(target, kind)
            record['quality_passed'] = True
        except Exception as problem:
            record['quality_error'] = str(problem)
    if error:
        record['generation_error'] = error
    if not record.get('audio'):
        record['quality_error'] = 'No generated audio'
    write(output / 'takes' / unit['id'] / kind / f'take-{take:02d}.json', record, immutable=True)
    publish(output)


def speech(output):
    manifest = read(output / 'spec.json')
    pin = resolve_production_higgs_model(ROOT)  # Full deployed checksum before every synthesis session.
    if pin.sha256 != manifest['speech_model']['sha256'] or file_digest(pin.provenance_path) != manifest['speech_model']['provenance_sha256']:
        raise ValueError('Frozen production model provenance changed')
    if file_digest(RUNNER) != manifest['runner']['sha256']:
        raise ValueError('Frozen speech runner changed')
    if {p.name: file_digest(p) for p in sorted(RUNNER.parent.glob('*.dll'))} != manifest['runner']['dlls']:
        raise ValueError('Frozen speech runner DLLs changed')
    ref = manifest['reference']
    if file_digest(Path(ref['path'])) != ref['audio_sha256'] or ref['reviewed'] is not True:
        raise ValueError('Frozen reviewed reference changed')
    pending = []
    for unit in manifest['units']:
        if needs_take(output, unit, 'speech'):
            take = len(receipts(output, unit, 'speech'))
            request = {'id': unit['id'] + f'-t{take}', 'text': unit['spoken'],
                       'voice_ref': ref['path'], 'reference_text': ref['text'],
                       'temperature': .8, 'top_k': 50, 'top_p': .95, 'repetition_penalty': 1.1,
                       'do_sample': True, 'seed': unit['seed'] + take, 'max_tokens': 256,
                       'text_chunk_mode': 'tag_aware', 'text_chunk_size': 65536}
            pending.append((unit, take, request))
    if not pending:
        return
    sequence = {'requests': [row[2] for row in pending]}
    sequence_id = sha(sequence)
    sequence_path = output / 'requests' / (sequence_id + '.json')
    write(sequence_path, sequence, immutable=True)
    staging = output / 'native' / sequence_id[:12]
    staging.mkdir(parents=True, exist_ok=True)
    command = [str(RUNNER), '--task', 'tts', '--family', 'higgs_audio_tts', '--model', str(pin.path),
               '--backend', 'cuda', '--device', '0', '--threads', '4', '--request-sequence', str(sequence_path),
               '--out-dir', str(staging), '--out-format', 'pcm16', '--metrics']
    write(output / 'requests' / (sequence_id + '.session.json'),
          {'command': command, 'sequence_sha256': file_digest(sequence_path),
           'model_sha256': pin.sha256, 'provenance_sha256': file_digest(pin.provenance_path)}, immutable=True)
    error = None
    log_path = output / 'requests' / (sequence_id + '.log')
    try:
        with log_path.open('w', encoding='utf-8') as log:
            result = subprocess.run(command, cwd=RUNNER.parent, stdout=log, stderr=subprocess.STDOUT, timeout=900)
        if result.returncode:
            error = 'Speech runner exited ' + str(result.returncode)
    except subprocess.TimeoutExpired:
        error = 'Speech runner exceeded 900 seconds'
    for unit, take, request in pending:
        store_take(output, unit, 'speech', take, staging / (request['id'] + '.wav'),
                   {'request': request, 'session': sequence_id, 'model': manifest['speech_model'],
                    'reference': ref, 'runner': manifest['runner']}, error)
    if error:
        raise RuntimeError(error + '; see ' + str(log_path))


def music(output, device='cuda'):
    from rimsky.music import generate, load_music_pack
    manifest = read(output / 'spec.json')
    pack = load_music_pack(PACK, {})
    if pack['_fingerprint'] != manifest['music_pack']['fingerprint']:
        raise ValueError('Frozen music pack changed')
    for unit in manifest['units']:
        if not needs_take(output, unit, 'music'):
            continue
        take = len(receipts(output, unit, 'music'))
        spec = {**unit['music'], 'device': device}
        frozen = {'cue_spec': spec, 'take': take,
                  'effective_seed': (spec['request']['seed'] + take) % 2**31,
                  'pack': manifest['music_pack'], 'generation': pack['generation'],
                  'runner_version': pack['runner_version'],
                  'threads': str(max(1, min(12, (os.cpu_count() or 4) - 2)))}
        source = output / 'native' / ('music-' + sha(frozen)[:12] + '.wav')
        source.parent.mkdir(exist_ok=True)
        write(output / 'requests' / (sha(frozen) + '.json'), frozen, immutable=True)
        error = None
        print('MUSIC', unit['id'], unit['name'], 'take', take + 1, device, flush=True)
        try:
            generate(pack, spec, take, source, lambda: False, timeout=600)
        except Exception as problem:
            error = str(problem)
        store_take(output, unit, 'music', take, source, frozen, error)
        print('MUSIC DONE', unit['id'], 'quality', receipts(output, unit, 'music')[-1]['quality_passed'], flush=True)


def verify(output):
    """Recognizer receives only audio and locale, never the expected sentence."""
    manifest = read(output / 'spec.json')
    pending = []
    for unit in manifest['units']:
        for kind in ('speech', 'music'):
            for receipt in receipts(output, unit, kind):
                if receipt.get('quality_passed') and audit(output, receipt) is None:
                    path = output / receipt['audio']
                    if file_digest(path) != receipt['audio_sha256']:
                        raise ValueError('Raw take changed before verification')
                    pending.append((unit, kind, receipt))
    if not pending:
        return
    # Validate the exact installed local verifier files against the pack manifest.
    from rimsky.music import load_music_pack
    pack = load_music_pack(PACK, {})
    if pack['_fingerprint'] != manifest['music_pack']['fingerprint']:
        raise ValueError('Verifier pack changed')
    prefix = pack['verifier']['path'].rstrip('/') + '/'
    files = {k: v for k, v in pack['files'].items() if k.startswith(prefix)}
    config = {'model': 'Qwen3-ASR-1.7B', 'files': files, 'device': 'cuda:0',
              'dtype': 'bfloat16', 'attention': 'sdpa', 'local_files_only': True,
              'max_new_tokens': 256, 'expected_text_given_to_recognizer': False,
              'implementation_sha256': file_digest(Path(__file__)),
              'batching_sha256': file_digest(Path(__file__).with_name('asr_batching.py'))}
    signature = sha(config)
    write(output / 'verifiers' / (signature + '.json'), config, immutable=True)
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import torch
    from qwen_asr import Qwen3ASRModel
    from asr_batching import last_position_logits
    torch.set_num_threads(4)
    model = last_position_logits(Qwen3ASRModel.from_pretrained(
        str(PACK / pack['verifier']['path']), device_map='cuda:0', dtype=torch.bfloat16,
        attn_implementation='sdpa', local_files_only=True,
        max_inference_batch_size=2, max_new_tokens=256))
    try:
        for kind, language in [('speech', 'Russian'), ('speech', 'English'), ('music', None)]:
            group = [r for r in pending if r[1] == kind and (kind == 'music' or r[0]['language'] == language)]
            for start in range(0, len(group), 2):
                batch = group[start:start+2]
                if not batch:
                    continue
                results = model.transcribe([str(output / r[2]['audio']) for r in batch], language=language)
                if len(results) != len(batch):
                    raise ValueError('Recognizer result count differs from audio count')
                for (unit, _, receipt), result in zip(batch, results):
                    heard = result.text
                    if kind == 'speech':
                        check = transcript_check(unit['spoken'], heard, receipt['audio_sha256'], signature)
                        passed = check['status'] == 'asr_exact_match'
                    else:
                        check = {'status': 'no_words_detected' if not word_tokens(heard) else 'vocals_detected',
                                 'heard': heard, 'heard_words': word_tokens(heard),
                                 'audio_sha256': receipt['audio_sha256'], 'verifier_sha256': signature}
                        passed = not word_tokens(heard)
                    check.update(passed=passed, kind=kind, language=language,
                                 verifier=config, interpretation='Machine check; no human listening approval')
                    write(output / 'checks' / (receipt['audio_sha256'] + '.json'), check, immutable=True)
                    print('VERIFY', unit['id'], kind, check['status'], repr(heard), flush=True)
                    publish(output)
    finally:
        del model
        torch.cuda.empty_cache()


def witness(output):
    """Second audio-only recognizer for Russian speech; never modifies primary checks."""
    manifest = read(output / 'spec.json')
    pending = []
    for unit in manifest['units']:
        if unit['language'] != 'Russian':
            continue
        for receipt in receipts(output, unit, 'speech'):
            primary_path = output / 'checks' / (receipt.get('audio_sha256', '') + '.json')
            if not receipt.get('quality_passed') or not primary_path.exists():
                continue
            if audit(output, receipt).get('passed'):
                continue
            if file_digest(output / receipt['audio']) != receipt['audio_sha256']:
                raise ValueError('Speech changed before independent witness')
            pending.append((unit, receipt, primary_path))
    if not pending:
        return
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import math
    import numpy as np
    import soundfile as sf
    import torch
    import transformers
    from scipy.signal import resample_poly
    from huggingface_hub import snapshot_download
    from transformers import WhisperProcessor, WhisperForConditionalGeneration
    from rimsky.spoken_homophones import dependencies as phonology_dependencies
    model_name = 'openai/whisper-small'
    revision = '973afd24965f72e36ca33b3055d56a652f456b4d'
    model_root = Path(snapshot_download(model_name, revision=revision, local_files_only=True,
                                      allow_patterns=['*.json', 'merges.txt', 'model.safetensors']))
    config = {'model': model_name, 'revision': revision, 'path': str(model_root),
              'files': {p.relative_to(model_root).as_posix(): file_digest(p) for p in sorted(model_root.rglob('*')) if p.is_file()},
              'device': 'cuda', 'dtype': 'float16', 'attention': 'sdpa', 'language': 'ru',
              'task': 'transcribe', 'do_sample': False, 'max_new_tokens': 256,
              'local_files_only': True, 'sample_rate': 16000, 'resampling': 'scipy.signal.resample_poly',
              'expected_text_given_to_recognizer': False, 'transformers': transformers.__version__,
              'torch': torch.__version__, 'implementation_sha256': file_digest(Path(__file__)),
              'comparison_sha256': file_digest(ROOT / 'runtime/rimsky/speech_quality.py'),
              'tolerance_sha256': file_digest(ROOT / 'runtime/rimsky/name_tolerance.py'),
              'spoken_homophones_sha256': file_digest(ROOT / 'runtime/rimsky/spoken_homophones.py'),
              'phonology_dependencies': phonology_dependencies()}
    signature = sha(config)
    write(output / 'verifiers' / (signature + '.json'), config, immutable=True)
    torch.set_num_threads(4)
    model = processor = None
    try:
        for unit, receipt, primary_path in pending:
            key = sha({'audio_sha256': receipt['audio_sha256'], 'verifier_sha256': signature,
                       'expected_text': unit['spoken']})
            witness_path = output / 'witnesses' / (key + '.json')
            if not witness_path.exists():
                if model is None:
                    processor = WhisperProcessor.from_pretrained(str(model_root), local_files_only=True)
                    model = WhisperForConditionalGeneration.from_pretrained(
                        str(model_root), local_files_only=True, torch_dtype=torch.float16,
                        attn_implementation='sdpa').to('cuda').eval()
                data, rate = sf.read(output / receipt['audio'], dtype='float32', always_2d=True)
                mono = data.mean(axis=1)
                if rate != 16000:
                    divisor = math.gcd(rate, 16000)
                    mono = resample_poly(mono, 16000 // divisor, rate // divisor).astype(np.float32)
                if not len(mono) or len(mono) > 30 * 16000:
                    raise ValueError('Independent witness requires untruncated short speech')
                inputs = {k: (v.to('cuda', dtype=torch.float16) if v.is_floating_point() else v.to('cuda'))
                          for k, v in processor(mono, sampling_rate=16000, return_tensors='pt', return_attention_mask=True).items()}
                with torch.inference_mode():
                    tokens = model.generate(**inputs, language='ru', task='transcribe', do_sample=False, max_new_tokens=256)
                heard = processor.batch_decode(tokens, skip_special_tokens=True)[0].strip()
                if file_digest(output / receipt['audio']) != receipt['audio_sha256']:
                    raise ValueError('Speech changed during independent witness')
                check = transcript_check(unit['spoken'], heard, receipt['audio_sha256'], signature)
                check.update(verifier=config, kind='speech', language='ru',
                             interpretation='Independent audio-only machine witness; no human listening approval')
                write(witness_path, check, immutable=True)
            check = read(witness_path)
            decision = speech_resolution(unit['spoken'], read(primary_path), check)
            if decision:
                resolution = {'schema': 1, 'audio_sha256': receipt['audio_sha256'],
                              'primary_check_sha256': file_digest(primary_path),
                              'witness_file': witness_path.relative_to(output).as_posix(),
                              'witness_file_sha256': file_digest(witness_path), 'decision': decision}
                write(primary_path.with_suffix('.resolution.json'), resolution, immutable=True)
            print('WITNESS', unit['id'], decision['status'] if decision else 'unresolved', repr(check['heard']), flush=True)
            publish(output)
    finally:
        del model
        torch.cuda.empty_cache()


def resolve_witnesses(output):
    """Reconcile existing raw witnesses only; no model loading or GPU work."""
    manifest = read(output / 'spec.json')
    candidates = [(path, read(path)) for path in sorted((output / 'witnesses').glob('*.json'))]
    for unit in manifest['units']:
        if unit['language'] != 'Russian':
            continue
        for receipt in receipts(output, unit, 'speech'):
            if not receipt.get('quality_passed'):
                continue
            primary_path = output / 'checks' / (receipt['audio_sha256'] + '.json')
            if not primary_path.exists() or audit(output, receipt)['passed']:
                continue
            if file_digest(output / receipt['audio']) != receipt['audio_sha256']:
                raise ValueError('Speech changed before source-bound resolution')
            for witness_path, check in candidates:
                if check.get('audio_sha256') != receipt['audio_sha256']:
                    continue
                config = check.get('verifier', {})
                if (config.get('model') != 'openai/whisper-small'
                        or config.get('revision') != '973afd24965f72e36ca33b3055d56a652f456b4d'
                        or config.get('expected_text_given_to_recognizer') is not False
                        or sha(config) != check['verifier_sha256']):
                    raise ValueError('Existing witness has invalid pinned audio-only verifier provenance')
                decision = speech_resolution(unit['spoken'], read(primary_path), check)
                if not decision:
                    continue
                resolution = {'schema': 1, 'audio_sha256': receipt['audio_sha256'],
                              'primary_check_sha256': file_digest(primary_path),
                              'witness_file': witness_path.relative_to(output).as_posix(),
                              'witness_file_sha256': file_digest(witness_path), 'decision': decision}
                write(primary_path.with_suffix('.resolution.json'), resolution, immutable=True)
                # Exercise all linked evidence and the current rule before publishing.
                audit(output, receipt)
                print('RESOLVE', unit['id'], decision['status'], repr(check['heard']), flush=True)
                break
    publish(output)


def mix(output):
    import numpy as np
    import soundfile as sf
    import scipy
    from scipy.signal import resample_poly
    from math import gcd
    manifest = read(output / 'spec.json')
    policy = manifest['mix']
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg is required to encode MP3')
    encoder = {'path': ffmpeg, 'sha256': file_digest(Path(ffmpeg)),
               'version': subprocess.check_output([ffmpeg, '-version'], text=True, timeout=30).splitlines()[0]}
    for unit in manifest['units']:
        voice, bed = selected(output, unit, 'speech'), selected(output, unit, 'music')
        final_receipt = output / 'final' / (unit['id'] + '.json')
        if not voice or not bed:
            continue
        mix_spec = {'speech_sha256': voice['audio_sha256'], 'music_sha256': bed['audio_sha256'],
                    'policy': policy, 'implementation_sha256': file_digest(Path(__file__)),
                    'sample_rate': 24000, 'music_resampling': 'scipy.signal.resample_poly',
                    'encoding': {'wav': 'PCM_16 stereo', 'mp3': 'libmp3lame 192k', 'encoder': encoder},
                    'libraries': {'numpy': np.__version__, 'scipy': scipy.__version__,
                                  'soundfile': sf.__version__, 'libsndfile': sf.__libsndfile_version__}}
        if final_receipt.exists():
            old = read(final_receipt)
            if old['mix_spec']['speech_sha256'] != voice['audio_sha256'] or old['mix_spec']['music_sha256'] != bed['audio_sha256']:
                raise ValueError('Published mix inputs changed; use another output version')
            for ext in ('wav', 'mp3'):
                if file_digest(output / old[ext]) != old[ext + '_sha256']:
                    raise ValueError('Published ' + ext + ' changed')
            continue
        speech_data, rate = sf.read(output / voice['audio'], dtype='float32', always_2d=True)
        music_data, music_rate = sf.read(output / bed['audio'], dtype='float32', always_2d=True)
        if rate != 24000:
            raise ValueError('Speech sample rate changed')
        if music_rate != rate:
            divisor = gcd(rate, music_rate)
            music_data = resample_poly(music_data, rate // divisor, music_rate // divisor, axis=0)
        count = round(policy['seconds'] * rate)
        entry = round(policy['entry_seconds'] * rate)
        if entry + len(speech_data) > count - int(.25 * rate):
            raise ValueError('Speech does not fit eight-second ident without truncation: ' + unit['id'])
        music_data = music_data[:count]
        if len(music_data) < count:
            music_data = np.pad(music_data, ((0, count-len(music_data)), (0, 0)))
        music_gain = 10 ** (policy['music_rms_dbfs'] / 20) / max(float(np.sqrt(np.mean(music_data ** 2))), 1e-9)
        speech_gain = 10 ** (policy['speech_rms_dbfs'] / 20) / max(float(np.sqrt(np.mean(speech_data ** 2))), 1e-9)
        speech_gain = min(speech_gain, .8 / max(float(np.abs(speech_data).max()), 1e-9))
        timeline = np.arange(count) / rate
        fade = np.minimum(np.clip(timeline / policy['music_fade_in_seconds'], 0, 1),
                          np.clip((policy['seconds'] - timeline) / policy['music_fade_out_seconds'], 0, 1))
        speech_end = policy['entry_seconds'] + len(speech_data) / rate
        duck_amount = np.minimum(np.clip((timeline - policy['entry_seconds'] + policy['duck_attack_seconds']) / policy['duck_attack_seconds'], 0, 1),
                                 np.clip((speech_end + policy['duck_release_seconds'] - timeline) / policy['duck_release_seconds'], 0, 1))
        envelope = fade * (1 - duck_amount * (1 - 10 ** (policy['duck_db'] / 20)))
        data = music_data * (music_gain * envelope[:, None])
        data[entry:entry + len(speech_data)] += speech_data * speech_gain
        peak = float(np.abs(data).max())
        master_gain = min(1., policy['peak_max'] / max(peak, 1e-9))
        data *= master_gain
        staging = output / 'native' / ('mix-' + unit['id'] + '.wav')
        sf.write(staging, data, rate, subtype='PCM_16')
        digest = file_digest(staging)
        wav = output / 'assets' / (digest + '.wav')
        if not wav.exists():
            shutil.copyfile(staging, wav)
        temporary_mp3 = staging.with_suffix('.mp3')
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(wav),
                        '-c:a', 'libmp3lame', '-b:a', '192k', str(temporary_mp3)], check=True, timeout=60)
        mp3_digest = file_digest(temporary_mp3)
        mp3 = output / 'assets' / (mp3_digest + '.mp3')
        if not mp3.exists():
            shutil.copyfile(temporary_mp3, mp3)
        record = {'unit': unit['id'], 'mix_spec': mix_spec, 'mix_spec_sha256': sha(mix_spec),
                  'speech_gain': speech_gain, 'music_gain': music_gain, 'master_gain': master_gain,
                  'wav': wav.relative_to(output).as_posix(), 'wav_sha256': digest,
                  'mp3': mp3.relative_to(output).as_posix(), 'mp3_sha256': mp3_digest,
                  'quality': quality(wav, 'mix'), 'speech_check': audit(output, voice), 'music_check': audit(output, bed)}
        write(final_receipt, record, immutable=True)
        print('MIX', unit['id'], 'verified assets ready', flush=True)
        publish(output)
    publish(output)


def publish(output):
    # Full listening UI and final export are installed below; this remains callable
    # during native generation to expose checkpoint progress immediately.
    spec_path = output / 'spec.json'
    if not spec_path.exists():
        return
    manifest = read(spec_path)
    rows = []
    for unit in manifest['units']:
        row = {**{k: unit[k] for k in ('id', 'number', 'name', 'language', 'display')},
                     'speech_takes': len(receipts(output, unit, 'speech')),
                     'music_takes': len(receipts(output, unit, 'music')),
                     'speech_verified': bool(selected(output, unit, 'speech')),
                     'music_verified': bool(selected(output, unit, 'music'))}
        final = output / 'final' / (unit['id'] + '.json')
        if final.exists():
            row['final'] = read(final)
        row['takes'] = {kind: [{**r, 'verification': audit(output, r)} for r in receipts(output, unit, kind)]
                        for kind in ('speech', 'music')}
        row['failed_take_history'] = [r for kind in ('speech', 'music') for r in row['takes'][kind]
                                      if not r.get('quality_passed') or (r['verification'] is not None and
                                          not r['verification'].get('primary_check', r['verification'])['passed'])]
        row['open_flags'] = [kind for kind in ('speech', 'music')
                             if not row[kind + '_verified'] and any(
                                 not r.get('quality_passed') or (r['verification'] is not None and not r['verification']['passed'])
                                 for r in row['takes'][kind])]
        rows.append(row)
    report = {'schema': 1, 'units': rows, 'ready': sum('final' in row for row in rows),
              'machine_checks_only': True, 'spec_sha256': file_digest(spec_path)}
    report['revision'] = sha(report)
    finals = [row for row in rows if 'final' in row]
    if finals:
        provenance = {'spec': manifest, 'report': report,
                      'takes': {u['id']: {k: receipts(output, u, k) for k in ('speech', 'music')} for u in manifest['units']}}
        provenance_path = output / 'exports' / (sha(provenance) + '.json')
        write(provenance_path, provenance, immutable=True)
        archive = output / 'exports' / ('dogma-intros-' + sha(provenance)[:16] + '.zip')
        if not archive.exists():
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as packed:
                packed.write(provenance_path, 'provenance.json')
                for row in finals:
                    for ext in ('wav', 'mp3'):
                        packed.write(output / row['final'][ext], row['id'] + '-' + row['name'].lower() + '.' + ext)
        report['archive'] = archive.relative_to(output).as_posix()
    write(output / 'report.json', report)
    cards = []
    for row in rows:
        final = row.get('final')
        status = 'Machine checks passed.' if final else f"Speech {row['speech_takes']}/3; music {row['music_takes']}/3. " + ('Words checked. ' if row['speech_verified'] else 'Words pending. ') + ('No vocals detected.' if row['music_verified'] else 'Music check pending.')
        if row['open_flags']:
            status += ' Held: ' + ', '.join(row['open_flags']) + '; see raw checks.'
        if any(r['verification'] and r['verification'].get('tolerated_deviations') for r in row['takes']['speech']):
            status += ' Brand spelling differs in recognition; guarded name tolerance, not an exact match.'
        if any(r['verification'] and r['verification'].get('orthographic_expansions') for r in row['takes']['speech']):
            status += ' Recognition spelling differences were resolved using the recorded Russian pronunciation rules.'
        media = (f'<audio controls preload="metadata" src="{final["mp3"]}"></audio><p><a download="dogma-{row["id"]}.mp3" href="{final["mp3"]}">MP3</a> · <a download="dogma-{row["id"]}.wav" href="{final["wav"]}">WAV</a></p>') if final else ''
        cards.append(f'<article><small>{row["number"]:02d} · {row["language"]}</small><h2>{html.escape(row["name"])}</h2><p>{html.escape(DESCRIPTIONS[row["number"] - 1])}</p><p>{html.escape(row["display"])}</p>{media}<p class="status">{html.escape(status)}</p></article>')
    archive_link = f'<a class="button" href="{report["archive"]}" download>Download all ({len(finals)}/10)</a>' if finals else ''
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Dogma</title><style>
    :root{color-scheme:dark}body{margin:0;background:#141916;color:#eee9dc;font:16px system-ui;line-height:1.6}main{max-width:1000px;margin:auto;padding:48px 24px}h1{font:600 56px Georgia;margin:0 0 20px}h2{font:26px Georgia;margin:8px 0}small,.status{color:#aeb8aa}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}article{background:#202820;border:1px solid #344333;border-radius:14px;padding:24px}audio{width:100%}a{color:#b9d6a8}.button,button{display:inline-block;padding:10px 18px;border:1px solid #6d8961;border-radius:8px;background:#293c25;color:#eee9dc;font:inherit;cursor:pointer;margin:0 8px 20px 0}.intro{max-width:700px}footer{font-size:12px;color:#aeb8aa;margin-top:32px}</style><main><h1>Dogma</h1><p class="intro">Ten audio ident options: five Russian and five English. Compare the music and spoken delivery, then choose a favorite. These are listening trials; none is applied to your books.</p>'''
    page += f'<p>{len(finals)} of 10 ready. Audio and music use separate machine checks; listening approval is still yours.</p><button id="play">Play all ready</button>{archive_link}<div class="grid">' + ''.join(cards) + '</div>'
    page += '<footer>' + html.escape(manifest['music_pack']['license']['attribution']) + '<br><a href="report.json">Status and raw check evidence</a> · <a href="spec.json">Generation provenance</a></footer>'
    page += '''</main><script>const clips=[...document.querySelectorAll('audio')];let playingAll=false;const playButton=document.querySelector('#play');function cancelAll(){playingAll=false;playButton.textContent='Play all ready'}clips.forEach(a=>{a.addEventListener('play',()=>clips.forEach(other=>{if(other!==a)other.pause()}));a.addEventListener('pointerdown',cancelAll);a.addEventListener('keydown',cancelAll)});playButton.onclick=()=>{playingAll=!playingAll;clips.forEach(a=>a.pause());playButton.textContent=playingAll?'Stop':'Play all ready';if(playingAll&&clips.length){clips.forEach((a,i)=>a.onended=()=>{if(playingAll&&clips[i+1]){clips[i+1].currentTime=0;clips[i+1].play()}else cancelAll()});clips[0].currentTime=0;clips[0].play()}};setInterval(async()=>{if(clips.some(a=>!a.paused))return;try{const r=await fetch('report.json',{cache:'no-store'});const j=await r.json();if(j.revision!==REVISION)location.reload()}catch(e){}},8000);</script>'''.replace('REVISION', json.dumps(report['revision']))
    (output / 'index.html').write_text(page, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['prepare', 'speech', 'music', 'verify', 'witness', 'resolve', 'mix', 'all'], required=True)
    parser.add_argument('--output', type=Path, default=DEFAULT)
    parser.add_argument('--device', choices=['cuda', 'cpu'], default='cuda', help='YuE2 device; recorded in each exact request')
    parser.add_argument('--asr-python', type=Path, default=ROOT / 'training-work/.venv-asr/Scripts/python.exe')
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to((ROOT / 'training-work/dogma-intros').resolve())
    output.mkdir(parents=True, exist_ok=True)
    if args.stage == 'all':
        prepare(output)
        for attempt in range(MAX_TAKES):
            for stage in ('speech', 'music', 'verify', 'resolve', 'witness', 'mix'):
                python = args.asr_python if stage in ('verify', 'resolve', 'witness') else Path(sys.executable)
                subprocess.run([str(python), '-X', 'utf8', '-u', str(Path(__file__).resolve()), '--stage', stage,
                                '--output', str(output), '--device', args.device], check=True)
            if read(output / 'report.json')['ready'] == 10:
                return
        raise RuntimeError('Ident trial remains held after three takes; inspect report.json and raw checks')
    if args.stage == 'prepare':
        prepare(output)
    elif args.stage == 'speech':
        speech(output)
    elif args.stage == 'music':
        music(output, args.device)
    elif args.stage == 'verify':
        verify(output)
    elif args.stage == 'witness':
        witness(output)
    elif args.stage == 'resolve':
        resolve_witnesses(output)
    elif args.stage == 'mix':
        mix(output)


if __name__ == '__main__':
    main()
