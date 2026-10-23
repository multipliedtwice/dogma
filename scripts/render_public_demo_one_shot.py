"""Render frozen demo text once with pinned Higgs; all recognition/recovery is off."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'runtime')]
import render_public_audiobook_demo as assets
from rimsky.production_higgs_model import resolve_production_higgs_model
from rimsky.util import file_digest

POLICY = 'public-demo-pinned-higgs-one-shot-v1'
SELF = Path(__file__).resolve()
read, sha, checked = assets.read, assets.sha, assets.checked
SAMPLING = dict(temperature=.8, top_k=50, top_p=.95, repetition_penalty=1.1,
                do_sample=True, text_chunk_mode='tag_aware', text_chunk_size=65536)


def write(path, value, immutable=False):
    """Local atomic writes tolerate short Windows reader sharing violations only."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        for attempt in range(10):
            try:
                if immutable and path.exists():
                    if read(path)!=value:raise ValueError('Immutable record differs: '+str(path))
                    return
                os.replace(temporary,path);return
            except OSError as error:
                if getattr(error,'winerror',None) not in (5,32,33) or attempt==9:raise
                time.sleep(min(.25,.025*2**attempt))
    finally:
        try:temporary.unlink(missing_ok=True)
        except OSError:pass  # Preserve the original exception; unique temp is not a public asset.


@contextmanager
def exclusive(output):
    output.mkdir(parents=True, exist_ok=True)
    with (output/'controller.lock').open('a+b') as stream:
        stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:yield
        finally:
            stream.seek(0)
            if os.name == 'nt':msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:fcntl.flock(stream, fcntl.LOCK_UN)


def configure(source, output, prepared_overrides=None, music_volume_scale=.9):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output == source or output.is_relative_to(source):
        raise ValueError('One-shot output must be separate from the frozen source workspace')
    frozen = read(source/'frozen.json')
    assignment, manifest = read(source/'assignment.json'), read(source/'manifest.json')
    if frozen['assignment'] != assignment or frozen['manifest'] != manifest:
        raise ValueError('Input assignment/manifest differs from its frozen preprocessing')
    if assignment.get('language') not in ('Russian','English') or not assignment.get('units'):
        raise ValueError('Explicit bilingual source assignment required')
    ids = set()
    for unit in assignment['units']:
        if not re.fullmatch(r'[A-Za-z0-9_-]+',unit['id']) or unit['id'] in ids:
            raise ValueError('Unsafe/duplicate source unit')
        ids.add(unit['id'])
        text = unit.get('synthesis_text',unit['spoken'])
        if not unit['text'].strip() or not text.strip() or re.search(r'(?:\[\s*|<\|\s*)(?:emotion|prosody|pitch|speed|vocal|style|effect)',text,re.I):
            raise ValueError('Empty text or disabled sound-changing tag')
        if unit['palette_reference'].get('reviewed') is not True:
            raise ValueError('Frozen reference must be human-reviewed')
    if set(manifest.get('sampling',{}))-set(assets.DEFAULT_SAMPLING):
        raise ValueError('Unknown synthesis sampling input')
    overrides=None
    if prepared_overrides is not None:
        path=Path(prepared_overrides).resolve();value=read(path)
        if value.get('schema')!=1 or value.get('human_approval') is not False or not isinstance(value.get('changes'),list):
            raise ValueError('Explicit prepared-input correction file required; no audio approval')
        seen=set();by_id={u['id']:u for u in assignment['units']}
        for change in value['changes']:
            unit=by_id.get(change.get('id'))
            if (unit is None or unit['id'] in seen or change.get('source_text')!=unit['text']
                    or change.get('before')!=unit.get('synthesis_text',unit['spoken'])
                    or not isinstance(change.get('after'),str) or not change['after'].strip() or not change.get('basis')
                    or re.search(r'(?:\[\s*|<\|\s*)(?:emotion|prosody|pitch|speed|vocal|style|effect)',change['after'],re.I)):
                raise ValueError('Prepared correction differs from exact frozen source/native input or enables effects')
            seen.add(unit['id'])
        overrides={'path':str(path),'sha256':file_digest(path),'data':value}
    if not math.isfinite(music_volume_scale) or not 0<music_volume_scale<=1:
        raise ValueError('Music volume scale must be finite in (0,1]')
    config = {'schema':1,'policy':POLICY,'source_generation':sha(frozen),'source':str(source),
              'frozen':frozen,'post_processing':'off','takes_per_unit':1,'bed_rms_dbfs':-30,
              'bed_gap_seconds':4,'implementation_sha256':file_digest(SELF),
              'music_volume_scale':music_volume_scale,'prepared_overrides':overrides,
              'asset_helper_sha256':file_digest(Path(assets.__file__)),
              'player_sha256':file_digest(assets.PLAYER_SOURCE)}
    write(output/'one-shot.json',config,immutable=True)
    return config


