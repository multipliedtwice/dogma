from __future__ import annotations

import ast
from pathlib import Path

from rimsky.util import AppError, digest, file_digest
from .common import json_read, owned_file, tree_hashes

CODEBOOKS = 16
CODEBOOK_SIZE = 2048
MODEL_TYPE = 'qwen3_tts'
TOKENIZER_TYPE = 'qwen3_tts_tokenizer_12hz'
SUPPORTED_SIZES = {'1b7', '0b6'}


def speech_config(root: Path, variant: str):
    config = json_read(root / 'config.json')
    if (not isinstance(config, dict) or config.get('model_type') != MODEL_TYPE
            or config.get('tokenizer_type') != TOKENIZER_TYPE
            or config.get('tts_model_size') not in SUPPORTED_SIZES
            or config.get('tts_model_type') != variant):
        raise AppError('This adapter requires a local Qwen3-TTS 12Hz 1.7B/0.6B ' + variant + ' checkpoint. Other Qwen models need a separately verified adapter.')
    talker = config.get('talker_config')
    if (not isinstance(talker, dict) or talker.get('num_code_groups') != CODEBOOKS
            or not isinstance(talker.get('code_predictor_config'), dict)
            or talker['code_predictor_config'].get('vocab_size') != CODEBOOK_SIZE):
        raise AppError('The speech checkpoint has an incompatible Qwen 12Hz codebook configuration.')
    return config


def tokenizer_identity(root: Path, files=None):
    config = json_read(root / 'config.json')
    if (not isinstance(config, dict) or config.get('model_type') != TOKENIZER_TYPE
            or config.get('encoder_valid_num_quantizers') != CODEBOOKS
            or config.get('input_sample_rate') != 24000 or config.get('output_sample_rate') != 24000
            or not isinstance(config.get('encoder_config'), dict)
            or config['encoder_config'].get('codebook_size') != CODEBOOK_SIZE):
        raise AppError('Use the Qwen3-TTS 12Hz tokenizer bundled with the selected Base model, not a text/ASR/25Hz tokenizer.')
    files = tree_hashes(root) if files is None else files
    required = {name: value for name, value in files.items()
                if name == 'config.json' or name.endswith('.safetensors')
                or name.endswith('.safetensors.index.json') or name == 'preprocessor_config.json'}
    if not any(name.endswith('.safetensors') for name in required):
        raise AppError('The Qwen speech tokenizer has no local weights.')
    return required


def verify_training_codec(base: Path, encoding_contract):
    expected = tokenizer_identity(base / 'speech_tokenizer')
    files = encoding_contract.get('tokenizer_files') if isinstance(encoding_contract, dict) else None
    if not isinstance(files, dict):
        raise AppError('Encoding has no frozen tokenizer identity; encode again with the Base model speech_tokenizer directory.')
    actual = {name: value for name, value in files.items()
              if name == 'config.json' or name.endswith('.safetensors')
              or name.endswith('.safetensors.index.json') or name == 'preprocessor_config.json'}
    if actual != expected:
        raise AppError('Training audio codes use a different tokenizer revision from the Base model. Re-encode with its speech_tokenizer directory.')
    return digest(expected)


def model_tensors(root: Path):
    try:
        from safetensors import safe_open
    except ImportError as error:
        raise AppError('Install the declared TTS training dependencies to inspect model weights; no model will be loaded.') from error
    index = root / 'model.safetensors.index.json'
    single = root / 'model.safetensors'
    if index.is_file():
        if single.exists():
            raise AppError('Ambiguous checkpoint: both single-file weights and a shard index are present.')
        value = json_read(index)
        mapping = value.get('weight_map') if isinstance(value, dict) else None
        if not isinstance(mapping, dict) or not mapping:
            raise AppError('Invalid safetensors shard index.')
        names = sorted(set(mapping.values())) if all(isinstance(v, str) for v in mapping.values()) else []
        if not names or any(not name.endswith('.safetensors') for name in names):
            raise AppError('Invalid safetensors shard file names.')
    else:
        names, mapping = ['model.safetensors'], None
    tensors, hashes = {}, {}
    for name in names:
        path = owned_file(root, name)
        try:
            with safe_open(str(path), framework='numpy') as handle:
                for key in handle.keys():
                    if key in tensors or mapping is not None and mapping.get(key) != name:
                        raise AppError('Duplicate or incorrectly indexed model tensor.')
                    shape = handle.get_slice(key).get_shape()
                    if any(size <= 0 for size in shape):
                        raise AppError('Model contains an empty weight tensor.')
                    tensors[key] = shape
        except AppError:
            raise
        except Exception as error:
            raise AppError(f'Unreadable or incomplete safetensors weights: {name}') from error
        hashes[name] = file_digest(path)
    if not tensors or mapping is not None and set(mapping) != set(tensors):
        raise AppError('The model weight index does not describe complete local tensors.')
    if 'talker.model.codec_embedding.weight' not in tensors or not any(key.startswith('talker.model.layers.') for key in tensors):
        raise AppError('The checkpoint is missing the Qwen talker weights; tokenizer-only files are not a trained model.')
    return tensors, hashes


def verify_checkpoint_weights(root: Path, base: Path | None = None):
    tensors, hashes = model_tensors(root)
    if any(key.startswith('speaker_encoder') for key in tensors):
        raise AppError('The single-speaker checkpoint still contains Base speaker-encoder weights; it is not the expected trainer output.')
    if base is not None:
        original, base_hashes = model_tensors(base)
        expected = {key: shape for key, shape in original.items() if not key.startswith('speaker_encoder')}
        if any(tensors.get(key) != shape for key, shape in expected.items()):
            raise AppError('The trained checkpoint is missing weights or has shapes different from its frozen Base model.')
        if hashes == base_hashes:
            raise AppError('Checkpoint weights are byte-identical to the Base model. A configuration change is not fine-tuning.')
    return len(tensors)


def validate_trainer_projection(tree: ast.AST):
    for function in (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        direct, projected, consumed = False, False, False
        for node in ast.walk(function):
            if not isinstance(node, ast.Assign):
                continue
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            attributes = {part.attr for part in ast.walk(node.value) if isinstance(part, ast.Attribute)}
            if 'input_text_embedding' in names:
                direct |= 'text_embedding' in attributes
                projected |= 'text_projection' in attributes
            if 'input_embeddings' in names:
                consumed |= any(isinstance(part, ast.Name) and part.id == 'input_text_embedding' for part in ast.walk(node.value))
        if direct and consumed and not projected:
            raise AppError('Selected Qwen trainer directly adds unprojected text embeddings to codec embeddings. Use a reviewed trainer with text_projection in that path; no upstream source was modified.')
