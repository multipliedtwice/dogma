"""Series character state grows across volumes and survives book deletion."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))

from rimsky.series_characters import apply_pronunciations, propagation_targets, record_listener_stress, seed_cast, sync_generation
from rimsky.store import Store


def fb2(title: str, volume: int, text: str) -> bytes:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info>
<author><first-name>Владимир</first-name><last-name>Автор</last-name></author>
<book-title>{title}</book-title><lang>ru</lang><sequence name="Элирм" number="{volume}"/>
</title-info></description><body><section><title><p>Глава 1</p></title><p>{text}</p></section></body></FictionBook>'''.encode()


class SeriesCharacterStateTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library', resources=ROOT / 'resources')

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def generation(self, book, cast):
        generation = self.store.create_generation(book['id'], {})
        self.store.execute('UPDATE generations SET cast=? WHERE id=?',
                           (json.dumps(cast, ensure_ascii=False), generation['id']))
        return self.store.generation(generation['id'])

    def test_listener_name_stress_survives_deleted_volume_and_seeds_later_volume(self):
        first = self.store.import_data(fb2('Элирм IV', 4, 'Гундахар посмотрел на Влада.'), 'four.fb2')
        cast = [{'id': 'gundahar', 'name': 'Гундахар', 'aliases': [], 'voice_id': 'masculine_6'}]
        generation = self.generation(first, cast)
        report = {'id': 'r' * 24, 'book_id': first['id'], 'generation_id': generation['id'],
                  'selected_text': 'Гундахар', 'intended_stress': 'Гунда́хар',
                  'created': '2026-10-21T00:00:00Z'}
        promoted = record_listener_stress(self.store, report)
        self.assertEqual('updated', promoted['status'])
        self.assertEqual('Гунда́хар', promoted['pronunciations']['гундахар'])

        self.store.delete_book(first['id'])
        self.assertEqual(1, len(self.store.rows('SELECT * FROM series_character_state')))

        second = self.store.import_data(fb2('Элирм V', 5, 'Гундахар встретил Назу.'), 'five.fb2')
        later_cast = [*cast, {'id': 'nazu', 'name': 'Назу', 'aliases': [], 'voice_id': 'masculine_4'}]
        later = self.generation(second, later_cast)
        inherited = seed_cast(self.store, second)
        self.assertEqual(['Гундахар'], [row['name'] for row in inherited])
        self.assertTrue(inherited[0]['_series_seed'])
        _, state = sync_generation(self.store, second, later)
        self.assertEqual({'Гундахар', 'Назу'}, {row['canonical_name'] for row in state})
        seeded = apply_pronunciations(self.store, second, later_cast,
                                      {'policy': 'test', 'names': {'gundahar': {'name': 'Гундахар', 'stressed': 'Гу́ндахар', 'source': 'model'}}})
        self.assertEqual('Гунда́хар', seeded['names']['gundahar']['stressed'])
        self.assertEqual('series_listener', seeded['names']['gundahar']['source'])

    def test_propagation_includes_inflected_forms_and_groups_one_retake_per_segment(self):
        state = {'status': 'updated', 'pronunciations': {'гундахар': 'Гунда́хар'}}
        generation = {'segments': [
            {'id': 'one', 'chapter_id': 'c1', 'source_id': 'p1', 'start': 10, 'ordinal': 1,
             'text': 'Гундахар позвал Гундахара.', 'spoken': 'Гундахар позвал Гундахара.',
             'status': 'ready', 'audio_hash': 'a' * 64},
            {'id': 'two', 'chapter_id': 'c1', 'source_id': 'p2', 'start': 40, 'ordinal': 2,
             'text': 'Обычное слово.', 'spoken': 'Обычное слово.', 'status': 'ready', 'audio_hash': 'b' * 64},
        ]}
        targets = propagation_targets(generation, state)
        self.assertEqual(1, len(targets))
        self.assertEqual(['Гунда́хар', 'Гунда́хара'],
                         [row['intended_stress'] for row in targets[0]['corrections']])

    def test_compound_character_component_binds_an_inflected_ui_selection(self):
        book = self.store.import_data(fb2('Элирм IV', 4, 'Генерал посмотрел Ар-Хакону в глаза.'), 'four.fb2')
        generation = self.generation(book, [
            {'id': 'ar_hakon', 'name': 'Ар-Хакон', 'aliases': [], 'voice_id': 'masculine_4'},
        ])
        promoted = record_listener_stress(self.store, {
            'id': 'h' * 24, 'book_id': book['id'], 'generation_id': generation['id'],
            'selected_text': 'Хакону', 'intended_stress': 'Хако́ну',
            'created': '2026-10-21T00:00:00Z',
        })
        self.assertEqual('updated', promoted['status'])
        self.assertEqual('Ар-Хакон', promoted['canonical_name'])
        self.assertEqual('Хако́н', promoted['pronunciations']['хакон'])


if __name__ == '__main__':
    unittest.main()
