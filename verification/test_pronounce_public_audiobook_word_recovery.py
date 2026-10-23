"""Generic source-bound word repairs, CPU-only; no providers or synthesis."""
import copy
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import pronounce_public_audiobook_word_recovery as m
from rimsky import homograph_stress as hs
from rimsky.speech_quality import transcript_check,word_tokens


class WordRecovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.folder=Path(self.temp.name)

    def fixture(self,source='Он братец добрый.',native=None,before='братец',after='бра́тец',kind='stress-only'):
        unit={'id':'fixture','source_id':'paragraph','start':21,'end':21+len(source),'text':source,
              'spoken':source,'synthesis_text':native or source}
        native=unit['synthesis_text'];start=native.index(before);surface=m.plain(before)
        offset=source.index(surface)
        facts=[{'url':'https://orfo.ruslang.ru/abc/fixture','headword':m.plain(after),
                'fact':'Synthetic dictionary fact for invariant testing, not a real dictionary quotation.',
                'retrieval_date':'2026-10-23'}]
        row={'id':unit['id'],'source_id':unit['source_id'],'start':unit['start'],'end':unit['end'],
             'text_sha256':m.renderer.sha(source),'native_before':native,
             'native_after':native[:start]+after+native[start+len(before):],
             'patches':[{'source_start':unit['start']+offset,'source_end':unit['start']+offset+len(surface),
                         'source_surface':surface,'native_start':start,'native_end':start+len(before),
                         'before':before,'after':after,'kind':kind,'dictionary_facts':facts}]}
        return unit,row

    def bound(self,unit,row):
        return {'target':unit,'repair':m.repair(unit,row),'dictionary':row['patches'][0]['dictionary_facts'],
                'phoneme_evidence':{'linguistic_only':True,'audio_approval':False},
                'original_spec':{'reference':'PRIVATE_REFERENCE','request':{'reference_text':'PRIVATE_SENTINEL'}}}

    def evidence(self,unit,a,b):
        return [{'path':name+'.json','sha256':'a'*64,'raw':transcript_check(unit['spoken'],heard,'b'*64,'c'*64),
                 'role':role,'family':family,'verifier':{}}
                for name,heard,role,family in [('qwen',a,'primary','qwen'),('turbo',b,'witness','whisper')]]

    def test_stress_addition_keeps_source_and_old_stress_attachments(self):
        unit,row=self.fixture('Он братец добрый.',native='О́н братец до́брый.')
        before=copy.deepcopy(unit);result=m.repair(unit,row)
        self.assertEqual(result['synthesis_text'],'О́н бра́тец до́брый.')
        self.assertEqual(unit,before)
        self.assertTrue(set(result['original_stress_anchors'])<=set(result['candidate_stress_anchors']))
        self.assertEqual(result['patches'][0]['spoken_index'],1)

    def test_letter_spacing_punctuation_and_existing_stress_changes_reject(self):
        for after in ('брат','брате́ц!','бра-тец','бра́те́ц','бра́тец лишний'):
            unit,row=self.fixture(after=after)
            with self.subTest(after=after),self.assertRaises(ValueError):m.repair(unit,row)
        unit,row=self.fixture(native='Он бра́тец добрый.',before='бра́тец',after='брате́ц')
        with self.assertRaisesRegex(ValueError,'stress'):m.repair(unit,row)

    def test_historical_one_letter_rule_only_after_hard_ts(self):
        unit,row=self.fixture('Он цыгарку взял.',native='Он цыга́рку взял.',before='цыга́рку',after='цига́рку',kind='historical-ts-spelling')
        candidate=m.repair(unit,row)
        self.assertEqual(candidate['synthesis_text'],'Он цига́рку взял.')
        self.assertEqual(candidate['source_stress_projection'],'Он цыга́рку взял.')
        for before,after in [('мы́ло','ми́ло'),('цыга́рку','сига́рку'),('цыга́рку','цига́ретку')]:
            unit,row=self.fixture('Он '+m.plain(before)+' взял.',native='Он '+before+' взял.',before=before,after=after,kind='historical-ts-spelling')
            with self.subTest(before=before,after=after),self.assertRaises(ValueError):m.repair(unit,row)

    def test_offsets_and_repeated_occurrences_must_bind_same_word(self):
        unit,row=self.fixture('Братец братец добрый.',before='братец')
        row['patches'][0]['source_start']-=7;row['patches'][0]['source_end']-=7
        with self.assertRaises(ValueError):m.repair(unit,row)
        unit,row=self.fixture();row['patches'][0]['native_start']+=1
        with self.assertRaises(ValueError):m.repair(unit,row)
        unit,row=self.fixture();row['native_after']+='!'
        with self.assertRaisesRegex(ValueError,'unapproved'):m.repair(unit,row)

    def test_stress_crosscheck_records_both_engines_and_requires_disagreement_adjudication(self):
        unit,row=self.fixture();patchset={'schema':1,'policy':m.PATCH_POLICY,'frozen_generation':'g','units':[row]}
        a={'units':[unit]}
        rows=m.crosscheck_patchset(patchset,a,'g',lambda text:'Он бра́тец добрый.',lambda text:{('братец',0):2})
        self.assertTrue(rows[0]['crosschecked'])
        rows=m.crosscheck_patchset(patchset,a,'g',lambda text:'Он брате́ц добрый.',lambda text:{('братец',0):2})
        self.assertFalse(rows[0]['crosschecked'])
        req=m.stress_request(rows);item=req['items'][0]
        self.assertEqual(item['dictionary_candidate'],'бра́тец')
        for choice,stressed in [('silero','брате́ц'),('ruaccent','бра́тец'),('dictionary','бра́тец'),('neither','братец')]:
            m.validate_stress_answer({'decisions':[{'id':item['id'],'choice':choice,'stressed':stressed,'reason':'Fixture.'}]},req)
        with self.assertRaises(ValueError):m.validate_stress_answer({'decisions':[{'id':item['id'],'choice':'dictionary','stressed':'братцы'}]},req)

    def test_preserved_historical_old_stress_needs_no_new_stress_decision(self):
        unit,row=self.fixture('Он цыгарку взял.',native='Он цыга́рку взял.',before='цыга́рку',after='цига́рку',kind='historical-ts-spelling')
        patchset={'schema':1,'policy':m.PATCH_POLICY,'frozen_generation':'g','units':[row]}
        def forbidden(text):raise AssertionError('Existing approved stress must not be reconsidered')
        self.assertEqual(m.crosscheck_patchset(patchset,{'units':[unit]},'g',forbidden,forbidden),[])

    def test_review_request_has_exact_candidate_facts_but_no_private_reference(self):
        unit,row=self.fixture();bound=self.bound(unit,row);request=m.proposal_request(bound)
        self.assertNotIn('PRIVATE',json.dumps(request));self.assertEqual(request['schema']['properties']['accepted'],{'type':'boolean'})
        answer={'accepted':True,'reason':'Lexical identity unchanged.','synthesis_text':row['native_after'],
                'patches':request['request']['candidate']['patches'],'identity_only':True,'audio_approval':False}
        m.validate_answer(answer,bound)
        with self.assertRaises(m.ProposalRejected):m.validate_answer({**answer,'accepted':False},bound)
        with self.assertRaises(ValueError):m.validate_answer({**answer,'synthesis_text':unit['spoken']},bound)

    def test_rejection_preserved_without_persuasion_retry(self):
        unit,row=self.fixture();bound=self.bound(unit,row);request=m.proposal_request(bound)
        answer={'accepted':False,'reason':'Fixture rejection.','synthesis_text':'','patches':[],
                'identity_only':True,'audio_approval':False}
        def reply(*args):return {'request_sha256':m.file_digest(args[-1]),'answer':answer,'metadata':{},'audio_approval':False}
        with patch.object(m,'call_provider',side_effect=reply) as call:
            with self.assertRaises(m.ProposalRejected):m.evaluate_provider(self.folder,unit,bound,request,*m.isolated.PROVIDERS[0])
            saved=m.home(self.folder,unit)/'codex_cli-attempt-1.answer.json';before=saved.read_bytes()
            with self.assertRaisesRegex(ValueError,'no persuasion retry'):m.evaluate_provider(self.folder,unit,bound,request,*m.isolated.PROVIDERS[0])
            self.assertEqual(call.call_count,1);self.assertEqual(saved.read_bytes(),before)

    def test_both_whole_source_recognizers_required_and_ordinary_target_not_name(self):
        unit,row=self.fixture();candidate=m.repair(unit,row)
        self.assertTrue(m.strict_decision(unit,self.evidence(unit,unit['spoken'],unit['spoken']),unit['text'],candidate)['passed'])
        # Force the shared helper's permissive invented-word branch, then
        # demonstrate the new ordinary-target guard still rejects it.
        import resolve_public_demo_recognition as resolver
        fake={'passed':True,'canonical_words':word_tokens(unit['spoken']),'equivalences':[],
              'tolerated_deviations':[{'source_index':1,'source_tokens':['братец'],'heard_tokens':['брате'],'rule':'proper-noun-slot-v1'}]}
        with patch.object(resolver,'russian_equivalence',return_value=fake):
            self.assertFalse(m.strict_decision(unit,self.evidence(unit,'Он брате добрый.','Он брате добрый.'),unit['text'],candidate)['passed'])
        for heard in ('Он братец злой.','Он добрый.','Он братец добрый лишний.'):
            with self.subTest(heard=heard):self.assertFalse(m.strict_decision(unit,self.evidence(unit,heard,unit['spoken']),unit['text'],candidate)['passed'])

    def test_historical_bridge_requires_both_literal_modern_slots_and_discloses_raw(self):
        unit,row=self.fixture('Он цуцык добрый.',before='цуцык',after='цу́цик',kind='historical-ts-spelling')
        candidate=m.repair(unit,row);evidence=self.evidence(unit,'Он цуцик добрый.','Он цуцик добрый.')
        before=copy.deepcopy(evidence);result=m.strict_decision(unit,evidence,unit['text'],candidate)
        self.assertTrue(result['passed']);self.assertEqual(evidence,before)
        self.assertTrue(all(e['raw']['status']!='asr_exact_match' for e in result['evidence']))
        self.assertEqual(len([c for c in result['equivalences'] if c['rule']=='reviewed-source-bound-historical-hard-ts-spelling-v1']),2)
        for a,b in [('Он суцик добрый.','Он цуцик добрый.'),('Он цуцик добрый.','Он цуцык добрый.'),
                    ('Он цуцик злой.','Он цуцик добрый.'),('Он цуцик добрый лишний.','Он цуцик добрый.')]:
            with self.subTest(a=a,b=b):self.assertFalse(m.strict_decision(unit,self.evidence(unit,a,b),unit['text'],candidate)['passed'])

    def test_private_patchset_policy_and_native_attempt_boundaries(self):
        unit,row=self.fixture();data={'schema':1,'policy':m.PATCH_POLICY,'frozen_generation':'g','units':[row]}
        self.assertIn(unit['id'],m.validate_patchset(data,{'units':[unit]},'g'))
        with self.assertRaises(ValueError):m.validate_patchset(data,{'units':[unit]},'wrong')
        with self.assertRaises(ValueError):m.attempt_home(self.folder,unit,4)
        bound=self.bound(unit,row);bound['original_spec']={'request':{'text':unit['synthesis_text'],'seed':2}}
        m.immutable(m.home(self.folder,unit)/'campaign.json',{'fixture':True});m.immutable(m.home(self.folder,unit)/'proposal.json',{'fixture':True})
        specs=[m.native_spec(self.folder,unit,bound,{},n) for n in (1,2,3)]
        self.assertEqual(len({s['request']['seed'] for s in specs}),3)
        self.assertTrue(all(s['request']['text']==row['native_after'] for s in specs))
        self.assertTrue(all(s['word_pronunciation_recovery']['limit']==3 for s in specs))

    def test_hash_cache_rechecks_changed_missing_and_extra_inventory_files(self):
        root=self.folder/'model';root.mkdir();file=root/'weight';file.write_bytes(b'one')
        original=m.checked_inventory(root)
        with patch.object(m.renderer,'inventory',wraps=m.renderer.inventory) as inventory:
            self.assertEqual(m.checked_inventory(root),original);self.assertEqual(inventory.call_count,0)
            previous=file.stat().st_mtime_ns;file.write_bytes(b'two');os.utime(file,ns=(previous+2_000_000_000,previous+2_000_000_000))
            self.assertNotEqual(m.checked_inventory(root),original);self.assertEqual(inventory.call_count,1)
            extra=root/'extra';extra.write_bytes(b'extra');self.assertIn('extra',m.checked_inventory(root))
            file.unlink();self.assertNotIn('weight',m.checked_inventory(root))
            extra.unlink()
            with self.assertRaises(ValueError):m.checked_inventory(root)

    def test_native_receipt_rechecks_actual_output_and_execution_not_just_copied_wav(self):
        unit,row=self.fixture();bound=self.bound(unit,row)
        spec={'request':{'id':'native-fixture','max_tokens':256},'runner':{'path':'runner.exe'},'model':{'path':'model.gguf'}}
        base=m.attempt_home(self.folder,unit,1);base.mkdir(parents=True)
        sequence=base/'native-request.json';m.immutable(sequence,{'requests':[spec['request']]})
        command=['runner.exe','--task','tts','--family','higgs_audio_tts','--model','model.gguf','--backend','cuda',
                 '--device','0','--threads','4','--request-sequence',str(sequence),'--out-dir',str(base/'native'),
                 '--out-format','pcm16','--metrics']
        m.immutable(base/'native-session.json',{'command':command,'spec':spec,'sequence_sha256':m.file_digest(sequence)})
        (base/'native.log').write_bytes(b'Synthetic execution provenance')
        native=base/'native/native-fixture.wav';native.parent.mkdir();native.write_bytes(b'synthetic audio')
        checksum=m.file_digest(native);copied=base/(checksum+'.wav');copied.write_bytes(native.read_bytes())
        receipt={'schema':1,'unit':unit['id'],'take':15,'spec':spec,'spec_sha256':m.renderer.sha(spec),
                 'quality_passed':True,'quality':{'duration':1},'audio':copied.relative_to(self.folder).as_posix(),
                 'audio_sha256':checksum,'audio_approval':False}
        m.immutable(m.receipt_path(self.folder,unit,1),receipt)
        with patch.object(m,'campaign',return_value=bound),patch.object(m,'validated_proposal',return_value={}),\
             patch.object(m,'native_spec',return_value=spec),patch.object(m.renderer,'quality',return_value={'duration':1}):
            self.assertEqual(m.native_receipt(self.folder,unit,1)[2],receipt)
            native.write_bytes(b'different source output')
            with self.assertRaisesRegex(ValueError,'actual native'):m.native_receipt(self.folder,unit,1)
            native.write_bytes(copied.read_bytes());(base/'native.log').unlink()
            with self.assertRaisesRegex(ValueError,'execution evidence'):m.native_receipt(self.folder,unit,1)

    def test_source_blind_recognition_config_and_raw_transcript_tamper_reject(self):
        unit,row=self.fixture();bound=self.bound(unit,row);bound['language']='Russian';bound['qwen_files']={'w':'hash'}
        config=m.verifier_config(bound,'turbo',m.importlib.metadata.version('torch'),m.importlib.metadata.version('transformers'))
        raw=transcript_check(unit['spoken'],unit['spoken'],'a'*64,m.renderer.sha(config))
        check={**raw,'verifier':config}
        import witness_public_demo_turbo as turbo
        with patch.object(m,'checked_inventory',return_value=turbo.FILES):
            self.assertEqual(m.check_record(check,unit['spoken'],'a'*64,bound,'turbo'),raw)
            with self.assertRaisesRegex(ValueError,'recomputed'):
                m.check_record({**check,'heard':'Другая фраза.'},unit['spoken'],'a'*64,bound,'turbo')
            bad={**check,'verifier':{**config,'expected_text_given_to_recognizer':True}}
            with self.assertRaisesRegex(ValueError,'source-blind'):m.check_record(bad,unit['spoken'],'a'*64,bound,'turbo')
        with patch.object(m,'checked_inventory',return_value={}):
            with self.assertRaisesRegex(ValueError,'snapshot changed'):m.check_record(check,unit['spoken'],'a'*64,bound,'turbo')

    def negative_fixture(self,audio):
        unit,row=self.fixture();manifest={'speech_model':{'path':'model.gguf'},'runner':{'path':'runner.exe'}}
        spec={'source':unit,'request':{'id':'queued-request','text':unit['synthesis_text'],'max_tokens':256},
              'model':manifest['speech_model'],'runner':manifest['runner'],'generation':'generation','effects':'disabled'}
        base=self.folder/'recovery/sessions/session';base.mkdir(parents=True);staging=base/'native';staging.mkdir()
        seq=base/'sequence.json';m.immutable(seq,{'requests':[spec['request']]})
        command=['runner.exe','--task','tts','--family','higgs_audio_tts','--model','model.gguf','--backend','cuda',
                 '--device','0','--threads','4','--request-sequence',str(seq),'--out-dir',str(staging),'--out-format','pcm16','--metrics']
        m.immutable(base/'session-evidence.json',{'specs':[spec],'command':command,'sequence_sha256':m.file_digest(seq)})
        (base/'native.log').write_bytes(b'Preserved actual synthetic batch outcome log')
        receipt={'unit':unit['id'],'spec':spec,'spec_sha256':m.renderer.sha(spec),'quality_passed':False}
        if audio:
            native=staging/'queued-request.wav';native.write_bytes(b'synthetic unsafe audio')
            receipt.update(audio=native.relative_to(self.folder).as_posix(),audio_sha256=m.file_digest(native),quality_error='Unsafe edge')
        else:
            receipt['generation_error']='Native recovery exit 1'
            m.immutable(base/'worker.json',{'status':'stopped','error':receipt['generation_error']})
        return unit,manifest,base,receipt

    def test_negative_parent_quality_is_reproduced_not_just_labeled(self):
        unit,manifest,base,receipt=self.negative_fixture(True)
        with patch.object(m.isolated,'frozen_context',return_value=({'units':[unit]},manifest,'generation')):
            with patch.object(m.renderer,'quality',side_effect=ValueError('Unsafe edge')):
                proof=m.negative_parent_proof(self.folder,receipt)
                self.assertEqual(proof['failed_stage'],'native-quality');self.assertTrue(proof['own_audio_produced'])
            with patch.object(m.renderer,'quality',return_value={'duration':1}):
                with self.assertRaisesRegex(ValueError,'contradicted'):m.negative_parent_proof(self.folder,receipt)
            (base/'native.log').unlink()
            with self.assertRaisesRegex(ValueError,'execution log'):m.negative_parent_proof(self.folder,receipt)

    def test_aborted_queued_parent_retains_failure_without_claiming_own_audio(self):
        unit,manifest,base,receipt=self.negative_fixture(False)
        with patch.object(m.isolated,'frozen_context',return_value=({'units':[unit]},manifest,'generation')):
            proof=m.negative_parent_proof(self.folder,receipt)
            self.assertEqual(proof['failed_stage'],'native-generation');self.assertFalse(proof['own_audio_produced'])
            self.assertTrue(proof['queued_request_verified'])
            (base/'worker.json').unlink()
            with self.assertRaisesRegex(ValueError,'terminal failed-session'):m.negative_parent_proof(self.folder,receipt)

    def test_context_failed_command_windows_repr_is_reproduced_exactly(self):
        unit,_=self.fixture();base=m.previous.attempt_home(self.folder,unit,1);base.mkdir(parents=True)
        spec={'request':{'id':'windows-native','text':'unchanged context','max_tokens':512},
              'runner':{'path':r'C:\local\native\runner.exe'},'model':{'path':r'C:\local\model.gguf'}}
        seq=base/'native-request.json';m.immutable(seq,{'requests':[spec['request']]})
        command=[spec['runner']['path'],'--task','tts','--family','higgs_audio_tts','--model',spec['model']['path'],
                 '--backend','cuda','--device','0','--threads','4','--request-sequence',str(seq),'--out-dir',str(base/'native'),
                 '--out-format','pcm16','--metrics']
        m.immutable(base/'native-session.json',{'command':command,'spec':spec,'sequence_sha256':m.file_digest(seq)})
        (base/'native.log').write_bytes(b'Native runner failed before producing the requested output')
        receipt={'quality_passed':False,'generation_error':str(subprocess.CalledProcessError(1,command))}
        self.assertNotIn(spec['runner']['path'],receipt['generation_error'])
        proof=m.validate_context_native_failure(self.folder,unit,1,spec,receipt)
        self.assertEqual(proof['failed_stage'],'native-generation');self.assertEqual(proof['exit_code'],1)
        self.assertTrue(proof['exact_failed_command_verified'])
        for error in ('failed',str(subprocess.CalledProcessError(1,['another-runner.exe'])),
                      str(subprocess.CalledProcessError(0,command))):
            with self.subTest(error=error),self.assertRaisesRegex(ValueError,'explicit source-bound'):
                m.validate_context_native_failure(self.folder,unit,1,spec,{**receipt,'generation_error':error})
        (base/'native.log').unlink()
        with self.assertRaisesRegex(ValueError,'execution log'):
            m.validate_context_native_failure(self.folder,unit,1,spec,receipt)


if __name__=='__main__':unittest.main()
