from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rimsky.packs as packs
from server import Application
from test_core import pack_fixture


class PackVerificationCache(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        pack_fixture(self.root / 'pack')
        pack_fixture(self.root / 'other-pack')
        self.app = Application(self.root / 'library', self.root / 'bin')
        self.app.dispatch('update_settings', {'pack_path': str(self.root / 'pack')})
        self.verified = []
        problem = packs.pack_problem

        def counting(pack, *args, **kwargs):
            self.verified.append(Path(pack['_root']).name)
            return problem(pack, *args, **kwargs)
        self.patch = patch.object(packs, 'pack_problem', counting)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.app.close()
        self.temporary.cleanup()

    def health(self):
        return self.app.dispatch('health', {})

    def test_reading_and_playback_settings_do_not_rehash_the_pack(self):
        self.health()
        self.assertEqual(['pack'], self.verified)
        for change in ({'volume': .5}, {'playback_rate': 1.5}, {'theme': 'dark'}, {'follow': False}, {'device': 'cpu'}):
            self.app.dispatch('update_settings', change)
            self.assertEqual('', self.health()['pack_error'])
        self.assertEqual(['pack'], self.verified)

    def test_changing_the_pack_path_verifies_the_new_pack(self):
        self.health()
        self.app.dispatch('update_settings', {'pack_path': str(self.root / 'other-pack')})
        self.health()
        self.assertEqual(['pack', 'other-pack'], self.verified)


if __name__ == '__main__':
    unittest.main()
