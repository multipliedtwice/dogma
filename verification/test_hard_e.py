"""Automatic hard-е respelling (rule hard-e-respelling-v1): candidates, validator, document, merge with listener entries,
render wiring (byte-identical spec without the flags), recognition tolerance and the app path."""
import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'runtime')]

from rimsky import hard_e as he
from rimsky.name_tolerance import tolerance
from rimsky.respelling import (apply_listener_names, apply_model_names, apply_name_respellings, apply_noun_respellings,
                               apply_respellings, disclosure, names_sources, respell_text, validate_respelling)
from rimsky.spoken_pronunciation import synthesis_text

A = '́'
NAMES = {'mozes': {'name': 'Мозес', 'stressed': 'Мо' + A + 'зес', 'source': 'model'},
         'terek': {'name': 'Терек', 'stressed': 'Те' + A + 'рек', 'source': 'model'},
         'ana': {'name': 'Ана Мелеса', 'stressed': 'А' + A + 'на Меле' + A + 'са', 'source': 'model'}}
TEXTS = ['Мозес сказал, что Терек далеко. Он видел Мозеса. Мозесом был доволен.', 'Игве́р не пришёл. Секонд-хенд закрыт.']


def model_doc(rows, decisions, names=NAMES, lexicon=None):
    return he.document(rows, decisions, names, lexicon or {}, {'source': 'model', 'rule': he.RULE, 'model': 'claude-opus-5-5'})


