"""Publish verified evaluation repairs without waiting for neighbouring failures."""
import copy,re,subprocess
from pathlib import Path
import numpy as np
import soundfile as sf
from higgs_trial_io import read_json as read
from rimsky.util import digest,file_digest,write_json,atomic_write
from rimsky.audio_join import boundary_check,prepare_clip

def reject(generation,chapter,uid,reason,receipt=None):
    record={'unit_id':uid,'reason':str(reason),'receipt':receipt,'requires_recovery':True,'flag_cleared':False}
    write_json(generation/chapter/'verified-word-publication/rejections'/(digest(record)+'.json'),record)


def checked_take(generation,row,audits):
    from regenerate_higgs_book_trial import cached_audit
    from higgs_secondary_verification import apply_secondary
    unit=row['unit'];receipt=row['receipt'];wav=Path(row['path']);check=row['word_check']
    if check.get('status')!='asr_exact_match':return False
    from render_higgs_book_trial import spoken_text
    from rimsky.spoken_edits import apply_spoken_edit
    from rimsky.spoken_pronunciation import unmarked
    from rimsky.speech_quality import word_tokens
    expected=spoken_text(unit['text'],unit.get('spoken_number_policy','grouped-digits-v2'))
    if unit.get('finish_fragment') and expected and expected[-1] not in '.!?…,:;':expected+=','
    if unit.get('spoken_text_plan'):_,expected=apply_spoken_edit(expected,unit['spoken_text_plan'])
    if word_tokens(unmarked(expected))!=word_tokens(unit['spoken']):
        raise ValueError('Word repair expected transcript differs from source words')
    if (file_digest(wav)!=receipt['audio_sha256'] or digest(unit['synthesis'])!=receipt['key']
        or check['audio_sha256']!=receipt['audio_sha256'] or check['synthesis_key']!=receipt['key']
        or check['expected_text_sha256']!=digest(unit['spoken'].encode('utf8'))):
        raise ValueError('Unbound word repair audio or synthesis')
    plan=unit['synthesis']
    if plan.get('effects_policy')!='disabled' or plan.get('backend') not in ('higgs_audio_cpp','qwen3_audio_cpp') or plan['prompt']!=plan['text']:
        raise ValueError('Word repair escaped the disabled-effects Higgs path')
    if wav.parent not in audits:audits[wav.parent]=cached_audit(wav.parent)
    audit=audits[wav.parent]
    if not audit or unit['id'] not in audit['checks']:return False
    primary=audit['checks'][unit['id']]
    if any(primary.get(k)!=check.get(k) for k in ('audio_sha256','synthesis_key','expected_text_sha256')):
        raise ValueError('Word repair differs from its checkpointed audit')
    if primary['status']=='asr_exact_match':return True
    evidence=Path(check.get('independent_report','')).resolve()
    if not evidence.is_relative_to(generation.resolve()) or not evidence.is_file():return False
    if file_digest(evidence)!=check.get('independent_report_sha256'):
        raise ValueError('Independent word repair evidence changed')
    reproduced=apply_secondary({unit['id']:{**row,'word_check':primary}},[evidence])[unit['id']]['word_check']
    # Re-normalizing the primary report changes its implementation fingerprint;
    # that historical disagreement is retained, but it is not the independent
    # witness. Require every field of the recomputed witness receipt to match.
    def witness(value):return {k:v for k,v in value.items() if k!='primary_disagreement'}
    return witness(reproduced)==witness(check) and reproduced['status']=='asr_exact_match'


