from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))

from rimsky.util import AppError
from server import dev_session_token, dev_session_aliases


class DevSession(unittest.TestCase):
    def test_local_dev_token_survives_restart(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = dev_session_token(root)
            self.assertEqual(first, dev_session_token(root))
            self.assertGreaterEqual(len(first), 40)

    def test_invalid_saved_token_is_rejected(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.dev-session-token').write_text('invalid')
            with self.assertRaises(AppError):
                dev_session_token(root)

    def test_previous_local_tab_token_is_retained(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = 'a' * 43
            (root / 'runtime.stdout.old.log').write_text('{"token":"' + previous + '"}\n', encoding='utf-8')
            (root / 'runtime.stdout.bad.log').write_text('not json\n', encoding='utf-8')
            self.assertEqual((previous,), dev_session_aliases(root, 'b' * 43))
