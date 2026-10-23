"""Synthetic supplementary-evidence provenance checks; no provider/inference."""
import copy
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import supplement_public_demo_word_recovery as helper
import resolve_public_demo_word_supplement as adapter
from verification import test_resolve_public_demo_word_pronunciation as fixtures


class Supplement(unittest.TestCase):
    def fixture(self):
        maker=fixtures.WordPronunciationAdapter();fixture=maker.fixture(True);self.addCleanup(maker.doCleanups)
        context,unit,receipt,oldpath,proof,bound,stress=fixture
        folder=context.folder;oldhome=helper.original.home(folder,unit);cli=folder/'reviewer.exe';cli.write_bytes(b'pinned executable')
        old={**bound,'generation':'frozen','target':unit,'repair':receipt['spec']['word_pronunciation_recovery']['repair'],
             'dictionary':[{'url':'https://orfo.ruslang.ru/abc/original','headword':'цуцик','fact':'Old general fact.','retrieval_date':'2026-10-23'}],
             'phoneme_evidence':{'linguistic_only':True,'audio_approval':False},'source_paragraph':context.blocks[unit['source_id']],
             'original_spec':{**receipt['spec'],'request':{'id':unit['id'],'text':unit['spoken'],'seed':7}}}
        old['original_spec'].pop('word_pronunciation_recovery')
        # V10 fixture makes a private native receipt; supplementary planning must
        # start with zero native attempts, so remove only our synthetic fixture.
        for p in oldpath.parent.iterdir():p.unlink()
        oldpath.parent.rmdir()
        helper.immutable(oldhome/'campaign.json',old);request=helper.original.proposal_request(old)
        helper.immutable(oldhome/'proposal-request.json',request)
        provider,model,effort=helper.isolated.PROVIDERS[0]
        rp=oldhome/(provider+'-attempt-1.request.json');ap=oldhome/(provider+'-attempt-1.answer.json');fp=oldhome/(provider+'-attempt-1.failure.json')
        helper.immutable(rp,{**request,'provider':provider,'model':model,'effort':effort})
        answer={'accepted':False,'reason':'Original facts do not establish lexical history or pronunciation.','synthesis_text':'','patches':[],
                'identity_only':True,'audio_approval':False}
        metadata={'provider':provider,'model_used':model,'effort':effort,'fallback':False,'version':'fixture',
                  'cli':str(cli),'cli_sha256':helper.file_digest(cli)}
        helper.immutable(ap,{'request_sha256':helper.file_digest(rp),'answer':answer,'metadata':metadata,'audio_approval':False})
        helper.immutable(fp,{'kind':'candidate_rejected','request_sha256':helper.file_digest(rp),'answer_sha256':helper.file_digest(ap),
                             'error':'substantive rejection','audio_approval':False})
        prior={key:{'path':path.relative_to(folder).as_posix(),'sha256':helper.file_digest(path)} for key,path in
               [('campaign',oldhome/'campaign.json'),('proposal_request',oldhome/'proposal-request.json'),('provider_request',rp),('provider_answer',ap),('failure',fp)]}
        prior.update(retained_byte_exact=True,rejection_remains_in_force=True)
        def fact(identifier,url):
            excerpt='Synthetic retained primary excerpt '+identifier
            return {'id':identifier,'url':url,'authority':'Synthetic official source fixture','retrieval_date':'2026-10-23',
              'retrieval':{'method':'Direct HTTPS GET; HTTP200','retrieved_utc':'2026-10-23T00:00:00Z','response_bytes':200,'response_sha256':'a'*64},
              'locator':'fixture','direct_fact':'New direct lexical or phonetic evidence for independent evaluation.',
              'excerpts':[{'text':excerpt,'utf8_sha256':hashlib.sha256(excerpt.encode()).hexdigest(),'whitespace':'collapsed only'}],'audio_approval':False}
        data={'schema':1,'policy':helper.FACT_POLICY,'unit':unit['id'],'frozen_generation':'frozen',
              'source':{k:unit[k] for k in ('id','source_id','start','end','text')},'source_text_sha256':helper.renderer.sha(unit['text']),
              'prior_rejection':prior,'candidate_repair_sha256':helper.renderer.sha(old['repair']),'candidate_repair_unchanged':True,
              'facts':[fact('lexical','https://oross.ruslang.ru/abc/new'),fact('phonetic','https://gramota.ru/uchebnik/new')],
              'limitations':['Linguistic facts only, not audio approval.'],'audio_approval':False}
        return context,unit,receipt,proof,old,stress,data

    def test_new_facts_bind_original_rejection_and_source_without_mutation(self):
        fixture=self.fixture();context,unit,_,_,old,_,data=fixture;before=copy.deepcopy(data)
        self.assertFalse(helper.validate_facts(context.folder,unit,data,old)['answer']['accepted']);self.assertEqual(data,before)
        for change in ('source','candidate','oldanswer','excerpt','nonewprimary'):
            bad=copy.deepcopy(data)
            if change=='source':bad['source']['text']='changed'
            elif change=='candidate':bad['candidate_repair_sha256']='0'*64
            elif change=='oldanswer':bad['prior_rejection']['provider_answer']['sha256']='0'*64
            elif change=='excerpt':bad['facts'][0]['excerpts'][0]['text']='forged'
            else:bad['facts'][1]['url']=old['dictionary'][0]['url']
            with self.subTest(change=change),self.assertRaises(ValueError):helper.validate_facts(context.folder,unit,bad,old)

    def test_zero_old_native_and_shared_three_slots_no_extension(self):
        context,unit,*_=self.fixture();helper.no_prior_native(context.folder,unit)
        old=helper.original.attempt_home(context.folder,unit,1);old.mkdir()
        with self.assertRaisesRegex(ValueError,'zero original'):helper.no_prior_native(context.folder,unit)
        old.rmdir();canonical=context.folder/'takes'/unit['id']/'take-15.json';canonical.parent.mkdir(parents=True);canonical.write_bytes(b'other strategy')
        with self.assertRaisesRegex(ValueError,'another strategy'):helper.no_prior_native(context.folder,unit,True)
        with self.assertRaisesRegex(ValueError,'three total'):helper.attempt_home(context.folder,unit,4)

    def test_forged_runtime_versions_never_define_expected_configuration(self):
        fixture,bound=self.planned();_,unit,receipt,_,old,*_=fixture
        check={'verifier':{'torch':'forged','transformers':'forged'},'heard':unit['spoken']}
        with patch.object(helper,'verifier_config') as expected:
            with self.assertRaisesRegex(ValueError,'configuration changed'):
                helper.check_record(check,unit['spoken'],receipt['audio_sha256'],bound,old,'qwen')
            expected.assert_not_called()

    def test_transient_transport_is_bounded_same_request_and_does_not_retry_rejection(self):
        fixture,bound=self.planned();context,unit,_,_,old,*_=fixture;calls=[]
        rejection={'accepted':False,'reason':'Still rejected.','synthesis_text':'','patches':[],'identity_only':True,'audio_approval':False}
        def call(*args):
            calls.append(copy.deepcopy(args[3]))
            if len(calls)==1:raise OSError('Temporary transport outage')
            return {'request_sha256':helper.file_digest(args[-1]),'answer':rejection,'metadata':{},'audio_approval':False}
        with patch.object(helper,'prepared_bound',return_value=(bound,old)),patch.object(helper.original,'call_provider',side_effect=call):
            with self.assertRaises(helper.original.ProposalRejected):helper.propose(context.folder,unit)
            with self.assertRaises(helper.original.ProposalRejected):helper.propose(context.folder,unit)
        self.assertEqual(len(calls),2);self.assertEqual(calls[0],calls[1])
        transport=helper.read(helper.home(context.folder,unit)/'codex_cli-transport-1.json');self.assertTrue(transport['transient'])

    def planned(self):
        fixture=self.fixture();context,unit,_,_,old,stress,data=fixture;path=context.folder/'new-facts.json';helper.immutable(path,data)
        with patch.object(helper.original,'campaign',return_value=old),patch.object(helper.original,'validated_stress',return_value=stress):
            bound=helper.plan(context.folder,unit,path)
        return fixture,{**bound,'rejection_reason':'Original facts did not establish identity.'}

    def test_provider_request_discloses_new_basis_rejection_and_can_reject_again(self):
        fixture,bound=self.planned();context,unit,_,_,old,_,data=fixture
        request=helper.proposal_request(bound,old);text=str(request)
        self.assertNotIn('palette_reference',text);self.assertNotIn('PRIVATE',text);self.assertNotIn('training-work',text)
        self.assertEqual(request['request']['candidate'],helper.original.proposal_request(old)['request']['candidate'])
        self.assertEqual(request['request']['supplementary_review']['old_rejection_sha256'],data['prior_rejection']['provider_answer']['sha256'])
        self.assertIn('accept OR reject',text)
        rejection={'accepted':False,'reason':'New facts still insufficient.','synthesis_text':'','patches':[],'identity_only':True,'audio_approval':False}
        def call(*args):return {'request_sha256':helper.file_digest(args[-1]),'answer':rejection,'metadata':{},'audio_approval':False}
        with patch.object(helper,'prepared_bound',return_value=(bound,old)),patch.object(helper.original,'call_provider',side_effect=call) as provider:
            for repeat in (1,2):
                with self.assertRaises(helper.original.ProposalRejected):helper.propose(context.folder,unit)
            self.assertEqual(provider.call_count,1)
        self.assertTrue((helper.home(context.folder,unit)/'codex_cli-attempt-1.failure.json').is_file())
        self.assertFalse((helper.home(context.folder,unit)/'proposal.json').exists())

    def supplemented_receipt(self):
        fixture,bound=self.planned();context,unit,receipt,proof,old,stress,data=fixture
        receipt=copy.deepcopy(receipt);receipt['spec'].pop('word_pronunciation_recovery')
        receipt['spec'][helper.MARKER]={'policy':helper.POLICY,'attempt':1,'repair':old['repair']}
        receipt['spec_sha256']=adapter.base.sha(receipt['spec']);path=helper.receipt_path(context.folder,unit,1);helper.immutable(path,receipt)
        # Re-create source-blind checks in the new namespace and compute the
        # frozen whole-source decision; the original historical raw mismatch stays.
        evidence=[]
        for recognizer,role,family in [('qwen','primary','qwen'),('turbo','witness','whisper')]:
            row=copy.deepcopy(next(r for r in proof['target_decision']['evidence'] if r['role']==role));check=path.parent/('native-'+recognizer+'.json')
            helper.immutable(check,{**row['raw'],'verifier':row['verifier']});row.update(path=check.relative_to(context.folder).as_posix(),sha256=helper.file_digest(check));row.pop('comparison')
            evidence.append(row)
        decision=helper.original.strict_decision(unit,evidence,context.blocks[unit['source_id']],old['repair'])
        proof=copy.deepcopy(proof);proof.update(policy=helper.POLICY,receipt_sha256=helper.file_digest(path),target_decision=decision)
        context.word_supplement_dependencies={'v11':'pinned'}
        context.__class__=adapter.Context
        return context,unit,receipt,path,proof,bound,old

    def receipt_guards(self,fixture):
        context,unit,receipt,path,proof,bound,old=fixture
        return patch.multiple(helper,validate_supplement_receipt=Mock(return_value=proof),prepared_bound=Mock(return_value=(bound,old)),
          check_record=Mock(side_effect=lambda check,*args:{k:v for k,v in check.items() if k!='verifier'}))

    def test_adapter_registration_is_append_only_and_source_bound(self):
        fixture=self.supplemented_receipt();context,unit,receipt,path,proof,_,_=fixture
        with self.receipt_guards(fixture):
            record=adapter.register(context,path);canonical=context.folder/record['canonical'];self.assertEqual(canonical.read_bytes(),path.read_bytes())
            self.assertTrue(context.assess(unit,receipt)['decision']['passed']);self.assertNotIn('palette_reference',str(record))
            canonical.write_bytes(b'protected conflict')
            with self.assertRaisesRegex(ValueError,'Never overwrite'):adapter.register(context,path)
        for field,value in [('request',{'text':unit['spoken']}),('source',{**unit,'text':'changed'}),('model',{'other':'model'})]:
            bad=copy.deepcopy(receipt);bad['spec'][field]=value;bad['spec_sha256']=adapter.base.sha(bad['spec'])
            with self.receipt_guards(fixture),self.assertRaises(ValueError):adapter.validate(context,unit,bad,proof['receipt_sha256'])

    def test_both_original_source_recognizers_strictly_recomputed(self):
        fixture=self.supplemented_receipt();context,unit,receipt,path,proof,_,_=fixture
        proof['target_decision']['evidence'][0]['comparison']['passed']=False
        with self.receipt_guards(fixture),self.assertRaisesRegex(ValueError,'Both original-source'):
            adapter.validate(context,unit,receipt,helper.file_digest(path))

    def test_unmarked_receipts_delegate_and_extension_dependencies_stay_separate(self):
        fixture=self.supplemented_receipt();context,unit,*_=fixture
        with patch.object(adapter.previous.Context,'assess',return_value='frozen V10'):
            self.assertEqual(context.assess(unit,{'spec':{}}),'frozen V10')
        context.recheck=adapter.Context.recheck.__get__(context)
        with patch.object(adapter.previous.Context,'recheck'),patch.object(adapter,'dependencies',return_value={'new':'hash'}):
            with self.assertRaisesRegex(ValueError,'Supplement dependencies changed'):context.recheck()
        self.assertEqual(context.dependencies,{})

    def test_report_preserves_registered_raw_counts_and_binds_new_provenance_before_hash(self):
        fixture=self.supplemented_receipt();context,unit,receipt,_,proof,*_=fixture
        summary={'take':15,'raw_transcripts':[{'evidence_sha256':r['sha256']} for r in proof['target_decision']['evidence']]}
        row={'id':'u1','takes':[summary],'selected':summary}
        value={'units':[row],'resolution_adapter_sha256':'frozen-v10','raw_mismatch_check_count':41,'disclosure':'Old failures retained.'}
        with patch.object(adapter.previous,'report',return_value=value),patch.object(adapter.base.renderer,'takes',return_value=[receipt]),\
             patch.object(context,'assess',return_value={'word_supplement_provenance':{'old_substantive_rejection_sha256':'pinned'}}):result=adapter.report(context)
        self.assertEqual(result['raw_mismatch_check_count'],41);self.assertEqual(result['raw_unregistered_word_supplement_mismatch_check_count'],0)
        self.assertEqual(result['word_pronunciation_resolution_adapter_sha256'],'frozen-v10')
        before=adapter.base.sha(result);row['selected']['word_supplement_provenance']['old_substantive_rejection_sha256']='tampered'
        self.assertNotEqual(before,adapter.base.sha(result))

    def test_new_native_request_preserves_every_sound_input_except_reviewed_patch_and_profile(self):
        fixture,bound=self.planned();context,unit,_,_,old,_,_=fixture;home=helper.home(context.folder,unit)
        helper.immutable(home/'proposal.json',{'fixture':'pin'})
        spec=helper.native_spec(context.folder,unit,bound,old,{},1)
        self.assertEqual(spec['request']['text'],old['repair']['synthesis_text']);self.assertEqual(spec['source'],unit)
        self.assertEqual(spec['model'],old['original_spec']['model']);self.assertEqual(spec['reference'],unit['palette_reference'])
        self.assertNotIn('word_pronunciation_recovery',spec);self.assertEqual(spec[helper.MARKER]['limit'],3)

    def test_ownership_sees_new_helper_and_controller_not_cpu_preparation(self):
        fixture=self.fixture();folder=fixture[0].folder
        def row(pid,script,stage,extra=()):return {'pid':pid,'ppid':0,'name':'python.exe','cwd':str(ROOT),'create_time':123,
            'command':['python',str(ROOT/'scripts'/script),'--folder',str(folder),'--stage',stage,*extra]}
        busy=adapter.previous_jobs([row(1,helper.SELF.name,'render'),row(2,helper.SELF.name,'propose'),row(3,adapter.SELF.name,'all'),
                                   row(4,adapter.SELF.name,'all',['--prepare-only']),row(5,'unrelated.py','all')],[folder],999)
        self.assertEqual({r['pid'] for r in busy},{1,3})

    def test_controller_cpu_preparation_never_runs_native_and_uses_asr_environment(self):
        fixture=self.fixture();context=fixture[0];data=context.folder/'supplement.json';data.write_bytes(b'private facts')
        args=SimpleNamespace(folder=[context.folder],unit=['u1'],output=context.folder/'controller',supplement_data=data,prepare_only=True)
        engine=Mock();engine.state={};engine.command.side_effect=lambda script,folder,flags,**kw:[str(script),*flags]
        engine.refresh.return_value={'report':{'units':[{'id':'u1','selected':None}]}}
        pin={'supplement_dependencies':{'code':'pin'},'supplement_data_sha256':helper.file_digest(data)}
        with patch.object(adapter,'configuration',return_value=pin),patch.object(adapter,'dependencies',return_value=pin['supplement_dependencies']),\
             patch.object(adapter.ownership,'pin_configuration'),patch.object(adapter,'Engine',return_value=engine),patch.object(adapter,'wait_previous') as guard:
            self.assertEqual(adapter.all_work(args),0)
        self.assertEqual([c.args[0] for c in engine.run.call_args_list],['supplement-u1-plan','supplement-u1-propose'])
        self.assertTrue(all(c.kwargs['asr'] for c in engine.command.call_args_list));guard.assert_not_called()
        self.assertTrue(engine.refresh.call_args.args[1]);self.assertEqual(engine.state['status'],'prepared')

    def test_controller_serial_both_checks_then_register_progressive_publish(self):
        fixture=self.fixture();context=fixture[0];data=context.folder/'supplement.json';data.write_bytes(b'private facts')
        args=SimpleNamespace(folder=[context.folder],unit=['u1'],output=context.folder/'controller',supplement_data=data,prepare_only=False)
        engine=Mock();engine.state={};engine.command.side_effect=lambda script,folder,flags,**kw:[str(script),*flags]
        engine.refresh.side_effect=[{'report':{'units':[{'id':'u1','selected':None}]}},
            {'report':{'units':[{'id':'u1','selected':{'take':15}}]},'completion_valid':False,'completion_reason':'other units held'}]
        events=[];engine.run.side_effect=lambda name,*a,**kw:events.append(name)
        pin={'supplement_dependencies':{'code':'pin'},'supplement_data_sha256':helper.file_digest(data)}
        with patch.object(adapter,'configuration',return_value=pin),patch.object(adapter,'dependencies',return_value=pin['supplement_dependencies']),\
             patch.object(adapter.ownership,'pin_configuration'),patch.object(adapter,'Engine',return_value=engine),patch.object(adapter,'wait_previous'),\
             patch.object(adapter.supervision,'read',return_value={'units':[{'id':'u1'}]}):self.assertEqual(adapter.all_work(args),1)
        self.assertEqual(events,['supplement-u1-plan','supplement-u1-propose','supplement-u1-1-render','supplement-u1-1-verifyqwen',
                                  'supplement-u1-1-verifyturbo','supplement-u1-1-validate','supplement-u1-1-register'])
        self.assertEqual(engine.refresh.call_count,2);self.assertEqual(engine.state['status'],'held')


if __name__=='__main__':unittest.main()
