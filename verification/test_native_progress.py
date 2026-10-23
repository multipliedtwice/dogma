import unittest,sys,tempfile,json
from pathlib import Path
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky.native_progress import ProgressBudget,CandidateRenderTimeout,CandidateRenderFailure,owned_native_process
from rimsky.util import file_digest,write_json
from higgs_exhausted_word_recovery import render_candidate

class NativeProgressTests(unittest.TestCase):
    def test_native_child_is_cleaned_up_after_a_checkpoint_exception(self):
        child=Mock();child.poll.return_value=None
        with patch('rimsky.native_progress.subprocess.Popen',return_value=child),\
             patch('rimsky.cli_process.terminate_process_tree') as terminate:
            with self.assertRaisesRegex(ValueError,'integrity'):
                with owned_native_process(['owned-runner']):raise ValueError('integrity')
            terminate.assert_called_once_with(child)

    def test_failed_candidate_keeps_partial_receipts_and_advances(self):
        with tempfile.TemporaryDirectory() as work:
            take=Path(work);plan=take/'render-plan.json';plan.write_text('frozen native request','utf8')
            write_json(take/'receipts.json',{'takes':{'one':{'key':'accepted'}}})
            render=Mock(side_effect=CandidateRenderFailure('two reached its cap'))
            render_candidate(take/'assignment.json',take,'c0001',render,lambda:None)
            self.assertEqual({'one':{'key':'accepted'}},json.loads((take/'receipts.json').read_text())['takes'])
            write_json(take/'candidate-render-failure.json',{'plan_sha256':file_digest(plan),'completed':['one']})
            render.reset_mock()
            render_candidate(take/'assignment.json',take,'c0001',render,lambda:None)
            render.assert_not_called()

    def test_full_sentence_recovery_does_not_shrink_token_budget(self):
        from rimsky.recovery_decoding import execution_pack
        pack={'_fingerprint':'original','speech':{'sampling':{'temperature':.8,'top_p':.95},'max_new_tokens':2048}}
        small=execution_pack(pack,'careful','A sentence with five words.')
        full=execution_pack(pack,'careful_full','A sentence with five words.')
        self.assertEqual(384,small['speech']['max_new_tokens'])
        self.assertEqual(2048,full['speech']['max_new_tokens'])
        self.assertNotEqual(full['_fingerprint'],small['_fingerprint'])
        self.assertEqual(2048,pack['speech']['max_new_tokens'])

    def test_only_completed_outputs_extend_the_native_deadline(self):
        now=[0.];budget=ProgressBudget(1,900,lambda:now[0])
        now[0]=899;budget.observe(1)
        now[0]=900
        with self.assertRaises(CandidateRenderTimeout):budget.observe(1)
        budget=ProgressBudget(1,900,lambda:now[0])
        now[0]=1799;budget.observe(2)
        now[0]=2698;budget.observe(2)
        now[0]=2699
        with self.assertRaises(CandidateRenderTimeout):budget.observe(2)
        with self.assertRaises(ValueError):budget.observe(0)

    def test_timeout_preserves_checkpoint_and_does_not_repeat_frozen_attempt(self):
        with tempfile.TemporaryDirectory() as work:
            take=Path(work);plan=take/'render-plan.json';plan.write_text('frozen native request','utf8')
            write_json(take/'candidate-render-timeout.json',{'plan_sha256':file_digest(plan),'completed':['one'],'pending':['two']})
            render=Mock();render_candidate(take/'assignment.json',take,'c0001',render,lambda:None)
            render.assert_not_called()
            self.assertEqual(['one'],json.loads((take/'candidate-render-timeout.json').read_text('utf8'))['completed'])
            plan.write_text('different request','utf8')
            with self.assertRaises(ValueError):render_candidate(take/'assignment.json',take,'c0001',render,lambda:None)

if __name__=='__main__':unittest.main()
