import sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
from render_higgs_palette_preview import _witness_complete
from rimsky.util import write_json,digest


class WitnessSkipTests(unittest.TestCase):
    def test_launch_is_skipped_only_when_every_flagged_take_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);sel=root/'selected.json';report=root/'w'/'report.json'
            row=lambda sha,status:{'unit':{'spoken':'текст '+sha},'word_check':{'status':status,'audio_sha256':sha}}
            write_json(sel,{'a':row('a1','spoken_text_mismatch'),'b':row('b1','asr_exact_match')})
            self.assertFalse(_witness_complete(report,[sel]))                         # no report yet
            write_json(report,{'checks':{'k':{'audio_sha256':'a1','expected_text_sha256':digest('текст a1'.encode('utf-8'))}}})
            self.assertTrue(_witness_complete(report,[sel]))                          # exact takes need no witness
            write_json(sel,{'a':row('a2','spoken_text_mismatch')})
            self.assertFalse(_witness_complete(report,[sel]))                         # new audio


if __name__=='__main__':unittest.main()
