"""Bounded, versioned auditions for user-reported character/delivery defects."""
import argparse
import copy
import html
import json
import shutil
import subprocess
import sys
from pathlib import Path
import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json,digest,atomic_write
from rimsky.audio_join import prepare_clip,boundary_check,gap_seconds,JOIN_POLICY
from higgs_trial_io import read_json as read
from render_higgs_book_trial import render,DEST,AUDIO_DEST
from higgs_character_direction import attribution_directions,delivery_profile
from prepare_higgs_book_trial import chunks
from normalize_higgs_word_audit import normalize

def build(folder):
    existing=folder/'revision-01/audition/assignment.json'
    if existing.exists():
        assigned=read(existing)
        if assigned['source_sha256']!=file_digest(folder/'source/book.json'):raise ValueError('Audition source changed')
        return existing.parent,assigned
    units=[];groups=[];book=read(folder/'source/book.json')
    old=read(folder/'audio-v3/c0001/master-01/render-plan.json')['units']
    voiceprofile=folder/'revision-01/character-profiles.json'
    profile_hash=file_digest(voiceprofile) if voiceprofile.exists() else None
    paragraph=next(u['source_id'] for u in old if 'Оставь меня в покое' in u['text'])
    group=[u for u in old if u['source_id']==paragraph]
    blocks=next(c for c in book['chapters'] if c['id']=='c0001')['blocks']
    cues=attribution_directions(group,blocks)
    for u in group:
        x={k:v for k,v in u.items() if k not in ('synthesis','key','audio','spoken','render_voice')}
        x['id']='hoarse-'+u['id'];x['finish_fragment']=True
        if u['mode']=='speech':x['delivery_profile']=delivery_profile(u['speaker'],cues.get(u['id'],{}).get('technique','ordinary'),profile_sha256=profile_hash)
        units.append(x)
    groups.append({'id':'terek','title':'Терек: хриплый ответ и авторская вставка','unit_ids':[u['id'] for u in units]})
    # Compare the same sentences using a stable low reviewed register.
    g=next(u for u in old if u['speaker']=='gundahar' and u['mode']=='speech' and len(u['text'])>35)
    picked=[g]+[u for u in old if u['source_id']==g['source_id'] and u['mode']=='speech' and u['id']!=g['id']][:2]
    selected=[]
    for u in picked:
        x={k:v for k,v in u.items() if k not in ('synthesis','key','audio','spoken','render_voice')}
        x.update(id='undead-'+u['id'],voice='masculine_6',mood='baseline',finish_fragment=True,
                 delivery_profile=delivery_profile('gundahar',profile_sha256=profile_hash))
        units.append(x);selected.append(x['id'])
    groups.append({'id':'gundahar','title':'Гундахар: низкий устойчивый регистр','unit_ids':selected})
    c=next(c for c in book['chapters'] if c['id']=='c0007')
    b=next(b for b in c['blocks'] if 'Крест, крест' in b.get('text',''))
    parts=chunks(b['text']);selected=[]
    for i,part in enumerate(parts):
        narration='бормотал Герман' in part['text']
        x={**part,'id':f'mutter-c0007-{i}','source_id':b['id'],'chapter_id':'c0007','scene':0,'kind':'paragraph',
           'mode':'narrator' if narration else 'speech','speaker':'narrator' if narration else 'german',
           'voice':'narrator_baseline' if narration else 'masculine_4','mood':'baseline','render_seed':20261013+700+i,
           'sentence_index':i,'finish_fragment':True}
        if not narration:x['delivery_profile']=delivery_profile('german','muttering',profile_sha256=profile_hash)
        units.append(x);selected.append(x['id'])
    groups.append({'id':'german','title':'Герман: «Крест, крест, крест…» — бормотание (глава 7)','unit_ids':selected})
    assignment={'purpose':'user_feedback_direction_audition','production_approved':False,'units':units,
                'source_sha256':file_digest(folder/'source/book.json'),'groups':groups}
    out=folder/'revision-01/audition';out.mkdir(parents=True,exist_ok=True);p=out/'assignment.json'
    if p.exists() and read(p)!=assignment:raise ValueError('Frozen audition changed; use a new version')
    if not p.exists():write_json(p,assignment)
    return out,assignment

