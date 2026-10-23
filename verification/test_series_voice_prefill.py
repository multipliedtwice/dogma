import json
from pathlib import Path
import unittest

from scripts.build_series_voice_prefill import (apply_reviewed_speakers,
                                                  apply_speaker_overrides,
                                                  character_profiles,
                                                  match_character,
                                                  normalized_name,
                                                  reviewed_speaker_overrides)


class SeriesVoicePrefillTests(unittest.TestCase):
    def test_reviewed_speaker_override_changes_identity_without_rewriting_candidate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'speaker-overrides.json'
            path.write_text(json.dumps({
                'schema': 1,
                'candidates_sha256': 'frozen',
                'clips': {'clip': {
                    'character_id': 'Иришка',
                    'character_display': 'Иришка',
                    'speaker_override_basis': 'Listener confirmed the performed speaker.',
                }},
            }, ensure_ascii=False), encoding='utf-8')
            corrections = reviewed_speaker_overrides(path, 'frozen')
            original = [{'id': 'clip', 'character_id': 'Радугин'}]
            corrected = apply_speaker_overrides(original, corrections)
            self.assertEqual(corrected[0]['character_id'], 'Иришка')
            self.assertEqual(original[0]['character_id'], 'Радугин')

    def test_review_decision_wins_when_building_character_voice_prior(self):
        original = [{'id': 'clip', 'character_id': 'Радугин'}]
        review = {'decisions': {'clip': {
            'speaker_verdict': 'no', 'corrected_character_id': 'Иришка'}}}
        corrected = apply_reviewed_speakers(original, review)
        self.assertEqual(corrected[0]['character_id'], 'Иришка')
        self.assertEqual(corrected[0]['character_display'], 'Иришка')

    def test_analysis_prompts_keep_nested_reported_speech_with_outer_performer(self):
        from rimsky.analysis import SYSTEM_PROMPT
        from rimsky.speech_structure import STRUCTURE_SYSTEM
        from rimsky.staged_analysis import SPEAKER_SYSTEM
        self.assertIn('reported speaker, not automatically the current performer',
                      SYSTEM_PROMPT)
        self.assertIn('reported source, not automatically the current performer',
                      SPEAKER_SYSTEM)
        self.assertIn('does not by itself establish a new speaker', STRUCTURE_SYSTEM)

    def test_names_and_reviewed_aliases_match_without_titles(self):
        self.assertEqual(normalized_name('герд Тамара'), normalized_name('Тамара'))
        self.assertEqual(normalized_name('Минн-0 Ла-Фин'), normalized_name('Минн-О Ла-Фин'))

    def test_only_stable_multichapter_character_becomes_a_prior(self):
        labels = [
            {'character_id': 'a', 'voice_family': 'f1', 'chapter_id': 'c1'},
            {'character_id': 'a', 'voice_family': 'f1', 'chapter_id': 'c2'},
            {'character_id': 'a', 'voice_family': 'f1', 'chapter_id': 'c2'},
            {'character_id': 'b', 'voice_family': 'f1', 'chapter_id': 'c1'},
            {'character_id': 'b', 'voice_family': 'f2', 'chapter_id': 'c2'},
            {'character_id': 'b', 'voice_family': 'f2', 'chapter_id': 'c2'},
        ]
        cast = {'characters': [
            {'id': 'a', 'name': 'Name A', 'aliases': ['Alias A']},
            {'id': 'b', 'name': 'Name B', 'aliases': []},
        ]}
        profiles, aliases = character_profiles(labels, cast)
        self.assertEqual(match_character('Alias A', aliases, profiles)['voice_family'], 'f1')
        self.assertIsNone(match_character('Name B', aliases, profiles))

    def test_book2_game_ui_wins_and_review_flags_are_well_formed(self):
        root = Path(__file__).resolve().parents[1]
        board = root / 'training-work/character-rb2-full/fine-tune-all'
        if not board.is_dir():
            self.skipTest('Full Book 2 board is not present')
        candidates = [json.loads(line) for line in
                      (board / 'candidates.jsonl').read_text(encoding='utf-8').splitlines()
                      if line.strip()]
        overlay = json.loads((board / 'voice-proposal-overrides.json').read_text(
            encoding='utf-8'))['clips']
        game_ui = [row for row in candidates
                   if row.get('presentation_channel_proposed') == 'game_ui']
        self.assertTrue(game_ui)
        self.assertTrue(all(overlay[row['id']]['base_voice_id_proposed'] ==
                            'mechanical_stats' for row in game_ui))
        flags = [value for value in overlay.values()
                 if value.get('voice_prefill_review')]
        self.assertTrue(flags)
        self.assertTrue(all(value.get('voice_prefill_alternative') and
                            value.get('voice_prefill_review_reason')
                            for value in flags))


if __name__ == '__main__':
    unittest.main()