def refresh_saved_witness(generation,row,audits):
    """Re-run comparison on bound raw evidence after a comparator code update."""
    from regenerate_higgs_book_trial import cached_audit
    from higgs_secondary_verification import apply_secondary
    check=row['word_check'];wav=Path(row['path']);unit=row['unit'];receipt=row['receipt']
    evidence=Path(check.get('independent_report','')).resolve()
    if (not evidence.is_relative_to(generation.resolve()) or not evidence.is_file()
        or file_digest(evidence)!=check.get('independent_report_sha256')):return row
    if wav.parent not in audits:audits[wav.parent]=cached_audit(wav.parent)
    audit=audits[wav.parent]
    if not audit or unit['id'] not in audit['checks']:return row
    primary=audit['checks'][unit['id']]
    if (file_digest(wav)!=receipt['audio_sha256'] or digest(unit['synthesis'])!=receipt['key']
        or any(primary.get(k)!=check.get(k) for k in ('audio_sha256','synthesis_key','expected_text_sha256'))):
        raise ValueError('Cannot refresh an unbound word witness')
    fresh=apply_secondary({unit['id']:{**row,'word_check':primary}},[evidence])[unit['id']]
    if fresh['word_check']['status']!='asr_exact_match':return row
    return {**fresh,'witness_revalidation':{'previous_verifier_sha256':check['verifier_sha256'],
        'current_verifier_sha256':fresh['word_check']['verifier_sha256'],
        'independent_report_sha256':file_digest(evidence),'recognition_rerun':False,
        'basis':'Current strict comparator reproduced an exact match from the same immutable raw ASR evidence.'}}


def publish_verified_repairs(generation,chapter,selected,state,gains):
    from render_higgs_palette_preview import MEDIA,PAGE,page
    from higgs_preview_music import attach_music
    ready=state['chapters'].get(chapter)
    if not ready or not ready.get('word_flags'):return False
    from higgs_published_timeline import read_timeline,commit_publication
    timeline=read_timeline(generation,chapter,ready)
    if len(timeline)!=ready['units']:raise ValueError('Word repair timeline differs from available chapter')
    registry=generation/'spoken-take-revisions.json'
    protected=set(read(registry).get('units',{})) if registry.exists() else set()
    # This is an evaluation asset already available to the listener. Never
    # publish new unchecked speech, replace explicit listening corrections, or
    # split a mechanical card whose separate whole-block gate has not passed.
    targets={r['id']:r for r in timeline if r.get('word_status') not in ('asr_exact_match','nonverbal_not_spoken_text')}
    selected=dict(selected);replacements={};audits={}
    for uid,row in selected.items():
        if uid not in targets or uid in protected:continue
        try:
            unit=row['unit'];target=targets[uid]
            if unit.get('presentation_route')=='mechanical_stats' or target.get('voice')=='mechanical_stats':continue
            if (unit['speaker'],unit['render_voice'],unit['mood'])!=(target['speaker'],target['voice'],target['mood']):continue
            if unit['text'].strip()!=target['text'].strip():raise ValueError('Word repair changed source text')
            if (re.search(r'(?<![\w\d])\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?![\w\d])',unit['text'])
                and unit.get('spoken_number_policy')!='grouped-digits-v2'):continue
            valid=checked_take(generation,row,audits)
            if not valid and row['word_check'].get('status')=='asr_exact_match':
                row=refresh_saved_witness(generation,row,audits)
                valid=checked_take(generation,row,audits)
                selected[uid]=row
            if not valid:
                if row['word_check'].get('status')=='asr_exact_match':
                    reject(generation,chapter,uid,'Exact-word receipt could not be independently reproduced',row.get('receipt'))
                continue
            data,rate=sf.read(row['path'],dtype='float32');edge=boundary_check(data,rate)
            if rate!=24000 or edge['unsafe_start'] or edge['unsafe_end']:
                reject(generation,chapter,uid,'Candidate has unsafe audio edges or a different sample rate',row.get('receipt'))
                continue
            data,_=prepare_clip(data,rate);actor=unit['speaker']+':'+unit['render_voice']
            if actor not in gains:raise ValueError('Word repair lacks its frozen voice gain')
            data*=gains[actor]
            if np.max(np.abs(data))>=.999:raise ValueError('Frozen word repair gain clips audio')
            replacements[uid]=np.clip(np.floor(data*32768),-32768,32767).astype('int16')
        except (ValueError,KeyError,OSError) as exc:
            reject(generation,chapter,uid,exc,row.get('receipt'))
    if not replacements:return False
    wave=MEDIA/Path(ready['stream']).name
    if file_digest(wave)!=ready['wav_sha256']:raise ValueError('Available chapter changed before word repairs')
    original,rate=sf.read(wave,dtype='int16');parts=[];cursor=0;delta=0
    before=copy.deepcopy(ready)
    for item in timeline:
        start,end=round(item['start']*rate),round(item['end']*rate)
        if item['id'] in replacements:
            new=replacements[item['id']];parts.extend([original[cursor:start],new]);cursor=end
            item['start']+=delta/rate;delta+=len(new)-(end-start);item['end']+=delta/rate
            unit=selected[item['id']]['unit']
            item.update(speaker=unit['speaker'],voice=unit['render_voice'],mood=unit['mood'],word_status='asr_exact_match')
        elif delta:item['start']+=delta/rate;item['end']+=delta/rate
    parts.append(original[cursor:]);assembled=np.concatenate(parts)
    out=generation/chapter/'verified-word-publication';out.mkdir(exist_ok=True)
    scratch=out/'patched-prefix.wav';sf.write(scratch,assembled,rate,subtype='PCM_16')
    sha=file_digest(scratch);destination=MEDIA/(chapter+'-'+sha[:12]+'.wav')
    if destination.exists():
        if file_digest(destination)!=sha:raise ValueError('Immutable word repair asset changed')
    else:atomic_write(destination,scratch.read_bytes())
    mp3=PAGE/(chapter+'-'+sha[:12]+'.mp3')
    if not mp3.exists():
        temporary=mp3.with_suffix('.building.mp3')
        subprocess.run(['C:/ffmpeg/bin/ffmpeg.exe','-hide_banner','-loglevel','error','-y','-i',str(destination),
            '-codec:a','libmp3lame','-b:a','128k',str(temporary)],check=True);temporary.replace(mp3)
    ready=copy.deepcopy(ready)
    ready.update(seconds=len(assembled)/rate,wav_sha256=sha,
        stream='/character-rb2/full/clips/elirm-iv-mvp/'+destination.name,audio=mp3.name,
        word_flags=sum(r.get('word_status') not in ('asr_exact_match','nonverbal_not_spoken_text') for r in timeline))
    write_json(out/('publication-'+sha[:16]+'.json'),{'previous_wav_sha256':before['wav_sha256'],
        'new_wav_sha256':sha,'changed_units':sorted(replacements),'preserved_other_pcm':True,
        'remaining_flags':ready['word_flags'],'human_approval':False,
        'takes':{uid:{'audio_sha256':selected[uid]['receipt']['audio_sha256'],
                      'synthesis_key':selected[uid]['receipt']['key'],'check':selected[uid]['word_check'],
                      **({'witness_revalidation':selected[uid]['witness_revalidation']} if selected[uid].get('witness_revalidation') else {})} for uid in replacements}})
    commit_publication(generation,chapter,ready,timeline,state)
    print('PUBLISHED VERIFIED WORD REPAIRS',chapter,len(replacements),'remaining',ready['word_flags'],flush=True)
    return True


