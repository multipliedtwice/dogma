from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]

import check_book2_speech_structure as check  # noqa: E402
from rimsky.boundaries import structure_chunks  # noqa: E402

DIPLOMAT = ('— Нашу фракцию более чем устроит такая помощь! — Дипломат уважительно поклонился грозному морфу, '
            'после чего повернулся в мою сторону. — Комар, на день, а может даже на два, я бы хотел воспользоваться '
            'услугами миелонки Айни — и с нашими «научниками» её свести, и ребят подкачать.')
NARRATION = 'Комар долго молчал.'
QUOTED = 'Он сказал: «Да». Потом ушёл.'


def clip(ident, source_id, paragraph, fragment, role, character=''):
    start = paragraph.index(fragment)
    return {'id': ident, 'source_id': source_id, 'chapter_id': 'c0031', 'paragraph_text': paragraph,
            'text_start': start, 'text_end': start + len(fragment), 'source_text': fragment,
            'role': role, 'character_id': character}


class Book2StructureCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.candidates, self.context, self.review = base / 'candidates.jsonl', base / 'context.json', base / 'review.json'
        self.answers = base / 'out' / 'answers.jsonl'
        remark = '— Дипломат уважительно поклонился грозному морфу, после чего повернулся в мою сторону. '
        rows = [clip('remark', 'p40', DIPLOMAT, remark, 'character', 'Иван Лозовский'),
                clip('opening', 'p40', DIPLOMAT, '— Нашу фракцию более чем устроит такая помощь! ', 'character', 'Иван Лозовский'),
                clip('whole', 'p40', DIPLOMAT, DIPLOMAT, 'character', 'Иван Лозовский'),
                clip('decided', 'p40', DIPLOMAT, remark, 'character', 'Иван Лозовский'),
                clip('plain', 'p41', NARRATION, NARRATION, 'narration'),
                clip('quote', 'p42', QUOTED, '«Да». ', 'narration')]
        self.candidates.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
        passages = [{'source_id': sid, 'ordinal': index, 'start': 0, 'text': text}
                    for index, (sid, text) in enumerate((('p39', 'Перед этим.'), ('p40', DIPLOMAT), ('p41', NARRATION),
                                                         ('p42', QUOTED)))]
        self.context.write_text(json.dumps({'passages': passages}, ensure_ascii=False), encoding='utf-8')
        self.review.write_text(json.dumps({'decisions': {'decided': {'speaker_verdict': 'no',
                                                                     'corrected_character_id': 'narrator'}}}),
                               encoding='utf-8')
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def provider(self, system, request, schema, **_):
        self.calls.append(request)
        answer = {}
        for case in request['dash_targets']:
            texts = [chunk['text'] for chunk in case['chunks']]
            answer[case['id']] = ['narrator' if text.startswith('— Дипломат') or text.startswith(('Он сказал', 'Потом'))
                                  else 'speech' for text in texts]
        return answer

    def test_plan_deduplicates_complete_paragraphs_and_stays_offline(self):
        doc = check.plan(self.candidates, self.context, 'all')
        self.assertEqual(['p40', 'p42'], [item['source_id'] for item in doc['paragraphs']])
        self.assertEqual(['decided', 'opening', 'remark', 'whole'], doc['paragraphs'][0]['clip_ids'])
        self.assertEqual((['p39'], ['p41', 'p42']), (doc['paragraphs'][0]['before_ids'], doc['paragraphs'][0]['after_ids']))
        self.assertEqual(len(DIPLOMAT) + len(QUOTED), doc['totals']['characters'])
        self.assertEqual(['p40'], [item['source_id'] for item in check.plan(self.candidates, self.context, 'character')['paragraphs']])
        self.assertEqual([], self.calls)

    def test_run_sends_each_paragraph_once_with_context_and_resumes(self):
        doc = check.plan(self.candidates, self.context, 'all')
        self.assertEqual(1, check.run(doc, self.candidates, self.context, self.answers, 'fake', self.provider, limit=1))
        self.assertEqual([DIPLOMAT], [case['chunks'][0]['text'] + ''.join(chunk['text'] for chunk in case['chunks'][1:])
                                      for case in self.calls[0]['dash_targets']])
        self.assertEqual(['p39'], [item['id'] for item in self.calls[0]['context_before']])
        self.assertEqual(1, check.run(doc, self.candidates, self.context, self.answers, 'fake', self.provider))
        self.assertEqual(0, check.run(doc, self.candidates, self.context, self.answers, 'fake', self.provider))
        self.assertEqual(2, len(self.calls))
        chunks = json.loads(self.answers.read_text(encoding='utf-8').splitlines()[0])['chunks']
        self.assertEqual(len(structure_chunks(DIPLOMAT)), len(chunks))

    def test_compare_flags_author_remarks_and_mixed_clips_without_overriding_people(self):
        doc = check.plan(self.candidates, self.context, 'all')
        check.run(doc, self.candidates, self.context, self.answers, 'fake', self.provider)
        report = check.compare(doc, self.candidates, self.answers, self.review)
        flags = {item['clip_id']: item for item in report['clips']}
        self.assertEqual('possible_author_remark', flags['remark']['flag'])
        self.assertEqual('narrator', flags['remark']['proposed_role'])
        self.assertIsNone(flags['remark']['human_speaker_decision'])
        self.assertEqual('narrator', flags['decided']['human_speaker_decision'])
        self.assertEqual('mixed_in_one_clip', flags['whole']['flag'])
        self.assertEqual('possible_speech', flags['quote']['flag'])
        self.assertNotIn('opening', flags)
        self.assertEqual(1, report['counts']['human_decided'])
        self.assertEqual('proposal_only', report['kind'])

    def test_run_refuses_a_stale_plan(self):
        doc = check.plan(self.candidates, self.context, 'all')
        with self.candidates.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(clip('late', 'p41', NARRATION, NARRATION, 'narration'), ensure_ascii=False) + '\n')
        with self.assertRaises(SystemExit):
            check.run(doc, self.candidates, self.context, self.answers, 'fake', self.provider)
        self.assertEqual([], self.calls)


if __name__ == '__main__':
    unittest.main()
