"""Shared block-level presentation classification for palette evaluation."""
from rimsky.narrative_frames import SYSTEM,request_for,schema_for,checked_ranges,reconcile,adjudicate
from rimsky.util import file_digest,write_json,digest,atomic_write
from pathlib import Path

def classify(calls,out,book,chapter):
    request=request_for(book,chapter)
    # This migration checks only displayed readouts. Existing opening/historical
    # choices remain frozen. The production frame pass uses the full outline.
    request['comparative_anchors']=[]
    schema=schema_for(request)
    for field,values in [('role',['main_story','system_readout']),('register',['ordinary','mechanical'])]:
        schema['properties']['ranges']['items']['properties'][field]['enum']=values
    system=SYSTEM+' For this scoped presentation check classify ordinary story versus system readout only; retain every target. Do not reclassify opening/historical performance.'
    valid=lambda answer:checked_ranges(answer,request)
    primary=calls.call(out,'primary',system,request,schema,valid)
    critic=calls.call(out,'critic',system+' Independently establish the entire display boundary from source meaning and formatting.',request,schema,valid)
    report=reconcile(primary,critic,request)
    if any(r['status']=='needs_adjudication' for r in report['by_source'].values()):
        answer=calls.call(out,'adjudication',system+' Resolve only the supplied disputes.',
            {**request,'primary':primary,'critic':critic,'checked':report['by_source']},schema,
            lambda a:adjudicate(report,a,request))
        report=adjudicate(report,answer,request)
    write_json(out/'report.json',report)
    return report

def apply_frames(rows,frames,groups):
    """A checked readout owns one palette route across all of its sentences."""
    options=sorted(k for k,g in groups.items() if g['voice_family']=='mechanical_stats')
    narrator_options=[k for k in options if groups[k]['character']=='narrator']
    if len(narrator_options)==1:options=narrator_options
    for row in rows:
        frame=frames.get(row['source_id'])
        if not frame or frame.get('register')!='mechanical':continue
        if frame['status'] not in ('checked_proposal','adjudicated_proposal'):
            raise ValueError('Mechanical presentation boundary requires adjudication')
        if len(options)!=1:raise ValueError('Mechanical readout requires one frozen reviewed palette template')
        row['_template']=options[0];row['presentation_route']='mechanical_stats'
        row['presentation_frame']=frame
    return rows

def repair_requested_readouts(generation,book,rows,frames,calls,cast,groups,context,state,gains,bank):
    """Prioritize source-bound presentation corrections through the normal gate."""
    from higgs_trial_io import read_json as read
    from render_higgs_palette_preview import assignments,check_and_retake
    from higgs_opus_word_recovery import SectionHeld
    queue=generation/'presentation-priority.json'
    if not queue.exists():return
    request=read(queue)
    if request['source_sha256']!=book['source_hash']:raise ValueError('Presentation priority belongs to another book')
    for wanted in request['ranges']:
        chapter=wanted['chapter'];first,last=wanted['first'],wanted['last']
        blocks={b['id']:b for c in book['chapters'] if c['id']==chapter for b in c['blocks']}
        own={key:frame for key,frame in frames.items() if frame.get('register')=='mechanical'
             and frame['first']==first and frame['last']==last and key in blocks}
        if not own or any(f['status'] not in ('checked_proposal','adjudicated_proposal') for f in own.values()):
            raise ValueError('Priority readout has no complete checked boundary')
        target=[row for row in rows if row['chapter_id']==chapter and row['source_id'] in own]
        if {row['source_id'] for row in target}!=set(own):raise ValueError('Priority readout omits source paragraphs')
        out=generation/chapter/('readout-repair-'+digest({'frames':own,'source':book['source_hash'],'bank':file_digest(bank)})[:16])
        assignment={'source_sha256':book['source_hash'],'cast':[],
            'units':assignments(calls,out/'direction',target,blocks,cast,groups,{**context,'presentation_frames':own}),
            'reference_first':True,'bank_sha256':file_digest(bank),'effects_policy':'disabled'}
        path=out/'assignment.json'
        if path.exists() and read(path)!=assignment:raise ValueError('Frozen presentation repair changed')
        write_json(path,assignment)
        state.update(status='rendering',active_chapter=chapter,active_presentation=first)
        write_json(generation/'progress.json',state)
        try:checked=check_and_retake(out,assignment,chapter,groups=groups)
        except SectionHeld as exc:
            write_json(out/'held.json',{'reason':str(exc),'source_sha256':book['source_hash']})
            continue
        selected={row['unit']['id']:row for row in checked}
        publish_checked_readouts(generation,chapter,selected,own,state,gains)
        write_json(out/'complete.json',{'assignment_sha256':file_digest(path),
            'selected_sha256':file_digest(out/'selected.json'),'word_flags':0,
            'source_sha256':book['source_hash'],'human_approval':False})
    state.pop('active_presentation',None)


