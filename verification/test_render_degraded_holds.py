from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]

from rimsky.store import Store, concern, hold_codes, speaker_review_holds  # noqa: E402
import higgs_render_holds as holds  # noqa: E402
import mark_higgs_degraded_takes as mark  # noqa: E402

TAGGED = '<|emotion:sad|><|prosody:speed_slow|>Он ушёл.'


def segment(ident: str, ordinal: int, speaker: str = 'narrator') -> dict:
    return {'id': ident, 'source_id': f'src-{ident}', 'chapter_id': 'c0002', 'start': 0, 'end': 9,
            'speaker': speaker, 'status': 'ready', 'ordinal': ordinal}


class RenderDegradedHoldTests(unittest.TestCase):
    def test_hold_blocks_only_its_own_take_and_cannot_be_confirmed(self):
        segments = [segment('a', 0), segment('b', 1, 'ivan'), segment('c', 2)]
        item = holds.hold_items(segments[1], [{'issue': 'control_stripped', 'variant': 'plain_same_voice',
                                                'controls': ['<|emotion:sad|>']}], 'hash')[0]
        self.assertEqual('backend', concern(item))
        verdicts = {'b': {'speaker': 'ivan'}}
        flagged, held = speaker_review_holds(segments, [item], verdicts)
        self.assertEqual(({'b'}, {'b'}), (flagged, held))
        self.assertEqual(['render_degraded'], hold_codes(segments[1], [item], verdicts))

    def test_issues_come_from_render_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'input').mkdir()
            for ident, planned, used, variant in (('lost', TAGGED, 'Он ушёл.', 'plain_same_voice'),
                                                  ('untagged', 'Он ушёл.', 'Он ушёл.', 'plain_same_voice'),
                                                  ('first', TAGGED, None, None),
                                                  ('unrecorded', TAGGED, None, 'plain')):
                (folder / 'input' / f'{ident}.txt').write_text(planned, encoding='utf-8')
                if used is not None:
                    (folder / f'{ident}.render-prompt.txt').write_text(used, encoding='utf-8')
                if variant:
                    (folder / f'{ident}.render-variant.txt').write_text(variant, encoding='utf-8')
            routes = {'narrator': None, 'ivan': None}
            issues = lambda ident, speaker='narrator': holds.render_issues(segment(ident, 0, speaker), folder, routes)
            self.assertEqual(([{'issue': 'control_stripped', 'variant': 'plain_same_voice',
                                'controls': ['<|emotion:sad|>', '<|prosody:speed_slow|>']}], True), issues('lost'))
            self.assertEqual(([], True), issues('untagged'))
            self.assertEqual(([], True), issues('first', 'ivan'))
            self.assertEqual(([{'issue': 'controls_unverified', 'variant': 'plain'}], False), issues('unrecorded'))
            self.assertEqual(([{'issue': 'voice_reference_missing', 'variant': '', 'speaker': 'denis'}], True),
                             issues('first', 'denis'))

    def test_missing_retry_evidence_keeps_the_take_held(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'input').mkdir()
            (folder / 'input' / 's2.txt').write_text(TAGGED, encoding='utf-8')
            (folder / 's2.render-variant.txt').write_text('plain_same_voice', encoding='utf-8')
            used = folder / 's2.render-prompt.txt'
            used.write_text('Он ушёл.', encoding='utf-8')
            item = segment('s2', 0, 'ivan')
            routes = {'narrator': None, 'ivan': None}
            analysis = holds.with_holds({}, item, holds.render_issues(item, folder, routes)[0], 'same')
            used.unlink()
            issues, complete = holds.render_issues(item, folder, routes)
            self.assertFalse(complete)
            after = holds.with_holds(analysis, item, issues, 'same')
            self.assertEqual(['controls_unverified'], [hold['render_issue'] for hold in after['speaker_resolution']])
            self.assertEqual({'s2'}, speaker_review_holds([item], after['speaker_resolution'])[0])

    def test_notices_and_holds_are_replaced_not_duplicated(self):
        issue = [{'issue': 'voice_reference_missing', 'variant': '', 'speaker': 'denis'}]
        warning = holds.warning_with('Speech duration looks unusual.', issue)
        self.assertEqual(warning, holds.warning_with(warning, issue))
        self.assertEqual('Speech duration looks unusual.', holds.warning_with(warning, []))
        item = segment('b', 1, 'denis')
        other = {'target_id': 'x', 'triggers': ['unresolved_speaker'], 'status': 'review'}
        analysis = holds.with_holds({'speaker_resolution': [other]}, item, issue, 'old')
        self.assertIs(analysis, holds.with_holds(analysis, item, issue, 'old'))
        cleared = holds.with_holds(analysis, item, [], 'new')
        self.assertEqual([other], cleared['speaker_resolution'])


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.library_root, self.folder, self.reports = base / 'library', base / 'render', base / 'reports'
        store = Store(self.library_root)
        with store.db:
            manifest={'id':'book','title':'B','author':'Test','language':'ru','source_hash':'fixture',
                'chapters':[{'id':'c0002','title':'Chapter','blocks':[
                    {'id':'src-'+ident,'kind':'paragraph','text':'Он ушёл.'}for ident in ('s1','s2','s3')]}]}
            store.db.execute("INSERT INTO books(id,title,manifest) VALUES('book','B',?)",(json.dumps(manifest),))
            store.db.execute("INSERT INTO generations(id,book_id,name,status,config,analysis) VALUES(?,?,?,?,?,?)",
                             ('gen', 'book', 'Higgs draft', 'failed',
                              json.dumps({'engine': 'higgs_audio_tts_q8_external_review'}), json.dumps({'provisional': True})))
            for ordinal, (ident, speaker) in enumerate((('s1', 'narrator'), ('s2', 'ivan'), ('s3', 'denis'))):
                audio = self.write_take(ident)
                store.db.execute("INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken,"
                                 "speaker,status,audio_hash,duration,warning,performance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                 ('gen', ident, ordinal, 'c0002', f'src-{ident}', 0, 9, 'Он ушёл.', 'Он ушёл.', speaker,
                                  'ready', audio, 1.0, 'Kept warning.' if ident == 's2' else '', '{"schema": 2}'))
        store.close()
        (self.folder / 'plan.json').write_text(json.dumps({'generation_id': 'gen'}), encoding='utf-8')
        chapter = self.folder / 'c0002'
        (chapter / 's2.render-variant.txt').write_text('plain_same_voice', encoding='utf-8')
        (chapter / 's2.render-prompt.txt').write_text('Он ушёл.', encoding='utf-8')
        reference = base / 'narrator.wav'
        reference.write_bytes(b'narrator reference')
        self.routes = {'narrator': (reference, 'text'), 'ivan': (reference, 'text')}
        self.patches = [patch.object(mark.render, 'reference_routes', lambda: self.routes),
                        patch.object(mark, 'REPORTS', self.reports)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def write_take(self, ident: str) -> str:
        chapter = self.folder / 'c0002'
        (chapter / 'output').mkdir(parents=True, exist_ok=True)
        (chapter / 'input').mkdir(exist_ok=True)
        (chapter / 'input' / f'{ident}.txt').write_text(TAGGED, encoding='utf-8')
        take = chapter / 'output' / f'{ident}.wav'
        take.write_bytes(f'take {ident}'.encode())
        return mark.render.file_sha256(take)

    def library(self) -> Path:
        return (self.library_root / 'library.sqlite3').resolve()

    def rows(self):
        db = sqlite3.connect(self.library())
        db.row_factory = sqlite3.Row
        try:
            segments = {row['id']: dict(row) for row in db.execute('SELECT * FROM segments')}
            analysis = json.loads(db.execute("SELECT analysis FROM generations WHERE id='gen'").fetchone()[0])
            revision = db.execute("SELECT revision FROM generation_versions WHERE id='gen'").fetchone()[0]
            return segments, analysis, revision
        finally:
            db.close()

    def test_dry_run_reports_without_writing(self):
        before = self.rows()
        report = mark.dry_run(self.library(), [self.folder], self.reports / 'dry.json')
        self.assertEqual(before, self.rows())
        generation = report['generations'][0]
        self.assertEqual({'plain_same_voice': 1}, generation['retry_variants'])
        self.assertEqual({'ready': 3, 'proposed': 2, 'control_stripped': 1, 'voice_reference_missing': 1,
                          'unlinked': 0, 'unverifiable': 0}, generation['counts'])
        self.assertEqual(['s2', 's3'], [target['segment_id'] for target in generation['targets']])

    def test_apply_snapshots_marks_and_is_idempotent(self):
        dry = self.reports / 'dry.json'
        mark.dry_run(self.library(), [self.folder], dry)
        _, _, revision = self.rows()
        audit = mark.apply(dry, self.library())
        self.assertTrue(Path(audit['snapshot']).is_file())
        segments, analysis, after = self.rows()
        self.assertGreater(after, revision)
        self.assertTrue(segments['s2']['warning'].startswith('Kept warning. Provisional take: a retry removed'))
        self.assertEqual('<|emotion:sad|>, <|prosody:speed_slow|>',
                         segments['s2']['warning'].split('controls (')[1].split(')')[0])
        self.assertIn('denis has no voice reference', segments['s3']['warning'])
        self.assertEqual('', segments['s1']['warning'])
        self.assertEqual('ready', segments['s2']['status'])
        self.assertEqual(2, json.loads(segments['s2']['performance'])['schema'])
        self.assertEqual({'s2': 'control_stripped', 's3': 'voice_reference_missing'},
                         {item['target_id']: item['render_issue'] for item in analysis['speaker_resolution']})
        self.assertTrue(analysis['provisional'])
        held = speaker_review_holds([{**row, 'performance': {}} for row in segments.values()],
                                    analysis['speaker_resolution'])[1]
        self.assertEqual({'s2', 's3'}, held)
        again = mark.apply(dry, self.library())
        self.assertEqual(['s2', 's3'], again['generations'][0]['already_applied'])
        self.assertEqual((segments, analysis, after), self.rows())
        rerun = mark.dry_run(self.library(), [self.folder], self.reports / 'dry2.json')
        self.assertEqual([], rerun['generations'][0]['targets'])

    def test_corrections_keep_degraded_takes_playable_and_drop_discarded_holds(self):
        mark.dry_run(self.library(), [self.folder], self.reports / 'dry.json')
        mark.apply(self.reports / 'dry.json', self.library())
        store = Store(self.library_root)
        try:
            with store.db:
                store.db.execute("UPDATE generations SET analysis=json_set(analysis,'$.complete',json('true')) WHERE id='gen'")
            unrelated = store.revise('gen', [{'id': 's1', 'retake': True}])
            by_id = {item['id']: item for item in unrelated['segments']}
            self.assertEqual(('pending', 'ready', 'ready'), tuple(by_id[sid]['status'] for sid in ('s1', 's2', 's3')))
            self.assertTrue(by_id['s2']['audio_hash'] and by_id['s3']['audio_hash'])
            resolutions = unrelated['analysis']['speaker_resolution']
            self.assertEqual({'s2', 's3'}, speaker_review_holds(unrelated['segments'], resolutions)[0])
            retaken = store.revise(unrelated['id'], [{'id': 's2', 'retake': True}])
            by_id = {item['id']: item for item in retaken['segments']}
            self.assertEqual(('pending', 'ready'), (by_id['s2']['status'], by_id['s3']['status']))
            self.assertEqual(['s3'], [item['target_id'] for item in retaken['analysis']['speaker_resolution']])
        finally:
            store.close()

    def test_apply_refuses_a_changed_take_and_writes_nothing(self):
        dry = self.reports / 'dry.json'
        mark.dry_run(self.library(), [self.folder], dry)
        db = sqlite3.connect(self.library())
        with db:
            db.execute("UPDATE segments SET audio_hash='retake' WHERE id='s3'")
        db.close()
        before = self.rows()
        with self.assertRaises(RuntimeError):
            mark.apply(dry, self.library())
        self.assertEqual(before, self.rows())

    def test_apply_refuses_changed_evidence(self):
        dry = self.reports / 'dry.json'
        mark.dry_run(self.library(), [self.folder], dry)
        (self.folder / 'c0002' / 's2.render-prompt.txt').write_text('<|emotion:sad|>Он ушёл.', encoding='utf-8')
        with self.assertRaises(SystemExit):
            mark.apply(dry, self.library())


if __name__ == '__main__':
    unittest.main()
