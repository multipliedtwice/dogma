"""Descriptive pitch of exact reviewed references; never a hoarseness verdict."""
import argparse
import sys
from pathlib import Path
import numpy as np
import soundfile as sf
import librosa
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json
from higgs_trial_io import read_json as read
from rimsky.audio_join import boundary_check

def pitch(path):
    x,rate=sf.read(path,dtype='float32');x=librosa.resample(x,orig_sr=rate,target_sr=16000)
    f0,voiced,_=librosa.pyin(x,fmin=55,fmax=400,sr=16000,frame_length=1024,hop_length=256)
    vals=f0[voiced & np.isfinite(f0)]
    return {'median_pitch_hz':float(np.median(vals)) if len(vals) else None,'voiced_fraction':float(np.mean(voiced)),
            'method':'librosa.pyin; descriptive only, not voice quality or hoarseness'}

def measure(folder):
    dataset=ROOT/'training-work/higgs-production/datasets/reviewed-20261012-v1'
    refs=read(dataset/'references.json');rows=[]
    for family,items in refs.items():
        if not family.startswith('masculine') or 'mechanical' in family:continue
        for i,r in enumerate(items):
            p=dataset/r['audio']
            if file_digest(p)!=r['audio_sha256']:raise ValueError('Reference changed')
            row={'voice':family,'index':i,'reference':r,**pitch(p)};rows.append(row)
            print(family,i,round(row['median_pitch_hz'] or 0,1),'Hz',flush=True)
    write_json(folder/'reference-measurements.json',{'references_sha256':file_digest(dataset/'references.json'),
                                                   'librosa':librosa.__version__,'references':rows})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);measure(p.parse_args().output)
