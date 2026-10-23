from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))

from test_core import FB2, AliasEngine, pack_fixture
from rimsky.casting import ALIAS_COMPARISON_LIMIT
from rimsky.jobs import Jobs, alias_candidates, subphrases
from rimsky.store import Store
from rimsky.util import AnalysisCapacityError, AppError


def record(cid, name, *aliases, evidence=True):
    return {'id': cid, 'name': name, 'aliases': list(aliases), 'description': '', 'evidence': [f'p{cid}'] if evidence else [],
            'gender': {'value': 'unknown', 'evidence': []}, 'age_band': {'value': 'unknown', 'evidence': []}}


def paired(first, second):
    return list(alias_candidates([record('a', *first), record('b', *second)]))


def ids(payloads):
    return [[character['id'] for character in payload['characters']] for payload in payloads]


class CandidateRecall(unittest.TestCase):
    def assertPaired(self, cases, expected):
        for first, second in cases:
            with self.subTest(first=first, second=second):
                self.assertEqual(expected, paired((first,), (second,)))

    def test_russian_case_endings_join_name_forms(self):
        self.assertPaired((('Лозовский', 'Лозовского'), ('Иван Лозовский', 'Ивана Лозовского'), ('Маша', 'Машей'),
                           ('Андрей', 'Андрея'), ('Артём Петров', 'Артёма Петрова'), ('Имран', 'Имрана'),
                           ('Иришка', 'Иришкой'), ('Николай', 'Николаем'), ('Мария', 'Марией')), [('a', 'b')])

    def test_long_prefix_forms_join_callsigns(self):
        self.assertPaired((('Комар', 'Кирилл Комаров'), ('Комаром', 'Кирилл Комаров'), ('Will', 'William')), [('a', 'b')])
        self.assertPaired((('Иван', 'Ивановский'), ('Ком', 'Комаров'), ('Кирилл', 'Комаров')), [])

    def test_common_diminutives_join_full_names(self):
        self.assertPaired((('Саша', 'Александр Петров'), ('Саша Петров', 'Александр Петров'), ('Сашей', 'Александр'),
                           ('Лёша', 'Алексей'), ('Слава', 'Ярослав'), ('Аня', 'Анна'), ('Женя', 'Евгения')), [('a', 'b')])
        self.assertPaired((('Вячеслав', 'Ярослав'), ('Анна', 'Алёна'), ('Борис', 'Глеб')), [])

    def test_stress_marks_accents_and_yo_are_folded(self):
        self.assertPaired((('Ма́ша', 'Маша'), ('Алёна', 'Алена'), ('José', 'Jose'), ('Йозеф', 'йозеф')), [('a', 'b')])
        self.assertPaired((('Йозеф', 'Иозеф'),), [])


class CandidateBounds(unittest.TestCase):
    def test_phrases_longer_than_six_words_are_not_expanded(self):
        self.assertEqual(21, len(list(subphrases(tuple('abcdef')))))
        self.assertEqual([tuple('abcdefg')], list(subphrases(tuple('abcdefg'))))
        self.assertEqual([('a', 'b')], paired(('Альфа', 'один два три четыре пять шесть'), ('Четыре',)))
        self.assertEqual([], paired(('Альфа', 'один два три четыре пять шесть семь'), ('Четыре',)))

    def test_at_most_thirty_one_phrases_per_character(self):
        aliases = [f'Слово{index}' for index in range(40)]
        self.assertEqual([('a', 'b')], paired(('Альфа', *aliases), ('Слово29',)))
        self.assertEqual([], paired(('Альфа', *aliases), ('Слово30',)))


