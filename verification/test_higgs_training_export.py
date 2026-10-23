import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from export_higgs_training import BOOKS, export, read, sha, verified_evidence, write


class TrainingExport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.benchmark = self.root / 'benchmark'
        self.benchmark.mkdir()
        evidence, inputs, splits = [], {}, {}
        for bi, book in enumerate(sorted(BOOKS)):
            archive = self.root / 'manual-labeling' / book
            (archive / 'audio').mkdir(parents=True)
            write(archive / 'review.json', {'human': True})
            review_hash = sha(archive / 'review.json')
            write(archive / 'latest.json', {'review_sha256': review_hash})
            write(archive / 'archive-manifest.json', {'review_sha256': review_hash})
            for name in ('review.json', 'latest.json', 'archive-manifest.json'):
                path = archive / name
                inputs[path.relative_to(self.root).as_posix()] = sha(path)
            splits[book] = {'development_chapters': ['dev'], 'test_chapters': ['test']}
            for n in range(4):
                temporary = self.root / 'temp.wav'
                sf.write(temporary, np.r_[np.zeros(1200), .1 * np.sin(np.arange(24000) * (.02 + (n + bi * 4) * .003)),np.zeros(1200)], 24000, subtype='PCM_16')
                audio_hash = sha(temporary)
                if book == 'reality-benders-1':
                    audio = archive / 'audio' / (audio_hash + '.wav')
                else:
                    audio = self.root / 'training-work/character-rb2/full/clips' / f'id{n}.wav'
                    audio.parent.mkdir(parents=True, exist_ok=True)
                audio.write_bytes(temporary.read_bytes())
                evidence.append({'book': book, 'id': f'id{n}', 'chapter_id': 'test' if n == 3 else 'dev',
                                 'voice': f'voice{bi}', 'voice_basis': 'human', 'mood': 'calm', 'mood_basis': 'human',
                                 'unit': 'clip', 'boundary_held': False, 'audio_sha256': audio_hash,
                                 'text': 'Reviewed exact text.', 'delivery_proposed': 'angry'})
        self.evidence = evidence
        (self.benchmark / 'synthesis-evidence.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in evidence), encoding='utf8')
        write(self.benchmark / 'split.json', {'books': splits})
        write(self.benchmark / 'evidence-manifest.json', {'inputs': inputs, 'outputs': {
            name: sha(self.benchmark / name) for name in ('synthesis-evidence.jsonl', 'split.json')}})

    def test_exact_audio_separate_chapters_and_human_conditioning(self):
        out = self.root / 'output'
        report = export(self.root, self.benchmark, out)
        self.assertEqual({'train': 2, 'validation': 2}, report['counts'])
        train = [json.loads(line) for line in (out / 'train.jsonl').read_text().splitlines()]
        valid = [json.loads(line) for line in (out / 'validation.jsonl').read_text().splitlines()]
        self.assertTrue(all(r['metadata']['chapter_id'] == 'dev' for r in train))
        self.assertTrue(all(r['metadata']['chapter_id'] == 'test' for r in valid))
        for row in [*train, *valid]:
            self.assertEqual(row['metadata']['audio_sha256'], sha(out / row['audio']))
            self.assertNotEqual(row['metadata']['audio_sha256'], row['metadata']['ref_audio_sha256'])
            self.assertNotIn('delivery', row)
            self.assertNotIn('delivery_proposed', row)
            self.assertEqual('calm', row['metadata']['mood'])
        with self.assertRaisesRegex(ValueError, 'fresh output'):
            export(self.root, self.benchmark, out)

    def test_changed_review_or_audited_bytes_refused_before_export(self):
        archive = self.root / 'manual-labeling/reality-benders-1/review.json'
        original = archive.read_bytes()
        archive.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Frozen input changed'):
            export(self.root, self.benchmark, self.root / 'output')
        self.assertFalse((self.root / 'output').exists())
        archive.write_bytes(original)
        row = next(r for r in self.evidence if r['book'] == 'reality-benders-1')
        audio = archive.parent / 'audio' / (row['audio_sha256'] + '.wav')
        audio.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Audited WAV changed'):
            verified_evidence(self.root, self.benchmark)

    def test_active_edge_excludes_reference_without_changing_human_training_target(self):
        row=next(r for r in self.evidence if r['book']=='reality-benders-1' and r['id']=='id0')
        source=self.root/'manual-labeling/reality-benders-1/audio'/('new.wav')
        sf.write(source,np.full(48000,.1),24000,subtype='PCM_16')
        row['audio_sha256']=sha(source)
        source.rename(source.with_name(row['audio_sha256']+'.wav'))
        path=self.benchmark/'synthesis-evidence.jsonl'
        path.write_text(''.join(json.dumps(r)+'\n' for r in self.evidence),encoding='utf8')
        manifest=read(self.benchmark/'evidence-manifest.json')
        manifest['outputs'][path.name]=sha(path);write(self.benchmark/'evidence-manifest.json',manifest)
        out=self.root/'reference-filtered';report=export(self.root,self.benchmark,out)
        self.assertTrue(any(r['id']=='id0' and r['book']==row['book'] for r in report['reference_rejections']))
        self.assertNotIn(row['audio_sha256'],[r['audio_sha256'] for r in read(out/'references.json')['voice0']])
        train=[json.loads(l) for l in (out/'train.jsonl').read_text('utf8').splitlines()]
        self.assertTrue(any(r['metadata']['audio_sha256']==row['audio_sha256'] and r['metadata']['mood']=='calm' for r in train))


if __name__ == '__main__':
    unittest.main()
