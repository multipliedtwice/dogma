"""Current public-demo preparation and supervised, unchecked Higgs narration.

Only the current runtime Russian recipe is used. Native retries are exclusively
technical incomplete executions; a completed take never enters another attempt.
"""
from __future__ import annotations
import argparse,copy,html,json,math,os,re,subprocess,sys,time
from pathlib import Path
import psutil
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import freeze_public_audiobook_demo as legacy
import public_demo_listener_feedback as feedback
import render_public_demo_one_shot as native
from rimsky import russian_preparation as recipe,russian_preparation_providers as providers
from rimsky import russian_pronunciation as phonetics
from rimsky.util import digest,file_digest

SELF=Path(__file__).resolve()
POLICY='current-public-demo-runtime-preparation-one-completed-take-v1'
SAMPLING={**native.SAMPLING,'temperature':.8,'top_k':30,'top_p':.8}
UNCHECKED={'post_processing':'off','recognition_status':'not_checked','machine_verified':False,
           'human_approval':False,'production_approved':False}
read,write,checked=native.read,native.write,native.checked


def pins():
    modules=(native,native.assets,legacy,feedback,recipe,providers,phonetics)
    return {str(Path(m.__file__).resolve().relative_to(ROOT)).replace('\\','/'):file_digest(Path(m.__file__)) for m in modules}|{
        'scripts/generate_public_demo_current.py':file_digest(SELF),
        'runtime/rimsky/listener_stress.py':file_digest(ROOT/'runtime/rimsky/listener_stress.py'),
        'runtime/rimsky/listener_family_projection.py':file_digest(ROOT/'runtime/rimsky/listener_family_projection.py'),
        **{'runtime/rimsky/'+name+'.py':sha for name,sha in recipe.module_pins().items()},
        **{name:file_digest(ROOT/name) for name in ('runtime/rimsky/spoken_numbers.py',
            'runtime/rimsky/spoken_pronunciation.py','runtime/rimsky/name_stress_analysis.py','runtime/rimsky/higgs_reference.py',
            'runtime/rimsky/production_higgs_model.py','runtime/rimsky/opening.py')}}


def source_binding(unit,blocks):
    feedback._unit_block(unit,blocks)
    return {'source_id':unit['source_id'],'source_text':blocks[unit['source_id']],
            'source_start':unit['start'],'source_end':unit['end']}


def names(folder,book,assigned,human):
    """One pinned name-baseline request; subsequent family logic is shared runtime."""
    from rimsky import name_stress_analysis as ns
    texts=[b['text'] for c in book['chapters'] for b in c['blocks'] if b.get('text')]
    characters=legacy.name_candidates(texts,assigned['cast']);validate=ns.validator(characters)
    path=folder/'names/answer.json';request_path=folder/'names/request.json'
    if characters:
        bound={'pin':list(providers.STRESS_PIN),'system':ns.SYSTEM,'request':ns.request(characters,texts),'schema':ns.SCHEMA}
        write(request_path,bound,immutable=True) # Entire source/context body, not just model/schema.
        if path.exists():saved=read(path)
        else:
            for attempt in range(3):
                child=folder/'names'/f'attempt-{attempt}'
                if (child/'failure.json').exists():continue
                if (child/'start.json').exists():raise ValueError('Unknown unfinished name-provider invocation')
                write(child/'start.json',{'request_sha256':digest(bound)},immutable=True)
                try:answer,metadata=providers.call(providers.STRESS_PIN,ns.SYSTEM,bound['request'],ns.SCHEMA,evidence_dir=child/'cli')
                except recipe.independent.TRANSPORT_ERRORS as error:
                    write(child/'failure.json',{'error':str(error),'request_sha256':digest(bound)},immutable=True)
                    if attempt==2:raise
                    continue
                saved={'answer':answer,'metadata':metadata,'attempt':attempt,'request_sha256':digest(bound)}
                write(child/'raw.json',saved,immutable=True)
                validate(answer);providers.validate(metadata,providers.STRESS_PIN)
                providers.validate_stress_binding(metadata,bound,child/'cli',answer)
                write(path,saved,immutable=True);break
            else:raise ValueError('Name-provider transport budget exhausted')
        validate(saved['answer']);providers.validate(saved['metadata'],providers.STRESS_PIN)
        providers.validate_stress_binding(saved['metadata'],bound,folder/'names'/f"attempt-{saved['attempt']}"/'cli',saved['answer'])
        if saved['request_sha256']!=digest(bound):raise ValueError('Names belong to another request')
        rows={r['character_id']:{**r,'source':'model'} for r in saved['answer']['names']}
    else:rows={}
    values={k:r['stressed'] for k,r in rows.items()}
    data={'inventory':recipe.proper_inventory(book,rows),'names':values,'names_rows':rows,
          'human_names':human['stress'].get('names',{}),'book_sha256':recipe.book_binding(book)}
    result=recipe.prepare_names(data)
    write(folder/'names/prepared.json',result,immutable=True)
    from rimsky.name_stress_analysis import common_words
    return {'names':rows,'common':common_words(values,texts),'proper_nouns':result['proper_nouns'],
            'source_names':result['source_names'],'source_name_receipts':result['source_name_receipts']}


