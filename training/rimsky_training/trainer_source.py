from __future__ import annotations

import ast
import shutil
from pathlib import Path

from rimsky.util import AppError, file_digest, write_json
from .common import local_path, new_directory, verify_files
from .model_contract import validate_trainer_projection

BEFORE = 'model.talker.model.text_embedding(input_text_ids) * text_embedding_mask'
AFTER = 'model.talker.text_projection(model.talker.model.text_embedding(input_text_ids)) * text_embedding_mask'


def corrected_projection(source: str):
    try:
        tree = ast.parse(source)
    except (ValueError, SyntaxError) as error:
        raise AppError('Cannot parse the local Qwen trainer.') from error
    expected = ast.dump(ast.parse(BEFORE, mode='eval').body)
    candidates = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'input_text_embedding' for target in node.targets)]
    if len(candidates) != 1:
        raise AppError('Trainer layout is not the supported single text-embedding assignment; manual compatibility review is required.')
    value = candidates[0].value
    if ast.dump(value) == ast.dump(ast.parse(AFTER, mode='eval').body):
        return source, False
    if ast.dump(value) != expected:
        raise AppError('Unknown text-embedding expression; the compatibility repair refuses to guess.')
    lines = source.encode('utf-8').splitlines(keepends=True)
    start = sum(map(len, lines[:value.lineno-1])) + value.col_offset
    end = sum(map(len, lines[:value.end_lineno-1])) + value.end_col_offset
    data = source.encode('utf-8')
    corrected = (data[:start] + AFTER.encode('utf-8') + data[end:]).decode('utf-8')
    validate_trainer_projection(ast.parse(corrected))
    return corrected, True


def prepare_trainer(source_path: Path, output: Path):
    source = local_path(source_path, directory=True)
    destination = output.expanduser().absolute().resolve()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise AppError('Prepare the trainer in a new directory outside the selected upstream checkout.')
    relatives = ('finetuning/sft_12hz.py', 'finetuning/dataset.py', 'LICENSE')
    hashes = {}
    for name in relatives:
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise AppError('The selected checkout lacks an ordinary required Qwen trainer file: ' + name)
        hashes[name] = file_digest(path)
    original = (source / relatives[0]).read_bytes().decode('utf-8')
    corrected, changed = corrected_projection(original)
    with new_directory(output) as stage:
        (stage / 'finetuning').mkdir()
        for name in relatives:
            shutil.copyfile(source / name, stage / name)
        verify_files(source, hashes)
        verify_files(stage, hashes)
        (stage / relatives[0]).write_bytes(corrected.encode('utf-8'))
        notice = {'schema': 1, 'adapter': 'qwen3-tts-text-projection-v1',
                  'source': str(source), 'source_files': hashes,
                  'prepared_files': {name: file_digest(stage / name) for name in relatives},
                  'text_projection_added': changed,
                  'scope': 'Only the audited text projection expression. Optimizer, loss, dataset and checkpoint saving are upstream code.',
                  'real_training_verified': False}
        write_json(stage / 'rimsky-trainer-provenance.json', notice)
        from .runner import upstream_files
        upstream_files(stage)
    return {'path': str(destination), 'text_projection_added': changed,
            'source_unchanged': True, 'real_training_verified': False}