def publish_checked_readouts(generation,chapter,selected,frames,state,gains):
    """Repair available evaluation audio after a complete display passes.

    Preserve every sample outside the changed display sentences. This is an
    automatic presentation correction, never a human verdict or chapter pass.
    """
    import numpy as np
    import soundfile as sf
    import subprocess
    from render_higgs_palette_preview import MEDIA,PAGE,page
    from higgs_trial_io import read_json as read
    from rimsky.audio_join import boundary_check,prepare_clip
    from regenerate_higgs_book_trial import cached_audit
    ready=state['chapters'].get(chapter)
    if not ready:return False
    from higgs_published_timeline import read_timeline,commit_publication
    timeline=read_timeline(generation,chapter,ready)
    eligible={r['id'] for r in timeline if r['id'] in selected and
        frames.get(selected[r['id']]['unit']['source_id'],{}).get('register')=='mechanical'}
    if not eligible:return False
    # The complete display must pass, including its first identity/header and
    # final row. An independently held sentence cannot be omitted from it.
    source_ids={selected[uid]['unit']['source_id'] for uid in eligible}
    groups={(frames[s]['first'],frames[s]['last']) for s in source_ids}
    complete_sources={s for s,f in frames.items() if (f.get('first'),f.get('last')) in groups}
    if source_ids!=complete_sources:return False
    needed={uid for uid,r in selected.items() if (frames.get(r['unit']['source_id'],{}).get('first'),
        frames.get(r['unit']['source_id'],{}).get('last')) in groups}
    if needed!=eligible:return False
    audit_cache={};replacements={}
    for uid in eligible:
        row=selected[uid];unit=row['unit'];receipt=row['receipt'];check=row['word_check']
        frame=frames[unit['source_id']];wav=Path(row['path']);target=next(r for r in timeline if r['id']==uid)
        import re
        if (re.search(r'(?<![\w\d])\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?![\w\d])',unit['text'])
            and unit.get('spoken_number_policy')!='grouped-digits-v2'):return False
        if (frame['status'] not in ('checked_proposal','adjudicated_proposal')
            or unit['voice']!='mechanical_stats' or unit['mood']!='neutral'
            or unit.get('presentation_route')!='mechanical_stats'
            or unit['palette_assignment'].get('presentation_frame_sha256')!=digest(frame)
            or unit['text'].strip()!=target['text'].strip()):
            raise ValueError('Display replacement escaped its checked source/palette route')
        if check['status']!='asr_exact_match':return False
        if (file_digest(wav)!=receipt['audio_sha256'] or check['audio_sha256']!=receipt['audio_sha256']
            or digest(unit['synthesis'])!=receipt['key'] or check['synthesis_key']!=receipt['key']
            or check['expected_text_sha256']!=digest(unit['spoken'].encode('utf8'))):
            raise ValueError('Display replacement has unbound audio or words')
        if wav.parent not in audit_cache:audit_cache[wav.parent]=cached_audit(wav.parent)
        audit=audit_cache[wav.parent]
        if not audit or audit['checks'].get(uid,{}).get('status')!='asr_exact_match':return False
        data,rate=sf.read(wav,dtype='float32');edge=boundary_check(data,rate)
        if rate!=24000 or edge['unsafe_start'] or edge['unsafe_end']:return False
        data,_=prepare_clip(data,rate)
        actor=unit['speaker']+':'+unit['render_voice']
        if actor not in gains:raise ValueError('Display replacement lacks its frozen reference gain')
        data=data*gains[actor]
        if np.max(np.abs(data))>=.999:raise ValueError('Frozen display gain would clip speech')
        replacements[uid]=np.clip(np.floor(data*32768),-32768,32767).astype('int16')
    wave=MEDIA/Path(ready['stream']).name
    if file_digest(wave)!=ready['wav_sha256']:raise ValueError('Available chapter changed before display repair')
    original,rate=sf.read(wave,dtype='int16')
    parts=[];cursor=0;delta=0;changed=[]
    for item in timeline:
        start,end=round(item['start']*rate),round(item['end']*rate)
        if item['id'] in replacements:
            new=replacements[item['id']]
            if np.array_equal(original[start:end],new):
                unit=selected[item['id']]['unit']
                if (item.get('voice'),item.get('mood'),item.get('word_status'))!=(unit['voice'],unit['mood'],'asr_exact_match'):
                    item.update(speaker=unit['speaker'],voice=unit['voice'],mood=unit['mood'],word_status='asr_exact_match')
                    changed.append(item['id'])
                item['start']+=delta/rate;item['end']+=delta/rate
                continue
            parts.extend([original[cursor:start],new]);cursor=end
            item['start']+=delta/rate
            delta+=len(new)-(end-start);item['end']+=delta/rate
            unit=selected[item['id']]['unit']
            item.update(speaker=unit['speaker'],voice=unit['voice'],mood=unit['mood'],word_status='asr_exact_match')
            changed.append(item['id'])
        elif delta:
            item['start']+=delta/rate;item['end']+=delta/rate
    if not changed:return False
    parts.append(original[cursor:]);assembled=np.concatenate(parts)
    scratch=generation/'presentation-routes-v1'/chapter/'patched-prefix.wav'
    sf.write(scratch,assembled,rate,subtype='PCM_16')
    sha=file_digest(scratch);destination=MEDIA/(chapter+'-'+sha[:12]+'.wav')
    if destination.exists():
        if file_digest(destination)!=sha:raise ValueError('Immutable display asset changed')
    else:atomic_write(destination,scratch.read_bytes())
    mp3=PAGE/(chapter+'-'+sha[:12]+'.mp3')
    subprocess.run(['C:/ffmpeg/bin/ffmpeg.exe','-hide_banner','-loglevel','error','-y','-i',str(destination),
        '-codec:a','libmp3lame','-b:a','128k',str(mp3)],check=True)
    import copy
    ready=copy.deepcopy(ready)
    ready.update(seconds=len(assembled)/rate,wav_sha256=sha,
        stream='/character-rb2/full/clips/elirm-iv-mvp/'+destination.name,audio=mp3.name,
        word_flags=sum(r['word_status'] not in ('asr_exact_match','nonverbal_not_spoken_text') for r in timeline))
    ready.setdefault('presentation_revisions',[]).extend(changed)
    write_json(generation/'presentation-routes-v1'/chapter/('publication-'+digest(frames)[:16]+'.json'),{
        'changed_units':changed,'source_frames_sha256':digest(frames),'previous_wav_sha256':file_digest(wave),
        'new_wav_sha256':sha,'preserved_other_pcm':True,'human_approval':False,
        'chapter_recognition_flags_remaining':ready['word_flags']})
    commit_publication(generation,chapter,ready,timeline,state)
    print('PUBLISHED CHECKED DISPLAY CORRECTIONS',chapter,len(changed),'remaining chapter flags',ready['word_flags'],flush=True)
    return True

