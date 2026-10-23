from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path

from rimsky.util import AppError, digest, file_digest, write_json
from .common import (OFFLINE_ENV, checked_manifest, json_read, local_path, new_directory,
                     owned_file, rows_read, rows_write, seal_json, stage_workspace, tree_hashes, verify_files)
from .corpus import load_corpus, select_recording
from .pairing import validate_timestamp_text, validate_transcript
from .model_contract import CODEBOOKS, CODEBOOK_SIZE, tokenizer_identity


def local_model(path: Path):
    root = local_path(path, directory=True)
    if not (root / 'config.json').is_file():
        raise AppError('Local model config.json is missing.')
    files = tree_hashes(root)
    if not any(name.endswith('.safetensors') for name in files):
        raise AppError('Expected local safetensors model weights; no model will be downloaded.')
    return root, files


def package_versions(names):
    result = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def repair_zero_duration_words(words):
    """Recover a zero-length Qwen word using a short gap or shared onset.

    These are still alignment candidates, never accepted training boundaries.
    A word with no adjacent interval to share remains an error.
    """
    repaired = 0
    for index, word in enumerate(words):
        if word['end'] != word['start']:
            continue
        if index + 1 >= len(words):
            continue
        following = words[index + 1]
        gap = following['start'] - word['start']
        if .04 <= gap <= .5:
            # The aligner sometimes places a word at the start of a silent
            # gap. Use that gap without moving another word's timestamp.
            word['end'] = following['start']
        elif abs(gap) < .001 and following['end'] - following['start'] >= .08:
            # Two words share an onset; assign 40 ms of the next interval.
            word['end'] = word['start'] + .04
            following['start'] = word['end']
        else:
            continue
        repaired += 1
    return repaired


def transcribe(corpus_path: Path, recording_id: str, asr_path: Path, aligner_path: Path,
               output: Path, device='cpu', resume=False, loaded_model=None):
    root, corpus = load_corpus(corpus_path)
    recording = select_recording(corpus, recording_id)
    asr, asr_files = local_model(asr_path)
    aligner, aligner_files = local_model(aligner_path)
    contract = {'kind': 'qwen-asr-timestamp-candidates', 'schema': 1,
                'corpus_revision': corpus['revision'], 'recording_id': recording_id,
                'asr_files': asr_files, 'aligner_files': aligner_files, 'window_seconds': 30,
                'device': device, 'packages': package_versions(['qwen-asr', 'torch', 'transformers'])}
    os.environ.update(OFFLINE_ENV)
    with stage_workspace(output, contract, resume) as stage:
        import soundfile as sf
        import torch
        from qwen_asr import Qwen3ASRModel
        dtype = torch.float32 if device in ('cpu', 'mps') else torch.float16
        model = loaded_model
        all_words, windows = [], []
        (stage / 'windows').mkdir(exist_ok=True)
        with sf.SoundFile(owned_file(root, recording['path'])) as audio:
            for index, start_frame in enumerate(range(0, audio.frames, audio.samplerate * 30)):
                offset = start_frame / audio.samplerate
                end = min((start_frame + audio.samplerate * 30) / audio.samplerate, recording['audio']['duration'])
                saved = stage / 'windows' / f'{index:06d}.json'
                row = None
                if saved.is_file():
                    candidate = json_read(saved)
                    if (candidate.get('contract_sha256') == digest(contract)
                            and candidate.get('index') == index and candidate.get('start') == offset
                            and candidate.get('end') == end
                            and candidate.get('sha256') == digest({k: v for k, v in candidate.items() if k != 'sha256'})):
                        row = candidate
                    else:
                        raise AppError('Transcription checkpoint changed; use a new stage directory.')
                if row is None:
                    if model is None:
                        model = Qwen3ASRModel.from_pretrained(str(asr), device_map=device, dtype=dtype,
                            local_files_only=True, max_inference_batch_size=1, max_new_tokens=2048,
                            forced_aligner=str(aligner),
                            forced_aligner_kwargs={'device_map': device, 'dtype': dtype, 'local_files_only': True})
                    audio.seek(start_frame)
                    samples = audio.read(min(audio.samplerate * 30, audio.frames - start_frame), dtype='float32', always_2d=True).mean(axis=1)
                    result = model.transcribe(audio=(samples, audio.samplerate), language='Russian', return_time_stamps=True)[0]
                    words = [{'text': item.text, 'start': offset + float(item.start_time), 'end': offset + float(item.end_time)}
                             for item in (result.time_stamps or [])]
                    repaired = repair_zero_duration_words(words)
                    rejection = None
                    if result.time_stamps is None and result.text.strip():
                        rejection = 'ASR returned no timestamps.'
                    elif any(word['start'] < offset or word['end'] > end + .001 for word in words):
                        rejection = 'ASR alignment escaped its source audio window.'
                    else:
                        try:
                            validate_timestamp_text(result.text, words)
                            if words:
                                validate_transcript({'schema': 1, 'recording_sha256': recording['sha256'],
                                                     'words': words}, recording)
                        except AppError as error:
                            rejection = str(error)
                    row = {'index': index, 'start': offset, 'end': end,
                           'text': '' if rejection else result.text,
                           'words': [] if rejection else words,
                           'raw_text': result.text if rejection else None,
                           'raw_words': words if rejection else None,
                           'timing_repairs': repaired, 'rejected_reason': rejection,
                           'contract_sha256': digest(contract)}
                    row['sha256'] = digest(row)
                validate_timestamp_text(row['text'], row['words'])
                if row['words']:
                    validate_transcript({'schema': 1, 'recording_sha256': recording['sha256'], 'words': row['words']}, recording)
                if any(word['start'] < offset or word['end'] > end + .001 for word in row['words']):
                    raise AppError('ASR alignment escaped its source audio window.')
                if not saved.exists():
                    write_json(saved, row)
                windows.append({'index': index, 'start': offset, 'end': end, 'text': row['text'],
                                'words': len(row['words']), 'timing_repairs': row.get('timing_repairs', 0),
                                'rejected_reason': row.get('rejected_reason')})
                all_words.extend(row['words'])
        result = {'schema': 1, 'recording_sha256': recording['sha256'], 'words': all_words,
                  'provenance': contract, 'windows': windows, 'reviewed': False}
        validate_transcript(result, recording)
        load_corpus(root)
        verify_files(asr, asr_files)
        verify_files(aligner, aligner_files)
        write_json(stage / 'transcript.json', result)
    return {'words': len(all_words), 'windows': len(windows),
            'rejected_windows': sum(bool(window['rejected_reason']) for window in windows),
            'reviewed': False, 'transcript': str(output / 'transcript.json')}


