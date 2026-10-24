"""Render and publish complete chapter listening trials, without label mutations.

Runs sentence requests in a persistent audio.cpp session. Controls are candidate
controls, never silently dropped or promoted to production listening approvals.
"""
from __future__ import annotations
import argparse
import copy
import html
import json
import math
import os,re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.higgs_backend import HiggsBackend, synthesis_plan, tag_token
from rimsky.production_higgs_model import require_production_higgs_engine,resolve_production_higgs_model
from rimsky.util import digest, file_digest, write_json, atomic_write
from higgs_trial_io import read_json
from higgs_file_checks import cached_sha
BASE=ROOT/'training-work/higgs-production'
MODEL_PIN=resolve_production_higgs_model(ROOT)
MODEL=MODEL_PIN.path
RUNNER=ROOT/'training-work/higgs-tts-3-pilot/audio-cpp-cuda/audiocpp_cli.exe'
# This production renderer is Higgs-only. Standalone legacy experiments must
# not turn into a model-selection bypass for production or evaluation audio.
ENGINE=require_production_higgs_engine(os.environ.get('RIMSKY_TTS_ENGINE'))
FAMILY,ENGINE_MODEL='higgs_audio_tts',MODEL
# Native outputs are staged under a short root: nested recovery folders exceeded
# Windows' 260-character path limit and the runner could not write its WAV.
STAGING=ROOT/'training-work/stg'
TOKEN_CAP_SECONDS=6.0
TOKEN_CAP_PER_LETTER=0.35
DATASET=BASE/'datasets/reviewed-20261012-v1'
DEST=ROOT/'training-work/character-rb2-full/fine-tune-all/elirm-iv-mvp'
AUDIO_DEST=ROOT/'training-work/character-rb2/full/clips/elirm-iv-mvp'

def read(p): return read_json(p)

def spoken_text(text,number_policy='grouped-digits-v2'):
    original=text
    text=text.strip().lstrip('\u2014\u2013- ').strip()
    if number_policy!='raw-numbers-v1':
        from rimsky.spoken_numbers import expand
        text=expand(text,number_policy)
    text=re.sub(r'\bед\.', 'единиц', text)
    text=text.replace('%',' процентов').replace('+',' плюс ').replace('=',' равно ')
    text=re.sub(r'\bее\b','её',text)
    text=text.replace('(', ', ').replace(')', '').replace('[', ' ').replace(']', ' ')
    text=text.translate(str.maketrans('', '', '«»“”<>'))
    text=re.sub(r'\s+',' ',text).strip()
    text=text.lstrip(', ').strip()
    if re.search(r'[<>{}]',text) or (number_policy!='raw-numbers-v1' and re.search(r'\d',text)): raise ValueError('Unresolved spoken-text symbol: '+original)
    return text

def make_packs(assignment):
    from rimsky.cli_process import run_cli
    from prepare_higgs_listening import candidate_pack
    model_hash=cached_sha(MODEL)
    proof=MODEL_PIN.provenance
    if proof['model_sha256']!=model_hash or proof['kind']!='lora' or proof['optimizer_steps']<1:
        raise ValueError('Custom checkpoint provenance mismatch')
    references=read(DATASET/'references.json')
    # The listener selected Calm as the production narrator's reference register.
    old=read(BASE/'listening/voice-lora-v1/plan.json')
    references['narrator_baseline']=[next(c['reference'] for c in old['cases'] if c['voice']=='narrator_baseline')]
    version=run_cli([str(RUNNER),'--version'],cwd=RUNNER.parent,timeout=30).stdout.strip()
    packs={}
    for voice in sorted({u['voice'] for u in assignment['units']}|{'mechanical_stats'}):
        case={'voice':voice,'reference':references[voice][0],'candidate_tags':[]}
        pack=candidate_pack(DATASET,MODEL,case,RUNNER,version,False)
        pack['voices'][0]['higgs_controls']['moods']={m:{'tags':([] if m=='baseline' else [m]),'reviewed':False}
                                                    for m in MOODS}
        pack['_fingerprint']=digest({k:v for k,v in pack.items() if not k.startswith('_')})
        packs[voice]=pack
    resolved=HiggsBackend().probe({'pack':next(iter(packs.values())),'device':'cuda'})
    return packs,resolved

MOODS=['baseline','amusement','anger','confusion','contemplation','determination','disgust',
       'enthusiasm','fear','sadness','surprise','whispering','shouting']