def model_guard(manifest):
    pin = resolve_production_higgs_model(ROOT)  # Fresh full checksum before EVERY native session.
    expected = manifest['speech_model']
    if (pin.checkpoint != expected['checkpoint'] or pin.sha256 != expected['sha256']
            or file_digest(pin.manifest_path) != expected['manifest_sha256']
            or file_digest(pin.provenance_path) != expected['provenance_sha256']
            or pin.provenance != expected['provenance']):
        raise ValueError('Frozen production model/provenance differs; fallback forbidden')
    runner = checked(manifest['runner']['path'],manifest['runner']['sha256'])
    if {p.name:file_digest(p) for p in sorted(runner.parent.glob('*.dll'))} != manifest['runner']['dlls']:
        raise ValueError('Native runner DLL inventory changed')
    return pin,runner


def basic_audio(path):
    """Container/finite checks only. No edges, silence, stress, cap or recognition checks."""
    import numpy as np
    import soundfile as sf
    data, rate = sf.read(path,dtype='float32',always_2d=True)
    info = sf.info(path)
    if rate != 24000 or data.shape[1] != 1 or not len(data) or info.subtype != 'PCM_16' or not np.isfinite(data).all():
        raise ValueError('Native output must be nonempty finite mono 24kHz PCM16')
    return {'sample_rate':rate,'channels':1,'frames':len(data),'duration':len(data)/rate,'format_valid':True}


def request_spec(config, unit, index):
    manifest = config['frozen']['manifest'];ref = unit['palette_reference']
    reference = checked(ref['path'],ref['audio_sha256'])
    text = unit.get('synthesis_text',unit['spoken'])
    overlay=config.get('prepared_overrides')
    if overlay is not None:
        checked(overlay['path'],overlay['sha256'])
        for change in overlay['data']['changes']:
            if change['id']==unit['id']:text=change['after']
    request = {**SAMPLING,**manifest.get('sampling',{}),'id':unit['id']+'-one-shot',
               'text':text,'voice_ref':str(reference),'reference_text':ref['text'],
               'max_tokens':min(2048,math.ceil((6+.35*sum(c.isalpha() for c in text))*25)+8),
               'seed':unit.get('seed',manifest.get('seed',202610230)+index*100)%2**31}
    return {'policy':POLICY,'source_generation':config['source_generation'],'source':unit,
            'model':manifest['speech_model'],'runner':manifest['runner'],'reference':ref,
            'language':config['frozen']['assignment']['language'],'request':request,'effects':'disabled','take':0,
            'prepared_overrides_sha256':overlay['sha256'] if overlay else None}


def receipts(output, config):
    rows=[]
    for index,unit in enumerate(config['frozen']['assignment']['units']):
        path=output/'units'/(unit['id']+'.json')
        if path.exists():
            row=read(path)
            if (row['unit']!=unit['id'] or row['take']!=0 or row['spec']!=request_spec(config,unit,index)
                    or row['spec_sha256']!=sha(row['spec']) or row['status'] not in ('rendered','technical_failure')):
                raise ValueError('One-shot receipt source/take differs')
            if row.get('audio'):checked(output/row['audio'],row['audio_sha256'])
            rows.append(row)
        else:rows.append({'unit':unit['id'],'status':'pending'})
    return rows