class CandidateTests(unittest.TestCase):
    def test_positions_are_e_after_a_consonant_only(self):
        self.assertEqual([1], he.positions('Белар'))
        self.assertEqual([1, 3], he.positions('Терек'))
        self.assertEqual([3], he.positions('Мозес'))           # Мозес: М-о-з-е-с
        self.assertEqual([], he.positions('Ена'))              # word start
        self.assertEqual([], he.positions('Каэль'))            # after a vowel
        self.assertEqual([], he.positions('Объект'))           # after ъ
        self.assertEqual([], he.positions('Пьер'))             # after ь
        self.assertEqual([], he.positions('Шеф'))              # ж ш ц ч щ have a fixed sound
        self.assertEqual([3], he.positions('Мо́зес'))          # stress marks do not shift positions

    def test_candidates_cover_name_words_and_lexicon_and_skip_listener_entries(self):
        lexicon = {'pn:Мерунес': 'Меруне' + A + 'с', 'cw:секонд': 'секо' + A + 'нд', 'pn:Игв': 'И' + A + 'гв'}
        rows = he.candidates(NAMES, lexicon)
        self.assertEqual({'Мозес', 'Терек', 'Мелеса', 'Мерунес', 'секонд'}, {r['word'] for r in rows})
        self.assertNotIn('Игв', {r['word'] for r in rows})
        self.assertEqual(['character name'] * 3, [r['kind'] for r in rows[:3]])
        skipped = he.candidates(NAMES, lexicon, skip_characters={'mozes'}, skip_nouns={'pn:Мерунес'})
        self.assertEqual({'Терек', 'Мелеса', 'секонд'}, {r['word'] for r in skipped})
        self.assertEqual(['h0000', 'h0001', 'h0002'], [r['id'] for r in skipped])

    def test_evidence_lists_forms_and_two_examples(self):
        rows = he.candidates({'mozes': NAMES['mozes']}, {}); he.evidence(rows, TEXTS)
        self.assertEqual({'мозес': 1, 'мозеса': 1, 'мозесом': 1}, rows[0]['forms'])
        self.assertEqual(2, len(rows[0]['examples']))
        request = he.request(rows)
        self.assertEqual('Моз[е]с', request['items'][0]['word_with_candidates'])


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.rows = he.candidates(NAMES, {}); he.evidence(self.rows, TEXTS)
        self.request = he.request(self.rows)
        self.ids = {r['word']: r['id'] for r in self.rows}

    def answer(self, **words):
        spoken = {r['word']: r['word'] for r in self.rows}; spoken.update(words)
        return {'decisions': [{'id': self.ids[w], 'respelled': s, 'basis': 'x'} for w, s in spoken.items()]}

    def test_soft_and_hard_answers_pass(self):
        he.validate(self.answer(), self.request)
        he.validate(self.answer(Мозес='Мозэс', Терек='Терэк'), self.request)

    def test_rejects_every_other_change(self):
        for bad in ({'Мозес': 'Мозёс'}, {'Мозес': 'Мозэз'}, {'Мозес': 'Мо' + A + 'зэс'}, {'Мозес': 'Мозес '}, {'Терек': 'Таррек'}):
            with self.assertRaises(ValueError, msg=bad):
                he.validate(self.answer(**bad), self.request)
        with self.assertRaises(ValueError):      # missing item
            he.validate({'decisions': self.answer()['decisions'][:-1]}, self.request)
        with self.assertRaises(ValueError):      # duplicate
            he.validate({'decisions': self.answer()['decisions'] * 2}, self.request)

    def test_schema_fixes_the_ids(self):
        schema = he.schema(['h0000', 'h0001'])
        self.assertEqual(2, schema['properties']['decisions']['minItems'])
        self.assertEqual(['h0000', 'h0001'], schema['properties']['decisions']['items']['properties']['id']['enum'])


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.lexicon = {'pn:Мерунес': 'Меруне' + A + 'с', 'cw:секонд': 'секо' + A + 'нд'}
        self.rows = he.candidates(NAMES, self.lexicon); he.evidence(self.rows, TEXTS)
        self.ids = {r['word']: r['id'] for r in self.rows}
        decisions = {r['id']: r['word'] for r in self.rows}
        decisions[self.ids['Мозес']] = 'Мозэс'; decisions[self.ids['Мелеса']] = 'Мелэса'; decisions[self.ids['Мерунес']] = 'Мерунэс'
        self.doc = model_doc(self.rows, decisions, lexicon=self.lexicon)

    def test_document_carries_the_stress_marks_and_the_proper_nouns(self):
        entries = {e['character_id']: e for e in self.doc['entries']}
        self.assertEqual('Мо' + A + 'зэс', entries['mozes']['stressed'])
        self.assertEqual('А' + A + 'на Мелэ' + A + 'са', entries['ana']['stressed'])
        self.assertNotIn('terek', entries)
        self.assertEqual([('pn:Мерунес', 'Мерунэ' + A + 'с')], [(n['key'], n['stressed']) for n in self.doc['proper_nouns']])
        for entry in self.doc['entries']:
            self.assertEqual('model', entry['provenance']['source'])
            self.assertTrue(all(s['spoken'] == 'э' for s in entry['substitutions']))

    def test_the_document_has_the_listener_schema(self):
        names = apply_listener_names(NAMES, self.doc)       # the listener-document consumer accepts it unchanged
        self.assertEqual('Мо' + A + 'зэс', names['mozes']['stressed'])

    def test_model_then_listener_listener_wins_per_character(self):
        listener = {'schema': 1, 'entries': [{'character_id': 'mozes', 'printed': 'Мозес', 'stressed': 'Мо' + A + 'зес',
                                              'provenance': 'listener request'}],
                    'proper_nouns': [{'key': 'pn:Мерунес', 'stressed': 'Меруне' + A + 'с'}]}
        names, nouns, sources = apply_respellings(NAMES, {'pn:Мерунес': 'Меруне' + A + 'с', 'cw:секонд': 'секо' + A + 'нд'}, self.doc, listener)
        self.assertEqual('Мо' + A + 'зес', names['mozes']['stressed'])           # the listener reverted it to soft
        self.assertEqual('listener', names['mozes']['source'])
        self.assertEqual('Меруне' + A + 'с', nouns['pn:Мерунес'])
        self.assertIn('Мелэ', names['ana']['stressed'])               # the model respelling stays where the listener is silent
        self.assertEqual({'мелеса': 'model'}, sources)

    def test_model_respelling_keeps_the_answer_and_is_overridden_by_listener_respelling(self):
        names = apply_model_names(NAMES, self.doc)
        self.assertEqual('Мо' + A + 'зес', names['mozes']['model_stressed'])
        self.assertEqual('model', names['mozes']['source'])
        listener = {'schema': 1, 'entries': [{'character_id': 'mozes', 'printed': 'Мозес', 'stressed': 'Мо' + A + 'зэс',
                                              'provenance': 'listener request 2026-10-21'}]}
        final, sources = apply_name_respellings(NAMES, self.doc, listener)
        self.assertEqual('Мо' + A + 'зес', final['mozes']['model_stressed'])
        self.assertEqual('listener', sources['мозес'])
        self.assertEqual('model', sources['мелеса'])

    def test_unknown_document_and_foreign_letters_are_refused(self):
        with self.assertRaises(ValueError):
            apply_model_names(NAMES, {'schema': 1, 'entries': []})
        bad = json.loads(json.dumps(self.doc)); bad['entries'][0]['stressed'] = bad['entries'][0]['stressed'].replace('э', 'ё')
        with self.assertRaises(ValueError):
            apply_model_names(NAMES, bad)

    def test_a_noun_missing_from_this_policys_lexicon_is_ignored(self):
        nouns, _ = apply_noun_respellings({'pn:Другой': 'Другой'}, self.doc, None)
        self.assertEqual({'pn:Другой': 'Другой'}, nouns)


