import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky import word_recovery
import json
from rimsky.util import digest,write_json
read_json=lambda p:json.loads(Path(p).read_text(encoding="utf-8"))

def fake(folder,request):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);path=folder/'request.json'
    if path.exists() and read_json(path)!=request:raise ValueError('Frozen diagnosis request changed')
    if not path.exists():write_json(path,request);write_json(folder/'answer.json',{'n':len(request['units'])})
    return {'folder':folder.name}

def request(n):return {'units':[{'unit_id':'u','prior_attempts':list(range(n))}]}

class RefreshTests(unittest.TestCase):
    def test_identical_request_reuses_base(self):
        with tempfile.TemporaryDirectory() as work,patch.object(word_recovery,'diagnose',fake):
            base=Path(work)/'diag';word_recovery.diagnose_fresh(base,request(1))
            result,used=word_recovery.diagnose_fresh(base,request(1))
            self.assertEqual(used,base);self.assertEqual([p.name for p in Path(work).iterdir()],['diag'])

    def test_changed_request_gets_digest_sibling_and_old_folder_untouched(self):
        with tempfile.TemporaryDirectory() as work,patch.object(word_recovery,'diagnose',fake):
            base=Path(work)/'diag';word_recovery.diagnose_fresh(base,request(1))
            before=(base/'request.json').read_bytes(),(base/'answer.json').read_bytes()
            result,used=word_recovery.diagnose_fresh(base,request(2))
            self.assertEqual(used.name,'diag-'+digest(request(2))[:12])
            self.assertEqual(before,((base/'request.json').read_bytes(),(base/'answer.json').read_bytes()))
            self.assertEqual(word_recovery.diagnose_fresh(base,request(2))[1],used)
            self.assertEqual(len(list(Path(work).iterdir())),2)

    def test_refresh_cap(self):
        with tempfile.TemporaryDirectory() as work,patch.object(word_recovery,'diagnose',fake):
            base=Path(work)/'diag';word_recovery.diagnose_fresh(base,request(0))
            for n in (1,2,3):word_recovery.diagnose_fresh(base,request(n))
            with self.assertRaises(word_recovery.RefreshCapReached):word_recovery.diagnose_fresh(base,request(4))
            self.assertEqual(word_recovery.diagnose_fresh(base,request(3))[1].name,'diag-'+digest(request(3))[:12])
            self.assertEqual(len(list(Path(work).iterdir())),4)

    def test_other_errors_propagate(self):
        def boom(folder,request):raise ValueError('Unbound Opus diagnosis')
        with tempfile.TemporaryDirectory() as work,patch.object(word_recovery,'diagnose',boom):
            with self.assertRaisesRegex(ValueError,'Unbound'):word_recovery.diagnose_fresh(Path(work)/'diag',request(1))

if __name__=='__main__':unittest.main()