def checkpoint(output,config,**worker):
    rows=receipts(output,config)
    state={'schema':1,'policy':POLICY,'source_generation':config['source_generation'],
           'post_processing':'off','recognition_status':'not_checked','human_approval':False,
           'render_complete':all(r['status']=='rendered' for r in rows),'units':rows,
           'counts':{name:sum(r['status']==name for r in rows) for name in ('pending','rendered','technical_failure')},
           'updated_unix':time.time(),**worker}
    write(output/'state.json',state)
    print('PROGRESS',state['counts'],flush=True)
    return state


def collect_session(output,session):
    """A reserved physical attempt never gets dispatched again, even after a crash."""
    base=output/'sessions'/session['id']
    for spec in session['specs']:
        unit=spec['source'];target=output/'units'/(unit['id']+'.json')
        if target.exists():continue
        source=base/'native'/(spec['request']['id']+'.wav')
        row={'schema':1,'unit':unit['id'],'take':0,'spec':spec,'spec_sha256':sha(spec),
             'session':session['id'],'recognition_status':'not_checked','human_approval':False}
        try:
            if not source.is_file():raise ValueError('Reserved one-shot session has no WAV; no automatic retry')
            row['format']=basic_audio(source)
            checksum=file_digest(source);asset=output/'assets'/(checksum+'.wav');asset.parent.mkdir(exist_ok=True)
            if not asset.exists():shutil.copyfile(source,asset)
            checked(asset,checksum)
            row.update(status='rendered',audio=asset.relative_to(output).as_posix(),audio_sha256=checksum)
        except (OSError,ValueError,RuntimeError) as error:
            row.update(status='technical_failure',error=str(error))
        write(target,row,immutable=True)


def run_session(output,config,specs,timeout):
    assets.wait_for_audio_resources()
    pin,runner=model_guard(config['frozen']['manifest'])
    for spec in specs:checked(spec['reference']['path'],spec['reference']['audio_sha256'])
    session_id=sha({'policy':POLICY,'specs':specs})
    base=output/'sessions'/session_id;base.mkdir(parents=True,exist_ok=True)
    sequence=base/'request.json';write(sequence,{'requests':[s['request'] for s in specs]},immutable=True)
    command=[str(runner),'--task','tts','--family','higgs_audio_tts','--model',str(pin.path),
             '--backend','cuda','--device','0','--threads','4','--request-sequence',str(sequence),
             '--out-dir',str(base/'native'),'--out-format','pcm16','--metrics']
    session={'id':session_id,'specs':specs,'command':command,'sequence_sha256':file_digest(sequence)}
    start=base/'physical-start.json'
    # Exclusive creation is the physical-attempt budget. Existing starts are resume-only.
    with start.open('x',encoding='utf-8') as stream:json.dump(session,stream,ensure_ascii=False,indent=2)
    process=None;error=None
    try:
        with (base/'native.log').open('ab') as log:
            process=subprocess.Popen(command,cwd=runner.parent,stdout=log,stderr=subprocess.STDOUT,**assets.HIDDEN)
            import psutil
            identity={'pid':process.pid,'create_time':psutil.Process(process.pid).create_time(),'command':command}
            write(base/'worker.json',identity,immutable=True)
            started=time.monotonic()
            while process.poll() is None:
                if time.monotonic()-started>timeout:
                    process.kill();process.wait();raise TimeoutError('Native session timed out; no retry')
                write(output/'worker.json',{'status':'running','session':session_id,**identity,'updated_unix':time.time()})
                time.sleep(1)
            if process.returncode:error='Native exit '+str(process.returncode)
    except (OSError,TimeoutError) as problem:error=str(problem)
    finally:
        if process is not None and process.poll() is None:process.kill();process.wait()
        write(base/'physical-result.json',{'returncode':process.returncode if process else None,'error':error},immutable=True)
        collect_session(output,session)
        checkpoint(output,config,worker_running=False)


