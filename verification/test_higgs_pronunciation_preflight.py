import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'runtime')]
from render_higgs_book_trial import require_bound_pronunciation


class PronunciationPreflightTests(unittest.TestCase):
    def test_v4_requires_every_frozen_decision_to_bind(self):
        exact = {
            'policy': 'stress-rare-v4',
            'homograph_overrides': {'unbound': []},
            'yo_restoration': {'unbound': []},
        }
        require_bound_pronunciation('joined-vse', exact)

        drifted = {
            **exact,
            'yo_restoration': {
                'unbound': [{'word': 'все', 'reason': 'no decision'}],
            },
        }
        with self.assertRaisesRegex(ValueError, 'joined-vse.*все'):
            require_bound_pronunciation('joined-vse', drifted)

    def test_other_policies_keep_their_existing_behavior(self):
        require_bound_pronunciation('legacy', {
            'policy': 'stress-rare-v3',
            'homograph_overrides': {'unbound': [{'word': 'продал'}]},
        })


if __name__ == '__main__':
    unittest.main()