def normalize_units(units,language):
    result=copy.deepcopy(units)
    for unit in result:
        if language=='English':spoken,changes=legacy.english_numbers(unit['text']);policy=legacy.NUMBER_POLICY+'+'+legacy.ABBREVIATION_POLICY
        else:
            from rimsky.spoken_numbers import expand,POLICY_V3
            spoken=expand(unit['text'],policy=POLICY_V3);policy=POLICY_V3
            changes=[] if spoken==unit['text'] else [{'source':unit['text'],'spoken':spoken,'rule':policy}]
        unit['spoken']=re.sub(r'\s+',' ',spoken).strip()
        unit['spoken_conversion']={'policy':policy,'source':unit['text'],'expanded':spoken,'changes':changes,'whitespace':'collapse-and-strip'}
    return result


def prepare(folder):
    assigned=read(folder/'analysis/assigned.json');book=legacy.verify_assignment_source(folder,assigned)
    out=folder/'pronunciation';binding={'schema':1,'assigned_sha256':file_digest(folder/'analysis/assigned.json'),
        'book_sha256':file_digest(folder/'prepared/book.json'),'implementation_sha256':file_digest(SELF),'dependencies':pins()}
    write(out/'binding.json',binding,immutable=True)
    if (out/'result.json').exists():
        result=read(out/'result.json')
        for path,checksum in result['evidence_sha256'].items():checked(folder/path,checksum)
        legacy.verify_assignment_source(folder,result['assignment'])
        if result['assignment']['assigned_sha256']!=binding['assigned_sha256']:raise ValueError('Embedded assignment belongs to another casting input')
        write(folder/'assignment.json',result['assignment'],immutable=True)
        validate_preparation(folder);return read(folder/'assignment.json')
    blocks=feedback._blocks(book);units=normalize_units(assigned['units'],assigned['language'])
    structure=legacy.attach_structure(folder,units,book)
    with feedback.source_store(folder/'listener-state') as store:human=feedback.freeze(store,book)
    write(out/'listener-feedback.json',human,immutable=True)
    if assigned['language']=='Russian':
        lexical=names(out,book,assigned,human);prepared={};batch_receipts=[]
        for start in range(0,len(units),recipe.BATCH):
            subset=units[start:start+recipe.BATCH]
            data={'units':[{'text':u['spoken'],'kind':u['kind'],'source':source_binding(u,blocks)} for u in subset],
                'pronunciation':lexical,'recipe':recipe.config('codex_cli'),'book_sha256':recipe.book_binding(book),'cache_dir':str(out/'runtime-cache')}
            response=recipe.prepare(data);prepared.update(response['prepared_units'])
            batch_receipts.append({'request_sha256':response['request_sha256'],'sha256':response['sha256']})
            write(folder/'preparation-progress.json',{'stage':'russian_preparation','done':min(start+len(subset),len(units)),
                'total':len(units),'updated_unix':time.time(),**UNCHECKED})
        for unit in units:
            source=source_binding(unit,blocks);key=recipe.entry_key(unit['spoken'],source)
            entry=recipe.resolve_entry(unit['spoken'],{'book_sha256':recipe.book_binding(book),'listener_source':source,'prepared_units':prepared})
            final,human_receipt=feedback.apply(entry['automatic_text'],human,**source)
            text,records=phonetics.apply(final)
            unit.update(synthesis_text=text,pronunciation={'prepared_entry':entry,'listener_preparation':human_receipt,
                'russian_phonetics':{'rule':phonetics.RULE,'prephonetic_source':final,'records':records,
                    'module_sha256':file_digest(Path(phonetics.__file__))}})
        details={'policy':recipe.POLICY,'stress_authority':'silero-ruaccent-opus-high','dictionary_context_policy':'disabled',
                 'recipe':recipe.config('codex_cli'),'module_pins':recipe.module_pins(),'batches':batch_receipts}
    else:
        for unit in units:
            final,human_receipt=feedback.apply(unit['spoken'],human,**source_binding(unit,blocks))
            unit.update(synthesis_text=final,pronunciation={'listener_preparation':human_receipt})
        details={'policy':legacy.NUMBER_POLICY,'russian_processing':'not_applicable'}
    assignment={**assigned,'units':units,'pronunciation':details,'listener_feedback':human,
        'assigned_sha256':binding['assigned_sha256'],'effects_policy':'disabled','analysis':{'speech_structure':structure}}
    evidence={p.relative_to(folder).as_posix():file_digest(p) for p in out.rglob('*') if p.is_file() and p.name not in ('result.json','held.json')}
    write(folder/'assignment.json',assignment,immutable=True)
    write(out/'result.json',{'schema':1,'assignment':assignment,'evidence_sha256':evidence},immutable=True)
    validate_preparation(folder);return assignment


