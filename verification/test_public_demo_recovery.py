"""Synthetic recovery provenance gates; CLI/GPU runners are never invoked."""
import copy
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('recovery', ROOT / 'scripts/recover_public_audiobook_demo.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryGates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.unit = {'id': 'u0001', 'source_id': 'p1', 'text': 'Her Jim.', 'spoken': 'Her Jim.',
                     'palette_reference': {'path': 'synthetic.wav', 'audio_sha256': 'f'*64, 'text': 'Reference.', 'reviewed': True}}
        assignment = {'language': 'English', 'units': [self.unit]}
        manifest = {'speech_model': {'sha256': 'a'*64}, 'runner': {'sha256': 'b'*64}}
        frozen = {'assignment': assignment, 'manifest': manifest,
                  'implementation_sha256': recovery.file_digest(Path(recovery.renderer.__file__))}
        self.generation = recovery.renderer.sha(frozen)
        for name, obj in [('assignment', assignment), ('manifest', manifest), ('frozen', frozen)]:
            recovery.immutable(self.folder / (name+'.json'), obj)
        self.native_request = {'id': 'u0001-t0', 'text': 'Her Jim.', 'voice_ref': 'synthetic.wav',
                               'reference_text': 'Reference.', 'seed': 100, 'temperature': .8, 'top_p': .95,
                               'top_k': 50, 'max_tokens': 211, 'do_sample': True, 'repetition_penalty': 1.1}
        for take in range(3):
            request = {**self.native_request, 'seed': 100+take, 'id': 'u0001-t'+str(take)}
            specification = {'request': request, 'source': self.unit, 'model': manifest['speech_model'],
                             'runner': manifest['runner'], 'reference': self.unit['palette_reference'],
                             'generation': self.generation, 'language': 'English', 'effects': 'disabled'}
            receipt = {'unit': self.unit['id'], 'take': take, 'quality_passed': False, 'quality_error': 'Synthetic active edge',
                       'spec': specification, 'spec_sha256': recovery.renderer.sha(specification)}
            recovery.immutable(self.folder / 'takes/u0001' / ('take-%02d.json' % take), receipt)
        self.campaign = recovery.plan(self.folder, self.unit)
        self.answer = {'synthesis_text': 'Her Jim.', 'profile_order': list(recovery.PROFILES),
                       'diagnosis': 'Uncertain short fragment.', 'source_quote': 'Her Jim.',
                       'independent_check': 'Audio-only ASR and safe edge checks.'}

    def proposal(self):
        campaign_file = recovery.campaign_path(self.folder, self.unit)
        directory = campaign_file.parent
        request = {'system': recovery.SYSTEM, 'schema': recovery.SCHEMA, 'request': recovery.proposal_request(self.campaign),
                   'campaign_sha256': recovery.file_digest(campaign_file), 'provider_order': recovery.PROVIDERS}
        # Immutable JSON stores tuples as lists; the production request is read
        # back from JSON before validating its shared subset.
        recovery.immutable(directory / 'proposal-request.json', request)
        request = recovery.read(directory / 'proposal-request.json')
        attempt = {**request, 'provider': 'codex_cli', 'model': 'gpt-6-luna', 'effort': 'low',
                   'request_sha256': recovery.file_digest(directory / 'proposal-request.json')}
        attempt_path = directory / 'codex_cli-attempt-0.request.json'
        recovery.immutable(attempt_path, attempt)
        answer_path = directory / 'codex_cli-attempt-0.answer.json'
        metadata = {'provider': 'codex_cli', 'model_used': 'gpt-6-luna', 'effort': 'low'}
        saved = {'request_sha256': recovery.file_digest(attempt_path), 'answer': self.answer, 'metadata': metadata}
        recovery.immutable(answer_path, saved)
        proposal = {'schema': 1, 'policy': recovery.POLICY, 'campaign_sha256': recovery.file_digest(campaign_file),
                    'request_sha256': attempt['request_sha256'], 'answer': self.answer, 'metadata': metadata,
                    'attempt_request': attempt_path.relative_to(self.folder).as_posix(),
                    'attempt_request_sha256': recovery.file_digest(attempt_path),
                    'attempt_answer': answer_path.relative_to(self.folder).as_posix(),
                    'attempt_answer_sha256': recovery.file_digest(answer_path), 'failures': [], 'audio_approval': False}
        recovery.immutable(recovery.proposal_path(self.folder, self.unit), proposal)
        return proposal

    def test_immutable_campaign_keeps_all_parent_failures(self):
        self.assertEqual(len(self.campaign['parents']), 3)
        parent_path = self.folder / self.campaign['parents'][0]['path']
        obj = recovery.read(parent_path)
        obj['quality_error'] = 'Changed evidence'
        recovery.renderer.write(parent_path, obj)
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            recovery.validate_campaign(self.folder, self.unit)

    def test_proposal_cannot_change_text_or_choose_unknown_sampling(self):
        recovery.validate_proposal(self.answer, self.campaign)
        for changes in ({'synthesis_text': 'Her gym.'}, {'synthesis_text': 'Her Jim!'},
                        {'profile_order': ['careful', 'careful', 'steady']},
                        {'profile_order': ['careful', 'steady', 'effect']}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    recovery.validate_proposal({**self.answer, **changes}, self.campaign)

    def test_recovery_receipt_recomputes_exact_source_and_sampling(self):
        proposal = self.proposal()
        recovery.validated_proposal(self.folder, self.unit, self.campaign)
        specification = recovery.recovery_spec(self.folder, self.unit, self.campaign, proposal, 1)
        self.assertEqual(specification['request']['text'], self.native_request['text'])
        self.assertNotEqual(specification['request']['seed'], self.native_request['seed'])
        receipt = {'unit': self.unit['id'], 'take': 3, 'spec': specification,
                   'spec_sha256': recovery.renderer.sha(specification), 'quality_passed': False}
        main = self.folder / 'takes/u0001/take-03.json'
        mirror = self.folder / 'recovery/u0001/takes/take-03.json'
        recovery.immutable(main, receipt)
        recovery.immutable(mirror, receipt)
        self.assertTrue(recovery.validate_recovery_receipt(self.folder, self.unit, receipt)['native_text_unchanged'])
        changed = copy.deepcopy(receipt)
        changed['spec']['request']['temperature'] = 1.5
        changed['spec_sha256'] = recovery.renderer.sha(changed['spec'])
        with self.assertRaisesRegex(ValueError, 'unauthorized sampling'):
            recovery.validate_recovery_receipt(self.folder, self.unit, changed)
        with self.assertRaisesRegex(ValueError, 'three separately bounded'):
            recovery.recovery_spec(self.folder, self.unit, self.campaign, proposal, 4)

    def test_cli_failure_fallback_is_explicit_and_bounded_without_real_calls(self):
        executable = self.folder / 'fake-cli.exe'
        executable.write_bytes(b'never executed')
        with patch('rimsky.codex_analysis.codex_executable', return_value=str(executable)), \
             patch('rimsky.claude_analysis.claude_executable', return_value=str(executable)), \
             patch.object(recovery, 'run_cli', return_value=subprocess.CompletedProcess([], 0, 'synthetic 1', '')), \
             patch('rimsky.codex_analysis.structured_call', side_effect=RuntimeError('Unavailable')) as codex, \
             patch('rimsky.claude_analysis.structured_call', return_value=self.answer) as claude:
            proposal = recovery.propose(self.folder, self.unit)
        self.assertEqual(codex.call_count, 2)
        self.assertEqual(claude.call_count, 1)
        self.assertEqual(proposal['metadata']['model_used'], 'claude-haiku-5-5')
        self.assertTrue(proposal['metadata']['fallback'])
        self.assertEqual(len(proposal['failures']), 2)


if __name__ == '__main__':
    unittest.main()