def profile_pack(pack,unit):
    """An explicit, hashed character register; never mutate the shared family."""
    if unit.get('palette_reference'):
        from rimsky.higgs_reference import check_reference
        selected=copy.deepcopy(pack);reference=unit['palette_reference']
        if reference['voice_family']!=unit['voice'] or reference['mood']!=unit['mood']:
            raise ValueError('Assigned palette voice/mood differs from the selected reviewed performance')
        path=DATASET/reference['audio'];check_reference(path,reference['audio_sha256'])
        relative=path.resolve().relative_to(ROOT).as_posix()
        selected['files'][relative]=reference['audio_sha256']
        selected['voices'][0]['references']['neutral']={'audio':relative,'text':reference['text']}
        selected['voices'][0]['higgs_controls']={'moods':{unit['mood']:{'tags':[],'reviewed':False}},
                                              'paces':{'normal':{'tags':[],'reviewed':False}}}
        selected['speech']['effects_policy']='disabled'
        selected['_fingerprint']=digest({'pack':{k:v for k,v in selected.items() if not k.startswith('_')},
                                         'palette_assignment':unit.get('palette_assignment')})
        return selected
    profile=unit.get('delivery_profile')
    if not profile:
        if unit.get('scene_direction'):raise ValueError('Scene direction has no bound delivery profile')
        return pack
    if unit.get('scene_direction'):
        from rimsky.scene_direction import require_realization
        require_realization(unit['scene_direction'],profile)
    if profile.get('blocked_controls'):
        raise ValueError('Required delivery controls are blocked; ordinary speech cannot satisfy the attribution: '+unit.get('id',''))
    if profile.get('realization_status')=='unmapped':
        raise ValueError('Author-directed delivery has no implementation; resolve it before rendering: '+unit.get('id',''))
    refs=read(DATASET/'references.json')
    reference_index=profile.get('reference_index',0)
    family=unit['voice']
    if type(reference_index) is not int or not 0<=reference_index<len(refs.get(family,[])):
        raise ValueError('Character profile has no reviewed family reference')
    reference=refs[family][reference_index]
    if reference.get('reviewed') is not True:raise ValueError('Character reference is not reviewed')
    path=DATASET/reference['audio']
    if file_digest(path)!=reference['audio_sha256']:raise ValueError('Reviewed profile reference changed')
    selected=copy.deepcopy(pack)
    old_reference=selected['voices'][0]['references']['neutral']['audio']
    selected['files'].pop(old_reference,None)
    relative=path.resolve().relative_to(ROOT).as_posix()
    selected['files'][relative]=reference['audio_sha256']
    selected['voices'][0]['references']['neutral']={'audio':relative,'text':reference['text']}
    # Reviewed reference identity and proposed controls remain distinct. There
    # is no official hoarse/muttering tag: an approximation must be disclosed.
    tags=[*profile.get('baseline_tags',[]),*profile.get('technique_tags',[])]
    for tag in tags:tag_token(tag)
    for rule in selected['voices'][0]['higgs_controls']['moods'].values():
        rule['tags']=list(dict.fromkeys([*tags,*rule['tags']]))
        if profile.get('disable_emotion_tags'):
            rule['tags']=[t for t in rule['tags'] if t.startswith(('pitch_','speed_','expressive_')) or t in ('whispering','shouting')]
    selected['_delivery_profile']=copy.deepcopy(profile)
    selected['_fingerprint']=digest({'pack':{k:v for k,v in selected.items() if not k.startswith('_')},'profile':profile})
    return selected

def quality(path,plan):
    import numpy as np
    import soundfile as sf
    x,rate=sf.read(path,dtype='float32',always_2d=True)
    seconds=len(x)/rate
    words=len(re.findall(r'\w+',plan['text']))
    # A real one-word reply can be shorter than 350 ms. It still has to pass
    # level/activity checks and the independent spoken-word audit.
    minimum=.12 if words<=2 else .35
    if x.shape[1]!=1 or rate!=24000 or not np.isfinite(x).all() or not minimum<=seconds<300:
        raise ValueError('Invalid WAV')
    checks={'peak':float(np.abs(x).max()),'rms':float(np.sqrt(np.mean(x*x))),
            'active_fraction':float(np.mean(np.abs(x)>.001)), 'clipped_fraction':float(np.mean(np.abs(x)>=.999))}
    if checks['rms']<.0001 or checks['active_fraction']<.01 or checks['clipped_fraction']>.01:
        raise ValueError('Silence or clipping')
    if seconds*25>=(plan['max_tokens']-8)*.98: raise ValueError('Reached token cap')
    # Implausible rates remain visible; they must not be called word-verified.
    checks['rate_warning']=words>5 and not .6<=words/seconds<=5.5
    return {'duration':seconds,'sample_rate':rate,'audio_checks':checks,'audio_sha256':file_digest(path)}

def sequence_request(ident,plan):
    return {'id':ident,'text':plan['prompt'],'voice_ref':str(ROOT/plan['reference']['audio']),
            'reference_text':plan['reference']['text'],**plan['sampling'], 'max_tokens':plan['max_tokens'],
            'text_chunk_mode':plan['chunk_mode'],'text_chunk_size':plan['chunk_size']}

def onset_pause_plan(synthesis):
    """Request a generated quiet onset; never fade an existing speech sample.

    Pause is a documented inline Higgs control. This remains a candidate until
    the resulting waveform and words are independently checked.
    https://huggingface.co/bosonai/higgs-tts-v3-4b/blob/main/PROMPTING.md
    """
    result=copy.deepcopy(synthesis);text=result['text']
    if not text or not result['prompt'].endswith(text):raise ValueError('Unexpected Higgs prompt layout')
    result['prompt']=result['prompt'][:-len(text)]+'<|prosody:pause|>'+text
    result['onset_pause']={'tag':'pause','basis':'persistent unsafe onset after seed-only retakes',
                           'listening_approved':False}
    return result


class PronunciationBindingError(ValueError):
    """A frozen linguistic preparation decision no longer matches its unit."""


def require_bound_pronunciation(unit_id, automatic):
    """Fail during planning when a frozen v4 linguistic decision no longer binds.

    `prepare()` finishes before native synthesis starts, so this turns unit
    reshaping or punctuation drift into a cheap preparation error instead of
    silently sending ambiguous text to Higgs.
    """
    if not automatic or automatic.get('policy') != 'stress-rare-v4':
        return
    failures = {}
    homograph = automatic.get('homograph_overrides') or {}
    yo = automatic.get('yo_restoration') or {}
    if homograph.get('unbound'):failures['homograph'] = homograph['unbound']
    if yo.get('unbound'):failures['yo'] = yo['unbound']
    if failures:
        raise PronunciationBindingError(
            f'Unbound pronunciation decisions for {unit_id}: {json.dumps(failures, ensure_ascii=False)}')

