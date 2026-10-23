from __future__ import annotations

import gc
import os
import shutil
from pathlib import Path

from rimsky.integrity import read_audio
from rimsky.packs import load_pack
from rimsky.references import inspect_reference
from rimsky.util import AppError, bounded_text, file_digest, reached_generation_cap, write_json
from .common import (OFFLINE_ENV, checked_manifest, json_read, local_path, new_directory,
                     owned_file, rows_read, seal_json, tree_hashes)
from .inference import package_versions
from .runner import completed_checkpoint

REVIEW_GATES = ('intelligible', 'narrator_identity', 'pronunciation', 'continuity', 'no_regression')


def render_evaluation(run_path: Path, checkpoint_path: Path, output: Path, count=12, device='cpu'):
    plan, dataset, checkpoint = completed_checkpoint(run_path, checkpoint_path)
    if type(count) is not int or not 1 <= count <= 100:
        raise AppError('Evaluate between 1 and 100 held-out passages per bundle.')
    dataset_root = Path(plan['dataset_path'])
    rows = rows_read(dataset_root / 'validation.raw.jsonl')[:count]
    if not rows:
        raise AppError('No held-out passages are available.')
    os.environ.update(OFFLINE_ENV)
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel
    dtype = torch.float32 if device in ('cpu', 'mps') else torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    with new_directory(output) as stage:
        for folder in ('base', 'candidate', 'target'):
            (stage / folder).mkdir()
        samples = [{'id': Path(row['audio']).stem, 'text': row['text'],
                    'target': f'target/{Path(row["audio"]).name}'} for row in rows]
        for row, item in zip(rows, samples):
            shutil.copyfile(owned_file(dataset_root, row['audio']), stage / item['target'])
        for kind, path in (('base', plan['base_model']), ('candidate', checkpoint['path'])):
            model = Qwen3TTSModel.from_pretrained(path, device_map=device, dtype=dtype,
                                                attn_implementation='sdpa', local_files_only=True)
            prompt = None
            try:
                if kind == 'base':
                    prompt = model.create_voice_clone_prompt(
                        ref_audio=str(owned_file(dataset_root, rows[0]['ref_audio'])),
                        ref_text=dataset['reference_text'], x_vector_only_mode=False)
                for row, item in zip(rows, samples):
                    torch.manual_seed(42)
                    arguments = {'text': row['text'], 'language': 'Russian' if kind == 'base' else 'Auto', 'max_new_tokens': 2048}
                    if kind == 'base':
                        wavs, rate = model.generate_voice_clone(voice_clone_prompt=prompt, **arguments)
                    else:
                        wavs, rate = model.generate_custom_voice(speaker=plan['speaker'], **arguments)
                    audio = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
                    if not len(audio) or not np.isfinite(audio).all():
                        raise AppError('Evaluation produced empty or invalid audio.')
                    if reached_generation_cap(len(audio), rate, 2048):
                        raise AppError('Evaluation audio may have reached its generation cap.')
                    relative = f'{kind}/{item["id"]}.wav'
                    sf.write(stage / relative, audio, rate, format='WAV', subtype='PCM_16')
                    inspect_reference(stage / relative)
                    info = read_audio(stage / relative)
                    item[kind] = relative
                    item[f'{kind}_audio'] = info
            finally:
                del prompt, model
                gc.collect()
                if device.startswith('cuda'):
                    torch.cuda.empty_cache()
        completed_checkpoint(run_path, checkpoint_path)
        write_json(stage / 'review-template.json', {'schema': 1, 'evaluation_revision': 'COPY_FROM_evaluation.json',
                  'accepted': False, 'reviewer': '', **{gate: False for gate in REVIEW_GATES}, 'notes': ''})
        manifest = seal_json(stage, 'evaluation.json', {'schema': 1, 'run': str(run_path.resolve()),
                    'plan_revision': plan['revision'], 'dataset_revision': dataset['revision'],
                    'checkpoint': checkpoint, 'samples': samples, 'narrator': dataset['narrator'],
                    'performance': dataset['performance'], 'scope': 'Single narrator/performance; not multi-character acting acceptance.',
                    'device': device, 'packages': package_versions(['qwen-tts', 'torch', 'transformers']),
                    'quality_reviewed': False, 'baseline_language': 'Russian', 'candidate_language': 'Auto'})
    return {'revision': manifest['revision'], 'samples': len(samples), 'quality_reviewed': False,
            'next': 'Copy review-template.json outside this immutable bundle and complete a listening review.'}


