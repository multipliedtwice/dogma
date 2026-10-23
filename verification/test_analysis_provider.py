from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'runtime'))

from rimsky.packs import resolve_character_analysis
from rimsky.util import ProviderUnavailableError


class AnalysisProviderTests(unittest.TestCase):
    def test_explicit_choice_is_preserved(self):
        with patch('rimsky.packs.trusted_executable', return_value='installed'):
            self.assertEqual(resolve_character_analysis('local'), 'local')
            self.assertEqual(resolve_character_analysis('codex_cli'), 'codex_cli')

    def test_auto_prefers_codex_then_claude(self):
        def both(names, **options):
            return next(iter(names))

        with patch('rimsky.packs.trusted_executable', side_effect=both):
            self.assertEqual(resolve_character_analysis('auto'), 'codex_cli')

        with patch('rimsky.packs.trusted_executable', side_effect=lambda names, **options:
                   (_ for _ in ()).throw(ProviderUnavailableError('missing')) if 'codex' in names else next(iter(names))):
            self.assertEqual(resolve_character_analysis('auto'), 'claude_cli')

    def test_auto_uses_local_only_without_clis(self):
        with patch('rimsky.packs.trusted_executable', side_effect=ProviderUnavailableError('missing')):
            self.assertEqual(resolve_character_analysis('auto'), 'local')


if __name__ == '__main__':
    unittest.main()
