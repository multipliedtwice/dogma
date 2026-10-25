from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from rimsky import packs
from rimsky.analysis import UNRESOLVED_HOLD
from rimsky.casting import cast_voices, casting_pool, voice_details
from rimsky.export import build_export
from rimsky.packs import CONTEXT_ERROR, health, load_pack, require_context
from rimsky.store import HOLD_NOTICE, NEIGHBOUR_NOTICE, POLLED_ANALYSIS, PRESENTATION_NOTICE, Store, hold_codes, speaker_review_holds
from rimsky.util import AppError, digest, file_digest, write_json

RUNTIME = Path(__file__).resolve().parents[1] / 'runtime'
FB2 = '''<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info><genre>prose</genre><author><first-name>Synthetic</first-name><last-name>Fixture</last-name></author><book-title>Merge book</book-title><lang>ru</lang></title-info></description><body><section><title><p>Глава первая</p></title><p>Маша открыла книгу. — Читай, — сказал Иван.</p><p>Она посмотрела на формулу: F = ma.</p><p>Иван молчал. Маша ждала. Потом она ушла.</p></section><section><title><p>Глава вторая</p></title><p>— Продолжим? — спросила Маша.</p><p>Иван кивнул.</p></section></body></FictionBook>'''.encode()
VOICES = [{'id': 'n', 'name': 'Narrator', 'role': 'narrator', 'speaker': 'Ryan'},
          {'id': 'a', 'name': 'A', 'speaker': 'Aiden', 'gender': 'male'},
          {'id': 'b', 'name': 'B', 'speaker': 'Serena', 'gender': 'female'}]
PRESENTATION = {'schema': 1, 'narrator_id': 'n', 'channels': {'game_ui': {'mode': 'fixed', 'voice': 'mechanical_stats', 'status': 'provisional',
                                                                          'evidence': {'reviewed_clips': 31, 'supporting_clips': 30}},
                                                              'radio': {'mode': 'speaker'}}}
CAST = [{'id': 'narrator', 'name': 'Narrator', 'voice_id': 'n'}, {'id': 'ivan', 'name': 'Иван', 'voice_id': 'a'}, {'id': 'masha', 'name': 'Маша', 'voice_id': 'b'}]


def make_pack(root: Path, voices=VOICES, **extra):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'model.gguf').write_bytes(b'not-a-real-model; verification only')
    (root / 'tts').mkdir(exist_ok=True)
    (root / 'tts' / 'config.json').write_text('{}')
    manifest = {'schema': 1, 'name': 'Merge pack', 'text_model': {'path': 'model.gguf', 'context': 8192},
                'speech': {'path': 'tts', 'variant': 'custom', 'instruction_control': True}, 'voices': voices,
                'files': {'model.gguf': file_digest(root / 'model.gguf'), 'tts/config.json': file_digest(root / 'tts' / 'config.json')}}
    manifest = {**manifest, **extra, 'text_model': {**manifest['text_model'], **extra.get('text_model', {})}}
    write_json(root / 'pack.json', manifest)
    return manifest


def binding(part, start, end, voice='a', base_voice='a'):
    return {'part': part, 'start': start, 'end': end, 'voice': voice, 'base_voice': base_voice, 'channel': 'game_ui', 'register': 'game_system',
            'confidence': 'high', 'evidence': ['quote'], 'pack': 'f' * 64}


def resolution(segment, trigger='unattributed_exchange', start=None, end=None):
    return {'target_id': segment['id'], 'source_id': segment['source_id'], 'start': segment['start'] if start is None else start,
            'end': segment['end'] if end is None else end, 'speaker': segment['speaker'], 'primary_speaker': segment['speaker'],
            'tier': 'model', 'triggers': [trigger], 'status': 'review'}