def validate_preparation(folder,*,fresh_human=True):
    binding=read(folder/'pronunciation/binding.json');result=read(folder/'pronunciation/result.json');assignment=read(folder/'assignment.json')
    if binding['dependencies']!=pins() or binding['implementation_sha256']!=file_digest(SELF) or result['assignment']!=assignment:
        raise ValueError('Prepared implementation/assignment changed')
    for path,checksum in result['evidence_sha256'].items():checked(folder/path,checksum)
    book=legacy.verify_assignment_source(folder,assignment);blocks=feedback._blocks(book)
    if assignment['assigned_sha256']!=file_digest(folder/'analysis/assigned.json'):raise ValueError('Casting/source analysis changed')
    if fresh_human:
        with feedback.source_store(folder/'listener-state') as store:current=feedback.freeze(store,book)
        if current!=assignment['listener_feedback']:raise ValueError('New human instruction requires preparation refresh before synthesis')
    for unit in assignment['units']:
        if re.search(r'(?:\[\s*|<\|\s*)(?:emotion|prosody|pitch|speed|vocal|style|effect)',unit['synthesis_text'],re.I):raise ValueError('Disabled sound tag')
        if assignment['language']=='Russian':
            entry=unit['pronunciation']['prepared_entry'];recipe.validate_entry(entry)
            if entry['module_pins']!=recipe.module_pins():raise ValueError('Runtime pronunciation modules changed')
            source=source_binding(unit,blocks)
            recipe.resolve_entry(unit['spoken'],{'book_sha256':recipe.book_binding(book),'listener_source':source,
                'prepared_units':{recipe.entry_key(unit['spoken'],source):entry}})
            final,receipt=feedback.apply(entry['automatic_text'],assignment['listener_feedback'],**source)
            expected,records=phonetics.apply(final);phonetics.validate(final,expected,records)
            if unit['synthesis_text']!=expected or receipt!=unit['pronunciation']['listener_preparation']:
                raise ValueError('Final human/phonetic preparation no longer replays')
        else:
            expected,receipt=feedback.apply(unit['spoken'],assignment['listener_feedback'],**source_binding(unit,blocks))
            if unit['synthesis_text']!=expected or receipt!=unit['pronunciation']['listener_preparation']:
                raise ValueError('English final human preparation no longer replays')
        legacy.validate_synthesis_pronunciation(unit['spoken'],unit['synthesis_text'],unit.get('pronunciation',{}).get('russian_phonetics'),
            listener=unit.get('pronunciation',{}).get('listener_preparation'),listener_context=assignment['listener_feedback'])
    return assignment


def freeze(folder,music):
    validate_preparation(folder)
    manifest=legacy.freeze(folder,music,persist=False)
    # Freeze chooses the known production model/opening/music. This adapter owns
    # its explicit sound profile and records it before any physical invocation.
    manifest.update(sampling=SAMPLING,base_freeze_implementation_sha256=manifest['freeze_implementation_sha256'],
                    freeze_implementation_sha256=file_digest(SELF),demo_adapter_policy=POLICY)
    write(folder/'manifest.json',manifest,immutable=True)
    write(folder/'frozen.json',{'assignment':read(folder/'assignment.json'),'manifest':manifest},immutable=True)
    return manifest