def prepare(assignment_path,output,chapters,limit=0,reuse_folder=None,reuse_sound_equivalent=False):
    """Plan, receipt, reuse, nonverbal assets and pending native rows of one folder (no native launch)."""
    assignment=read(assignment_path)
    from rimsky.nonverbal import animal_vocalization
    cast=assignment.get('cast',{})
    identities={c['id']:c for c in cast} if isinstance(cast,list) else cast
    for unit in assignment['units']:
        if unit.get('mode')=='speech' and not unit.get('nonverbal_intent'):
            intent=animal_vocalization(unit['text'],identities.get(unit.get('speaker'),{}))
            if intent:unit['nonverbal_intent']=intent
    packs,resolved=make_packs(assignment)
    units=[u for u in assignment['units'] if u['chapter_id'] in chapters]
    if limit: units=units[:limit]
    output.mkdir(parents=True,exist_ok=True)
    model_hash=next(iter(packs.values()))['files'][next(iter(packs.values()))['speech']['model']]
    plan={'purpose':'listening_trial','production_approved':False,'assignment_sha256':file_digest(assignment_path),
          'model_checkpoint':MODEL_PIN.checkpoint,'model_sha256':model_hash,'resolved_speech':resolved,'units':[]}
    profile_cache={}
    frozen_path=output/'render-plan.json'
    # Resume immutable old requests with their original preprocessing policy.
    # New takes use the shared rule; existing published audio is not invalidated.
    frozen_units={u['id']:u for u in read(frozen_path)['units']} if frozen_path.exists() else {}
    for i,unit in enumerate(units):
        if unit.get('nonverbal_intent'):
            from rimsky.nonverbal import asset_plan
            synthesis=asset_plan(unit['nonverbal_intent'],ROOT)
            plan['units'].append({**unit,'render_voice':synthesis['voice_family'],'spoken':'','audio_kind':'nonverbal',
                                 'synthesis':synthesis,'key':digest(synthesis),'audio':unit['id']+'.wav'})
            continue
        policy=unit.get('spoken_number_policy','grouped-digits-v2')
        if unit['id'] in frozen_units:
            old_policy=frozen_units[unit['id']].get('spoken_number_policy','legacy-v1')
            if unit.get('spoken_number_policy') and policy!=old_policy:
                raise ValueError('Spoken-number policy changed; use a new output folder')
            policy=old_policy
        spoken=spoken_text(unit['text'],policy)
        if unit.get('finish_fragment') and not re.search(r'[.!?…,:;]$',spoken):spoken+=','
        # A unit with no closing punctuation (an author remark split from dialogue) ends
        # abruptly: 24/114 such takes had a clipped end against 0/322 ending with a period
        # (2026-10-16). Frozen per generation; words and the word check are unchanged.
        terminal=assignment.get('terminal_punctuation')=='period-if-missing-v1'
        voice=unit['voice']
        # Explicit visible numerical game tables use a presentation route,
        # retaining the author/narrator identity in the saved assignment.
        if (not unit.get('presentation_frame') and re.match(r'^\s*(?:[-–—]\s*)?(?:Здоровье|Мана|Доступно свободных очков|Сила|Ловкость|Выносливость|Интеллект|Восприятие|Харизма|Удача|Мудрость|Влияние)\b',unit['text']) and re.search(r'\d',unit['text']) and unit['mode']=='narrator'):
            voice='mechanical_stats'
        profile_key=digest({'voice':voice,'profile':unit.get('delivery_profile'),'palette':unit.get('palette_reference')})
        if profile_key not in profile_cache:profile_cache[profile_key]=profile_pack(packs[voice],{**unit,'voice':voice})
        pack=profile_cache[profile_key]
        from rimsky.recovery_decoding import execution_pack
        native_spoken=spoken
        if unit.get('spoken_text_plan'):
            from rimsky.spoken_edits import apply_spoken_edit
            native_spoken,spoken=apply_spoken_edit(spoken,unit['spoken_text_plan'])
        # After any reading recipe: a listener recipe written before this policy ("Ай, всё" -> "Ай, всё.")
        # is bound to the unpunctuated wording and stopped the chapters 1-3 run (2026-10-17).
        if terminal:
            if not re.search(r'[.!?…,:;]$',native_spoken):native_spoken+='.'
            if not re.search(r'[.!?…,:;]$',spoken):spoken+='.'
        from rimsky.spoken_pronunciation import phrase_stress
        pronunciation=None
        if unit['id'] not in frozen_units or frozen_units[unit['id']]['synthesis'].get('phrase_pronunciation'):
            native_spoken,pronunciation=phrase_stress(native_spoken)
        automatic=None;respelled=[];context_source=native_spoken
        spec=assignment.get('pronunciation')
        if spec:
            from rimsky.auto_pronunciation import auto_pronunciation,policy_options
            names={cid:row['stressed'] for cid,row in spec['names'].items()}
            names.update(spec.get('proper_nouns',{}))
            options=policy_options(spec['policy'],spec)
            # Listener respellings (е->э/ё) are applied first so every later pass and word check sees the spoken spelling.
            from rimsky.respelling import respell_text
            native_spoken,respelled=respell_text(native_spoken,names,spec.get('common',[]),spec.get('terms'),options.get('implicit_single',False),spec.get('respelling_sources'))
            context_source=native_spoken
            native_spoken,automatic=auto_pronunciation(native_spoken,names,spec.get('common',[]),**options)
            require_bound_pronunciation(unit['id'], automatic)
        suppressed=None
        if unit.get('stress_marks')=='suppress-automatic-v1':
            # Recovery variant: ordinary-word marks removed, name marks kept (user rule: names keep
            # their base stress). Two adjacent marks ("тебе́ позави́дую") drew an inserted syllable
            # on every seed (c0001-u0312, 2026-10-17).
            keep={e['marked'] for e in (automatic or {}).get('name_edits',[])}
            before=native_spoken
            native_spoken=re.sub('[А-Яа-яЁё́]+',lambda m:m[0] if m[0] in keep else m[0].replace('́',''),native_spoken)
            suppressed={'policy':'suppress-automatic-v1','marked_text':before,'kept':sorted(keep)}
        stripped=None
        contextual=None
        if spec and spec.get('contextual_stress') and spec.get('contextual_stress_policy')=='experimental-luna-context-v4':
            from rimsky import contextual_stress
            protected={(r['word'].casefold().replace('ё','е'),r['index']) for r in spec.get('homograph_stress',{}).get('overrides',[])
                       if r['sha256']==digest(context_source.encode('utf8'))}
            protected|={(r['word'].casefold().replace('ё','е'),r['index']) for r in (automatic or {}).get('homograph_overrides',{}).get('applied',[])}
            native_spoken,contextual=contextual_stress.apply(context_source,native_spoken,spec['contextual_stress'],names,spec.get('common',[]),protected)
            contextual['module_sha256']=file_digest(Path(contextual_stress.__file__))
        listener=None
        if assignment.get('listener_stress_snapshot'):
            from rimsky import listener_stress
            native_spoken,listener=listener_stress.apply(native_spoken,assignment['listener_stress_snapshot'],
                source_id=unit.get('source_id'),source_text=assignment.get('listener_stress_sources',{}).get(unit.get('source_id')),
                source_start=unit.get('start',0),source_end=unit.get('end'))
            listener['module_sha256']=file_digest(Path(listener_stress.__file__))
        phonetic=None
        if unit['id'] not in frozen_units or frozen_units[unit['id']]['synthesis'].get('russian_phonetics'):
            from rimsky import russian_pronunciation as phonetics
            before_phonetic=native_spoken
            native_spoken,phonetic_records=phonetics.apply(before_phonetic)
            phonetics.validate(before_phonetic,native_spoken,phonetic_records)
            phonetic={'rule':phonetics.RULE,'prephonetic_source':before_phonetic,'records':phonetic_records,
                      'module_sha256':file_digest(Path(phonetics.__file__))}
        pack=execution_pack(pack,unit.get('decoding_profile'),native_spoken)
        synthesis=synthesis_plan(pack,pack['voices'][0],native_spoken,unit['mood'],None,resolved,unit.get('retake_number',0))
        # Both the human-readable checkpoint and its verified artifact hash are
        # immutable sound inputs. The hash already comes from the voice pack.
        synthesis['model_checkpoint']=MODEL_PIN.checkpoint
        if assignment.get('reference_onset'):
            # Frozen per generation: references starting or ending on speech get a bound padded copy.
            from rimsky.reference_onset import POLICY as ONSET_POLICY,padded
            if assignment['reference_onset']!=ONSET_POLICY:raise ValueError('Unknown reference onset policy')
            synthesis['reference']=padded(synthesis['reference'],ROOT,BASE/'derived-references')
        # Distinct deterministic per-unit seed; retries add to this exact seed.
        synthesis['sampling']['seed']=unit.get('render_seed',(20261013+i)%2**31)
        # A runaway take (no end-of-speech) ran to the 2048-token cap, about 82 s,
        # before failing. Cap generation by text length instead: 6 s + 0.35 s per
        # letter; on 3,279 verified takes none reached 80% of it (2026-10-16).
        # Frozen requests keep their original cap.
        if unit['id'] not in frozen_units or 'token_cap' in frozen_units[unit['id']]['synthesis']:
            letters=len(re.findall(r'[А-Яа-яЁёA-Za-z]',native_spoken))
            cap=min(synthesis['max_tokens'],math.ceil((TOKEN_CAP_SECONDS+TOKEN_CAP_PER_LETTER*letters)*synthesis.get('codec_frame_rate',25))+8)
            synthesis['token_cap']={'policy':'letters-v1','base_seconds':TOKEN_CAP_SECONDS,
                                    'seconds_per_letter':TOKEN_CAP_PER_LETTER,'previous_max_tokens':synthesis['max_tokens']}
            synthesis['max_tokens']=cap
        if unit.get('delivery_profile'):synthesis['character_delivery_profile']=copy.deepcopy(unit['delivery_profile'])
        if unit.get('palette_assignment'):synthesis['palette_assignment']=copy.deepcopy(unit['palette_assignment'])
        if unit.get('spoken_text_plan'):synthesis['spoken_text_plan']=copy.deepcopy(unit['spoken_text_plan'])
        if pronunciation:synthesis['phrase_pronunciation']=pronunciation
        if automatic:synthesis['automatic_pronunciation']=automatic
        if contextual:synthesis['contextual_stress']=contextual
        if listener and (listener.get('edits') or listener.get('applications')):synthesis['listener_stress']=listener
        if phonetic:synthesis['russian_phonetics']=phonetic
        if respelled:
            from rimsky.respelling import disclosure
            synthesis['listener_respelling']=disclosure(respelled)
        if stripped:synthesis['stress_marks_stripped']={'engine':ENGINE,'marked_text':stripped}
        if suppressed:synthesis['stress_marks_suppressed']=suppressed
        if unit.get('finish_fragment'):synthesis['fragment_terminal']='comma_if_unpunctuated'
        if unit.get('onset_pause'):synthesis=onset_pause_plan(synthesis)
        plan['units'].append({**unit,'render_voice':voice,'spoken':spoken,'synthesis':synthesis,
                              'key':digest(synthesis),'audio':unit['id']+'.wav'})
    validate_voice_continuity(plan['units'])
    path=output/'render-plan.json'
    if path.exists():
        # A render plan is the immutable synthesis request for this take.  A
        # resumed run may contain newer preparation code, but already planned
        # (and often already rendered/audited) units must retain their exact
        # native text, keys and provenance.  Only an assignment change is a
        # real conflict; not-yet-planned chunks get the current preparation.
        frozen_plan=read(path)
        if frozen_plan.get('assignment_sha256')!=file_digest(assignment_path):
            raise ValueError('Frozen render plan belongs to a changed assignment; use a new output folder')
        plan=frozen_plan
    else:
        write_json(path,plan)
    receipt_path=output/'receipts.json'
    receipt=read(receipt_path) if receipt_path.exists() else {'plan_sha256':file_digest(path),'takes':{}}
    if receipt['plan_sha256']!=file_digest(path): raise ValueError('Render receipt plan mismatch')
    # Even a zero-output timeout must leave a durable, plan-bound checkpoint.
    write_json(receipt_path,receipt)
    if reuse_folder and (reuse_folder/'receipts.json').exists():
        previous=read(reuse_folder/'receipts.json')
        previous_plan=read(reuse_folder/'render-plan.json')
        if previous['plan_sha256']!=file_digest(reuse_folder/'render-plan.json'):
            raise ValueError('Reuse source does not bind to its frozen plan')
        previous_units={r['id']:r for r in previous_plan['units']}
        previous_checks=read(reuse_folder/'word-checks.json') if (reuse_folder/'word-checks.json').exists() else None
        reused_checks={}
        for row in plan['units']:
            old=previous['takes'].get(row['id'])
            if not old or row['id'] in receipt['takes']: continue
            exact=old['key']==row['key']
            sound_fields=('text','prompt','reference','sampling','model_sha256','runner_sha256','max_tokens','codec_frame_rate','chunk_mode','chunk_size','threads','take','implementation_sha256')
            original=previous_units[row['id']]['synthesis']
            equivalent=reuse_sound_equivalent and all(original.get(k)==row['synthesis'].get(k) for k in sound_fields)
            equivalent &= original.get('resolved',{})==row['synthesis'].get('resolved',{})
            if not exact and not equivalent:continue
            if exact and digest(original)!=digest(row['synthesis']):
                raise ValueError('Same reuse key has a different synthesis plan')
            source_audio=reuse_folder/old['audio']
            if file_digest(source_audio)!=old['audio_sha256']:raise ValueError('Reuse take changed')
            if equivalent:
                import soundfile as sf
                from rimsky.audio_join import boundary_check
                audio,rate=sf.read(source_audio,dtype='float32');edges=boundary_check(audio,rate)
                if edges['unsafe_start'] or edges['unsafe_end']:continue
            shutil.copyfile(source_audio,output/row['audio'])
            receipt['takes'][row['id']]={**old,'key':row['key'],'synthesis_sha256':digest(row['synthesis']),
                'audio':row['audio'],'reuse_source':str(reuse_folder),
                **({'original_synthesis_key':old['key'],'reuse_basis':'identical native sound inputs; fresh boundary check',
                    'original_plan_sha256':previous['plan_sha256']} if equivalent and not exact else {})}
            if previous_checks and previous_checks['plan_sha256']==previous['plan_sha256']:
                check=previous_checks['checks'].get(row['id'])
                if check and check['audio_sha256']==old['audio_sha256'] and check['synthesis_key']==row['key']:
                    reused_checks[row['id']]=check
        if reused_checks and not (output/'word-checks.json').exists():
            write_json(output/'word-checks.json',{**previous_checks,'plan_sha256':file_digest(path),'checks':reused_checks})
        write_json(receipt_path,receipt)
        print('REUSED',len(receipt['takes']),'unchanged exact-key takes',flush=True)
    pending=[]
    for row in plan['units']:
        old=receipt['takes'].get(row['id'])
        if old:
            if old['key']!=row['key'] or file_digest(output/old['audio'])!=old['audio_sha256']:
                raise ValueError('Existing take changed')
        elif row['synthesis'].get('backend')=='nonverbal_asset':
            from rimsky.nonverbal import write_asset
            result=write_asset(row['synthesis'],ROOT,output/row['audio'])
            receipt['takes'][row['id']]={**result,'key':row['key'],'audio':row['audio'],
                                       'synthesis_sha256':digest(row['synthesis'])}
            write_json(receipt_path,receipt)
        else: pending.append(row)
    # Group by voice to reuse encoded reference audio. Request IDs/seed/plan
    # remain unchanged; assembly still follows the original chapter order.
    pending.sort(key=lambda u:(u['render_voice'],u['id']))
    return {'output':output,'path':path,'receipt':receipt,'receipt_path':receipt_path,'units':units,'pending':pending}