class Packs(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_master_presentation_manifest_keeps_the_sealed_fingerprint(self):
        voices = [*VOICES, {'id': 'mechanical_stats', 'name': 'Stats', 'role': 'mechanical', 'speaker': 'Eric'}]
        manifest = make_pack(self.root / 'p', voices, presentation_voices=PRESENTATION,
                             text_model={'analysis_mode': 'staged', 'dialogue_style': 'russian_dash'})
        pack = load_pack(str(self.root / 'p'), verify=True)
        self.assertEqual(digest(manifest), pack['_fingerprint'])
        self.assertEqual(PRESENTATION, pack['presentation_voices'])
        self.assertEqual([], pack['_warnings'])

    def test_master_text_and_presentation_checks_share_the_load_path(self):
        cases = [({'text_model': {'analysis_mode': 'guess'}}, 'analysis mode'), ({'text_model': {'dialogue_style': 'quotes'}}, 'dialogue style'),
                 ({'presentation_voices': {**PRESENTATION, 'channels': {'game_ui': {'mode': 'fixed', 'voice': 'missing'}}}}, 'missing voice')]
        for extra, message in cases:
            with self.subTest(message=message):
                make_pack(self.root / message.replace(' ', '-'), **extra)
                with self.assertRaisesRegex(AppError, message):
                    load_pack(str(self.root / message.replace(' ', '-')))

    def test_legacy_game_voice_declared_as_character_is_never_cast_to_a_person(self):
        voices = [*VOICES, {'id': 'mechanical_game_voice', 'name': 'Game UI', 'role': 'character', 'speaker': 'Eric'},
                  {'id': 'hud', 'name': 'HUD', 'speaker': 'Vivian', 'tags': ['game_ui']}]
        make_pack(self.root / 'legacy', voices)
        pack = load_pack(str(self.root / 'legacy'))
        self.assertEqual(['a', 'b'], [voice['id'] for voice in casting_pool({'kind': 'person'}, pack['voices'])])
        self.assertEqual(['mechanical_game_voice', 'hud'], [voice['id'] for voice in casting_pool({'kind': 'mechanical'}, pack['voices'])])
        self.assertEqual('mechanical', voice_details(voices[3])['role'])
        self.assertEqual(2, len(pack['_warnings']))
        cast = cast_voices([{'id': 'ivan', 'name': 'Иван', 'kind': 'person'}], pack['voices'],
                           [{'speaker': 'ivan', 'text': 'Да.', 'chapter_id': 'c', 'source_id': 's', 'start': 0}])
        self.assertIn(cast[1]['voice_id'], ('a', 'b'))

    def test_ambiguous_mechanical_character_voice_is_held_with_a_warning(self):
        voices = [VOICES[0], {'id': 'feminine_4_mechanical', 'name': 'F4', 'speaker': 'Serena'}, {'id': 'plain', 'name': 'P', 'speaker': 'Aiden'}]
        make_pack(self.root / 'ambiguous', voices)
        pack = load_pack(str(self.root / 'ambiguous'))
        self.assertEqual(['plain'], [voice['id'] for voice in casting_pool({'kind': 'person'}, pack['voices'])])
        self.assertIn('feminine_4_mechanical', pack['_warnings'][0])
        only = [VOICES[0], voices[1]]
        make_pack(self.root / 'only', only)
        held = load_pack(str(self.root / 'only'))
        with self.assertRaisesRegex(AppError, 'Review the declared role of: feminine_4_mechanical'):
            cast_voices([{'id': 'ivan', 'name': 'Иван'}], held['voices'], [])

    def test_new_mechanical_role_still_requires_an_ordinary_character_and_one_narrator(self):
        make_pack(self.root / 'mech', [VOICES[0], {'id': 'mechanical_stats', 'name': 'S', 'role': 'mechanical', 'speaker': 'Eric'}])
        with self.assertRaisesRegex(AppError, 'at least one character performance'):
            load_pack(str(self.root / 'mech'))
        make_pack(self.root / 'two', [*VOICES, {'id': 'n2', 'name': 'N2', 'role': 'narrator', 'speaker': 'Eric'}])
        with self.assertRaisesRegex(AppError, 'exactly one narrator'):
            load_pack(str(self.root / 'two'))

    def test_context_floor_applies_to_local_analysis_only(self):
        make_pack(self.root / 'small', text_model={'context': 4096})
        path = str(self.root / 'small')
        with self.assertRaisesRegex(AppError, f'^{CONTEXT_ERROR}$'):
            load_pack(path)
        small = load_pack(path, provider='codex_cli')
        self.assertIs(small, require_context(small, 'claude_cli'))
        self.assertEqual(small['_fingerprint'], load_pack(path, pinned=True)['_fingerprint'])
        for provider in ((), ('local',)):
            with self.assertRaisesRegex(AppError, f'^{CONTEXT_ERROR}$'):
                require_context(small, *provider)
        with self.assertRaisesRegex(AppError, 'provider'):
            require_context(small, 'auto')
        make_pack(self.root / 'tiny', text_model={'context': 4095})
        with self.assertRaisesRegex(AppError, f'^{CONTEXT_ERROR}$'):
            load_pack(str(self.root / 'tiny'), provider='codex_cli')

    def test_health_uses_the_resolved_provider(self):
        make_pack(self.root / 'small', text_model={'context': 4096})
        settings = {'pack_path': str(self.root / 'small')}
        cli = health({**settings, 'character_analysis': 'codex_cli'}, self.root / 'bin')
        local = health({**settings, 'character_analysis': 'local'}, self.root / 'bin')
        self.assertEqual(('', False), (cli['pack_error'], cli['offline']))
        self.assertEqual((CONTEXT_ERROR, True), (local['pack_error'], local['offline']))
        with patch.object(packs, 'trusted_executable', side_effect=AppError('missing')):
            self.assertEqual('local', packs.resolve_character_analysis('auto'))
            self.assertIn('codex CLI', health({**settings, 'character_analysis': 'codex_cli'}, self.root / 'bin')['missing'])
        with patch.object(packs, 'trusted_executable', side_effect=lambda names, **options:
                          (_ for _ in ()).throw(AppError('missing')) if 'codex' in names else '/bin/claude'):
            self.assertEqual('claude_cli', packs.resolve_character_analysis('auto'))


class Revisions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'library')
        self.addCleanup(self.store.close)
        self.book = self.store.import_data(FB2, 'fixture.fb2')

    def generation(self, speakers=None, items=(), performance=None, policy='hold_flagged_segments', approved=None, split=False):
        # Calls in this factory are independent review scenarios, not requests
        # to regenerate the same current audiobook.
        if self.store.generation_list(self.book['id']):
            self.book=self.store.import_data(FB2.replace(b'Merge book',('Merge fixture '+str(len(self.store.library()))).encode()),'independent.fb2')
        generation = self.store.create_generation(self.book['id'], {'pack_path': '', 'pack_fingerprint': 'x', 'pack_name': 'p', 'device': 'cpu',
                                                                    'align_words': False, 'unresolved_policy': policy})
        gid = generation['id']
        if split:
            last = generation['segments'][3]
            self.store.execute('UPDATE segments SET end=12,text=? WHERE generation_id=? AND id=?', (last['text'][:12], gid, last['id']))
            self.store.execute('INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken) VALUES(?,?,?,?,?,?,?,?,?)',
                               (gid, last['source_id'] + '-13-40', last['ordinal'] + 1, last['chapter_id'], last['source_id'], 13, 40, last['text'][13:], last['text'][13:]))
        for sid, speaker in (speakers or {}).items():
            self.store.execute('UPDATE segments SET speaker=? WHERE generation_id=? AND id=?', (speaker, gid, sid))
        for sid, value in (performance or {}).items():
            self.store.execute('UPDATE segments SET performance=? WHERE generation_id=? AND id=?', (json.dumps(value, ensure_ascii=False), gid, sid))
        segments = self.store.generation(gid)['segments']
        resolutions = [factory(segments) for factory in items]
        flagged, held = speaker_review_holds(segments, resolutions)
        for segment in segments:
            if segment['id'] in held:
                self.store.execute("UPDATE segments SET status='review',warning=? WHERE generation_id=? AND id=?",
                                   (HOLD_NOTICE if segment['id'] in flagged else NEIGHBOUR_NOTICE, gid, segment['id']))
            else:
                self.store.execute("UPDATE segments SET status='ready',audio_hash=?,duration=.1 WHERE generation_id=? AND id=?", (digest(segment['id']), gid, segment['id']))
        analysis = {'complete': True, 'speaker_resolution': resolutions, 'casting_inputs': [
            {'chapter_id': s['chapter_id'], 'source_id': s['source_id'], 'ordinal': s['ordinal'], 'start': s['start'], 'speaker': s['speaker']} for s in segments]}
        if approved is not None:
            analysis['approved'] = approved
        self.store.execute("UPDATE generations SET status=?,cast=?,analysis=? WHERE id=?",
                           ('held' if held else 'ready', json.dumps(CAST), json.dumps(analysis, ensure_ascii=False), gid))
        return self.store.generation(gid)

    def by_id(self, generation):
        return {segment['id']: segment for segment in generation['segments']}

    def ids(self, generation):
        return [segment['id'] for segment in generation['segments']]

    def test_confirmation_is_rejected_for_unknown_or_unheld_passages(self):
        original = self.generation({'c0001-p00002-0-43': 'unknown'}, [lambda s: resolution(s[1])])
        ids = self.ids(original)
        with self.assertRaises(AppError) as unknown:
            self.store.revise(original['id'], [{'id': ids[1], 'confirm_speaker': True}])
        with self.assertRaises(AppError) as unheld:
            self.store.revise(original['id'], [{'id': ids[5], 'confirm_speaker': True}])
        self.assertEqual(('unknown_speaker', 'not_held'), (unknown.exception.code, unheld.exception.code))
        for invalid in ({'id': ids[2], 'confirm_speaker': False}, {'id': ids[2], 'confirm_speaker': True, 'speaker': 'ivan'}):
            with self.assertRaisesRegex(AppError, 'Invalid passage correction'):
                self.store.revise(original['id'], [invalid])
        self.assertEqual([original['id']], [item['id'] for item in self.store.generation_list(self.book['id'])])

    def test_confirmation_releases_only_the_verdict_and_its_unflagged_neighbours(self):
        speakers = {'c0001-p00002-0-43': 'ivan', 'c0002-p00002-0-29': 'masha'}
        original = self.generation(speakers, [lambda s: resolution(s[1]), lambda s: resolution(s[5])])
        ids = self.ids(original)
        self.assertEqual({'review'}, {self.by_id(original)[sid]['status'] for sid in (ids[0], ids[1], ids[2], ids[4], ids[5], ids[6])})
        revised = self.store.revise(original['id'], [{'id': ids[1], 'confirm_speaker': True}])
        segments = self.by_id(revised)
        self.assertEqual(('pending', '', None), (segments[ids[1]]['status'], segments[ids[1]]['warning'], segments[ids[1]]['audio_hash']))
        self.assertEqual(['pending', 'pending'], [segments[sid]['status'] for sid in (ids[0], ids[2])])
        self.assertEqual([('review', NEIGHBOUR_NOTICE), ('review', HOLD_NOTICE), ('review', NEIGHBOUR_NOTICE)],
                         [(segments[sid]['status'], segments[sid]['warning']) for sid in (ids[4], ids[5], ids[6])])
        self.assertEqual(('ready', segments[ids[3]]['audio_hash']), (segments[ids[3]]['status'], self.by_id(original)[ids[3]]['audio_hash']))
        analysis = revised['analysis']
        self.assertEqual([('resolved', 'human_confirmation', 'ivan'), ('review', 'model', 'masha')],
                         [(item['status'], item['tier'], item['speaker']) for item in analysis['speaker_resolution']])
        self.assertEqual({'speaker': 'ivan', 'tier': 'human_confirmation'}, {key: analysis['speaker_verdicts'][ids[1]][key] for key in ('speaker', 'tier')})
        self.assertEqual({'flagged_segments': 1, 'held_with_neighbours': 3, 'reasons': {'speaker_review': 1, 'neighbour': 2}}, {key: value for key, value in analysis['speaker_review_holds'].items() if not key.endswith('_ids')})
        self.assertEqual(3, len(analysis['speaker_review_holds']['held_ids']))
        self.assertEqual(1, analysis['speaker_resolution_metrics']['review'])
        progress = next(item for item in self.store.generation_list(self.book['id']) if item['id'] == revised['id'])['progress']
        self.assertEqual((7, 1, 3), (progress['total'], progress['ready'], progress['held']))
        self.assertEqual('paused', revised['status'])

    def test_speaker_correction_releases_with_human_correction_and_cast_approval_gate_is_kept(self):
        original = self.generation({'c0001-p00002-0-43': 'ivan'}, [lambda s: resolution(s[1])], approved=False)
        ids = self.ids(original)
        revised = self.store.revise(original['id'], [{'id': ids[1], 'speaker': 'masha'}])
        self.assertEqual('review', revised['status'])
        self.assertEqual(('resolved', 'human_correction', 'masha'), tuple(revised['analysis']['speaker_resolution'][0][key] for key in ('status', 'tier', 'speaker')))
        self.assertEqual('masha', revised['analysis']['casting_inputs'][1]['speaker'])
        self.assertEqual({'pending'}, {self.by_id(revised)[sid]['status'] for sid in ids[:3]})

    def test_a_span_over_two_passages_needs_both_verdicts(self):
        original = self.generation({'c0001-p00004-0-40': 'ivan', 'c0001-p00004-13-40': 'ivan'},
                                   [lambda s: resolution(s[3], start=0, end=40)], split=True)
        ids = self.ids(original)
        first = self.store.revise(original['id'], [{'id': ids[3], 'confirm_speaker': True}])
        segments = self.by_id(first)
        self.assertEqual(('pending', 'review'), (segments[ids[3]]['status'], segments[ids[4]]['status']))
        self.assertEqual('review', first['analysis']['speaker_resolution'][0]['status'])
        second = self.store.revise(first['id'], [{'id': ids[4], 'confirm_speaker': True}])
        self.assertEqual('resolved', second['analysis']['speaker_resolution'][0]['status'])
        self.assertEqual(set(), {segment['id'] for segment in second['segments'] if segment['status'] == 'review'})

    def test_non_speaker_holds_and_edits_without_a_verdict_stay_held(self):
        original = self.generation({'c0001-p00002-0-43': 'ivan', 'c0002-p00002-0-29': 'masha'},
                                   [lambda s: resolution(s[1], 'backend_limit'), lambda s: resolution(s[5])])
        ids = self.ids(original)
        revised = self.store.revise(original['id'], [{'id': ids[1], 'confirm_speaker': True}, {'id': ids[5], 'spoken': 'Продолжим?', 'delivery': 'questioning'}])
        segments = self.by_id(revised)
        self.assertEqual([('review', HOLD_NOTICE)] * 2, [(segments[sid]['status'], segments[sid]['warning']) for sid in (ids[1], ids[5])])
        self.assertEqual(('Продолжим?', None), (segments[ids[5]]['spoken'], segments[ids[5]]['audio_hash']))
        self.assertEqual({'review'}, {item['status'] for item in revised['analysis']['speaker_resolution']})
        self.assertEqual({'backend_limit': 1, 'speaker_review': 1, 'neighbour': 4}, revised['analysis']['speaker_review_holds']['reasons'])

    def test_an_unknown_speaker_correction_is_held_instead_of_narrated(self):
        original = self.generation({'c0001-p00003-0-34': 'masha'})
        ids = self.ids(original)
        revised = self.store.revise(original['id'], [{'id': ids[2], 'speaker': 'unknown'}])
        segments = self.by_id(revised)
        self.assertEqual([('review', f'{UNRESOLVED_HOLD} {HOLD_NOTICE}'), ('review', NEIGHBOUR_NOTICE), ('review', NEIGHBOUR_NOTICE)],
                         [(segments[sid]['status'], segments[sid]['warning']) for sid in (ids[2], ids[1], ids[3])])
        self.assertEqual(['unresolved_speaker'], revised['analysis']['speaker_resolution'][0]['triggers'])
        relaxed = self.generation({'c0001-p00003-0-34': 'masha'}, policy='render_with_warning')
        loose = self.by_id(self.store.revise(relaxed['id'], [{'id': ids[2], 'speaker': 'unknown'}]))
        self.assertEqual(('pending', 'ready'), (loose[ids[2]]['status'], loose[ids[1]]['status']))

    def test_mixed_passage_speaker_rewrites_only_matching_parts_and_spoken_edits_are_refused(self):
        sid = 'c0001-p00002-0-43'
        parts = [{'start': 0, 'end': 20, 'text': 'Маша открыла книгу.', 'speaker': 'narrator', 'delivery': 'neutral'},
                 {'start': 21, 'end': 29, 'text': '— Читай,', 'speaker': 'ivan', 'delivery': 'firm'},
                 {'start': 30, 'end': 43, 'text': '— сказал Иван.', 'speaker': 'narrator', 'delivery': 'neutral'}]
        performance = {'schema': 2, 'internal_transition': True, 'analysis_parts': parts}
        original = self.generation({sid: 'ivan'}, performance={sid: performance})
        with self.assertRaises(AppError) as refused:
            self.store.revise(original['id'], [{'id': sid, 'spoken': 'Читай.'}])
        self.assertEqual('mixed_spoken_edit', refused.exception.code)
        with self.assertRaises(AppError) as delivery_refused:
            self.store.revise(original['id'], [{'id': sid, 'delivery': 'excited'}])
        self.assertEqual('mixed_delivery_edit', delivery_refused.exception.code)
        revised = self.store.revise(original['id'], [{'id': sid, 'speaker': 'masha'}])
        changed = self.by_id(revised)[sid]['performance']
        self.assertEqual(['narrator', 'masha', 'narrator'], [part['speaker'] for part in changed['analysis_parts']])
        self.assertEqual(performance, self.by_id(original)[sid]['performance'])
        self.assertEqual(original['id'],revised['id'])
        untouched = self.by_id(revised)['c0001-p00003-0-34']
        self.assertEqual({}, untouched['performance'])

    def test_delivery_correction_rebuilds_the_saved_performance_plan(self):
        original = self.generation()
        sid = self.ids(original)[1]
        before = self.by_id(original)[sid]
        self.store.execute('UPDATE segments SET performance=? WHERE generation_id=? AND id=?',
                           (json.dumps({'schema': 2, 'delivery': 'neutral', 'pace': 'normal', 'cues': [],
                                        'presentation_channel': 'direct', 'content_register': 'prose',
                                        'context_confidence': 'low', 'context_evidence': []}), original['id'], sid))
        revised = self.store.revise(original['id'], [{'id': sid, 'delivery': 'enumeration'}])
        changed = self.by_id(revised)[sid]
        self.assertEqual(('enumeration', 'enumeration', 'brisk'),
                         (changed['delivery'], changed['performance']['delivery'], changed['performance']['pace']))
        self.assertIn('enumeration', changed['performance']['cues'])
        self.assertEqual(before['spoken'], changed['spoken'])

    def test_preview_policy_and_presentation_holds_are_released_only_by_verdicts(self):
        speakers = {'c0001-p00002-0-43': 'ivan', 'c0002-p00002-0-29': 'masha'}
        original = self.generation(speakers, [lambda s: resolution(s[1]), lambda s: resolution(s[5], 'presentation_review')], policy='render_with_warning')
        ids = self.ids(original)
        self.store.execute("UPDATE segments SET status='ready',audio_hash='preview',warning=? WHERE generation_id=? AND id=?",
                           ('Unreviewed preview; this passage is still held for review.', original['id'], ids[1]))
        revised = self.store.revise(original['id'], [{'id': ids[1], 'confirm_speaker': True}, {'id': ids[5], 'confirm_speaker': True}])
        segments = self.by_id(revised)
        self.assertEqual(('pending', None, ''), (segments[ids[1]]['status'], segments[ids[1]]['audio_hash'], segments[ids[1]]['warning']))
        self.assertEqual(('review', PRESENTATION_NOTICE), (segments[ids[5]]['status'], segments[ids[5]]['warning']))
        self.assertEqual(['resolved', 'review'], [item['status'] for item in revised['analysis']['speaker_resolution']])
        self.assertEqual({'presentation_review': 1, 'neighbour': 2}, revised['analysis']['speaker_review_holds']['reasons'])
        independent=self.generation(speakers,[lambda s:resolution(s[1]),lambda s:resolution(s[5],'presentation_review')],policy='render_with_warning')
        partial = self.store.revise(independent['id'], [{'id': ids[1], 'confirm_speaker': True}])
        self.assertEqual(['resolved', 'review'], [item['status'] for item in partial['analysis']['speaker_resolution']])
        self.assertEqual('review', self.by_id(partial)[ids[5]]['status'])

    def test_mixed_passage_may_restore_its_source_text(self):
        sid = 'c0001-p00002-0-43'
        performance = {'schema': 2, 'internal_transition': True, 'analysis_parts': [{'start': 0, 'end': 43, 'text': 'x', 'speaker': 'ivan'}]}
        original = self.generation({sid: 'ivan'}, performance={sid: performance})
        self.store.execute('UPDATE segments SET spoken=? WHERE generation_id=? AND id=?', ('Читай.', original['id'], sid))
        revised = self.store.revise(original['id'], [{'id': sid, 'spoken': original['segments'][1]['text']}])
        self.assertEqual(original['segments'][1]['text'], self.by_id(revised)[sid]['spoken'])

    def test_polling_excludes_speech_structure_and_includes_review_keys(self):
        self.assertLessEqual({'speaker_resolution', 'speaker_resolution_metrics', 'speaker_review_holds', 'approved', 'casting_notices'}, set(POLLED_ANALYSIS))
        self.assertNotIn('speech_structure', POLLED_ANALYSIS)
        original = self.generation({'c0001-p00002-0-43': 'ivan'}, [lambda s: resolution(s[1])])
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps({**original['analysis'], 'speech_structure': {'x': [{'text': 'x' * 1000}]}}), original['id']))
        polled = self.store.generation_poll(original['id'])['generation']['analysis']
        self.assertNotIn('speech_structure', polled)
        self.assertIn('speaker_resolution', polled)

    def test_export_refuses_held_generations_without_dropping_text(self):
        original = self.generation({'c0001-p00002-0-43': 'ivan'}, [lambda s: resolution(s[1])])
        with self.assertRaises(AppError) as refused:
            build_export(self.store, original['id'], 'vtt', None, self.root)
        self.assertEqual(('held_segments', 3), (refused.exception.code, refused.exception.details['held']))
        self.assertIn('3 held passage(s)', str(refused.exception))
        try:
            build_export(self.store, original['id'], 'vtt', ['c0002'], self.root)
        except AppError as error:
            self.assertNotEqual('held_segments', error.code)
        self.assertEqual([], [row for row in self.store.rows('SELECT * FROM exports') if row['generation_id'] == original['id'] and row['format'] != 'vtt'])