def configure(folder,output):
    assignment=validate_preparation(folder);frozen=read(folder/'frozen.json')
    if frozen!={'assignment':assignment,'manifest':read(folder/'manifest.json')} or frozen['manifest']['sampling']!=SAMPLING:
        raise ValueError('Frozen source/sound inputs changed')
    config={'schema':1,'policy':POLICY,'source':str(folder),'source_generation':digest(frozen),'frozen':frozen,
        'dependencies':pins(),'post_processing':'off','music_volume_scale':.9,'maximum_technical_attempts':3,
        'player_sha256':file_digest(native.assets.PLAYER_SOURCE)}
    key=digest(config);job=output/'jobs'/key
    write(job/'config.json',config,immutable=True);write(output/'current-job.json',{'job':key,'config_sha256':digest(config)})
    return config,job


def native_command(config,session):
    manifest=config['frozen']['manifest']
    return [str(Path(manifest['runner']['path'])),'--task','tts','--family','higgs_audio_tts','--model',
        str(Path(manifest['speech_model']['path'])),'--backend','cuda','--device','0','--threads','4',
        '--request-sequence',str(session/'request.json'),'--out-dir',str(session/'native'),'--out-format','pcm16','--metrics']


def request(config,unit,index,attempt):
    if attempt not in (0,1,2):raise ValueError('Technical attempt budget exhausted')
    base=native.request_spec(config,unit,index);cap=base['request']['max_tokens']
    if attempt==2 and cap==2048:raise ValueError('Technical ceiling permits only one distinct seed+1 retry')
    if attempt:base['request']['max_tokens']=min(2048,max(cap*2,cap+256))
    if attempt==2 or attempt==1 and cap==2048:base['request']['seed']=(base['request']['seed']+1)%2**31
    base['request']['id']=unit['id']+f'-technical-{attempt}'
    return {**base,'policy':POLICY,'attempt':attempt,'take':0,'maximum_completed_takes':1}


def live(identity):
    try:
        process=psutil.Process(identity['pid'])
        return abs(process.create_time()-identity['create_time'])<.01 and process.cmdline()==identity['command']
    except psutil.NoSuchProcess:return False


def terminal_session(session):
    terminal=session/'physical-result.json';worker=session/'worker.json'
    if worker.exists() and live(read(worker)):raise ValueError('Prior native worker remains live')
    if not terminal.exists():
        if not worker.exists():raise ValueError('Unknown physical worker: start exists without terminal/identity proof')
        write(terminal,{'returncode':None,'error':'Controller interruption; exact saved native identity is stopped'},immutable=True)
    return read(terminal)


def collect(job,session):
    start=read(session/'physical-start.json');terminal=terminal_session(session)
    checked(session/'request.json',start['sequence_sha256']);log=session/'native.log'
    text=log.read_text('utf8',errors='replace') if log.exists() else ''
    completed=[]
    for spec in start['specs']:
        raw=session/'native'/(spec['request']['id']+'.wav')
        completed.append(f'audio_out={raw}' in text and f"metrics[{spec['request']['id']}].audio_duration_ms=" in text)
    first_missing=next((n for n,complete in enumerate(completed) if not complete),len(completed))
    if any(completed[first_missing+1:]):raise ValueError('Native completion markers are not a contiguous request prefix')
    # This runner prints request_id in its successful summary. It is not a
    # pre-dispatch marker. Only an explicit generation/cap failure or a partial
    # output proves an incomplete request after the contiguous completed prefix.
    raw_next=(session/'native'/(start['specs'][first_missing]['request']['id']+'.wav')) if first_missing<len(completed) else None
    entered=bool(re.search(r'audiocpp_cli failed:.*Higgs.*(?:generation|max_tokens)|max_tokens.*before.*EOC',text,re.I)
                 or raw_next is not None and raw_next.exists())
    uncertain=first_missing<len(completed) and not entered and 'mode=offline' in text
    undispatched=[]
    for index,spec in enumerate(start['specs']):
        unit=spec['source']['id'];attempt=spec['attempt'];target=job/'attempts'/unit/f'{attempt}.json'
        if index>first_missing or index==first_missing and not entered:
            undispatched.append({'unit':unit,'attempt':attempt,'request_sha256':digest(spec['request']),
                'reason':'Uncertain request dispatch; held for inspection' if uncertain else
                    'Unstarted suffix after native abort' if entered else 'Backend startup failed before request loop'})
            continue
        if target.exists():continue
        raw=session/'native'/(spec['request']['id']+'.wav')
        receipt={'unit':unit,'attempt':attempt,'spec':spec,'spec_sha256':digest(spec),'session':session.name,
                 'log_sha256':file_digest(log) if log.exists() else None,'terminal':terminal,
                 'physical_start_sha256':file_digest(session/'physical-start.json'),
                 'physical_result_sha256':file_digest(session/'physical-result.json'),**UNCHECKED}
        try:
            receipt['format']=native.basic_audio(raw)
            # The native runner emits audio_out/metrics only for a completed
            # request; cap-before-EOC/aborted WAVs never qualify by format alone.
            if f'audio_out={raw}' not in text or f"metrics[{spec['request']['id']}].audio_duration_ms=" not in text:
                raise ValueError('Native request has no durable completion marker; incomplete WAV excluded')
            receipt.update(status='rendered',audio=str(raw.relative_to(job)),audio_sha256=file_digest(raw))
        except (OSError,ValueError,RuntimeError) as error:
            receipt.update(status='technical_incomplete',error=str(error),native_cap_reported=bool(re.search(r'max_tokens|before.*EOC',text,re.I)))
        write(target,receipt,immutable=True)
    write(session/'undispatched.json',{'units':undispatched,'physical_start_sha256':file_digest(session/'physical-start.json'),
        'physical_result_sha256':file_digest(session/'physical-result.json'),'log_sha256':file_digest(log) if log.exists() else None,
        'uncertain_dispatch':uncertain},immutable=True)
    if uncertain:raise ValueError('Unknown request dispatch after backend startup; no source attempt charged or automatically repeated')