def promote_checkpoint(evaluation_path: Path, review_path: Path, text_model_path: Path,
                       text_license_path: Path, output: Path, context=16384):
    root = local_path(evaluation_path, directory=True)
    evaluation = checked_manifest(root, 'evaluation.json')
    if evaluation.get('candidate_language') != 'Auto':
        raise AppError('Re-evaluate this checkpoint with the matching Auto-language conditioning before publishing a new trained pack.')
    review = json_read(review_path)
    if (not isinstance(review, dict) or review.get('schema') != 1
            or review.get('evaluation_revision') != evaluation['revision'] or review.get('accepted') is not True
            or not all(review.get(gate) is True for gate in REVIEW_GATES)):
        raise AppError('A complete explicit listening review of this exact evaluation is required before pack publication.')
    bounded_text(review.get('reviewer'), 200)
    bounded_text(review.get('notes'), 4000)
    plan, dataset, checkpoint = completed_checkpoint(Path(evaluation['run']), Path(evaluation['checkpoint']['path']))
    if (evaluation['checkpoint'] != checkpoint or evaluation.get('plan_revision') != plan['revision']
            or evaluation.get('dataset_revision') != dataset['revision']):
        raise AppError('Evaluation belongs to a different training result.')
    text_model = local_path(text_model_path)
    with text_model.open('rb') as handle:
        header = handle.read(4)
    if text_model.suffix.lower() != '.gguf' or header != b'GGUF':
        raise AppError('Select an actual local GGUF text model.')
    text_license = local_path(text_license_path)
    if type(context) is not int or not 8192 <= context <= 131072:
        raise AppError('Invalid text-model context.')
    destination = output.expanduser().absolute().resolve()
    for source in (root, Path(checkpoint['path']), Path(plan['base_model']), Path(plan['dataset_path'])):
        if destination.is_relative_to(source) or source.is_relative_to(destination):
            raise AppError('Publish to a new directory outside training, evaluation and base assets.')
    with new_directory(output) as stage:
        (stage / 'models' / 'text').mkdir(parents=True)
        shutil.copyfile(text_model, stage / 'models' / 'text' / 'model.gguf')
        shutil.copytree(checkpoint['path'], stage / 'models' / 'speech')
        if tree_hashes(stage / 'models' / 'speech') != checkpoint['files']:
            raise AppError('Checkpoint changed while copying into the new pack.')
        if file_digest(text_model) != file_digest(stage / 'models' / 'text' / 'model.gguf'):
            raise AppError('Text model changed while copying.')
        (stage / 'LICENSES').mkdir()
        shutil.copyfile(Path(plan['upstream']) / 'LICENSE', stage / 'LICENSES' / 'Qwen3-TTS.txt')
        shutil.copyfile(text_license, stage / 'LICENSES' / 'text-model.txt')
        write_json(stage / 'LICENSES' / 'narrator-rights.json', dataset['rights'])
        write_json(stage / 'training-provenance.json', {'schema': 1, 'narrator': dataset['narrator'],
                   'performance': dataset['performance'], 'plan_revision': plan['revision'],
                   'dataset_revision': dataset['revision'], 'evaluation_revision': evaluation['revision'],
                   'checkpoint_revision': checkpoint['revision'], 'review': review,
                   'scope': evaluation['scope'], 'instruction_control_trained': False})
        speaker = plan['speaker']
        pack = {'schema': 1, 'name': f'{dataset["narrator"]["name"]} — {dataset["performance"]} (local SFT)',
                'text_model': {'path': 'models/text/model.gguf', 'context': context},
                'speech': {'path': 'models/speech', 'variant': 'custom', 'instruction_control': False, 'language_mode': 'auto', 'max_new_tokens': 2048},
                'voices': [{'id': 'narrator', 'name': dataset['narrator']['name'], 'role': 'narrator', 'speaker': speaker, 'tags': []},
                           {'id': 'shared-performance', 'name': 'Shared narrator performance; not a distinct trained voice',
                            'role': 'character', 'speaker': speaker, 'tags': []}],
                'rights': {'voice_assets': dataset['rights']['basis'],
                           'redistribution_authorized': dataset['rights']['redistribution_authorized']},
                'training': {'single_speaker': True, 'evaluation_revision': evaluation['revision'],
                             'instruction_control_trained': False, 'language_conditioning': 'auto'}}
        pack['files'] = tree_hashes(stage)
        write_json(stage / 'pack.json', pack)
        accepted = load_pack(str(stage), verify=True)
    return {'pack_revision': accepted['_fingerprint'], 'instruction_control': False,
            'distinct_baseline_performances': 1,
            'redistribution_authorized': dataset['rights']['redistribution_authorized']}
