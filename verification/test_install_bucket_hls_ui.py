import gzip
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bucket_hls_installer', ROOT / 'scripts/install_bucket_hls_ui.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class BucketHlsInstallTest(unittest.TestCase):
    def test_install_is_idempotent_preserves_answers_and_replaces_existing_gzip(self):
        with tempfile.TemporaryDirectory(prefix='dogma-bucket-ui-') as folder:
            board = Path(folder)
            js = (ROOT / 'scripts/book1_bucket_review.js').read_text(encoding='utf-8') + '\n' + (ROOT / 'scripts/book_bucket_split_controls.js').read_text(encoding='utf-8')
            (board / 'bucket-review.js').write_text(js, encoding='utf-8')
            (board / 'bucket-review.js.gz').write_bytes(gzip.compress(b'old UI'))
            (board / 'bucket-splits.js').write_bytes((ROOT / 'scripts/book_bucket_splits.js').read_bytes())
            (board / 'index.html').write_bytes((ROOT / 'scripts/book1_bucket_review.html').read_bytes())
            answers = b'{"schema":1,"decisions":{"protected":{"voice_verdict":"yes"}}}'
            (board / 'review-draft.json').write_bytes(answers)
            installer.install(board)
            before = {p.name: p.read_bytes() for p in board.iterdir()}
            installer.install(board)
            self.assertEqual(before, {p.name: p.read_bytes() for p in board.iterdir()})
            self.assertEqual((board / 'review-draft.json').read_bytes(), answers)
            self.assertEqual(gzip.decompress((board / 'bucket-review.js.gz').read_bytes()), (board / 'bucket-review.js').read_bytes())
            self.assertIn('bucket-hls.js', (board / 'index.html').read_text(encoding='utf-8'))
            self.assertIn('if(globalThis.BucketHls?.isHls(audio))return;', (board / 'bucket-review.js').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
