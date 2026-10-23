"""Book-bound preview music through the app's scene-bed planning and YuE2 code."""
from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
from rimsky import music
from rimsky.util import file_digest,write_json,digest
from higgs_trial_io import read_json as read


def prepare(generation):
    from render_higgs_palette_preview import Calls
    book=read(generation.parent/'source/book.json');book['id']=digest({'source':book['source_hash']})[:24]
    folder=generation/'scene-music-v2';folder.mkdir(exist_ok=True)
    calls=Calls();chapter_ids=['c0001','c0002','c0003']
    payload=music.palette_request(book,chapter_ids,music.EXCERPT_CHARS['cli']['palette'])
    palette=calls.call(folder/'direction','palette',music.DIRECTION_SYSTEM['palette'],payload,
                       music.palette_schema(),lambda a:music.checked_palette(a))
    directed={};previous=None
    for cid in chapter_ids:
        request=music.chapter_request(book,cid,palette,previous,music.EXCERPT_CHARS['cli']['chapter'])
        answer=calls.call(folder/'direction',cid,music.DIRECTION_SYSTEM['chapter'],request,
                         music.chapter_schema(request),lambda a:music.checked_chapter(a,request))
        directed[cid]=music.checked_chapter(answer,request);previous=directed[cid]['scenes'][-1]
    segments=[]
    for cid in chapter_ids:
        path=next((generation.parent/'audio-v7'/cid).rglob('render-plan.json'))
        for unit in read(path)['units']:
            segments.append({'id':unit['id'],'source_id':unit['source_id'],'chapter_id':cid,
                'ordinal':len(segments),'duration':0,'delivery':unit.get('mood','calm')})
    direction={'palette':palette,'chapters':directed}
    layout=music.plan_music(book,{'segments':segments,'analysis':{}},direction)
    manifest={'schema':2,'source_sha256':file_digest(generation.parent/'source/book.json'),
        'direction':direction,'layout':layout,'module_sha256':file_digest(ROOT/'runtime/rimsky/music.py'),
        'direction_requests':{p.name:file_digest(p) for p in (folder/'direction').glob('*.request.json')},
        'house_sound':{'genres':music.SIGNATURE_GENRES,'instruments':music.SIGNATURE_INSTRUMENTS},
        'production_approved':False}
    path=folder/'manifest.json'
    # JSON canonicalization converts tuples to lists.
    manifest=json.loads(json.dumps(manifest))
    if path.exists() and read(path)!=manifest:raise ValueError('Frozen scene music changed; use another version')
    write_json(path,manifest)
    print('SCENE MUSIC PLANNED',len(layout['scenes']),'scenes',len(layout['takes']),'takes',flush=True)
    return folder,manifest


