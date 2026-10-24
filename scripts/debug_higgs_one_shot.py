"""Private development adapter: fresh bound text, one native take, local reader publication."""
from __future__ import annotations
import argparse,copy,json,math,os,re,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'runtime'),str(ROOT/'scripts')]
import render_public_demo_one_shot as basic
from rimsky.util import file_digest,digest
from rimsky.production_higgs_model import resolve_production_higgs_model
read,write=basic.read,basic.write
POLICY='development-private-higgs-one-shot-v1'
UNCHECKED={'post_processing':'off','recognition_status':'not_checked','machine_verified':False,'human_approval':False,'production_approved':False}
SELF=Path(__file__).resolve()
MEDIA=ROOT/'training-work/character-rb2/full/clips/elirm-iv-mvp'
PREFIX='/character-rb2/full/clips/elirm-iv-mvp/'


def canonical_assignments(generation,chapter):
    result=[]
    for chunk in sorted((generation/chapter).glob('chunk-*')):
        paths=[p for p in chunk.rglob('assignment.json') if not any(x.startswith(('take-','word-','recovery-')) for x in p.relative_to(chunk).parts[:-1])]
        if not paths:continue
        depth=max(len(p.relative_to(chunk).parts) for p in paths);paths=[p for p in paths if len(p.relative_to(chunk).parts)==depth]
        if len(paths)!=1:raise ValueError('Ambiguous final assignment: '+str(chunk))
        result.append(paths[0])
    if not result:raise ValueError('No canonical assignments for '+chapter)
    return result


def prepare_unit(original,number_policy,fixes):
    from render_higgs_book_trial import spoken_text
    from rimsky.spoken_edits import apply_spoken_edit
    row=copy.deepcopy(original);removed={}
    for key in ('spoken_text_plan','decoding_profile','onset_pause','stress_marks','retake_number'):
        if key in row:removed[key]=row.pop(key)
    row['spoken_number_policy']=number_policy;row['retake_number']=0
    baseline=spoken_text(row['text'],number_policy)
    if row.get('finish_fragment') and not re.search(r'[.!?…,:;]$',baseline):baseline+=','
    candidates=[]
    if removed.get('spoken_text_plan'):candidates.append(removed['spoken_text_plan'])
    entry=fixes.get('entries',{}).get(baseline)
    if entry:candidates.append(entry['spoken_text_plan'])
    for plan in candidates:
        authority=plan.get('evidence',{}).get('source')=='explicit_listener_instruction' or plan.get('recovery_kind')=='editorial_reading_repair'
        if not authority:continue  # Failed/automatic recovery is evidence, not preparation policy.
        if plan.get('expected_spoken')!=baseline:
            raise ValueError('Listener/editorial correction needs a fresh source binding: '+row['id'])
        apply_spoken_edit(baseline,plan);row['spoken_text_plan']=copy.deepcopy(plan);break
    return row,{'unit':row['id'],'previous_fields':removed,'number_policy_before_lookup':number_policy,
                'retained_listener_plan':bool(row.get('spoken_text_plan')),'human_approval':False}


