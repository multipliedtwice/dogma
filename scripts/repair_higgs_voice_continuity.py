"""Apply a frozen character persona to every utterance, then remaster voices."""
import argparse
import copy
import os
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json
from rimsky.audio_join import JOIN_POLICY
from higgs_trial_io import read_json as read
from higgs_character_direction import delivery_profile
from render_higgs_book_trial import render,assemble,validate_voice_continuity
from regenerate_higgs_book_trial import audit,edge,gains,wait_for_audio_resources


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--character',default='terek');a=p.parse_args();folder=a.folder.resolve()
    policy=folder/'revision-01/character-profiles.json';persona=read(policy)['profiles'][a.character]
    masters=[];reports=[]
    for chapter in ('c0001','c0002','c0003'):
        source=folder/'audio-v5'/chapter/'master';old=read(source/'render-plan.json');receipt=read(source/'receipts.json')
        if receipt['plan_sha256']!=file_digest(source/'render-plan.json'):raise ValueError('Source plan changed')
        out=folder/'audio-v6'/chapter;master=out/'master';master.mkdir(parents=True,exist_ok=True)
        old_checks=read(source/'word-checks.json')['checks'];selected={}
        units=[]
        for u in old['units']:
            if u['speaker']!=a.character:continue
            unit={k:copy.deepcopy(v) for k,v in u.items() if k not in ('key','synthesis','audio','spoken','render_voice')}
            profile=delivery_profile(a.character,u['delivery_profile']['technique'],u['delivery_profile']['energy'],file_digest(policy),persona)
            profile.update(disable_emotion_tags=True,planned_mood=u['delivery_profile']['planned_mood'])
            unit.update(delivery_profile=profile,render_seed=u['synthesis']['sampling']['seed'],retake_number=u['synthesis'].get('take',0))
            units.append(unit)
        if units:
            work=out/'persona-takes';work.mkdir(exist_ok=True);snapshot=work/'assignment.json'
            data={'purpose':'evaluation_only','original_plan_sha256':file_digest(source/'render-plan.json'),
                  'persona_sha256':file_digest(policy),'units':units}
            if snapshot.exists() and read(snapshot)!=data:raise ValueError('Persona repair changed during resume')
            write_json(snapshot,data)
            render(snapshot,work,[chapter],limit=len(units),reuse_folder=source,reuse_sound_equivalent=True,
                   execution_guard=wait_for_audio_resources)
            checks=audit(work);plan=read(work/'render-plan.json');takes=read(work/'receipts.json')
            selected={u['id']:(u,takes['takes'][u['id']],work,checks['checks'][u['id']]) for u in plan['units']}
            # Retake unsafe boundaries, retaining identical persona and words.
            for attempt in range(1,5):
                unsafe=[key for key,(_,take,parent,_) in selected.items()
                        if any(edge(parent/take['audio'])[name] for name in ('unsafe_start','unsafe_end'))]
                if not unsafe:break
                retry=out/('edge-retakes-'+str(attempt));retry.mkdir(exist_ok=True);retry_units=[]
                for key in unsafe:
                    base=next(u for u in units if u['id']==key)
                    retry_units.append({**base,'render_seed':base['render_seed']+attempt,
                                        'retake_number':base['retake_number']+attempt,
                                        'onset_pause':attempt>=2})
                write_json(retry/'assignment.json',{'units':retry_units})
                render(retry/'assignment.json',retry,[chapter],limit=len(retry_units),execution_guard=wait_for_audio_resources)
                fresh=audit(retry);new_takes=read(retry/'receipts.json')
                for u in read(retry/'render-plan.json')['units']:
                    take=new_takes['takes'][u['id']];bounds=edge(retry/take['audio'])
                    if not bounds['unsafe_start'] and not bounds['unsafe_end']:
                        selected[u['id']]=(u,take,retry,fresh['checks'][u['id']])
        rows=[];takes={};checks={}
        for original in old['units']:
            key=original['id'];u,take,parent,check=selected.get(key,(original,receipt['takes'][key],source,old_checks[key]))
            path=(parent/take['audio']).resolve();bounds=edge(path)
            if bounds['unsafe_start'] or bounds['unsafe_end']:raise ValueError('Unsafe persona take: '+key)
            if file_digest(path)!=take['audio_sha256']:raise ValueError('Persona audio bytes changed')
            audio=Path(os.path.relpath(path,master)).as_posix()
            rows.append({**u,'audio':audio});takes[key]={**take,'audio':audio,'word_status':check['status']}
            checks[key]={**check,'audio':audio}
        validate_voice_continuity(rows)
        plan={**old,'units':rows,'persona_repair':{'character':a.character,'persona_sha256':file_digest(policy),
            'source_plan_sha256':file_digest(source/'render-plan.json'),'production_approved':False}}
        write_json(master/'render-plan.json',plan);saved={'plan_sha256':file_digest(master/'render-plan.json'),'takes':takes}
        write_json(master/'receipts.json',saved);write_json(master/'word-checks.json',{'plan_sha256':saved['plan_sha256'],'checks':checks})
        masters.append((master,plan,saved));reports.append({'chapter':chapter,'character_units':len(selected),
            'asr_flags':sum(c['status']!='asr_exact_match' for _,_,_,c in selected.values()),'unsafe_edges':0})
    reference_gains=gains(masters)
    for master,plan,receipt in masters:assemble(master,plan,receipt,JOIN_POLICY,reference_gains)
    write_json(folder/'audio-v6/persona-repair-report.json',{'chapters':reports,'production_approved':False})


if __name__=='__main__':main()
