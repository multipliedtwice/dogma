from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from test_core import NEUTRAL_CONTEXT, agreeing_critic, pack_fixture
from rimsky.analysis import boundary_units
from rimsky.util import AnalysisCapacityError, AppError, EngineTransportError, ProviderTransportError, ProviderUnavailableError

SPEC = importlib.util.spec_from_file_location('evaluate_speakers', Path(__file__).resolve().parents[1] / 'scripts' / 'evaluate_speakers.py')
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)

CAST = [{'id': 'g_masha', 'name': 'Маша Петрова', 'aliases': ['Маша'], 'description': '', 'evidence': []},
        {'id': 'g_ivan', 'name': 'Иван', 'aliases': [], 'description': '', 'evidence': []},
        {'id': 'g_robot', 'name': 'Робот', 'aliases': [], 'description': '', 'evidence': []}]
LONG = ' '.join(['Маша долго смотрела в окно и думала о прошлом.'] * 12)


def case(cid, chapter, pieces, before=(), after=()):
    parts, start = [], 0
    for piece, speaker in pieces:
        parts.append({'start': start, 'end': start + len(piece), 'mode': 'narrator' if speaker == 'narrator' else 'speech', 'speaker': speaker, 'text': piece})
        start += len(piece)
    context = lambda items: [{'id': sid, 'source_id': sid, 'text': text} for sid, text in items]
    return {'id': cid, 'book_id': 'synthetic', 'chapter_id': chapter, 'chapter_title': chapter, 'source_id': cid,
            'text': ''.join(piece for piece, _ in pieces), 'before': context(before), 'after': context(after), 'parts': parts}


SELECTION = [case('c1-p2', 'c1', [('— Пойдём домой, ', 'g_masha'), ('— сказала Маша. ', 'narrator'), ('— Уже поздно.', 'g_masha')],
                  before=[('c1-p1', 'Вечер был тихим.')], after=[('c1-p3', 'Иван кивнул.')]),
             case('c1-p4', 'c1', [('— Кто там? ', 'g_ivan'), ('— спросил Иван.', 'narrator')], before=[('c1-p3', 'Иван кивнул.')]),
             case('c2-p1', 'c2', [('Робот ответил: ', 'narrator'), ('«Доступ запрещён».', 'g_robot')]),
             case('c2-p2', 'c2', [(LONG, 'narrator')]),
             case('c2-p3', 'c2', [('— Стой! ', 'unknown'), ('— донеслось из темноты.', 'narrator')])]
TEST = [case('c3-p1', 'c3', [('— Это тайна, ', 'g_ivan'), ('— шепнул Иван.', 'narrator')], before=[('c3-p0', 'Ночь опустилась на лес.')]),
        case('c3-p2', 'c3', [('Никто не ответил.', 'narrator')])]


def write_gold(folder, selection=SELECTION, test=TEST, protocol=None, manifest=None):
    folder.mkdir(parents=True)
    gold = {'schema': 1, 'cast': CAST, 'cases': [*selection, *test], 'content_sha256': 'c' * 64}
    raw = json.dumps(gold, ensure_ascii=False).encode()
    (folder / 'gold.json').write_bytes(raw)
    (folder / 'manifest.json').write_text(json.dumps(manifest or {'sha256': hashlib.sha256(raw).hexdigest()}), 'utf-8')
    (folder / 'evaluation-protocol.json').write_text(json.dumps(protocol or {
        'gold_content_sha256': 'c' * 64, 'selection_case_ids': [item['id'] for item in selection], 'test_case_ids': [item['id'] for item in test],
        'selection_note': 'Synthetic selection note.'}), 'utf-8')
    return folder


class Oracle:
    def __init__(self, cases, names=None, same=False):
        self.gold = {item['source_id']: item for item in cases}
        self.names = {'g_masha': 'Маша', 'g_ivan': 'Иван', 'g_robot': 'Робот'} | (names or {})
        self.same = same
        self.calls = []
        self.critiques = []

    def labels(self, target):
        gold = self.gold[target['source_id']]
        for start, end in boundary_units(target['text']):
            low, high = target['start'] + start, target['start'] + end
            weights = Counter()
            for part in gold['parts']:
                weights[part['speaker']] += sum(char.isalnum() for char in gold['text'][max(low, part['start']):min(high, part['end'])])
            yield max(+weights, key=weights.get) if +weights else 'narrator'

    def passage(self, target, known, characters):
        groups = []
        for index, speaker in enumerate(self.labels(target)):
            if groups and groups[-1][0] == speaker:
                groups[-1][2] = index
            else:
                groups.append([speaker, index, index])
        parts = []
        for speaker, first, last in groups:
            if speaker not in ('narrator', 'unknown'):
                name = self.names[speaker]
                if name not in known:
                    known[name] = f'new_{len(characters) + 1}'
                    characters.append({'id': known[name], 'name': name, 'aliases': [], 'description': '', 'evidence': [target['id']],
                                       'kind': 'mechanical' if speaker == 'g_robot' else 'person',
                                       'gender': {'value': 'unknown', 'evidence': []}, 'age_band': {'value': 'unknown', 'evidence': []}})
                speaker = known[name]
            parts.append({'first': first, 'last': last, 'speaker': speaker, 'delivery': 'neutral',
                          'evidence': [] if speaker in ('narrator', 'unknown') else [target['id']], 'context': dict(NEUTRAL_CONTEXT)})
        return {'id': target['id'], 'parts': parts}

    def analyze(self, payload):
        known, characters = {item['name']: item['id'] for item in payload['cast']}, []
        return {'characters': characters, 'passages': [self.passage(target, known, characters) for target in payload['segments']]}

    def call(self, method, payload, stop):
        if method == 'critique':
            self.critiques.append(payload)
            return agreeing_critic(payload)
        self.calls.append((method, payload))
        if method == 'analyze':
            return self.analyze(payload)
        if method == 'reconcile':
            first, second = (list(payload['evidence'][item['id']])[:1] for item in payload['characters'])
            return {'same': True, 'evidence': first + second} if self.same else {'same': False, 'evidence': []}
        raise AppError('Unexpected operation.')

    def close(self):
        pass

    def texts(self):
        return {item['text'] for method, payload in self.calls if method == 'analyze'
                for item in [*payload['segments'], *payload['before'], *payload['after']]}