def prepare(source,output,chapters):
    if output.parent!=source.parent or output==source:raise ValueError('Fresh private sibling generation required')
    if (output/'ready.json').exists():
        ready=read(output/'ready.json')
        if ready.get('source')!=str(source) or ready.get('chapters')!=chapters:raise ValueError('Frozen preparation scope differs from requested source/chapters')
        return ready
    manifest=read(source/'manifest.json');book_path=source.parent/'source/book.json'
    if manifest['source_sha256']!=file_digest(book_path):raise ValueError('Source book changed')
    book=read(book_path);blocks={b['id']:b.get('text','') for c in book['chapters'] for b in c['blocks']}
    fixes={'entries':{}}
    if manifest.get('book_fixes'):
        spec=manifest['book_fixes'];basic.checked(spec['path'],spec['sha256']);fixes=read(Path(spec['path']))
    original=[];copied=[];seen=set()
    for cid in chapters:
        for path in canonical_assignments(source,cid):
            document=read(path);units=[];changes=[]
            for unit in document['units']:
                if unit['id'] in seen:raise ValueError('Duplicate canonical unit')
                if (unit.get('chapter_id')!=cid or blocks.get(unit.get('source_id'),'')[unit['start']:unit['end']]!=unit['text']):
                    raise ValueError('Canonical unit is not its exact original source slice: '+unit['id'])
                seen.add(unit['id']);fresh,change=prepare_unit(unit,'grouped-digits-v3',fixes);units.append(fresh);changes.append(change)
            relative=path.relative_to(source);target=output/relative
            document={**document,'units':units}
            write(target,document,immutable=True)
            original.append({'path':str(path),'sha256':file_digest(path),'output':relative.as_posix()})
            copied.append({'assignment':relative.as_posix(),'changes':changes})
    write(output/'prepared-inputs.json',{'policy':POLICY,'source_generation':str(source),'manifest_sha256':file_digest(source/'manifest.json'),
          'source_sha256':file_digest(book_path),'chapters':chapters,'assignments':original,'changes':copied},immutable=True)
    if (source/'section-markers.json').exists():
        target=output/'section-markers.json'
        if target.exists():basic.checked(target,file_digest(source/'section-markers.json'))
        else:shutil.copyfile(source/'section-markers.json',target)
    # Rebuild decisions on the exact final native text; no old answer is silently rebound.
    for script,folder in [('build_homograph_stress.py','homograph'),('build_yo_restoration.py','yo')]:
        command=[sys.executable,'-X','utf8','-u',str(ROOT/'scripts'/script),'--generations',output.name,'--chapters',*chapters,'--output',str(output/'preparation'/folder)]
        with (output/(folder+'-preparation.log')).open('ab') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=5400,env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8','CUDA_VISIBLE_DEVICES':''},**basic.assets.HIDDEN)
    return freeze(source,output,chapters)


def validate_adjudication(answer):
    batches=answer.get('metadata',{}).get('batches')
    if not isinstance(batches,list):raise ValueError('Missing pronunciation adjudication batches')
    for metadata in batches:
        if any(metadata.get(k)!=v for k,v in zip(('provider','model','effort'),('claude_cli','claude-opus-5-5','high'))):
            raise ValueError('Expected pinned Opus-high pronunciation adjudication')
        actual=metadata.get('model_usage',{})
        if not actual or any(not re.fullmatch(r'claude-opus-5-5(?:-\d{8})?',name) for name in actual):
            raise ValueError('Reported actual pronunciation model differs from pinned Opus')
        if metadata.get('requested_model')!='claude-opus-5-5' or not metadata.get('cli_version'):
            raise ValueError('Missing actual pronunciation CLI/model provenance')