def native(ctxs,execution_guard=None):
    """Render the pending rows of every prepared folder in ONE native audio.cpp session per batch.

    Measured 2026-10-17: each take folder cost a 15-20 s model load for ~4.5 s of generation.
    Request ids are namespaced per job (`{job}-{unit id}`) only when several folders share a
    session; an id is a name, never a sound input (seed, text, reference and limits are explicit),
    and the staged WAV is `{request id}.wav`. Each folder keeps its own sequence record, receipt,
    markers and WAVs under the original unit ids.
    """
    from rimsky.run_events import event as run_event
    from rimsky.native_progress import owned_native_process,ProgressBudget,CandidateRenderTimeout
    from rimsky.cli_process import terminate_process_tree
    many=len(ctxs)>1
    items=[{'job':j,'ctx':c,'row':row,'rid':f"{j}-{row['id']}" if many else row['id']} for j,c in enumerate(ctxs) for row in c['pending']]
    # Group by voice across folders to reuse encoded reference audio.
    items.sort(key=lambda i:(i['row']['render_voice'],i['job'],i['row']['id']))
    # Bound native CUDA/host allocations on the 6 GB production trial GPU.
    # This changes execution grouping only, not any request or sound settings.
    # One launch for a whole section fits the 6 GB GPU (100 requests: 19 s overhead,
    # peak 5,852 MiB, 2026-10-16); RIMSKY_HIGGS_BATCH sets it, 25 stays the default.
    batch_size=int(os.environ.get('RIMSKY_HIGGS_BATCH','25'))
    queue=[items[i:i+batch_size] for i in range(0,len(items),batch_size)];batch_n=-1;deferred=[]
    def taken(i):return i['row']['id'] in i['ctx']['receipt']['takes']
    def total():return sum(len(c['receipt']['takes']) for c in ctxs)
    while queue:
        batch=queue.pop(0);batch_n+=1
        jobs=list(dict.fromkeys(i['job'] for i in batch))
        records={}
        for j in jobs:
            requests={'requests':[sequence_request(i['row']['id'],i['row']['synthesis']) for i in batch if i['job']==j]}
            tag=digest(requests)[:16];sequence=ctxs[j]['output']/f'sequence-{tag}.json'
            if sequence.exists() and read(sequence)!=requests:raise ValueError('Frozen request sequence changed')
            write_json(sequence,requests);records[j]=tag
        owner=ctxs[jobs[0]]['output']
        if not many:stem=f'sequence-{records[jobs[0]]}';sequence=owner/(stem+'.json')
        else:
            shared={'requests':[sequence_request(i['rid'],i['row']['synthesis']) for i in batch]}
            stem=f'sequence-{digest(shared)[:16]}.shared';sequence=owner/(stem+'.json')
            if sequence.exists() and read(sequence)!=shared:raise ValueError('Frozen request sequence changed')
            write_json(sequence,shared)
            for j in jobs:
                # Each folder documents exactly what was rendered for it, and where the shared session lives.
                write_json(ctxs[j]['output']/f'sequence-{records[j]}.session.json',{'session_sequence':str(sequence),
                    'request_ids':{i['row']['id']:i['rid'] for i in batch if i['job']==j},
                    'folders':[str(ctxs[k]['output']) for k in jobs]})
        staging=STAGING/uuid.uuid4().hex
        staging.mkdir(parents=True)
        command=[str(RUNNER),'--task','tts','--family',FAMILY,'--model',str(ENGINE_MODEL),
                 '--backend','cuda','--device','0','--threads','4','--request-sequence',str(sequence),
                 '--out-dir',str(staging),'--out-format','pcm16','--metrics',
                 '--batch-manifest-out',str(owner/(stem+'.manifest.json'))]
        log_path=owner/(stem+'.log')
        print('RENDER BATCH',batch_n,len(batch),'remaining',len(batch)+sum(len(b) for b in queue),flush=True)
        stable_sizes={}
        if execution_guard:execution_guard()
        native_started=time.time()
        with log_path.open('w',encoding='utf-8') as log:
            with owned_native_process(command,cwd=RUNNER.parent,stdout=log,stderr=subprocess.STDOUT) as process:
                budget=ProgressBudget(total())
                while process.poll() is None:
                    for i in batch:
                        row,c=i['row'],i['ctx'];wav=staging/(i['rid']+'.wav')
                        if taken(i) or not wav.is_file(): continue
                        size=wav.stat().st_size
                        if stable_sizes.get(i['rid'])!=size:
                            stable_sizes[i['rid']]=size
                            continue
                        try:
                            result=quality(wav,row['synthesis'])
                        except (ValueError,RuntimeError): continue
                        atomic_write(c['output']/row['audio'],wav.read_bytes())
                        c['receipt']['takes'][row['id']]={**result,'key':row['key'],'audio':row['audio'],
                                                    'synthesis_sha256':digest(row['synthesis']),'word_status':'pending'}
                        write_json(c['receipt_path'],c['receipt'])
                        print('rendered',len(c['receipt']['takes']),'/',len(c['units']),row['id'],flush=True)
                    try:budget.observe(total())
                    except CandidateRenderTimeout:
                        terminate_process_tree(process)
                        for j in jobs:
                            c=ctxs[j]
                            write_json(c['output']/'candidate-render-timeout.json',{'plan_sha256':file_digest(c['path']),
                                'completed':sorted(c['receipt']['takes']),'pending':[i['row']['id'] for i in batch if i['job']==j and not taken(i)],
                                'reason':'No completed native output for 900 seconds; preserved all durable candidates',
                                'word_gate_unchanged':True})
                        raise
                    time.sleep(2)
                print('batch exit',process.returncode,flush=True)
        # Load/overhead versus generation for this native launch (per-request wall times from the runner log).
        native_log=log_path.read_text('utf-8',errors='replace')
        generated=sum(float(x) for x in re.findall(r'\[TIMING\] request\.[^ ]+\.wall_ms ([0-9.]+)',native_log))/1000
        run_event('native',engine=ENGINE,units=len(batch),seconds=round(time.time()-native_started,3),
                  generation_seconds=round(generated,3),overhead_seconds=round(time.time()-native_started-generated,3),
                  exit=process.returncode,folder=owner.name,folders=[ctxs[j]['output'].name for j in jobs])
        missing=[]
        for i in batch:
            row,c=i['row'],i['ctx'];staged=staging/(i['rid']+'.wav')
            try:
                result=quality(staged,row['synthesis'])
                if taken(i):
                    if result['audio_sha256']!=c['receipt']['takes'][row['id']]['audio_sha256']:
                        raise ValueError('Staged audio changed after provisional publication')
                    continue
                atomic_write(c['output']/row['audio'],staged.read_bytes())
                c['receipt']['takes'][row['id']]={**result,'key':row['key'],'audio':row['audio'],
                                           'synthesis_sha256':digest(row['synthesis']),'word_status':'pending'}
            except (ValueError,OSError,RuntimeError) as exc:
                if taken(i):
                    raise ValueError('Staged output no longer matches a completed receipt') from exc
                missing.append((i,str(exc)))
        for j in jobs:write_json(ctxs[j]['receipt_path'],ctxs[j]['receipt'])
        if len(missing)>1:
            # audio.cpp aborts the whole session when one request fails (e.g. a runaway at
            # its token cap), leaving every later request unrendered. Only the request that
            # aborted is retried alone; the rest relaunch together in one session (one model
            # load instead of one per sentence: 73 single retries cost 21 min, 2026-10-16).
            # Requests run in batch order, so the one that aborted is the first without output.
            missing_ids={i['rid'] for i,_ in missing}
            culprit=next(i['rid'] for i in batch if i['rid'] in missing_ids)
            rest=[i for i,_ in missing if i['rid']!=culprit]
            if culprit and rest and len(rest)<len(batch):
                print('NATIVE ABORT at',culprit,'- relaunching',len(rest),'remaining requests together',flush=True)
                run_event('native.abort',culprit=culprit,relaunched=len(rest),folder=owner.name)
                queue.insert(0,rest)
                missing=[(i,e) for i,e in missing if i['rid']==culprit]
        if missing:
            for j in jobs:
                mine=[{'id':i['row']['id'],'error':e} for i,e in missing if i['job']==j]
                if mine:write_json(ctxs[j]['output']/f'sequence-{batch_n:04d}.failures.json',mine)
            # A collateral batch failure is resumed individually with precisely
            # the same controls/reference. No tag stripping or voice fallback.
            failed={}
            for i,error in missing:
                row,c=i['row'],i['ctx'];output=c['output']
                if execution_guard:execution_guard()
                print('individual retry',row['id'],error,flush=True)
                p=row['synthesis']; s=p['sampling']
                retry_audio=STAGING/uuid.uuid4().hex/row['audio']
                retry_audio.parent.mkdir(parents=True)
                command=[str(RUNNER),'--task','tts','--family',FAMILY,'--model',str(ENGINE_MODEL),
                         '--backend','cuda','--device','0','--threads','4','--text',p['prompt'],
                         '--voice-ref',str(ROOT/p['reference']['audio']),'--reference-text',p['reference']['text'],
                         '--seed',str(s['seed']),'--max-tokens',str(p['max_tokens']),
                         '--temperature',str(s['temperature']),'--top-k',str(s['top_k']),'--top-p',str(s['top_p']),
                         '--do-sample',str(s['do_sample']).lower(),'--repetition-penalty',str(s['repetition_penalty']),
                         '--text-chunk-mode',p['chunk_mode'],'--text-chunk-size',str(p['chunk_size']),
                         '--out',str(retry_audio),'--out-format','pcm16','--metrics']
                try:
                    with (output/(row['id']+'.retry.log')).open('w',encoding='utf-8') as log:
                        subprocess.run(command,cwd=RUNNER.parent,stdout=log,stderr=subprocess.STDOUT,timeout=900,check=True)
                    result=quality(retry_audio,p)
                except (subprocess.CalledProcessError,subprocess.TimeoutExpired,ValueError,RuntimeError) as exc:
                    failed.setdefault(i['job'],[]).append({'id':row['id'],'error':str(exc),'log':row['id']+'.retry.log'})
                    write_json(output/'candidate-render-failure.json',{'plan_sha256':file_digest(c['path']),
                        'completed':sorted(c['receipt']['takes']),'failures':failed[i['job']],'word_gate_unchanged':True})
                    print('CANDIDATE FAILED; continuing remaining native requests',row['id'],flush=True)
                    continue
                atomic_write(output/row['audio'],retry_audio.read_bytes())
                c['receipt']['takes'][row['id']]={**result,'key':row['key'],'audio':row['audio'],
                                           'synthesis_sha256':digest(p),'word_status':'pending'}
                write_json(c['receipt_path'],c['receipt'])
            if failed:
                # Report only after the queue drains: the requests relaunched after an abort are
                # still rendered (raising here left 67 of 123 first takes unrendered, full run
                # 2026-10-17, so they were retaken as take-1 with a speculative take-2).
                deferred.append(', '.join(f['id'] for fs in failed.values() for f in fs))
    if deferred:
        from rimsky.native_progress import CandidateRenderFailure
        raise CandidateRenderFailure('Native candidate failed for '+', '.join(deferred))