class Faulty(Oracle):
    def __init__(self, cases, broken, **options):
        super().__init__(cases, **options)
        self.broken = dict(broken)

    def analyze(self, payload):
        reply = super().analyze(payload)
        for passage in reply['passages']:
            if self.broken.get(passage['id'], 0):
                self.broken[passage['id']] -= 1
                for part in passage['parts']:
                    part['evidence'] = []
                    part['delivery'] = 'angry'
        return reply


class Failing(Oracle):
    def __init__(self, cases, failure, when, times=1):
        super().__init__(cases)
        self.failure, self.when, self.times = failure, when, times

    def analyze(self, payload):
        if self.times and self.when(payload):
            self.times -= 1
            raise self.failure
        return super().analyze(payload)


def rename(reply, payload):
    if any(target['source_id'] == 'c1-p9' for target in payload['segments']):
        for character in reply['characters']:
            character['name'], character['id'] = 'Маша', 'new_1'
        for passage in reply['passages']:
            for part in passage['parts']:
                if part['speaker'] not in ('narrator', 'unknown'):
                    part['speaker'] = 'new_1'
        if not reply['characters']:
            reply['characters'].append({'id': 'new_1', 'name': 'Маша', 'aliases': [], 'description': '', 'evidence': [payload['segments'][0]['id']],
                                        'kind': 'person', 'gender': {'value': 'unknown', 'evidence': []}, 'age_band': {'value': 'unknown', 'evidence': []}})
    return reply


def run(cases, engine, reconcile=True, secondary=None):
    with tempfile.TemporaryDirectory() as temporary:
        pack = pack_fixture(Path(temporary) / 'pack')
        return harness.evaluate(cases, CAST, pack, lambda binaries: engine, 'cpu', reconcile, Path(temporary) / 'runtime', secondary=secondary)


class Second(Oracle):
    def __init__(self, cases, swap=(), failure=None, **options):
        super().__init__(cases, **options)
        self.swap, self.failure = set(swap), failure

    def analyze(self, payload):
        reply = super().analyze(payload)
        if payload.get('analysis_mode') != 'claude_cli':
            return reply
        if self.failure:
            raise self.failure
        for passage in reply['passages']:
            if passage['id'] in self.swap:
                for part in passage['parts']:
                    if part['speaker'] not in ('narrator', 'unknown'):
                        part['speaker'], part['evidence'] = 'unknown', []
        return reply


class Repairing(Second):
    def __init__(self, cases, broken):
        super().__init__(cases)
        self.broken = dict(broken)

    def analyze(self, payload):
        reply = super().analyze(payload)
        if payload.get('analysis_mode') == 'claude_cli':
            return reply
        for passage in reply['passages']:
            if self.broken.get(passage['id'], 0):
                self.broken[passage['id']] -= 1
                for part in passage['parts']:
                    part['evidence'], part['delivery'] = [], 'angry'
        return reply


AGREEMENT = [case('c1-p2', 'c1', [('— Пойдём домой, ', 'g_masha'), ('— сказала Маша. ', 'narrator'), ('— Уже поздно.', 'g_masha')]),
             case('c1-p4', 'c1', [('— Иду, ', 'g_masha'), ('— ответила Маша.', 'narrator')]),
             case('c1-p5', 'c1', [('— Скорее, ', 'g_masha'), ('— сказала Маша.', 'narrator')])]


