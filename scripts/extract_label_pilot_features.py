"""Frozen feature encoders used by the Book 1 trained label heads."""
import argparse, json, os
import time
from pathlib import Path
from prepare_book_label_pilot import ROOT, sha, save
from benchmark_book1_emotion2vec import ACOUSTIC_KEYS
from book1_pitch_contour import pitch_contour
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')

def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['wave','emotion','emotion-frame','combine']);p.add_argument('--emotion-model',choices=['base','large'],default='base')
    p.add_argument('--out',type=Path,default=ROOT/'training-work/character-rb2/pilot')
    p.add_argument('--manifest',default='checked-clips.jsonl')
    p.add_argument('--emotion-frame-mean',action='store_true',
                   help='Combine the frame-level emotion2vec mean used by the Books 1+2 series model.')
    args=p.parse_args()
    folder=args.out.resolve()
    rows=[json.loads(line) for line in (folder/args.manifest).read_text(encoding='utf-8').splitlines()]
    import numpy as np
    if args.stage=='emotion':
        import benchmark_book1_emotion2vec as benchmark
        benchmark.OUT=folder
        benchmark.extract(rows,device='cuda')
        return
    if args.stage=='emotion-frame':
        import setuptools  # noqa: F401 - Python 3.12 distutils compatibility
        import torch
        from funasr import AutoModel
        from benchmark_book1_emotion2vec import MODEL
        cache=folder/'emotion-frame-features';cache.mkdir(exist_ok=True)
        pending=[row for row in rows if not (cache/(row['exact_audio_sha256']+'.npy')).is_file()]
        if not pending:
            print(f'All {len(rows)} frame-level emotion features are cached.',flush=True);return
        model=AutoModel(model=str(MODEL),device='cuda',ncpu=4,disable_update=True,
                        disable_pbar=True,disable_log=True,log_level='ERROR')
        started=time.monotonic()
        for index,row in enumerate(pending,1):
            path=ROOT/row['exact_audio']
            if sha(path)!=row['exact_audio_sha256']:raise ValueError('Changed WAV')
            result=model.generate(input=str(path),granularity='frame',extract_embedding=True,
                                  disable_pbar=True,disable_log=True)[0]
            frames=np.asarray(result['feats'],dtype=np.float16)
            if frames.ndim!=2 or frames.shape[1]!=768 or not np.isfinite(frames).all():
                raise ValueError(f'Invalid frame features for {row["id"]}: {frames.shape}')
            target=cache/(row['exact_audio_sha256']+'.npy');temporary=target.with_suffix('.npy.tmp')
            with temporary.open('wb') as stream:np.save(stream,frames,allow_pickle=False)
            temporary.replace(target)
            if index%25==0 or index==len(pending):
                print(f'Emotion frames {index}/{len(pending)} elapsed={time.monotonic()-started:.1f}s',flush=True)
        return
    if args.stage=='combine':
        revision='b318240bfe67db81a8c572ecb37ce9c3759b81c9' if args.emotion_model=='base' else '6c303ba987b86b93193de93e34bb2b077a6bedc4'
        emotion_folder=folder if args.emotion_model=='base' else folder/'emotion-large'
        features={r['id']:r for r in map(json.loads,(folder/'audio-features.jsonl').read_text(encoding='utf-8').splitlines())}
        wave=[];timbre=[];emotion=[]
        for row in rows:
            a=features[row['id']]
            if a['audio_sha256']!=sha(ROOT/row['exact_audio']) or a['audio_sha256']!=row['exact_audio_sha256']:raise ValueError('Changed WAV')
            wave.append([np.nan if a.get(k) is None else a[k] for k in ACOUSTIC_KEYS]+[np.nan if b is None else b for b in a['relative_pitch_curve_semitones']])
            timbre.append(a['speaker_embedding'])
            if args.emotion_frame_mean:
                frames=np.load(folder/'emotion-frame-features'/(row['exact_audio_sha256']+'.npy'),allow_pickle=False)
                if frames.ndim!=2 or frames.shape[1]!=768 or not np.isfinite(frames).all():raise ValueError('Invalid frame emotion features')
                emotion.append(frames.astype(np.float32).mean(axis=0))
            else:
                with np.load(emotion_folder/('features-'+revision[:12])/(row['exact_audio_sha256']+'.npz')) as v:emotion.append(v['features'])
        name='features.npz' if args.emotion_model=='base' else 'features-large.npz'
        np.savez_compressed(folder/name,ids=np.asarray([r['id'] for r in rows]),audio_sha256=np.asarray([r['exact_audio_sha256'] for r in rows]),wave=np.asarray(wave),timbre=np.asarray(timbre),emotion=np.asarray(emotion),emotion_revision=np.asarray(revision),emotion_feature_kind=np.asarray('frame-mean' if args.emotion_frame_mean else 'utterance'))
        print(f'Bound {len(rows)} complete feature rows',flush=True);return
    import librosa, soundfile as sf, torch
    from scipy.signal import butter,find_peaks,sosfilt
    from scipy.ndimage import gaussian_filter1d
    from safetensors import safe_open
    from qwen_tts.core.models.configuration_qwen3_tts import Qwen3TTSSpeakerEncoderConfig
    from qwen_tts.core.models.modeling_qwen3_tts import Qwen3TTSSpeakerEncoder,mel_spectrogram
    from audit_book1_label_acoustics import average_level_dbfs
    torch.set_num_threads(4)
    model=ROOT/'training-work/models/Qwen3-TTS-12Hz-0.6B-Base'
    config=json.loads((model/'config.json').read_text(encoding='utf-8'))
    encoder=Qwen3TTSSpeakerEncoder(Qwen3TTSSpeakerEncoderConfig(**config['speaker_encoder_config']))
    state={}
    for path in model.glob('*.safetensors'):
        with safe_open(path,framework='pt',device='cpu') as reader:
            for name in reader.keys():
                if name.startswith('speaker_encoder.'):state[name.removeprefix('speaker_encoder.')]=reader.get_tensor(name).float()
    encoder.load_state_dict(state,strict=True);encoder.eval();del state
    cache=folder/'audio-features.jsonl'
    completed={r['audio_sha256'] for r in map(json.loads,cache.read_text().splitlines())} if cache.exists() else set()
    rate,frame,hop=16000,1024,256
    band=butter(4,[300,3000],btype='bandpass',fs=rate,output='sos')
    with cache.open('a',encoding='utf-8') as output:
        for index,row in enumerate(rows):
            path=ROOT/row['exact_audio']
            if sha(path)!=row['exact_audio_sha256']:raise ValueError('Changed WAV')
            if row['exact_audio_sha256'] in completed:continue
            audio,sr=sf.read(path,dtype='float32',always_2d=True);mono=audio.mean(axis=1)
            y=librosa.resample(mono,orig_sr=sr,target_sr=rate) if sr!=rate else mono
            rms=librosa.feature.rms(y=y,frame_length=frame,hop_length=hop)[0]
            db=20*np.log10(np.maximum(rms,1e-9));active=rms>max(float(rms.max())*.08,.0015)
            indices=np.flatnonzero(active)
            if len(indices)<5:raise ValueError('Insufficient active audio')
            first,last=indices[0],indices[-1];span=max(.1,(last-first+1)*hop/rate)
            f0=librosa.yin(y,fmin=55,fmax=450,sr=rate,frame_length=frame,hop_length=hop)
            frames=librosa.util.frame(np.pad(y,(frame//2,frame//2)),frame_length=frame,hop_length=hop).T
            pitch=[];times=[]
            for i in indices:
                if i>=len(f0) or i>=len(frames):continue
                lag=int(round(rate/f0[i]));signal=frames[i]-frames[i].mean()
                if 1<=lag<frame//2:
                    left,right=signal[:-lag],signal[lag:]
                    corr=float(np.dot(left,right)/max(1e-10,np.linalg.norm(left)*np.linalg.norm(right)))
                    if corr>=.65 and 57<f0[i]<440:pitch.append(float(f0[i]));times.append(float(i*hop/rate))
            pitch=np.asarray(pitch);contour=pitch_contour(times,pitch.tolist(),first*hop/rate,(last+1)*hop/rate)
            envelope=np.sqrt(np.mean(librosa.util.frame(np.pad(sosfilt(band,y)**2,(160,160)),frame_length=320,hop_length=160),axis=0))
            envelope=gaussian_filter1d(envelope,2)
            peaks,_=find_peaks(envelope,distance=10,prominence=max(float(envelope.max())*.09,.001))
            a24=librosa.resample(mono,orig_sr=sr,target_sr=24000) if sr!=24000 else mono
            with torch.inference_mode():
                mel=mel_spectrogram(torch.from_numpy(a24).unsqueeze(0),n_fft=1024,num_mels=128,sampling_rate=24000,hop_size=256,win_size=1024,fmin=0,fmax=12000).transpose(1,2)
                embedding=encoder(mel)[0].numpy()
            embedding/=max(float(np.linalg.norm(embedding)),1e-9)
            feature={'id':row['id'],'audio_sha256':row['exact_audio_sha256'],'duration_seconds':round(len(mono)/sr,3),'speech_span_seconds':round(span,3),
                'median_pitch_hz':round(float(np.median(pitch)),2) if len(pitch)>=8 else None,
                'pitch_range_semitones':round(float(12*np.log2(np.percentile(pitch,90)/np.percentile(pitch,10))),2) if len(pitch)>=8 else None,
                'active_level_dbfs':round(float(np.median(db[active])),2),'average_level_dbfs':average_level_dbfs(mono),
                'level_range_db':round(float(np.percentile(db[active],90)-np.percentile(db[active],10)),2),
                'internal_pause_fraction':round(float(1-active[first:last+1].mean()),4),'envelope_peaks_per_second':round(len(peaks)/span,3),
                'periodic_fraction':round(len(pitch)/len(indices),4),**contour,'speaker_embedding':embedding.tolist()}
            output.write(json.dumps(feature)+'\n');output.flush()
            if (index+1)%25==0 or index+1==len(rows):print(f'Wave/timbre {index+1}/{len(rows)}',flush=True)

def benchmark_revision():
    from benchmark_book1_emotion2vec import MODEL_REVISION
    return MODEL_REVISION[:12]

if __name__=='__main__':main()