class PartVerdicts(unittest.TestCase):
    setUp, generation, by_id, ids = Revisions.setUp, Revisions.generation, Revisions.by_id, Revisions.ids

    def mixed(self):
        segment = self.store.create_generation(self.book['id'], {'pack_path': '', 'pack_fingerprint': 'x', 'pack_name': 'p', 'device': 'cpu'})['segments'][1]
        text = segment['text']
        first, second = text.index('— Читай'), text.index('— сказал')
        base = segment['start']
        parts = [{'start': base, 'end': base + first, 'text': text[:first], 'speaker': 'narrator', 'delivery': 'neutral', 'context': {}, 'evidence': []},
                 {'start': base + first, 'end': base + second, 'text': text[first:second], 'speaker': 'ivan', 'delivery': 'neutral', 'context': {}, 'evidence': []},
                 {'start': base + second, 'end': base + len(text), 'text': text[second:], 'speaker': 'masha', 'delivery': 'neutral', 'context': {}, 'evidence': []}]
        performance = {'internal_transition': True, 'analysis_parts': parts}
        record = lambda part: lambda s: {**resolution(s[1], start=part['start'], end=part['end']), 'speaker': part['speaker']}
        return self.generation({segment['id']: 'ivan'}, [record(parts[1]), record(parts[2])], {segment['id']: performance}), segment['id']

    def test_segment_confirmation_releases_only_parts_of_the_shown_speaker(self):
        original, sid = self.mixed()
        revised = self.store.revise(original['id'], [{'id': sid, 'confirm_speaker': True}])
        records = revised['analysis']['speaker_resolution']
        self.assertEqual([('ivan', 'resolved'), ('masha', 'review')], [(item['speaker'], item['status']) for item in records])
        self.assertEqual('review', self.by_id(revised)[sid]['status'])

    def test_part_verdicts_release_the_mixed_sentence_and_keep_record_speakers(self):
        original, sid = self.mixed()
        revised = self.store.revise(original['id'], [{'id': sid, 'part': 1, 'confirm_speaker': True}, {'id': sid, 'part': 2, 'confirm_speaker': True}])
        self.assertEqual([('ivan', 'resolved'), ('masha', 'resolved')], [(item['speaker'], item['status']) for item in revised['analysis']['speaker_resolution']])
        self.assertEqual('pending', self.by_id(revised)[sid]['status'])

    def test_part_speaker_change_rewrites_one_part_and_verdict(self):
        original, sid = self.mixed()
        revised = self.store.revise(original['id'], [{'id': sid, 'part': 2, 'speaker': 'ivan'}, {'id': sid, 'part': 1, 'confirm_speaker': True}])
        segment = self.by_id(revised)[sid]
        self.assertEqual(['narrator', 'ivan', 'ivan'], [part['speaker'] for part in segment['performance']['analysis_parts']])
        self.assertEqual(('ivan', 'pending', None), (segment['speaker'], segment['status'], segment['audio_hash']))
        with self.assertRaisesRegex(AppError, 'not both at once'):
            self.store.revise(original['id'], [{'id': sid, 'part': 1, 'confirm_speaker': True}, {'id': sid, 'confirm_speaker': True}])
        independent,sid=self.mixed()
        unknown = self.store.revise(independent['id'], [{'id': sid, 'part': 1, 'speaker': 'unknown'}, {'id': sid, 'part': 2, 'confirm_speaker': True}])
        segment = self.by_id(unknown)[sid]
        self.assertEqual(('unknown', 'review'), (segment['speaker'], segment['status']))
        part = segment['performance']['analysis_parts'][1]
        self.assertTrue(any(item['status'] == 'review' and 'unresolved_speaker' in item['triggers'] and (item['start'], item['end']) == (part['start'], part['end'])
                            for item in unknown['analysis']['speaker_resolution']))

    def test_presentation_hold_is_released_only_by_a_presentation_verdict(self):
        sid = 'c0001-p00002-0-43'
        original = self.generation({sid: 'ivan'}, [lambda s: resolution(s[1], 'presentation_review')], {sid: {'presentation_binding': [binding(None, 0, 43)]}})
        spoken = self.store.revise(original['id'], [{'id': sid, 'confirm_speaker': True}])
        self.assertEqual(('review', PRESENTATION_NOTICE), (self.by_id(spoken)[sid]['status'], self.by_id(spoken)[sid]['warning']))
        self.assertEqual('review', spoken['analysis']['speaker_resolution'][0]['status'])
        presented = self.store.revise(spoken['id'], [{'id': sid, 'confirm_presentation': True}])
        self.assertEqual('pending', self.by_id(presented)[sid]['status'])
        self.assertEqual({'tier': 'presentation_confirmation', 'binding': binding(None, 0, 43)}, presented['analysis']['speaker_verdicts'][f'{sid}@presentation'])
        self.assertEqual({}, presented['analysis']['speaker_review_holds']['reasons'])
        self.store.execute('UPDATE segments SET performance=? WHERE generation_id=? AND id=?',
                           (json.dumps({'presentation_binding': [binding(None, 0, 43, voice='other')]}), presented['id'], sid))
        changed = self.store.generation(presented['id'])
        self.assertIn(sid, speaker_review_holds(changed['segments'], changed['analysis']['speaker_resolution'], changed['analysis']['speaker_verdicts'])[0])

    def test_speaker_correction_with_a_provisional_mapping_keeps_the_presentation_hold(self):
        sid = 'c0001-p00002-0-43'
        original = self.generation({sid: 'ivan'}, [lambda s: resolution(s[1]), lambda s: resolution(s[1], 'presentation_review')],
                                   {sid: {'presentation_binding': [binding(None, 0, 43)]}})
        both = self.store.revise(original['id'], [{'id': sid, 'confirm_presentation': True}])
        self.assertEqual(['review', 'review'], [item['status'] for item in both['analysis']['speaker_resolution']])
        self.assertEqual(['speaker_review'], hold_codes(self.by_id(both)[sid], both['analysis']['speaker_resolution'], both['analysis']['speaker_verdicts']))
        corrected = self.store.revise(both['id'], [{'id': sid, 'speaker': 'masha'}])
        self.assertNotIn(f'{sid}@presentation', corrected['analysis']['speaker_verdicts'])
        self.assertEqual(['resolved', 'review'], [item['status'] for item in corrected['analysis']['speaker_resolution']])
        self.assertEqual(('review', PRESENTATION_NOTICE), (self.by_id(corrected)[sid]['status'], self.by_id(corrected)[sid]['warning']))
        independent=self.generation({sid:'ivan'},[lambda s:resolution(s[1]),lambda s:resolution(s[1],'presentation_review')],
                                    {sid:{'presentation_binding':[binding(None,0,43)]}})
        both=self.store.revise(independent['id'],[{'id':sid,'confirm_presentation':True}])
        with self.assertRaises(AppError) as stale:
            self.store.revise(both['id'], [{'id': sid, 'speaker': 'masha'}, {'id': sid, 'confirm_presentation': True}])
        self.assertEqual('stale_presentation', stale.exception.code)

    def test_part_presentation_confirmation_and_rejections(self):
        original, sid = self.mixed()
        parts = self.by_id(original)[sid]['performance']['analysis_parts']
        entries = [binding(1, parts[1]['start'], parts[1]['end']), binding(2, parts[2]['start'], parts[2]['end'], voice='m')]
        gid = original['id']
        self.store.execute('UPDATE segments SET performance=? WHERE generation_id=? AND id=?',
                           (json.dumps({**self.by_id(original)[sid]['performance'], 'presentation_binding': entries}, ensure_ascii=False), gid, sid))
        analysis = original['analysis']
        records = [*analysis['speaker_resolution'], {**resolution(self.by_id(original)[sid], 'presentation_review'), 'start': parts[2]['start'], 'end': parts[2]['end']}]
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps({**analysis, 'speaker_resolution': records}, ensure_ascii=False), gid))
        for change, code in (({'id': sid, 'part': 0, 'confirm_presentation': True}, 'not_held'), ({'id': self.ids(original)[2], 'confirm_presentation': True}, 'not_held')):
            with self.assertRaises(AppError) as refused:
                self.store.revise(gid, [change])
            self.assertEqual(code, refused.exception.code)
        identity = self.store.revise(gid, [{'id': sid, 'part': 1, 'confirm_speaker': True}, {'id': sid, 'part': 2, 'confirm_speaker': True}])
        self.assertEqual(['resolved', 'resolved', 'review'], [item['status'] for item in identity['analysis']['speaker_resolution']])
        self.assertEqual('review', self.by_id(identity)[sid]['status'])
        presented = self.store.revise(identity['id'], [{'id': sid, 'part': 2, 'confirm_presentation': True}])
        self.assertEqual('pending', self.by_id(presented)[sid]['status'])
        self.assertEqual({f'{sid}#2@presentation'}, {key for key in presented['analysis']['speaker_verdicts'] if key.endswith('@presentation')})
        moved = self.store.revise(presented['id'], [{'id': sid, 'part': 2, 'speaker': 'ivan'}])
        self.assertNotIn(f'{sid}#2@presentation', moved['analysis']['speaker_verdicts'])
        self.assertEqual('review', self.by_id(moved)[sid]['status'])

    def test_presentation_verdict_never_clears_identity_or_backend_holds(self):
        sid, other = 'c0001-p00002-0-43', 'c0002-p00002-0-29'
        original = self.generation({sid: 'ivan', other: 'masha'}, [lambda s: resolution(s[1]), lambda s: resolution(s[5], 'backend_limit')],
                                   {sid: {'presentation_binding': [binding(None, 0, 43)]}, other: {'presentation_binding': [binding(None, 0, 29)]}})
        revised = self.store.revise(original['id'], [{'id': sid, 'confirm_presentation': True}, {'id': other, 'confirm_presentation': True}, {'id': other, 'confirm_speaker': True}])
        self.assertEqual([('review', HOLD_NOTICE)] * 2, [(self.by_id(revised)[key]['status'], self.by_id(revised)[key]['warning']) for key in (sid, other)])
        self.assertEqual(['review', 'review'], [item['status'] for item in revised['analysis']['speaker_resolution']])
        self.assertEqual({'speaker_review': 1, 'backend_limit': 1, 'neighbour': 4}, revised['analysis']['speaker_review_holds']['reasons'])