def freeze(source,output,chapters):
    import render_higgs_book_trial as trial
    from rimsky.yo_restoration import yo_block
    hp=output/'preparation/homograph/homograph.answer.json';yp=output/'preparation/yo/yo.answer.json'
    hs=read(hp);validate_adjudication(hs);validate_adjudication(read(yp))
    yo=yo_block(yp);records=[];units=[]
    for cid in chapters:
        for path in canonical_assignments(output,cid):
            assignment=read(path);spec=copy.deepcopy(assignment['pronunciation']);spec.update(policy='stress-rare-v4',
                homograph_stress={'rule':hs['rule'],'answer_sha256':file_digest(hp),'overrides':hs['overrides']},yo_restoration=yo)
            frozen_path=path.with_name('one-shot-assignment.json');write(frozen_path,{**assignment,'pronunciation':spec},immutable=True)
            context=trial.prepare(frozen_path,path.parent/'one-shot-plan',[cid]);plan=read(context['path'])
            records.append({'assignment':frozen_path.relative_to(output).as_posix(),'sha256':file_digest(frozen_path),
                            'plan':(path.parent/'one-shot-plan/render-plan.json').relative_to(output).as_posix(),
                            'plan_sha256':file_digest(path.parent/'one-shot-plan/render-plan.json')})
            units.extend(plan['units'])
    pin=resolve_production_higgs_model(ROOT)
    runner=trial.RUNNER
    native_manifest={'speech_model':{'checkpoint':pin.checkpoint,'sha256':pin.sha256,'manifest_sha256':file_digest(pin.manifest_path),
        'provenance_sha256':file_digest(pin.provenance_path),'provenance':pin.provenance},
        'runner':{'path':str(runner),'sha256':file_digest(runner),'dlls':{p.name:file_digest(p) for p in sorted(runner.parent.glob('*.dll'))}}}
    manifest={**read(source/'manifest.json'),'pronunciation':spec,'render_policy':POLICY,'post_processing':'off',
              'tts_engine':{'engine':'higgs','checkpoint':pin.checkpoint,'model_sha256':pin.sha256}}
    write(output/'manifest.json',manifest,immutable=True)
    music_source=source/'scene-music-v2';music_target=output/'scene-music-v2';music_files={}
    music_receipt=read(music_source/'receipts.json')
    for relative in ['manifest.json','receipts.json',*['assets/'+r['audio'] for r in music_receipt['takes'].values()]]:
        path=music_source/relative;target=music_target/relative;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():basic.checked(target,file_digest(path))
        else:shutil.copyfile(path,target)
        music_files[relative]=file_digest(target)
    ready={'schema':1,'policy':POLICY,'source':str(source),'chapters':chapters,'unit_count':len(units),
           'source_sha256':file_digest(source.parent/'source/book.json'),'preparation_sha256':file_digest(output/'prepared-inputs.json'),
           'homograph_answer_sha256':file_digest(hp),'yo_answer_sha256':file_digest(yp),'plans':records,'native_manifest':native_manifest,
           'implementation_sha256':file_digest(SELF),'generic_primitives_sha256':file_digest(Path(basic.__file__)),
           'trial_preparation_sha256':file_digest(Path(trial.__file__)),'music_volume_scale':.9,'music_files':music_files,**UNCHECKED}
    ready['manifest_sha256']=file_digest(output/'manifest.json')
    ready['chapter_counts']={cid:sum(u['chapter_id']==cid for u in units) for cid in chapters}
    ready['estimated_spoken_minutes']={cid:round(sum(max(.6,len(re.findall(r'\w+',u['text']))/2.6)+.24 for u in units if u['chapter_id']==cid)/60,2) for cid in chapters}
    for unit in units:validate_synthesis(unit['synthesis'],ready)
    write(output/'ready.json',ready,immutable=True)
    print('PREPARED',json.dumps({'unique_units':ready['chapter_counts'],'estimated_spoken_minutes':ready['estimated_spoken_minutes']},ensure_ascii=False),flush=True)
    return ready


def planned(output,ready):
    import render_higgs_book_trial as trial
    if file_digest(SELF)!=ready['implementation_sha256'] or file_digest(Path(basic.__file__))!=ready['generic_primitives_sha256']:
        raise ValueError('Frozen private execution implementation changed')
    basic.checked(output/'manifest.json',ready['manifest_sha256'])
    basic.checked(Path(trial.__file__),ready['trial_preparation_sha256'])
    basic.checked(output/'prepared-inputs.json',ready['preparation_sha256'])
    basic.checked(output/'preparation/homograph/homograph.answer.json',ready['homograph_answer_sha256'])
    basic.checked(output/'preparation/yo/yo.answer.json',ready['yo_answer_sha256'])
    basic.checked(output.parent/'source/book.json',ready['source_sha256'])
    units=[]
    for record in ready['plans']:
        basic.checked(output/record['assignment'],record['sha256']);path=basic.checked(output/record['plan'],record['plan_sha256'])
        units.extend({**u,'_prepared_plan':str(path)} for u in read(path)['units'])
    if len(units)!=ready['unit_count']:raise ValueError('Prepared source coverage changed')
    for unit in units:validate_synthesis(unit['synthesis'],ready)
    for relative,checksum in ready['music_files'].items():basic.checked(output/'scene-music-v2'/relative,checksum)
    return units


