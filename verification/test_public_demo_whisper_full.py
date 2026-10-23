"""Synthetic-only full Whisper witness integrity and blind decoding checks."""
import copy
from contextlib import nullcontext
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import witness_public_demo_whisper_full as full


class FullWhisperWitness(unittest.TestCase):
    def test_exact_full_model_pin_and_configuration_reject_forgery(self):
        config=full.witness_config('en')
        self.assertTrue(full.validate_verifier_config(config))
        self.assertEqual(config['model'],'openai/whisper-large-v3')
        self.assertEqual(config['file_sizes']['model.safetensors'],3087130976)
        self.assertFalse(config['expected_text_given_to_recognizer'])
        for key,value in [('model','openai/whisper-large-v3-turbo'),('revision','0'*40),
                          ('implementation_sha256','0'*64),('prompt','He enfolded'),
                          ('expected_text_given_to_recognizer',True),('torch','forged-runtime'),('truncation','allowed')]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                full.validate_verifier_config({**config,key:value})

    def test_only_original_quality_takes_selected_no_context_or_pronunciation(self):
        unit={'id':'u1'}
        context=SimpleNamespace(folder=Path('.'),assignment={'units':[unit]},assess=lambda u,r:{'decision':{'passed':False}})
        receipts=[{'take':n,'quality_passed':True,'spec':{}} for n in range(12)]
        receipts[1]['quality_passed']=False;receipts[2]['spec']['context_recovery']={}
        receipts[3]['spec']['pronunciation_recovery']={}
        with patch.object(full.renderer,'takes',return_value=receipts),patch.object(full,'validate_original'):
            self.assertEqual([r['take'] for _,r in full.choose_pending(context,all_takes=True)],[0,4,5])
            self.assertEqual([r['take'] for _,r in full.choose_pending(context)],[5])
        with self.assertRaises(ValueError):full.choose_pending(context,['unknown'])

    def test_model_cache_rechecks_changes_missing_added_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);model=root/'model.safetensors';model.write_bytes(b'abcd')
            files={'model.safetensors':full.file_digest(model)};sizes={'model.safetensors':4}
            full._MODEL_CACHE.clear()
            with patch.object(full,'FILES',files),patch.object(full,'SIZES',sizes),patch.object(full.renderer,'inventory',wraps=full.renderer.inventory) as inventory:
                full.verify_model(root);full.verify_model(root);self.assertEqual(inventory.call_count,1)
                model.write_bytes(b'efgh')
                with self.assertRaisesRegex(ValueError,'checksums'):full.verify_model(root)
                (root/'unexpected.json').write_text('{}')
                with self.assertRaisesRegex(ValueError,'filenames/sizes'):full.verify_model(root)
                (root/'unexpected.json').unlink();model.unlink()
                with self.assertRaisesRegex(ValueError,'filenames/sizes'):full.verify_model(root)

    def test_overlong_audio_is_held_whole_without_model_or_truncation(self):
        import numpy as np
        import soundfile as sf
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);audio=folder/'audio.wav';sf.write(audio,np.zeros(31*24000),24000,subtype='PCM_16')
            checksum=full.file_digest(audio)
            context=SimpleNamespace(folder=folder,generation='fixture',assignment={'language':'English'})
            receipt={'take':0,'audio':'audio.wav','audio_sha256':checksum}
            with patch.object(full,'choose_pending',return_value=[({'id':'u1'},receipt)]):
                pending,plan=full.plan(context)
            self.assertEqual(pending,[]);self.assertFalse(plan['holds'][0]['truncated'])
            self.assertEqual(full.file_digest(audio),checksum);self.assertFalse((folder/'witnesses').exists())

    def test_audio_only_decode_never_receives_expected_text_and_cap_fails(self):
        tensor=SimpleNamespace(is_floating_point=lambda:True,to=lambda *args,**kwargs:'audio-features')
        processor=Mock(return_value={'input_features':tensor});processor.batch_decode.return_value=[' heard words ']
        model=Mock();model.generate.return_value=SimpleNamespace(shape=(1,12))
        torch=SimpleNamespace(float16='fp16',inference_mode=nullcontext)
        self.assertEqual(full.decode_audio(model,processor,[0]*160,torch,{'language':'ru'}),'heard words')
        self.assertEqual(processor.call_args.args,([0]*160,))
        self.assertIs(processor.call_args.kwargs['truncation'],False)
        kwargs=model.generate.call_args.kwargs
        self.assertEqual(set(kwargs),{'input_features','language','task','prompt_ids','do_sample','max_new_tokens'})
        self.assertIsNone(kwargs['prompt_ids']);self.assertEqual(kwargs['language'],'ru')
        model.generate.return_value=SimpleNamespace(shape=(1,256))
        with self.assertRaisesRegex(ValueError,'token cap'):full.decode_audio(model,processor,[0],torch,{'language':'en'})
        with self.assertRaises(ValueError):full.decode_audio(model,processor,[0]*(30*16000+1),torch,{'language':'en'})

    def test_independent_gpu_workers_wait_but_own_chain_does_not(self):
        def proc(pid,parent,command):return {'ProcessId':pid,'ParentProcessId':parent,'CommandLine':command}
        rows=[proc(10,1,'witness_public_demo_whisper_full.py --stage witness'),
              proc(11,10,'witness_public_demo_whisper_full.py --stage witness'),
              proc(20,1,'witness_public_demo_whisper_full.py --stage witness'),
              proc(30,1,'context_public_audiobook_recovery.py --stage verify'),
              proc(40,1,'pronounce_public_audiobook_recovery.py --stage render'),
              proc(50,1,'witness_public_demo_whisper_full.py --stage plan')]
        self.assertEqual([r['ProcessId'] for r in full.competing_extra_jobs(rows,11)],[20,30,40])

    def test_evidence_requires_original_language_registry_and_raw_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);unit={'id':'u1','spoken':'A source phrase.'}
            receipt={'take':0,'spec':{},'audio_sha256':'a'*64}
            context=SimpleNamespace(folder=folder,assignment={'language':'English'})
            config=full.witness_config('en');signature=full.renderer.sha(config)
            target=folder/'full-witness'/('a'*64+'-'+signature+'.json')
            raw=full.transcript_check(unit['spoken'],unit['spoken'],'a'*64,signature)
            full.renderer.write(target,{**raw,'verifier':config},immutable=True)
            full.renderer.write(folder/'verifiers'/(signature+'.json'),config,immutable=True)
            with patch.object(full,'validate_original'):
                result=full.evidence(context,unit,receipt)
                self.assertEqual([(e['role'],e['family']) for e in result],[('witness','whisper')])
                self.assertEqual(result[0]['raw'],raw)
                context.assignment['language']='Russian'
                with self.assertRaisesRegex(ValueError,'language'):full.evidence(context,unit,receipt)
                context.assignment['language']='English'
                full.renderer.write(target,{**raw,'audio_sha256':'b'*64,'verifier':config})
                with self.assertRaisesRegex(ValueError,'cannot be reproduced'):full.evidence(context,unit,receipt)
                self.assertEqual(full.evidence(context,unit,{**receipt,'take':9}),[])
                self.assertEqual(full.evidence(context,unit,{**receipt,'spec':{'context_recovery':{}}}),[])

    def test_original_receipt_binding_audio_and_quality_are_reproduced(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);audio=folder/'audio.wav';audio.write_bytes(b'synthetic audio')
            unit={'id':'u1','spoken':'A source phrase.','palette_reference':{'fixture':'ref'}}
            context=SimpleNamespace(folder=folder,generation='fixture',assignment={'units':[unit]},
                                    manifest={'speech_model':{'fixture':'model'},'runner':{'fixture':'runner'}},recheck=Mock())
            spec={'source':unit,'generation':'fixture','request':{'text':unit['spoken'],'max_tokens':123},'effects':'disabled',
                  'model':context.manifest['speech_model'],'runner':context.manifest['runner'],'reference':unit['palette_reference']}
            receipt={'unit':'u1','take':0,'quality_passed':True,'spec':spec,'spec_sha256':full.renderer.sha(spec),
                     'audio':'audio.wav','audio_sha256':full.file_digest(audio),'quality':{'duration':1}}
            path=folder/'takes/u1/take-00.json';full.renderer.write(path,receipt)
            with patch.object(full.renderer,'quality',return_value={'duration':1}):
                self.assertEqual(full.validate_original(context,unit,receipt),audio.resolve())
                changed=copy.deepcopy(receipt);changed['spec']['request']['text']='A changed phrase.'
                changed['spec_sha256']=full.renderer.sha(changed['spec']);full.renderer.write(path,changed)
                with self.assertRaisesRegex(ValueError,'binding'):full.validate_original(context,unit,changed)
                full.renderer.write(path,receipt);audio.write_bytes(b'tampered')
                with self.assertRaises(ValueError):full.validate_original(context,unit,receipt)


if __name__=='__main__':unittest.main()
