"""Synthetic-only integrity/source-blind CPU CTC witness checks."""
import copy
import os
from contextlib import nullcontext
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import witness_public_demo_wav2vec2 as ctc


class LexicalCtcWitness(unittest.TestCase):
    def test_exact_sourceblind_cpu_model_and_runtime_pin(self):
        config=ctc.witness_config('en');self.assertTrue(ctc.validate_verifier_config(config))
        self.assertEqual(config['model'],'facebook/wav2vec2-base-960h');self.assertEqual(config['device'],'cpu')
        self.assertEqual(config['dtype'],'float32');self.assertIsNone(config['language_model'])
        for key,value in [('model','other-model'),('revision','0'*40),('implementation_sha256','0'*64),
                          ('prompt','Expected source'),('expected_text_given_to_recognizer',True),
                          ('expected_text_given_to_recognizer',0),('device','cuda:0'),('decoder','beam-plus-LM'),
                          ('torch','forged-runtime'),('truncation','allowed')]:
            with self.subTest(key=key),self.assertRaises(ValueError):ctc.validate_verifier_config({**config,key:value})
        with self.assertRaises(ValueError):ctc.witness_config('ru')

    def test_model_inventory_checksum_cache_invalidates_changed_missing_extra(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);weight=root/'model.safetensors';weight.write_bytes(b'abcd')
            files={'model.safetensors':ctc.file_digest(weight)};sizes={'model.safetensors':4};ctc._MODEL_CACHE.clear()
            with patch.object(ctc,'FILES',files),patch.object(ctc,'SIZES',sizes),patch.object(ctc.renderer,'inventory',wraps=ctc.renderer.inventory) as inventory:
                ctc.verify_model(root);ctc.verify_model(root);self.assertEqual(inventory.call_count,1)
                before=weight.stat();weight.write_bytes(b'efgh')
                os.utime(weight,ns=(before.st_atime_ns,before.st_mtime_ns+2_000_000_000))
                with self.assertRaisesRegex(ValueError,'checksums'):ctc.verify_model(root)
                (root/'extra.json').write_text('{}')
                with self.assertRaisesRegex(ValueError,'filenames/sizes'):ctc.verify_model(root)
                (root/'extra.json').unlink();weight.unlink()
                with self.assertRaisesRegex(ValueError,'filenames/sizes'):ctc.verify_model(root)

    def test_only_original_qualitypassed_source_takes_are_eligible(self):
        unit={'id':'u1'};context=SimpleNamespace(folder=Path('.'),assignment={'units':[unit]},
                                               assess=lambda u,r:{'decision':{'passed':False}})
        receipts=[{'take':n,'quality_passed':True,'spec':{}} for n in range(12)]
        receipts[1]['quality_passed']=False;receipts[2]['spec']['context_recovery']={}
        receipts[3]['spec']['pronunciation_recovery']={}
        with patch.object(ctc.renderer,'takes',return_value=receipts),patch.object(ctc,'validate_original'):
            self.assertEqual([r['take'] for _,r in ctc.choose_pending(context,all_takes=True)],[0,4,5])
            self.assertEqual([r['take'] for _,r in ctc.choose_pending(context)],[5])
        with self.assertRaises(ValueError):ctc.choose_pending(context,['unknown'])

    def test_model_boundary_has_audio_argmax_only_no_expected_text_or_language_model(self):
        import numpy as np
        inputs={'input_values':'audio-only'};processor=Mock(return_value=inputs);processor.batch_decode.return_value=['HE ENFOLDED HIS DELA']
        logits=object();ids=object();model=Mock(return_value=SimpleNamespace(logits=logits))
        torch=SimpleNamespace(inference_mode=nullcontext,isfinite=lambda x:SimpleNamespace(all=lambda:True),argmax=Mock(return_value=ids))
        heard=ctc.decode_audio(np.zeros(160),processor,model,torch)
        self.assertEqual(heard,'HE ENFOLDED HIS DELA')
        self.assertEqual(set(model.call_args.kwargs),{'input_values'})
        self.assertEqual(processor.call_args.kwargs,{'sampling_rate':16000,'return_tensors':'pt','padding':False})
        torch.argmax.assert_called_once_with(logits,dim=-1);processor.batch_decode.assert_called_once_with(ids)
        with self.assertRaises(ValueError):ctc.decode_audio(np.zeros(30*16000+1),processor,model,torch)
        with self.assertRaises(ValueError):ctc.decode_audio(np.array([float('nan')]),processor,model,torch)
        processor.return_value={'input_values':'audio','text':'not permitted'}
        with self.assertRaisesRegex(ValueError,'non-audio'):ctc.decode_audio(np.zeros(160),processor,model,torch)

    def test_overlong_plan_holds_whole_audio_without_creating_witness(self):
        import numpy as np
        import soundfile as sf
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);audio=folder/'audio.wav';sf.write(audio,np.zeros(31*24000),24000,subtype='PCM_16')
            checksum=ctc.file_digest(audio);receipt={'take':0,'audio':'audio.wav','audio_sha256':checksum}
            context=SimpleNamespace(folder=folder,generation='fixture',assignment={'language':'English'})
            with patch.object(ctc,'choose_pending',return_value=[({'id':'u1'},receipt)]):pending,plan=ctc.plan(context)
            self.assertEqual(pending,[]);self.assertFalse(plan['holds'][0]['truncated'])
            self.assertEqual(ctc.file_digest(audio),checksum);self.assertFalse(list((folder/'ctc-witness').glob('*.json')))

    def test_raw_evidence_language_registry_hash_and_model_config_are_rechecked(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);unit={'id':'u1','spoken':'A source phrase.'}
            receipt={'take':0,'quality_passed':True,'spec':{},'audio_sha256':'a'*64}
            context=SimpleNamespace(folder=folder,assignment={'language':'English'})
            config=ctc.witness_config('en');signature=ctc.renderer.sha(config)
            target=folder/'ctc-witness'/('a'*64+'-'+signature+'.json')
            raw=ctc.transcript_check(unit['spoken'],'A source phrase.','a'*64,signature)
            ctc.renderer.write(target,{**raw,'verifier':config},immutable=True)
            ctc.renderer.write(folder/'verifiers'/(signature+'.json'),config,immutable=True)
            with patch.object(ctc,'validate_original'):
                found=ctc.evidence(context,unit,receipt)
                self.assertEqual([(e['role'],e['family']) for e in found],[('witness','wav2vec2')])
                self.assertEqual(found[0]['raw'],raw)
                context.assignment['language']='Russian'
                with self.assertRaises(ValueError):ctc.evidence(context,unit,receipt)
                context.assignment['language']='English'
                ctc.renderer.write(target,{**raw,'heard':'Changed phrase','verifier':config})
                with self.assertRaisesRegex(ValueError,'binding differs'):ctc.evidence(context,unit,receipt)
                self.assertEqual(ctc.evidence(context,unit,{**receipt,'take':9}),[])
                self.assertEqual(ctc.evidence(context,unit,{**receipt,'spec':{'pronunciation_recovery':{}}}),[])

    def test_diagnostic_and_other_family_receipts_remain_separate_and_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);diagnostic=folder/'diagnostics';diagnostic.mkdir()
            old=diagnostic/'take-00.json';old.write_bytes(b'old experimental raw data')
            unit={'id':'u1','spoken':'A source phrase.'};receipt={'take':0,'quality_passed':True,'spec':{},'audio_sha256':'a'*64}
            context=SimpleNamespace(folder=folder,assignment={'language':'English'})
            with patch.object(ctc,'validate_original'):
                self.assertEqual(ctc.evidence(context,unit,receipt),[])
            self.assertEqual(old.read_bytes(),b'old experimental raw data')
            target=folder/'ctc-witness'/'synthetic.json';ctc.renderer.write(target,{'heard':'raw original'},immutable=True)
            with self.assertRaises(ValueError):ctc.renderer.write(target,{'heard':'edited'},immutable=True)


if __name__=='__main__':unittest.main()
