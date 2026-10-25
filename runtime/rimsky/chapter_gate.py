"""Bound publication gate shared by app jobs and listening previews.

Missing measurements are unavailable, never zero failures. Qualification may
be bypassed only for a disclosed evaluation pack; audio checks may not.
"""
from .util import digest, SpeechHoldError

CHECKS=('words','edges','voice_continuity','voice_distinctness','scene_loudness','sound_events','direction','qualification')

def report(binding,checks,evaluation_only=False):
    rows={}
    for name in CHECKS:
        values=checks.get(name,[{'status':'unavailable','reason':'No bound measurement supplied.'}])
        if not isinstance(values,list):raise ValueError('Gate measurements must be lists')
        counts={key:0 for key in ('pass','fail','unavailable','bypassed')}
        for item in values:
            status=item.get('status')
            if status not in counts:raise ValueError('Unknown chapter-gate measurement status')
            if status=='bypassed' and not (name=='qualification' and evaluation_only):
                raise ValueError('Only evaluation control qualification may be bypassed')
            counts[status]+=1
        rows[name]={'counts':counts,'items':values,
                    'passed':not counts['fail'] and not counts['unavailable']}
    result={'schema':1,'binding':binding,'checks':rows,
            'evaluation_only':bool(evaluation_only),'qualification_bypassed':bool(rows['qualification']['counts']['bypassed']),
            'passed':all(row['passed'] for row in rows.values())}
    return {**result,'report_sha256':digest(result)}

def require(value,binding):
    if not isinstance(value,dict) or value.get('binding')!=binding:
        raise SpeechHoldError('Chapter acceptance report is missing or stale; nothing was published.')
    saved={k:v for k,v in value.items() if k!='report_sha256'}
    if value.get('report_sha256')!=digest(saved):raise SpeechHoldError('Chapter acceptance report changed.')
    rebuilt=report(binding,{k:v['items'] for k,v in value['checks'].items()},value['evaluation_only'])
    if rebuilt!=value:raise SpeechHoldError('Chapter acceptance counts do not match the measurements.')
    if not value['passed']:
        failed=', '.join(k for k,v in value['checks'].items() if not v['passed'])
        raise SpeechHoldError('Chapter acceptance is held: '+failed+'. Nothing was published.')

def audio_binding(plan_sha256,takes):
    return digest({'plan_sha256':plan_sha256,'takes':{
        key:{k:row.get(k) for k in ('key','audio_sha256','audio')} for key,row in sorted(takes.items())}})

def acoustic_signature(voice):
    signature=voice.get('signature') or {}
    return {'family':voice.get('family'),'reference':voice.get('reference'),
            'permanent_tags':signature.get('permanent_tags',[]),'gain':signature.get('gain',1.)}

def voice_pair_binding(left,right,model_sha256):
    # Qualification follows the acoustic voices, not book-specific identity
    # IDs. Reusing the same qualified pair need not repeat a listening task.
    return digest({'model_sha256':model_sha256,
                   'signatures':sorted([acoustic_signature(left),acoustic_signature(right)],key=digest)})

def generation_binding(segments,pack_fingerprint,root):
    from .util import file_digest
    takes={}
    for segment in segments:
        key=segment['audio_hash'];path=root/'audio'/f'{key}.wav' if key else None
        takes[segment['id']]={'key':key,'audio':str(path) if path else None,
            'audio_sha256':file_digest(path) if path and path.exists() else None}
    bound=[{k:s[k] for k in ('id','source_id','start','end','spoken','speaker','performance')} for s in segments]
    return audio_binding(digest({'segments':bound,'pack':pack_fingerprint}),takes)


def chapter_parts_binding(segments):
    return digest([{k:s.get(k)for k in ('id','source_id','start','end','spoken','speaker','performance','audio_hash','duration')}
        for s in segments])


def processing_origin(generation,chapter_id):
    """A retained playable chapter keeps its original processing provenance."""
    from .post_processing import mode
    analysis=generation.get('analysis')or{}
    segments=[s for s in generation.get('segments',[])if s['chapter_id']==chapter_id]
    saved=(analysis.get('chapter_processing')or{}).get(chapter_id)
    if saved:
        if saved.get('policy')!='chapter-processing-origin-v1'or saved.get('sha256')!=digest({k:v for k,v in saved.items()if k!='sha256'}):
            raise SpeechHoldError('Retained chapter processing provenance changed.')
        if saved.get('parts_sha256')==chapter_parts_binding(segments)and all(s['status']=='ready'for s in segments):
            mode({'post_processing':saved.get('post_processing')})
            return saved
    result={'policy':'chapter-processing-origin-v1','post_processing':mode(generation.get('config')or{}),
        'recognition_status':'not_checked'if mode(generation.get('config')or{})=='off'else'previously_enabled',
        'pack_fingerprint':generation['config'].get('pack_fingerprint'),
        'parts_sha256':chapter_parts_binding(segments),'directed':bool(analysis.get('scene_direction')),
        'gate':analysis.get('chapter_acceptance',{}).get(chapter_id)}
    result['sha256']=digest(result);return result

