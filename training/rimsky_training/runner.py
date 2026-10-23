from __future__ import annotations

import ast
from itertools import zip_longest
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from rimsky.instance import library_lease
from rimsky.util import AppError, digest, file_digest, now, signal_group, write_json
from .common import (checked_manifest, json_read, local_path, new_directory, number,
                     offline_environment, owned_file, rows_read, iter_rows, verify_files)
from .inference import local_model, package_versions, validate_codes
from .model_contract import (speech_config, model_tensors, verify_training_codec,
                             verify_checkpoint_weights, validate_trainer_projection)

FLAGS = {'--init_model_path', '--output_model_path', '--train_jsonl', '--batch_size',
         '--lr', '--num_epochs', '--speaker_name'}


def upstream_files(path: Path):
    root = local_path(path, directory=True)
    folder = root / 'finetuning'
    paths = [folder / 'sft_12hz.py', folder / 'dataset.py', root / 'LICENSE']
    if any(not path.is_file() or path.is_symlink() for path in paths):
        raise AppError('Provide a local Qwen3-TTS checkout containing finetuning/sft_12hz.py, dataset.py and LICENSE.')
    try:
        tree = ast.parse(paths[0].read_text('utf-8'))
    except (ValueError, SyntaxError) as error:
        raise AppError('Cannot inspect the selected Qwen trainer.') from error
    validate_trainer_projection(tree)
    flags = {arg.value for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == 'add_argument'
             for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)}
    if not FLAGS <= flags:
        raise AppError('Selected upstream trainer does not expose the verified single-speaker CLI contract.')
    provenance = root / 'rimsky-trainer-provenance.json'
    if provenance.exists():
        value = json_read(provenance)
        if not isinstance(value, dict) or value.get('schema') != 1:
            raise AppError('Invalid prepared-trainer provenance.')
        verify_files(root, value.get('prepared_files'))
        paths.append(provenance)
    return root, {path.relative_to(root).as_posix(): file_digest(path) for path in paths}


def validate_encoding(dataset_root, dataset, encoded_root):
    stage = json_read(encoded_root / 'stage.json')
    encoding = json_read(encoded_root / 'encoding.json')
    contract = encoding.get('contract')
    if (stage.get('status') != 'complete' or not isinstance(contract, dict)
            or stage.get('contract') != contract or stage.get('contract_sha256') != digest(contract)
            or contract.get('dataset_revision') != dataset['revision']
            or contract.get('dataset_path') != str(dataset_root)):
        raise AppError('Code preparation is incomplete or belongs to another dataset revision.')
    encoded = owned_file(encoded_root, 'train.codes.jsonl')
    if file_digest(encoded) != encoding.get('sha256'):
        raise AppError('Encoded dataset checksum changed.')
    originals = rows_read(dataset_root / 'train.raw.jsonl')
    count = 0
    for original, row in zip_longest(originals, iter_rows(encoded)):
        if original is None or row is None:
            raise AppError('Encoded dataset dropped or duplicated training rows.')
        expected = {**original, 'audio': str(owned_file(dataset_root, original['audio'])),
                    'ref_audio': str(owned_file(dataset_root, original['ref_audio']))}
        validate_codes(row, expected)
        count += 1
    if count != encoding.get('rows'):
        raise AppError('Encoded dataset row count changed.')
    return encoded


