import sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime'))
import numpy as np,soundfile as sf
from rimsky.reference_onset import padded,PAD_SECONDS
from rimsky.util import file_digest


class ReferenceOnsetTests(unittest.TestCase):
    def make(self,root,name,lead,tail):
        rate=24000;speech=0.2*np.sin(np.linspace(0,600,rate)).astype('float32')
        audio=np.concatenate([np.zeros(int(lead*rate),'float32'),speech,np.zeros(int(tail*rate),'float32')])
        path=root/name;sf.write(path,audio,rate);return {'audio':name,'sha256':file_digest(path),'text':'x'}

    def test_abrupt_reference_gets_a_bound_padded_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);ref=self.make(root,'ref.wav',0.013,0.02)
            out=padded(ref,root,root/'derived')
            self.assertEqual(out['sha256'],ref['sha256'])                   # reviewed identity kept
            self.assertNotEqual(out['audio'],ref['audio'])
            self.assertEqual(out['onset_padding']['source_sha256'],ref['sha256'])
            x,rate=sf.read(root/out['audio']);self.assertGreaterEqual(int(np.flatnonzero(np.abs(x)>0.01)[0]),int(PAD_SECONDS*rate))

    def test_reference_with_quiet_edges_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);ref=self.make(root,'ref.wav',0.3,0.3)
            self.assertEqual(padded(ref,root,root/'derived'),ref)


if __name__=='__main__':unittest.main()