class CandidateOrder(unittest.TestCase):
    def test_specific_pairs_come_before_generic_cliques(self):
        characters = [record(f'r{index:02d}', f'Номер{index}', 'боец') for index in range(24)]
        characters += [record('l0', 'Лозовский'), record('l1', 'Иван Лозовский')]
        pairs = list(alias_candidates(characters))
        self.assertEqual((277, ('l0', 'l1'), ('r00', 'r01')), (len(pairs), pairs[0], pairs[1]))

    def test_multi_word_phrases_come_first_among_equally_rare_phrases(self):
        characters = [record('a', 'Анна'), record('b', 'Анна Каренина'), record('c', 'Иван Петров'), record('d', 'Иван Петров младший')]
        self.assertEqual([('c', 'd'), ('a', 'b')], list(alias_candidates(characters)))

    def test_speaking_pairs_come_before_silent_pairs(self):
        characters = [record('q1', 'Борис'), record('q2', 'Борис Котов'), record('s1', 'Олег'), record('s2', 'Олег Гусев')]
        self.assertEqual([('q1', 'q2'), ('s1', 's2')], list(alias_candidates(characters)))
        self.assertEqual([('s1', 's2'), ('q1', 'q2')], list(alias_candidates(characters, {'s1': 10, 's2': 5})))
        characters = [record('a', 'Нина'), record('b', 'Нина Ивановна'), record('c', 'Нина')]
        self.assertEqual([('a', 'c'), ('a', 'b'), ('b', 'c')], list(alias_candidates(characters, {'a': 3, 'c': 4})))