def render_many(jobs,execution_guard=None):
    """jobs: [(assignment_path, output, chapters)]. Each folder gets what render() would give it; one native session."""
    native([prepare(a,o,c) for a,o,c in jobs],execution_guard)

def render(assignment_path,output,chapters,limit=0,reuse_folder=None,reuse_sound_equivalent=False,execution_guard=None):
    native([prepare(assignment_path,output,chapters,limit,reuse_folder,reuse_sound_equivalent)],execution_guard)
    # Rendering never publishes. The orchestrator must finish its word/voice
    # checks and explicit chapter gate before it calls assemble().

def mastering_voice_key(unit):
    """Aliases share an identity; temporary delivery never changes its gain."""
    return digest({'speaker':unit['speaker'],'voice_family':unit['synthesis']['voice_family'],
                   'reference_sha256':unit['synthesis']['reference']['sha256']})


def validate_voice_continuity(units):
    references={}
    for unit in units:
        family=unit['synthesis']['voice_family']
        presentation=(family if unit['speaker']=='narrator' else
                      'nonverbal' if unit['synthesis'].get('backend')=='nonverbal_asset' else
                      unit.get('presentation_route','dialogue'))
        route=(unit['speaker'],presentation)
        reference=(family,unit['synthesis']['reference']['sha256'])
        palette=unit.get('palette_assignment')
        if palette:
            if reference[1] not in palette['allowed_reference_sha256']:
                raise ValueError('Reference is outside the frozen character performance palette')
            if unit['palette_reference']['character']!=palette['source_character']:
                raise ValueError('Delivery sample belongs to another source character')
            reference=(family,palette['template_id'])
        if route in references and references[route]!=reference:
            raise ValueError('Unexplained character reference switch: '+str(route))
        references[route]=reference
    return references


