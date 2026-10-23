from __future__ import annotations

import contextlib
import json
import math
import os
import shutil
import tempfile
from pathlib import Path

from rimsky.util import AppError, digest, file_digest, inside, os_metadata, write_json

MAX_JSON = 64 * 1024 * 1024
MAX_ROW_BYTES = 1024 * 1024
MAX_DATA_BYTES = 2 * 1024 * 1024 * 1024
MAX_DATA_ROWS = 100000
OFFLINE_ENV = {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
               'HF_DATASETS_OFFLINE': '1', 'DO_NOT_TRACK': '1',
               'WANDB_DISABLED': 'true', 'WANDB_MODE': 'disabled',
               'TOKENIZERS_PARALLELISM': 'false'}


def offline_environment():
    return {**os.environ, **OFFLINE_ENV}


def json_read(path: Path):
    if not path.is_file() or path.stat().st_size > MAX_JSON:
        raise AppError(f'Missing or oversized JSON file: {path.name}')
    try:
        return json.loads(path.read_text('utf-8'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    except (UnicodeError, ValueError) as error:
        raise AppError(f'Invalid JSON: {path.name}') from error


def rows_read(path: Path):
    if not path.is_file() or path.stat().st_size > MAX_JSON:
        raise AppError('Missing or oversized JSONL file.')
    result = []
    try:
        with path.open(encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
                    if not isinstance(row, dict):
                        raise ValueError('Expected objects')
                    result.append(row)
    except (UnicodeError, ValueError) as error:
        raise AppError('Invalid JSONL record.') from error
    return result


def iter_rows(path: Path):
    if not path.is_file() or path.stat().st_size > MAX_DATA_BYTES:
        raise AppError('Missing or oversized streaming dataset.')
    total = count = 0
    try:
        with path.open('rb') as handle:
            while raw := handle.readline(MAX_ROW_BYTES + 1):
                total += len(raw)
                if len(raw) > MAX_ROW_BYTES or total > MAX_DATA_BYTES:
                    raise AppError('Encoded dataset exceeds the row or file byte limit.')
                if not raw.strip():
                    continue
                row = json.loads(raw.decode('utf-8'), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
                count += 1
                if not isinstance(row, dict) or count > MAX_DATA_ROWS:
                    raise AppError('Invalid or oversized encoded dataset.')
                yield row
    except (UnicodeError, ValueError) as error:
        raise AppError('Invalid JSONL record.') from error


def rows_write(path: Path, rows):
    total = count = 0
    descriptor, name = tempfile.mkstemp(prefix=f'.{path.name}-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            for row in rows:
                raw = (json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
                total += len(raw)
                count += 1
                if len(raw) > MAX_ROW_BYTES or total > MAX_DATA_BYTES or count > MAX_DATA_ROWS:
                    raise AppError('Dataset exceeds the row, file or record-count limit.')
                handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def number(value, name, minimum=0., maximum=float('inf')):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise AppError(f'Invalid {name}.')
    return float(value)


def local_path(value, *, directory=False):
    if not isinstance(value, (str, Path)) or not str(value).strip() or '://' in str(value):
        raise AppError('An existing local filesystem path is required, not a model ID or URL.')
    path = Path(value).expanduser().resolve()
    if not (path.is_dir() if directory else path.is_file()):
        raise AppError(f'Local asset does not exist: {path}')
    return path


def owned_file(root: Path, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise AppError('Expected a relative corpus path.')
    raw = root / relative
    if any(part.is_symlink() for part in (raw, *raw.parents) if part != root.parent):
        raise AppError('Training assets must be ordinary local files, not symlinks.')
    result = inside(root, relative)
    if not result.is_file():
        raise AppError(f'Missing corpus asset: {relative}')
    return result


def tree_hashes(root: Path, excluded=()):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise AppError('Materialize local model files before training; symlinks are not accepted.')
        if path.is_file() and not os_metadata(path.name) and path.relative_to(root).as_posix() not in excluded:
            result[path.relative_to(root).as_posix()] = file_digest(path)
    return result


def verify_files(root: Path, manifest):
    if not isinstance(manifest, dict) or not manifest:
        raise AppError('Missing training asset hashes.')
    for name, expected in manifest.items():
        if not isinstance(expected, str) or file_digest(owned_file(root, name)) != expected:
            raise AppError(f'Training asset changed: {name}')


@contextlib.contextmanager
def new_directory(destination: Path):
    destination = destination.expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise AppError('Output already exists; use a new directory. Nothing was overwritten.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.rimsky-training-', dir=destination.parent) as tmp:
        stage = Path(tmp) / 'data'
        stage.mkdir()
        yield stage
        destination.mkdir()
        try:
            for item in stage.iterdir():
                item.rename(destination / item.name)
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            raise


def seal_json(root: Path, name: str, metadata):
    result = {**metadata, 'files': tree_hashes(root, (name,))}
    result['revision'] = digest(result)
    write_json(root / name, result)
    return result


def checked_manifest(root: Path, name: str):
    result = json_read(root / name)
    if not isinstance(result, dict) or result.get('schema') != 1:
        raise AppError('Unsupported training manifest.')
    if result.get('revision') != digest({k: v for k, v in result.items() if k != 'revision'}):
        raise AppError('Training manifest changed; rebuild it from reviewed inputs.')
    verify_files(root, result.get('files'))
    return result


@contextlib.contextmanager
def stage_workspace(output: Path, contract: dict, resume=False):
    from rimsky.instance import library_lease
    output = output.expanduser().absolute()
    if output.is_symlink():
        raise AppError('Stage directory cannot be a symbolic link.')
    if resume:
        if not output.is_dir():
            raise AppError('There is no preparation stage to resume.')
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            output.mkdir()
        except FileExistsError as error:
            raise AppError('Stage directory already exists. Use --resume only for a compatible interrupted preparation stage.') from error
    with library_lease(output):
        path = output / 'stage.json'
        if resume:
            previous = json_read(path)
            if previous.get('contract') != contract or previous.get('contract_sha256') != digest(contract):
                raise AppError('Stage inputs changed. Use a new output directory.')
        else:
            write_json(path, {'schema': 1, 'contract': contract, 'contract_sha256': digest(contract), 'status': 'preparing'})
        try:
            yield output
        except BaseException as error:
            write_json(path, {'schema': 1, 'contract': contract, 'contract_sha256': digest(contract),
                              'status': 'interrupted' if isinstance(error, KeyboardInterrupt) else 'failed',
                              'error': str(error)[:2000]})
            raise
        else:
            write_json(path, {'schema': 1, 'contract': contract, 'contract_sha256': digest(contract), 'status': 'complete'})
