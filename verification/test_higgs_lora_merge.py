import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from export_higgs_training import read, sha, write
from merge_higgs_lora import merge


class MergeTest(unittest.TestCase):
    def test_tensorwise_delta_and_frozen_codec_round_trip(self):
        import torch
        from safetensors.torch import load_file, save_file
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, adapter, native = root / 'base.safetensors', root / 'adapter', root / 'config'
            adapter.mkdir()
            native.mkdir()
            for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'chat_template.jinja'):
                (native / name).write_text('{}')
            target = 'body.layers.0.self_attn.q_proj.weight'
            codec = 'tied.embedding.modality_embeddings.0.model.test.weight'
            weight, a, b = torch.arange(6.).reshape(2, 3), torch.ones(1, 3), torch.tensor([[2.], [3.]])
            save_file({target: weight, codec: torch.tensor([7., 9.]), 'tied.head.text_head.weight': torch.zeros(1)}, str(base))
            prefix = 'base_model.model.model.layers.0.self_attn.q_proj'
            save_file({prefix + '.lora_A.weight': a, prefix + '.lora_B.weight': b}, str(adapter / 'adapter_model.safetensors'))
            write(adapter / 'adapter_config.json', {'peft_type': 'LORA', 'r': 1, 'lora_alpha': 2})
            report = root / 'training-report.json'
            write(report, {'status': 'trained_pending_validation', 'checkpoint_sha256': sha(adapter / 'adapter_model.safetensors'),
                           'optimizer_steps': 3, 'dataset_sha256': 'a' * 64})
            output = root / 'merged'
            proof = merge(base, adapter, native, output, report, max_shard_bytes=16)
            index = read(output / 'model.safetensors.index.json')['weight_map']
            self.assertEqual({target, codec}, set(index))
            self.assertTrue(torch.equal(weight + 2 * (b @ a), load_file(str(output / index[target]))[target]))
            self.assertTrue(torch.equal(torch.tensor([7., 9.]), load_file(str(output / index[codec]))[codec]))
            self.assertEqual([target], proof['modified_tensors'])
            self.assertEqual(sha(base), proof['base_sha256'])
            with self.assertRaisesRegex(ValueError, 'immutable'):
                merge(base, adapter, native, output, report)

    def test_sharded_checkpoint_directory(self):
        import hashlib
        import torch
        from safetensors.torch import load_file, save_file
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, adapter, native = root / 'base', root / 'adapter', root / 'config'
            base.mkdir(); adapter.mkdir(); native.mkdir()
            for name in ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'chat_template.jinja'):
                (native / name).write_text('{}')
            target = 'body.layers.0.self_attn.q_proj.weight'
            codec = 'tied.embedding.test.weight'
            save_file({target: torch.zeros(2, 2)}, str(base / 'model-00001.safetensors'))
            save_file({codec: torch.ones(2)}, str(base / 'model-00002.safetensors'))
            write(base / 'model.safetensors.index.json', {'weight_map': {
                target: 'model-00001.safetensors', codec: 'model-00002.safetensors'}})
            prefix = 'base_model.model.model.layers.0.self_attn.q_proj'
            save_file({prefix + '.lora_A.weight': torch.ones(1, 2),
                       prefix + '.lora_B.weight': torch.ones(2, 1)},
                      str(adapter / 'adapter_model.safetensors'))
            write(adapter / 'adapter_config.json', {'peft_type': 'LORA', 'r': 1, 'lora_alpha': 1})
            report = root / 'training-report.json'
            write(report, {'status': 'trained_pending_validation',
                           'checkpoint_sha256': sha(adapter / 'adapter_model.safetensors'),
                           'optimizer_steps': 1, 'dataset_sha256': 'b' * 64})
            output = root / 'merged'
            proof = merge(base, adapter, native, output, report, max_shard_bytes=16)
            index = read(output / 'model.safetensors.index.json')['weight_map']
            self.assertTrue(torch.equal(torch.ones(2, 2),
                                        load_file(str(output / index[target]))[target]))
            files = {name: sha(base / name) for name in (
                'model.safetensors.index.json', 'model-00001.safetensors', 'model-00002.safetensors')}
            expected = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            self.assertEqual(proof['base_sha256'], expected)
            self.assertEqual(proof['base_files'], files)


if __name__ == '__main__':
    unittest.main()
