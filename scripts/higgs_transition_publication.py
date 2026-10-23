"""Add structural pauses to a published evaluation without regenerating speech."""
import copy
from pathlib import Path
from rimsky.util import file_digest,write_json
from rimsky.narration_transitions import POLICY,source_boundaries
from higgs_trial_io import read_json as read
from higgs_published_timeline import read_timeline,commit_publication

def publish_transition_pauses(generation,state):
    import soundfile as sf
    import numpy as np
    import subprocess
    from render_higgs_palette_preview import PAGE,MEDIA
    book_path=generation.parent/'source/book.json'
    if file_digest(book_path)!=read(generation/'manifest.json')['source_sha256']:
        raise ValueError('Transition publication source changed')
    book=read(book_path)
    for cid,original in list(state['chapters'].items()):
        if original.get('transition_policy')==POLICY:continue
        rows=copy.deepcopy(read_timeline(generation,cid,original))
        units={u['id']:u for path in (generation/cid).glob('chunk-*/assignment.json') for u in read(path)['units']}
        for row in rows:
            if not row.get('source_id'):
                if row['id'] not in units:raise ValueError('Published transition has no bound source: '+row['id'])
                row['source_id']=units[row['id']]['source_id']
        blocks=next(c['blocks'] for c in book['chapters'] if c['id']==cid)
        boundaries=source_boundaries(rows,blocks);by_id={r['id']:r for r in rows}
        source=MEDIA/Path(original['stream']).name
        if file_digest(source)!=original['wav_sha256']:raise ValueError('Speech changed before transition edit')
        x,rate=sf.read(source,dtype='int16');insertions=[]
        for b in boundaries:
            before=by_id[b['before']];after=by_id[b['after']]
            count=max(0,round((POLICY['scene_seconds']-.08-(after['start']-before['end']))*rate))
            if count:insertions.append((round(before['end']*rate),count))
        if original['complete'] and cid!='c0003':
            count=max(0,round((POLICY['chapter_seconds']-(original['seconds']-rows[-1]['end']))*rate))
            if count:insertions.append((len(x),count))
        if not insertions:
            ready={**original,'transition_policy':POLICY}
            commit_publication(generation,cid,ready,rows,state);continue
        scratch=generation/cid/'transition-publication';scratch.mkdir(parents=True,exist_ok=True)
        building=scratch/'pauses.wav';cursor=0
        with sf.SoundFile(building,'w',samplerate=rate,channels=1,subtype='PCM_16') as destination:
            for position,count in sorted(insertions):
                destination.write(x[cursor:position]);destination.write(np.zeros(count,dtype='int16'));cursor=position
            destination.write(x[cursor:])
        for row in rows:
            start=round(row['start']*rate);end=round(row['end']*rate)
            row['start']+=sum(n for p,n in insertions if p<=start)/rate
            row['end']+=sum(n for p,n in insertions if p<end)/rate
        new,rr=sf.read(building,dtype='int16');old_rows=read_timeline(generation,cid,original)
        for a,b in zip(old_rows,rows):
            if not np.array_equal(x[round(a['start']*rate):round(a['end']*rate)],
                                  new[round(b['start']*rate):round(b['end']*rate)]):
                raise ValueError('Transition edit changed spoken PCM: '+a['id'])
        sha=file_digest(building);wave=MEDIA/(cid+'-'+sha[:12]+'.wav')
        if not wave.exists():wave.write_bytes(building.read_bytes())
        if file_digest(wave)!=sha:raise ValueError('Immutable transition speech changed')
        mp3=PAGE/(cid+'-'+sha[:12]+'.mp3')
        if not mp3.exists():
            temp=mp3.with_suffix('.building.mp3')
            subprocess.run(['C:/ffmpeg/bin/ffmpeg.exe','-hide_banner','-loglevel','error','-y','-i',str(wave),
                '-codec:a','libmp3lame','-b:a','128k',str(temp)],check=True);temp.replace(mp3)
        ready={**original,'stream':'/character-rb2/full/clips/elirm-iv-mvp/'+wave.name,'audio':mp3.name,
            'wav_sha256':sha,'seconds':len(new)/rr,'transition_policy':POLICY}
        ready.pop('music',None);ready.pop('audio_with_music',None)
        commit_publication(generation,cid,ready,rows,state)
        write_json(scratch/(sha+'.json'),{'before_sha256':original['wav_sha256'],'after_sha256':sha,
            'source_sha256':file_digest(book_path),'policy':POLICY,'boundaries':boundaries,
            'inserted_samples':insertions,'all_spoken_pcm_preserved':True,'word_flags_unchanged':True})
        print('PUBLISHED STRUCTURAL PAUSES',cid,len(boundaries),flush=True)
