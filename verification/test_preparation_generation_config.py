"""Normal API selects the new recipe only for explicit new Russian requests."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
import server
from rimsky.russian_preparation import POLICY
from rimsky.util import AppError


class PreparationGenerationConfigTests(unittest.TestCase):
    def setUp(self):
        self.app=server.Application.__new__(server.Application)
        self.app.require_open=Mock();self.app.binaries=Path('fixture-unused')
        self.app.jobs=SimpleNamespace(active=None,start=Mock(return_value={'id':'current'}))
        self.settings={'pack_path':'fixture','character_analysis':'local','device':'cpu','align_words':False,
            'speaker_tie_breaker':'none','unresolved_policy':'hold_flagged_segments'}
        self.app.store=SimpleNamespace(settings=Mock(return_value=self.settings),book=Mock(return_value={}),
            create_generation=Mock(return_value={'id':'current'}))
        self.app.verify_pack=Mock(return_value={'_root':'fixture','_fingerprint':'p'*64,'name':'fixture'})
        self.preflight={'ready':True,'missing':[],'disclosure':'fixture explicit disclosure'}
        for target,value in (('server.health',{'ready':True}),('server.resolve_character_analysis','local'),
            ('rimsky.preparation.speech_language','Russian'),('rimsky.speech_backend.is_higgs',True),
            ('rimsky.russian_preparation_providers.preflight',self.preflight)):
            patcher=patch(target,return_value=value);self.addCleanup(patcher.stop);patcher.start()

    def generated_config(self,data=None):
        self.app.dispatch('generate',{'book_id':'book',**(data or{})})
        return self.app.store.create_generation.call_args.args[1]

    def test_explicit_new_generation_pins_recipe_and_defaults_postprocessing_off(self):
        config=self.generated_config()
        self.assertEqual(config['russian_preparation']['policy'],POLICY)
        self.assertFalse(config['russian_preparation']['online_allowed'])
        self.assertEqual(config['post_processing'],'off')
        self.assertEqual(config['preparation_disclosure'],'fixture explicit disclosure')

    def test_optin_mode_persists_and_invalid_value_rejects(self):
        self.assertEqual(self.generated_config({'post_processing':'on'})['post_processing'],'on')
        self.app.store.create_generation.reset_mock()
        with self.assertRaises(AppError):self.generated_config({'post_processing':'invalid'})
        self.app.store.create_generation.assert_not_called()

    def test_resume_never_changes_old_frozen_config_or_rechecks_providers(self):
        with patch('rimsky.russian_preparation_providers.preflight',side_effect=AssertionError('resume must not prepare')):
            self.assertEqual(self.app.dispatch('resume',{'id':'old'}),{'id':'current'})
        self.app.store.create_generation.assert_not_called();self.app.jobs.start.assert_called_once_with('old')

    def test_english_not_blocked_by_russian_provider_requirements(self):
        with patch('rimsky.preparation.speech_language',return_value='English'),\
             patch('rimsky.russian_preparation_providers.preflight',side_effect=AssertionError('English has no Russian provider requirement')):
            self.assertNotIn('russian_preparation',self.generated_config())


if __name__=='__main__':unittest.main()
