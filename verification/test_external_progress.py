import json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.external_progress import external_jobs,summarize


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False),'utf-8')


class ExternalProgressTests(unittest.TestCase):
    def make(self,alive=True):
        root=Path(self.tmp.name);folder=root/'training-work/book-x';gen=folder/'audio-run'
        write(folder/'source/book.json',{'title':'Book','chapters':[{'id':'c0001','title':'One'},{'id':'c0002','title':'Two'}]})
        for cid,n in (('c0001',3),('c0002',4)):
            write(folder/f'audio-v7/{cid}/plan/render-plan.json',{'units':[{'id':f'{cid}-u{i}'} for i in range(n)]})
        ok={'word_check':{'status':'asr_exact_match'}};bad={'word_check':{'status':'spoken_text_mismatch'}}
        write(gen/'c0002/chunk-000/selected.json',{'c0002-u0':ok,'c0002-u1':bad,'c0002-u2':ok})
        write(gen/'progress.json',{'status':'rendering','chapters':{'c0001':{'complete':True,'units':3,'word_flags':1,'seconds':120}},
            'held_sections':{'c0002/chunk-000':{'reason':'Unverified spoken words remain after recovery; section held: c0002-u1'}}})
        write(gen/'supervision.json',{'worker_alive':alive,'journal':[{'event':'started','command':['x','--chapters','c0002','c0001']}]})
        return root,gen

    def setUp(self):self.tmp=tempfile.TemporaryDirectory()
    def tearDown(self):self.tmp.cleanup()

    def test_counts_verified_held_and_unrendered_passages(self):
        root,gen=self.make();before={p:p.read_bytes() for p in gen.rglob('*') if p.is_file()}
        job=external_jobs(root)[0]
        self.assertEqual([c['id'] for c in job['chapters']],['c0002','c0001'])
        two,one=job['chapters']
        self.assertEqual((two['verified'],two['rendered'],two['held']),(2,3,['c0002-u1']))
        self.assertEqual((one['verified'],one['open_flags'],one['complete']),(2,1,True))
        self.assertEqual((job['total'],job['verified'],job['held']),(7,4,1))
        self.assertEqual(job['eta_basis']['unrendered_units'],1)
        self.assertEqual({p:p.read_bytes() for p in gen.rglob('*') if p.is_file()},before)

    def test_no_estimate_when_worker_is_down(self):
        _,gen=self.make(alive=False)
        self.assertIsNone(summarize(gen)['eta_seconds'])


if __name__=='__main__':unittest.main()