class SynthesisTests(unittest.TestCase):
    def setUp(self):
        from rimsky import spoken_pronunciation
        spoken_pronunciation.set_resolver(None)    # another suite may have installed an engine-backed resolver

    def spec(self):
        names, sources = apply_name_respellings(NAMES, DocumentTests.doc_for_spec(), None)
        return {'policy': 'auto-pronunciation-v1', 'names': names, 'common': [], 'respelling_sources': sources}

    def test_inflected_forms_follow_and_the_record_names_its_source(self):
        spec = self.spec()
        text, plan = synthesis_text('Мозеса и Мелесу видели.', spec)
        text = text.replace(A, '')
        self.assertIn('Мозэса', text); self.assertIn('Мелэсу', text.replace('Мэлэ', 'Мелэ'))
        records = plan['listener_respelling']['records']
        self.assertEqual({'model'}, {r['source'] for r in records})
        self.assertEqual('Мозеса', records[0]['printed'])

    def test_without_sources_the_disclosure_is_unchanged(self):
        spec = self.spec(); spec.pop('respelling_sources')
        records = synthesis_text('Мозеса видели.', spec)[1]['listener_respelling']['records']
        self.assertEqual([{'kind': 'name', 'printed': 'Мозеса', 'spoken': 'Мозэса'}], records)

    def test_recognition_tolerates_a_model_respelling_like_a_listener_one(self):
        records = respell_text('Мозес пришёл.', {'mozes': 'Мо' + A + 'зэс'}, sources={'мозес': 'model'})[1]
        self.assertEqual('model', records[0]['source'])
        expected = ['мозес', 'пришел']
        self.assertEqual((expected, []), tolerance(expected, ['мозес', 'пришел'], 'Мозес пришёл.', disclosure(records)['records']))
        corrected, changes = tolerance(expected, ['мозэс', 'пришел'], 'Мозес пришёл.', disclosure(records)['records'])
        self.assertEqual(expected, corrected)
        self.assertEqual('listener-respelling-v1', changes[0]['rule'])
        self.assertEqual('model', changes[0]['respelling_source'])
        self.assertIn('automatic hard-е', changes[0]['user_instructions'][0])


def _doc_for_spec():
    rows = he.candidates(NAMES, {}); he.evidence(rows, TEXTS)
    decisions = {r['id']: r['word'] for r in rows}
    for r in rows:
        if r['word'] in ('Мозес', 'Мелеса'):decisions[r['id']] = r['word'].replace('е', 'э')
    return model_doc(rows, decisions)


DocumentTests.doc_for_spec = staticmethod(_doc_for_spec)


def legacy_spec(args, book):
    """The pronunciation block exactly as render_higgs_palette_preview built it before --hard-e existed."""
    from rimsky.name_stress_analysis import common_words
    from rimsky.util import file_digest
    answer = json.loads(Path(args.name_stress).read_text('utf8'))['answer']
    names = {r['character_id']: {'name': r['name'], 'stressed': r['stressed'], 'confidence': r['confidence'],
                                 'basis': r['basis'], 'source': 'model'} for r in answer['names']}
    texts = [b['text'] for c in book['chapters'] for b in c['blocks'] if b.get('text')]
    spec = {'policy': args.pronunciation_policy, 'names': names, 'common': common_words({k: v['stressed'] for k, v in names.items()}, texts),
            'name_stress_answer_sha256': file_digest(Path(args.name_stress))}
    from rimsky.proper_noun_stress import proper_noun_lexicon, RULE
    spec.update(proper_nouns=proper_noun_lexicon(texts, {k: v['stressed'] for k, v in names.items()}), proper_noun_rule=RULE)
    return spec