def assemble(output,plan,receipt,assembly_policy=None,reference_gains=None):
    from rimsky.chapter_gate import require,audio_binding
    # Check before writing anything into the served audio/download folders.
    gate_path=output/'chapter-acceptance.json'
    require(read(gate_path) if gate_path.exists() else None,
            audio_binding(file_digest(output/'render-plan.json'),receipt['takes']))
    import numpy as np
    import soundfile as sf
    validate_voice_continuity(plan['units'])
    DEST.mkdir(parents=True,exist_ok=True)
    chapters=[]
    for chapter in dict.fromkeys(u['chapter_id'] for u in plan['units']):
        rows=[u for u in plan['units'] if u['chapter_id']==chapter]
        if any(u['id'] not in receipt['takes'] for u in rows): continue
        wav=output/(chapter+'.wav')
        timeline=[]; position=0; previous=None;previous_edge=None
        with sf.SoundFile(wav,'w',samplerate=24000,channels=1,subtype='PCM_16') as out:
            for row in rows:
                info=receipt['takes'][row['id']]
                path=output/info['audio']
                if file_digest(path)!=info['audio_sha256']: raise ValueError('Take changed during assembly')
                x,rate=sf.read(path,dtype='float32')
                if rate!=24000:raise ValueError('Assembly sample rate changed')
                edge=None
                if assembly_policy is not None:
                    from rimsky.audio_join import prepare_clip
                    from rimsky.narration_transitions import gap_seconds
                    x,edge=prepare_clip(x,rate,assembly_policy)
                    gap=gap_seconds(previous,row,previous_edge,edge,assembly_policy)
                    gain=(reference_gains or {}).get(mastering_voice_key(row),
                        (reference_gains or {}).get(row['synthesis']['reference']['sha256'],1.))
                    x*=gain
                    if np.max(np.abs(x))>=.999:raise ValueError('Reference mastering gain clips speech')
                    edge['reference_gain']=gain
                    edge['mastering_voice_key']=mastering_voice_key(row)
                else:
                # Added silence only. Speech samples and their dynamics stay intact.
                    gap=(0 if previous is None else 1.15 if previous.get('scene')!=row.get('scene')
                         else .8 if previous['kind']=='heading' else .34 if previous['source_id']!=row['source_id'] else .13)
                out.write(np.zeros(round(gap*rate),dtype='float32')); position+=gap
                timeline.append({'id':row['id'],'start':position,'end':position+len(x)/rate,
                                 'text':row['text'],'speaker':row['speaker'],'voice':row['render_voice'],
                                 'word_status':info.get('word_status','pending'),
                                 **({'join_processing':edge,'added_silence':gap} if edge is not None else {})})
                out.write(x); position+=len(x)/rate; previous=row;previous_edge=edge
        write_json(output/(chapter+'.timeline.json'),timeline)
        if assembly_policy is not None:
            write_json(output/(chapter+'.assembly.json'),{'policy':assembly_policy,'plan_sha256':file_digest(output/'render-plan.json'),
                'reference_gains':reference_gains or {},
                'input_audio':[{'id':u['id'],'sha256':receipt['takes'][u['id']]['audio_sha256']} for u in rows]})
        wav_hash=file_digest(wav)
        version=chapter+'-'+wav_hash[:12]
        mp3=DEST/(version+'.mp3')
        if not mp3.exists():
            temporary=DEST/(version+'.building.mp3')
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(wav),
                            '-codec:a','libmp3lame','-b:a','128k','-metadata','title=Элирм IV — '+chapter,
                            '-metadata','artist=Higgs • candidate fine-tune',str(temporary)],check=True)
            temporary.replace(mp3)
        # The existing server supports byte ranges for WAVs in its audio root.
        # A unique experiment subfolder provides mobile seeking without a
        # labeling-server restart or changes to any board/corpus manifest.
        AUDIO_DEST.mkdir(parents=True,exist_ok=True)
        published=AUDIO_DEST/(version+'.wav')
        if published.exists():
            if file_digest(published)!=wav_hash:raise ValueError('Published immutable chapter changed')
        else:
            staged=published.with_suffix('.building.wav')
            shutil.copyfile(wav,staged);staged.replace(published)
        chapters.append({'id':chapter,'seconds':position,'audio':mp3.name,'sha256':file_digest(mp3),'units':len(rows),
                         'stream':'/character-rb2/full/clips/elirm-iv-mvp/'+published.name,
                         'wav_sha256':wav_hash})
        print('ASSEMBLED',chapter,round(position/60,1),'minutes',flush=True)
    write_json(output/'chapters.json',chapters)
    publish(plan,chapters)