def publish_saved_repairs(generation,state,gains):
    """Resume publication from exact checkpointed selections, without rendering."""
    for chapter,ready in state['chapters'].items():
        if not ready.get('word_flags'):continue
        root=generation/chapter
        paths=sorted(root.glob('chunk-*/**/selected.json'),key=lambda p:p.stat().st_mtime_ns)
        for path in paths:
            if 'word-' in str(path.parent.relative_to(root)) or 'diagnosis' in str(path.parent):continue
            try:
                from regenerate_higgs_book_trial import cached_audit
                from higgs_secondary_verification import cached_secondary
                rows=read(path);audits={};fresh={}
                for uid,row in rows.items():
                    take=Path(row['path']).parent
                    if take not in audits:audits[take]=cached_audit(take)
                    primary=(audits[take] or {}).get('checks',{}).get(uid)
                    if primary and all(primary.get(k)==row['word_check'].get(k)
                        for k in ('audio_sha256','synthesis_key','expected_text_sha256')):
                        fresh[uid]={**row,'word_check':primary}
                    else:fresh[uid]=row
                fresh=cached_secondary(path.parent,fresh)
                publish_verified_repairs(generation,chapter,fresh,state,gains)
            except (ValueError,KeyError,OSError) as exc:reject(generation,chapter,'checkpoint:'+str(path.relative_to(generation)),exc)