class Recovery(unittest.TestCase):
    def test_only_the_owning_application_recovers_interrupted_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'library'
            store = Store(root)
            book = store.import_data(FB2, 'fixture.fb2')
            plain = store.create_generation(book['id'], {'pack_path': ''})
            other=store.import_data(FB2.replace(b'Merge book',b'External fixture'),'external.fb2')
            external = store.create_generation(other['id'], {'pack_path': '', 'engine': 'higgs_audio_tts_q8_external'})
            # External publishers set their ownership marker explicitly;
            # normal app preparation deliberately clears external mode.
            store.execute('UPDATE generations SET config=? WHERE id=?',(json.dumps({**external['config'],'engine':'higgs_audio_tts_q8_external'}),external['id']))
            for generation in (plain, external):
                store.status(generation['id'], 'rendering')
            store.close()
            leftover = root / 'work' / 'render.wav'
            leftover.write_bytes(b'x')
            observer = Store(root)
            self.assertEqual(('rendering', True), (observer.generation(plain['id'])['status'], leftover.exists()))
            observer.close()
            owner = Store(root, recover_interrupted=True)
            try:
                self.assertEqual(('paused', 'rendering', False), (owner.generation(plain['id'])['status'], owner.generation(external['id'])['status'], leftover.exists()))
            finally:
                owner.close()


class Workers(unittest.TestCase):
    def test_alignment_worker_writes_raw_utf8_text_lines(self):
        result = subprocess.run([sys.executable, str(RUNTIME / 'align_worker.py')], input='{"id": "ид", "method": "x"}\n'.encode(),
                                capture_output=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        line = result.stdout.decode('utf-8').strip()
        self.assertIn('"ид"', line)
        self.assertEqual('ValueError: Unknown alignment operation.', json.loads(line)['error'])


if __name__ == '__main__':
    unittest.main()