class Reconcile(unittest.TestCase):
    def jobs(self, decide):
        class Engine:
            def __init__(self):
                self.payloads = []
            def call(self, method, payload, stop):
                self.payloads.append(payload)
                reply = decide(payload)
                if isinstance(reply, Exception):
                    raise reply
                return reply
        jobs = Jobs(None, Path('.'))
        jobs.engine = Engine()
        jobs.stop = threading.Event()
        return jobs

    def same(self, payload):
        return {'same': True, 'evidence': [next(iter(passages)) for passages in payload['evidence'].values()]}

    def texts(self, characters):
        return {pid: 'text' for character in characters for pid in character['evidence']}

    def test_specific_pair_merges_although_a_generic_clique_exceeds_the_limit(self):
        characters = [record(f'r{index:02d}', f'Номер{index}', 'боец') for index in range(24)]
        characters += [record('l0', 'Лозовский'), record('l1', 'Иван Лозовский')]
        jobs = self.jobs(lambda payload: self.same(payload) if ids([payload]) == [['l0', 'l1']] else {'same': False, 'evidence': []})
        cast, merges, unmerged, skipped, state = jobs._reconcile({}, characters, self.texts(characters))
        self.assertEqual(({'l1': 'l0'}, 0, 21, ALIAS_COMPARISON_LIMIT), (merges, unmerged, skipped, len(jobs.engine.payloads)))
        self.assertEqual(['l0', 'l1'], ids(jobs.engine.payloads)[0])

    def test_capacity_failure_of_a_group_check_is_not_resent(self):
        characters = [record('a', 'Анна'), record('b', 'Анна Петровна'), record('c', 'Анна Сергеевна'), record('d', 'Анна'), record('e', 'Анна')]
        jobs = self.jobs(lambda payload: AnalysisCapacityError('full') if ids([payload]) == [['b', 'c']] else self.same(payload))
        cast, merges, unmerged, skipped, state = jobs._reconcile({}, characters, self.texts(characters))
        self.assertEqual([['a', 'b'], ['a', 'c'], ['b', 'c'], ['a', 'd'], ['a', 'e']], ids(jobs.engine.payloads))
        self.assertEqual(({'b': 'a', 'd': 'a', 'e': 'a'}, 1, 0), (merges, unmerged, skipped))
        self.assertEqual([['a', 'c']], state['unmerged_pairs'])
        self.assertEqual([['a', 'b', True], ['a', 'c', True], ['b', 'c', None], ['a', 'd', True], ['a', 'e', True]], state['reconcile']['decisions'])

    def test_capacity_failure_between_groups_judged_different_is_not_counted(self):
        characters = [record('a', 'Анна'), record('b', 'Анна'), record('c', 'Анна')]
        replies = {('a', 'b'): AnalysisCapacityError('full'), ('b', 'c'): {'same': False, 'evidence': []}}
        jobs = self.jobs(lambda payload: replies.get(tuple(ids([payload])[0])) or self.same(payload))
        cast, merges, unmerged, skipped, state = jobs._reconcile({}, characters, self.texts(characters))
        self.assertEqual(({'c': 'a'}, 0, []), (merges, unmerged, state['unmerged_pairs']))
        self.assertEqual(3, len(jobs.engine.payloads))

    def test_limit_skips_and_capacity_failures_are_kept_as_unmerged_pairs(self):
        characters = [record(f'c{index:02d}', 'Анна') for index in range(24)]
        jobs = self.jobs(lambda payload: AnalysisCapacityError('full') if ids([payload]) == [['c00', 'c01']] else {'same': False, 'evidence': []})
        cast, merges, unmerged, skipped, state = jobs._reconcile({}, characters, self.texts(characters))
        pairs = list(alias_candidates(characters))
        self.assertEqual((1, 276 - ALIAS_COMPARISON_LIMIT), (unmerged, skipped))
        self.assertEqual([['c00', 'c01'], *map(list, pairs[ALIAS_COMPARISON_LIMIT:])], state['unmerged_pairs'])
        self.assertEqual({'compared': ALIAS_COMPARISON_LIMIT, 'skipped': 20}, {key: state['reconcile'][key] for key in ('compared', 'skipped')})

    def test_every_comparison_is_saved_with_progress(self):
        characters = [record('a', 'Анна'), record('b', 'Анна Петровна'), record('c', 'Борис'), record('d', 'Борис Котов')]
        saves = []
        jobs = self.jobs(lambda payload: {'same': False, 'evidence': []} if ids([payload]) == [['c', 'd']] else self.same(payload))
        result = jobs._reconcile({}, characters, self.texts(characters), save=saves.append)
        progress = [{'stage': 'reconcile', 'done': done, 'total': 2} for done in range(3)]
        self.assertEqual([{'progress': progress[0]},
                          {'reconcile': {'decisions': [['a', 'b', True]], 'compared': 1, 'skipped': 0}, 'progress': progress[1]},
                          {'reconcile': {'decisions': [['a', 'b', True], ['c', 'd', False]], 'compared': 2, 'skipped': 0}, 'progress': progress[2]}],
                         saves)
        self.assertEqual(saves[-1]['reconcile'], result[4]['reconcile'])
        self.assertEqual({'b': 'a'}, result[1])

    def test_resume_replays_saved_decisions_without_model_calls(self):
        characters = [record('a', 'Анна'), record('b', 'Анна Петровна'), record('c', 'Борис'), record('d', 'Борис Котов'),
                      record('e', 'Глеб'), record('f', 'Глеб Орлов')]
        saves = []
        jobs = self.jobs(self.same)
        def pause(update):
            saves.append(update)
            if 'reconcile' in update:
                jobs.stop.set()
        with self.assertRaisesRegex(AppError, 'Preparation paused.'):
            jobs._reconcile({}, characters, self.texts(characters), save=pause)
        self.assertEqual([['a', 'b']], ids(jobs.engine.payloads))
        resumed = self.jobs(self.same)
        cast, merges, unmerged, skipped, state = resumed._reconcile({}, characters, self.texts(characters), saved=saves[-1]['reconcile'])
        self.assertEqual([['c', 'd'], ['e', 'f']], ids(resumed.engine.payloads))
        self.assertEqual({'b': 'a', 'd': 'c', 'f': 'e'}, merges)
        self.assertEqual({'decisions': [['a', 'b', True], ['c', 'd', True], ['e', 'f', True]], 'compared': 3, 'skipped': 0}, state['reconcile'])

    def test_saved_capacity_failures_and_differences_are_replayed(self):
        characters = [record('a', 'Анна'), record('b', 'Анна Петровна'), record('c', 'Борис'), record('d', 'Борис Котов')]
        jobs = self.jobs(self.same)
        saved = {'decisions': [['a', 'b', None], ['c', 'd', False]], 'compared': 2, 'skipped': 0}
        cast, merges, unmerged, skipped, state = jobs._reconcile({}, characters, self.texts(characters), saved=saved)
        self.assertEqual(([], {}, 1, [['a', 'b']]), (jobs.engine.payloads, merges, unmerged, state['unmerged_pairs']))

    def test_invalid_saved_decisions_are_ignored(self):
        characters = [record('a', 'Анна'), record('b', 'Анна Петровна')]
        for saved in ({'decisions': [['a', 'zzz', True], ['a', 'b', 'yes'], ['a', 'b', 1], 'junk', ['a', 'a', True], [['a'], 'b', True]]},
                      {'decisions': 'junk'}, ['a', 'b', True], None):
            with self.subTest(saved=saved):
                jobs = self.jobs(self.same)
                self.assertEqual({'b': 'a'}, jobs._reconcile({}, characters, self.texts(characters), saved=saved)[1])
                self.assertEqual([['a', 'b']], ids(jobs.engine.payloads))