def run(folder):
    out,assigned=build(folder);chapters=['c0001','c0007']
    render(out/'assignment.json',out,chapters,limit=len(assigned['units']))
    # Retake unsafe edges; do not taper active speech. At most two new seeds.
    for attempt in range(2):
        plan=read(out/'render-plan.json');receipts=read(out/'receipts.json');unsafe=[]
        for u in plan['units']:
            x,rate=sf.read(out/receipts['takes'][u['id']]['audio'],dtype='float32')
            edge=boundary_check(x,rate)
            if edge['unsafe_start'] or edge['unsafe_end']:unsafe.append(u['id'])
        if not unsafe:break
        retry=out/f'edge-retake-{attempt+1}';retry.mkdir(exist_ok=True)
        units=[{**u,'render_seed':u.get('render_seed',20261013)+attempt+1} for u in assigned['units'] if u['id'] in unsafe]
        write_json(retry/'assignment.json',{'units':units,'parent_plan_sha256':file_digest(out/'render-plan.json')})
        render(retry/'assignment.json',retry,chapters,limit=len(units))
        retake_plan=read(retry/'render-plan.json');new_receipts=read(retry/'receipts.json')
        original_units={u['id']:u for u in plan['units']}
        for u in retake_plan['units']:
            info=new_receipts['takes'][u['id']];x,rate=sf.read(retry/info['audio'],dtype='float32')
            edge=boundary_check(x,rate)
            if edge['unsafe_start'] or edge['unsafe_end']:continue
            # Original plan is immutable. Store a new selected master below.
            old=original_units[u['id']];old.clear();old.update(u)
            receipts['takes'][u['id']]={**info,'audio':f'edge-retake-{attempt+1}/'+info['audio']}
        selected=out/f'selected-{attempt+1}';selected.mkdir(exist_ok=True)
        for info in receipts['takes'].values():info['audio']='../'+info['audio']
        write_json(selected/'render-plan.json',plan)
        receipts['plan_sha256']=file_digest(selected/'render-plan.json');write_json(selected/'receipts.json',receipts)
        # Subsequent auditing/publication reads this selected master; paths are
        # relative to the selected folder. Never rewrite original plan/receipt.
        out=selected
    subprocess.run([str(ROOT/'training-work/.venv-asr/Scripts/python.exe'),'-X','utf8','-u',
                    str(ROOT/'scripts/audit_higgs_book_cuda.py'),'--folders',str(out)],check=True)
    checks=normalize(out)['checks'];plan=read(out/'render-plan.json');receipts=read(out/'receipts.json')
    publish(out,plan,receipts,checks,assigned['groups'])

def publish(out,plan,receipts,checks,groups):
    public=DEST/'revision-01';public.mkdir(exist_ok=True);audio=AUDIO_DEST/'revision-01';audio.mkdir(exist_ok=True)
    by_id={u['id']:u for u in plan['units']};parts=[];report=[]
    for group in groups:
        arrays=[];previous=None;previous_edge=None;timeline=[];seconds=0
        for key in group['unit_ids']:
            u=by_id[key];info=receipts['takes'][key];path=out/info['audio']
            if file_digest(path)!=info['audio_sha256']:raise ValueError('Audition take changed')
            x,rate=sf.read(path,dtype='float32');x,edge=prepare_clip(x,rate)
            gap=gap_seconds(previous,u,previous_edge,edge);arrays.extend([np.zeros(round(gap*rate)),x])
            timeline.append({'id':key,'start':seconds+gap,'seconds':len(x)/rate,'audio_sha256':info['audio_sha256'],
                             'join':edge,'word_check':checks[key]})
            seconds+=gap+len(x)/rate;previous=u;previous_edge=edge
        local=out/(group['id']+'.wav');sf.write(local,np.concatenate(arrays),24000,subtype='PCM_16')
        sha=file_digest(local);name=group['id']+'-'+sha[:12]+'.wav';destination=audio/name
        if destination.exists() and file_digest(destination)!=sha:raise ValueError('Immutable audition changed')
        if not destination.exists():shutil.copyfile(local,destination)
        url='/character-rb2/full/clips/elirm-iv-mvp/revision-01/'+name
        text=' '.join(by_id[k]['text'] for k in group['unit_ids'])
        flags=sum(checks[k]['status']!='asr_exact_match' for k in group['unit_ids'])
        parts.append(f'<article><h2>{html.escape(group["title"])}</h2><p>{html.escape(text)}</p><audio controls preload="metadata" src="{url}"></audio><small>Проверка слов: {len(group["unit_ids"])-flags}/{len(group["unit_ids"])} совпадают. Флаги: {flags}. Тембр и приёмы — кандидат для прослушивания.</small></article>')
        report.append({'group':group['id'],'audio_sha256':sha,'stream':url,'timeline':timeline})
    page='<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Элирм IV — исправления</title><style>body{font:19px system-ui;max-width:800px;margin:30px auto;padding:20px;background:#f7f8f4}article{padding:20px;border:1px solid #bac7c0;border-radius:14px;margin:20px 0}audio{width:100%}small{color:#566}</style><h1>Элирм IV: голос и склейки</h1><p>Три коротких фрагмента после замечаний. Новые ссылки; исходные главы сохранены. Низкий хриплый голос и бормотание ещё оцениваются на слух.</p>'+''.join(parts)
    atomic_write(public/'index.html',page.encode('utf8'));write_json(out/'audition-report.json',{'policy':JOIN_POLICY,
            'plan_sha256':file_digest(out/'render-plan.json'),'groups':report,'production_approved':False})
    print('PUBLISHED',public,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True);p.add_argument('--run',action='store_true');a=p.parse_args()
    if a.run:run(a.folder.resolve())
    else:print(build(a.folder.resolve())[0])