def validate_reference(reference):
    padding=reference.get('onset_padding')
    if padding is None:
        basic.checked(ROOT/reference['audio'],reference['sha256']);return
    if (padding.get('policy')!='reference-onset-padding-v1' or padding.get('pad_seconds')!=.15
            or padding.get('source_sha256')!=reference['sha256']
            or not re.fullmatch(r'[a-f0-9]{64}',str(padding.get('derived_sha256','')))
            or not padding.get('source_audio')):
        raise ValueError('Invalid source-bound reference onset provenance')
    # The reviewed identity stays the original checksum; Higgs consumes the
    # separately pinned derived WAV. Never compare derived bytes to source SHA.
    basic.checked(ROOT/padding['source_audio'],padding['source_sha256'])
    basic.checked(ROOT/reference['audio'],padding['derived_sha256'])


def validate_synthesis(plan,ready):
    if plan['backend']=='nonverbal_asset':
        source=plan.get('source',{});ref=plan['reference']
        if (source.get('license')!='CC0-1.0' or source.get('source_sha256')!=ref['sha256']
                or not source.get('source_page','').startswith('https://opengameart.org/')
                or not (ROOT/ref['audio']).resolve().is_relative_to((ROOT/'resources/nonverbal').resolve())):
            raise ValueError('Nonverbal asset is not the pinned licensed source; old Higgs outputs forbidden')
        basic.checked(ROOT/ref['audio'],ref['sha256']);return
    if (plan['backend']!='higgs_audio_cpp' or plan['effects_policy']!='disabled' or plan['model_sha256']!=ready['native_manifest']['speech_model']['sha256']
            or plan['runner_sha256']!=ready['native_manifest']['runner']['sha256']
            or re.search(r'(?:\[\s*|<\|\s*)(?:emotion|prosody|pitch|speed|vocal|style|effect)',plan['prompt'],re.I)):
        raise ValueError('Native plan enables effects or a nonproduction checkpoint/runner')
    validate_reference(plan['reference'])


def collect(output,session):
    for unit in session['units']:
        receipt=output/'units'/(unit['id']+'.json')
        if receipt.exists():continue
        source=output/'native'/session['id']/(unit['id']+'.wav')
        value={'unit':unit['id'],'session':session['id'],'synthesis_sha256':digest(unit['synthesis']),'take':0,**UNCHECKED}
        try:
            value['format']=basic.basic_audio(source);checksum=file_digest(source);target=output/'assets'/(checksum+'.wav');target.parent.mkdir(exist_ok=True)
            if not target.exists():shutil.copyfile(source,target)
            value.update(status='rendered',audio=target.relative_to(output).as_posix(),audio_sha256=checksum)
        except (OSError,ValueError,RuntimeError) as error:value.update(status='technical_failure',error=str(error))
        write(receipt,value,immutable=True)