def render(output,config,batch_size=32,timeout=1800):
    # Crash recovery only collects already-reserved outputs; it never repeats a take.
    for start in sorted((output/'sessions').glob('*/physical-start.json')):
        session=read(start);worker=start.parent/'worker.json'
        by_id={u['id']:(i,u) for i,u in enumerate(config['frozen']['assignment']['units'])}
        if (session['id']!=sha({'policy':POLICY,'specs':session['specs']}) or start.parent.name!=session['id']
                or any(s!=request_spec(config,*reversed(by_id[s['source']['id']])) for s in session['specs'])
                or file_digest(start.parent/'request.json')!=session['sequence_sha256']
                or read(start.parent/'request.json')!={'requests':[s['request'] for s in session['specs']]}):
            raise ValueError('Reserved physical request differs from frozen one-shot inputs')
        if worker.exists():
            import psutil
            identity=read(worker)
            try:
                process=psutil.Process(identity['pid'])
                if abs(process.create_time()-identity['create_time'])<.01:
                    raise ValueError('Prior native worker still lives; refuse duplicate controller')
            except psutil.NoSuchProcess:pass
        collect_session(output,session)
    units=config['frozen']['assignment']['units']
    while True:
        done={r['unit'] for r in receipts(output,config) if r['status']!='pending'}
        pending=[request_spec(config,u,i) for i,u in enumerate(units) if u['id'] not in done][:batch_size]
        if not pending:break
        run_session(output,config,pending,timeout)
        publish(output,config)
    return checkpoint(output,config,worker_running=False)


