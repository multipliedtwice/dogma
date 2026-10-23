"""Regenerate a continuous trial through shared direction, QA and mastering."""
from __future__ import annotations
import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json,digest
from rimsky.audio_join import JOIN_POLICY,boundary_check
from rimsky.narrative_frames import requested_voice
from rimsky.speech_quality import transcript_check
from higgs_trial_io import read_json as read
from higgs_file_checks import cached_sha
from render_higgs_book_trial import render,assemble,MOODS,mastering_voice_key
from higgs_character_direction import attribution_directions,delivery_profile
from normalize_higgs_word_audit import normalize

def status(folder,**values):
    write_json(folder/'audio-v4/status.json',values);print('STATUS',json.dumps(values),flush=True)

def wait_for(path):
    while not path.exists():time.sleep(10)
    return read(path)

def wait_for_audio_resources():
    """Respect independent music and Higgs training/evaluation jobs.

    Windows CPU music inference can consume enough host memory to prevent
    CUDA narration/verifier loading. Never terminate another user's job.
    """
    if os.name!='nt':return
    command=("$ErrorActionPreference='Stop'; "
             "$music=@(Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
             "Where-Object { $_.CommandLine -match 'music_[a-zA-Z0-9_-]+\\.py' }); "
             "$stress=@(Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
             "Where-Object { $_.CommandLine -match '(run_higgs_stress_pilot|train_higgs_local|evaluate_higgs_stress_preference)\\.py' }); "
             "$native=@(Get-Process audiocpp_cli -ErrorAction SilentlyContinue); "
             "if ($music.Count -or $stress.Count -or $native.Count) { 'busy' } else { 'free' }")
    waiting=False
    while True:
        result=subprocess.run(['powershell.exe','-NoProfile','-Command',command],
            capture_output=True,text=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or result.stdout.strip() not in ('busy','free'):
            raise RuntimeError('Could not verify shared audio-resource availability')
        if result.stdout.strip()=='free':
            if waiting:print('AUDIO RESOURCES AVAILABLE; resuming',flush=True)
            return
        if not waiting:print('WAITING FOR INDEPENDENT MUSIC/TRAINING/AUDIO JOB; checkpoint preserved',flush=True)
        waiting=True;time.sleep(5)

def assignment(folder,chapter):
    book=read(folder/'source/book.json');blocks=next(c['blocks'] for c in book['chapters'] if c['id']==chapter)
    original=read(folder/'audio-v3'/chapter/'master-01/render-plan.json')
    cast=read(folder/'final-cast.json');policy=read(folder/'revision-01/character-profiles.json')
    direction=wait_for(folder/'revision-01'/f'{chapter}-direction.json')
    if direction['original_plan_sha256']!=file_digest(folder/'audio-v3'/chapter/'master-01/render-plan.json'):
        raise ValueError('Original assignment changed')
    frames=wait_for(folder/'narrative-frame-check-02/frames.json')
    changes={c['unit_id']:c for c in direction['corrections']}
    cues=attribution_directions(original['units'],blocks)
    profile_sha=file_digest(folder/'revision-01/character-profiles.json')
    units=[]
    for old in original['units']:
        u={k:copy.deepcopy(v) for k,v in old.items() if k not in ('synthesis','key','audio','spoken','render_voice')}
        change=changes.get(old['id'],{});cue=cues.get(old['id'],{})
        technique=cue.get('technique',change.get('technique','ordinary'))
        profile=delivery_profile(u['speaker'],technique,change.get('energy','conversational'),profile_sha,
                                 policy['profiles'].get(u['speaker']))
        profile['disable_emotion_tags']=True
        profile['planned_mood']=change.get('mood',u['mood'])
        if cue:profile['attribution']=cue
        profile['control_policy']='No unqualified emotion prefixes; persistent reference and explicit prosody/technique only.'
        if u['speaker']=='gundahar':
            u['voice']='masculine_6'
        elif u['mode']=='narrator':
            frame=frames['by_source'][u['source_id']]
            u['narrative_frame']=frame
            u['voice']=requested_voice(frame,[{'id':k} for k in read(ROOT/'training-work/higgs-production/datasets/reviewed-20261012-v1/references.json')],u['voice'])
            profile['reference_index']=0;profile['baseline_tags']=[];profile['technique_tags']=[]
        u.update(mood='baseline',planned_delivery=change or {'mood':old['mood']},
                 delivery_profile=profile,render_seed=old['synthesis']['sampling']['seed'],
                 retake_number=old['synthesis'].get('take',0))
        # Preserve words; give isolated attribution fragments a completion cue.
        u['finish_fragment']=not bool(re.search(r'[.!?…,:;]$',u['text'].strip()))
        units.append(u)
    return {'purpose':'continuous_listening_revision','production_approved':False,
            'source_sha256':file_digest(folder/'source/book.json'),'profile_sha256':profile_sha,
            'scene_direction_sha256':file_digest(folder/'revision-01'/f'{chapter}-direction.json'),
            'narrative_frames_sha256':file_digest(folder/'narrative-frame-check-02/frames.json'),
            'previous_plan_sha256':file_digest(folder/'audio-v3'/chapter/'master-01/render-plan.json'),
            'units':units,'cast':cast}

def audit_configuration(device='cuda:0'):
    model_path=ROOT/'training-work/models/Qwen3-ASR-1.7B'
    return {'model_files':{p.relative_to(ROOT).as_posix():cached_sha(p)
                for p in model_path.rglob('*') if p.is_file() and '.cache' not in p.parts},
            'device':device,'dtype':'bfloat16','attention':'sdpa','language':'Russian',
            'max_new_tokens':512,**({'max_inference_batch_size':1} if device=='cpu' else {}),
            'implementation_sha256':file_digest(ROOT/('scripts/audit_higgs_book_cpu.py' if device=='cpu' else 'scripts/audit_higgs_book_cuda.py')),
            **({} if device=='cpu' else {'batching_sha256':file_digest(ROOT/'scripts/asr_batching.py')})}

def cached_audit(output):
    choices=[p for p in (output/'word-checks-1.7B-cpu.json',output/'word-checks-1.7B.json') if p.exists()]
    if not choices:return None
    receipt=read(output/'receipts.json')
    report_path=next((p for p in choices if set(read(p)['checks'])==set(receipt['takes'])),choices[0])
    report=read(report_path);binding=file_digest(output/'render-plan.json')
    receipt=read(output/'receipts.json');plan=read(output/'render-plan.json')
    if report['plan_sha256']!=binding or receipt['plan_sha256']!=binding:
        raise ValueError('Cached word audit plan binding changed')
    device=report['verifier'].get('device')
    if device not in ('cpu','cuda:0') or report['verifier']!=audit_configuration(device):raise ValueError('Cached word verifier changed')
    units={u['id']:u for u in plan['units']};signature=digest(report['verifier'])
    for key,check in report['checks'].items():
        if key not in receipt['takes']:raise ValueError('Unexpected cached word check')
        take=receipt['takes'][key]
        if (check['audio_sha256']!=take['audio_sha256'] or check['synthesis_key']!=take['key']
            or take['key']!=units[key]['key']
            or check['expected_text_sha256']!=digest(units[key]['spoken'].encode('utf-8'))
            or check['verifier_sha256']!=signature
            or file_digest(output/take['audio'])!=take['audio_sha256']):
            raise ValueError('Cached word check no longer matches take: '+key)
    if set(report['checks'])!=set(receipt['takes']):return None
    print('VERIFIED CACHED WORD CHECKS',output.name,len(report['checks']),flush=True)
    return normalize(output,report_path)

def primary_audit_command(output):
    policy=next((read(p/'primary-asr-policy.json') for p in [output,*output.parents] if (p/'primary-asr-policy.json').exists()),{})
    script='audit_higgs_book_cpu.py' if policy.get('device')=='cpu' else 'audit_higgs_book_cuda.py'
    command=[str(ROOT/'training-work/.venv-asr/Scripts/python.exe'),'-X','utf8','-u',
            str(ROOT/'scripts'/script),'--folders',str(output)]
    # Inline glossary/padded witnesses (same Qwen3-ASR weights), set up by the preview worker.
    import os
    if script=='audit_higgs_book_cuda.py' and os.environ.get('RIMSKY_WITNESS_GLOSSARY'):
        command+=['--witness-glossary',os.environ['RIMSKY_WITNESS_GLOSSARY'],
                  '--glossary-output',os.environ['RIMSKY_WITNESS_GLOSSARY_OUTPUT'],'--padded-output',os.environ['RIMSKY_WITNESS_PADDED_OUTPUT']]
    return command

def audit(output):
    cached=cached_audit(output)
    if cached is not None:return cached
    wait_for_audio_resources()
    subprocess.run([str(ROOT/'training-work/.venv-asr/Scripts/python.exe'),'-X','utf8','-u',
                    str(ROOT/'scripts/audit_higgs_book_cuda.py'),'--folders',str(output)],check=True)
    return normalize(output)

def edge(path):
    audio,rate=sf.read(path,dtype='float32')
    return boundary_check(audio,rate)

def gains(masters):
    groups={}
    entries=[(master,u,receipts) for master,plan,receipts in masters for u in plan['units']]
    for master,u,receipts in entries:
        x,rate=sf.read(master/receipts['takes'][u['id']]['audio'],dtype='float32')
        width=round(.02*rate);padded=np.pad(x,(0,(-len(x))%width));rms=np.sqrt(np.mean(padded.reshape(-1,width)**2,axis=1))
        threshold=max(.0001,float(np.percentile(rms,95))*.12);active=rms[rms>threshold]
        ref=mastering_voice_key(u);item=groups.setdefault(ref,{'levels':[],'peaks':[]})
        if u['synthesis'].get('backend')=='nonverbal_asset':item['fixed_gain']=1.
        item['peaks'].append(float(np.max(np.abs(x))))
        profile=u.get('delivery_profile') or {}
        if (len(active) and profile.get('scene_register','ordinary')=='ordinary'
                and profile.get('technique','ordinary') in ('ordinary',profile.get('baseline_technique','ordinary'))):
            item['levels'].append(float(np.median(active)))
    values={};details={}
    for key,item in groups.items():
        level=float(np.median(item['levels'])) if item['levels'] else None
        # One gain per persistent character/reference voice. Expressive and quiet
        # takes keep their relative level; no compression/per-clip normalization.
        desired=10**(-24/20)/level if level else 1.
        gain=item.get('fixed_gain',min(max(desired,10**(-9/20)),10**(9/20),.92/max(item['peaks'])))
        values[key]=gain;details[key]={'gain':gain,'gain_db':20*np.log10(gain),'baseline_samples':len(item['levels']),
                                    'baseline_active_rms':level,'target_active_dbfs':None if 'fixed_gain' in item else -24,
                                    'gain_basis':'prepared nonverbal asset level retained' if 'fixed_gain' in item else 'persistent voice baseline',
                                    'headroom_limit':.92}
    for master,plan,receipts in masters:
        write_json(master/'reference-mastering.json',{'method':'one gain per persistent character/reference voice across all rendered chapters; dynamic delivery preserved',
            'input_plans':[file_digest(m/'render-plan.json') for m,_,_ in masters],'references':details})
    return values

def regenerate(folder,chapters):
    masters=[]
    for chapter in chapters:
        out=folder/'audio-v4'/chapter;out.mkdir(parents=True,exist_ok=True)
        status(folder,chapter=chapter,stage='direction')
        assigned=assignment(folder,chapter);snapshot=out/'assignment.json'
        if snapshot.exists() and read(snapshot)!=assigned:raise ValueError('Frozen revision changed')
        write_json(snapshot,assigned)
        status(folder,chapter=chapter,stage='rendering')
        render(snapshot,out,[chapter],limit=len(assigned['units']),
               reuse_folder=folder/'audio-v3'/chapter/'master-01',reuse_sound_equivalent=True,
               execution_guard=wait_for_audio_resources)
        status(folder,chapter=chapter,stage='word_check')
        checks=audit(out);plan=read(out/'render-plan.json');receipt=read(out/'receipts.json')
        selected={u['id']:(u,receipt['takes'][u['id']],out,checks['checks'][u['id']]) for u in plan['units']}
        trials=[]
        # Two word-retake rounds; a small bounded edge-only tail can resolve
        # persistent clipped onsets without touching speech during mastering.
        for attempt in range(1,9):
            unsafe=[];mismatched=[]
            for key,(u,take,parent,check) in selected.items():
                e=edge(parent/take['audio'])
                if e['unsafe_start'] or e['unsafe_end']:unsafe.append(key)
                elif check['status']!='asr_exact_match' and attempt<=2:mismatched.append(key)
            pending=unsafe+mismatched
            if not pending:break
            status(folder,chapter=chapter,stage='automatic_retakes',attempt=attempt,
                   unsafe_edges=len(unsafe),word_flags=len(mismatched))
            retry=out/f'quality-retakes-{attempt:02d}';retry.mkdir(exist_ok=True)
            units=[]
            for key in pending:
                base=next(u for u in assigned['units'] if u['id']==key)
                replacement={**base,'render_seed':(base['render_seed']+attempt)%2**31,'retake_number':base.get('retake_number',0)+attempt}
                if attempt>=4:
                    old_unit,old_take,old_parent,_=selected[key]
                    if edge(old_parent/old_take['audio'])['unsafe_start']:replacement['onset_pause']=True
                units.append(replacement)
            write_json(retry/'assignment.json',{'units':units,'parent_plan_sha256':file_digest(out/'render-plan.json')})
            render(retry/'assignment.json',retry,[chapter],limit=len(units),execution_guard=wait_for_audio_resources)
            new_checks=audit(retry);new_receipts=read(retry/'receipts.json')
            for u in read(retry/'render-plan.json')['units']:
                key=u['id'];take=new_receipts['takes'][key];check=new_checks['checks'][key]
                bounds=edge(retry/take['audio'])
                if bounds['unsafe_start'] or bounds['unsafe_end']:continue
                old_u,old_take,old_parent,old_check=selected[key];old_edge=edge(old_parent/old_take['audio'])
                if check['status']=='asr_exact_match' or old_edge['unsafe_start'] or old_edge['unsafe_end']:
                    selected[key]=(u,take,retry,check)
            trials.append({'attempt':attempt,'plan_sha256':file_digest(retry/'render-plan.json')})
        master=out/'master';master.mkdir(exist_ok=True);rows=[];takes={};word_checks={}
        for original in plan['units']:
            key=original['id'];u,take,parent,check=selected[key];bounds=edge(parent/take['audio'])
            if bounds['unsafe_start'] or bounds['unsafe_end']:raise ValueError('Automatic edge retakes exhausted: '+key)
            relative=Path('..')/parent.relative_to(out)/take['audio']
            audio=relative.as_posix();rows.append({**u,'audio':audio})
            takes[key]={**take,'audio':audio,'word_status':check['status']}
            word_checks[key]={**check,'audio':audio}
        selected_plan={**plan,'units':rows,'automatic_trials':trials}
        write_json(master/'render-plan.json',selected_plan)
        selected_receipt={'plan_sha256':file_digest(master/'render-plan.json'),'takes':takes}
        write_json(master/'receipts.json',selected_receipt)
        write_json(master/'word-checks.json',{'plan_sha256':selected_receipt['plan_sha256'],'checks':word_checks})
        exact=sum(c['status']=='asr_exact_match' for c in word_checks.values())
        write_json(master/'chapter-quality.json',{'word_checked':len(rows),'asr_exact':exact,'asr_flags':len(rows)-exact,
            'unsafe_edges':0,'unqualified_emotion_tags':None,'control_qualification':'not_measured',
            'source':assigned['source_sha256'],
            'production_approved':False,'flags_are_not_proven_synthesis_errors':True})
        status(folder,chapter=chapter,stage='mastering',asr_exact=exact,units=len(rows))
        masters.append((master,selected_plan,selected_receipt))
    status(folder,stage='mastering',chapters=chapters)
    reference_gains=gains(masters)
    for master,plan,receipt in masters:
        assemble(master,plan,receipt,JOIN_POLICY,reference_gains)
        status(folder,chapter=master.parent.name,stage='published')
    status(folder,stage='complete',chapters=chapters)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--chapters',nargs='+',default=['c0001','c0002','c0003']);a=p.parse_args()
    regenerate(a.folder.resolve(),a.chapters)