def native_batch(output,ready,units,timeout):
    import render_higgs_book_trial as trial
    basic.assets.wait_for_audio_resources();pin,runner=basic.model_guard(ready['native_manifest'])
    requests=[]
    for unit in units:
        plan=unit['synthesis'];validate_reference(plan['reference'])
        if plan['effects_policy']!='disabled' or re.search(r'(?:\[\s*|<\|\s*)(?:emotion|prosody|pitch|speed|vocal|style|effect)',plan['prompt'],re.I):raise ValueError('Disabled effect in prepared native request')
        requests.append(trial.sequence_request(unit['id'],plan))
    sid=digest({'requests':requests,'units':units,'ready_sha256':file_digest(output/'ready.json')});folder=output/'native'/sid;folder.mkdir(parents=True,exist_ok=True)
    write(folder/'requests.json',{'requests':requests},immutable=True)
    command=[str(runner),'--task','tts','--family','higgs_audio_tts','--model',str(pin.path),'--backend','cuda','--device','0','--threads','4',
             '--request-sequence',str(folder/'requests.json'),'--out-dir',str(folder),'--out-format','pcm16','--metrics']
    session={'id':sid,'units':units,'command':command,'sequence_sha256':file_digest(folder/'requests.json')}
    with (folder/'physical-start.json').open('x',encoding='utf-8') as stream:json.dump(session,stream,ensure_ascii=False)
    child=None;error=None
    try:
        with (folder/'native.log').open('ab') as log:
            child=subprocess.Popen(command,cwd=runner.parent,stdout=log,stderr=subprocess.STDOUT,**basic.assets.HIDDEN)
            import psutil
            identity={'pid':child.pid,'create_time':psutil.Process(child.pid).create_time(),'command':command};write(folder/'worker.json',identity,immutable=True)
            started=time.monotonic()
            while child.poll() is None:
                write(output/'worker.json',{**identity,'stage':'native','session':sid,'updated_unix':time.time()})
                if time.monotonic()-started>timeout:raise TimeoutError('Native session timeout; no retry')
                time.sleep(1)
            if child.returncode:error='Native exit '+str(child.returncode)
    except (OSError,TimeoutError) as problem:error=str(problem)
    finally:
        if child is not None and child.poll() is None:child.kill();child.wait()
        write(folder/'physical-result.json',{'error':error,'returncode':child.returncode if child else None},immutable=True);collect(output,session)


def publish(output,ready,cid,units,data_dir=None):
    import numpy as np,soundfile as sf
    from higgs_published_timeline import pin_timeline
    selected=[]
    for unit in [u for u in units if u['chapter_id']==cid]:
        path=output/'units'/(unit['id']+'.json')
        if not path.exists() or read(path)['status']!='rendered':break
        selected.append((unit,read(path)))
    if not selected:return
    full=len(selected)==sum(u['chapter_id']==cid for u in units);chunks=[];timeline=[];position=0.;previous=None
    for unit,row in selected:
        source=basic.checked(output/row['audio'],row['audio_sha256']);data,rate=sf.read(source,dtype='int16')
        gap=0 if previous is None else .8 if unit.get('kind')=='heading' else 1.15 if unit.get('scene')!=previous.get('scene') else .34 if unit['source_id']!=previous['source_id'] else .24
        frames=round(gap*rate);chunks.append(np.zeros(frames,dtype=np.int16));position+=frames/rate
        timeline.append({k:unit.get(k) for k in ('id','source_id','text','speaker','voice','mood','analysis_parts')}|{'start':position,'end':position+len(data)/rate,**UNCHECKED})
        chunks.append(data);position+=len(data)/rate;previous=unit
    if position<300 and not full:return
    MEDIA.mkdir(parents=True,exist_ok=True);pcm=np.concatenate(chunks);key=digest({'ready':file_digest(output/'ready.json'),'timeline':timeline,'audio':[r['audio_sha256'] for _,r in selected]})
    wav=MEDIA/('one-shot-'+key+'.wav')
    if not wav.exists():sf.write(wav,pcm,24000,subtype='PCM_16')
    state=read(output/'progress.json') if (output/'progress.json').exists() else {'generation':output.name,'chapters':{}}
    chapter={'complete':full,'units':len(timeline),'seconds':len(pcm)/24000,'stream':PREFIX+wav.name,'wav_sha256':file_digest(wav),**UNCHECKED}
    pin_timeline(output,cid,chapter,timeline);state['chapters'][cid]=chapter
    state.update(status='complete' if all(state['chapters'].get(c,{}).get('complete') for c in ready['chapters']) else 'rendering',
                 effects_policy='disabled',limitations=['One-shot development listening; recognition and acoustic checks were not run; no human approval.'],**UNCHECKED)
    state['tts_engine']=read(output/'manifest.json')['tts_engine']
    write(output/'progress.json',state)
    try:attach_cached_music(output,ready,cid,state,timeline)
    except (ValueError,OSError,RuntimeError) as error:
        chapter['music_pending']='Cached music mix needs attention: '+str(error)
        write(output/cid/'music-error.json',{'error':str(error),'speech_sha256':chapter['wav_sha256']})
    write(output/'progress.json',state)
    if data_dir is not None:
        from sync_higgs_preview_runtime import sync_preview
        sync_preview(workspace=ROOT,source=ROOT/'content/04 Элирм IV.fb2',prepared_book=output.parent/'source/book.json',generation_dir=output,data_dir=data_dir)


