from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from rimsky.jobs import Jobs
from rimsky.store import Store
from rimsky.util import AppError, now


def manifest(book_id='book1'):
    return {
        'id': book_id,
        'title': 'Book',
        'author': 'Author',
        'filename': 'book.fb2',
        'language': 'ru',
        'format': 'fb2',
        'characters': 12,
        'cover': None,
        'chapters': [{'id': 'c1', 'title': 'One', 'source': '', 'blocks': [
            {'id': 'p1', 'kind': 'paragraph', 'text': 'First', 'html': '<p>First</p>', 'source': '', 'assets': []},
            {'id': 'p2', 'kind': 'paragraph', 'text': 'Second', 'html': '<p>Second</p>', 'source': '', 'assets': []},
        ]}],
        'assets': [],
        'warnings': [],
    }


class DefectRepairs(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))
        book = manifest()
        self.store.execute('INSERT INTO books VALUES(?,?,?,?,?)', ('book1', 'Book', 'Author', now(), json.dumps(book)))

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_reading_and_playback_positions_are_independent(self):
        self.store.save_reading_position('book1', {'chapter_id': 'c1', 'source_id': 'p2', 'offset': 88})
        self.store.save_position('book1', {'chapter_id': 'c1', 'source_id': 'p1', 'offset': 0})
        book = self.store.book('book1')
        self.assertEqual('p2', book['reading_position']['source_id'])
        self.assertEqual('p1', book['position']['source_id'])

    def test_bookmark_is_idempotent_and_note_is_editable(self):
        first = self.store.bookmark('book1', 'p1')
        second = self.store.bookmark('book1', 'p1')
        self.assertEqual(first['id'], second['id'])
        updated = self.store.update_bookmark(first['id'], 'Return here')
        self.assertEqual('Return here', updated['note'])
        self.assertEqual(1, len(self.store.book('book1')['bookmarks']))

    def test_deleting_parent_clears_revision_provenance(self):
        config = json.dumps({'chapters': ['c1']})
        self.store.execute('INSERT INTO generations(id,book_id,name,created,updated,status,config) VALUES(?,?,?,?,?,?,?)',
                           ('g1', 'book1', 'Original', now(), now(), 'ready', config))
        self.store.execute('INSERT INTO generations(id,book_id,name,created,updated,status,config,parent_id) VALUES(?,?,?,?,?,?,?,?)',
                           ('g2', 'book1', 'Revision', now(), now(), 'ready', config, 'g1'))
        self.store.delete_generation('g1')
        self.assertIsNone(self.store.generation('g2')['parent_id'])

    def test_export_can_be_removed_without_deleting_generation(self):
        config = json.dumps({'chapters': ['c1']})
        self.store.execute('INSERT INTO generations(id,book_id,name,created,updated,status,config) VALUES(?,?,?,?,?,?,?)',
                           ('g1', 'book1', 'Generation', now(), now(), 'ready', config))
        path = self.store.root / 'exports' / 'saved.srt'
        path.write_text('saved', encoding='utf-8')
        self.store.execute('INSERT INTO exports VALUES(?,?,?,?,?)', ('e1', 'g1', 'saved.srt', 'srt', now()))
        result = self.store.delete_export('e1')
        self.assertTrue(result['deleted'])
        self.assertFalse(path.exists())
        self.assertEqual([], self.store.generation('g1')['exports'])

    def test_dead_aligner_stops_alignment_and_keeps_passage_subtitles(self):
        config = json.dumps({'chapters': ['c1'], 'align_words': True})
        self.store.execute('INSERT INTO generations(id,book_id,name,created,updated,status,config,analysis) VALUES(?,?,?,?,?,?,?,?)',
                           ('g1', 'book1', 'Generation', now(), now(), 'aligning', config, json.dumps({'complete': True})))
        self.store.execute('INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken,status,audio_hash,duration) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           ('g1', 's1', 1, 'c1', 'p1', 0, 5, 'First', 'First', 'ready', 'a' * 64, 1.0))

        class Process:
            def poll(self):
                return 1

        class Aligner:
            def __init__(self, *args, **kwargs):
                self.process = Process()

            def call(self, *args, **kwargs):
                raise AppError('aligner crashed')

            def close(self):
                return None

        jobs = Jobs(self.store, Path(self.temporary.name))
        pack = {'_root': self.temporary.name, 'aligner': {'path': '.'}, 'files': {}, '_stats': {}}
        with patch('rimsky.jobs.resolve_binary', return_value='aligner'), patch('rimsky.jobs.Engine', Aligner):
            jobs._align('g1', pack, 'ru', {'device': 'cpu'})
        generation = self.store.generation('g1')
        self.assertEqual('Word alignment stopped because the local aligner exited; passage subtitles are kept.', generation['segments'][0]['warning'])
        self.assertEqual(['a' * 64], generation['analysis']['alignment_failed'])

    def test_fb2_block_count_is_bounded_before_extraction(self):
        raw = b'<FictionBook><body><section><p>one</p><p>two</p></section></body></FictionBook>'
        with patch('rimsky.books.MAX_BLOCKS', 1), self.assertRaisesRegex(AppError, '100,000-passage'):
            self.store.import_data(raw, 'large.fb2')

    def test_fb2_metadata_is_bounded(self):
        raw = ('<FictionBook><description><title-info><book-title>' + 'T' * 800 +
               '</book-title><author><first-name>' + 'A' * 1200 +
               '</first-name></author><lang>' + 'r' * 150 +
               '</lang></title-info></description><body><section><p>text</p></section></body></FictionBook>').encode()
        book = self.store.import_data(raw, 'metadata.fb2')
        self.assertEqual(500, len(book['title']))
        self.assertEqual(1000, len(book['author']))
        self.assertEqual(100, len(book['language']))


if __name__ == '__main__':
    unittest.main()