class RendererWiringTests(unittest.TestCase):
    def setUp(self):
        from rimsky import spoken_pronunciation
        spoken_pronunciation.set_resolver(None)
        self.tmp = Path(tempfile.mkdtemp())
        answer = {'answer': {'names': [{'character_id': 'mozes', 'name': 'Мозес', 'stressed': 'Мо' + A + 'зес', 'confidence': 'high', 'basis': 'x'},
                                       {'character_id': 'terek', 'name': 'Терек', 'stressed': 'Те' + A + 'рек', 'confidence': 'high', 'basis': 'x'}]}}
        (self.tmp / 'names.json').write_text(json.dumps(answer, ensure_ascii=False), 'utf8')
        self.book = {'chapters': [{'blocks': [{'text': t} for t in TEXTS + ['Мерунес улыбнулся. Он видел Мерунеса. Мерунесу было смешно.']]}]}
        self.args = argparse.Namespace(name_stress=self.tmp / 'names.json', pronunciation_policy='stress-rare-v2', homograph_stress=None,
                                       yo_restoration=None, hard_e=None, listener_respellings=None)

    def test_without_the_flags_the_spec_is_byte_identical_to_before(self):
        from render_higgs_palette_preview import pronunciation_spec
        new = pronunciation_spec(self.args, self.book)
        self.assertEqual(json.dumps(legacy_spec(self.args, self.book), ensure_ascii=False, sort_keys=True),
                         json.dumps(new, ensure_ascii=False, sort_keys=True))
        for key in ('hard_e_sha256', 'listener_respellings_sha256', 'respelling_sources'):
            self.assertNotIn(key, new)

    def test_flags_apply_names_and_nouns_and_freeze_their_hashes(self):
        from render_higgs_palette_preview import pronunciation_spec
        from rimsky.util import file_digest
        names = {'mozes': {'name': 'Мозес', 'stressed': 'Мо' + A + 'зес'}, 'terek': {'name': 'Терек', 'stressed': 'Те' + A + 'рек'}}
        lexicon = legacy_spec(self.args, self.book)['proper_nouns']
        self.assertIn('pn:Мерунеса', lexicon)
        rows = he.candidates(names, lexicon); he.evidence(rows, TEXTS)
        decisions = {r['id']: (r['word'].replace('е', 'э') if r['word'] in ('Мозес', 'Мерунеса') else r['word']) for r in rows}
        (self.tmp / 'hard.json').write_text(json.dumps(model_doc(rows, decisions, names, lexicon), ensure_ascii=False), 'utf8')
        (self.tmp / 'listener.json').write_text(json.dumps({'schema': 1, 'entries': [
            {'character_id': 'mozes', 'printed': 'Мозес', 'stressed': 'Мо' + A + 'зэс', 'provenance': 'listener'}]}, ensure_ascii=False), 'utf8')
        self.args.hard_e = self.tmp / 'hard.json'; self.args.listener_respellings = self.tmp / 'listener.json'
        spec = pronunciation_spec(self.args, self.book)
        self.assertEqual('Мо' + A + 'зэс', spec['names']['mozes']['stressed'])
        self.assertEqual('Те' + A + 'рек', spec['names']['terek']['stressed'])
        self.assertEqual('Мэрунэ' + A + 'са', spec['proper_nouns']['pn:Мерунеса'])
        self.assertEqual(file_digest(self.args.hard_e), spec['hard_e_sha256'])
        self.assertEqual(file_digest(self.args.listener_respellings), spec['listener_respellings_sha256'])
        self.assertEqual({'мозес': 'listener', 'мерунеса': 'model'}, spec['respelling_sources'])
        # the frozen spec changes the synthesis text, hence the synthesis key
        text = synthesis_text('Мерунесу и Мозесу сказали.', {**spec, 'policy': 'auto-pronunciation-v1'})[0]
        self.assertIn('Мозэсу', text.replace(A, ''))

    def test_supervisor_passes_both_flags_to_the_renderer(self):
        source = (ROOT / 'scripts/supervise_higgs_preview.py').read_text('utf8')
        self.assertIn("'--hard-e'", source); self.assertIn("'--listener-respellings'", source)
        render = (ROOT / 'scripts/render_higgs_palette_preview.py').read_text('utf8')
        self.assertIn("'--hard-e'", render); self.assertIn("'--listener-respellings'", render)
        self.assertIn("'--hard-e'", (ROOT / 'scripts/seed_iteration_generation.py').read_text('utf8'))