def render(output,data_dir,batch_size,timeout):
    ready=read(output/'ready.json');units=planned(output,ready)
    for path in sorted((output/'native').glob('*/physical-start.json')):
        session=read(path);worker=path.parent/'worker.json'
        import render_higgs_book_trial as trial
        requests=[trial.sequence_request(u['id'],u['synthesis']) for u in session['units']]
        known={u['id']:u for u in units}
        if (any(known.get(u['id'])!=u for u in session['units'])
                or session['id']!=digest({'requests':requests,'units':session['units'],'ready_sha256':file_digest(output/'ready.json')})
                or read(path.parent/'requests.json')!={'requests':requests}):raise ValueError('Reserved physical session changed')
        if not worker.exists() and not (path.parent/'physical-result.json').exists():
            raise ValueError('Unknown physical worker: started session has neither identity nor terminal result; do not collect')
        if worker.exists():
            import psutil
            identity=read(worker)
            try:
                if abs(psutil.Process(identity['pid']).create_time()-identity['create_time'])<.01:raise ValueError('Prior native worker still lives')
            except psutil.NoSuchProcess:pass
        collect(output,session)
    for cid in ready['chapters']:
        for unit in [u for u in units if u['chapter_id']==cid and u['synthesis']['backend']=='nonverbal_asset']:
            path=output/'units'/(unit['id']+'.json')
            if not path.exists():
                synthesis=unit['synthesis'];validate_synthesis(synthesis,ready)
                source=Path(unit['_prepared_plan']).parent/unit['audio'];basic.basic_audio(source)
                target=output/'assets'/(file_digest(source)+'.wav');target.parent.mkdir(exist_ok=True);shutil.copyfile(source,target)
                write(path,{'unit':unit['id'],'take':0,'status':'rendered','audio':target.relative_to(output).as_posix(),'audio_sha256':file_digest(target),'source_asset':synthesis,**UNCHECKED},immutable=True)
        pending=[u for u in units if u['chapter_id']==cid and not (output/'units'/(u['id']+'.json')).exists()]
        for start in range(0,len(pending),batch_size):native_batch(output,ready,pending[start:start+batch_size],timeout);publish(output,ready,cid,units,data_dir)
        publish(output,ready,cid,units,data_dir)
    failures=[u['id'] for u in units if read(output/'units'/(u['id']+'.json'))['status']!='rendered']
    if failures and (output/'progress.json').exists():
        state=read(output/'progress.json');state.update(status='technical_failure',technical_failures=failures,error='First-take technical failures; no automatic retries')
        write(output/'progress.json',state)
    write(output/'worker.json',{'stage':'stopped','technical_failures':failures,'updated_unix':time.time()});return int(bool(failures))


