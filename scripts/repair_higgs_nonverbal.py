"""Route explicit animal sounds to a pinned local CC0 asset, not human TTS."""
import argparse
import copy
import json
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json,digest
from rimsky.nonverbal import animal_vocalization
from rimsky.audio_join import boundary_check,JOIN_POLICY
from higgs_trial_io import read_json as read
from render_higgs_book_trial import assemble
from regenerate_higgs_book_trial import gains


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);a=p.parse_args()
    import numpy as np
    import librosa
    import soundfile as sf
    folder=a.folder.resolve();policy=folder/'revision-01/character-profiles.json';characters=read(policy)['profiles']
    asset=ROOT/'resources/nonverbal/dog/dog-growl.flac'
    asset_hash=file_digest(asset)
    proof={'source_page':'https://opengameart.org/content/dog-snarl-grunt-grumble',
           'archive_url':'https://opengameart.org/sites/default/files/dog_0.7z',
           'author':'qubodup','license':'CC0-1.0','source_sha256':asset_hash,
           'archive_sha256':file_digest(asset.parents[1]/'dog-qubodup.7z')}
    write_json(asset.parents[1]/'dog-growl-source.json',proof)
    x,sr=sf.read(asset,dtype='float32',always_2d=True);x=x.mean(axis=1)
    original=boundary_check(x,sr)
    if original['unsafe_start'] or original['unsafe_end']:raise ValueError('Source effect has clipped/active edges')
    y=librosa.resample(x,orig_sr=sr,target_sr=24000)
    # One gain for this asset; no pitch shift, speech fade or invented model tag.
    gain=10**(-30/20)/float(np.sqrt(np.mean(y*y)))
    y=np.pad(y*gain,(960,960))
    bounds=boundary_check(y,24000)
    if bounds['unsafe_start'] or bounds['unsafe_end']:raise ValueError('Prepared effect boundary failed')
    processing={'samplerate':24000,'mono':True,'asset_gain':gain,'target_rms_dbfs':-30,
                'padding_seconds':.04,'resampler':'librosa '+librosa.__version__,'fade':False}
    masters=[];replaced=[]
    for chapter in ('c0001','c0002','c0003'):
        source=folder/'audio-v6'/chapter/'master';old=read(source/'render-plan.json');receipt=read(source/'receipts.json')
        if receipt['plan_sha256']!=file_digest(source/'render-plan.json'):raise ValueError('Original chapter changed')
        words=read(source/'word-checks.json')['checks'];out=folder/'audio-v7'/chapter;master=out/'master';master.mkdir(parents=True,exist_ok=True)
        rows=[];takes={};checks={}
        for original in old['units']:
            key=original['id'];u=copy.deepcopy(original);take=receipt['takes'][key];check=words[key]
            intent=animal_vocalization(u['text'],characters.get(u['speaker'],{})) if u['mode']=='speech' else None
            if intent:
                sound={'schema':1,'backend':'nonverbal_asset','text':u['text'],'intent':intent,'source':proof,
                       'processing':processing,'implementation_sha256':file_digest(Path(__file__)),
                       'classifier_sha256':file_digest(ROOT/'runtime/rimsky/nonverbal.py'),
                       'voice_family':'nonverbal:dog_growl',
                       'reference':{'audio':asset.relative_to(ROOT).as_posix(),'sha256':asset_hash,'text':''}}
                audio=out/(key+'.wav');sf.write(audio,y,24000,subtype='PCM_16')
                u.update(synthesis=sound,key=digest(sound),render_voice='nonverbal:dog_growl',audio_kind='nonverbal',spoken='')
                take={'key':u['key'],'audio_sha256':file_digest(audio),'duration':len(y)/24000,'sample_rate':24000,
                      'word_status':'nonverbal_not_spoken_text','audio_checks':bounds}
                check={'status':'nonverbal_not_spoken_text','audio_sha256':take['audio_sha256'],
                       'synthesis_key':u['key'],'intent':intent,'word_verification_applicable':False}
                replaced.append({'chapter':chapter,'id':key,'source_text':u['text'],'source':proof})
            else:audio=(source/take['audio']).resolve()
            if file_digest(audio)!=take['audio_sha256']:raise ValueError('Audio changed during effect assembly')
            relative=Path(os.path.relpath(audio,master)).as_posix()
            rows.append({**u,'audio':relative});takes[key]={**take,'audio':relative};checks[key]={**check,'audio':relative}
        plan={**old,'units':rows,'nonverbal_repair':{'source_plan_sha256':file_digest(source/'render-plan.json'),
                                                 'production_approved':False}}
        write_json(master/'render-plan.json',plan);saved={'plan_sha256':file_digest(master/'render-plan.json'),'takes':takes}
        write_json(master/'receipts.json',saved);write_json(master/'word-checks.json',{'plan_sha256':saved['plan_sha256'],'checks':checks})
        masters.append((master,plan,saved))
    levels=gains(masters)
    for master,plan,receipt in masters:assemble(master,plan,receipt,JOIN_POLICY,levels)
    write_json(folder/'audio-v7/nonverbal-report.json',{'replaced':replaced,'production_approved':False})
    print(json.dumps({'replaced':len(replaced)},ensure_ascii=False))


if __name__=='__main__':main()
