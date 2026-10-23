"""Pinned musical Dogma branding, separate from every book's source timeline."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import re
import wave

from .audio_source import open_shared_read
from .util import AppError, atomic_write, digest, file_digest, language_name

PACK_ID = 'dogma-opening-v1'
MANIFEST_SHA256 = '704e166f98ad9dff7e9056af309ef2e45fdbcb83a98f0e9927dd1eb4e74d72aa'
AUDIO_PINS = {
    'ru': {'speech': '8b18b982ecd30f8b3c6fb7f907424ea4836dfcdd40d89f05c832e726129271c8',
           'music': '9f4c63c1dfdfeff417e818c8cefb2031f068980eab5d63b1572e0f05fbe48d62'},
    'en': {'speech': '3b6b892f47e85fa8aed9403b3414a23bc706936dc7141631a0e54f76c151b60f',
           'music': 'c13fe1b4d4f03ae344ff9d084bda0fe2a2be7f8038141ba59ecf6fdde372dc0d'},
}


def matching_language(book):
    declared = book.get('language')
    normalized = language_name(declared) if isinstance(declared, str) else ''
    if normalized in ('Russian', 'English'):
        return 'ru' if normalized == 'Russian' else 'en'
    actual = declared.strip() if isinstance(declared, str) and declared.strip() else 'unknown'
    raise AppError(f'A matching-language Dogma opening is required for this book (declared language: {actual}). '
                   'Supported openings are Russian and English; set a supported book language or add an approved matching opening.',
                   'opening_language_required', language=actual)


def _measure(path, *, stream=None):
    try:
        with ExitStack() as stack:
            source = stream if stream is not None else stack.enter_context(open_shared_read(path))
            source.seek(0)
            with wave.open(source, 'rb') as audio:
                channels, width, rate, samples = audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()
                if audio.getcomptype() != 'NONE' or width != 2 or rate != 24000 or channels not in (1, 2) or not 0 < samples <= 8 * rate:
                    raise AppError('Dogma opening has an invalid PCM16 WAV format or duration.')
                remaining = samples * channels * width
                while remaining:
                    data = audio.readframes(min(65536, remaining // (channels * width)))
                    if not data:
                        raise AppError('Dogma opening audio is truncated.')
                    remaining -= len(data)
            source.seek(0)
            checksum = hashlib.file_digest(source, 'sha256').hexdigest()
            source.seek(0)
    except (OSError, EOFError, wave.Error) as error:
        raise AppError('Dogma opening audio is missing or cannot be decoded.') from error
    return {'sha256': checksum, 'duration': samples / rate, 'samples': samples,
            'sample_rate': rate, 'channels': channels, 'sample_width': width}


def _variant(actual, variant, name):
    if (actual['sha256'] != variant.get('audio_hash')
            or any(actual[k] != variant.get(k) for k in ('samples', 'sample_rate', 'channels', 'sample_width'))
            or type(variant.get('duration')) not in (float, int)
            or not math.isfinite(variant['duration']) or abs(actual['duration'] - variant['duration']) > 1 / actual['sample_rate']
            or actual['channels'] != (2 if name == 'music' else 1)
            or name == 'music' and actual['samples'] != 8 * 24000):
        raise AppError('Dogma opening audio does not match its approved hash, format or duration.')


def load_opening_pack(resources=None):
    resource_root = Path(resources) if resources is not None else Path(os.environ.get('RIMSKY_RESOURCE_DIR', str(Path(__file__).resolve().parents[2] / 'resources')))
    folder = resource_root / 'branding' / PACK_ID
    manifest_path = folder / 'manifest.json'
    try:
        if file_digest(manifest_path) != MANIFEST_SHA256:
            raise AppError('Bundled Dogma opening manifest checksum mismatch.')
        manifest = json.loads(manifest_path.read_text('utf-8'))
        if manifest.get('schema') != 1 or manifest.get('id') != PACK_ID or manifest.get('music_policy') != 'always_musical_branding':
            raise AppError('Bundled Dogma opening manifest is invalid.')
        for language in ('ru', 'en'):
            for name in ('speech', 'music'):
                variant = manifest['languages'][language]['variants'][name]
                if variant['file'] != f'{language}-{name}.wav' or variant['audio_hash'] != AUDIO_PINS[language][name]:
                    raise AppError('Bundled Dogma opening does not describe the approved assets.')
                _variant(_measure(folder / variant['file']), variant, name)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise AppError('Bundled Dogma opening is missing or has invalid provenance.') from error
    return manifest, folder


def validate_opening(opening):
    if (not isinstance(opening, dict) or opening.get('schema') != 1 or opening.get('pack') != PACK_ID
            or opening.get('manifest_sha256') != MANIFEST_SHA256
            or not isinstance(opening.get('language'), str) or opening['language'] not in AUDIO_PINS
            or opening.get('music_policy') != 'always_musical_branding'
            or not isinstance(opening.get('key'), str)
            or not re.fullmatch('[a-f0-9]{64}', opening['key'])
            or digest({k: v for k, v in opening.items() if k != 'key'}) != opening['key']):
        raise AppError('Saved Dogma opening pin is missing or invalid.')
    variants = opening.get('variants')
    if not isinstance(variants, dict) or set(variants) != {'speech', 'music'}:
        raise AppError('Saved Dogma opening variants are invalid.')
    for name, variant in variants.items():
        if (not isinstance(variant, dict) or variant.get('audio_hash') != AUDIO_PINS[opening['language']][name]
                or type(variant.get('duration')) not in (int, float) or not math.isfinite(variant['duration'])
                or not 0 < variant['duration'] <= 8 or variant.get('sample_rate') != 24000
                or variant.get('sample_width') != 2 or variant.get('channels') != (2 if name == 'music' else 1)
                or type(variant.get('samples')) is not int or variant['samples'] != round(variant['duration'] * 24000)
                or name == 'music' and variant['duration'] != 8):
            raise AppError('Saved Dogma opening variant pin is invalid.')
    return opening


def attach_opening(store, book):
    """Install immutable copies for a NEW generation; no synthesis and no source row."""
    language = matching_language(book)
    manifest, folder = load_opening_pack(store.resources)
    selected = manifest['languages'][language]
    opening = {'schema': 1, 'pack': PACK_ID, 'manifest_sha256': MANIFEST_SHA256,
               'language': language, 'text': selected['text'], 'spoken_text': selected['spoken_text'],
               'attribution': manifest['attribution'], 'music_policy': 'always_musical_branding',
               'variants': {name: {k: v for k, v in variant.items() if k != 'file'}
                            for name, variant in selected['variants'].items()},
               'provenance': {k: manifest[k] for k in ('model_sha256', 'reference_sha256', 'music_sha256', 'source_spec_sha256')},
               'checks': {'speech': selected['provenance']['final_receipt']['speech_check']['status'],
                          'music': selected['provenance']['final_receipt']['music_check']['status']}}
    opening['key'] = digest(opening)
    validate_opening(opening)
    with store.lock:
        for name, variant in selected['variants'].items():
            path = store.audio_path(variant['audio_hash'])
            metadata = {'schema': 1, 'kind': 'dogma-opening', 'pack_manifest_sha256': MANIFEST_SHA256,
                        'sha256': variant['audio_hash'],
                        **{k: variant[k] for k in ('duration', 'samples', 'sample_rate', 'channels', 'sample_width')},
                        'warning': '', 'reset_cues': False}
            if path.exists() or path.with_suffix('.json').exists():
                verify_opening_audio(path, {**variant, 'variant': name})
            else:
                atomic_write(path, (folder / variant['file']).read_bytes())
                atomic_write(path.with_suffix('.json'), json.dumps(metadata, ensure_ascii=False).encode())
    return opening


def select_opening(generation, music_enabled=None):
    """Branding is always musical; the background-score setting has no effect."""
    config = generation.get('config') or {}
    if not isinstance(config, dict):
        raise AppError('Saved generation configuration is invalid.')
    if 'opening' not in config:
        return None  # Historical generations are never silently backfilled.
    opening = validate_opening(config['opening'])
    return {**opening['variants']['music'], 'variant': 'music',
            **{k: opening[k] for k in ('key', 'language', 'text', 'attribution', 'manifest_sha256')}}


def verify_opening_audio(path, variant, *, stream=None, metadata_stream=None):
    if not isinstance(variant, dict):
        raise AppError('Dogma opening variant pin is invalid.')
    name = variant.get('variant', 'music')
    if name not in ('speech', 'music') or variant.get('audio_hash') not in {p[name] for p in AUDIO_PINS.values()}:
        raise AppError('Audio is not an approved Dogma opening variant.')
    actual = _measure(Path(path), stream=stream)
    _variant(actual, variant, name)
    try:
        if metadata_stream is None:
            metadata = json.loads(Path(path).with_suffix('.json').read_text('utf-8'))
        else:
            metadata_stream.seek(0)
            metadata = json.load(metadata_stream)
        if (not isinstance(metadata, dict) or metadata.get('schema') != 1 or metadata.get('kind') != 'dogma-opening'
                or metadata.get('pack_manifest_sha256') != MANIFEST_SHA256
                or any(metadata.get(k) != v for k, v in actual.items())
                or metadata.get('reset_cues') is not False or metadata.get('warning') != ''):
            raise AppError('Saved Dogma opening metadata does not match the approved audio.')
    except (OSError, ValueError, TypeError) as error:
        raise AppError('Saved Dogma opening metadata is missing or invalid.') from error
    return {**actual, 'warning': '', 'reset_cues': False}


def opening_hashes(generation):
    config = generation.get('config') or {}
    if not isinstance(config, dict):
        raise AppError('Saved generation configuration is invalid.')
    if 'opening' not in config:
        return set()
    opening = validate_opening(config['opening'])
    return {variant['audio_hash'] for variant in opening['variants'].values()}


def opening_reference(store, audio_hash):
    """Authorize only immutable branding referenced by a live generation."""
    for row in store.rows("SELECT config FROM generations WHERE instr(config,'\"opening\"')>0"):
        try:
            opening = validate_opening(json.loads(row['config'])['opening'])
        except (ValueError, TypeError, KeyError) as error:
            raise AppError('Saved Dogma opening reference is invalid.') from error
        for name, variant in opening['variants'].items():
            if variant['audio_hash'] == audio_hash:
                return {**variant, 'variant': name, 'key': opening['key']}
    return None


def validate_opening_position(value, generation):
    opening = select_opening(generation)
    if (not opening or not isinstance(value, dict) or set(value) - {'key', 'offset', 'complete', 'variant'}
            or value.get('key') != opening['key'] or value.get('variant', 'music') != 'music'
            or type(value.get('complete')) is not bool or type(value.get('offset')) not in (int, float)
            or not math.isfinite(value['offset']) or not 0 <= value['offset'] <= opening['duration']):
        raise AppError('Invalid branded opening playback position.')
    return value