class Scoring(unittest.TestCase):
    def test_oracle_answers_score_perfectly_and_map_by_name(self):
        engine = Oracle(SELECTION)
        report = run(SELECTION, engine)
        self.assertEqual({'gold': 5, 'predicted': 5, 'matched': 5, 'precision': 1.0, 'recall': 1.0, 'f1': 1.0}, report['boundaries'])
        self.assertEqual({'spans': 4, 'correct': 4, 'accuracy': 1.0, 'character': 0, 'narrator': 0, 'unknown': 0, 'degraded': 0}, report['speakers'])
        self.assertEqual({'spans': 1, 'predicted_unresolved': 1}, report['gold_unresolved'])
        self.assertEqual({'g_masha', 'g_ivan', 'g_robot'}, {entry['gold'] for entry in report['mapping'].values()})
        self.assertEqual({'name'}, {entry['by'] for entry in report['mapping'].values()})
        self.assertEqual((0, None, [], []), (report['degraded']['targets'], report['stopped'], report['errors'], report['degraded']['passages']))
        self.assertEqual(6, report['targets'])

    def test_unmatched_names_map_by_majority_overlap(self):
        cases = [case('c1-p4', 'c1', [('— Кто там? ', 'g_ivan'), ('— спросил незнакомец в плаще.', 'narrator')]) if item['id'] == 'c1-p4' else item
                 for item in SELECTION]
        report = run(cases, Oracle(cases, names={'g_ivan': 'Незнакомец в плаще'}))
        self.assertEqual(1.0, report['speakers']['accuracy'])
        ivan = next(entry for entry in report['mapping'].values() if entry['name'] == 'Незнакомец в плаще')
        self.assertEqual({'name': 'Незнакомец в плаще', 'gold': 'g_ivan', 'by': 'overlap'}, ivan)

    def test_whitespace_and_punctuation_around_a_boundary_do_not_matter(self):
        self.assertEqual({4}, harness.cuts('ab  cd', [(2, True), (6, False)]))
        self.assertEqual({4}, harness.cuts('ab  cd', [(3, True), (6, False)]))
        self.assertEqual({6}, harness.cuts('ab, — cd', [(3, True), (8, False)]))
        self.assertEqual({6}, harness.cuts('ab, — cd', [(5, True), (8, False)]))
        self.assertEqual(set(), harness.cuts('ab  ', [(2, True), (4, False)]))
        self.assertEqual(set(), harness.cuts('abcd', [(2, None), (4, False)]))

    def test_alias_reconciliation_merges_are_applied_before_scoring(self):
        names = {'g_masha': 'Маша Петрова'}
        first = case('c1-p8', 'c1', [('— Пора, ', 'g_masha'), ('— сказала Маша Петрова.', 'narrator')])
        second = case('c1-p9', 'c1', [('— Иду, ', 'g_masha'), ('— ответила Маша.', 'narrator')])
        engine = Oracle([first, second], names=names, same=True)
        engine.analyze = lambda payload, original=engine.analyze: rename(original(payload), payload)
        report = run([first, second], engine)
        self.assertEqual({'merges': 1, 'unmerged': 0, 'skipped': 0}, report['reconcile'])
        self.assertEqual(1, len(report['mapping']))
        self.assertEqual(1.0, report['speakers']['accuracy'])
        self.assertTrue(any(method == 'reconcile' for method, _ in engine.calls))


class BlindAgreementReport(unittest.TestCase):
    def test_paired_report_scores_the_gate_and_the_same_run_without_it(self):
        engine = Second(AGREEMENT, swap={'c1-p5-0-25'})
        report = run(AGREEMENT, engine, secondary='claude_cli')
        modes = Counter(payload['analysis_mode'] for method, payload in engine.calls if method == 'analyze')
        self.assertEqual(modes['local'], modes['claude_cli'])
        control = report['single_provider_control']['acceptance']
        self.assertEqual((4, 0, 1.0), (control['accepted'], control['accepted_wrong'], control['useful_coverage']))
        self.assertEqual((1, 0, 3), (report['acceptance']['accepted'], report['acceptance']['accepted_wrong'], report['acceptance']['held']))
        self.assertEqual({'provider_new_identity': 2, 'provider_disagreement': 1}, {key: value for key, value in report['holds'].items() if key.startswith('provider_')})
        self.assertFalse(any(key.startswith('provider_') for key in report['single_provider_control']['holds']))
        self.assertEqual({'c1-p2', 'c1-p4', 'c1-p5'}, set(report['proposals']))
        self.assertTrue(any(record.get('secondary_speaker') == 'unknown' for record in report['records']))

    def test_second_provider_never_receives_the_primary_repair_and_its_failure_holds(self):
        engine = Repairing(AGREEMENT, {'c1-p4-0-23': 1})
        run(AGREEMENT, engine, secondary='claude_cli')
        second = [payload for method, payload in engine.calls if method == 'analyze' and payload['analysis_mode'] == 'claude_cli']
        self.assertTrue(second and not any(payload['retry_error'] or 'retry_response' in payload for payload in second))
        self.assertTrue(any(payload.get('retry_error') for method, payload in engine.calls if method == 'analyze' and payload['analysis_mode'] == 'local'))
        failed = run(AGREEMENT, Second(AGREEMENT, failure=ProviderUnavailableError('claude CLI is not installed.')), secondary='claude_cli')
        self.assertEqual((None, 0), (failed['stopped'], failed['acceptance']['accepted']))
        self.assertEqual(3, failed['holds']['provider_unavailable'])
        self.assertEqual(4, failed['single_provider_control']['acceptance']['accepted'])
        broken = run(AGREEMENT, Second(AGREEMENT, failure=ProviderTransportError('Claude character analysis failed: 500')), secondary='claude_cli')
        self.assertEqual(({'case': 'c1-p2', 'error': 'Claude character analysis failed: 500'}, 0), (broken['stopped'], broken['cases']))

    def test_report_round_trips_both_proposals_and_the_unrepaired_gate(self):
        class OnceInvalid(Second):
            def analyze(self, payload):
                reply = super().analyze(payload)
                if payload.get('analysis_mode') == 'claude_cli' and not payload.get('retry_error'):
                    for passage in reply['passages']:
                        for part in passage['parts']:
                            part['evidence'], part['delivery'] = [], 'angry'
                return reply
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = write_gold(root / 'gold', selection=AGREEMENT, test=TEST)
            pack_fixture(root / 'pack')
            output, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                code = harness.main(['--gold', str(folder), '--pack', str(root / 'pack'), '--device', 'cpu', '--secondary', 'claude_cli',
                                     '--secondary-repair', 'own-v1', '--out', str(root / 'report.json')], lambda binaries: OnceInvalid(AGREEMENT + TEST))
            report = json.loads((root / 'report.json').read_text('utf-8'))
        self.assertEqual(0, code, errors.getvalue())
        self.assertEqual(('claude_cli', 'own-v1', 'blind_agreement'), tuple(report['configuration'][key] for key in ('secondary', 'secondary_repair', 'gate')))
        self.assertEqual(3, len(report['agreement_audit']))
        entry = report['agreement_audit'][0]
        self.assertEqual([('original', False), ('repair', True)], [(item['kind'], item['valid']) for item in entry['secondary']])
        self.assertTrue(entry['secondary'][0]['error'].startswith('Passage c1-p2'))
        self.assertEqual(entry['target_id'], entry['primary'][0]['passage']['id'])
        self.assertEqual(('c1-p2', 0, 45), (entry['source_id'], entry['start'], entry['end']))
        self.assertEqual(0, report['first_secondary_outcome']['acceptance']['accepted'])
        self.assertEqual(2, report['acceptance']['accepted'])
        self.assertIn('Gate with the unrepaired second answers: accepted 0', output.getvalue())

    def test_alias_merge_after_agreement_holds_the_merged_spans(self):
        names = {'g_masha': 'Маша Петрова'}
        first = case('c1-p8', 'c1', [('— Пора, ', 'g_masha'), ('— сказала Маша Петрова.', 'narrator')])
        second = case('c1-p9', 'c1', [('— Иду, ', 'g_masha'), ('— ответила Маша.', 'narrator')])
        third = case('c1-pa', 'c1', [('— Да, ', 'g_masha'), ('— кивнула Маша.', 'narrator')])
        def short_name(reply, payload):
            reply = rename(reply, payload)
            short = next((item['id'] for item in payload['cast'] if item['name'] == 'Маша'), None)
            if short and any(target['source_id'] == 'c1-pa' for target in payload['segments']):
                reply['characters'] = []
                for passage in reply['passages']:
                    for part in passage['parts']:
                        if part['speaker'] not in ('narrator', 'unknown'):
                            part['speaker'] = short
            return reply
        engine = Second([first, second, third], names=names, same=True)
        engine.analyze = lambda payload, original=engine.analyze: short_name(original(payload), payload)
        report = run([first, second, third], engine, secondary='claude_cli')
        self.assertEqual(1, report['reconcile']['merges'])
        self.assertEqual(1, report['holds']['alias_merge_after_agreement'])
        self.assertEqual((0, 0), (report['acceptance']['accepted'], report['acceptance']['accepted_wrong']))
        self.assertEqual(3, report['single_provider_control']['acceptance']['accepted'])


