"""Independently verify a current unchecked public demo; CPU and private proof only.

No preparation, publication, provider, recognition or speech-model invocation.
The compact integrity receipt is consumed by github_pages.export_one_shot;
its detailed sibling report stays private.
"""
from __future__ import annotations
import argparse
import copy
import io
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import generate_public_demo_current as producer
import github_pages as pages

POLICY='public-demo-one-shot-integrity-v1'
RATE=24000
CHECKS=('source_coverage','native_pcm','opening','music','mp3_decode')
RECOVERY='exact-cuda-warmup-log-interleaving-recovery-v1'
CUDA_NOISE=b'ggml_backend_cuda_graph_compute: CUDA graph warmup complete\r\n'


def unchecked(value):
    return all(type(value.get(key))is type(expected)and value.get(key)==expected
        for key,expected in producer.UNCHECKED.items())


def strict_coverage(book,assignment):
    """Cover every source character except whitespace; retain exact ordering."""
    blocks=[b for c in book['chapters'] for b in c['blocks'] if b.get('text','').strip()]
    by_id={b['id']:(index,b['text']) for index,b in enumerate(blocks)}
    if len(by_id)!=len(blocks):raise ValueError('Duplicate source block identity')
    intervals={key:[] for key in by_id};previous=(-1,-1)
    for unit in assignment['units']:
        index,text=by_id[unit['source_id']];start,end=unit['start'],unit['end']
        if (type(start)is not int or type(end)is not int or not 0<=start<end<=len(text)
                or text[start:end]!=unit['text']or(index,start)<previous):
            raise ValueError('Source unit span/order changed')
        intervals[unit['source_id']].append((start,end));previous=(index,end)
    for key,(_,text) in by_id.items():
        offset=0
        for start,end in intervals[key]:
            if start<offset or any(not c.isspace()for c in text[offset:start]):
                raise ValueError('Overlapping or unexplained non-whitespace source gap: '+key)
            offset=end
        if any(not c.isspace()for c in text[offset:]):
            raise ValueError('Uncovered source tail: '+key)
    return {'blocks':len(blocks),'units':len(assignment['units']),
        'characters':sum(len(b['text'])for b in blocks),'gap_policy':'whitespace-only'}


def dead(identity):
    try:process=producer.psutil.Process(identity['pid'])
    except producer.psutil.NoSuchProcess:return
    if abs(process.create_time()-identity['create_time'])<.01:
        raise ValueError('A bound native/controller owner remains live')


def recovery_binding(source,output,pointer,path,checksum):
    if (path is None)!=(checksum is None):raise ValueError('Recovery config and reviewed checksum must be supplied together')
    if path is None:return None
    path=pages.safe(path);config=pages.read(producer.checked(path,checksum))
    if (config.get('policy')!=RECOVERY or config.get('original_job')!=pointer
            or Path(config.get('command_scope','')).resolve()!=output.resolve()or source.resolve()!=output.resolve()
            or config.get('producer_sha256')!=producer.file_digest(Path(producer.__file__))
            or not re.fullmatch(pages.HASH,str(config.get('implementation_sha256')))):
        raise ValueError('Recovery anchor belongs to another source/book/job/producer')
    return {'path':path,'sha256':checksum,'config':config}


