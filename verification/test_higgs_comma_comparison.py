import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from compare_higgs_commas import prepare
from rimsky.speech_quality import word_tokens


class CommaComparisonTests(unittest.TestCase):
    def test_pinned_comparison_preserves_words_seed_pairs_and_source(self):
        source=ROOT/'training-work/elirm-iv-higgs-mvp/source/book.json'
        if not source.exists():self.skipTest('Local audition source unavailable')
        before=source.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            with patch('compare_higgs_commas.publish'):
                manifest=prepare(folder)
                self.assertEqual(prepare(folder),manifest)
            self.assertEqual(len(manifest['seed_pairs']),5)
            a,b=manifest['variants'].values()
            self.assertEqual(word_tokens(a),word_tokens(b))
            self.assertEqual(a.replace(',',''),b)
            self.assertGreaterEqual(manifest['commas_removed'],7)
            self.assertEqual(manifest['effects'],'disabled')
            self.assertFalse(manifest['production_approved'])
            self.assertEqual(source.read_bytes(),before)
            changed=json.loads((folder/'manifest.json').read_text('utf8'))
            changed['seed_pairs'][0]+=1
            (folder/'manifest.json').write_text(json.dumps(changed),encoding='utf8')
            with patch('compare_higgs_commas.publish'),self.assertRaisesRegex(ValueError,'inputs changed'):
                prepare(folder)


if __name__=='__main__':unittest.main()
