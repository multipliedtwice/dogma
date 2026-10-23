import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import torch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('train_higgs_local', ROOT / 'scripts/train_higgs_local.py')
TRAIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRAIN)
A = '\u0301'


class CounterfactualTextTests(unittest.TestCase):
    def test_accepts_one_stress_move_and_preserves_other_marks(self):
        correct = f'Ма{A}ма сказала, что он стои{A}т.'
        wrong = f'Ма{A}ма сказала, что он сто{A}ит.'
        self.assertEqual(TRAIN.validate_counterfactual_text(correct, wrong), (correct, wrong))
        self.assertEqual(TRAIN.validate_counterfactual_text(f'сё{A}ла', f'сёла{A}'),
                         (f'сё{A}ла', f'сёла{A}'))

    def test_rejects_changed_letters_cross_word_move_and_bad_mark(self):
        invalid = [
            (f'Он стои{A}т.', f'Он стро{A}ит.'),
            (f'Ма{A}ма идет.', f'Мама и{A}дет.'),
            (f'Он стои{A}т.', f'Он с{A}тоит.'),
            (f'Он стои{A}т.', f'Он сто{A}и{A}т.'),
            (f'Он стои{A}{A}т.', f'Он сто{A}и{A}т.'),
        ]
        for correct, wrong in invalid:
            with self.subTest(correct=correct, wrong=wrong), self.assertRaises(ValueError):
                TRAIN.validate_counterfactual_text(correct, wrong)

    def test_inventory_is_stable_and_counts_unpaired_marks(self):
        records = [
            {'id': 'a', 'text': f'Он стои{A}т.', 'counterfactual_text': f'Он сто{A}ит.'},
            {'id': 'b', 'text': f'Ма{A}ма.'},
            {'id': 'c', 'text': 'Дом.'},
        ]
        pairs, summary = TRAIN.counterfactual_inventory(records, 'counterfactual_text')
        self.assertEqual(pairs, {0: f'Он сто{A}ит.'})
        self.assertEqual(summary['provided_pairs'], 1)
        self.assertEqual(summary['marked_without_pair'], 1)
        self.assertEqual(len(summary['pairs_sha256']), 64)
        self.assertEqual(summary, TRAIN.counterfactual_inventory(records, 'counterfactual_text')[1])

    def test_atomic_report_leaves_no_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.json'
            TRAIN.atomic_write(path, {'status': 'running', 'слово': 'да'})
            self.assertIn('"слово": "да"', path.read_text(encoding='utf-8'))
            self.assertFalse(path.with_name('report.json.tmp').exists())


class _ToyModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.theta = torch.nn.Parameter(torch.tensor(0.35))

    def forward(self, branch, use_cache=False):
        # Random multiplier behaves like dropout and tests exact RNG replay.
        multiplier = 0.8 + torch.rand((), device=self.theta.device) * 0.4
        center = branch.float().mean()
        nll = ((self.theta * multiplier - center) ** 2 + 1.0).reshape(1, 1)
        base = ((self.theta * multiplier - 0.1) ** 2).reshape(())
        return SimpleNamespace(loss=base, all_sum_losses=nll,
                               all_token_nums=torch.ones_like(nll, dtype=torch.long))


class CounterfactualGradientTests(unittest.TestCase):
    def test_sequential_recompute_matches_joint_two_branch_gradient(self):
        correct = {'branch': torch.tensor([0.2])}
        wrong = {'branch': torch.tensor([1.3])}
        weight, margin = 0.3, 5.0

        expected = _ToyModel()
        torch.manual_seed(912)
        correct_output = expected(**correct)
        wrong_output = expected(**wrong)
        expected_gap = wrong_output.all_sum_losses[:, 0] - correct_output.all_sum_losses[:, 0]
        expected_objective = correct_output.loss + weight * torch.relu(margin - expected_gap).mean()
        expected_objective.backward()
        expected_gradient = expected.theta.grad.detach().clone()
        expected_rng = torch.random.get_rng_state().clone()

        actual = _ToyModel()
        torch.manual_seed(912)
        metrics = TRAIN.backward_counterfactual_pair(
            actual, correct, wrong, weight=weight, margin=margin, codebook=0,
            device_type='cpu', dtype=None)

        self.assertTrue(torch.allclose(actual.theta.grad, expected_gradient, atol=1e-6, rtol=1e-6))
        self.assertTrue(torch.equal(torch.random.get_rng_state(), expected_rng))
        self.assertEqual(metrics['pairs'], 1)
        self.assertEqual(metrics['active_pairs'], 1)
        self.assertAlmostEqual(metrics['gap_nats'], float(expected_gap.detach()), places=6)

    def test_inactive_hinge_keeps_only_correct_ce_gradient(self):
        correct = {'branch': torch.tensor([0.2])}
        wrong = {'branch': torch.tensor([3.0])}
        model = _ToyModel()
        torch.manual_seed(71)
        metrics = TRAIN.backward_counterfactual_pair(
            model, correct, wrong, weight=0.2, margin=0.01, codebook=0,
            device_type='cpu', dtype=None)
        self.assertEqual(metrics['active_pairs'], 0)
        self.assertEqual(metrics['hinge'], 0.0)
        self.assertTrue(torch.isfinite(model.theta.grad))


if __name__ == '__main__':
    unittest.main()
