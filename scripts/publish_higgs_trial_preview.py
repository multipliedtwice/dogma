"""Publish an immutable contiguous opening while full chapters render."""
from __future__ import annotations
import argparse
import itertools
import json
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json
from higgs_trial_io import read_json

def publish(folder):
    import soundfile as sf
    import numpy as np
    plan=read_json(folder/'render-plan.json');receipt=read_json(folder/'receipts.json')
    rows=list(itertools.takewhile(lambda u:u['id'] in receipt['takes'],plan['units']))
    if len(rows)<len(plan['units']):
        next_source=plan['units'][len(rows)]['source_id']
        while rows and rows[-1]['source_id']==next_source:rows.pop()
    if not rows:raise ValueError('No complete opening paragraph yet')
    destination=ROOT/'training-work/character-rb2-full/fine-tune-all/elirm-iv-mvp/opening-01'
    audio_destination=ROOT/'training-work/character-rb2/full/clips/elirm-iv-mvp'
    destination.mkdir(parents=True,exist_ok=True);audio_destination.mkdir(parents=True,exist_ok=True)
    wav=audio_destination/'opening-01.wav'
    if wav.exists():raise ValueError('Opening preview is immutable and already published')
    duration=0;previous=None
    with sf.SoundFile(wav,'w',samplerate=24000,channels=1,subtype='PCM_16') as target:
        for row in rows:
            take=receipt['takes'][row['id']];path=folder/take['audio']
            if file_digest(path)!=take['audio_sha256']:raise ValueError('Preview take changed')
            x,rate=sf.read(path,dtype='float32')
            gap=0 if previous is None else .8 if previous['kind']=='heading' else .34 if previous['source_id']!=row['source_id'] else .13
            target.write(np.zeros(round(gap*rate),dtype='float32'));target.write(x)
            duration+=gap+len(x)/rate;previous=row
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(wav),'-codec:a','libmp3lame',
                    '-b:a','128k',str(destination/'opening-01.mp3')],check=True)
    write_json(destination/'manifest.json',{'purpose':'opening_preview','production_approved':False,
            'render_plan_sha256':file_digest(folder/'render-plan.json'),'seconds':duration,'units':[r['id'] for r in rows],
            'wav_sha256':file_digest(wav),'mp3_sha256':file_digest(destination/'opening-01.mp3')})
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Элирм IV — начало</title><style>body{font:20px system-ui;max-width:800px;margin:40px auto;padding:18px;background:#f7f8f4;color:#172a25}audio{width:100%;margin:20px 0}a{color:#245d46}</style>
<h1>Элирм IV · глава 1</h1><p>Первые '''+f'{duration/60:.1f}'+''' минуты. Полные главы продолжают готовиться.</p>
<audio controls preload="metadata" src="/character-rb2/full/clips/elirm-iv-mvp/opening-01.wav"></audio>
<p><a href="opening-01.mp3" download>Скачать MP3</a></p><p><a href="../">Полные главы</a></p>
<small>Предварительное прослушивание дообученной версии Higgs. Качество голосов и произношения ещё проверяется.</small></html>'''
    (destination/'index.html').write_text(page,encoding='utf-8')
    return {'minutes':round(duration/60,1),'units':len(rows),'page':str(destination)}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True)
    a=p.parse_args();print(json.dumps(publish(a.folder.resolve())))
