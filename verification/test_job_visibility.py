from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rimsky.util import AppError
from rimsky.visuals import save_visual
from server import Application
from test_core import FB2


def png():
    output = io.BytesIO()
    Image.new('RGB', (8, 8), (20, 40, 60)).save(output, format='PNG')
    return output.getvalue()


class JobVisibility(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.app = Application(self.root / 'library', self.root)

    def tearDown(self):
        self.app.close()
        self.temporary.cleanup()

    def test_health_names_the_book_that_owns_active_narration_and_illustrations(self):
        book = self.app.store.import_data(FB2, 'fixture.fb2')
        generation = self.app.store.create_generation(book['id'], {}, chapters=[book['chapters'][0]['id']])
        idle = self.app.dispatch('health', {})
        self.assertIsNone(idle['active_book'])
        self.assertIsNone(idle['illustration_book'])
        self.app.jobs.active = generation['id']
        try:
            busy = self.app.dispatch('health', {})
        finally:
            self.app.jobs.active = None
        self.assertEqual({'id': book['id'], 'title': book['title']}, busy['active_book'])
        self.app.store.execute('INSERT INTO illustration_runs (id, book_id, created, updated, status, state) VALUES (?,?,?,?,?,?)',
                               ('run-1', book['id'], 'now', 'now', 'running', '{}'))
        for run, expected in (('run-1', {'id': book['id'], 'title': book['title']}), ('missing-run', None)):
            self.app.illustrations.active = run
            try:
                self.assertEqual(expected, self.app.dispatch('health', {})['illustration_book'])
            finally:
                self.app.illustrations.active = None

    def test_deleting_an_image_from_the_running_illustration_job_is_rejected_atomically(self):
        book = self.app.store.import_data(FB2, 'fixture.fb2')
        paragraphs = [block['id'] for block in book['chapters'][0]['blocks'] if block['kind'] == 'paragraph']
        protected = save_visual(self.app.store, book['id'], paragraphs[0], png())
        other = save_visual(self.app.store, book['id'], paragraphs[1], png(), kind='imported')
        state = {'items': [{'source_id': paragraphs[0], 'status': 'ready', 'visual_id': protected['id'], 'error': ''}]}
        self.app.store.execute('INSERT INTO illustration_runs (id, book_id, created, updated, status, state) VALUES (?,?,?,?,?,?)',
                               ('run-1', book['id'], 'now', 'now', 'running', json.dumps(state)))
        self.app.illustrations.active = 'run-1'
        try:
            with self.assertRaises(AppError) as rejected:
                self.app.dispatch('delete_visual', {'id': protected['id']})
            self.assertEqual('illustration_run_active', rejected.exception.code)
            self.assertFalse(self.app.illustrations.stop.is_set())
            self.assertEqual(1, len(self.app.store.rows('SELECT id FROM visuals WHERE id=?', (protected['id'],))))
            self.assertEqual({'deleted': True, 'cleanup_warnings': []}, self.app.dispatch('delete_visual', {'id': other['id']}))
        finally:
            self.app.illustrations.active = None
        self.assertEqual({'deleted': True, 'cleanup_warnings': []}, self.app.dispatch('delete_visual', {'id': protected['id']}))


if __name__ == '__main__':
    unittest.main()