def selected(job,config):
    rows=[]
    for index,unit in enumerate(config['frozen']['assignment']['units']):
        history=[]
        for path in sorted((job/'attempts'/unit['id']).glob('*.json')):
            row=read(path);attempt=len(history)
            if path.name!=f'{attempt}.json' or row['spec']!=request(config,unit,index,attempt) or row['spec_sha256']!=digest(row['spec']):
                raise ValueError('Technical history/source/sampling changed')
            if any(r['status']=='rendered' for r in history):raise ValueError('A completed take was retried')
            if not retryable({'history':history,'selected':None},config,index):raise ValueError('Technical history exceeded its distinct retry budget')
            session=job/'sessions'/row['session']
            if not session.resolve().is_relative_to((job/'sessions').resolve()):raise ValueError('Session escaped its job')
            start=read(checked(session/'physical-start.json',row['physical_start_sha256']))
            terminal=read(checked(session/'physical-result.json',row['physical_result_sha256']))
            if row['log_sha256'] is not None:checked(session/'native.log',row['log_sha256'])
            elif (session/'native.log').exists():raise ValueError('Previously absent native log changed')
            if row['spec'] not in start['specs'] or row['terminal']!=terminal:raise ValueError('Physical request/terminal receipt changed')
            if start['command']!=native_command(config,session):raise ValueError('Physical model/runner/backend command changed')
            checked(session/'request.json',start['sequence_sha256'])
            if read(session/'request.json')!={'requests':[s['request'] for s in start['specs']]}:raise ValueError('Physical native sequence changed')
            if row['status']=='rendered':checked(job/row['audio'],row['audio_sha256'])
            elif row['status']!='technical_incomplete':raise ValueError('Unknown technical outcome')
            history.append(row)
        rows.append({'unit':unit['id'],'history':history,'selected':history[-1] if history and history[-1]['status']=='rendered' else None})
    return rows


def state(output,job,config,**extra):
    rows=selected(job,config);value={'policy':POLICY,'source_generation':config['source_generation'],**UNCHECKED,
        'counts':{'total':len(rows),'rendered':sum(r['selected'] is not None for r in rows),
                  'technical_retries':sum(max(0,len(r['history'])-1) for r in rows),
                  'failed':sum(not retryable(r,config,i) and r['selected'] is None for i,r in enumerate(rows))},
        'render_complete':all(r['selected'] is not None for r in rows),'updated_unix':time.time(),**extra}
    write(output/'progress.json',value);return value


def retryable(row,config,index):
    history=row['history']
    if row['selected'] is not None or len(history)>=3:return False
    # At the ceiling there is no cap extension to try; one seed+1 attempt is
    # the last authorized technical strategy, never an unchanged repeat.
    if len(history)>=2:
        unit=config['frozen']['assignment']['units'][index]
        if native.request_spec(config,unit,index)['request']['max_tokens']==2048:return False
    return True


