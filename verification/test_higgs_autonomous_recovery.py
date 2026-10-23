import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from render_higgs_palette_preview import Calls,obj,string
from rimsky.util import ProviderTransportError,file_digest,write_json
from rimsky.spoken_edits import apply_spoken_edit
from adjudicate_higgs_word_flags import validate,schema
import supervise_higgs_preview as supervisor
from higgs_opus_word_recovery import prior_verified_takes
from rimsky.speech_quality import transcript_check

class AutonomousRecoveryTests(unittest.TestCase):
    def test_cpu_word_audit_reuses_exact_bound_audio_after_incomplete_gpu_check(self):
        from regenerate_higgs_book_trial import cached_audit
        from rimsky.util import digest
        with tempfile.TemporaryDirectory() as work:
            out=Path(work);wav=out/'take.wav';wav.write_bytes(b'pinned audio')
            sha=file_digest(wav);write_json(out/'render-plan.json',{'units':[{'id':'one','spoken':'one two','key':'key'}]})
            binding=file_digest(out/'render-plan.json')
            write_json(out/'receipts.json',{'plan_sha256':binding,'takes':{'one':{'audio':'take.wav','audio_sha256':sha,'key':'key'}}})
            cuda={'device':'cuda:0','weights':'pinned'};cpu={'device':'cpu','weights':'pinned'}
            write_json(out/'word-checks-1.7B.json',{'plan_sha256':binding,'verifier':cuda,'checks':{}})
            check={**transcript_check('one two','one two',sha,digest(cpu)),'synthesis_key':'key'}
            report={'plan_sha256':binding,'verifier':cpu,'checks':{'one':check}}
            write_json(out/'word-checks-1.7B-cpu.json',report)
            with patch('regenerate_higgs_book_trial.audit_configuration',side_effect=lambda device:cpu if device=='cpu' else cuda), \
                 patch('regenerate_higgs_book_trial.normalize',side_effect=lambda folder,path:__import__('json').loads(path.read_text('utf8'))):
                self.assertEqual(report,cached_audit(out))
                wav.write_bytes(b'changed audio')
                with self.assertRaisesRegex(ValueError,'no longer matches take'):cached_audit(out)

    def test_rate_limit_fallback_preserves_request_and_binds_actual_provider(self):
        with tempfile.TemporaryDirectory() as work:
            folder=Path(work);calls=Calls.__new__(Calls);calls.provider='codex_cli';calls.version='codex-test'
            contract=obj({'value':string()});answer={'value':'valid'}
            with patch('rimsky.codex_analysis.structured_call',side_effect=ProviderTransportError('429 Too Many Requests')) as primary, \
                 patch('rimsky.claude_analysis.structured_call',return_value=answer) as alternate, \
                 patch('rimsky.claude_analysis.claude_executable',return_value='claude.exe'), \
                 patch('rimsky.claude_analysis._version',return_value='2.1.286'):
                self.assertEqual(calls.call(folder,'unit','system',{'book':'data'},contract,lambda a:None),answer)
                sha=file_digest(folder/'unit.request.json')
                record=json.loads((folder/'unit.answer.json').read_text('utf-8'))
                self.assertEqual(record['request_sha256'],sha)
                self.assertEqual(record['actual_provider'],'claude_cli')
                self.assertEqual(alternate.call_args.kwargs['model'],'claude-opus-5-5')
                self.assertEqual(json.loads((folder/'unit.request.json').read_text('utf-8'))['provider'],'codex_cli')
                calls.call(folder,'unit','system',{'book':'data'},contract,lambda a:None)
                self.assertEqual(primary.call_count,1);self.assertEqual(alternate.call_count,1)
                self.assertEqual(file_digest(folder/'unit.request.json'),sha)

    def test_opus_candidates_cannot_change_words_or_yo(self):
        text='Это всё, и потом.'
        plan={'expected_spoken':text,'native_text':'Это всё и по́том.','recovery_kind':'opus_word_recovery',
              'evidence':{'diagnosis_sha256':'a'*64}}
        self.assertEqual(apply_spoken_edit(text,plan)[1],'Это всё и потом.')
        for wrong in ['Это все и потом.','Это всё потом.','Это всё, и тогда.','Это всё и п́отом.']:
            with self.assertRaises(ValueError):apply_spoken_edit(text,{**plan,'native_text':wrong})
        with self.assertRaises(ValueError):apply_spoken_edit(text,{**plan,'evidence':{}})
        already_marked='По\u0301том, ещё раз.'
        marked_plan={**plan,'expected_spoken':already_marked,'native_text':'По\u0301том — ещё раз.'}
        self.assertEqual(apply_spoken_edit(already_marked,marked_plan)[0],marked_plan['native_text'])
        with self.assertRaises(ValueError):apply_spoken_edit(already_marked,{**marked_plan,'native_text':'Потом — раз.'})

    def test_text_diagnosis_requires_every_unit_and_exact_source_evidence(self):
        request={'units':[{'unit_id':'one','spoken':'Одна фраза.','paragraph':'Одна фраза.'}]}
        row={'unit_id':'one','classification':'uncertain','reason':'r','source_evidence':'Одна фраза.',
             'independent_check':'audio-only recognition','native_candidates':['Одна фра́за.']}
        validate({'diagnoses':[row]},request,schema(['one']))
        for invalid in [{'diagnoses':[]},{'diagnoses':[{**row,'source_evidence':'another paragraph'}]},
                        {'diagnoses':[{**row,'native_candidates':['Другая фраза.']}]}]:
            with self.assertRaises(ValueError):validate(invalid,request,schema(['one']))
        literal=schema(['one'],['Одна фраза.'])
        with self.assertRaises(ValueError):validate({'diagnoses':[{**row,'source_evidence':'Source says: Одна фраза.'}]},request,literal)

    def test_production_word_proposals_preserve_yo_and_expressive_punctuation(self):
        request={'citation_contract':'source-slice-v3','units':[{'unit_id':'one','spoken':'Это всё?',
            'source_quote':'Это всё?','paragraph':'Это всё?'}]}
        row={'unit_id':'one','classification':'uncertain','reason':'r','source_evidence':'Это всё?',
            'independent_check':'audio-only recognition','native_candidates':['Это всё?']}
        validate({'diagnoses':[row]},request,schema(['one'],['Это всё?']))
        for native in ['Это все?','Это всё!','Это всё','Это всё\u0301\u0301?']:
            with self.assertRaises(ValueError):validate({'diagnoses':[{**row,'native_candidates':[native]}]},request,schema(['one'],['Это всё?']))

    def test_resume_reuses_existing_verified_repair_before_calling_a_model(self):
        import soundfile as sf
        import numpy as np
        with tempfile.TemporaryDirectory() as work:
            out=Path(work);take=out/'word-omission-recovery/take-3';take.mkdir(parents=True)
            unit={'id':'line','text':'one two','spoken':'one two','voice':'male','mood':'calm','speaker':'actor',
                  'palette_reference':{'audio_sha256':'allowed'},'palette_assignment':{'allowed_reference_sha256':['allowed']}}
            assignment={'source_sha256':'source','bank_sha256':'bank','effects_policy':'disabled','units':[unit]}
            write_json(take/'assignment.json',assignment);write_json(take/'render-plan.json',{'units':[unit]})
            wav=take/'line.wav';sf.write(wav,np.concatenate([np.zeros(1000),np.full(1000,.03),np.zeros(1000)]),24000)
            sha=file_digest(wav);receipt={'audio':'line.wav','audio_sha256':sha,'key':'correct'}
            write_json(take/'receipts.json',{'takes':{'line':receipt}})
            check={**transcript_check('one two','one two',sha,'audio-only'),'synthesis_key':'correct'}
            selected={'line':{'word_check':{'status':'spoken_text_mismatch'}}}
            with patch('render_higgs_palette_preview.cached_audit',return_value={'checks':{'line':check}}):
                recovered=prior_verified_takes(out,assignment,selected)
            self.assertEqual(recovered['line']['word_check']['status'],'asr_exact_match')
            self.assertEqual(recovered['line']['path'],str(wav))
            self.assertEqual(recovered['line']['unit']['voice'],'male')

    def test_supervisor_restarts_failed_job_and_records_completion(self):
        with tempfile.TemporaryDirectory() as work:
            root=Path(work);out=root/'generation';out.mkdir();page=root/'page';page.mkdir()
            state={'generation':'generation','chapters':{},'status':'failed','error':'transport timeout'}
            write_json(out/'progress.json',state);write_json(page/'progress.json',state)
            commands=[]
            def start(command,**kwargs):
                commands.append(command)
                next_state={**state,'status':'failed' if len(commands)==1 else 'complete'}
                write_json(out/'progress.json',next_state)
                return SimpleNamespace(pid=100+len(commands),poll=lambda:0)
            argv=['supervise','--folder',str(root),'--bank',str(root/'bank'),
                  '--output-name','generation','--voice-descriptors',str(root/'descriptors'),
                  '--spoken-corrections',str(root/'edits')]
            with patch.object(sys,'argv',argv),patch.object(supervisor,'PAGE',page), \
                 patch.object(supervisor,'page'),patch.object(supervisor.subprocess,'Popen',side_effect=start), \
                 patch.object(supervisor.time,'sleep'):
                supervisor.main()
            report=json.loads((out/'supervision.json').read_text('utf-8'))
            self.assertEqual(len(commands),2)
            self.assertTrue(all('--heal-word-flags' in command for command in commands))
            self.assertEqual(report['status'],'completed');self.assertFalse(report['worker_alive'])
            self.assertEqual([e['event'] for e in report['journal']],['started','exited','started','exited'])

    def test_second_checkpoint_reload_preserves_previous_receipt_and_resumes(self):
        with tempfile.TemporaryDirectory() as work:
            root=Path(work);out=root/'generation';out.mkdir();page=root/'page';page.mkdir()
            state={'generation':'generation','chapters':{},'status':'rendering'}
            write_json(out/'progress.json',state);write_json(page/'progress.json',state)
            old={'generation':'generation','worker_pid':99,'reason':'old'}
            new={'generation':'generation','worker_pid':101,'reason':'new'}
            write_json(out/'reload.applied.json',old);write_json(out/'reload.request.json',new)
            commands=[]
            def start(command,**kwargs):
                commands.append(command)
                if len(commands)==2:write_json(out/'progress.json',{**state,'status':'complete'})
                return SimpleNamespace(pid=100+len(commands),poll=lambda:None if len(commands)==1 else 0)
            argv=['supervise','--folder',str(root),'--bank',str(root/'bank'),'--output-name','generation',
                '--voice-descriptors',str(root/'descriptors'),'--spoken-corrections',str(root/'edits'),'--max-restarts','0']
            with patch.object(sys,'argv',argv),patch.object(supervisor,'PAGE',page),patch.object(supervisor,'page'), \
                 patch.object(supervisor.subprocess,'Popen',side_effect=start),patch.object(supervisor,'audio_busy',return_value=False), \
                 patch.object(supervisor,'terminate_process_tree') as terminate,patch.object(supervisor.time,'sleep'):
                supervisor.main()
            self.assertEqual(len(commands),2);terminate.assert_called_once()
            self.assertEqual(json.loads((out/'reload.applied.json').read_text('utf-8')),new)
            self.assertEqual(len(list((out/'reload-history').glob('*.json'))),2)
            report=json.loads((out/'supervision.json').read_text('utf-8'))
            self.assertEqual(report['restarts'],0);self.assertEqual(report['status'],'completed')

if __name__=='__main__':unittest.main()
