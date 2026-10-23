from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]

from build_listener_yo_corpus import build

GENERATION_ID = 'g1'
FOLDER = 'preview-gen'
MAR = 'Ты предлагаешь устроить капитальный рейд мародерки?'
MAR_HEARD = 'Ты предлагаешь устроить капитальный рейд мароде́рки?'
VSE = 'А этих уродов там тысячи, и все голодные.'
VSE_HEARD = 'А этих уродов там тысячи, и всё голодные.'
STRESS_ONLY = 'Он увидел батька рядом.'
COLUMNS = ('id', 'book_id', 'generation_id', 'segment_id', 'chapter_id', 'source_id', 'selected_text', 'text_offset', 'audio_offset',
           'audio_hash', 'passage_text', 'spoken_text', 'created', 'kind', 'comment', 'intended_stress', 'synthesis_snapshot')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), 'utf8')


def instruction(segment, source, heard, start, report_id):
    return {'schema': 1, 'native_text': heard, 'listener_report': {'id': report_id},
            'before': {'unit': {'id': segment, 'text': source, 'start': start, 'synthesis': {'text': heard}}}}


class Fixture:
    def __init__(self, root):
        self.root = Path(root); self.folder = self.root / FOLDER; self.database = self.root / 'lib' / 'library.sqlite3'
        self.database.parent.mkdir(parents=True)
        self.db = sqlite3.connect(self.database)
        self.db.execute('CREATE TABLE generations (id TEXT PRIMARY KEY, analysis TEXT)')
        self.db.execute(f'CREATE TABLE stress_reports ({", ".join(COLUMNS)})')
        self.db.execute('INSERT INTO generations VALUES (?, ?)', (GENERATION_ID, json.dumps({'external_preview': {'generation': FOLDER}})))
        write(self.folder / 'manifest.json', {'pronunciation': {'policy': 'stress-rare-v3'}})
        self.n = 0

    def report(self, kind, segment, source, word, intended='', comment='', created=None, offset=7, heard=None, derived=None, queue=None):
        self.n += 1; rid = f'r{self.n:02d}'; start = 100
        passage = 'Предыдущая фраза. ' + source
        text_offset = start + source.index(word)
        self.db.execute(f'INSERT INTO stress_reports VALUES ({",".join("?" * len(COLUMNS))})',
                        (rid, 'b1', GENERATION_ID, segment, 'c0001', 'p1', word, text_offset, 1.0, 'h-' + segment, passage, source,
                         created or f'2026-10-23T00:00:{self.n:02d}Z', kind, comment, intended or None, None))
        queue_dir = self.folder / 'listener-stress-queue'
        if heard:write(queue_dir / 'instructions' / f'{rid}.json', instruction(segment, source, heard, start, rid))
        if derived:write(queue_dir / 'derived' / f'{rid}.json', {'schema': 1, 'report_id': rid, 'original_kind': kind, 'listener_comment': comment,
                                                                'interpretation': 'Agent reading of the note.', 'intended_stress': derived})
        if queue:write(queue_dir / f'{rid}.json', {'id': rid, 'status': queue, 'report': {'id': rid, 'segment_id': segment}, 'replacement_audio_sha256': 'abc'})
        self.db.commit()
        return rid


def read_lines(path):
    return [json.loads(line) for line in Path(path).read_text('utf8').splitlines() if line.strip()]


class ListenerYoCorpusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.tmp.name); self.addCleanup(self.tmp.cleanup); self.addCleanup(self.fx.db.close); self.out = Path(self.tmp.name) / 'out'

    def build(self):
        return build(self.fx.database, self.out)

    def cases(self):
        return {c['id']: c for c in read_lines(self.out / 'cases.jsonl')}

    def test_explicit_missed_yo(self):
        rid = self.fx.report('stress', 's1', MAR, 'мародерки', 'мародёрки', heard=MAR_HEARD, queue='applied')
        manifest = self.build(); case = self.cases()[rid]
        self.assertEqual('listener_explicit', case['label_source']); self.assertEqual('missed_yo', case['direction'])
        self.assertEqual('мародёрки', case['target_form']); self.assertEqual('мароде́рки', case['heard_word'])
        self.assertEqual('stress-rare-v3', case['pronunciation_policy']); self.assertEqual(FOLDER, case['generation'])
        self.assertEqual(MAR, case['sentence']); self.assertEqual(MAR.index('мародерки'), case['sentence_offset'])
        self.assertEqual({'status': 'applied', 'replacement_audio_sha256': 'abc'}, case['retake'])
        self.assertEqual({'listener_explicit': 1}, manifest['counts']['by_label_source'])

    def test_explicit_false_yo(self):
        rid = self.fx.report('stress', 's2', VSE, 'все', 'все́', heard=VSE_HEARD)
        self.build(); case = self.cases()[rid]
        self.assertEqual('false_yo', case['direction']); self.assertEqual('все', case['target_form'])
        self.assertEqual('все́', case['target_stressed']); self.assertEqual('всё', case['heard_word'])
        self.assertEqual(VSE_HEARD, case['heard_native_text'])

    def test_free_text_with_derived_record_is_interpreted(self):
        rid = self.fx.report('other', 's1', MAR, 'мародерки', comment='Должно быть ё', heard=MAR_HEARD, derived='мародёрки')
        self.build(); case = self.cases()[rid]
        self.assertEqual('listener_free_text_interpreted', case['label_source']); self.assertEqual('Должно быть ё', case['listener_comment'])
        self.assertEqual('Agent reading of the note.', case['interpretation']); self.assertIn('agent interpretation', case['interpretation_basis'])
        self.assertEqual('мародёрки', case['target_form'])

    def test_free_text_without_derived_record_is_pending(self):
        rid = self.fx.report('other', 's1', MAR, 'мародерки', comment='ё pronounced incorrectly')
        self.fx.report('other', 's2', VSE, 'все', comment='glitch')
        manifest = self.build()
        self.assertEqual({}, self.cases()); self.assertEqual([rid], [p['id'] for p in read_lines(self.out / 'pending.jsonl')])
        self.assertEqual(0, manifest['counts']['cases']); self.assertEqual(1, manifest['counts']['pending'])

    def test_stress_only_report_is_excluded(self):
        self.fx.report('stress', 's3', STRESS_ONLY, 'батька', 'батька́', heard=STRESS_ONLY)
        self.fx.report('stress', 's4', MAR, 'мародерки', 'мародерки́')
        self.build(); self.assertEqual({}, self.cases()); self.assertEqual([], read_lines(self.out / 'pending.jsonl'))

    def test_duplicate_notes_are_merged(self):
        first = self.fx.report('other', 's1', MAR, 'мародерки', comment='ё', derived='мародёрки', heard=MAR_HEARD)
        second = self.fx.report('other', 's1', MAR, 'мародерки', comment='Ё')
        third = self.fx.report('stress', 's1', MAR, 'мародерки', 'мародёрки', heard=MAR_HEARD)
        self.build(); cases = self.cases()
        self.assertEqual([third], list(cases)); self.assertEqual(sorted([first, second]), cases[third]['duplicate_report_ids'])
        self.assertEqual([], read_lines(self.out / 'pending.jsonl'))

    def test_conflicting_notes_are_excluded(self):
        self.fx.report('stress', 's2', VSE, 'все', 'все́', heard=VSE_HEARD)
        self.fx.report('other', 's2', VSE, 'все', comment='Должно быть ё', derived='всё', heard=VSE_HEARD)
        manifest = self.build()
        self.assertEqual({}, self.cases()); conflicts = read_lines(self.out / 'conflicts.jsonl')
        self.assertEqual(1, len(conflicts)); self.assertEqual(['все', 'всё'], sorted(r['target_form'] for r in conflicts[0]['reports']))
        self.assertEqual(1, manifest['counts']['conflicts'])

    def test_database_and_inputs_are_unchanged(self):
        self.fx.report('stress', 's1', MAR, 'мародерки', 'мародёрки', heard=MAR_HEARD, queue='applied')
        self.fx.report('other', 's2', VSE, 'все', comment='всё', derived='все́', heard=VSE_HEARD)
        self.fx.db.close()
        files = [p for p in Path(self.tmp.name).rglob('*') if p.is_file()]
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        manifest = self.build()
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p in before})
        self.assertEqual(hashlib.sha256(self.fx.database.read_bytes()).hexdigest(), manifest['database_sha256'])
        self.assertEqual(2, manifest['counts']['cases'])
        # the output is deterministic apart from the build time
        text = (self.out / 'cases.jsonl').read_text('utf8'); self.build()
        self.assertEqual(text, (self.out / 'cases.jsonl').read_text('utf8'))


if __name__ == '__main__':
    unittest.main()
