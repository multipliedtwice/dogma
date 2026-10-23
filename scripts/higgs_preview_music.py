"""Verified YuE2 overlay for playable evaluation prefixes; speech stays immutable."""
from __future__ import annotations
import json
import shutil
import subprocess
import uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
from rimsky.util import file_digest,write_json,digest
from rimsky.music import RATE
PREVIEW_CUE_KINDS=('opening','chapter','ending')

def read(p):return json.loads(Path(p).read_text('utf-8'))


def build_path(target):
    return target.with_name(target.stem+'.'+uuid.uuid4().hex+'.building'+target.suffix)


def install_asset(temporary,target):
    """Concurrent deterministic mixers must never share a writable asset."""
    sha=file_digest(temporary)
    if target.exists():
        if file_digest(target)!=sha:raise ValueError('Immutable mixed music asset changed')
        temporary.unlink();return
    try:temporary.replace(target)
    except PermissionError:
        if not target.exists() or file_digest(target)!=sha:raise
        temporary.unlink()

def verified_cues():
    # Real-model, independently verified instrumental assets; no fixture audio.
    source=ROOT/'training-work/music/e2e/library/music'
    result={}
    for p in source.glob('*.json'):
        record=read(p);kind=record.get('cue',{}).get('kind');wav=p.with_suffix('.wav')
        if kind not in PREVIEW_CUE_KINDS or record.get('checks',{}).get('vocals')!='none_heard':continue
        if not wav.is_file() or file_digest(wav)!=record.get('sha256'):raise ValueError('Verified music asset changed')
        if record.get('spec',{}).get('family')!='yue2':continue
        result[kind]=(wav,record)
    if set(result)!=set(PREVIEW_CUE_KINDS):raise ValueError('Opening, chapter and ending need verified instrumental assets')
    return result

