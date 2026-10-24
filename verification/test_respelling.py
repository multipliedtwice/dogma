"""Listener respellings (е->э/ё): validator, name inflection, series terms, recognition tolerance, reports."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'runtime')]

from rimsky.auto_pronunciation import auto_pronunciation, validate_name_stress
from rimsky.name_stress import mark_names, name_spans
from rimsky.name_tolerance import RESPELLING_RULE, tolerance
from rimsky.respelling import (apply_listener_names, disclosure, respell_text, validate_respelled_stress,
                               validate_respelling)
from rimsky.series_characters import apply_pronunciations, record_listener_stress
from rimsky.speech_quality import spoken_word_tokens as tokens, word_tokens
from rimsky.spoken_terms import apply_terms, series_terms
from rimsky.store import Store
from rimsky.util import AppError
from rimsky.spoken_pronunciation import synthesis_text

A = '́'
SERIES = {'id': 'f0271ef4b2fe52888ca95c55eb0fa7ac', 'name': 'Искажающие реальность'}
MOZES = {'mozes': 'Мо' + A + 'зэс'}


def fb2(title: str, series: str, volume: int, text: str) -> bytes:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info>
<author><first-name>Владимир</first-name><last-name>Автор</last-name></author>
<book-title>{title}</book-title><lang>ru</lang><sequence name="{series}" number="{volume}"/>
</title-info></description><body><section><title><p>Глава 1</p></title><p>{text}</p></section></body></FictionBook>'''.encode()


class ValidatorTests(unittest.TestCase):
    def test_accepts_only_e_to_e_hard_or_yo_with_case(self):
        self.assertEqual([{'index': 3, 'printed': 'е', 'spoken': 'э'}], validate_respelling('Мозес', 'Мо' + A + 'зэс'))
        self.assertEqual([{'index': 1, 'printed': 'е', 'spoken': 'э'}], validate_respelling('тек-купол', 'тэк-купол')[:1])
        self.assertEqual('Э', validate_respelling('Елирм', 'Элирм')[0]['spoken'])
        self.assertEqual('ё', validate_respelling('мед', 'мёд')[0]['spoken'])
        self.assertEqual([], validate_respelling('Мозес', 'Мо' + A + 'зес'))      # stress alone is not a respelling

    def test_rejects_every_other_change(self):
        for printed, spoken in (('Мозес', 'Мозис'), ('Мозес', 'Мозэ'), ('Мозес', 'Мозэсс'), ('мед', 'МЭД'),
                                ('Мозес', 'Нозэс'), ('эти', 'ети'), ('мёд', 'мед'), ('Мозес', 'МозЁс'), ('кот', 'кэт'),
                                ('тек', 'тэк-'), ('мед', 'мид')):
            with self.subTest(printed=printed, spoken=spoken), self.assertRaises(ValueError):
                validate_respelling(printed, spoken)

    def test_yo_is_its_own_stress(self):
        validate_respelled_stress('Мозес', 'Мо' + A + 'зэс')
        validate_respelled_stress('Мозес', 'Мозёс')
        for bad in ('Мозэс', 'Мо' + A + 'зёс', 'Мозё' + A + 'с', 'Мо' + A + 'зис'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_respelled_stress('Мозес', bad)

    def test_name_stress_validation_accepts_a_respelled_name_only(self):
        validate_name_stress('Мозес', 'Мо' + A + 'зэс')
        validate_name_stress('Мозес', 'Мозёс')
        validate_name_stress('Ар-Хакон', 'Ар-Хако' + A + 'н')
        for bad in ('Мо' + A + 'зис', 'Мозэс', 'Мо' + A + 'зэс' + A):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_name_stress('Мозес', bad)


class NameInflectionTests(unittest.TestCase):
    def test_names_do_not_match_inside_unicode_identifiers(self):
        lexicon={'alexander':'Алекса'+A+'ндр'}
        for text in ('xАлександр','Александр2','_Александр','Александр_',
                     'éАлександр','αАлександр','字Александр','Александр١','Александр漢',
                     'xАле'+A+'ксандр','Але'+A+'ксандр2'):
            with self.subTest(text=text):self.assertEqual([],name_spans(text,lexicon))

    def test_name_boundaries_keep_inflections_explicit_acutes_and_compound_components(self):
        lexicon={'alexander':'Алекса'+A+'ндр','hakon':'Хако'+A+'н'}
        self.assertEqual([],name_spans('александр',lexicon))
        self.assertEqual([(1,len('Александром')+1,'Алекса'+A+'ндром')],name_spans('«Александром»',lexicon))
        explicit='Але'+A+'ксандр'
        self.assertEqual([(0,len(explicit),explicit)],name_spans(explicit,lexicon))
        for dash in ('-','‐','‑'):
            word='Ар'+dash+'Хакону'
            self.assertEqual([(3,len(word),'Хако'+A+'ну')],name_spans(word,lexicon))
        self.assertEqual([],name_spans('2игв',{'cw:игв':'и'+A+'гв'},implicit_single=True))

    def test_respelling_follows_stress_into_inflected_forms(self):
        for printed, expected in (('Мозес', 'Мо' + A + 'зэс'), ('Мозеса', 'Мо' + A + 'зэса'), ('Мозесом', 'Мо' + A + 'зэсом'),
                                  ('Мозесу', 'Мо' + A + 'зэсу')):
            with self.subTest(printed=printed):
                self.assertEqual([(0, len(printed), expected)], name_spans(printed, MOZES))
                self.assertEqual([(0, len(printed), expected)], name_spans(printed, MOZES, implicit_single=True))
        text, edits = mark_names('Мозес увидел Мозеса. С Мозесом.', MOZES)
        self.assertEqual('Мо' + A + 'зэс увидел Мо' + A + 'зэса. С Мо' + A + 'зэсом.', text)
        self.assertEqual(3, len(edits))

    def test_unchanged_names_ordinary_words_and_explicit_marks(self):
        self.assertEqual([(0, 3, 'Вел'), (8, 12, 'Ве' + A + 'ла')], name_spans('Вел вёл Вела', {'vel': 'Вел'}, implicit_single=True))
        self.assertEqual([], name_spans('мозес', MOZES))                                      # lowercase: ordinary text
        self.assertEqual([(0, 6, 'Мозе' + A + 'с')], name_spans('Мозе' + A + 'с', MOZES))      # explicit mark wins, no respelling
        self.assertEqual([(0, 5, 'Мо' + A + 'зэс')], name_spans('Мозэс', MOZES))               # already respelled text

    def test_original_e_hard_names_and_yo_respelling(self):
        self.assertEqual([(0, 4, 'Э' + A + 'рик')], name_spans('Эрик', {'e': 'Э' + A + 'рик'}))
        self.assertEqual([(0, 5, 'Мозёс')], name_spans('Мозес', {'m': 'Мозёс'}))
        self.assertEqual([(0, 6, 'Мозёса')], name_spans('Мозеса', {'m': 'Мозёс'}))

    def test_pre_pass_changes_only_substituted_letters_and_stress_pass_agrees(self):
        text = 'Мозес сказал, что Мозеса не видел. Мозесом доволен.'
        respelled, records = respell_text(text, MOZES)
        self.assertEqual('Мозэс сказал, что Мозэса не видел. Мозэсом доволен.', respelled)
        self.assertEqual(['Мозес', 'Мозеса', 'Мозесом'], [r['printed'] for r in records])
        out, plan = auto_pronunciation(respelled, MOZES)       # the word check of the stress pass still holds
        self.assertIn('Мо' + A + 'зэса', out)
        self.assertEqual(word_tokens(respelled), word_tokens(out))
        self.assertEqual(3, text.count('е') - respelled.count('е'))      # exactly the three name letters changed
        self.assertEqual({'kind', 'printed', 'spoken'}, set(disclosure(records)['records'][0]))

    def test_without_a_respelling_nothing_is_touched(self):
        names = {'g': 'Гунда' + A + 'хар'}
        text = 'Гундахар пришёл. Тек-купол пал.'
        self.assertEqual((text, []), respell_text(text, names))
        self.assertEqual(text, synthesis_text(text, None)[0].replace(A, ''))


class TermTests(unittest.TestCase):
    def setUp(self):
        self.terms = series_terms(SERIES)

    def test_reality_benders_seed_is_found_by_id_or_series_name_only(self):
        self.assertEqual(['tek-prefix'], [t['id'] for t in self.terms['entries']])
        self.assertEqual('listener request 2026-10-21', self.terms['entries'][0]['source'])
        self.assertIsNotNone(series_terms({'id': 'other', 'name': 'Искажающие  реальность'}))
        self.assertIsNone(series_terms({'id': 'other', 'name': 'Элирм'}))

    def test_whole_compound_family_is_respelled(self):
        text = 'Закрытой тек-куполом ноде. Тек-купол пал. У тек-купола нет. О тек-куполе. ТЕК-КУПОЛ.'
        out, records = apply_terms(text, self.terms)
        self.assertEqual('Закрытой тэк-куполом ноде. Тэк-купол пал. У тэк-купола нет. О тэк-куполе. ТЭК-КУПОЛ.', out)
        self.assertEqual(5, len(records))
        self.assertEqual({'printed', 'spoken', 'term_id', 'rule'} <= set(records[0]), True)

    def test_unrelated_words_are_not_touched(self):
        text = 'Текст и тек не тронуты. Ректор, купол-тек, дверь-купол, анти-тек-щит, тексты-купол.'
        self.assertEqual((text, []), apply_terms(text, self.terms))

    def test_every_tek_compound_and_inflection_is_respelled(self):
        text = 'Тек-щит, тек-барьер, тек-щитом, о тек-барьере, тек-куполный, Тек-Купол, ТЕК-ЩИТ.'
        out, records = apply_terms(text, self.terms)
        self.assertEqual('Тэк-щит, тэк-барьер, тэк-щитом, о тэк-барьере, тэк-куполный, Тэк-Купол, ТЭК-ЩИТ.', out)
        self.assertEqual(7, len(records))
        self.assertTrue(all(r['term_id'] == 'tek-prefix' for r in records))

    def test_prefix_term_validation(self):
        from rimsky.spoken_terms import validate_term
        base = {'id': 'x', 'source': 's', 'match': 'hyphen-prefix', 'printed': 'тек-', 'spoken': 'тэк-'}
        self.assertEqual(base, validate_term(dict(base)))
        for bad in ({'printed': 'тек'}, {'spoken': 'так-'}, {'printed': 'тек-купол', 'spoken': 'тэк-купол'}, {'spoken': 'тек-'}):
            with self.assertRaises(ValueError):validate_term({**base, **bad})

    def test_sealed_into_the_synthesis_text_and_recorded(self):
        spec = {'policy': 'auto-pronunciation-v1', 'names': {}, 'common': [], 'terms': self.terms}
        text, plan = synthesis_text('Он вошёл в тек-куполом закрытую ноду.', spec)
        self.assertIn('тэк-куполом', text)
        self.assertEqual('тэк-куполом', plan['listener_respelling']['records'][0]['spoken'])
        self.assertNotIn('тэк', synthesis_text('Он вошёл в тек-куполом закрытую ноду.', {**spec, 'terms': None})[0])


class ToleranceTests(unittest.TestCase):
    records = [{'kind': 'name', 'printed': 'Мозес', 'spoken': 'Мозэс'},
               {'kind': 'term', 'printed': 'тек-куполом', 'spoken': 'тэк-куполом'}]

    def check(self, source, heard):
        return tolerance(tokens(source), tokens(heard), source, self.records)

    def test_recognizer_writing_either_spelling_is_a_disclosed_deviation(self):
        for heard in ('Это сказал Мозес вчера.', 'Это сказал Мозэс вчера.'):
            corrected, changes = self.check('Это сказал Мозес вчера.', heard)
            self.assertEqual(tokens('Это сказал Мозес вчера.'), corrected)
            if 'Мозэс' in heard:
                self.assertEqual(RESPELLING_RULE, changes[0]['rule'])
                self.assertEqual(['мозес'], changes[0]['source_tokens'])
                self.assertEqual(['мозэс'], changes[0]['heard_tokens'])
        corrected, changes = self.check('Закрытой тек-куполом ноде.', 'Закрытой тэк куполом ноде.')
        self.assertEqual(tokens('Закрытой тек-куполом ноде.'), corrected)
        self.assertEqual([RESPELLING_RULE], [c['rule'] for c in changes])

    def test_respelling_does_not_excuse_other_differences(self):
        corrected, changes = self.check('Закрытой тек-куполом ноде.', 'Закрытой так куполом ноде.')
        self.assertNotEqual(tokens('Закрытой тек-куполом ноде.'), corrected)
        corrected, _ = self.check('Закрытой тек-куполом ноде.', 'Закрытой тэк куполом.')
        self.assertNotEqual(tokens('Закрытой тек-куполом ноде.'), corrected)
        # Without the unit's recorded respelling the rule never applies (no global е/э loosening).
        corrected, changes = tolerance(tokens('Эти тек-купола'), tokens('Ети тэк купола'), 'Эти тек-купола')
        self.assertFalse(any(c['rule'] == RESPELLING_RULE for c in changes))


class StoreAndSeriesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name) / 'library', resources=ROOT / 'resources')
        self.book = self.store.import_data(fb2('Книга 11', 'Искажающие реальность', 11,
                                               'Мозес посмотрел на тек-купол. Мозесом он был доволен.'), 'b.fb2')
        cast = [{'id': 'mozes', 'name': 'Мозес', 'aliases': [], 'voice_id': 'masculine_6'}]
        created = self.store.create_generation(self.book['id'], {})
        self.store.execute('UPDATE generations SET cast=? WHERE id=?', (json.dumps(cast, ensure_ascii=False), created['id']))
        for segment in created['segments']:
            self.store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=? WHERE generation_id=? AND id=?",
                               ('a' * 64, .5, created['id'], segment['id']))
        self.generation = self.store.generation(created['id'])
        self.segment = next(s for s in self.generation['segments'] if 'Мозес' in s['text'])

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def report(self, selected, intended):
        return self.store.report_stress(self.generation['id'], self.segment['id'], self.segment['source_id'], selected,
                                        self.segment['start'] + self.segment['text'].index(selected), .1, intended_stress=intended)

    def test_report_accepts_a_respelled_form_and_rejects_other_letter_changes(self):
        self.assertEqual('Мо' + A + 'зэс', self.report('Мозес', 'Мо' + A + 'зэс')['intended_stress'])
        self.assertEqual('Мозёс', self.report('Мозес', 'Мозёс')['intended_stress'])
        self.assertEqual('Мо' + A + 'зес', self.report('Мозес', 'Мо' + A + 'зес')['intended_stress'])   # existing behaviour
        for bad in ('Мо' + A + 'зис', 'Мозэс', 'Мо' + A + 'зэсс', 'Мо' + A + 'зёс', 'Мозе' + A + A + 'с', None, 'Мо' + A + 'зэ'):
            with self.subTest(bad=bad), self.assertRaises(AppError):
                self.report('Мозес', bad)

    def test_listener_report_records_the_respelled_name_and_every_form_inherits_it(self):
        report = {'id': 'r' * 24, 'book_id': self.book['id'], 'generation_id': self.generation['id'],
                  'selected_text': 'Мозесом', 'intended_stress': 'Мо' + A + 'зэсом', 'created': '2026-10-21T00:00:00Z'}
        promoted = record_listener_stress(self.store, report)
        self.assertEqual('updated', promoted['status'])
        self.assertEqual('Мо' + A + 'зэс', promoted['pronunciations']['мозес'])
        seeded = apply_pronunciations(self.store, self.book, self.generation['cast'],
                                      {'policy': 'test', 'names': {'mozes': {'name': 'Мозес', 'stressed': 'Мо' + A + 'зес', 'source': 'model'}}})
        self.assertEqual('Мо' + A + 'зэс', seeded['names']['mozes']['stressed'])
        self.assertEqual('series_listener', seeded['names']['mozes']['source'])
        self.assertEqual(['tek-prefix'], [t['id'] for t in seeded['terms']['entries']])   # the series term lexicon rides along
        lexicon = {cid: row['stressed'] for cid, row in seeded['names'].items()}
        self.assertEqual('Мо' + A + 'зэсом', name_spans('Мозесом', lexicon)[0][2])

    def test_report_with_yo_records_the_yo_form(self):
        record_listener_stress(self.store, {'id': 'y' * 24, 'book_id': self.book['id'], 'generation_id': self.generation['id'],
                                            'selected_text': 'Мозес', 'intended_stress': 'Мозёс', 'created': '2026-10-21T00:00:00Z'})
        seeded = apply_pronunciations(self.store, self.book, self.generation['cast'], {'policy': 'test', 'names': {}})
        self.assertEqual('Мозёс', seeded['names']['mozes']['stressed'])


class ElirmArtifactTests(unittest.TestCase):
    FOLDER = ROOT / 'training-work/elirm-iv-higgs-mvp'

    @unittest.skipUnless((FOLDER / 'listener-respellings/respellings.json').exists(), 'listener respelling artifact not present')
    def test_artifact_is_valid_against_the_frozen_name_stress_answer(self):
        document = json.loads((self.FOLDER / 'listener-respellings/respellings.json').read_text('utf8'))
        answer = json.loads((self.FOLDER / 'name-stress-v1/characters.answer.json').read_text('utf8'))['answer']
        names = {r['character_id']: {'name': r['name'], 'stressed': r['stressed'], 'source': 'model'} for r in answer['names']}
        merged = apply_listener_names(names, document)
        self.assertEqual('Мо' + A + 'зэс', merged['mozes']['stressed'])
        self.assertEqual('Мо' + A + 'зес', merged['mozes']['model_stressed'])
        self.assertEqual({k: v for k, v in names.items() if k != 'mozes'}, {k: v for k, v in merged.items() if k != 'mozes'})


if __name__ == '__main__':
    unittest.main()