def completion_log(job,row,recovery,cache):
    """Remove only exact CUDA diagnostics, with an externally anchored parser."""
    session=pages.safe(job/'sessions'/row['session']);raw=(session/'native.log').read_bytes()
    if pages.sha(raw)!=row['log_sha256']:raise ValueError('Raw native log changed')
    proof_path=session/'log-parser-recovery.json'
    if not proof_path.exists()and not any(k in row for k in('log_parser_recovery_path','log_parser_recovery_sha256')):
        return raw.decode('utf8',errors='replace')
    if recovery is None:raise ValueError('Parser recovery requires its reviewed config/hash anchor')
    if (not isinstance(row.get('log_parser_recovery_path'),str)
            or pages.safe(job/row['log_parser_recovery_path']).resolve()!=proof_path.resolve()):
        raise ValueError('Recovered row is not bound to its exact session parser proof')
    proof=pages.read(producer.checked(proof_path,row.get('log_parser_recovery_sha256')))
    if row['session']in cache:
        checksum,text=cache[row['session']]
        if checksum!=row['log_parser_recovery_sha256']:raise ValueError('Recovered rows disagree on parser proof')
        return text
    start=pages.read(session/'physical-start.json');terminal=pages.read(session/'physical-result.json')
    if terminal!={'returncode':0,'error':None}:raise ValueError('Diagnostic normalization requires clean native exit zero')
    dead(pages.read(session/'worker.json'))
    spans=[];chunks=[];offset=0
    while True:
        found=raw.find(CUDA_NOISE,offset)
        if found<0:chunks.append(raw[offset:]);break
        chunks.append(raw[offset:found]);end=found+len(CUDA_NOISE)
        spans.append({'start':found,'end':end,'bytes_hex':CUDA_NOISE.hex()});offset=end
    if not spans:raise ValueError('No exact known CUDA diagnostic bytes exist to normalize')
    clean=b''.join(chunks);text=clean.decode('utf8');completed=[]
    for index,spec in enumerate(start['specs']):
        identifier=spec['request']['id'];wav=session/'native'/(identifier+'.wav')
        for pattern in(r'^request_index='+str(index)+r'\r?$',r'^request_id='+re.escape(identifier)+r'\r?$',
                        r'^audio_out='+re.escape(str(wav))+r'\r?$'):
            if len(re.findall(pattern,text,re.M))!=1:raise ValueError('Normalized native request/path completion is not unique')
        def metric(name):
            values=re.findall(r'^metrics\['+re.escape(identifier)+r'\]\.'+re.escape(name)+r'=([^\r\n]+)\r?$',text,re.M)
            if len(values)!=1:raise ValueError('Normalized native completion metric is missing or duplicated')
            return values[0]
        fmt=producer.native.basic_audio(wav)
        if (metric('sample_rate')!='24000'or metric('channels')!='1'or metric('audio_duration_source')!='output'
                or not math.isfinite(float(metric('audio_duration_ms')))
                or abs(float(metric('audio_duration_ms'))-fmt['duration']*1000)>.001
                or not math.isfinite(float(metric('wall_ms')))):
            raise ValueError('Normalized native output metrics differ from actual PCM')
        completed.append({'unit':spec['source']['id'],'request_sha256':producer.digest(spec['request']),
            'audio':str(wav.relative_to(session)),'audio_sha256':producer.file_digest(wav),'format':fmt})
    expected={'policy':RECOVERY,'parser_sha256':recovery['config']['implementation_sha256'],
        'producer_sha256':recovery['config']['producer_sha256'],'physical_start_sha256':producer.file_digest(session/'physical-start.json'),
        'physical_result_sha256':producer.file_digest(session/'physical-result.json'),'log_sha256':pages.sha(raw),
        'normalized_log_sha256':pages.sha(clean),'removed_spans':spans,'completed':completed,
        'post_processing':'off','recognition_status':'not_checked'}
    if producer.digest(proof)!=producer.digest(expected):raise ValueError('Exact diagnostic spans/parser/native proof differs')
    cache[row['session']]=(row['log_parser_recovery_sha256'],text)
    return text