class GuestEngine(AliasEngine):
    added = False
    def call(self, method, payload, stop):
        result = super().call(method, payload, stop)
        cited = next((segment['id'] for segment in payload.get('segments', []) if 'Иван' in segment['text']), None)
        if method == 'analyze' and cited and not self.added:
            self.added = True
            result['characters'].append({'id': 'new_3', 'name': 'Иван', 'aliases': [], 'description': '', 'evidence': [cited], 'kind': 'person',
                                         'gender': {'value': 'unknown', 'evidence': []}, 'age_band': {'value': 'unknown', 'evidence': []}})
        return result


class ReconcileCheckpoint(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'library')
        self.store.update_settings({'cast_review': False})
        self.book = self.store.import_data(FB2, 'book.fb2')
        pack = pack_fixture(self.root / 'pack')
        self.generation = self.store.create_generation(self.book['id'], {'device': 'cpu', 'pack_path': pack['_root'],
                                                                         'pack_fingerprint': pack['_fingerprint']})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def run_job(self, engine):
        jobs = Jobs(self.store, self.root, engine_factory=lambda _: engine)
        engine.jobs = jobs
        jobs.start(self.generation['id'])
        jobs.thread.join(20)
        self.assertFalse(jobs.thread.is_alive())
        return self.store.generation(self.generation['id'])

    def test_pause_after_a_comparison_keeps_it_for_resume(self):
        engine = GuestEngine()
        engine.decide = lambda payload: engine.jobs.stop.set() or {'same': False, 'evidence': []}
        paused = self.run_job(engine)
        self.assertEqual(('paused', False, 1), (paused['status'], paused['analysis']['complete'], len(engine.reconciled)))
        first = ids(engine.reconciled)[0]
        self.assertEqual([[*first, False]], paused['analysis']['reconcile']['decisions'])
        self.assertEqual({'stage': 'reconcile', 'done': 1, 'total': 3}, paused['analysis']['progress'])
        engine.decide = lambda payload: {'same': False, 'evidence': []}
        ready = self.run_job(engine)
        self.assertEqual('ready', ready['status'], ready['error'])
        self.assertEqual(3, len(engine.reconciled))
        self.assertEqual(3, len({tuple(pair) for pair in ids(engine.reconciled)}))
        analysis = ready['analysis']
        self.assertEqual((True, 3, 0, []), (analysis['complete'], analysis['reconcile']['compared'], analysis['reconcile']['skipped'],
                                            analysis['unmerged_pairs']))
        self.assertEqual([*first, False], analysis['reconcile']['decisions'][0])
        self.assertNotIn('progress', analysis)

    def test_pause_while_casting_keeps_the_merge_decision(self):
        engine = AliasEngine()
        engine.decide = lambda payload: engine.jobs.stop.set() or True
        paused = self.run_job(engine)
        analysis = paused['analysis']
        self.assertEqual(('paused', False, [], {'stage': 'casting', 'done': 0, 'total': 1}),
                         (paused['status'], analysis['complete'], analysis['unmerged_pairs'], analysis['progress']))
        [decision] = analysis['reconcile']['decisions']
        self.assertTrue(decision[2])
        self.assertEqual(2, len({segment['speaker'] for segment in paused['segments']} - {'narrator'}))
        resumed = self.run_job(engine)
        self.assertEqual('ready', resumed['status'], resumed['error'])
        self.assertEqual((1, [decision]), (len(engine.reconciled), resumed['analysis']['reconcile']['decisions']))
        self.assertEqual(1, len({segment['speaker'] for segment in resumed['segments']} - {'narrator'}))


if __name__ == '__main__':
    unittest.main()