class BuilderTests(unittest.TestCase):
    def setUp(self):
        import build_hard_e
        self.mod = build_hard_e
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / 'source').mkdir(); (self.tmp / 'name-stress-v1').mkdir()
        (self.tmp / 'source/book.json').write_text(json.dumps({'chapters': [{'blocks': [{'text': t} for t in TEXTS]}]}, ensure_ascii=False), 'utf8')
        names = [{'character_id': k, 'name': v['name'], 'stressed': v['stressed'], 'confidence': 'high', 'basis': 'x'} for k, v in NAMES.items()]
        (self.tmp / 'name-stress-v1/characters.answer.json').write_text(json.dumps({'answer': {'names': names}}, ensure_ascii=False), 'utf8')

    def test_listener_entries_are_excluded_and_attempts_feed_errors_back(self):
        listener = self.tmp / 'listener.json'
        listener.write_text(json.dumps({'schema': 1, 'entries': [{'character_id': 'mozes'}]}), 'utf8')
        rows, names, lexicon, _ = self.mod.gather(self.tmp, self.tmp / 'name-stress-v1/characters.answer.json', listener)
        self.assertNotIn('Мозес', {r['word'] for r in rows}); self.assertNotIn('mozes', names)
        request = he.request(rows); out = self.tmp / 'out'; calls = []

        def fake(system, body, contract, folder, tag):
            calls.append(body)
            ids = [i['id'] for i in body['items']]
            words = {i['id']: i['word'] for i in body['items']}
            if len(calls) == 1:                    # first attempt: a non-candidate letter changes
                return {'decisions': [{'id': i, 'respelled': 'Хзхзх', 'basis': 'x'} for i in ids]}, {'model': 'claude-opus-5-5', 'cost_usd': 0.1}
            return {'decisions': [{'id': i, 'respelled': words[i].replace('Мелеса', 'Мелэса'), 'basis': 'x'} for i in ids]}, {'model': 'claude-opus-5-5', 'cost_usd': 0.1}
        with patch.object(self.mod, 'call', side_effect=fake):
            batches = self.mod.adjudicate(out, request)
        self.assertEqual(2, len(calls)); self.assertIn('validation_errors', calls[1]); self.assertEqual(2, batches[0]['attempts'])
        with patch.object(self.mod, 'call', side_effect=AssertionError('answered batches are immutable')):
            self.assertEqual(1, len(self.mod.adjudicate(out, request)))      # re-run reuses the frozen answer
        self.assertEqual(2, len(list((out / 'batches').glob('*.attempt-*.request.json'))))


class AppPathTests(unittest.TestCase):
    def test_new_generation_pass_applies_model_hard_e_and_reuses_it(self):
        from rimsky.jobs import Jobs
        calls = []

        def call(method, payload):
            calls.append(payload)
            return {'decisions': [{'id': i['id'], 'respelled': i['word'].replace('Мозес', 'Мозэс'), 'basis': 'x'} for i in payload['request']['items']]}
        fake = SimpleNamespace(_check=lambda: None, _call=call)
        names = json.loads(json.dumps(NAMES))
        listener = {**names['terek'], 'source': 'listener'}
        names2 = {**names, 'terek': listener}
        result, info = Jobs._hard_e(fake, names2, TEXTS, {'analysis_mode': 'claude_cli'}, {})
        self.assertEqual('Мо' + A + 'зэс', result['mozes']['stressed']); self.assertEqual(listener, result['terek'])
        self.assertEqual('hard_e', calls[0]['kind']); self.assertEqual('hard-e-respelling-v1', info['rule'])
        self.assertNotIn('Терек', [n for item in calls[0]['request']['items'] for n in item['names']])   # listener-edited names are not candidates
        again, info2 = Jobs._hard_e(SimpleNamespace(_check=lambda: None, _call=lambda *a: 1 / 0), names2, TEXTS, {}, {'hard_e': info})
        self.assertEqual(result['mozes']['stressed'], again['mozes']['stressed'])
        sources = Jobs._respelling_sources({'names': result})['respelling_sources']
        self.assertEqual('model', sources['мозес'])
        series_wins = {**result, 'mozes': {**result['mozes'], 'stressed': 'Мо' + A + 'зэс', 'source': 'series_listener'}}
        self.assertEqual('listener', Jobs._respelling_sources({'names': series_wins})['respelling_sources']['мозес'])

    def test_worker_knows_the_stage(self):
        source = (ROOT / 'runtime/worker.py').read_text('utf8')
        self.assertIn("'hard_e'", source)


class SeriesTermTests(unittest.TestCase):
    def test_bundled_prefix_term_is_listener_authored(self):
        data = json.loads((ROOT / 'resources/series-terms.json').read_text('utf8'))
        term = data['series'][0]['terms'][0]
        self.assertEqual(('tek-prefix', 'hyphen-prefix', 'listener request 2026-10-21'), (term['id'], term['match'], term['source']))
        self.assertTrue(validate_respelling(term['printed'], term['spoken']))


if __name__ == '__main__':
    unittest.main()