class ChapterOrder(unittest.TestCase):
    def book(self, cases):
        chapters = {}
        for item in cases:
            blocks = chapters.setdefault(item['chapter_id'], [])
            for block in [*item['before'], {'source_id': item['source_id'], 'text': item['text']}, *item['after']]:
                if all(existing['id'] != block['source_id'] for existing in blocks):
                    blocks.append({'id': block['source_id'], 'kind': 'paragraph', 'text': block['text']})
        return {'chapters': [{'id': cid, 'title': cid, 'blocks': blocks} for cid, blocks in chapters.items()]}

    def test_whole_chapters_run_in_book_order_and_only_split_cases_are_scored(self):
        cases = [{**item, 'source_sha256': hashlib.sha256(item['text'].encode()).hexdigest()} for item in SELECTION]
        engine = Oracle(SELECTION + [case('c1-p1', 'c1', [('Вечер был тихим.', 'narrator')]), case('c1-p3', 'c1', [('Иван кивнул.', 'narrator')])])
        with tempfile.TemporaryDirectory() as temporary:
            pack = pack_fixture(Path(temporary) / 'pack')
            report = harness.evaluate(cases, CAST, pack, lambda binaries: engine, 'cpu', True, Path(temporary) / 'runtime', book=self.book(cases))
        targets = [item['source_id'] for method, payload in engine.calls if method == 'analyze' for item in payload['segments']]
        self.assertIn('c1-p1', targets)
        self.assertLess(targets.index('c1-p4'), targets.index('c2-p1'))
        self.assertEqual((5, 1.0), (report['cases'], report['speakers']['accuracy']))
        self.assertEqual(6, report['targets'])

    def test_unscored_earlier_and_bridging_chapters_carry_the_registry_and_non_narrative_ones_stay_out(self):
        prefix = case('c0-p1', 'c0', [('— Я Маша Петрова, ', 'g_masha'), ('— сказала Маша Петрова.', 'narrator')])
        bridge = case('c1-p1', 'c1', [('— Кто там? ', 'g_ivan'), ('— спросил Иван.', 'narrator')])
        scored = case('c2-p1', 'c2', [('— Пора, ', 'g_masha'), ('— сказала Маша Петрова.', 'narrator')])
        scored = {**scored, 'source_sha256': hashlib.sha256(scored['text'].encode()).hexdigest()}
        appendix = {'id': 'c9', 'title': 'Примечания', 'default_narration': False, 'blocks': [{'id': 'c9-p1', 'kind': 'paragraph', 'text': '— Нет, — сказал Иван.'}]}
        book = {'chapters': [*self.book([prefix, bridge, scored])['chapters'], appendix]}
        engine = Oracle([prefix, bridge, scored, case('c9-p1', 'c9', [('— Нет, ', 'g_ivan'), ('— сказал Иван.', 'narrator')])], names={'g_masha': 'Маша Петрова'})
        with tempfile.TemporaryDirectory() as temporary:
            pack = pack_fixture(Path(temporary) / 'pack')
            report = harness.evaluate([scored], CAST, pack, lambda binaries: engine, 'cpu', True, Path(temporary) / 'runtime', book=book)
            asks = [payload for method, payload in engine.calls if method == 'analyze']
            self.assertEqual(['c0-p1', 'c1-p1', 'c2-p1'], [item['source_id'] for payload in asks for item in payload['segments']])
            self.assertEqual({'Маша Петрова', 'Иван'}, {entry['name'] for entry in asks[-1]['cast']})
            self.assertEqual((1, 1, 1.0), (report['cases'], report['attempted_cases'], report['speakers']['accuracy']))
            explicit = harness.work_plan([{**scored, 'chapter_id': 'c9', 'source_id': 'c9-p1'}], book)
            self.assertEqual(['c0', 'c1', 'c2', 'c9'], [work['chapter'] for work in explicit])

    def test_stopped_run_still_exports_attempted_audit(self):
        class StopsOnSecond(Second):
            def analyze(self, payload):
                if payload.get('analysis_mode') == 'claude_cli' and any(item['source_id'] == 'c1-p4' for item in payload['segments']):
                    raise ProviderTransportError('Claude character analysis failed: 500')
                return super().analyze(payload)
        report = run(AGREEMENT, StopsOnSecond(AGREEMENT), secondary='claude_cli')
        report = json.loads(json.dumps(report, ensure_ascii=False))
        self.assertEqual(({'case': 'c1-p4', 'error': 'Claude character analysis failed: 500'}, 1, 2), (report['stopped'], report['cases'], report['attempted_cases']))
        stopped = [entry for entry in report['agreement_audit'] if entry['case'] == 'c1-p4']
        self.assertEqual(1, len(stopped))
        self.assertEqual(([True], ['ProviderTransportError']), ([item['valid'] for item in stopped[0]['primary']], [item['category'] for item in stopped[0]['secondary']]))
        self.assertNotIn('terminal', stopped[0])

    def interrupted(self, cases, book, engine, secondary='claude_cli'):
        with tempfile.TemporaryDirectory() as temporary:
            pack = pack_fixture(Path(temporary) / 'pack')
            report = harness.evaluate(cases, CAST, pack, lambda binaries: engine, 'cpu', False, Path(temporary) / 'runtime', secondary=secondary, book=book)
        return json.loads(json.dumps(report, ensure_ascii=False))

    def scenes(self, count):
        cases = [case(f'c1-p{index}', 'c1', [('— Да, ', 'g_ivan'), ('— сказал Иван.', 'narrator')]) for index in range(count)]
        blocks = []
        for index, item in enumerate(cases):
            if index:
                blocks.append({'id': f'break{index}', 'kind': 'separator', 'text': ''})
            blocks.append({'id': item['source_id'], 'kind': 'paragraph', 'text': item['text']})
        return [{**item, 'source_sha256': ''} for item in cases], {'chapters': [{'id': 'c1', 'blocks': blocks}]}

    def test_interrupted_chapter_counts_only_invoked_cases_with_and_without_the_gate(self):
        class FailsOn(Oracle):
            def __init__(self, cases, failing):
                super().__init__(cases)
                self.failing = failing
            def analyze(self, payload):
                if any(item['source_id'] == self.failing for item in payload['segments']) and payload.get('analysis_mode') != 'claude_cli':
                    raise ProviderTransportError('Controlled failure.')
                return super().analyze(payload)
        cases, book = self.scenes(3)
        for failing, expected in (('c1-p0', (0, 1, 3, ['c1-p0'])), ('c1-p1', (0, 2, 3, ['c1-p0', 'c1-p1']))):
            for secondary in ('claude_cli', None):
                with self.subTest(failing=failing, secondary=secondary):
                    report = self.interrupted(cases, book, FailsOn(cases, failing), secondary)
                    self.assertEqual(expected[:3], (report['cases'], report['attempted_cases'], report['planned_cases']))
                    self.assertEqual(expected[3], [item['target_id'].rsplit('-', 2)[0] for item in report['attempted_targets']])
                    self.assertEqual(expected[3] if secondary else [], [entry['case'] for entry in report['agreement_audit']])
                    self.assertEqual('c1', report['stopped']['chapter'])

    def test_capacity_split_then_later_failure_keeps_partial_commit_unscored_and_both_attempted(self):
        class SplitThenFail(Oracle):
            def analyze(self, payload):
                if len(payload['segments']) > 1:
                    raise AnalysisCapacityError('Too large.')
                if payload['segments'][0]['source_id'] == 'c1-p1' and payload.get('analysis_mode') != 'claude_cli':
                    raise ProviderTransportError('Controlled failure.')
                return super().analyze(payload)
        cases = [{**case(f'c1-p{index}', 'c1', [('— Да, ', 'g_ivan'), ('— сказал Иван.', 'narrator')]), 'source_sha256': ''} for index in range(2)]
        book = {'chapters': [{'id': 'c1', 'blocks': [{'id': item['source_id'], 'kind': 'paragraph', 'text': item['text']} for item in cases]}]}
        report = self.interrupted(cases, book, SplitThenFail(cases))
        self.assertEqual((0, 2, 2), (report['cases'], report['attempted_cases'], report['planned_cases']))
        audit = {entry['case']: entry for entry in report['agreement_audit']}
        self.assertEqual('analysed', audit['c1-p0']['terminal'])
        self.assertEqual(['analysis_capacity', 'ProviderTransportError'], [item['category'] for item in audit['c1-p1']['primary']])
        self.assertNotIn('terminal', audit['c1-p1'])

    def test_unscored_prefix_failure_keeps_its_source_bound_audit(self):
        class StopsImmediately(Oracle):
            def analyze(self, payload):
                raise ProviderTransportError('Controlled first-call failure.')
        prefix = case('c0-p0', 'c0', [('— Да, ', 'g_ivan'), ('— сказал Иван.', 'narrator')])
        selected = {**case('c1-p0', 'c1', [('— Да, ', 'g_ivan'), ('— сказал Иван.', 'narrator')]), 'source_sha256': ''}
        book = {'chapters': [{'id': item['chapter_id'], 'blocks': [{'id': item['source_id'], 'kind': 'paragraph', 'text': item['text']}]} for item in (prefix, selected)]}
        report = self.interrupted([selected], book, StopsImmediately([prefix, selected]))
        self.assertEqual((0, 0, 1), (report['cases'], report['attempted_cases'], report['planned_cases']))
        self.assertEqual(('', 'c0'), (report['stopped']['case'], report['stopped']['chapter']))
        self.assertEqual([('c0-p0-0-20', 'c0', None, 'ProviderTransportError')],
                         [(entry['target_id'], entry['chapter_id'], entry.get('case'), entry['primary'][0]['category']) for entry in report['agreement_audit']])
        self.assertEqual([{'target_id': 'c0-p0-0-20', 'chapter_id': 'c0'}], report['attempted_targets'])

    def test_only_the_frozen_book_file_is_accepted(self):
        cases = [{**item, 'source_sha256': hashlib.sha256(item['text'].encode()).hexdigest()} for item in SELECTION]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            book_path = root / 'training-book.json'
            book_path.write_text(json.dumps(self.book(cases), ensure_ascii=False), 'utf-8')
            folder = root / 'gold'
            folder.mkdir()
            gold = {'sources': {'book': {'sha256': hashlib.sha256(book_path.read_bytes()).hexdigest(), 'source_hash': 'x'}}}
            (folder / 'gold.json').write_text(json.dumps(gold), 'utf-8')
            self.assertEqual(['c1', 'c2'], [chapter['id'] for chapter in harness.load_book(book_path, folder, cases)['chapters']])
            changed = [{**cases[0], 'source_sha256': '0' * 64}, *cases[1:]]
            with self.assertRaisesRegex(AppError, 'differs from gold case c1-p2'):
                harness.load_book(book_path, folder, changed)
            book_path.write_text(json.dumps(self.book(cases), ensure_ascii=False) + ' ', 'utf-8')
            with self.assertRaisesRegex(AppError, 'must be the training-book.json'):
                harness.load_book(book_path, folder, cases)