def validate_codes(row, original):
    if not isinstance(row, dict) or set(row) != {*original, 'audio_codes'} or any(row.get(key) != value for key, value in original.items()):
        raise AppError('Encoded speech record no longer matches the reviewed dataset.')
    codes = row.get('audio_codes')
    if not isinstance(codes, list) or not 1 <= len(codes) <= 4000:
        raise AppError('Invalid audio-code sequence length.')
    if any(not isinstance(frame, list) or len(frame) != CODEBOOKS or
           any(type(value) is not int or not 0 <= value < CODEBOOK_SIZE for value in frame) for frame in codes):
        raise AppError('Expected sixteen Qwen 12Hz codebooks, each with an integer ID from 0 to 2047.')
    return row


def encode_dataset(dataset_path: Path, tokenizer_path: Path, output: Path, device='cuda:0', resume=False):
    root = local_path(dataset_path, directory=True)
    dataset = checked_manifest(root, 'dataset.json')
    tokenizer_root, tokenizer_files = local_model(tokenizer_path)
    tokenizer_identity(tokenizer_root, tokenizer_files)
    raw = rows_read(owned_file(root, 'train.raw.jsonl'))
    for row in raw:
        owned_file(root, row['audio'])
        owned_file(root, row['ref_audio'])
    contract = {'kind': 'qwen-12hz-codes', 'schema': 1, 'dataset_revision': dataset['revision'],
                'dataset_path': str(root), 'tokenizer_files': tokenizer_files, 'device': device,
                'packages': package_versions(['qwen-tts', 'torch', 'transformers'])}
    os.environ.update(OFFLINE_ENV)
    with stage_workspace(output, contract, resume) as stage:
        codec = None
        count = 0
        (stage / 'rows').mkdir(exist_ok=True)
        def encoded_rows():
            nonlocal codec, count
            for index, source in enumerate(raw):
                original = {**source, 'audio': str(owned_file(root, source['audio'])),
                            'ref_audio': str(owned_file(root, source['ref_audio']))}
                path = stage / 'rows' / f'{index:07d}.json'
                if path.exists():
                    envelope = json_read(path)
                    if envelope.get('input_sha256') != digest(original) or envelope.get('row_sha256') != digest(envelope.get('row')):
                        raise AppError('Audio-code checkpoint changed; use a new stage directory.')
                    row = validate_codes(envelope['row'], original)
                else:
                    if codec is None:
                        from qwen_tts import Qwen3TTSTokenizer
                        codec = Qwen3TTSTokenizer.from_pretrained(str(tokenizer_root), device_map=device, local_files_only=True)
                    result = codec.encode([original['audio']])
                    if len(result.audio_codes) != 1:
                        raise AppError('Tokenizer did not return exactly one audio-code sequence.')
                    row = validate_codes({**original, 'audio_codes': result.audio_codes[0].cpu().tolist()}, original)
                    write_json(path, {'input_sha256': digest(original), 'row_sha256': digest(row), 'row': row})
                count += 1
                yield row
        rows_write(stage / 'train.codes.jsonl', encoded_rows())
        checked_manifest(root, 'dataset.json')
        verify_files(tokenizer_root, tokenizer_files)
        write_json(stage / 'encoding.json', {'schema': 1, 'contract': contract, 'rows': count,
                                           'sha256': file_digest(stage / 'train.codes.jsonl')})
    return {'rows': count, 'dataset_revision': dataset['revision'], 'encoded': str(output / 'train.codes.jsonl')}