def recovery_terminal(supervision,recovery):
    if recovery is None:
        if any(key.startswith('recovery_')for key in supervision):raise ValueError('Unanchored recovery controller')
        return None
    config=recovery['config'];command=supervision['worker']['command']
    if (supervision.get('recovery_policy')!=RECOVERY or supervision.get('recovery_config_sha256')!=recovery['sha256']
            or len(command)!=9 or command[1:4]!=['-X','utf8','-u']or command[5:8]!=['--stage','render','--session-timeout']
            or str(config.get('session_timeout'))!=command[8]):
        raise ValueError('Recovery supervision/command is not the reviewed controller')
    wrapper=pages.safe(command[4]);producer.checked(wrapper,config['implementation_sha256'])
    owner=pages.read(recovery['path'].parent/'worker.json');terminal=pages.read(recovery['path'].parent/'exit.json')
    if owner!=supervision['worker']or terminal.get('worker')!=owner or terminal.get('exit_code')!=0:
        raise ValueError('Recovery outer terminal does not bind the stopped controller')
    dead(owner)
    return {'config_sha256':recovery['sha256'],'implementation_sha256':config['implementation_sha256'],
        'terminal_sha256':producer.file_digest(recovery['path'].parent/'exit.json')}


def audio(path,channels=None):
    info=sf.info(path);data,rate=sf.read(path,dtype='float32',always_2d=True)
    if (not len(data)or info.subtype!='PCM_16'or not np.isfinite(data).all()
            or channels is not None and data.shape[1]!=channels):
        raise ValueError('Invalid finite PCM16 audio container')
    return data,rate