def publish(plan,chapters):
    # Merge independently completed chapters on the same read-only page.
    manifest=DEST/'chapters.json'
    prior=read(manifest) if manifest.exists() else []
    merged={c['id']:c for c in prior}; merged.update({c['id']:c for c in chapters})
    chapters=[merged[k] for k in sorted(merged)]
    write_json(manifest,chapters)
    options=''.join(f'<option value="{html.escape(c["stream"])}" data-hash="{c["wav_sha256"]}" data-download="{c["audio"]}">Глава {int(c["id"][1:])} · {c["seconds"]/60:.0f} мин</option>' for c in chapters)
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Элирм IV — Higgs</title><style>body{font:19px system-ui;max-width:800px;margin:40px auto;padding:18px;background:#f7f8f4;color:#172a25}select,audio{width:100%;margin:18px 0}select,button{font:inherit;padding:14px;border-radius:10px}small{color:#59645f}a{color:#245d46}</style>
<h1>Элирм IV</h1><p>Первые главы · локальная версия Higgs с дообученным голосовым пакетом.</p>
<label for="chapter">Глава</label><select id="chapter">'''+options+'''</select><audio id="player" controls preload="metadata"></audio>
<p id="download"></p><small>Прослушивание кандидата MVP. Голоса и выразительность ещё оцениваются на слух; проверки слов сохраняются отдельно. Это не утверждённый релиз.</small>
<script>const s=document.querySelector('#chapter'),a=document.querySelector('#player'),d=document.querySelector('#download');
function load(){a.src=s.value;d.replaceChildren();const l=document.createElement('a');l.href=s.selectedOptions[0].dataset.download;l.download=l.href;l.textContent='Скачать MP3';d.append(l);let p=null;try{p=JSON.parse(localStorage.getItem('elirm-higgs-position')||'null')}catch{}if(p&&p.file===s.value&&p.hash===s.selectedOptions[0].dataset.hash){a.addEventListener('loadedmetadata',()=>{a.currentTime=Math.min(p.time,a.duration||0)},{once:true})}}
s.onchange=()=>{a.pause();load()};a.ontimeupdate=()=>localStorage.setItem('elirm-higgs-position',JSON.stringify({file:s.value,hash:s.selectedOptions[0].dataset.hash,time:a.currentTime}));a.onended=()=>{if(s.selectedIndex<s.options.length-1){s.selectedIndex++;load();a.play().catch(()=>{})}};load();</script></html>'''
    atomic_write(DEST/'index.html',page.encode('utf-8'))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assignment',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--chapters',nargs='+',default=['c0001','c0002','c0003'])
    p.add_argument('--limit',type=int,default=0)
    a=p.parse_args()
    render(a.assignment.resolve(),a.output.resolve(),a.chapters,a.limit)

if __name__=='__main__': main()
