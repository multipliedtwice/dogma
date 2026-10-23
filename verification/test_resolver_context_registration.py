"""Synthetic CPU adapter tests; context renderer/recognizers are never launched."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'),str(ROOT/'runtime')]
import resolve_public_demo_recognition as resolver
import context_public_audiobook_recovery as context_module


class ContextRegistration(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        unit = {'id':'u1','source_id':'p1','start':0,'end':11,'text':'Target gift.',
                'spoken':'Target gift.','palette_reference':{'path':'PRIVATE_TRAINING_PATH'}}
        ctx = resolver.Context.__new__(resolver.Context)
        ctx.folder,ctx.generation = folder,'frozen-fixture-generation'
        ctx.assignment = {'language':'English','units':[unit]}
        ctx.blocks,ctx.dependencies,ctx.bindings,ctx.english_policy = {'p1':unit['text']},{},{},None
        ctx.recheck = Mock()
        ctx.evidence = Mock(side_effect=AssertionError('Context ASR must use its strict adapter'))
        crop = folder/'assets/crop.wav'
        crop.parent.mkdir()
        crop.write_bytes(b'synthetic immutable crop fixture')
        crop_hash = resolver.file_digest(crop)
        native_hash = 'a'*64
        full_spoken = 'Before. Target gift. After.'

        def decision(kind,expected,audio_hash):
            evidence = []
            base = context_module.attempt_home(folder,unit,1)
            for family,role,model in [('qwen','primary','Qwen3-ASR-1.7B'),('whisper','witness','openai/whisper-large-v3-turbo')]:
                config = {'model':model,'expected_text_given_to_recognizer':False,'fixture':kind}
                raw = resolver.transcript_check(expected,expected,audio_hash,resolver.sha(config))
                path = base/(kind+'-'+family+'.json')
                resolver.immutable(path,{**raw,'verifier':config})
                evidence.append({'path':path.relative_to(folder).as_posix(),'sha256':resolver.file_digest(path),
                                 'raw':raw,'verifier':config,'family':family,'role':role})
            return resolver.resolve_transcripts({'spoken':expected},'English',expected,evidence)

        full = decision('context',full_spoken,native_hash)
        target = decision('crop',unit['spoken'],crop_hash)
        bounds = {'start_sample':100,'end_sample':200,'sample_rate':24000}
        spec = {'source':unit,'generation':ctx.generation,'request':{'text':'Before. Target gift. After.'},
                'context_recovery':{'policy':context_module.POLICY,'attempt':1,'crop':bounds,
                                    'context':{'spoken':full_spoken}}}
        receipt = {'schema':1,'unit':'u1','take':6,'spec':spec,'spec_sha256':resolver.sha(spec),
                   'audio':'assets/crop.wav','audio_sha256':crop_hash,'quality_passed':True,
                   'canonical_registration':False}
        namespace = context_module.attempt_home(folder,unit,1)/'crop-receipt.json'
        resolver.immutable(namespace,receipt)
        proof = {'policy':context_module.POLICY,'receipt_sha256':resolver.file_digest(namespace),
                 'full_context_decision':full,'target_decision':target,'native_audio_sha256':native_hash,
                 'crop_audio_sha256':crop_hash,'crop':bounds,
                 'source_spans':[{'id':'u1','source_id':'p1','start':0,'end':11,'text':unit['text'],
                                  'spoken':unit['spoken'],'palette_reference':unit['palette_reference']}]}
        return ctx,unit,receipt,namespace,proof

    def test_registration_appends_byte_exact_mirror_and_preserves_original_six(self):
        ctx,unit,receipt,namespace,proof = self.fixture()
        original = {}
        for take in range(6):
            path = ctx.folder/'takes/u1'/('take-%02d.json'%take)
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(('protected original fixture '+str(take)).encode())
            original[path] = path.read_bytes()
        with patch.object(context_module,'validate_context_receipt',return_value=proof):
            registration = resolver.register_context(ctx,namespace)
            canonical = ctx.folder/registration['canonical']
            self.assertEqual(canonical.read_bytes(),namespace.read_bytes())
            self.assertFalse(resolver.read(canonical)['canonical_registration'])
            assessed = ctx.assess(unit,receipt)
        ctx.evidence.assert_not_called()
        self.assertEqual(assessed['decision'],proof['target_decision'])
        self.assertNotIn('PRIVATE_TRAINING_PATH',json.dumps(assessed['context_provenance']))
        self.assertEqual(assessed['context_provenance']['full_context_decision'],proof['full_context_decision'])
        for path,raw in original.items():
            self.assertEqual(path.read_bytes(),raw)

    def test_forged_spec_crop_hash_and_raw_evidence_are_rejected(self):
        ctx,unit,receipt,namespace,proof = self.fixture()
        bad = copy.deepcopy(receipt)
        bad['spec']['source']['text'] = 'A different target.'
        with patch.object(context_module,'validate_context_receipt',return_value=proof) as validator:
            with self.assertRaisesRegex(ValueError,'frozen source'):
                resolver.validated_context(ctx,unit,bad)
            validator.assert_not_called()
        variants = []
        changed = copy.deepcopy(proof);changed['crop']['end_sample'] += 1;variants.append(changed)
        changed = copy.deepcopy(proof);changed['crop_audio_sha256'] = '0'*64;variants.append(changed)
        changed = copy.deepcopy(proof);changed['target_decision']['evidence'][0]['raw']['audio_sha256'] = '0'*64;variants.append(changed)
        for invalid in variants:
            with patch.object(context_module,'validate_context_receipt',return_value=invalid):
                with self.assertRaises(ValueError):
                    resolver.validated_context(ctx,unit,receipt,resolver.file_digest(namespace))

    def test_full_or_crop_hold_prevents_registration(self):
        ctx,unit,receipt,namespace,proof = self.fixture()
        for key in ['full_context_decision','target_decision']:
            invalid = copy.deepcopy(proof)
            invalid[key]['passed'] = False
            with patch.object(context_module,'validate_context_receipt',return_value=invalid):
                with self.assertRaisesRegex(ValueError,'remains held'):
                    resolver.register_context(ctx,namespace)
            self.assertFalse((ctx.folder/'takes/u1/take-06.json').exists())

    def test_canonical_existing_different_bytes_never_overwritten(self):
        ctx,unit,receipt,namespace,proof = self.fixture()
        canonical = ctx.folder/'takes/u1/take-06.json'
        canonical.parent.mkdir(parents=True)
        canonical.write_text(json.dumps(receipt,separators=(',',':')),encoding='utf-8')
        before = canonical.read_bytes()
        with patch.object(context_module,'validate_context_receipt',return_value=proof):
            with self.assertRaisesRegex(ValueError,'cannot overwrite'):
                resolver.register_context(ctx,namespace)
            with self.assertRaisesRegex(ValueError,'canonical mirror'):
                ctx.assess(unit,receipt)
        self.assertEqual(canonical.read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