def instrumental(path):
    data,rate=audio(path)
    if data.shape[1]not in(1,2):raise ValueError('Music must be mono or stereo')
    if rate!=RATE:
        divisor=math.gcd(rate,RATE);data=resample_poly(data,RATE//divisor,rate//divisor,axis=0)
    return np.repeat(data,2,axis=1)if data.shape[1]==1 else data


def reconstruct(assignment,rows,job,intro_path,bed_path,ending_path):
    """Independent exact source-PCM layout; no join conditioning or speech DSP."""
    intro,rate=audio(intro_path,2)
    if rate!=RATE or intro.shape!=(8*RATE,2):raise ValueError('Opening must be exact eight-second stereo 24kHz PCM16')
    speech=[intro];captions=[];position=8*RATE
    for unit,row in zip(assignment['units'],rows,strict=True):
        chosen=row['selected']
        if chosen is None:raise ValueError('Missing completed native take')
        path=pages.safe(job/chosen['audio'])
        producer.checked(path,chosen['audio_sha256']);raw,rate=audio(path,1)
        if rate!=RATE:raise ValueError('Native speech rate changed')
        if captions:speech.append(np.zeros((3840,2),dtype=np.float32));position+=3840
        captions.append({'id':unit['id'],'source_id':unit['source_id'],'text':unit['text'],
            'start':position/RATE,'end':(position+len(raw))/RATE,'raw_audio_sha256':chosen['audio_sha256'],
            'recognition_status':'not_checked'})
        speech.append(np.repeat(raw,2,axis=1));position+=len(raw)
    mixed=np.concatenate(speech);speech_end=position/RATE
    bed=instrumental(bed_path)[:20*RATE].copy()
    ending=instrumental(ending_path).copy()
    if len(bed)!=20*RATE:raise ValueError('Known music must contain the complete 20-second phrase')
    fade=2*RATE
    envelope=np.ones(len(bed));envelope[:fade]=np.linspace(0,1,fade);envelope[-fade:]=np.linspace(1,0,fade)
    # Match float32 stage boundaries, not a different float64 normalization.
    bed[:fade]*=envelope[:fade,None];bed[-fade:]*=envelope[-fade:,None]
    bed_rms=float(np.sqrt(np.mean(bed**2)))
    if bed_rms<=1e-9:raise ValueError('Silent music phrase has no valid normalization')
    bed*=.9*10**(-30/20)/bed_rms
    for start in range(8*RATE,len(mixed),24*RATE):
        count=min(len(bed),len(mixed)-start);mixed[start:start+count]+=bed[:count]
    ending_rms=float(np.sqrt(np.mean(ending**2)))
    if ending_rms<=1e-9:raise ValueError('Silent ending has no valid normalization')
    ending_gain=.9*10**(-30/20)/ending_rms
    fade_in=min(36000,len(ending));fade_out=min(168000,len(ending))
    ending[:fade_in]*=np.linspace(0,1,fade_in)[:,None]
    ending[-fade_out:]*=np.linspace(1,0,fade_out)[:,None]
    mixed=np.concatenate((mixed,ending*ending_gain))
    if not np.isfinite(mixed).all()or np.max(np.abs(mixed))>=1:raise ValueError('Nonfinite or clipped reconstructed mix')
    return mixed,captions,{'speech_end':speech_end,'duration':len(mixed)/RATE,'opening_seconds':8,
        'music_volume_scale':.9,'bed_rms_dbfs':-30+20*math.log10(.9),'bed_gap_seconds':4,
        'ending_seconds':len(ending)/RATE,'ending_nominal_seconds':24}


def compare_pcm(expected,path):
    info=sf.info(path);actual,rate=sf.read(path,dtype='int16',always_2d=True)
    if rate!=RATE or info.subtype!='PCM_16'or actual.shape!=expected.shape:
        raise ValueError('Published WAV shape/rate/format differs from complete reconstructed PCM')
    peak=0
    for start in range(0,len(actual),RATE*30):
        reference=np.rint(expected[start:start+RATE*30].astype(np.float64)*32768).astype(np.int32)
        difference=np.abs(actual[start:start+RATE*30].astype(np.int32)-reference)
        peak=max(peak,int(difference.max(initial=0)))
        if peak>1:raise ValueError('Published WAV differs from raw speech/opening/music by more than one LSB')
    return {'frames':len(actual),'sample_rate':rate,'channels':2,'max_error_lsb':peak}


def captions_match(actual,expected):
    if not isinstance(actual,list)or len(actual)!=len(expected):return False
    for left,right in zip(actual,expected,strict=True):
        if not isinstance(left,dict)or set(left)!=set(right):return False
        for key,value in right.items():
            if key in('start','end'):
                if type(left[key])not in(int,float)or not math.isfinite(left[key])or abs(left[key]-value)>1e-7:return False
            elif left[key]!=value:return False
    return True


def verify_mp3(ffmpeg,wav,mp3,duration):
    """Bind compressed media to this WAV, then actually decode its container."""
    ffmpeg=pages.safe(ffmpeg);ffmpeg_sha=producer.file_digest(ffmpeg)
    version=subprocess.run([str(ffmpeg),'-version'],check=True,capture_output=True,timeout=30,
        **producer.native.assets.HIDDEN).stdout.decode('utf8',errors='replace').splitlines()[0]
    with tempfile.TemporaryDirectory(prefix='dogma-mp3-verify-')as folder:
        encoded=Path(folder)/'verified.mp3'
        subprocess.run([str(ffmpeg),'-v','error','-y','-i',str(wav),'-c:a','libmp3lame','-b:a','192k',str(encoded)],
            check=True,capture_output=True,timeout=180,**producer.native.assets.HIDDEN)
        if producer.file_digest(encoded)!=producer.file_digest(mp3):
            raise ValueError('MP3 is not the deterministic encoding of the validated current WAV')
    result=subprocess.run([str(ffmpeg),'-v','error','-i',str(mp3),'-map','0:a:0','-c:a','pcm_s16le','-f','wav','pipe:1'],
        check=True,capture_output=True,timeout=180,**producer.native.assets.HIDDEN)
    if result.stderr.strip():raise ValueError('MP3 decoder reported errors')
    decoded,rate=sf.read(io.BytesIO(result.stdout),dtype='int16',always_2d=True)
    if rate!=RATE or decoded.shape[1]!=2 or not len(decoded)or abs(len(decoded)/rate-duration)>.1:
        raise ValueError('Decoded MP3 duration/channel/rate differs')
    if producer.file_digest(ffmpeg)!=ffmpeg_sha:raise ValueError('Verification ffmpeg changed during decode')
    return {'decoded_frames':len(decoded),'sample_rate':rate,'channels':2,'wav_encoding_identical':True,
        'ffmpeg_sha256':ffmpeg_sha,'ffmpeg_version':version}


def verify(source,output,*,recovery_config=None,recovery_config_sha256=None):
    source=pages.safe(source);output=pages.safe(output)
    pointer_path=output/'current-job.json';pointer_raw=pointer_path.read_bytes();pointer=json.loads(pointer_raw)
    key=pointer.get('job')
    if not isinstance(key,str)or not re.fullmatch(pages.HASH,key):raise ValueError('Invalid current job identity')
    job=pages.safe(output/'jobs'/key)
    for path in job.rglob('*'):pages.safe(path)
    config_path=job/'config.json';config_raw=config_path.read_bytes();config=json.loads(config_raw)
    if pointer!={'job':producer.digest(config),'config_sha256':producer.digest(config)}:
        raise ValueError('Current job/config digest differs')
    recovery=recovery_binding(source,output,pointer,recovery_config,recovery_config_sha256)
    for name in('pronunciation','analysis','prepared'):
        for path in(source/name).rglob('*'):pages.safe(path)
    assignment=producer.validate_preparation(source,fresh_human=False)
    frozen=pages.read(source/'frozen.json');manifest=frozen['manifest']
    if (config.get('policy')!=producer.POLICY or config.get('post_processing')!='off'
            or config.get('dependencies')!=producer.pins()or config.get('frozen')!=frozen
            or frozen['assignment']!=assignment or config.get('source_generation')!=producer.digest(frozen)
            or frozen!={'assignment':pages.read(source/'assignment.json'),'manifest':pages.read(source/'manifest.json')}
            or config.get('music_volume_scale')!=.9 or manifest.get('sampling')!=producer.SAMPLING
            or assignment.get('effects_policy')!='disabled'or manifest.get('effects_policy')!='disabled'):
        raise ValueError('Frozen source/preparation/model policy differs')
    book=producer.legacy.verify_assignment_source(source,assignment);coverage=strict_coverage(book,assignment)
    copied=copy.deepcopy(assignment['units']);structure=producer.legacy.attach_structure(source,copied,book)
    if structure!=assignment['analysis']['speech_structure']or any(a['analysis_parts']!=b['analysis_parts']for a,b in zip(copied,assignment['units'],strict=True)):
        raise ValueError('Frozen speech structure does not cover exact source characters')
    producer.native.model_guard(manifest) # One full model/runner/provenance/DLL check; no model invocation.
    pinned_paths=[Path(manifest['speech_model']['path']),Path(manifest['runner']['path'])]
    pinned_paths.extend(Path(manifest['runner']['path']).parent/name for name in manifest['runner']['dlls'])
    def file_identity(path):
        info=path.stat();return(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
    stats={str(p):file_identity(p)for p in pinned_paths}
    references={}
    for unit in assignment['units']:
        ref=unit['palette_reference']
        if ref.get('reviewed')is not True or ref.get('voice_family')!=unit['voice']or ref.get('mood')!=unit['mood']:
            raise ValueError('Reference is not the frozen reviewed voice/mood')
        path=pages.safe(ref['path']);identity=(str(path),ref['audio_sha256'])
        if identity not in references:
            producer.checked(path,ref['audio_sha256']);data,rate=audio(path,1)
            if rate!=RATE:raise ValueError('Reference sample rate changed')
            references[identity]={'sha256':ref['audio_sha256'],'frames':len(data)}
    rows=producer.selected(job,config)
    if any(row['selected']is None for row in rows):raise ValueError('Not all source units have a completed native take')
    if any(not unchecked(attempt)for row in rows for attempt in row['history']):
        raise ValueError('Native history misstates its unchecked mode')
    sessions=[];by_id={u['id']:(i,u)for i,u in enumerate(assignment['units'])}
    for start_path in sorted((job/'sessions').glob('*/physical-start.json')):
        session=start_path.parent;start=pages.read(start_path);terminal=pages.read(session/'physical-result.json')
        if session.name!=producer.digest({'specs':start['specs'],'launch':start['launch']}):raise ValueError('Native session reservation digest differs')
        if start['command']!=producer.native_command(config,session):raise ValueError('Native runner/model/output command changed')
        expected=[]
        for spec in start['specs']:
            index,unit=by_id[spec['source']['id']];expected.append(producer.request(config,unit,index,spec['attempt']))
        if start['specs']!=expected:raise ValueError('Physical request/source/seed/reference changed')
        sequence=pages.read(producer.checked(session/'request.json',start['sequence_sha256']))
        if sequence!={'requests':[s['request']for s in expected]}:raise ValueError('Native request sequence changed')
        worker_path=session/'worker.json'
        if worker_path.exists():
            worker=pages.read(worker_path);dead(worker)
            if worker['command']!=start['command']:raise ValueError('Native worker launch record differs')
        elif terminal.get('returncode')is not None or not terminal.get('error')or list((session/'native').glob('*.wav')):
            raise ValueError('Unknown native owner without proven pre-launch failure')
        sessions.append({'session':session.name,'start_sha256':producer.file_digest(start_path),
            'terminal_sha256':producer.file_digest(session/'physical-result.json'),'returncode':terminal.get('returncode'),
            'error':terminal.get('error'),'reserved_requests':len(expected)})
    bindings=[];parser_logs={}
    for row in rows:
        chosen=row['selected'];receipt=job/'attempts'/row['unit']/f"{chosen['attempt']}.json"
        session=job/'sessions'/chosen['session'];log=completion_log(job,chosen,recovery,parser_logs)
        raw=job/chosen['audio'];request_id=chosen['spec']['request']['id']
        if f'audio_out={raw}'not in log or f'metrics[{request_id}].audio_duration_ms='not in log:
            raise ValueError('Selected native take lacks real completion markers')
        bindings.append({'id':row['unit'],'receipt_sha256':producer.file_digest(receipt),'audio_sha256':chosen['audio_sha256']})
    public=pages.safe(output/'public');latest_raw=(public/'latest.json').read_bytes();latest=json.loads(latest_raw)
    total=len(rows);attempts=sum(len(row['history'])for row in rows);retries=attempts-total
    if (not unchecked(latest)
            or latest.get('policy')!=producer.POLICY or latest.get('render_complete')is not True
            or latest.get('source_generation')!=config['source_generation']or latest.get('total_units')!=total
            or latest.get('rendered_units')!=total or latest.get('physical_native_attempts')!=attempts or latest.get('technical_retries')!=retries
            or any(latest.get(k)!=assignment[k]for k in('title','author','language'))):
        raise ValueError('Completed publication/attempt/unchecked disclosure differs')
    version=latest.get('version')
    if not isinstance(version,str)or not re.fullmatch(pages.HASH,version)or pages.read(public/(version+'.json'))!=latest:
        raise ValueError('Latest immutable version receipt differs')
    progress=pages.read(output/'progress.json');supervision=pages.read(output/'supervision.json')
    if (not unchecked(progress)or not unchecked(supervision)
            or progress.get('render_complete')is not True or progress.get('counts',{}).get('rendered')!=total
            or progress['counts'].get('failed')!=0 or supervision.get('worker_running')is not False
            or supervision.get('stage')!='render'or supervision.get('exit_code')!=0):
        raise ValueError('Render/controller completion is not terminal')
    dead(supervision['worker'])
    recovery_owner=recovery_terminal(supervision,recovery)
    intro=pages.safe(producer.native.assets.opening(assignment,manifest))
    bed=pages.safe(producer.native.assets.music_asset(manifest['music']['bed'],'bed'))
    ending=pages.safe(producer.native.assets.music_asset(manifest['music']['ending'],'ending'))
    expected,captions,timing=reconstruct(assignment,rows,job,intro,bed,ending)
    if (not captions_match(latest.get('captions'),captions)or abs(latest.get('duration',0)-timing['duration'])>1/RATE
            or abs(latest.get('speech_end',0)-timing['speech_end'])>1/RATE or latest.get('opening_seconds')!=8
            or latest.get('mix')!={k:timing[k]for k in('bed_rms_dbfs','bed_gap_seconds','music_volume_scale')}|{'opening_unchanged':True}
            or latest['version']!=producer.digest({'job':producer.digest(config),'captions':latest['captions'],'full':True})):
        raise ValueError('Caption/mix/timeline identity differs from source-native reconstruction')
    if latest.get('wav')!='current.wav'or latest.get('mp3')!='current.mp3':raise ValueError('Unexpected stable media names')
    wav=pages.safe(public/'current.wav');mp3=pages.safe(public/'current.mp3')
    producer.checked(wav,latest['wav_sha256']);producer.checked(mp3,latest['mp3_sha256'])
    pcm=compare_pcm(expected,wav);del expected
    decoded=verify_mp3(manifest['ffmpeg'],wav,mp3,timing['duration'])
    if (pointer_path.read_bytes()!=pointer_raw or(config_path.read_bytes()!=config_raw)
            or(public/'latest.json').read_bytes()!=latest_raw or producer.pins()!=config['dependencies']
            or any(file_identity(p)!=stats[str(p)]for p in pinned_paths)):
        raise ValueError('Bound publication/code/model changed during verification')
    receipt={'schema':1,'policy':POLICY,'producer_policy':producer.POLICY,'config_sha256':pages.sha(config_raw),
        'latest_sha256':pages.sha(latest_raw),'mp3_sha256':latest['mp3_sha256'],'wav_sha256':latest['wav_sha256'],
        'source_generation':config['source_generation'],'total_units':total,'selected':bindings,
        'checks':{name:True for name in CHECKS},'post_processing':'off','recognition_status':'not_checked','human_approval':False}
    report={'policy':POLICY,'verifier_sha256':producer.file_digest(Path(__file__)),'source':str(source),'output':str(output),
        'integrity_receipt_sha256':producer.digest(receipt),'coverage':coverage,'timing':timing,'pcm':pcm,'mp3':decoded,
        'physical_native_attempts':attempts,'technical_retries':retries,'native_invocations':len(sessions),'sessions':sessions,
        'references':list(references.values()),'model_sha256':manifest['speech_model']['sha256'],
        'parser_recovery':recovery_owner,'normalized_sessions':{key:value[0]for key,value in parser_logs.items()},
        'opening_sha256':producer.file_digest(intro),'bed_sha256':producer.file_digest(bed),'ending_sha256':producer.file_digest(ending),
        'recognition_status':'not_checked','acoustic_quality_status':'not_checked','human_approval':False}
    return receipt,report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--integrity',type=Path,required=True)
    parser.add_argument('--recovery-config',type=Path);parser.add_argument('--recovery-config-sha256')
    args=parser.parse_args();target=pages.safe(args.integrity)
    if target.resolve().is_relative_to((ROOT/'website').resolve()):parser.error('Integrity evidence must remain private, outside website')
    receipt,report=verify(args.source,args.output,recovery_config=args.recovery_config,recovery_config_sha256=args.recovery_config_sha256)
    producer.write(target.with_suffix('.report.json'),report,immutable=True);producer.write(target,receipt,immutable=True)
    print(json.dumps({'status':'passed','total_units':receipt['total_units'],'mp3_sha256':receipt['mp3_sha256'],
        'integrity_sha256':producer.file_digest(target),'post_processing':'off','recognition_status':'not_checked'},indent=2))


if __name__=='__main__':main()
