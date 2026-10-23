from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from rimsky.production_higgs_model import require_production_higgs_engine, resolve_production_higgs_model
from rimsky.util import AppError


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ProductionHiggsModelTests(unittest.TestCase):
    def test_production_renderer_rejects_legacy_engine_switches(self):
        self.assertEqual(require_production_higgs_engine(None), 'higgs')
        self.assertEqual(require_production_higgs_engine('higgs'), 'higgs')
        with self.assertRaisesRegex(AppError, 'pinned Higgs checkpoint'):
            require_production_higgs_engine('qwen')

    def test_legacy_book_renderers_use_the_resolver_not_stock_higgs(self):
        for name in ('render_book1_higgs_demo.py', 'render_book1_higgs_half.py'):
            source = (ROOT / 'scripts' / name).read_text(encoding='utf-8')
            self.assertIn('resolve_production_higgs_model(ROOT)', source)
            self.assertNotIn('Higgs-Audio-v3-TTS-4B-GGUF', source)

    def test_production_renderer_has_no_qwen_selector(self):
        source = (ROOT / 'scripts/render_higgs_book_trial.py').read_text(encoding='utf-8')
        self.assertIn('require_production_higgs_engine', source)
        self.assertNotIn('QWEN_MODEL', source)
        self.assertNotIn("ENGINE=='qwen'", source)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'resources').mkdir()
        (self.root / 'models').mkdir()
        self.model_bytes = b'pinned model fixture'
        self.model_sha = sha(self.model_bytes)
        (self.root / 'models/model.gguf').write_bytes(self.model_bytes)
        self.adapter_sha = 'a' * 64
        self.dataset_sha = 'b' * 64
        proof = {'model_sha256': self.model_sha, 'checkpoint_sha256': self.adapter_sha,
                 'dataset_sha256': self.dataset_sha, 'precision': 'q8_0'}
        proof_bytes = json.dumps(proof, sort_keys=True).encode()
        (self.root / 'models/model.provenance.json').write_bytes(proof_bytes)
        manifest = {'schema': 1, 'checkpoint': 'fixture-v1',
                    'model': {'path': 'models/model.gguf', 'sha256': self.model_sha, 'precision': 'q8_0'},
                    'provenance': {'path': 'models/model.provenance.json', 'sha256': sha(proof_bytes),
                                   'fine_tuning_adapter_sha256': self.adapter_sha,
                                   'dataset_sha256': self.dataset_sha}}
        (self.root / 'resources/production-higgs-model.json').write_text(
            json.dumps(manifest), encoding='utf-8')

    def resolve(self, override=None):
        return resolve_production_higgs_model(self.root, override=override)

    def test_resolves_only_the_hash_bound_checkpoint(self):
        selected = self.resolve()
        self.assertEqual(selected.checkpoint, 'fixture-v1')
        self.assertEqual(selected.path, (self.root / 'models/model.gguf').resolve())
        self.assertEqual(selected.sha256, self.model_sha)

    def test_environment_override_may_relocate_only_identical_bytes(self):
        copy = self.root / 'relocated.gguf'
        copy.write_bytes(self.model_bytes)
        with patch.dict(os.environ, {'RIMSKY_HIGGS_MODEL': str(copy)}):
            self.assertEqual(self.resolve().path, copy.resolve())
        copy.write_bytes(b'an older checkpoint')
        with patch.dict(os.environ, {'RIMSKY_HIGGS_MODEL': str(copy)}):
            with self.assertRaisesRegex(AppError, 'checksum mismatch'):
                self.resolve()

    def test_changed_provenance_fails_closed(self):
        path = self.root / 'models/model.provenance.json'
        path.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(AppError, 'provenance'):
            self.resolve()

    def test_missing_model_does_not_fall_back(self):
        (self.root / 'models/model.gguf').unlink()
        with self.assertRaisesRegex(AppError, 'missing'):
            self.resolve()


if __name__ == '__main__':
    unittest.main()
