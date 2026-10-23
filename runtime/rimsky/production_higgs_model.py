"""Fail-closed resolution of the one user-pinned production Higgs model."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from .util import AppError, file_digest

PIN_ENV = 'RIMSKY_HIGGS_MODEL'
DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / 'resources/production-higgs-model.json'
SHA256 = re.compile(r'[a-f0-9]{64}')


@dataclass(frozen=True)
class ProductionHiggsModel:
    checkpoint: str
    path: Path
    sha256: str
    precision: str
    provenance_path: Path
    provenance: dict
    manifest_path: Path


def require_production_higgs_engine(value: str | None) -> str:
    """Reject legacy engine switches at production-renderer entry points."""
    engine = value or 'higgs'
    if engine != 'higgs':
        raise AppError('Production narration requires the pinned Higgs checkpoint; RIMSKY_TTS_ENGINE must be higgs.')
    return engine


def _inside(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise AppError(f'Pinned Higgs {label} path must be relative to the project root.')
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise AppError(f'Pinned Higgs {label} path leaves the project root.') from error
    return path


def resolve_production_higgs_model(
    root: Path | None = None,
    override: str | os.PathLike[str] | None = None,
    manifest_path: Path | None = None,
) -> ProductionHiggsModel:
    """Resolve and hash the pinned model; never fall back to another checkpoint.

    ``RIMSKY_HIGGS_MODEL`` may point at a relocated copy, but that copy must have
    the exact pinned SHA-256. Provenance always comes from the immutable manifest.
    """
    root = (root or Path(__file__).resolve().parents[2]).resolve()
    manifest_path = (manifest_path or root / 'resources/production-higgs-model.json').resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise AppError('Pinned production Higgs manifest is missing or invalid.') from error
    model = manifest.get('model')
    provenance_pin = manifest.get('provenance')
    checkpoint = manifest.get('checkpoint')
    if (manifest.get('schema') != 1 or not isinstance(checkpoint, str) or not checkpoint
            or not isinstance(model, dict) or not isinstance(provenance_pin, dict)):
        raise AppError('Pinned production Higgs manifest has an unsupported schema.')
    expected = model.get('sha256')
    proof_hash = provenance_pin.get('sha256')
    adapter_hash = provenance_pin.get('fine_tuning_adapter_sha256')
    dataset_hash = provenance_pin.get('dataset_sha256')
    if not all(isinstance(value, str) and SHA256.fullmatch(value)
               for value in (expected, proof_hash, adapter_hash, dataset_hash)):
        raise AppError('Pinned production Higgs manifest has invalid checksums.')

    canonical = _inside(root, model.get('path'), 'model')
    selected_value = override if override is not None else os.environ.get(PIN_ENV)
    if selected_value:
        selected = Path(selected_value).expanduser()
        if not selected.is_absolute():
            selected = root / selected
        selected = selected.resolve()
    else:
        selected = canonical
    if not selected.is_file():
        raise AppError(f'Pinned production Higgs model is missing: {selected}')
    actual = file_digest(selected)
    if actual != expected:
        raise AppError(
            f'Production Higgs model checksum mismatch: expected {expected}, got {actual}. '
            'Fallback to an older or stock checkpoint is forbidden.'
        )

    provenance_path = _inside(root, provenance_pin.get('path'), 'provenance')
    if not provenance_path.is_file() or file_digest(provenance_path) != proof_hash:
        raise AppError('Pinned production Higgs provenance is missing or has changed.')
    try:
        provenance = json.loads(provenance_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise AppError('Pinned production Higgs provenance is invalid.') from error
    if (not isinstance(provenance, dict) or provenance.get('model_sha256') != expected
            or provenance.get('checkpoint_sha256') != adapter_hash
            or provenance.get('dataset_sha256') != dataset_hash):
        raise AppError('Pinned production Higgs provenance does not describe the selected checkpoint.')
    if model.get('precision') != provenance.get('precision'):
        raise AppError('Pinned production Higgs precision disagrees with its provenance.')
    return ProductionHiggsModel(checkpoint, selected, expected, model['precision'],
                                provenance_path, provenance, manifest_path)