def attach_cached_music(output,ready,cid,state,timeline):
    """Only reuse historically verified instrumental phrases; no music workers/ASR."""
    import numpy as np,soundfile as sf
    from rimsky import music
    from rimsky.export import mix_music
    from rimsky.narration_transitions import source_boundaries,windows,bridge_layers
    folder=output/'scene-music-v2';manifest=read(folder/'manifest.json');receipt=read(folder/'receipts.json')
    if receipt['manifest_sha256']!=file_digest(folder/'manifest.json'):raise ValueError('Cached music manifest changed')
    takes={};sources={}
    for identifier,row in receipt['takes'].items():
        source=basic.checked(folder/'assets'/row['audio'],row['sha256'])
        if row['cue']['kind']=='ambience':
            if row['checks'].get('vocals')!='not_applicable_procedural':raise ValueError('Unknown ambience provenance')
        elif row['checks'].get('vocals')!='none_heard':raise ValueError('Cached instrumental lacks historical no-vocals evidence')
        takes[identifier]={'audio_key':row['sha256'],'duration':row['seconds']};sources[row['sha256']]=source
    chapter=state['chapters'][cid];times={r['id']:r['start'] for r in timeline}
    scenes=[s for s in manifest['layout']['scenes'] if s['chapter_id']==cid and s['anchor_segment_id'] in times]
    if not scenes:raise ValueError('Cached scene anchors do not cover the new published prefix')
    ending=None
    if cid==ready['chapters'][-1] and chapter['complete']:
        cue=next(c for c in manifest['layout']['takes'] if c['kind']=='ending')
        if cue['id'] not in takes:raise ValueError('Cached ending missing')
        ending={**cue,'anchor_segment_id':timeline[-1]['id'],'start':max(0,chapter['seconds']-music.CUES['ending']['accent_after']-timeline[-1]['start'])}
    book=read(output.parent/'source/book.json');blocks=next(c['blocks'] for c in book['chapters'] if c['id']==cid)
    markers=read(output/'section-markers.json')['confirmed'] if (output/'section-markers.json').exists() else []
    boundaries=source_boundaries(timeline,blocks,markers)
    transitions=windows(timeline,boundaries,chapter['seconds'],chapter['complete'] and cid!=ready['chapters'][-1])
    layers=bridge_layers(music.scene_layers(scenes,takes,times,chapter['seconds'],ending),scenes,takes,times,transitions)
    placed=[{**layer,'path':str(sources[layer['key']]),'gain':ready['music_volume_scale'],'kind':layer.get('kind','bed')} for layer in layers]
    tail=0.
    if ending:
        take=takes[ending['id']];at=times[ending['anchor_segment_id']]+ending['start']
        placed.append({'path':str(sources[take['audio_key']]),'at':at,'window':None,'gain':ready['music_volume_scale'],'kind':'ending','duration':take['duration']})
        tail=max(0,at+take['duration']-chapter['seconds'])
    key=digest({'ready':file_digest(output/'ready.json'),'chapter':cid,'placed':placed,'seconds':chapter['seconds'],'tail':tail})
    track=MEDIA/('one-shot-music-'+key+'.wav')
    if not track.exists():mix_music(placed,chapter['seconds'],tail,track)
    chapter['music']=[{'kind':'scene_bed','start':0,'seconds':chapter['seconds'],'stream':PREFIX+track.name,'sha256':file_digest(track),
        'qualification':'Reused historically independently verified YuE2 phrases and procedural ambience; no new music generation or recognition.'}]
    if tail:
        ending_path=MEDIA/('one-shot-ending-'+key+'.wav')
        if not ending_path.exists():
            with sf.SoundFile(track) as stream:
                stream.seek(round(chapter['seconds']*stream.samplerate));data=stream.read(dtype='float32',always_2d=True)
                sf.write(ending_path,data,stream.samplerate,subtype='PCM_16')
        chapter['music'].append({'kind':'ending','start':chapter['seconds'],'seconds':tail,'stream':PREFIX+ending_path.name,'sha256':file_digest(ending_path)})
    state['music']={'default_on':True,'volume':ready['music_volume_scale'],'status':'available','source':'existing verified instrumental cache','new_recognition':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source-generation',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--chapters',nargs='+',default=['c0001','c0002','c0003']);parser.add_argument('--stage',choices=['prepare','render','all'],default='prepare')
    parser.add_argument('--data-dir',type=Path);parser.add_argument('--batch-size',type=int,default=32);parser.add_argument('--timeout',type=int,default=1800)
    args=parser.parse_args();output=args.output.resolve()
    if not 1<=args.batch_size<=64 or not 60<=args.timeout<=7200:parser.error('Invalid native batch/timeout')
    with basic.exclusive(output):
        if args.stage in ('prepare','all'):prepare(args.source_generation.resolve(),output,args.chapters)
        if args.stage in ('render','all'):return render(output,args.data_dir.resolve() if args.data_dir else None,args.batch_size,args.timeout)
    return 0


if __name__=='__main__':raise SystemExit(main())