def session_run(folder,output,job,config,specs,timeout):
    validate_preparation(folder);native.assets.wait_for_audio_resources()
    pin,runner=native.model_guard(config['frozen']['manifest']) # Fresh checksum before every synthesis session.
    # Separate backend launches do not consume an unstarted unit's attempt.
    for launch in range(3):
        sid=digest({'specs':specs,'launch':launch});session=job/'sessions'/sid
        if not (session/'physical-start.json').exists():break
    else:raise ValueError('Bounded backend startup launch budget exhausted; source requests remain unstarted')
    session.mkdir(parents=True,exist_ok=True)
    sequence=session/'request.json';write(sequence,{'requests':[s['request'] for s in specs]},immutable=True)
    command=native_command(config,session)
    if Path(command[0]).resolve()!=runner.resolve() or Path(command[command.index('--model')+1]).resolve()!=pin.path.resolve():
        raise ValueError('Resolved speech model/runner differs from exact frozen command')
    start={'specs':specs,'launch':launch,'command':command,'sequence_sha256':file_digest(sequence)}
    with (session/'physical-start.json').open('x',encoding='utf8') as stream:json.dump(start,stream,ensure_ascii=False)
    process=None;error=None
    try:
        with (session/'native.log').open('ab') as log:
            process=subprocess.Popen(command,cwd=runner.parent,stdout=log,stderr=subprocess.STDOUT,**native.assets.HIDDEN)
            identity={'pid':process.pid,'create_time':psutil.Process(process.pid).create_time(),'command':command}
            write(session/'worker.json',identity,immutable=True);began=time.monotonic();last=(0,began)
            while process.poll() is None:
                size=(session/'native.log').stat().st_size
                if size!=last[0]:last=(size,time.monotonic())
                state(output,job,config,stage='native',worker=identity,worker_running=True,session=sid,log_bytes=size)
                if time.monotonic()-began>timeout or time.monotonic()-last[1]>600:
                    process.kill();process.wait();error='Bounded native timeout/inactivity';break
                time.sleep(2)
    except OSError as problem:error=str(problem)
    finally:
        if process is not None and process.poll() is None:process.kill();process.wait()
        write(session/'physical-result.json',{'returncode':process.returncode if process else None,'error':error},immutable=True)
    try:collect(job,session)
    except Exception as problem:
        state(output,job,config,stage='held',worker_running=False,error=str(problem))
        raise
    state(output,job,config,stage='collected',worker_running=False)


