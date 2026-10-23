from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))

from rimsky.casting import cast_voices


class CastingLabels(unittest.TestCase):
    def test_cast_keeps_readable_voice_names_beside_voice_ids(self):
        voices = [{'id': 'n', 'role': 'narrator', 'name': 'Warm narrator'}, {'id': 'one', 'name': 'Bright alto'}, {'id': 'two'}]
        characters = [{'id': 'a', 'name': 'A'}, {'id': 'b', 'name': 'B'}]
        segments = [{'speaker': 'a', 'text': 'Hello', 'chapter_id': 'c', 'source_id': 'p1', 'ordinal': 0},
                    {'speaker': 'b', 'text': 'Yes', 'chapter_id': 'c', 'source_id': 'p2', 'ordinal': 1}]
        cast = cast_voices(characters, voices, segments)
        names = {voice['id']: voice.get('name', '') for voice in voices}
        self.assertEqual('Warm narrator', cast[0]['voice_name'])
        for character in cast:
            self.assertEqual(names[character['voice_id']], character['voice_name'])


if __name__ == '__main__':
    unittest.main()
