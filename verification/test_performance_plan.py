from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from rimsky.performance import performance_plan


class PerformancePlanTests(unittest.TestCase):
    def test_fast_action_pace_preserves_structural_pauses(self):
        part = {'text': 'He dodged and struck back.', 'delivery': 'neutral',
                'speaker': 'narrator', 'context': {
                    'mood': 'fast_action', 'confidence': 'high',
                    'channel': 'direct', 'content_register': 'prose', 'evidence': [
                        {'id': 'p1', 'quote': 'He dodged and struck back.'}]}}
        plan = performance_plan(part)
        self.assertEqual(plan['pace'], 'brisk')
        self.assertIn('fast_action', plan['cues'])
        self.assertEqual(plan['delivery'], 'neutral')
        trailing = performance_plan({**part, 'text': 'He struck back...'})
        self.assertEqual(trailing['pace'], 'brisk')
        self.assertEqual(trailing['ending_pace'], 'deliberate')
        self.assertGreaterEqual(trailing['pause_after_seconds'], .8)
        heading = performance_plan({**part, 'kind': 'heading'})
        self.assertEqual(heading['pace'], 'deliberate')
        self.assertGreater(heading['pause_before_seconds'], 0)
        low = performance_plan({**part, 'context': {
            **part['context'], 'confidence': 'low'}})
        self.assertEqual(low['pace'], 'normal')
        self.assertNotIn('fast_action', low['cues'])

    def test_user_reviewed_delivery_cases(self):
        delighted = performance_plan(
            {'text': '— Как я его! Вы видели?!', 'delivery': 'excited',
             'speaker': 'kirill', 'source_id': 'p1'},
            [{'text': '— восторженно прокричал я.', 'id': 'p2'}])
        self.assertIn('explicit_excited_attribution', delighted['cues'])
        self.assertEqual(delighted['basis'], 'text_intent_unverified_audio')
        ellipsis = performance_plan({'text': 'И замер…', 'delivery': 'neutral',
                                     'speaker': 'narrator', 'source_id': 'p3'})
        self.assertIn('trailing_ellipsis', ellipsis['cues'])
        self.assertGreaterEqual(ellipsis['pause_after_seconds'], .8)
        stunned = performance_plan({'text': 'И замер…', 'delivery': 'stunned',
                                    'speaker': 'narrator', 'source_id': 'p3'})
        self.assertIn('stunned_reaction', stunned['cues'])
        heading = performance_plan({'text': 'Глава первая', 'delivery': 'neutral',
                                    'speaker': 'narrator', 'source_id': 'p4'})
        self.assertIn('section_heading', heading['cues'])
        self.assertGreater(heading['pause_before_seconds'], 0)
        self.assertGreater(heading['pause_after_seconds'], 0)
        ordinary = performance_plan({'text': 'Глава клана обсуждала поход.',
                                     'delivery': 'neutral', 'speaker': 'narrator',
                                     'source_id': 'p4b'})
        self.assertNotIn('section_heading', ordinary['cues'])
        enumeration = performance_plan({'text': 'Вторжение. Война. Истребление…',
                                        'delivery': 'enumeration', 'speaker': 'narrator',
                                        'source_id': 'p5'})
        self.assertEqual(enumeration['pace'], 'brisk')
        self.assertEqual(enumeration['ending_pace'], 'deliberate')
        self.assertIn('enumeration', enumeration['cues'])


if __name__ == '__main__':
    unittest.main()