def create_plan(dataset_path: Path, encoded_path: Path, base_path: Path, qwen_source: Path,
                output: Path, epochs=3, batch_size=1, learning_rate=2e-6, timeout_seconds=86400):
    dataset_root = local_path(dataset_path, directory=True)
    dataset = checked_manifest(dataset_root, 'dataset.json')
    encoded_root = local_path(encoded_path, directory=True)
    encoded = validate_encoding(dataset_root, dataset, encoded_root)
    base, model_files = local_model(base_path)
    config = speech_config(base, 'base')
    model_tensors(base)
    codec_revision = verify_training_codec(base, json_read(encoded_root / 'encoding.json').get('contract'))
    upstream, source_files = upstream_files(qwen_source)
    if type(epochs) is not int or not 1 <= epochs <= 100 or type(batch_size) is not int or not 1 <= batch_size <= 32:
        raise AppError('Invalid training epoch or batch count.')
    number(learning_rate, 'learning rate', 1e-8, 1e-3)
    number(timeout_seconds, 'training timeout', 1, 604800)
    speaker = dataset['narrator']['id'].lower()
    destination = output.expanduser().absolute()
    for source in (dataset_root, encoded_root, base, upstream):
        if destination.resolve().is_relative_to(source) or source.is_relative_to(destination.resolve()):
            raise AppError('Training output must be separate from the corpus, dataset, base model and upstream source.')
    command = [sys.executable, str(upstream / 'finetuning/sft_12hz.py'), '--init_model_path', str(base),
               '--output_model_path', str(destination / 'checkpoints'), '--train_jsonl', str(encoded),
               '--batch_size', str(batch_size), '--lr', str(learning_rate), '--num_epochs', str(epochs), '--speaker_name', speaker]
    contract = {'schema': 1, 'backend': 'qwen3-tts-official-single-speaker',
                'dataset_path': str(dataset_root), 'dataset_revision': dataset['revision'],
                'encoded_path': str(encoded_root), 'encoded_sha256': file_digest(encoded),
                'base_model': str(base), 'base_files': model_files,
                'model_family': config['model_type'], 'model_size': config['tts_model_size'],
                'codec_revision': codec_revision, 'upstream': str(upstream), 'upstream_files': source_files,
                'speaker': speaker, 'narrator': dataset['narrator'], 'performance': dataset['performance'],
                'epochs': epochs, 'batch_size': batch_size, 'learning_rate': learning_rate,
                'timeout_seconds': timeout_seconds, 'command': command,
                'cwd': str(upstream / 'finetuning'), 'output': str(destination),
                'optimizer_resume_supported': False, 'instruction_control_trained': False}
    with new_directory(output) as stage:
        write_json(stage / 'plan.json', {**contract, 'revision': digest(contract)})
        write_json(stage / 'run.json', {'schema': 1, 'status': 'planned', 'plan_revision': digest(contract),
                                       'note': 'No training started. Execute explicitly on a prepared CUDA/BF16/FlashAttention environment.'})
    return {'status': 'planned', 'plan_revision': digest(contract), 'command': command,
            'training_clips': dataset['training_clips'], 'held_out_clips': dataset['validation_clips'],
            'training_hours': dataset['training_seconds'] / 3600,
            'held_out_hours': dataset['validation_seconds'] / 3600,
            'model_family': config['model_type'], 'model_size': config['tts_model_size'],
            'narrator': dataset['narrator'], 'instruction_control_trained': False}


def load_plan(root: Path):
    plan = json_read(root / 'plan.json')
    if not isinstance(plan, dict) or plan.get('revision') != digest({k: v for k, v in plan.items() if k != 'revision'}):
        raise AppError('Training plan changed; prepare a new run.')
    if str(root.resolve()) != str(Path(plan['output']).resolve()):
        raise AppError('Training run moved; create a new plan for its current output path.')
    dataset_root = local_path(plan['dataset_path'], directory=True)
    dataset = checked_manifest(dataset_root, 'dataset.json')
    if dataset['revision'] != plan['dataset_revision']:
        raise AppError('Dataset revision changed.')
    encoded = validate_encoding(dataset_root, dataset, local_path(plan['encoded_path'], directory=True))
    if file_digest(encoded) != plan['encoded_sha256']:
        raise AppError('Encoded data changed after training planning.')
    base = local_path(plan['base_model'], directory=True)
    verify_files(base, plan['base_files'])
    speech_config(base, 'base')
    model_tensors(base)
    codec_revision = verify_training_codec(base, json_read(Path(plan['encoded_path']) / 'encoding.json').get('contract'))
    if plan.get('codec_revision', codec_revision) != codec_revision:
        raise AppError('Training tokenizer identity changed after planning.')
    upstream, files = upstream_files(Path(plan['upstream']))
    if files != plan['upstream_files']:
        raise AppError('Qwen trainer source changed after planning.')
    return plan, dataset


def training_preflight():
    try:
        import torch
        import flash_attn
        import accelerate
        import qwen_tts
        import safetensors
    except ImportError as error:
        raise AppError('Use the isolated TTS training environment with Torch, FlashAttention 2, qwen-tts, Accelerate and safetensors.') from error
    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise AppError('The upstream trainer requires CUDA with BF16 and FlashAttention 2. CPU/MPS training is not supported by this adapter.')
    versions = package_versions(['qwen-tts', 'transformers', 'accelerate', 'torch', 'flash-attn', 'safetensors'])
    if versions['qwen-tts'] != '0.1.1' or versions['transformers'] != '4.57.3' or versions['accelerate'] != '1.12.0':
        raise AppError('The audited trainer adapter targets qwen-tts 0.1.1, Transformers 4.57.3 and Accelerate 1.12.0. Revalidate before changing these versions.')
    return {'packages': versions, 'cuda': torch.version.cuda, 'device': torch.cuda.get_device_name(0)}


