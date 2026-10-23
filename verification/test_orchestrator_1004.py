from __future__ import annotations

import json
import unittest

from test_core import SyntheticEngine
from test_render_1004 import Base
from rimsky.jobs import merge_aliases
from rimsky.preparation import speech_language


class SpeechLanguagePin(Base):
    def languages(self, engine):
        return [payload['language'] for method, payload in engine.calls if method == 'render']

    def reopen_last(self, generation):
        last = max(self.store.generation(generation['id'])['segments'], key=lambda segment: segment['ordinal'])
        for suffix in ('.wav', '.json'):
            (self.store.root / 'audio' / f"{last['audio_hash']}{suffix}").unlink(missing_ok=True)
        self.store.execute("UPDATE segments SET status='pending' WHERE generation_id=? AND id=?", (generation['id'], last['id']))
        self.store.status(generation['id'], 'paused')

    def recording(self):
        class Recording(SyntheticEngine):
            def __init__(self):
                super().__init__()
                self.calls = []
            def call(self, method, payload, stop):
                self.calls.append((method, payload))
                return super().call(method, payload, stop)
        return Recording()

    def test_language_is_pinned_at_first_render_and_survives_metadata_changes(self):
        generation = self.generation()
        engine = self.recording()
        _, done = self.run_job(generation, lambda _: engine)
        self.assertEqual('ready', done['status'], done['error'])
        pinned = done['analysis']['speech_language']
        self.assertEqual({pinned}, set(self.languages(engine)))
        manifest = self.store.rows('SELECT manifest FROM books WHERE id=?', (self.book['id'],))[0]['manifest']
        changed = {**json.loads(manifest), 'language': 'en' if pinned != 'English' else 'ru'}
        self.store.execute('UPDATE books SET manifest=? WHERE id=?', (json.dumps(changed, ensure_ascii=False), self.book['id']))
        self.reopen_last(generation)
        again = self.recording()
        _, resumed = self.run_job(generation, lambda _: again)
        self.assertEqual('ready', resumed['status'], resumed['error'])
        self.assertEqual([pinned], self.languages(again))

    def test_pre_upgrade_generation_with_ready_audio_keeps_the_legacy_language_rule(self):
        generation = self.generation()
        _, done = self.run_job(generation, lambda _: SyntheticEngine())
        analysis = {key: value for key, value in done['analysis'].items() if key != 'speech_language'}
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps(analysis), generation['id']))
        manifest = self.store.rows('SELECT manifest FROM books WHERE id=?', (self.book['id'],))[0]['manifest']
        self.store.execute('UPDATE books SET manifest=? WHERE id=?', (json.dumps({**json.loads(manifest), 'language': 'und'}, ensure_ascii=False), self.book['id']))
        self.assertEqual('Russian', speech_language(self.store.book(self.book['id'])))
        self.reopen_last(generation)
        engine = self.recording()
        _, resumed = self.run_job(generation, lambda _: engine)
        self.assertEqual('ready', resumed['status'], resumed['error'])
        self.assertEqual(['Auto'], self.languages(engine))
        self.assertEqual('Auto', resumed['analysis']['speech_language'])


class AliasMergeNames(unittest.TestCase):
    def test_alias_merge_prefers_a_revealed_name_over_a_capitalised_descriptor(self):
        cast = [{'id': 'a', 'name': 'Незнакомец', 'aliases': [], 'description': '', 'evidence': ['p1']},
                {'id': 'b', 'name': 'Кирилл Комаров', 'aliases': [], 'description': '', 'evidence': ['p2']}]
        merged = merge_aliases(cast, {'b': 'a'}, frozenset({'незнакомец'}))
        self.assertEqual(['Кирилл Комаров'], [character['name'] for character in merged])
        self.assertIn('Незнакомец', merged[0]['aliases'])
        self.assertEqual(['Незнакомец'], [character['name'] for character in merge_aliases(cast, {'b': 'a'})])


if __name__ == '__main__':
    unittest.main()
