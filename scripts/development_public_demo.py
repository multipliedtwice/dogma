"""Explicit DEV-only bounded boundary repair and honest selected-take assembly.

Production one-shot modules/defaults are untouched. No ASR, fades or speech edits.
"""
from __future__ import annotations
import argparse,copy,hashlib,html,json,math,os,shutil,subprocess,sys,time,uuid,wave
from pathlib import Path
import psutil
ROOT=next(p for p in Path(__file__).resolve().parents if(p/'runtime/rimsky/store.py').is_file())
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import render_public_demo_one_shot_v2 as first
from rimsky.audio_join import boundary_check,JOIN_POLICY
from rimsky.util import digest,file_digest
read,write,sha,checked,assets=first.read,first.write,first.sha,first.checked,first.assets
basic_audio=first.basic_audio
POLICY='public-demo-explicit-development-selected-v1'
_BINARIES={}

def checked_binary(path,expected):
    path=Path(path).resolve();stat=path.stat();identity=(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
    if _BINARIES.get((str(path),expected))!=identity:
        checked(path,expected)
        after=path.stat()
        if (after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=identity:raise ValueError('Native binary changed while hashing')
        _BINARIES[(str(path),expected)]=identity

def idle():
    if any((p.info['name']or'').lower()=='audiocpp_cli.exe'for p in psutil.process_iter(['name'])):
        raise ValueError('Another native audio worker owns the GPU')

def source_recipe(config):
    """Prove the frozen title extension and every final human/phonetic input."""
    from rimsky import russian_preparation as recipe,russian_pronunciation as phonetics,heading_stress
    import public_demo_listener_feedback as feedback
    source=Path(config['source']);frozen=read(source/'frozen.json');assignment=config['frozen']['assignment']
    if (frozen!=config['frozen']or sha(frozen)!=config['source_generation']
        or read(source/'assignment.json')!=assignment or config['post_processing']!='off'):
        raise ValueError('Original immutable one-shot source/config changed')
    book=read(source/'prepared/book.json')
    if file_digest(source/'prepared/book.json')!=assignment['source_sha256']:raise ValueError('Original source book changed')
    blocks=feedback._blocks(book);human=assignment['listener_feedback']
    for unit in assignment['units']:
        feedback._unit_block(unit,blocks)
        binding={'source_id':unit['source_id'],'source_text':blocks[unit['source_id']],
            'source_start':unit['start'],'source_end':unit['end']}
        entry=unit['pronunciation']['prepared_entry'];recipe.validate_entry(entry)
        key=recipe.entry_key(unit['spoken'],binding)
        recipe.resolve_entry(unit['spoken'],{'book_sha256':recipe.book_binding(book),'listener_source':binding,'prepared_units':{key:entry}})
        if entry.get('heading_stress'):
            proof=entry['heading_stress'];pin=entry['module_pins']['heading_stress']
            checked(Path(heading_stress.__file__),pin)
            derived=heading_stress.derive_entry(proof['base_entry'],proof['base_entry']['automatic_text'],proof['witness'],proof['answer'],
                provider_receipts=proof['provider_receipts'],module_sha256=pin)
            if derived!=entry:raise ValueError('Heading lexical evidence changed')
        before,receipt=feedback.apply(entry['automatic_text'],human,**binding);feedback.validate(receipt,human)
        native,records=phonetics.apply(before);phonetics.validate(before,native,records)
        if native!=unit['synthesis_text']or receipt!=unit['pronunciation']['listener_feedback']:
            raise ValueError('Selected native input lost final human authority')
    return blocks

def effective_request(base,take):
    if take not in(1,2):raise ValueError('Only two additional explicit DEV takes allowed')
    request=copy.deepcopy(base);request['id']=base['id']+'-dev-t'+str(take)
    request['top_k']=30
    if take==2:request['seed']=(base['seed']+1)%2**31
    return request

def validate_request(base,request,take):
    if request!=effective_request(base,take):raise ValueError('DEV request changed unapproved source/voice/control inputs')

def effective_spec(origin,take):
    spec=copy.deepcopy(origin);spec.update(policy=POLICY,take=take,request=effective_request(origin['request'],take));return spec

def pcm(path):
    import numpy as np
    first.basic_audio(path)
    with wave.open(str(path))as stream:raw=stream.readframes(stream.getnframes())
    return raw,np.frombuffer(raw,np.int16).astype(np.float32)/32768

def physical(root,row):
    root=Path(root).resolve();session=root/'sessions'/row['session']
    if session.resolve().parent!=(root/'sessions').resolve():raise ValueError('Native session escapes its reserved root')
    start=read(session/'physical-start.json');terminal=read(session/'physical-result.json');worker=read(session/'worker.json')
    if terminal!={'returncode':0,'error':None}:raise ValueError('Native batch failed')
    try:
        if abs(psutil.Process(worker['pid']).create_time()-worker['create_time'])<.01:raise ValueError('Native worker still live')
    except psutil.NoSuchProcess:pass
    sequence=session/'request.json'
    command=worker['command']
    normalized=list(command)
    normalized[0]=str(Path(normalized[0]).resolve())
    normalized[normalized.index('--model')+1]=str(Path(normalized[normalized.index('--model')+1]).resolve())
    expected=[str(Path(row['spec']['runner']['path']).resolve()),'--task','tts','--family','higgs_audio_tts','--model',
        str(Path(row['spec']['model']['path']).resolve()),'--backend','cuda','--device','0','--threads','4',
        '--request-sequence',str(sequence),'--out-dir',str(session/'native'),'--out-format','pcm16','--metrics']
    if normalized!=expected:raise ValueError('Unexpected native launch flags or backend')
    if (command!=start['command']or command[command.index('--request-sequence')+1]!=str(sequence)
        or command[command.index('--out-dir')+1]!=str(session/'native')
        or Path(command[command.index('--model')+1]).resolve()!=Path(row['spec']['model']['path']).resolve()
        or Path(command[0]).resolve()!=Path(row['spec']['runner']['path']).resolve()):raise ValueError('Native command differs from pinned model/runner/sequence')
    checked_binary(Path(command[0]),row['spec']['runner']['sha256'])
    checked_binary(Path(command[command.index('--model')+1]),row['spec']['model']['sha256'])
    if file_digest(sequence)!=start['sequence_sha256']:raise ValueError('Physical sequence changed')
    specs=start['specs'];requests=[s['request']for s in specs]
    if read(sequence)!={'requests':requests}or row['spec']not in specs:raise ValueError('Selected take was not physically requested')
    if sha(row['spec'])!=row['spec_sha256']:raise ValueError('Selected specification changed')
    request=row['spec']['request'];raw=session/'native'/(request['id']+'.wav')
    asset=(root/row['audio']).resolve()
    if not asset.is_relative_to(root)or file_digest(raw)!=row['audio_sha256']or file_digest(asset)!=row['audio_sha256']:
        raise ValueError('Selected audio differs from its physical request')
    body,audio=pcm(asset);original,_=pcm(raw)
    if body!=original:raise ValueError('Selected PCM differs from native output')
    return asset,boundary_check(audio,24000)

def diagnostic_inventory(descriptors):
    records=[];seen=set()
    for item in descriptors:
        root=Path(item['root']).resolve();manifest_path=Path(item['manifest']).resolve();manifest=read(manifest_path);rows=[]
        for filename in item['receipts']:
            path=Path(filename).resolve()
            if not path.is_relative_to(root)or str(path)in seen:raise ValueError('Diagnostic receipt escapes root or is duplicated')
            seen.add(str(path));row=read(path);physical(root,row)
            rows.append({'unit':row['unit'],'receipt':str(path),'receipt_sha256':file_digest(path),
                'audio_sha256':row['audio_sha256'],'request_sha256':sha(row['spec']['request']),'session':row['session']})
        if 'units'in manifest:
            if manifest['units']!=[read(p)for p in item['receipts']]:raise ValueError('Diagnostic manifest differs from actual receipts')
        elif len(rows)!=1 or manifest.get('new_wav_sha256')!=rows[0]['audio_sha256']or manifest.get('new_request_sha256')!=rows[0]['request_sha256']:
            raise ValueError('Singleton diagnostic manifest differs from actual receipt')
        records.append({'kind':item['kind'],'manifest':str(manifest_path),'manifest_sha256':file_digest(manifest_path),
            'root':str(root),'native_requests':len(rows),'receipts':rows})
    return records

def plan(base,output,diagnostics=()):
    base=Path(base).resolve();output=Path(output).resolve()
    if base==output or base.is_relative_to(output)or output.is_relative_to(base):raise ValueError('DEV namespace must be separate')
    config=read(base/'one-shot.json');rows=first.receipts(base,config)
    if any(r['status']!='rendered'for r in rows):raise ValueError('Full first-take coverage required')
    first.model_guard(config['frozen']['manifest'])
    blocks=source_recipe(config)
    snapshot=[]
    for unit,row in zip(config['frozen']['assignment']['units'],rows,strict=True):
        if blocks[unit['source_id']][unit['start']:unit['end']]!=unit['text']:raise ValueError('First take source range changed')
        path,edge=physical(base,row)
        snapshot.append({'unit':unit['id'],'first_receipt_sha256':file_digest(base/'units'/(unit['id']+'.json')),
            'first_audio_sha256':row['audio_sha256'],'unsafe_first':bool(edge['unsafe_start']or edge['unsafe_end']),'first_edge':edge})
    value={'policy':POLICY,'base':str(base),'config_sha256':file_digest(base/'one-shot.json'),
        'source_generation':config['source_generation'],'first_takes':snapshot,'units':len(rows),
        'diagnostic_inputs':list(diagnostics),'diagnostic_inventory':diagnostic_inventory(diagnostics),
        'ffmpeg_sha256':file_digest(Path(config['frozen']['manifest']['ffmpeg'])),
        'boundary_policy':JOIN_POLICY,'boundary_module_sha256':file_digest(ROOT/'runtime/rimsky/audio_join.py'),
        'implementation_sha256':file_digest(Path(__file__)),'attempt1':'top_k30; exact same seed and all other sound inputs',
        'attempt2':'top_k30; seed+1 only if previous selected take remains boundary-unsafe',
        'post_processing':'development_only','production_default_post_processing':'off',
        'automatic_production_retake':False,'development_user_request':True}
    write(output/'development-plan.json',value,immutable=True);return value

def load_plan(output):
    output=Path(output).resolve();value=read(output/'development-plan.json')
    checked(Path(__file__),value['implementation_sha256']);checked(ROOT/'runtime/rimsky/audio_join.py',value['boundary_module_sha256'])
    if value['policy']!=POLICY or value['boundary_policy']!=JOIN_POLICY:raise ValueError('DEV policy changed')
    base=Path(value['base']);config=read(checked(base/'one-shot.json',value['config_sha256']))
    first.model_guard(config['frozen']['manifest'])
    source_recipe(config)
    checked(Path(config['frozen']['manifest']['ffmpeg']),value['ffmpeg_sha256'])
    if diagnostic_inventory(value['diagnostic_inputs'])!=value['diagnostic_inventory']:raise ValueError('Diagnostic inventory changed')
    return value,base,config

def selection(output,*,require_safe=False):
    output=Path(output).resolve();value,base,config=load_plan(output);selected=[]
    originals=first.receipts(base,config)
    for unit,origin,binding in zip(config['frozen']['assignment']['units'],originals,value['first_takes'],strict=True):
        uid=unit['id']
        if uid!=binding['unit']or file_digest(base/'units'/(uid+'.json'))!=binding['first_receipt_sha256']:
            raise ValueError('Immutable first take changed')
        history=[(base,origin)]
        if any(p.name not in('take-1.json','take-2.json')for p in(output/'takes'/uid).glob('*.json')):
            raise ValueError('DEV attempt budget exceeded')
        for take in(1,2):
            path=output/'takes'/uid/('take-'+str(take)+'.json')
            if not path.exists():continue
            if take!=len(history):raise ValueError('DEV attempt history has a gap')
            row=read(path)
            if not binding['unsafe_first']or row.get('take')!=take or row.get('unit')!=uid:
                raise ValueError('Only original unsafe units are eligible for DEV repair')
            if row.get('base_receipt_sha256')!=binding['first_receipt_sha256']or row.get('source_generation')!=config['source_generation']:
                raise ValueError('DEV take belongs to another source')
            if row['spec']!=effective_spec(origin['spec'],take):raise ValueError('DEV source/model/reference/control changed')
            validate_request(origin['spec']['request'],row['spec']['request'],take)
            if row.get('status')!='rendered':raise ValueError('Technical DEV failure remains held')
            # A valid safe prior take must never trigger another quality attempt.
            _,prior_edge=physical(*history[-1])
            if not(prior_edge['unsafe_start']or prior_edge['unsafe_end']):raise ValueError('Unnecessary extra DEV take after a safe result')
            history.append((output,row))
        root,row=history[-1];path,edge=physical(root,row)
        if require_safe and(edge['unsafe_start']or edge['unsafe_end']):raise ValueError('Selected unsafe boundary remains: '+uid)
        selected.append({'unit':uid,'status':'rendered','take':len(history)-1,'physical_attempt_count':len(history),
            'audio_path':str(path),'audio_sha256':row['audio_sha256'],'request_sha256':sha(row['spec']['request']),
            'receipt_sha256':file_digest((base/'units'/(uid+'.json'))if len(history)==1 else output/'takes'/uid/('take-'+str(len(history)-1)+'.json')),
            'native_text':row['spec']['request']['text'],'edge':edge,'source_preserved':True,'raw_pcm_exact':True})
    return config,selected

def repair(output,take):
    output=Path(output).resolve();value,base,config=load_plan(output);_,selected=selection(output)
    targets=[s for s in selected if s['edge']['unsafe_start']or s['edge']['unsafe_end']]
    if any(s['take']!=take-1 for s in targets):raise ValueError('Attempt is out of order or budget exhausted')
    if not targets:return []
    idle();pin,runner=first.model_guard(config['frozen']['manifest']);original={r['unit']:r for r in first.receipts(base,config)}
    specs=[]
    for target in targets:
        origin=original[target['unit']];spec=effective_spec(origin['spec'],take)
        specs.append(spec)
    sid=sha({'policy':POLICY,'take':take,'specs':specs});session=output/'sessions'/sid;session.mkdir(parents=True,exist_ok=True)
    sequence=session/'request.json';write(sequence,{'requests':[s['request']for s in specs]},immutable=True)
    command=[str(runner),'--task','tts','--family','higgs_audio_tts','--model',str(pin.path),'--backend','cuda','--device','0',
        '--threads','4','--request-sequence',str(sequence),'--out-dir',str(session/'native'),'--out-format','pcm16','--metrics']
    start={'id':sid,'specs':specs,'command':command,'sequence_sha256':file_digest(sequence)}
    with(session/'physical-start.json').open('x',encoding='utf8')as stream:json.dump(start,stream,ensure_ascii=False,indent=2)
    process=None;error=None
    try:
        with(session/'native.log').open('ab')as log:
            process=subprocess.Popen(command,cwd=runner.parent,stdout=log,stderr=subprocess.STDOUT,**assets.HIDDEN)
            write(session/'worker.json',{'pid':process.pid,'create_time':psutil.Process(process.pid).create_time(),'command':command},immutable=True)
            try:process.wait(timeout=1800)
            except subprocess.TimeoutExpired:process.kill();process.wait();error='Bounded DEV native timeout'
            if process.returncode:error=error or'Native exit '+str(process.returncode)
    finally:
        if process is not None and process.poll()is None:process.kill();process.wait()
        write(session/'physical-result.json',{'returncode':process.returncode if process else None,'error':error},immutable=True)
        for spec in specs:
            uid=spec['source']['id'];raw=session/'native'/(spec['request']['id']+'.wav')
            row={'policy':POLICY,'unit':uid,'take':take,'spec':spec,'spec_sha256':sha(spec),'session':sid,
                'source_generation':config['source_generation'],'base_receipt_sha256':file_digest(base/'units'/(uid+'.json')),
                'recognition_status':'not_checked','human_approval':False}
            try:
                row['format']=first.basic_audio(raw);row.update(status='rendered',audio=raw.relative_to(output).as_posix(),audio_sha256=file_digest(raw))
            except Exception as problem:row.update(status='technical_failure',error=str(problem))
            write(output/'takes'/uid/('take-'+str(take)+'.json'),row,immutable=True)
    return targets


def assemble_selected(output):
    output=Path(output).resolve()
    config,selected=selection(output,require_safe=True)
    plan_value=read(output/'development-plan.json')
    proof={'policy':POLICY,'config_sha256':sha(config),'selected_sha256':sha(selected),
           'diagnostic_count':sum(r['native_requests']for r in plan_value['diagnostic_inventory']),
           'source_pcm_boundary_verified':True,'plan_sha256':file_digest(output/'development-plan.json')}
    return mix_selected(output,config,selected,proof)


def mix_selected(output,config,selected,selection_proof,*,speech_processing='Whole native PCM; no trim, fade, normalization or time stretch'):
    """Mix caller-verified whole native clips or provenance-bound exact PCM slices.

    This function adds no recognition or approval claim. The caller must validate
    physical/source lineage; this layer rechecks coverage, hashes and boundaries.
    """
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    if(selection_proof.get('config_sha256')!=sha(config)or selection_proof.get('selected_sha256')!=sha(selected)
       or selection_proof.get('source_pcm_boundary_verified')is not True or type(selection_proof.get('diagnostic_count'))is not int
       or selection_proof['diagnostic_count']<0):raise ValueError('Verified selection proof does not bind assembly inputs')
    units=config['frozen']['assignment']['units']
    if len(selected)!=len(units)or any(u['id']!=r['unit']or r['status']!='rendered'for u,r in zip(units,selected,strict=True)):
        raise ValueError('Selected assembly requires exact full source coverage')
    for row in selected:
        _,audio=pcm(checked(Path(row['audio_path']),row['audio_sha256']))
        edge=boundary_check(audio,24000)
        if edge['unsafe_start']or edge['unsafe_end']:raise ValueError('Selected assembly contains unsafe boundary')
    diagnostic_count=selection_proof['diagnostic_count']
    write(output/'selected-manifest.json',{'policy':POLICY,'config_sha256':sha(config),'selected':selected,'selection_proof':selection_proof},immutable=True)
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    assignment,manifest=config['frozen']['assignment'],config['frozen']['manifest'];rate=24000
    intro,sr=sf.read(assets.opening(assignment,manifest),dtype='float32',always_2d=True)
    if sr!=rate or intro.shape!=(8*rate,2):raise ValueError('Approved opening format changed')
    chunks=[intro];position=8.0;captions=[]
    for unit,row in zip(assignment['units'],selected,strict=True):
        if row['status']!='rendered':break
        path=checked(Path(row['audio_path']),row['audio_sha256']);basic_audio(path)
        data,sr=sf.read(path,dtype='float32')
        if captions:chunks.append(np.zeros((round(.16*rate),2),np.float32));position+=.16
        captions.append({'id':unit['id'],'source_id':unit['source_id'],'text':unit['text'],
                         'start':position,'end':position+len(data)/rate,'raw_audio_sha256':row['audio_sha256'],
                         'recognition_status':'not_checked','take':row['take'],
                         'physical_attempt_count':row['physical_attempt_count']})
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
    version=sha({'policy':POLICY,'config':sha(config),'captions':captions,'full':full,'selection_proof_sha256':sha(selection_proof),'speech_processing':speech_processing,'implementation_sha256':file_digest(Path(__file__))})
    public=output/'public';public.mkdir(exist_ok=True);wav=public/(version+'.wav')
    mp3=public/(version+'.mp3')
    temporary_wav=public/(version+'.'+uuid.uuid4().hex+'.tmp.wav')
    temporary_mp3=public/(version+'.'+uuid.uuid4().hex+'.tmp.mp3')
    try:
        sf.write(temporary_wav,audio,rate,subtype='PCM_16')
        with wave.open(str(temporary_wav))as stream:
            if(stream.getnchannels(),stream.getsampwidth(),stream.getframerate(),stream.getnframes())!=(2,2,rate,len(audio)):
                raise ValueError('Assembled WAV format/duration differs from expected PCM')
            pcm_sha256=hashlib.sha256(stream.readframes(stream.getnframes())).hexdigest()
        subprocess.run([manifest['ffmpeg'],'-v','error','-y','-i',str(temporary_wav),'-c:a','libmp3lame','-b:a','192k',str(temporary_mp3)],check=True,timeout=180,**assets.HIDDEN)
        subprocess.run([manifest['ffmpeg'],'-v','error','-i',str(temporary_mp3),'-f','null','-'],check=True,timeout=180,**assets.HIDDEN)
        proof={'policy':POLICY,'version':version,'selected_sha256':sha(selected),'wav_sha256':file_digest(temporary_wav),
            'mp3_sha256':file_digest(temporary_mp3),'pcm_sha256':pcm_sha256,'samples':len(audio),'sample_rate':rate,'channels':2}
        # Persist the complete expected identity BEFORE either final asset is visible.
        write(output/'assembly-assets'/(version+'.json'),proof,immutable=True)
        for temporary,target,key in((temporary_wav,wav,'wav_sha256'),(temporary_mp3,mp3,'mp3_sha256')):
            if target.exists():checked(target,proof[key]);temporary.unlink()
            else:os.replace(temporary,target)
    finally:
        temporary_wav.unlink(missing_ok=True);temporary_mp3.unlink(missing_ok=True)
    status={'schema':1,'policy':POLICY,'source_generation':config['source_generation'],'generation':sha(config),'version':version,
            'title':assignment['title'],'language':assignment['language'],'post_processing':'development_only','production_default_post_processing':'off','recognition_status':'not_checked',
            'machine_verified':False,'human_approval':False,'render_complete':full,'rendered_units':len(captions),'total_units':len(assignment['units']),
            'opening_seconds':8,'speech_end':position,'duration':len(audio)/rate,'captions':captions,
            'mp3':mp3.name,'mp3_sha256':file_digest(mp3),'wav':wav.name,'wav_sha256':file_digest(wav),
            'mix':{'bed_rms_dbfs':-30+20*math.log10(volume),'bed_gap_seconds':4,'music_volume_scale':volume,
                   'opening_unchanged':True,'speech_processing':speech_processing},
            'selection_proof_sha256':sha(selection_proof),'development_checks':{'source_pcm_boundary_verified':True,'additional_takes':sum(r['take']for r in selected),
                                  'units_with_additional_takes':sum(r['take']>0 for r in selected),
                                  'selected_lineage_native_requests':sum(r['physical_attempt_count']for r in selected),
                                  'separate_diagnostic_native_requests':diagnostic_count,
                                  'total_native_requests_in_this_recheck':diagnostic_count+sum(r['physical_attempt_count']for r in selected)},
            'limitation':'Development recheck. Source, selected PCM and raw boundaries checked; additional takes are recorded. Recognition is not checked for the complete selection. No human listening approval.',
            'attribution':'Music: YuE2-3B by Multimodal Art Projection, via audio.cpp. CC BY-NC 4.0, non-commercial use.'}
    write(public/(version+'.json'),status,immutable=True);write(public/'latest.json',status)
    player='player-'+config['player_sha256']+'.js';shutil.copyfile(assets.PLAYER_SOURCE,public/player)
    cfg=json.dumps({'generation':config['source_generation'],'version':version,'captions':captions,'opening_seconds':8,'opening_sha256':manifest['opening']['sha256']},ensure_ascii=False).replace('<','\\u003c')
    rows=''.join('<p data-start="%s" data-end="%s">%s</p>'%(c['start'],c['end'],html.escape(c['text'])) for c in captions)
    markup='<!doctype html><html lang="'+('ru' if assignment['language']=='Russian' else 'en')+'"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+html.escape(assignment['title'])+'</title><style>body{max-width:850px;margin:2rem auto;padding:1rem;background:#fff;color:#20251f;font:18px/1.6 system-ui}audio{width:100%}a{color:#315d3b}p[data-start]{cursor:pointer}.active{background:#e9f2e5}</style><h1>'+html.escape(assignment['title'])+'</h1><p>'+status['limitation']+'</p><audio controls src="'+mp3.name+'"></audio><p><a download href="'+mp3.name+'">MP3</a></p>'+rows+'<footer>'+status['attribution']+'</footer><script type="application/json" id="playback-config">'+cfg+'</script><script src="'+player+'"></script></html>'
    (public/'index.html').write_text(markup,encoding='utf-8')
    return status


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['plan','repair','select','assemble'],required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--first-takes',type=Path)
    parser.add_argument('--diagnostics',type=Path);parser.add_argument('--take',type=int,choices=[1,2]);parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    with first.exclusive(args.output):
        if args.stage=='plan':
            if not args.first_takes:parser.error('--first-takes required')
            result=plan(args.first_takes,args.output,read(args.diagnostics)if args.diagnostics else())
        elif args.stage=='repair':
            if not args.execute or args.take is None:parser.error('Explicit --execute --take required for DEV native')
            result=repair(args.output,args.take)
        elif args.stage=='select':
            _,rows=selection(args.output);result={'policy':POLICY,'selected':rows}
            write(args.output/'selected-review.json',result)
        else:
            if not args.execute:parser.error('Explicit --execute required for private assembly')
            result=assemble_selected(args.output)
    print(json.dumps({'stage':args.stage,'status':'complete','output':str(args.output)}),flush=True)
