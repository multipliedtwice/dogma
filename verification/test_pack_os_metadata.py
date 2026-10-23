from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rimsky.packs import load_pack
from rimsky.util import AppError, file_digest, write_json
from seal_pack import seal
from test_core import pack_fixture

METADATA = ('.DS_Store', '._model.gguf', 'tts/Thumbs.db', 'tts/desktop.ini', 'tts/Desktop.ini')


class PackOsMetadata(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / 'pack'
        self.pack = pack_fixture(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, data=b'metadata'):
        (self.root / relative).write_bytes(data)

    def test_os_metadata_added_after_sealing_keeps_the_pinned_revision(self):
        for relative in METADATA:
            self.write(relative)
        self.assertEqual(self.pack['_fingerprint'], load_pack(str(self.root), verify=True)['_fingerprint'])

    def test_real_files_stay_sealed(self):
        self.write('tts/extra.json', b'{}')
        with self.assertRaisesRegex(AppError, 'Pack contents changed'):
            load_pack(str(self.root), verify=True)
        (self.root / 'tts/extra.json').unlink()
        self.write('model.gguf', b'changed weights')
        with self.assertRaisesRegex(AppError, 'checksum mismatch'):
            load_pack(str(self.root), verify=True)

    def test_seal_skips_os_metadata(self):
        for relative in METADATA:
            self.write(relative)
        seal(self.root)
        files = json.loads((self.root / 'pack.json').read_text('utf-8'))['files']
        self.assertEqual({'model.gguf', 'tts/config.json'}, set(files))

    def test_metadata_listed_by_an_older_seal_is_ignored(self):
        self.write('.DS_Store')
        manifest = json.loads((self.root / 'pack.json').read_text('utf-8'))
        manifest['files']['.DS_Store'] = file_digest(self.root / '.DS_Store')
        write_json(self.root / 'pack.json', manifest)
        pinned = load_pack(str(self.root), verify=True)['_fingerprint']
        self.write('.DS_Store', b'rewritten by Finder')
        self.assertEqual(pinned, load_pack(str(self.root), verify=True)['_fingerprint'])
        (self.root / '.DS_Store').unlink()
        self.assertEqual(pinned, load_pack(str(self.root), verify=True)['_fingerprint'])


if __name__ == '__main__':
    unittest.main()
