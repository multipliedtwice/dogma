"""Read-only inventory of flags on the audio actually served to the listener."""
from __future__ import annotations
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
from higgs_trial_io import read_json as read
from higgs_published_timeline import read_timeline
from rimsky.util import write_json,file_digest,digest


def inventory(generation,media):
    import numpy as np,soundfile as sf
    from rimsky.audio_join import prepare_clip
    progress=generation/'progress.json';progress_sha=file_digest(progress);state=read(progress)
    gains=read(generation/'frozen-gains.json');chapters={}
    for cid,ready in state['chapters'].items():
        timeline=read_timeline(generation,cid,ready)
        audio=media/Path(ready['stream']).name
        if file_digest(audio)!=ready['wav_sha256']:raise ValueError('Served speech audio changed')
        samples,rate=sf.read(audio,dtype='int16')
        flags={row['id']:{'timeline':row,'published_pcm_sha256':digest(samples[round(row['start']*rate):round(row['end']*rate)].tobytes()),
            'audio_bound_evidence':[],'candidate_evidence':[]} for row in timeline
            if row.get('word_status') not in ('asr_exact_match','nonverbal_not_spoken_text')}
        seen=set()
        for path in sorted((generation/cid).glob('**/selected.json')):
            for uid,row in read(path).items():
                if uid not in flags:continue
                wav=Path(row['path']);receipt=row['receipt'];unit=row['unit'];check=row['word_check']
                identity=(uid,receipt['audio_sha256'],receipt['key'])
                if identity in seen:continue
                seen.add(identity)
                if (file_digest(wav)!=receipt['audio_sha256'] or check['audio_sha256']!=receipt['audio_sha256']
                    or check['synthesis_key']!=receipt['key']):raise ValueError('Flag candidate evidence changed')
                data,sr=sf.read(wav,dtype='float32');data,_=prepare_clip(data,sr)
                actor=unit['speaker']+':'+unit['render_voice'];gain=gains.get(actor)
                matches=False
                if gain is not None and sr==rate:
                    pcm=np.clip(np.floor(data*gain*32768),-32768,32767).astype('int16')
                    matches=digest(pcm.tobytes())==flags[uid]['published_pcm_sha256']
                evidence={'selection':str(path.relative_to(generation)),'selection_sha256':file_digest(path),
                    'raw_audio':str(wav),'audio_sha256':receipt['audio_sha256'],'synthesis_key':receipt['key'],
                    'word_check':check,'matches_published_pcm':matches}
                flags[uid]['audio_bound_evidence' if matches else 'candidate_evidence'].append(evidence)
        if len(flags)!=ready['word_flags']:raise ValueError('Flag inventory does not reproduce the served counter')
        chapters[cid]={'published_wav_sha256':ready['wav_sha256'],'timeline_asset':ready.get('timeline_asset'),
            'open_flag_count':len(flags),'flags':flags}
    # Other work may legitimately advance the progress file. The immutable
    # waveform/timeline hashes above identify this inventory's exact snapshot.
    return {'generation':generation.name,'progress_sha256':progress_sha,'chapters':chapters,
        'implementation_sha256':file_digest(Path(__file__)),'read_only_inventory':True,
        'grants_acceptance':False,'candidate_transcripts_are_not_published_audio_evidence':True}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--generation',type=Path,required=True)
    p.add_argument('--media',type=Path,default=ROOT/'training-work/character-rb2/full/clips/elirm-iv-mvp')
    a=p.parse_args();report=inventory(a.generation,a.media)
    output=a.generation/'published-word-ledger'/(digest(report)+'.json');write_json(output,report)
    print(output)
    for cid,row in report['chapters'].items():print(cid,row['open_flag_count'],'open;',sum(bool(r['audio_bound_evidence']) for r in row['flags'].values()),'bound to original takes')