LEGACY_SPEC = importlib.util.spec_from_file_location('legacy_master_selection', Path(__file__).resolve().parents[1] / 'scripts' / 'legacy_master_selection.py')
legacy_tool = importlib.util.module_from_spec(LEGACY_SPEC)
LEGACY_SPEC.loader.exec_module(legacy_tool)


class LegacyBaselineTool(unittest.TestCase):
    def test_only_the_clean_legacy_revision_is_accepted(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run(['git', 'init', '-q', temporary], check=True)
            (Path(temporary) / 'file.txt').write_text('x', 'utf-8')
            subprocess.run(['git', '-C', temporary, 'add', '.'], check=True)
            subprocess.run(['git', '-C', temporary, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'x'], check=True)
            with self.assertRaisesRegex(SystemExit, 'must be a checkout of de954783'):
                legacy_tool.legacy_checkout(temporary)

    def test_report_names_only_an_index_that_matches_the_imported_source(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(['git', 'init', '-q', temporary], check=True)
            (root / 'runtime').mkdir()
            (root / 'runtime' / 'module.py').write_text('x = 1\n', 'utf-8')
            subprocess.run(['git', '-C', temporary, 'add', '.'], check=True)
            subprocess.run(['git', '-C', temporary, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'x'], check=True)
            self.assertTrue(legacy_tool.candidate_source(root)['runtime_and_scripts_match_index'])
            (root / 'runtime' / 'module.py').write_text('x = 2\n', 'utf-8')
            self.assertEqual([' M runtime/module.py'], legacy_tool.status(root, 'runtime', 'scripts'))
            with self.assertRaisesRegex(SystemExit, ' M runtime/module.py'):
                legacy_tool.candidate_source(root)
            subprocess.run(['git', '-C', temporary, 'add', '.'], check=True)
            self.assertEqual(['M  runtime/module.py'], legacy_tool.status(root, 'runtime', 'scripts'))
            self.assertTrue(legacy_tool.candidate_source(root)['runtime_and_scripts_match_index'])
            (root / 'runtime' / 'module.py').write_text('x = 3\n', 'utf-8')
            with self.assertRaisesRegex(SystemExit, 'MM runtime/module.py'):
                legacy_tool.candidate_source(root)
            subprocess.run(['git', '-C', temporary, 'add', '.'], check=True)
            (root / 'runtime' / 'extra.py').write_text('', 'utf-8')
            with self.assertRaisesRegex(SystemExit, 'extra.py'):
                legacy_tool.candidate_source(root)

    def test_internal_analysis_parts_are_scored_against_internal_gold_boundaries(self):
        target = SELECTION[1]
        cut = len('— Кто там? ')
        unit = {'start': 0, 'end': len(target['text']), 'speaker': 'char_ivan', 'warning': '', 'evidence': [],
                'analysis_parts': [{'start': 0, 'end': cut, 'speaker': 'char_ivan', 'evidence': []},
                                   {'start': cut, 'end': len(target['text']), 'speaker': 'narrator', 'evidence': []}]}
        predicted = {'cast': [{'id': 'char_ivan', 'name': 'Иван', 'aliases': []}], 'cases': {target['id']: {'targets': ['t'], 'degraded': [], 'parts': [unit]}}}
        analysis = legacy_tool.score([target], CAST, predicted, 'analysis')
        outer = legacy_tool.score([target], CAST, predicted, 'outer')
        self.assertEqual((1, 1, 1), (analysis['boundaries']['matched'], analysis['boundaries']['predicted'], analysis['boundaries']['gold']))
        self.assertEqual((0, 0), (outer['boundaries']['matched'], outer['boundaries']['predicted']))
        self.assertEqual((1, 0), (analysis['acceptance']['accepted'], analysis['acceptance']['accepted_wrong']))


class AppPath(unittest.TestCase):
    def test_invalid_target_is_reasked_alone_with_its_error_then_accepted(self):
        target = 'c1-p4-0-26'
        engine = Faulty(SELECTION, {target: 1})
        report = run(SELECTION, engine)
        asks = [payload for method, payload in engine.calls if method == 'analyze' and any(item['id'] == target for item in payload['segments'])]
        self.assertEqual(2, len(asks))
        self.assertEqual(([target], ''), ([item['id'] for item in asks[1]['segments']], asks[0]['retry_error']))
        self.assertTrue(asks[1]['retry_error'].startswith(f'Passage {target}:'))
        self.assertEqual((1.0, 0), (report['speakers']['accuracy'], report['degraded']['targets']))

    def test_target_still_invalid_after_the_reask_is_degraded_and_scored_as_such(self):
        target = 'c1-p2-0-45'
        report = run(SELECTION, Faulty(SELECTION, {target: 2}))
        self.assertEqual(1, report['degraded']['targets'])
        self.assertEqual(target, report['degraded']['passages'][0]['source'])
        self.assertTrue(report['degraded']['passages'][0]['reason'].startswith(f'Passage {target}:'))
        self.assertEqual({'spans': 4, 'correct': 2, 'accuracy': 0.5, 'character': 0, 'narrator': 0, 'unknown': 0, 'degraded': 2}, report['speakers'])
        self.assertEqual((5, 3, 3), (report['boundaries']['gold'], report['boundaries']['predicted'], report['boundaries']['matched']))
        error = next(item for item in report['errors'] if item['id'] == 'c1-p2')
        self.assertEqual(([18, 34], [], 2), (error['missed_boundaries'], error['extra_boundaries'], len(error['speakers'])))

    def test_capacity_failure_bisects_a_multi_target_batch(self):
        engine = Failing(SELECTION, AnalysisCapacityError('Too large.'), lambda payload: len(payload['segments']) > 1, times=5)
        report = run(SELECTION, engine)
        sizes = [len(payload['segments']) for method, payload in engine.calls if method == 'analyze' and payload['segments'][0]['source_id'] == 'c2-p2']
        self.assertEqual([2, 1, 1], sizes)
        self.assertEqual((0, 1.0), (report['degraded']['targets'], report['boundaries']['f1']))

    def test_single_target_timeout_is_retried_once_and_a_second_timeout_in_a_row_stops_the_run(self):
        timeout = 'Local inference exceeded its 1800-second operation limit.'
        engine = Failing(SELECTION, EngineTransportError(timeout, 'timeout'), lambda payload: payload['segments'][0]['source_id'] == 'c2-p1')
        report = run(SELECTION, engine)
        self.assertEqual((None, [], 1.0), (report['stopped'], report['degraded']['passages'], report['speakers']['accuracy']))
        self.assertFalse(any('operation limit' in payload['retry_error'] for method, payload in engine.calls if method == 'analyze'))
        engine = Failing(SELECTION, EngineTransportError(timeout, 'timeout'), lambda payload: payload['segments'][0]['source_id'] == 'c2-p1', times=2)
        report = run(SELECTION, engine)
        self.assertEqual(({'case': 'c2-p1', 'error': timeout}, 2), (report['stopped'], report['cases']))
        self.assertFalse(any(payload['segments'][0]['source_id'] in ('c2-p2', 'c2-p3') for method, payload in engine.calls if method == 'analyze'))

    def test_exited_worker_is_retried_once_like_the_app(self):
        engine = Failing(SELECTION, EngineTransportError('Local inference process exited.', 'exit'), lambda payload: True)
        report = run(SELECTION, engine)
        self.assertEqual((None, 1.0, 0), (report['stopped'], report['speakers']['accuracy'], report['degraded']['targets']))

    def test_worker_error_stops_the_run_and_scores_only_completed_cases(self):
        engine = Failing(SELECTION, AppError('Model file is unreadable.'), lambda payload: payload['segments'][0]['source_id'] == 'c2-p1')
        report = run(SELECTION, engine)
        self.assertEqual({'case': 'c2-p1', 'error': 'Model file is unreadable.'}, report['stopped'])
        self.assertEqual(2, report['cases'])
        self.assertFalse(any(payload['segments'][0]['source_id'] in ('c2-p2', 'c2-p3') for method, payload in engine.calls if method == 'analyze'))

    def test_context_and_registry_follow_the_app_request_shape(self):
        engine = Oracle(SELECTION)
        run(SELECTION, engine, reconcile=False)
        first = next(payload for method, payload in engine.calls if payload['segments'][0]['source_id'] == 'c1-p2')
        later = next(payload for method, payload in engine.calls if payload['segments'][0]['source_id'] == 'c1-p4')
        self.assertEqual((['Вечер был тихим.'], ['Иван кивнул.'], []), ([item['text'] for item in first['before']], [item['text'] for item in first['after']], first['cast']))
        self.assertEqual(['Маша'], [item['name'] for item in later['cast']])
        self.assertEqual({'id', 'name', 'aliases', 'description'}, set(later['cast'][0]))
        self.assertEqual({'pack', 'device', 'analysis_mode', 'tie_breaker', 'analysis_effort', 'segments', 'cast', 'retry_error', 'matched_aliases', 'before', 'after'}, set(later))
        self.assertEqual(('cpu', 'local', 'none'), (later['device'], later['analysis_mode'], later['tie_breaker']))


class Splits(unittest.TestCase):
    def test_selection_never_sends_or_scores_test_cases(self):
        with tempfile.TemporaryDirectory() as temporary:
            cases, cast, protocol = harness.load_gold(write_gold(Path(temporary) / 'gold'))
        self.assertEqual([item['id'] for item in SELECTION], [item['id'] for item in cases])
        engine = Oracle(cases)
        run(cases, engine)
        sent = engine.texts()
        self.assertFalse(sent & {text for item in TEST for text in [item['text'], *(entry['text'] for entry in item['before'] + item['after'])]})

    def test_test_split_needs_explicit_final_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = write_gold(Path(temporary) / 'gold')
            with self.assertRaises(AppError) as raised:
                harness.load_gold(folder, 'test')
            self.assertIn(harness.TEST_NOTE, str(raised.exception))
            cases, _, _ = harness.load_gold(folder, 'test', final_acceptance=True)
        self.assertEqual(['c3-p1', 'c3-p2'], [item['id'] for item in cases])
        engine = Oracle(cases)
        run(cases, engine)
        self.assertEqual({'Ночь опустилась на лес.', '— Это тайна, — шепнул Иван.', 'Никто не ответил.'}, engine.texts())

    def test_gold_integrity_is_checked_before_any_case_is_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stale = write_gold(root / 'stale', protocol={'gold_content_sha256': 'd' * 64, 'selection_case_ids': [], 'test_case_ids': []})
            edited = write_gold(root / 'edited', manifest={'sha256': '0' * 64})
            shared = write_gold(root / 'shared', protocol={'gold_content_sha256': 'c' * 64, 'selection_case_ids': ['c1-p2'], 'test_case_ids': ['c1-p2']})
            missing = write_gold(root / 'missing', protocol={'gold_content_sha256': 'c' * 64, 'selection_case_ids': ['absent'], 'test_case_ids': []})
            for folder, message in ((stale, 'another gold version'), (edited, 'manifest.json'), (shared, 'both'), (missing, 'lacks')):
                with self.subTest(folder=folder.name), self.assertRaises(AppError) as raised:
                    harness.load_gold(folder)
                self.assertIn(message, str(raised.exception))


class CommandLine(unittest.TestCase):
    def invoke(self, *argv, engine=None):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            try:
                code = harness.main(list(argv), lambda binaries: engine or Oracle(SELECTION + TEST))
            except SystemExit as exit:
                code = exit.code
        return code, output.getvalue(), errors.getvalue()

    def test_real_runs_need_a_pack_and_the_test_split_needs_final_acceptance(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = write_gold(Path(temporary) / 'gold')
            code, _, errors = self.invoke('--gold', str(folder))
            self.assertEqual(2, code)
            self.assertIn('--pack is required', errors)
            code, _, errors = self.invoke('--gold', str(folder), '--split', 'test', '--pack', str(Path(temporary) / 'pack'))
            self.assertEqual(2, code)
            self.assertIn(harness.TEST_NOTE, errors)
            self.assertIn('--final-acceptance', errors)

    def test_report_file_and_summary_state_scores_and_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = write_gold(root / 'gold')
            pack_fixture(root / 'pack')
            code, output, _ = self.invoke('--gold', str(folder), '--pack', str(root / 'pack'), '--limit', '2', '--device', 'cpu', '--out', str(root / 'report.json'))
            report = json.loads((root / 'report.json').read_text('utf-8'))
        self.assertEqual(0, code)
        self.assertEqual(('selection', 2, 5, 'cpu', 1.0), (report['split'], report['cases'], report['gold']['split_cases'], report['device'], report['boundaries']['f1']))
        self.assertEqual([*harness.LIMITS, 'Synthetic selection note.'], report['limits'])
        self.assertIn('Speaker attribution: 1.000 (3 of 3 gold speech spans', output)
        self.assertIn('Not validated with a real model', output)

    def test_final_acceptance_run_scores_only_test_cases_and_repeats_the_tuning_warning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = write_gold(root / 'gold')
            pack_fixture(root / 'pack')
            engine = Oracle(TEST)
            code, output, errors = self.invoke('--gold', str(folder), '--pack', str(root / 'pack'), '--split', 'test', '--final-acceptance', engine=engine)
        self.assertEqual(0, code)
        self.assertIn(harness.TEST_NOTE, errors)
        self.assertIn(f'- {harness.TEST_NOTE}', output)
        self.assertIn('Speaker evaluation: test split, 2 cases', output)
        self.assertFalse(engine.texts() & {item['text'] for item in SELECTION})

    def test_unreadable_pack_fails_without_contacting_an_engine(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = write_gold(Path(temporary) / 'gold')
            engine = Oracle(SELECTION)
            code, _, errors = self.invoke('--gold', str(folder), '--pack', str(Path(temporary) / 'absent'), engine=engine)
        self.assertEqual(1, code)
        self.assertIn('Evaluation failed:', errors)
        self.assertEqual([], engine.calls)


if __name__ == '__main__':
    unittest.main()