def run_process(command, cwd: Path, log: Path, timeout: float):
    process = None
    with log.open('wb') as handle:
        try:
            process = subprocess.Popen(command, cwd=cwd, env=offline_environment(), stdin=subprocess.DEVNULL,
                                       stdout=handle, stderr=subprocess.STDOUT, start_new_session=os.name != 'nt')
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise AppError('Training exceeded its explicitly configured execution budget.')
                time.sleep(.1)
            if process.returncode:
                raise AppError(f'Trainer exited with status {process.returncode}. See training.log; no model was promoted.')
        finally:
            if process is not None:
                try:
                    if os.name == 'nt':
                        if process.poll() is None:
                            process.terminate()
                    else:
                        signal_group(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                if os.name != 'nt':
                    until = time.monotonic() + .2
                    while time.monotonic() < until:
                        process.poll()
                        if not signal_group(process.pid, 0):
                            break
                        time.sleep(.01)
                    signal_group(process.pid, signal.SIGKILL)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        if os.name == 'nt':
                            process.kill()
                        else:
                            signal_group(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=3)


def checkpoint_info(path: Path, speaker, base: Path | None = None):
    root, hashes = local_model(path)
    config = speech_config(root, 'custom_voice')
    speakers = config['talker_config'].get('spk_id')
    if not isinstance(speakers, dict) or set(speakers) != {speaker}:
        raise AppError('Training checkpoint is not the expected single-speaker CustomVoice model.')
    speaker_id = speakers[speaker]
    if type(speaker_id) is not int or speaker_id != 3000:
        raise AppError('Single-speaker checkpoint must use the audited trainer speaker token 3000.')
    dialects = config['talker_config'].get('spk_is_dialect')
    if not isinstance(dialects, dict) or dialects.get(speaker) is not False:
        raise AppError('The trained speaker must retain the upstream non-dialect conditioning flag.')
    verify_checkpoint_weights(root, base)
    return {'path': str(root), 'files': hashes, 'revision': digest(hashes)}


def execute_training(run_path: Path):
    root = local_path(run_path, directory=True)
    with library_lease(root):
        plan, dataset = load_plan(root)
        previous = json_read(root / 'run.json')
        if previous.get('status') != 'planned' or (root / 'checkpoints').exists():
            raise AppError('This run already started. The upstream trainer has no optimizer-resume contract; preserve its output and create a new run.')
        environment = training_preflight()
        state = {'schema': 1, 'plan_revision': plan['revision'], 'status': 'running', 'started': now(),
                 'environment': environment, 'executed_command': [sys.executable, *plan['command'][1:]]}
        write_json(root / 'run.json', state)
        try:
            command = [sys.executable, *plan['command'][1:]]
            run_process(command, Path(plan['cwd']), root / 'training.log', plan['timeout_seconds'])
            load_plan(root)
            checkpoints = [checkpoint_info(root / 'checkpoints' / f'checkpoint-epoch-{epoch}', plan['speaker'], Path(plan['base_model']))
                           for epoch in range(plan['epochs'])]
            state.update(status='complete', completed=now(), checkpoints=checkpoints,
                         quality_reviewed=False, instruction_control_trained=False)
        except BaseException as error:
            state.update(status='interrupted' if isinstance(error, KeyboardInterrupt) else 'failed',
                         error=str(error)[:2000], completed=now())
            write_json(root / 'run.json', state)
            raise
        write_json(root / 'run.json', state)
    return state


def completed_checkpoint(run_path: Path, checkpoint_path: Path):
    root = local_path(run_path, directory=True)
    plan, dataset = load_plan(root)
    run = json_read(root / 'run.json')
    if run.get('status') != 'complete' or run.get('plan_revision') != plan['revision']:
        raise AppError('Training run has not completed successfully.')
    checkpoint = local_path(checkpoint_path, directory=True)
    expected = next((item for item in run.get('checkpoints', []) if item['path'] == str(checkpoint)), None)
    actual = checkpoint_info(checkpoint, plan['speaker'], Path(plan['base_model']))
    if expected is None or actual != expected:
        raise AppError('Checkpoint is not a frozen output of this completed run.')
    return plan, dataset, actual
