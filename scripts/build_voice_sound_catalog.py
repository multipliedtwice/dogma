"""Measure exact palette audio and describe families without inventing verdicts.

CPU only. Perceived weight/age/roughness are not inferred from pitch or level.
Unknown traits stay unknown. Existing explicit listener metadata is cited.
"""
import argparse
from collections import defaultdict
import importlib.metadata
import math
import sys
from pathlib import Path
import numpy as np
import soundfile as sf
import librosa
from scipy.signal import resample_poly

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json
from higgs_trial_io import read_json as read
DATASET=ROOT/'training-work/higgs-production/datasets/reviewed-20261012-v1'


def measure(path):
    x,sr=sf.read(path,dtype='float32',always_2d=True);x=x.mean(axis=1)
    divisor=math.gcd(sr,16000);x=resample_poly(x,16000//divisor,sr//divisor)
    frame,hop=1024,256
    frames=librosa.util.frame(np.pad(x,(frame//2,frame//2)),frame_length=frame,hop_length=hop).T
    rms=np.sqrt(np.mean(frames.astype('float64')**2,axis=1))
    active=rms>max(.0015,float(rms.max())*.08)
    pitch=librosa.yin(x,fmin=55,fmax=500,sr=16000,frame_length=frame,hop_length=hop)
    voiced=[];correlations=[];brightness=[]
    frequencies=np.fft.rfftfreq(frame,1/16000);band=(frequencies>=80)&(frequencies<=4000)
    for i in np.flatnonzero(active):
        if i>=len(pitch):continue
        signal=frames[i]-frames[i].mean();lag=round(16000/pitch[i])
        if not 1<=lag<frame//2:continue
        a,b=signal[:-lag],signal[lag:]
        correlation=float(a@b/max(1e-10,np.linalg.norm(a)*np.linalg.norm(b)))
        if correlation<.65 or not 57<pitch[i]<490:continue
        voiced.append(float(pitch[i]));correlations.append(correlation)
        power=np.abs(np.fft.rfft(signal*np.hanning(frame)))**2
        brightness.append(float(np.sum(frequencies[band]*power[band])/max(1e-12,power[band].sum())))
    return {'seconds':len(x)/16000,'voiced_frames':len(voiced),
        'median_pitch_hz':float(np.median(voiced)) if len(voiced)>=8 else None,
        'pitch_iqr_hz':list(map(float,np.percentile(voiced,[25,75]))) if len(voiced)>=8 else None,
        'voiced_spectral_centroid_hz':float(np.median(brightness)) if brightness else None,
        'median_periodicity_correlation':float(np.median(correlations)) if correlations else None,
        'active_level_dbfs':float(20*np.log10(max(1e-9,np.median(rms[active])))) if active.any() else None}


def ranked(value,values,low,middle,high):
    if value is None:return 'unclear'
    others=[x for x in values if x is not None]
    if len(others)<3:return 'unclear'
    percentile=sum(x<value for x in others)/len(others)
    return low if percentile<.3 else high if percentile>=.7 else middle


def build(bank_path,output,directions_path):
    if (output/'catalog.json').exists():raise ValueError('Use a fresh sound-catalog output')
    bank=read(bank_path);taxonomy_path=ROOT/'training/voice_families.json'
    taxonomy={r['id']:r for r in read(taxonomy_path)['families']}
    directions=read(directions_path);families=defaultdict(dict)
    for entry in bank['entries']:
        for r in entry['candidates']:families[entry['voice_family']][r['audio_sha256']]=r
    samples={};aggregates={};cache=[]
    for family,unique in sorted(families.items()):
        ranked_rows=sorted(unique.values(),key=lambda r:(r['mood'] not in ('calm','neutral'),abs(r['seconds']-5),r['id']))
        chosen=[];characters=set()
        for r in ranked_rows:
            if r['character'] not in characters:chosen.append(r);characters.add(r['character'])
            if len(chosen)==4:break
        for r in ranked_rows:
            if len(chosen)==4:break
            if r not in chosen:chosen.append(r)
        features=[]
        for row in chosen:
            path=(DATASET/row['audio']).resolve()
            if not path.is_relative_to((DATASET/'audio').resolve()) or file_digest(path)!=row['audio_sha256']:
                raise ValueError('Sound descriptor reference changed')
            if row['voice_basis']!='human' or row['mood_basis']!='human':raise ValueError('Unreviewed palette label')
            feature=measure(path);features.append(feature)
            cache.append({'family':family,'reference_sha256':row['audio_sha256'],'character':row['character'],
                'route':row['route'],'mood':row['mood'],'measurements':feature})
        samples[family]=chosen
        aggregates[family]={key:float(np.median([r[key] for r in features if r[key] is not None]))
            if any(r[key] is not None for r in features) else None
            for key in ('median_pitch_hz','voiced_spectral_centroid_hz','median_periodicity_correlation','active_level_dbfs')}
        print('MEASURED FAMILY',family,len(chosen),'references',aggregates[family],flush=True)
    pools=defaultdict(list)
    for family in families:
        category=taxonomy.get(family,{}).get('group','unknown')
        pools[category].append(aggregates[family])
    descriptions={}
    for family in families:
        label=taxonomy.get(family,{}).get('label',family);category=taxonomy.get(family,{}).get('group','unknown')
        metrics=aggregates[family];pool=pools[category]
        depth=ranked(metrics['median_pitch_hz'],[v['median_pitch_hz'] for v in pool],'low','medium','high')
        bright=ranked(metrics['voiced_spectral_centroid_hz'],[v['voiced_spectral_centroid_hz'] for v in pool],'dark','balanced','bright')
        age='mature' if 'middle-aged' in label or 'mature' in label else 'young' if 'young kid' in label else 'unclear'
        weight='unclear';weight_basis=None
        for direction in directions['casting'].values():
            if direction['voice_family']==family and 'weightier' in direction['instruction']:
                weight='heavy';weight_basis=direction['listener_evidence']
        gender='masculine' if category=='Masculine voices' else 'feminine' if category=='Feminine voices' else 'unclear'
        descriptors={'voice_weight':weight,'voice_depth':depth,'brightness':bright,'roughness':'unclear',
                     'perceived_age':age,'performed_gender':gender}
        descriptors['voice_quality']='nasal' if family=='nasal' else 'mechanical' if 'mechanical' in family else 'soft' if family=='narrator_historical_prose' else 'unclear'
        pitch=metrics['median_pitch_hz'];centroid=metrics['voiced_spectral_centroid_hz']
        summary=f'{label}. '
        if pitch is not None:summary+=f'Available references have median pitch {pitch:.0f} Hz; {depth} register relative to the {category} pool. '
        if centroid is not None:summary+=f'Voiced spectral balance is relatively {bright} in that pool (centroid {centroid:.0f} Hz). '
        if weight_basis:summary+='The listener explicitly describes this family as strong/weighty. '
        if age!='unclear':summary+=f'The human family label describes a {age} performed age. '
        if family=='nasal':summary+='The human-reviewed family label specifies nasal delivery. '
        if 'mechanical' in family or family=='mechanical_stats':summary+='Human-labeled mechanical presentation route. '
        summary+='Unspecified weight, age and roughness remain unknown; sample meaning is not timbre evidence.'
        descriptions[family]={'descriptors':descriptors,'summary':summary,'measurements':metrics,
            'basis':'measured_palette_audio_and_explicit_listener_metadata','reviewed':False,
            'trait_basis':{'voice_weight':{'kind':'explicit_listener' if weight_basis else 'unknown','quote':weight_basis},
                'voice_depth':{'kind':'measured_relative_pitch','comparison_pool':category},
                'brightness':{'kind':'voiced_spectral_proxy','comparison_pool':category},
                'roughness':{'kind':'unknown'},'perceived_age':{'kind':'human_family_label' if age!='unclear' else 'unknown','label':label}},
            'audio_evidence_sha256':[r['audio_sha256'] for r in samples[family]],
            'reference_contexts':[{'character':r['character'],'route':r['route'],'mood':r['mood']} for r in samples[family]],
            'limits':['Automated casting descriptor, not a human listening approval.',
                'Pitch does not establish weight or age. Periodicity does not establish hoarseness.',
                'Spectral brightness depends on phonemes, mood and recording conditions.',
                'Same-actor family differences and shared-family signatures still require listening qualification.']}
    write_json(output/'measurements.json',{'samples':cache,'methods':{
        'pitch':'librosa YIN with >=.65 waveform lag correlation; median across references',
        'brightness':'power spectral centroid of periodic frames, 80–4000 Hz',
        'versions':{name:importlib.metadata.version(name) for name in ('numpy','scipy','librosa','soundfile')}}})
    result={'schema':1,'kind':'voice_family_sound_catalog','narrator_id':'vlad-rimsky',
        'bank_sha256':file_digest(bank_path),'taxonomy_sha256':file_digest(taxonomy_path),
        'listener_directions_sha256':file_digest(directions_path),'builder_sha256':file_digest(Path(__file__)),
        'measurement_sha256':file_digest(output/'measurements.json'),'families':descriptions}
    write_json(output/'catalog.json',result)
    lines=['# Vlad Rimsky voice-family sound descriptions','',
           'Measured descriptions for casting. Unknown traits are explicit; these are not listener approvals.','']
    for family,row in sorted(descriptions.items()):
        lines.extend([f'## {family}','',row['summary'],''])
    (output/'descriptions.md').write_text('\n'.join(lines),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--listener-directions',type=Path,required=True)
    args=parser.parse_args();build(args.bank.resolve(),args.output.resolve(),args.listener_directions.resolve())