def publish(output,config):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    assignment,manifest=config['frozen']['assignment'],config['frozen']['manifest'];rate=24000
    intro,sr=sf.read(assets.opening(assignment,manifest),dtype='float32',always_2d=True)
    if sr!=rate or intro.shape!=(8*rate,2):raise ValueError('Approved opening format changed')
    chunks=[intro];position=8.0;captions=[]
    for unit,row in zip(assignment['units'],receipts(output,config)):
        if row['status']!='rendered':break
        path=checked(output/row['audio'],row['audio_sha256']);basic_audio(path)
        data,sr=sf.read(path,dtype='float32')
        if captions:chunks.append(np.zeros((round(.16*rate),2),np.float32));position+=.16
        captions.append({'id':unit['id'],'source_id':unit['source_id'],'text':unit['text'],
                         'start':position,'end':position+len(data)/rate,'raw_audio_sha256':row['audio_sha256'],
                         'recognition_status':'not_checked'})
        chunks.append(np.repeat(data[:,None],2,axis=1));position+=len(data)/rate
    full=len(captions)==len(assignment['units'])
    if not captions or position-8<300 and not full:return None
    audio=np.concatenate(chunks);music=manifest['music']
    def instrumental(kind):
        data,sr=sf.read(assets.music_asset(music[kind],kind),dtype='float32',always_2d=True)
        if sr!=rate:
            divisor=math.gcd(sr,rate);data=resample_poly(data,rate//divisor,sr//divisor,axis=0)
        return np.repeat(data,2,axis=1) if data.shape[1]==1 else data
    bed=instrumental('bed')[:20*rate].copy();fade=min(2*rate,len(bed)//2)
    bed[:fade]*=np.linspace(0,1,fade)[:,None];bed[-fade:]*=np.linspace(1,0,fade)[:,None]
    volume=config['music_volume_scale']
    bed*=volume*10**(-30/20)/max(1e-9,float(np.sqrt(np.mean(bed**2))))
    for start in range(8*rate,len(audio),24*rate):
        count=min(len(bed),len(audio)-start);audio[start:start+count]+=bed[:count]
    if full:
        ending=instrumental('ending').copy();gain=volume*10**(float(music.get('ending_rms_dbfs',-30))/20)/max(1e-9,float(np.sqrt(np.mean(ending**2))))
        fade_in,fade_out=min(round(1.5*rate),len(ending)),min(7*rate,len(ending))
        ending[:fade_in]*=np.linspace(0,1,fade_in)[:,None];ending[-fade_out:]*=np.linspace(1,0,fade_out)[:,None]
        audio=np.concatenate([audio,ending*gain])
    if not np.isfinite(audio).all() or np.max(np.abs(audio))>=1:raise ValueError('Nonfinite/clipped deliverable; no automatic retake')
    version=sha({'config':sha(config),'captions':captions,'full':full})
    public=output/'public';public.mkdir(exist_ok=True);wav=public/(version+'.wav')
    if not wav.exists():sf.write(wav,audio,rate,subtype='PCM_16')
    mp3=public/(version+'.mp3')
    if not mp3.exists():subprocess.run([manifest['ffmpeg'],'-v','error','-y','-i',str(wav),'-c:a','libmp3lame','-b:a','192k',str(mp3)],check=True,timeout=180,**assets.HIDDEN)
    subprocess.run([manifest['ffmpeg'],'-v','error','-i',str(mp3),'-f','null','-'],check=True,timeout=180,**assets.HIDDEN)
    status={'schema':1,'policy':POLICY,'source_generation':config['source_generation'],'generation':sha(config),'version':version,
            'title':assignment['title'],'language':assignment['language'],'post_processing':'off','recognition_status':'not_checked',
            'machine_verified':False,'human_approval':False,'render_complete':full,'rendered_units':len(captions),'total_units':len(assignment['units']),
            'opening_seconds':8,'speech_end':position,'duration':len(audio)/rate,'captions':captions,
            'mp3':mp3.name,'mp3_sha256':file_digest(mp3),'wav':wav.name,'wav_sha256':file_digest(wav),
            'mix':{'bed_rms_dbfs':-30+20*math.log10(volume),'bed_gap_seconds':4,'music_volume_scale':volume,
                   'opening_unchanged':True,'speech_processing':'Whole native PCM; no trim, fade, normalization or time stretch'},
            'limitation':'One-shot listening render. Recognition, acoustic checks and automatic retakes are disabled; not reviewed.',
            'attribution':'Music: YuE2-3B by Multimodal Art Projection, via audio.cpp. CC BY-NC 4.0, non-commercial use.'}
    write(public/(version+'.json'),status,immutable=True);write(public/'latest.json',status)
    player='player-'+config['player_sha256']+'.js';shutil.copyfile(assets.PLAYER_SOURCE,public/player)
    cfg=json.dumps({'generation':config['source_generation'],'version':version,'captions':captions,'opening_seconds':8,'opening_sha256':manifest['opening']['sha256']},ensure_ascii=False).replace('<','\\u003c')
    rows=''.join('<p data-start="%s" data-end="%s">%s</p>'%(c['start'],c['end'],html.escape(c['text'])) for c in captions)
    markup='<!doctype html><html lang="'+('ru' if assignment['language']=='Russian' else 'en')+'"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+html.escape(assignment['title'])+'</title><style>body{max-width:850px;margin:2rem auto;padding:1rem;background:#171b19;color:#eee;font:18px/1.6 system-ui}audio{width:100%}a{color:#a7d8b5}p[data-start]{cursor:pointer}.active{background:#2c3d2e}</style><h1>'+html.escape(assignment['title'])+'</h1><p>'+status['limitation']+'</p><audio controls src="'+mp3.name+'"></audio><p><a download href="'+mp3.name+'">MP3</a></p>'+rows+'<footer>'+status['attribution']+'</footer><script type="application/json" id="playback-config">'+cfg+'</script><script src="'+player+'"></script></html>'
    (public/'index.html').write_text(markup,encoding='utf-8')
    return status


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--stage',choices=['plan','render','publish','all'],default='all')
    parser.add_argument('--prepared-overrides',type=Path,help='Explicit source-bound listener preparation corrections, before first synthesis')
    parser.add_argument('--music-volume-scale',type=float,default=.9)
    parser.add_argument('--batch-size',type=int,default=32);parser.add_argument('--session-timeout',type=int,default=1800)
    args=parser.parse_args()
    if not 1<=args.batch_size<=64 or not 60<=args.session_timeout<=7200:parser.error('Invalid batch size/timeout')
    output=args.output.resolve()
    with exclusive(output):
        config=configure(args.source,output,args.prepared_overrides,args.music_volume_scale)
        if args.stage=='plan':return 0
        if args.stage in ('render','all'):state=render(output,config,args.batch_size,args.session_timeout)
        else:state=checkpoint(output,config)
        if args.stage in ('publish','all'):publish(output,config)
        write(output/'worker.json',{'status':'stopped','updated_unix':time.time(),'render_complete':state['render_complete']})
        return 1 if state['counts']['technical_failure'] else 0


if __name__=='__main__':raise SystemExit(main())