def music_released(generation,chapter_id,root):
    try:origin=processing_origin(generation,chapter_id)
    except SpeechHoldError:return False
    if origin['post_processing']=='off':return True
    analysis=generation.get('analysis') or {}
    # Legacy generations have no chapter direction/gate and retain their
    # existing behavior. New directed generations never bypass this check.
    if not origin['directed']:return True
    segments=[s for s in generation['segments'] if s['chapter_id']==chapter_id]
    gate=origin['gate']
    if not gate or not gate.get('passed'):return False
    try:
        require(gate,generation_binding(segments,origin['pack_fingerprint'],root))
        return bool(segments) and all(s['status']=='ready' for s in segments)
    except SpeechHoldError:return False

def measure(rows,pair_reviews=(),model_sha256=None):
    """Measure exact WAVs; pair/level limits come from listening calibration.

    Each row supplies id, path, synthesis, word_check, direction, and scene.
    Frozen level intervals and pair approvals are never fitted to this chapter.
    """
    import numpy as np
    import soundfile as sf
    from .audio_join import boundary_check
    from .speech_quality import transcript_check
    from .util import file_digest
    checks={key:[] for key in CHECKS};voices={};scenes={}
    for row in rows:
        key=row['id'];plan=row.get('synthesis') or {};signature=plan.get('voice_signature') or {}
        path=row.get('path');direction=row.get('direction') or plan.get('direction')
        def add(name,status,**details):checks[name].append({'id':key,'status':status,**details})
        if not path:
            for name in ('words','edges','direction','scene_loudness','sound_events','qualification'):
                add(name,'unavailable',reason='No accepted candidate audio.')
            continue
        audio_hash=file_digest(path);x,rate=sf.read(path,dtype='float32')
        if row.get('audio_sha256')!=audio_hash:
            for name in ('words','edges','scene_loudness','sound_events'):add(name,'fail',reason='Take hash changed.')
            continue
        event=plan.get('backend')=='nonverbal_asset'
        if event:
            audit=row.get('word_check') or {}
            add('sound_events','pass' if audit.get('status')=='nonverbal_not_spoken_text' and audit.get('audio_sha256')==audio_hash else 'fail')
        else:
            audit=row.get('word_check') or {};expected=plan.get('text',row.get('text',''))
            from .word_verification import verified
            add('words','pass' if verified(expected,audit,audio_hash,audit.get('verifier_sha256'),row.get('language')) else 'fail')
        try:
            edge=boundary_check(x,rate)
            add('edges','fail' if edge['unsafe_start'] or edge['unsafe_end'] else 'pass',measurement=edge)
        except ValueError as exc:add('edges','fail',reason=str(exc))
        add('direction','pass' if direction and not direction.get('needs_adjudication') and direction.get('technique')!='unmapped'
            else 'unavailable',reason='' if direction else 'No complete source-bound direction.')
        add('qualification','bypassed' if plan.get('qualification_bypassed') else
            'pass' if plan.get('performance_selection',{}).get('qualification_sha256') or plan.get('qualification_sha256') else 'unavailable')
        actor=signature.get('character_id') or row.get('speaker')
        route=row.get('presentation_route','narration' if actor=='narrator' else 'dialogue')
        if actor and not event:
            identity=(actor,route);frozen={'family':plan.get('voice_family'),
                'reference':plan.get('reference',{}).get('sha256'),
                'signature':signature}
            previous=voices.setdefault(identity,frozen)
            add('voice_continuity','pass' if previous==frozen else 'fail',signature=frozen)
            scenes.setdefault(row.get('scene',row.get('source_id','chapter')),set()).add(identity)
        if event:continue
        width=max(1,round(.02*rate))
        if x.ndim!=1 or not len(x) or not np.isfinite(x).all():
            add('scene_loudness','fail',reason='Invalid audio.');continue
        padded=np.pad(x,(0,(-len(x))%width));rms=np.sqrt(np.mean(padded.reshape(-1,width)**2,axis=1))
        active=rms[rms>max(.0001,float(np.percentile(rms,95))*.12)]
        level=20*np.log10(max(1e-10,float(np.median(active)) if len(active) else 0))
        register=(direction or {}).get('register','ordinary')
        interval=signature.get('level_policy',{}).get(register)
        if (not isinstance(interval,list) or len(interval)!=2 or not all(
                isinstance(v,(int,float)) and np.isfinite(v) for v in interval) or interval[0]>interval[1]):
            add('scene_loudness','unavailable',active_dbfs=float(level),reason='No frozen listening-calibrated interval for this register.')
        else:add('scene_loudness','pass' if interval[0]<=level<=interval[1] else 'fail',active_dbfs=float(level),interval=interval)
    pairs=set()
    import itertools
    for members in scenes.values():
        pairs.update(tuple(sorted(p)) for p in itertools.combinations(members,2) if p[0][0]!=p[1][0] and
                     p[0][0]!='narrator' and p[1][0]!='narrator')
    for left,right in sorted(pairs):
        binding=voice_pair_binding(voices[left],voices[right],model_sha256)
        identical=acoustic_signature(voices[left])==acoustic_signature(voices[right])
        approved=any(r.get('binding')==binding and r.get('verdict')=='approved' and r.get('listening_checked') is True
                     and r.get('measurement_checked') is True for r in pair_reviews)
        checks['voice_distinctness'].append({'pair':[left,right],'binding':binding,
            'status':'fail' if identical else 'pass' if approved else 'unavailable',
            'reason':'Co-scene characters have identical acoustic signatures.' if identical else '' if approved else
                     'Co-scene voices lack bound listening and measured qualification.'})
    return checks
