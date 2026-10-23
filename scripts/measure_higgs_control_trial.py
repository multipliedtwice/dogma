"""Record pitch and boundary diagnostics for actual control-trial WAVs."""
import argparse
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.audio_join import boundary_check
from rimsky.util import file_digest,write_json
from higgs_trial_io import read_json


def energy_profile(x, sr):
    """Descriptive 20 ms energy windows, not speech detection or approval."""
    import numpy as np
    x=np.asarray(x)
    if x.ndim!=1 or not len(x) or not np.isfinite(x).all():
        raise ValueError('Diagnostics require finite mono audio')
    size=max(1,round(sr*.02))
    frames=[x[i:i+size] for i in range(0,len(x),size)]
    rms=np.array([np.sqrt(np.mean(f.astype('float64')**2)) for f in frames])
    threshold=max(.003,float(rms.max())*.02)
    active=np.flatnonzero(rms>threshold)
    pauses=[]
    if len(active):
        for left,right in zip(active[:-1],active[1:]):
            seconds=(right-left-1)*size/sr
            if seconds>=.10:pauses.append(round(seconds,4))
    level=float(np.median(rms[active])) if len(active) else 0.
    return {'duration_seconds':len(x)/sr,'active_median_dbfs':float(20*np.log10(level)) if level else None,
            'energy_threshold':threshold,'leading_low_energy_seconds':float(active[0]*size/sr) if len(active) else len(x)/sr,
            'trailing_low_energy_seconds':max(0.,(len(x)-(active[-1]+1)*size)/sr) if len(active) else len(x)/sr,
            'internal_low_energy_runs_seconds':pauses}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);a=p.parse_args()
    import librosa
    import numpy as np
    import soundfile as sf
    folder=a.folder.resolve();plan=read_json(folder/'render-plan.json');takes=read_json(folder/'receipts.json')
    if takes['plan_sha256']!=file_digest(folder/'render-plan.json'):raise ValueError('Control plan changed')
    checks_path=next((folder/name for name in ('word-checks-1.7B-normalized.json','word-checks-1.7B.json')
                      if (folder/name).exists()),None)
    checks=read_json(checks_path) if checks_path else None
    if checks and checks['plan_sha256']!=file_digest(folder/'render-plan.json'):
        raise ValueError('Word audit answers a different control plan')
    def describe(path):
        x,sr=sf.read(path,dtype='float32');profile=energy_profile(x,sr)
        y=librosa.resample(x,orig_sr=sr,target_sr=16000)
        f0,voiced,_=librosa.pyin(y,fmin=55,fmax=450,sr=16000,frame_length=1024,hop_length=256)
        pitch=f0[voiced & np.isfinite(f0)]
        return {**profile,'median_pitch_hz':float(np.median(pitch)) if len(pitch) else None,
                'pitch_p90_hz':float(np.percentile(pitch,90)) if len(pitch) else None,
                'voiced_frames':len(pitch),'edges':boundary_check(x,sr)}
    references={}
    rows=[]
    for u in plan['units']:
        r=takes['takes'][u['id']];path=folder/r['audio']
        if file_digest(path)!=r['audio_sha256'] or r['key']!=u['key']:raise ValueError('Trial take changed')
        check=checks['checks'][u['id']] if checks else None
        if check and (check['audio_sha256']!=r['audio_sha256'] or check.get('synthesis_key')!=r['key']):
            raise ValueError('Word audit answers a different take')
        reference=u['synthesis']['reference'];ref_path=ROOT/reference['audio']
        if file_digest(ref_path)!=reference['sha256']:raise ValueError('Voice reference changed')
        if reference['sha256'] not in references:
            references[reference['sha256']]={'audio':reference['audio'],'text':reference['text'],**describe(ref_path)}
        row={'id':u['id'],'audio_sha256':r['audio_sha256'],'synthesis_key':r['key'],
             'prompt':u['synthesis']['prompt'],'reference_sha256':u['synthesis']['reference']['sha256'],
             **describe(path),'word_status':check['status'] if check else 'not_checked'}
        rows.append(row);print(u['id'],row['median_pitch_hz'],row['word_status'],flush=True)
    write_json(folder/'control-measurements.json',{'plan_sha256':file_digest(folder/'render-plan.json'),
        'receipts_sha256':file_digest(folder/'receipts.json'),
        'word_audit_sha256':file_digest(checks_path) if checks_path else None,
        'implementation_sha256':file_digest(Path(__file__)),'librosa':librosa.__version__,
        'method':'pYIN, 16kHz, 55-450Hz, 1024-frame, 256-hop; finite voiced frames only. Energy: 20ms windows, threshold max(0.003, 2% peak window RMS), internal gaps at least 100ms.',
        'limits':'Energy gaps are not verified word pauses. References use different text, so timing comparisons do not establish delivery. Pitch does not establish hoarseness, voice identity or successful shouting. No listening approval.',
        'references':references,'rows':rows})


if __name__=='__main__':main()