def attach_legacy_music(chapter_out,cid,state,page,media):
    import soundfile as sf
    import numpy as np
    ready=state['chapters'][cid];cues=verified_cues();overlays=[]
    kinds=['opening' if cid=='c0001' else 'chapter']
    if cid=='c0003' and ready['complete']:kinds.append('ending')
    for kind in kinds:
        source,proof=cues[kind];name='music-'+proof['sha256'][:16]+'.wav';target=media/name
        if not target.exists():shutil.copyfile(source,target)
        if file_digest(target)!=proof['sha256']:raise ValueError('Immutable published music changed')
        samples,rate=sf.read(target,dtype='float32',always_2d=True)
        if rate!=RATE or samples.shape[1]!=2 or not np.isfinite(samples).all():raise ValueError('Invalid music format')
        start=max(0,ready['seconds']-proof['spec']['mix']['accent_after']) if kind=='ending' else 0
        overlays.append({'kind':kind,'start':start,'seconds':len(samples)/rate,'sha256':proof['sha256'],
            'stream':'/character-rb2/full/clips/elirm-iv-mvp/'+name,'source_record_sha256':file_digest(source.with_suffix('.json')),
            'qualification':'verified instrumental YuE2 evaluation cue reused; book-specific direction pending'})
    ready['music']=overlays
    state['music']={'default_on':True,'volume':1.0,'new_generation':'deferred until narration is idle',
        'attribution':'YuE2-3B by m-a-p, CC BY-NC 4.0; audio.cpp GGUF conversion.'}
    speech=media/Path(ready['stream']).name
    if file_digest(speech)!=ready['wav_sha256']:raise ValueError('Speech changed before music export')
    # Never normalize narration to conceal a clipping mix.
    from scipy.signal import resample_poly
    import math
    with sf.SoundFile(speech) as source:
        for cue in overlays:
            music,mr=sf.read(media/Path(cue['stream']).name,dtype='float32',always_2d=True)
            divisor=math.gcd(mr,source.samplerate)
            music=resample_poly(music,source.samplerate//divisor,mr//divisor,axis=0)
            offset=round(cue['start']*source.samplerate);source.seek(min(offset,len(source)))
            voice=source.read(len(music),dtype='float32',always_2d=True)
            if len(voice) and np.max(np.abs(voice+music[:len(voice)]))>=.999:
                raise ValueError('Music overlay would clip narration; speech remains intact')
    # Keep the speech-only download too. Music never shifts the speech timeline.
    mix_spec={'speech_sha256':ready['wav_sha256'],'overlays':overlays,'gain':1.0,
              'normalize':False,'codec':'libmp3lame','bitrate':'128k',
              'implementation_sha256':file_digest(Path(__file__)),
              'ffmpeg_sha256':file_digest(Path('C:/ffmpeg/bin/ffmpeg.exe'))}
    export_key=digest(mix_spec)[:24]
    mp3=page/(cid+'-'+export_key+'-music.mp3')
    if not mp3.exists():
        command=['C:/ffmpeg/bin/ffmpeg.exe','-hide_banner','-loglevel','error','-y','-i',str(speech)]
        filters=[]
        for i,cue in enumerate(overlays,1):
            command+=['-i',str(media/Path(cue['stream']).name)]
            filters.append(f'[{i}:a]adelay={round(cue["start"]*1000)}:all=1[m{i}]')
        labels='[0:a]'+''.join(f'[m{i}]' for i in range(1,len(overlays)+1))
        filters.append(labels+f'amix=inputs={len(overlays)+1}:duration=longest:normalize=0[mixed]')
        temp=mp3.with_suffix('.building.mp3')
        command+=['-filter_complex',';'.join(filters),'-map','[mixed]','-codec:a','libmp3lame','-b:a','128k',str(temp)]
        subprocess.run(command,check=True);temp.replace(mp3)
    ready['audio_with_music']=mp3.name
    write_json(chapter_out/(cid+'.music-overlay.json'),{'speech_sha256':ready['wav_sha256'],'overlays':overlays,
        'mixed_mp3_sha256':file_digest(mp3),'mix_spec':mix_spec,'timing_shift':0,'production_approved':False})
    return state


# Beds are rendered at -38 dBFS, about 10-12 dB under narration. An extra 0.5
# gain left them ~18.6 dB under speech: present in the file, inaudible in use.
MUSIC_GAIN=1.0


def attach_music(chapter_out,cid,state,page,media):
    """Use the app's scene layout and export mixer, retaining immutable speech."""
    from rimsky import music
    from rimsky.export import mix_music,music_filter
    from higgs_trial_io import read_json
    import soundfile as sf
    import numpy as np
    generation=Path(chapter_out).parent;folder=generation/'scene-music-v2'
    # Old evaluations remain readable; requested upgrades never reuse piano cues.
    if not (folder/'manifest.json').exists() and not (generation/'scene-music.request.json').exists():
        return attach_legacy_music(chapter_out,cid,state,page,media)
    ready=state['chapters'][cid]
    state['music']={'schema':2,'default_on':True,'volume':MUSIC_GAIN,
        'style':'quiet ambient medieval folk; lute, wooden flute, soft hand percussion',
        'attribution':'YuE2-3B by m-a-p, CC BY-NC 4.0; audio.cpp GGUF conversion.',
        'status':'pending'}
    if not (folder/'receipts.json').exists():
        ready['music']=[];ready.pop('audio_with_music',None);ready['music_pending']='Generating verified scene beds and ambience.'
        return state
    manifest=read_json(folder/'manifest.json');receipt=read_json(folder/'receipts.json')
    if receipt['manifest_sha256']!=file_digest(folder/'manifest.json'):raise ValueError('Scene music binding changed')
    assets=folder/'assets';takes={};sources={}
    for identifier,row in receipt['takes'].items():
        source=assets/row['audio']
        if file_digest(source)!=row['sha256']:raise ValueError('Verified scene music changed')
        if row['cue']['kind']=='ambience':
            if row['checks'].get('vocals')!='not_applicable_procedural':raise ValueError('Unverified ambience')
        elif row['checks'].get('vocals')!='none_heard':raise ValueError('Music has no independent no-vocals approval')
        takes[identifier]={'audio_key':row['sha256'],'duration':row['seconds']};sources[row['sha256']]=source
    from higgs_published_timeline import read_timeline
    timeline=read_timeline(Path(chapter_out).parent,cid,ready,require_quality=False)
    times={r['id']:r['start'] for r in timeline};layout=manifest['layout']
    scenes=[s for s in layout['scenes'] if s['chapter_id']==cid and s['anchor_segment_id'] in times]
    available=[s for s in scenes if any(f"bed:{s['bed']}:{v}" in takes for v in range(music.BED_VARIANTS))]
    if len(available)!=len(scenes) or not scenes:
        ready['music']=[];ready.pop('audio_with_music',None);ready['music_pending']='Waiting for every scene in the published prefix to have its first verified bed.'
        return state
    ending=None
    if cid=='c0003' and ready['complete']:
        cue=next(c for c in layout['takes'] if c['kind']=='ending')
        if cue['id'] in takes:
            ending={**cue,'anchor_segment_id':timeline[-1]['id'],
                    'start':max(0,ready['seconds']-music.CUES['ending']['accent_after']-timeline[-1]['start'])}
    from rimsky.narration_transitions import source_boundaries,windows,bridge_layers,POLICY
    book_path=generation.parent/'source/book.json'
    boundaries=[]
    if book_path.exists():
        book=read_json(book_path)
        if (generation/'manifest.json').exists() and read_json(generation/'manifest.json')['source_sha256']!=file_digest(book_path):
            raise ValueError('Scene transition source changed')
        units={u['id']:u for path in (generation/cid).glob('chunk-*/assignment.json') for u in read_json(path)['units']}
        timeline=[{**r,'source_id':r.get('source_id',units.get(r['id'],{}).get('source_id'))} for r in timeline]
        blocks=next(c['blocks'] for c in book['chapters'] if c['id']==cid)
        confirmed=read_json(generation/'section-markers.json')['confirmed'] if (generation/'section-markers.json').exists() else []
        boundaries=source_boundaries(timeline,blocks,confirmed)
    transitions=windows(timeline,boundaries,ready['seconds'],ready['complete'] and cid!='c0003')
    layers=bridge_layers(music.scene_layers(scenes,takes,times,ready['seconds'],ending),scenes,takes,times,transitions)
    placed=[{**layer,'path':str(sources[layer['key']]),'gain':MUSIC_GAIN,'kind':layer.get('kind','bed')} for layer in layers]
    tail=0.
    if ending:
        take=takes[ending['id']];at=times[ending['anchor_segment_id']]+ending['start']
        placed.append({'path':str(sources[take['audio_key']]),'at':at,'window':None,'gain':MUSIC_GAIN,'kind':'ending','duration':take['duration']})
        tail=max(0,at+take['duration']-ready['seconds'])
    music_spec={'schema':2,'manifest_sha256':file_digest(folder/'manifest.json'),
        'receipts_sha256':file_digest(folder/'receipts.json'),'scenes':scenes,'placed':placed,
        'speech_seconds':ready['seconds'],'tail':tail,'mixer_sha256':file_digest(ROOT/'runtime/rimsky/export.py'),
        'music_implementation_sha256':file_digest(ROOT/'runtime/rimsky/music.py'),
        'transition_policy':POLICY,'transition_implementation_sha256':file_digest(ROOT/'runtime/rimsky/narration_transitions.py')}
    key=digest(music_spec);track=media/('scene-music-'+key[:24]+'.wav')
    if not track.exists():
        temporary=build_path(track);mix_music(placed,ready['seconds'],tail,temporary);install_asset(temporary,track)
    speech=media/Path(ready['stream']).name
    if file_digest(speech)!=ready['wav_sha256']:raise ValueError('Speech changed before scene music mix')
    # Streaming blocks keep the memory bounded and do not alter speech gain.
    peak=0.
    with sf.SoundFile(speech) as voice,sf.SoundFile(track) as background:
        if voice.samplerate!=background.samplerate or len(background)<len(voice):raise ValueError('Scene music timing mismatch')
        while len(x:=voice.read(24000*30,dtype='float32',always_2d=True)):
            bed=background.read(len(x),dtype='float32',always_2d=True)
            peak=max(peak,float(np.max(np.abs(x+bed))))
    if peak>=.999:raise ValueError('Scene music mix would clip; speech gain remains frozen')
    sha=file_digest(track);export_spec={**music_spec,'music_sha256':sha,'speech_sha256':ready['wav_sha256'],
        'normalize':False,'codec':'libmp3lame','bitrate':'128k','ffmpeg_sha256':file_digest(Path('C:/ffmpeg/bin/ffmpeg.exe'))}
    mp3=page/(cid+'-'+digest(export_spec)[:24]+'-music.mp3')
    if not mp3.exists():
        temp=build_path(mp3)
        subprocess.run(['C:/ffmpeg/bin/ffmpeg.exe','-hide_banner','-loglevel','error','-y','-i',str(speech),
            '-i',str(track),'-filter_complex',music_filter(1),'-map','[mixed]',
            '-codec:a','libmp3lame','-b:a','128k',str(temp)],check=True);install_asset(temp,mp3)
    ready['music']=[{'kind':'scene_bed','start':0,'seconds':ready['seconds'],
        'sha256':sha,'stream':'/character-rb2/full/clips/elirm-iv-mvp/'+track.name,
        'qualification':'Shared app scene layout; independently verified YuE2 phrases and procedural ambience.'}]
    # The same premixed track contains the ending. Give its tail a distinct
    # cue/offset so a paused narration never restarts the bed after the last word.
    if tail:
        tail_name=media/('scene-ending-'+key[:24]+'.wav')
        if not tail_name.exists():
            with sf.SoundFile(track) as f:
                f.seek(round(ready['seconds']*f.samplerate));audio=f.read(dtype='float32',always_2d=True)
                temporary=build_path(tail_name);sf.write(temporary,audio,f.samplerate,subtype='PCM_16');install_asset(temporary,tail_name)
        ready['music'].append({'kind':'ending','start':ready['seconds'],'seconds':tail,
            'sha256':file_digest(tail_name),'stream':'/character-rb2/full/clips/elirm-iv-mvp/'+tail_name.name})
    ready['music_layout']={'scenes':len(scenes),'phrases':len(layers),'crossfade':music.CROSSFADE,
        'transitions':transitions,'transition_policy':POLICY,
        'manifest_sha256':file_digest(folder/'manifest.json'),'verified_takes':len(takes),'planned_takes':len(layout['takes'])}
    ready['audio_with_music']=mp3.name;ready.pop('music_pending',None)
    state['music']['status']='complete' if (folder/'complete.json').exists() else 'more_variants_pending'
    write_json(Path(chapter_out)/(cid+'.music-overlay.json'),{'speech_sha256':ready['wav_sha256'],
        'music_spec':music_spec,'music_sha256':sha,'mixed_mp3_sha256':file_digest(mp3),
        'combined_peak':peak,'timing_shift':0,'production_approved':False})
    return state
