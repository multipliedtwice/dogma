"""Diagnostic never supplies source text or converts quality holds to approval."""
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
import numpy as np
import soundfile as sf
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import diagnose_public_demo_raw_audio as m
class Diagnostic(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.audio=self.root/'raw.wav';sf.write(self.audio,np.zeros(2400,dtype=np.int16),24000,subtype='PCM_16')
        self.receipt=self.root/'receipt.json';self.campaign=self.root/'campaign.json'
        self.r={'audio_sha256':m.file_digest(self.audio),'quality_passed':False,'audio_approval':False,'quality_error':'Active END edge','spec':{'source':'SECRET EXPECTED WORDS'}}
        self.c={'policy':m.frozen.POLICY,'language':'Russian','dependencies':{},'text':'SECRET EXPECTED WORDS'}
        self.save()
    def save(self):
        import json
        self.receipt.write_text(json.dumps(self.r),encoding='utf-8');self.campaign.write_text(json.dumps(self.c),encoding='utf-8')
    def run_prepare(self):
        with patch.object(m.frozen,'dependencies',return_value={}),patch.object(m.frozen,'verifier_config',return_value={'policy':'decoder','model':'fixture','decoder_language':'Russian'}):
            return m.prepare(self.audio,self.receipt,self.campaign,'qwen')[0]
    def test_request_has_no_source_or_expected_words_and_keeps_hold(self):
        old=self.receipt.read_bytes();request=self.run_prepare()
        self.assertNotIn('SECRET',str(request));self.assertFalse(request['acceptance']);self.assertFalse(request['native_quality_passed'])
        self.assertFalse(request['expected_text_given_to_recognizer']);self.assertEqual(self.receipt.read_bytes(),old)
        self.assertEqual(request['frames'],2400)
    def test_changed_audio_or_positive_quality_refused(self):
        self.r['audio_sha256']='0'*64;self.save()
        with self.assertRaises(ValueError):self.run_prepare()
        self.r.update(audio_sha256=m.file_digest(self.audio),quality_passed=True);self.save()
        with self.assertRaises(ValueError):self.run_prepare()
    def test_changed_frozen_decoder_pin_refused(self):
        self.c['dependencies']={'different':'code'};self.save()
        with self.assertRaises(ValueError):self.run_prepare()
if __name__=='__main__':unittest.main()