def run(generation):
    import soundfile as sf
    from regenerate_higgs_book_trial import wait_for_audio_resources
    folder= generation/'scene-music-v2'
    manifest=read(folder/'manifest.json') if (folder/'manifest.json').exists() else prepare(generation)[1]
    if manifest['source_sha256']!=file_digest(generation.parent/'source/book.json') or manifest['module_sha256']!=file_digest(ROOT/'runtime/rimsky/music.py'):
        raise ValueError('Scene music source or implementation changed')
    pack=music.load_music_pack(ROOT/'resources/music/yue2-3b-q8',{})
    # CPU is the measured safe route on this 6 GB / 16 GB machine. Narration
    # is checkpointed by the supervisor; YuE2 and the verifier never coexist.
    device='cpu';assets=folder/'assets';assets.mkdir(exist_ok=True)
    receipt_path=folder/'receipts.json'
    receipt=read(receipt_path) if receipt_path.exists() else {'manifest_sha256':file_digest(folder/'manifest.json'),'takes':{}}
    if receipt['manifest_sha256']!=file_digest(folder/'manifest.json'):raise ValueError('Music receipt binding changed')
    for cue in manifest['layout']['takes']:
        identifier=cue['id'];spec=music.cue_spec(pack,cue,device)
        old=receipt['takes'].get(identifier)
        if old:
            if (old['spec']!=spec and old.get('requested_spec_sha256')!=digest(spec)) or file_digest(assets/old['audio'])!=old['sha256']:raise ValueError('Existing music take changed')
            continue
        errors=[]
        variants=[(cue,spec,take,None) for take in range(1 if cue['kind']=='ambience' else music.TAKES)]
        if cue['kind']=='bed':
            # Song vocals often enter near the end of the extra 22 s source.
            # Shorter raw requests still supply the full 20 s bed. Every raw
            # waveform must pass the same no-vocals gate; no vocals are muted.
            for stage in (1,2):
                candidate={**cue,'seconds':music.CUES['bed']['seconds'],
                    'seed':(cue['seed']+stage*music.TAKES)%2**31}
                if stage==2:candidate['style']=music.compose_style('bed',cue['mood'])
                actual=music.cue_spec(pack,candidate,device)
                recovery={'stage':stage,'requested_seconds':cue['seconds'],'actual_seconds':candidate['seconds'],
                    'requested_style':cue['style'],'actual_style':candidate['style'],
                    'basis':'Prior source takes failed no-vocals; retain scene mood and house instruments; every replacement is independently checked.'}
                variants.extend((candidate,actual,take,recovery) for take in range(music.TAKES))
        requested_spec=spec
        for candidate,spec,take,recovery in variants:
            key=music.render_key(spec,take);target=assets/(key+'.wav');work=folder/'work'/key;work.mkdir(parents=True,exist_ok=True)
            if (work/'failure.json').exists():
                failure=read(work/'failure.json')
                if failure['spec']!=spec or failure['take']!=take:raise ValueError('Music failure checkpoint changed')
                errors.append(failure['error']);continue
            try:
                if cue['kind']=='ambience':
                    audio=music.synthesize_ambience(cue['ambience'],cue['seed']);checks={'vocals':'not_applicable_procedural'}
                else:
                    wait_for_audio_resources();print('YUE2',identifier,'take',take,flush=True)
                    raw=music.generate(pack,spec,take,work/'raw.wav',lambda:False)
                    audio,rate=sf.read(raw,dtype='float32',always_2d=True)
                    checks=music.audio_checks(audio,rate,candidate['seconds'])
                    subprocess.run([str(ROOT/'training-work/.venv-asr/Scripts/python.exe'),'-X','utf8','-u',
                        str(ROOT/'scripts/verify_higgs_scene_music.py'),'--audio',str(raw),'--output',str(work/'vocals.json')],cwd=ROOT,check=True)
                    proof=read(work/'vocals.json')
                    if proof['audio_sha256']!=file_digest(raw):raise ValueError('Music no-vocals evidence changed')
                    if proof['heard_words']:raise ValueError('Audible words detected in music: '+proof['heard'])
                    checks.update(vocals='none_heard',no_vocals_report_sha256=file_digest(work/'vocals.json'))
                    audio=music.finish(audio,candidate['kind'])
                sf.write(target,audio,music.RATE,subtype='PCM_16')
                row={'sha256':file_digest(target),'audio':target.name,'spec':spec,'take':take,'cue':candidate,
                     'checks':checks,'seconds':len(audio)/music.RATE,'previous_failures':errors,
                     'requested_spec_sha256':digest(requested_spec),'recovery':recovery}
                write_json(target.with_suffix('.json'),row);receipt['takes'][identifier]=row
                write_json(receipt_path,receipt)
                refresh_preview(generation)
                print('MUSIC READY',identifier,len(receipt['takes']),'/',len(manifest['layout']['takes']),flush=True)
                break
            except (ValueError,music.AppError,subprocess.CalledProcessError) as exc:
                errors.append(str(exc));write_json(work/'failure.json',{'error':str(exc),'spec':spec,'take':take})
        else:
            write_json(folder/'held.json',{'cue_id':identifier,'errors':errors})
            raise ValueError('Music take held after bounded recovery: '+identifier)
    write_json(folder/'complete.json',{'manifest_sha256':file_digest(folder/'manifest.json'),
        'receipts_sha256':file_digest(receipt_path),'takes':len(receipt['takes']),'no_vocals_verified':True})
    refresh_preview(generation)


def refresh_preview(generation):
    from render_higgs_palette_preview import PAGE,MEDIA,page
    from higgs_preview_music import attach_music
    state=read(generation/'progress.json')
    for cid in state['chapters']:
        attach_music(generation/cid,cid,state,PAGE,MEDIA)
    write_json(generation/'progress.json',state);write_json(PAGE/'progress.json',state);page()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--generation',type=Path,required=True)
    p.add_argument('--run',action='store_true');args=p.parse_args()
    if args.run:run(args.generation.resolve())
    else:prepare(args.generation.resolve())
