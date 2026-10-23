import sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import numpy as np
import soundfile as sf
import torch
from asr_batching import transcribe,last_position_logits


class Result:
    def __init__(self,text):self.text=text


class FakeModel:
    def __init__(self,fail_above=None):self.calls=[];self.fail_above=fail_above
    def transcribe(self,paths,**kwargs):
        if self.fail_above and len(paths)>self.fail_above:raise torch.cuda.OutOfMemoryError('fake')
        self.calls.append((len(paths),kwargs.get('context')))
        return [Result(Path(p).stem) for p in paths]


class AsrBatchingTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();root=Path(self.folder.name)
        self.items=[]
        for name,seconds in (('long',3.0),('short',0.5),('mid',1.5),('tiny',0.2)):
            path=root/(name+'.wav');sf.write(path,np.zeros(int(16000*seconds),dtype='float32'),16000);self.items.append(path)
    def tearDown(self):self.folder.cleanup()

    def test_every_item_is_answered_once_in_duration_order_within_budget(self):
        model=FakeModel()
        out=list(transcribe(model,self.items,lambda p:p,size=3,max_seconds=2.5,language='Russian'))
        self.assertEqual([p.stem for p,_ in out],['tiny','short','mid','long'])
        self.assertTrue(all(p.stem==r.text for p,r in out))
        self.assertEqual([n for n,_ in model.calls],[3,1])  # 0.2+0.5+1.5 fits; 3.0 s goes alone

    def test_context_follows_each_item_and_out_of_memory_splits_the_batch(self):
        model=FakeModel(fail_above=1)
        contexts={p:'ctx-'+p.stem for p in self.items}
        out=list(transcribe(model,self.items,lambda p:p,size=4,max_seconds=100,context=[contexts[p] for p in self.items]))
        self.assertEqual(len(out),4)
        self.assertTrue(all(n==1 for n,_ in model.calls))
        self.assertEqual([c[0] for _,c in model.calls],['ctx-tiny','ctx-short','ctx-mid','ctx-long'])

    def test_last_position_logits_keeps_the_final_projection(self):
        class Wrapper:pass
        inner=torch.nn.Module();inner.lm_head=torch.nn.Linear(4,7,bias=False)
        wrapper=Wrapper();wrapper.model=inner
        hidden=torch.randn(2,5,4);full=inner.lm_head(hidden)
        last_position_logits(wrapper);last_position_logits(wrapper)  # idempotent
        sliced=inner.lm_head(hidden)
        self.assertEqual(tuple(sliced.shape),(2,1,7))
        self.assertTrue(torch.allclose(sliced[:,-1],full[:,-1]))


class AuditConfigurationMirrorTests(unittest.TestCase):
    def test_cached_audit_mirror_names_every_key_the_cuda_audit_records(self):
        # iteration 8 crashed with "Cached word verifier changed" when batching_sha256 was added to one side only
        import re
        from regenerate_higgs_book_trial import audit_configuration
        source=(ROOT/'scripts/audit_higgs_book_cuda.py').read_text('utf-8')
        block=source[source.index('config={'):source.index('signature=digest(config)')]
        recorded=set(re.findall(r"'([a-z_0-9]+)':",block))
        self.assertEqual(recorded,set(audit_configuration('cuda:0')))


class WitnessSectionTests(unittest.TestCase):
    def test_section_is_the_chunk_whose_reports_the_worker_reads(self):
        from audit_higgs_book_cuda import _section
        with tempfile.TemporaryDirectory() as folder:
            gen=Path(folder)/'gen';(gen/'manifest.json').parent.mkdir();(gen/'manifest.json').write_text('{}')
            chunk=gen/'c0003'/'chunk-001';(chunk/'take-1').mkdir(parents=True);(chunk/'assignment.json').write_text('{}')
            frame=chunk/'presentation-ab';(frame/'word-opus-recovery'/'take-0').mkdir(parents=True);(frame/'assignment.json').write_text('{}')
            batched=gen/'batched-recovery'/'round-0'/'variant-0';batched.mkdir(parents=True)
            self.assertEqual(_section(chunk/'take-1'),chunk)
            self.assertEqual(_section(frame/'word-opus-recovery'/'take-0'),frame)
            self.assertIsNone(_section(batched))


if __name__=='__main__':unittest.main()
