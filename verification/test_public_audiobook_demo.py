"""Synthetic-only checks for the standalone demo's critical preservation gates."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('demo', ROOT / 'scripts/render_public_audiobook_demo.py')
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class DemoGates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.unit = dict(id='s0001', source_id='p1', text='The gift.', spoken='The gift.',
                         palette_reference=dict(reviewed=True, text='Synthetic reference.'))
        self.assignment = dict(language='English', units=[self.unit])

    def receipt(self):
        path = self.folder / 'assets/raw.wav'
        path.parent.mkdir()
        path.write_bytes(b'synthetic fixture; never inference input')
        receipt = dict(quality_passed=True, audio='assets/raw.wav', audio_sha256=demo.file_digest(path), take=0)
        demo.write(self.folder / 'takes/s0001/take-00.json', receipt)
        return receipt

    def check(self, receipt, heard, witness=False):
        config = {'model': 'synthetic-witness' if witness else 'synthetic-primary'}
        value = demo.transcript_check(self.unit['spoken'], heard, receipt['audio_sha256'], demo.sha(config))
        value['verifier'] = config
        path = self.folder / ('witnesses' if witness else 'checks') / (receipt['audio_sha256'] + ('-witness' if witness else '') + '.json')
        demo.write(path, value)
        return path

    def test_frozen_assignment_detects_changes(self):
        demo.write(self.folder / 'assignment.json', self.assignment)
        demo.write(self.folder / 'manifest.json', {})
        demo.load(self.folder)
        self.unit['spoken'] = 'A different sentence.'
        demo.write(self.folder / 'assignment.json', self.assignment)
        with self.assertRaisesRegex(ValueError, 'Immutable'):
            demo.load(self.folder)

    def test_native_sound_effect_tags_are_disabled(self):
        for tag in ('<|emotion:happy|>', '<|prosody:whisper|>', '<|style:angry|>',
                    '<|effect:laugh|>', '[speed=2]', '[vocal_effect:growl]'):
            with self.subTest(tag=tag):
                self.unit['synthesis_text'] = tag + ' The gift.'
                demo.write(self.folder / 'assignment.json', self.assignment)
                demo.write(self.folder / 'manifest.json', {})
                with self.assertRaisesRegex(ValueError, 'Disabled'):
                    demo.load(self.folder)

    def test_modified_opening_manifest_cannot_approve_itself(self):
        from rimsky.util import AppError
        original = demo.read(ROOT / 'resources/branding/dogma-opening-v1/manifest.json')
        original['approval']['basis'] = 'Unapproved replacement'
        path = self.folder / 'resources/branding/dogma-opening-v1/manifest.json'
        demo.write(path, original)
        manifest = {'opening': {'language': 'English', 'manifest_sha256': demo.file_digest(path),
                               'sha256': original['languages']['en']['variants']['music']['audio_hash']}}
        with patch.object(demo, 'ROOT', self.folder):
            with self.assertRaisesRegex(AppError, 'checksum mismatch'):
                demo.opening(self.assignment, manifest)

    def test_resource_guard_ignores_ancestors_but_waits_for_independent_asr(self):
        processes = [dict(ProcessId=10, ParentProcessId=5, Name='python.exe',
                          CommandLine='python render_public_audiobook_demo.py --stage verify'),
                     dict(ProcessId=5, ParentProcessId=1, Name='python.exe',
                          CommandLine='python render_public_audiobook_demo.py --stage all'),
                     dict(ProcessId=20, ParentProcessId=1, Name='python.exe',
                          CommandLine='python scripts/audit_higgs_secondary_asr.py'),
                     dict(ProcessId=30, ParentProcessId=1, Name='python.exe',
                          CommandLine='python scripts/process_listener_stress_queue.py --watch'),
                     dict(ProcessId=40, ParentProcessId=30, Name='python.exe',
                          CommandLine='python scripts/revise_higgs_spoken_take.py'),
                     dict(ProcessId=50, ParentProcessId=1, Name='python.exe',
                          CommandLine='python prepare_public_demo_music.py --stage prepare')]
        busy = demo.competing_audio_jobs(processes, 10)
        self.assertEqual([p['ProcessId'] for p in busy], [20, 40])

    def test_player_bookmark_maps_units_and_resets_replaced_audio(self):
        import shutil
        if not shutil.which('node'):
            self.skipTest('Node unavailable')
        program = r'''
const assert = require('node:assert/strict');
const sandbox = {module:{exports:{}}};
require('node:vm').runInNewContext(require('node:fs').readFileSync(process.argv[1],'utf8'),sandbox);
const {capturePosition, resumePosition} = sandbox.module.exports;
const old = [{id:'one',start:8,end:13,raw_audio_sha256:'first'},
             {id:'two',start:13,end:23,raw_audio_sha256:'second'}];
const saved = capturePosition(17, old, 'v1');
assert.deepEqual(JSON.parse(JSON.stringify(saved)), {unit_id:'two',offset:4,raw_audio_sha256:'second',version:'v1'});
const shifted = [{id:'one',start:8,end:18,raw_audio_sha256:'retake-first'},
                 {id:'two',start:18,end:28,raw_audio_sha256:'second'}];
assert.equal(resumePosition(saved, shifted),22); // preceding duration changed
shifted[1].raw_audio_sha256='retake-second';
assert.equal(resumePosition(saved, shifted),18); // selected unit replaced: reset
assert.equal(resumePosition(17, shifted),0); // old absolute-time bookmarks unsupported
assert.equal(resumePosition({...saved,offset:-1}, shifted),0);
assert.equal(resumePosition({...saved,unit_id:'missing'}, shifted),0);
'''
        subprocess.run(['node', '-e', program, str(ROOT / 'scripts/public_demo_player.js')], check=True)

    def test_disagreement_stays_held_until_exact_independent_witness(self):
        receipt = self.receipt()
        primary = self.check(receipt, 'A gift.')
        before = primary.read_bytes()
        self.assertIsNone(demo.selected(self.folder, self.unit))
        state = demo.checkpoint(self.folder, self.assignment, 'fixture', 1)
        self.assertEqual(state['units'][0]['status'], 'held')
        self.check(receipt, 'The gift.', witness=True)
        chosen, decision = demo.selected(self.folder, self.unit)
        self.assertEqual(chosen['audio_sha256'], receipt['audio_sha256'])
        self.assertEqual(decision['resolution'], 'independent_witness_exact')
        self.assertEqual(primary.read_bytes(), before)

    def test_evidence_cannot_be_reused_after_audio_change(self):
        receipt = self.receipt()
        self.check(receipt, 'The gift.')
        (self.folder / receipt['audio']).write_bytes(b'changed raw audio')
        with self.assertRaisesRegex(ValueError, 'changed pinned'):
            demo.selected(self.folder, self.unit)

    def test_fabricated_primary_verdict_rejected(self):
        receipt = self.receipt()
        path = self.check(receipt, 'Wrong extra words.')
        record = demo.read(path)
        record['status'] = 'asr_exact_match'
        demo.write(path, record)
        with self.assertRaisesRegex(ValueError, 'not bound'):
            demo.audit(self.folder, self.unit, receipt)

    def test_unverified_take_is_rendered_not_retry_pending(self):
        self.receipt()
        state = demo.checkpoint(self.folder, self.assignment, 'fixture', 3)
        self.assertEqual(state['units'][0]['status'], 'rendered')

    def test_quality_rejects_active_edge_and_token_cap(self):
        import numpy as np
        import soundfile as sf
        path = self.folder / 'fixture.wav'
        x = np.zeros(24000, dtype=np.float32)
        x[2400:21600] = .05*np.sin(np.arange(19200)*.1)
        sf.write(path, x, 24000)
        self.assertEqual(demo.quality(path, 256)['sample_rate'], 24000)
        with self.assertRaisesRegex(ValueError, 'token cap'):
            demo.quality(path, 30)
        x[:] = .1
        sf.write(path, x, 24000)
        with self.assertRaisesRegex(ValueError, 'Active audio edge'):
            demo.quality(path, 256)

    def test_short_complete_speech_publishes_playable_preview_without_claiming_music_complete(self):
        import numpy as np
        import soundfile as sf
        import shutil
        if not shutil.which('ffmpeg'):
            self.skipTest('FFmpeg unavailable')
        path = self.folder / 'assets/fixture.wav'
        path.parent.mkdir()
        audio = np.zeros(24000, np.float32)
        audio[2400:21600] = .03*np.sin(np.arange(19200)*.1)
        sf.write(path, audio, 24000)
        receipt = dict(quality_passed=True, audio='assets/fixture.wav', audio_sha256=demo.file_digest(path), take=0)
        demo.write(self.folder / 'takes/s0001/take-00.json', receipt)
        self.check(receipt, 'The gift.')
        brand_path = ROOT / 'resources/branding/dogma-opening-v1/manifest.json'
        brand = demo.read(brand_path)
        manifest = {'opening': {'sha256': brand['languages']['en']['variants']['music']['audio_hash'],
                               'manifest_sha256': demo.file_digest(brand_path), 'language': 'English'}}
        self.assertFalse(demo.publish(self.folder, self.assignment, manifest, 'fixture', SimpleNamespace(max_takes=3)))
        self.assertFalse((self.folder / 'complete.json').exists())
        latest = demo.read(self.folder / 'public/latest.json')
        self.assertFalse(latest['music_ready'])
        self.assertEqual(latest['captions'][0]['start'], 8)
        self.assertEqual(latest['open_recognition_flags'], 0)
        self.assertTrue((self.folder / 'public' / latest['mp3']).stat().st_size > 1000)
        self.assertNotIn('Synthetic reference.', (self.folder / 'public/latest.json').read_text('utf8'))


if __name__ == '__main__':
    unittest.main()
