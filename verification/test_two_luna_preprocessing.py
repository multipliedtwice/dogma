"""Two independent opinions, pass-2 authority and source-bound failure gates."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import build_contextual_stress as context
import build_yo_restoration as yo
from rimsky import contextual_stress as rules
from verification.luna_test_support import responder
from rimsky.util import ProviderTransportError


class TwoLuna(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        exe=self.root/'codex.exe';exe.write_bytes(b'fixture')
        self.metadata=dict(zip(('provider','model','effort'),context.PIN))|{'policy':rules.RULE,'requested_model':'gpt-6-luna',
            'model_provenance':'explicit-cli-model-selection','cli_version':'fixture','executable':str(exe),
            'executable_sha256':context.file_digest(exe),'structured_call_sha256':context.file_digest(Path(context.codex_analysis.__file__))}
        self.inputs=[{'source':'На площади ни души…','selected':'На площади ни души́…',
            'upstream_evidence':{'stage':'post Silero + HS/YO/guard','automatic_pronunciation':{'close_mark_guard':{'dropped':[]}}}},
            {'source':'Здесь стоит человек.','selected':'Здесь стоит человек.'}]
    def votes(self,system,request,schema):
        return {'decisions':{item['id']:{'choice':('ду́ши' if request['pass_id']=='pass-1' else 'души́')
            if item['word']=='души' else item['candidates'][0 if request['pass_id']=='pass-1' else -1],
            'basis':'Independent grammar and inflected-form analysis.'} for item in request['items']}}
    def prepare(self):
        with patch.object(context,'call',side_effect=responder(self.votes,self.metadata)) as call:
            block=context.prepare(self.inputs,self.root/'context');self.assertEqual(call.call_count,2)
        return block
    def test_two_actual_requests_are_blind_counterbalanced_and_second_wins(self):
        block=self.prepare();decision=next(r for r in block['decisions'] if r['word']=='души')
        pair=decision['two_luna'];self.assertEqual(decision['stressed'],'души́');self.assertFalse(pair['agreement'])
        self.assertEqual(pair['selected_pass'],'pass-2');self.assertEqual(len(pair['passes']),2)
        self.assertNotEqual(pair['passes'][0]['invocation_id'],pair['passes'][1]['invocation_id'])
        files=list((self.root/'context/batches').glob('*/pass-1/request.json'));self.assertEqual(len(files),1)
        first=context.read(files[0]);second=context.read(files[0].parent.parent/'pass-2/request.json')
        self.assertNotEqual(first['system'],second['system']);self.assertIn('INFLECTED FORM',second['system'])
        self.assertEqual([i['id'] for i in second['request']['items']],list(reversed([i['id'] for i in first['request']['items']])))
        before={i['id']:i for i in first['request']['items']};after={i['id']:i for i in second['request']['items']}
        for key,item in before.items():
            self.assertEqual(after[key]['candidates'],list(reversed(item['candidates'])))
            self.assertEqual(after[key]['upstream_proposal'],item['upstream_proposal'])
        self.assertNotIn('first_answer',second['request']);self.assertEqual(block,context.block(self.root/'context/answer.json'))
    def test_missing_duplicate_batches_and_pass_cache_tamper_are_rejected(self):
        self.prepare();path=self.root/'context/answer.json';original=context.read(path)
        for duplicate in (False,True):
            changed=copy.deepcopy(original)
            changed['batches']=changed['batches']*2 if duplicate else []
            changed['decisions']=changed['decisions']*2 if duplicate else []
            path.write_text(json.dumps(changed),encoding='utf8')
            with self.assertRaises(ValueError):context.block(path)
        path.write_text(json.dumps(original),encoding='utf8')
        one=next((self.root/'context/batches').glob('*/pass-1/answer.json'));two=one.parent.parent/'pass-2/answer.json'
        two.write_bytes(one.read_bytes())
        with self.assertRaises(ValueError):context.block(path)
    def test_malformed_vote_is_held_without_third_vote_or_ready_answer(self):
        def bad(system,request,schema):
            result=self.votes(system,request,schema)
            if request['pass_id']=='pass-1':next(iter(result['decisions'].values()))['choice']='unsupported'
            return result
        with patch.object(context,'call',side_effect=responder(bad,self.metadata)) as call:
            with self.assertRaises(ValueError):context.prepare(self.inputs,self.root/'bad')
            self.assertEqual(call.call_count,1)
        self.assertFalse((self.root/'bad/answer.json').exists());self.assertTrue((self.root/'bad/unresolved.json').exists())
        with patch.object(context,'call') as call,self.assertRaises(ValueError):context.prepare(self.inputs,self.root/'bad')
        call.assert_not_called()
    def test_success_binds_transport_history_and_rejects_later_orphan_attempts(self):
        good=responder(self.votes,self.metadata);calls=[]
        def flaky(system,request,schema,**kwargs):
            calls.append(request['pass_id'])
            if len(calls)==1:raise ProviderTransportError('fixture transport before execution')
            return good(system,request,schema,**kwargs)
        with patch.object(context,'call',side_effect=flaky):context.prepare(self.inputs,self.root/'retry')
        self.assertEqual(calls,['pass-1','pass-1','pass-2'])
        folder=next((self.root/'retry/batches').glob('*/pass-1'))
        saved=context.read(folder/'answer.json');self.assertEqual(len(saved['preceding_transport_failures']),1)
        consensus=context.read(folder.parent/'consensus.json')
        self.assertEqual(consensus['counts']['occurrences'],len(consensus['selected']))
        self.assertEqual(consensus['counts']['disagreements_second_selected'],1)
        context.block(self.root/'retry/answer.json')
        failure=folder/'attempt-0.failure.json';original=failure.read_bytes()
        changed=context.read(failure);changed['error']='rewritten transport account'
        failure.write_text(json.dumps(changed),encoding='utf8')
        with self.assertRaises(ValueError):context.block(self.root/'retry/answer.json')
        failure.write_bytes(original)
        (folder/'attempt-2.start.json').write_text('{}',encoding='utf8')
        with self.assertRaisesRegex(ValueError,'Orphan'):context.block(self.root/'retry/answer.json')
    def test_yo_second_choice_is_authoritative_and_witnesses_reach_both_calls(self):
        req={'task':'yo_restoration_adjudication','items':[{'id':'y0000','word':'Все','sentence':'Все голодные.',
            'candidate_e':'Все','candidate_yo':'Всё','upstream_witnesses':{'silero':'e','ruaccent':'yo'}}]}
        def votes(system,request,schema):
            return {'decisions':{'y0000':{'choice':'Всё' if request['pass_id']=='pass-1' else 'Все','basis':'Independent interpretation.'}}}
        metadata={**self.metadata,'policy':yo.POLICY}
        with patch.object(yo,'call',side_effect=responder(votes,metadata)) as call:
            batches=yo.adjudicate(self.root/'yo',req);self.assertEqual(call.call_count,2)
        self.assertEqual(batches[0]['answer']['decisions'][0]['choice'],'e');self.assertFalse(batches[0]['selections'][0]['agreement'])
        for request in (self.root/'yo/batches').glob('*/pass-*/request.json'):
            self.assertEqual(yo.read(request)['request']['items'][0]['upstream_witnesses'],{'silero':'e','ruaccent':'yo'})
        yo.validate_batch(batches[0])
    def test_low_confidence_unchanged_and_high_confidence_change_route_by_proposal_only(self):
        inputs=[{'source':'На площади ни души…','selected':'На площади ни души́…'},
                {'source':'Здесь стоит человек.','selected':'Здесь сто́ит человек.'}]
        requests=[]
        def vote(system,request,schema):
            requests.append(copy.deepcopy(request));result={}
            for item in request['items']:
                result[item['id']]={'choice':'души́' if item['word']=='души' else 'стои́т','basis':'Context and inflection.',
                    **({'confidence':0 if item['word']=='души' else 100} if request['pass_id']=='pass-1' else {})}
            return {'decisions':result}
        with patch.object(context,'call',side_effect=responder(vote,self.metadata)):
            block=context.prepare(inputs,self.root/'mixed')
        self.assertEqual(len(requests),2)
        self.assertEqual([i['id'] for i in requests[1]['items']],['c0001'])
        self.assertNotIn('confidence',requests[1]['items'][0]);self.assertNotIn('first_choice',requests[1]['items'][0])
        pair=[d['two_luna'] for d in block['decisions']]
        self.assertEqual((pair[0]['route'],pair[0]['first_confidence'],len(pair[0]['passes'])),('baseline-kept',0,1))
        self.assertEqual((pair[1]['route'],pair[1]['first_confidence'],len(pair[1]['passes'])),('proposed-change-rechecked',100,2))
        cp=next((self.root/'mixed/batches').glob('*/consensus.json'));manifest=context.read(cp)
        manifest['passes']=manifest['passes'][:1];cp.write_text(json.dumps(manifest),encoding='utf8')
        # Even an updated outer hash cannot make a missing required second opinion valid.
        answer=self.root/'mixed/answer.json';saved=context.read(answer)
        saved['batches'][0]['consensus_sha256']=context.file_digest(cp);answer.write_text(json.dumps(saved),encoding='utf8')
        with self.assertRaises(ValueError):context.block(answer)
    def test_all_baselines_kept_never_creates_second_call_and_rejects_extra_pass(self):
        inputs=[{'source':'На площади ни души…','selected':'На площади ни души́…'}]
        def vote(system,request,schema):
            return {'decisions':{i['id']:{'choice':i['baseline'],'basis':'Keep upstream proposal.','confidence':0} for i in request['items']}}
        with patch.object(context,'call',side_effect=responder(vote,self.metadata)) as call:
            block=context.prepare(inputs,self.root/'unchanged');self.assertEqual(call.call_count,1)
        pair=block['decisions'][0]['two_luna'];self.assertEqual(pair['selected_pass'],'pass-1');self.assertIsNone(pair['agreement'])
        folder=next((self.root/'unchanged/batches').glob('*/pass-1')).parent
        self.assertFalse((folder/'pass-2').exists());(folder/'pass-2').mkdir()
        with self.assertRaisesRegex(ValueError,'unexpected second check'):context.block(self.root/'unchanged/answer.json')
    def test_yo_kept_silero_proposal_needs_one_opinion_even_at_zero_confidence(self):
        req={'task':'yo_restoration_adjudication','items':[{'id':'y0000','word':'Все','sentence':'Все голодные.',
            'candidate_e':'Все','candidate_yo':'Всё','upstream_witnesses':{'silero':'e','ruaccent':'yo'}}]}
        vote={'decisions':{'y0000':{'choice':'Все','basis':'Keep the supplied Silero reading.','confidence':0}}}
        with patch.object(yo,'call',side_effect=responder(vote,{**self.metadata,'policy':yo.POLICY})) as call:
            batch=yo.adjudicate(self.root/'yo-kept',req)[0];self.assertEqual(call.call_count,1)
        pair=batch['selections'][0];self.assertEqual(pair['route'],'baseline-kept');self.assertEqual(pair['first_confidence'],0)
        self.assertEqual(len(pair['passes']),1);yo.validate_batch(batch)
    def test_changed_subset_missing_a_valid_second_decision_cannot_freeze(self):
        inputs=[{'source':'На площади ни души…','selected':'На площади ни души…'},
                {'source':'Здесь стоит человек.','selected':'Здесь стоит человек.'}]
        def vote(system,request,schema):
            result={i['id']:{'choice':i['candidates'][0],'basis':'Contextual reading.'} for i in request['items']}
            if request['pass_id']=='pass-2':result.pop(next(iter(result)))
            return {'decisions':result}
        with patch.object(context,'call',side_effect=responder(vote,self.metadata)) as call:
            with self.assertRaises(ValueError):context.prepare(inputs,self.root/'missing-second-item')
            self.assertEqual(call.call_count,2)
        self.assertFalse((self.root/'missing-second-item/answer.json').exists())


if __name__=='__main__':unittest.main()