def publish(output,job,config):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    if read(output/'current-job.json')!={'job':job.name,'config_sha256':digest(config)}:
        raise ValueError('Stale worker cannot replace the current book publication')
    assignment,manifest=config['frozen']['assignment'],config['frozen']['manifest'];rate=24000
    intro,sr=sf.read(native.assets.opening(assignment,manifest),dtype='float32',always_2d=True)
    if sr!=rate or intro.shape!=(8*rate,2):raise ValueError('Approved opening format changed')
    audio=[intro];captions=[];position=8.
    for unit,row in zip(assignment['units'],selected(job,config),strict=True):
        chosen=row['selected']
        if chosen is None:break
        pcm,sr=sf.read(checked(job/chosen['audio'],chosen['audio_sha256']),dtype='float32')
        if captions:audio.append(np.zeros((3840,2),np.float32));position+=.16
        captions.append({'id':unit['id'],'source_id':unit['source_id'],'text':unit['text'],'start':position,
            'end':position+len(pcm)/rate,'raw_audio_sha256':chosen['audio_sha256'],'recognition_status':'not_checked'})
        audio.append(np.repeat(pcm[:,None],2,axis=1));position+=len(pcm)/rate
    full=len(captions)==len(assignment['units'])
    if not captions or position-8<300 and not full:return None
    mixed=np.concatenate(audio);music=manifest['music']
    def instrumental(kind):
        data,sr=sf.read(native.assets.music_asset(music[kind],kind),dtype='float32',always_2d=True)
        if sr!=rate:
            divisor=math.gcd(sr,rate);data=resample_poly(data,rate//divisor,sr//divisor,axis=0)
        return np.repeat(data,2,axis=1) if data.shape[1]==1 else data
    bed=instrumental('bed')[:20*rate].copy();fade=min(2*rate,len(bed)//2)
    bed[:fade]*=np.linspace(0,1,fade)[:,None];bed[-fade:]*=np.linspace(1,0,fade)[:,None]
    bed*=.9*10**(-30/20)/max(1e-9,float(np.sqrt(np.mean(bed**2))))
    for start in range(8*rate,len(mixed),24*rate):
        count=min(len(bed),len(mixed)-start);mixed[start:start+count]+=bed[:count]
    if full:
        end=instrumental('ending').copy();gain=.9*10**(-30/20)/max(1e-9,float(np.sqrt(np.mean(end**2))))
        a,b=min(36000,len(end)),min(168000,len(end));end[:a]*=np.linspace(0,1,a)[:,None];end[-b:]*=np.linspace(1,0,b)[:,None]
        mixed=np.concatenate([mixed,end*gain])
    if not np.isfinite(mixed).all() or np.max(np.abs(mixed))>=1:raise ValueError('Nonfinite/clipped mix; no quality retake')
    version=digest({'job':digest(config),'captions':captions,'full':full});public=output/'public';public.mkdir(exist_ok=True)
    wav=job/'mix.pending.wav';mp3=job/'mix.pending.mp3'
    sf.write(wav,mixed,rate,subtype='PCM_16')
    subprocess.run([manifest['ffmpeg'],'-v','error','-y','-i',str(wav),'-c:a','libmp3lame','-b:a','192k',str(mp3)],check=True,timeout=180,**native.assets.HIDDEN)
    subprocess.run([manifest['ffmpeg'],'-v','error','-i',str(mp3),'-f','null','-'],check=True,timeout=180,**native.assets.HIDDEN)
    status={'schema':1,'policy':POLICY,'title':assignment['title'],'author':assignment['author'],'language':assignment['language'],
        'source_generation':config['source_generation'],'version':version,'render_complete':full,'rendered_units':len(captions),
        'total_units':len(assignment['units']),'duration':len(mixed)/rate,'speech_end':position,'opening_seconds':8,
        'captions':captions,'mp3':'current.mp3','mp3_sha256':file_digest(mp3),'wav':'current.wav','wav_sha256':file_digest(wav),
        'mix':{'bed_rms_dbfs':-30+20*math.log10(.9),'bed_gap_seconds':4,'music_volume_scale':.9,'opening_unchanged':True},
        'limitation':'One completed native take per passage; bounded technical retries disclosed. Recognition/acoustic checks are off; no listening approval.',
        'attribution':'YuE2-3B by Multimodal Art Projection via audio.cpp. CC BY-NC 4.0; non-commercial use.',**UNCHECKED}
    histories=selected(job,config)
    status['physical_native_attempts']=sum(len(row['history']) for row in histories)
    status['technical_retries']=sum(max(0,len(row['history'])-1) for row in histories)
    player='player-'+config['player_sha256']+'.js';(public/player).write_bytes(native.assets.PLAYER_SOURCE.read_bytes())
    cfg=json.dumps({'generation':assignment['source_epub_sha256'],'version':version,'captions':captions,'opening_seconds':8,
        'opening_sha256':manifest['opening']['sha256']},ensure_ascii=False).replace('<','\\u003c')
    rows=''.join('<p data-start="%s" data-end="%s">%s</p>'%(c['start'],c['end'],html.escape(c['text'])) for c in captions)
    media='current.mp3?revision='+version
    markup='<!doctype html><html lang="'+('ru' if assignment['language']=='Russian' else 'en')+'"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+html.escape(assignment['title'])+'</title><style>:root{color-scheme:light}body{max-width:850px;margin:2rem auto;padding:1rem;background:#fbfaf7;color:#252c28;font:18px/1.6 system-ui}audio{width:100%}a{color:#22634f}p[data-start]{cursor:pointer}.active{background:#e3efe6}</style><h1>'+html.escape(assignment['title'])+'</h1><p>'+html.escape(status['limitation'])+'</p><audio controls src="'+media+'"></audio><p><a download href="'+media+'">MP3</a></p>'+rows+'<footer>'+status['attribution']+'</footer><script type="application/json" id="playback-config">'+cfg+'</script><script src="'+player+'"></script></html>'
    # The stable book's metadata is committed only after its new media decodes.
    write(public/(version+'.json'),status,immutable=True)
    from rimsky.util import atomic_write,atomic_replace
    atomic_replace(mp3,public/'current.mp3');atomic_replace(wav,public/'current.wav')
    atomic_write(public/'index.html',markup.encode('utf8'));write(public/'latest.json',status)
    return status


def render(folder,output,batch_size,timeout):
    config,job=configure(folder,output)
    for start in sorted((job/'sessions').glob('*/physical-start.json')):
        saved=read(start);expected=[]
        by_id={u['id']:(i,u) for i,u in enumerate(config['frozen']['assignment']['units'])}
        for spec in saved['specs']:
            i,u=by_id[spec['source']['id']];expected.append(request(config,u,i,spec['attempt']))
        if saved['specs']!=expected or start.parent.name!=digest({'specs':expected,'launch':saved['launch']}):raise ValueError('Reserved native source/request changed')
        collect(job,start.parent)
    while True:
        rows=selected(job,config);specs=[]
        for index,(unit,row) in enumerate(zip(config['frozen']['assignment']['units'],rows,strict=True)):
            if retryable(row,config,index):specs.append(request(config,unit,index,len(row['history'])))
            if len(specs)==batch_size:break
        if not specs:break
        session_run(folder,output,job,config,specs,timeout);publish(output,job,config)
    publish(output,job,config)
    return state(output,job,config,stage='stopped',worker_running=False)


def render_exclusive(folder,output,batch_size,timeout):
    """One native queue for these demos, in addition to the shared audio guard."""
    manager=None
    while manager is None:
        candidate=native.exclusive(ROOT/'training-work/public-demos/.native-queue')
        try:candidate.__enter__();manager=candidate
        except OSError:
            write(output/'waiting.json',{'stage':'waiting_for_other_demo','updated_unix':time.time(),**UNCHECKED})
            print('WAITING for the current public-demo native owner',flush=True);time.sleep(5)
    try:return render(folder,output,batch_size,timeout)
    finally:manager.__exit__(None,None,None)


def supervise(args):
    """Bounded operational resumes. Never converts technical success into ASR pass."""
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    stages=('prepare','freeze','render')
    for stage in stages:
        for restart in range(3):
            command=[sys.executable,'-X','utf8','-u',str(SELF),'--folder',str(args.folder.resolve()),'--output',str(output),
                '--stage',stage,'--batch-size',str(args.batch_size),'--session-timeout',str(args.session_timeout)]
            if args.music:command+=['--music',str(args.music.resolve())]
            log_path=output/f'{stage}-{restart}.log'
            with log_path.open('ab') as log:
                child=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                    env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'},**native.assets.HIDDEN)
                identity={'pid':child.pid,'create_time':psutil.Process(child.pid).create_time(),'command':command}
                last=(0,time.monotonic());began=time.monotonic()
                while child.poll() is None:
                    size=log_path.stat().st_size
                    if size!=last[0]:last=(size,time.monotonic())
                    write(output/'supervision.json',{'policy':POLICY,'stage':stage,'restart':restart,'worker':identity,
                        'worker_running':True,'updated_unix':time.time(),'log_bytes':size,**UNCHECKED})
                    if time.monotonic()-last[1]>2400 or time.monotonic()-began>14400:
                        process=psutil.Process(child.pid)
                        # Only our exact child tree may be stopped.
                        if abs(process.create_time()-identity['create_time'])<.01:
                            members=process.children(recursive=True)+[process]
                            for member in reversed(members):
                                try:member.kill()
                                except psutil.NoSuchProcess:pass
                            psutil.wait_procs(members,timeout=10)
                        child.wait();break
                    time.sleep(2)
            write(output/'supervision.json',{'policy':POLICY,'stage':stage,'restart':restart,'worker':identity,
                'worker_running':False,'exit_code':child.returncode,'updated_unix':time.time(),**UNCHECKED})
            if child.returncode==0:break
            if stage=='render' and (output/'progress.json').exists() and read(output/'progress.json').get('counts',{}).get('failed'):
                return 1 # Exhausted technical strategy, never restart to reset its budget.
        else:return 1
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--music',type=Path);parser.add_argument('--stage',choices=['prepare','freeze','render','all'],default='all')
    parser.add_argument('--batch-size',type=int,default=16);parser.add_argument('--session-timeout',type=int,default=1800)
    args=parser.parse_args();folder=args.folder.resolve();output=args.output.resolve()
    if not 1<=args.batch_size<=32 or not 60<=args.session_timeout<=7200:parser.error('Invalid batch/timeout')
    if args.stage=='all':return supervise(args)
    with native.exclusive(output):
        if args.stage=='prepare':prepare(folder)
        elif args.stage=='freeze':freeze(folder,args.music)
        else:
            result=render_exclusive(folder,output,args.batch_size,args.session_timeout)
            return 0 if result['render_complete'] else 1
    return 0


if __name__=='__main__':raise SystemExit(main())
