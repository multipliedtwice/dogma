"""Replace previously blocked delivery candidates; never change human labels.

Only exact-word, safe-edge replacements are selected. Failed candidates leave
an explicit unresolved requirement in the report, not a claimed successful cue.
"""
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
from render_higgs_book_trial import render,assemble
from regenerate_higgs_book_trial import audit,edge,gains,wait_for_audio_resources


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--chapters',nargs='+',default=['c0001','c0002','c0003']);a=p.parse_args()
    folder=a.folder.resolve();masters=[];summary=[]
    for chapter in a.chapters:
        source=folder/'audio-v4'/chapter/'master';old=read(source/'render-plan.json');receipt=read(source/'receipts.json')
        if receipt['plan_sha256']!=file_digest(source/'render-plan.json'):raise ValueError('Original plan changed')
        out=folder/'audio-v5'/chapter/'delivery-retakes';out.mkdir(parents=True,exist_ok=True)
        units=[]
        for u in old['units']:
            if not (u.get('delivery_profile') or {}).get('blocked_controls'):continue
            replacement={k:copy.deepcopy(v) for k,v in u.items() if k not in ('key','synthesis','audio','spoken','render_voice')}
            profile=delivery_profile(u['speaker'],u['delivery_profile']['technique'],
                                     u['delivery_profile']['energy'],u['delivery_profile']['character_profile_sha256'])
            profile.update(disable_emotion_tags=True,planned_mood=u['delivery_profile']['planned_mood'])
            replacement.update(delivery_profile=profile,render_seed=u['synthesis']['sampling']['seed'],
                               retake_number=u['synthesis'].get('take',0))
            units.append(replacement)
        snapshot=out/'assignment.json'
        data={'purpose':'evaluation_only','original_plan_sha256':file_digest(source/'render-plan.json'),
              'control_evidence_sha256':file_digest(folder/'attribution-control-test-01/control-measurements.json'),
              'units':units}
        if snapshot.exists() and read(snapshot)!=data:raise ValueError('Delivery repair changed during resume')
        write_json(snapshot,data)
        if not units:continue
        render(snapshot,out,[chapter],limit=len(units),execution_guard=wait_for_audio_resources)
        checks=audit(out);new=read(out/'render-plan.json');new_receipt=read(out/'receipts.json')
        master=out.parent/'master';master.mkdir(exist_ok=True)
        takes={};rows=[];words={};old_words=read(source/'word-checks.json')['checks']
        proposed={u['id']:u for u in new['units']};selected=[];held=[]
        for original in old['units']:
            key=original['id'];unit=original;take=receipt['takes'][key];parent=source;check=old_words[key]
            if key in proposed:
                bounds=edge(out/new_receipt['takes'][key]['audio'])
                if checks['checks'][key]['status']=='asr_exact_match' and not bounds['unsafe_start'] and not bounds['unsafe_end']:
                    unit=proposed[key];take=new_receipt['takes'][key];parent=out;check=checks['checks'][key];selected.append(key)
                else:held.append({'id':key,'word_status':checks['checks'][key]['status'],'edges':bounds})
            path=(parent/take['audio']).resolve()
            if file_digest(path)!=take['audio_sha256']:raise ValueError('Selected audio changed')
            relative=Path(os.path.relpath(path,master)).as_posix()
            rows.append({**unit,'audio':relative})
            takes[key]={**take,'audio':relative,'word_status':check['status']}
            words[key]={**check,'audio':relative}
        plan={**old,'units':rows,'delivery_repair':{'source_plan_sha256':data['original_plan_sha256'],
              'selected':selected,'unresolved':held,'production_approved':False}}
        write_json(master/'render-plan.json',plan)
        saved={'plan_sha256':file_digest(master/'render-plan.json'),'takes':takes};write_json(master/'receipts.json',saved)
        write_json(master/'word-checks.json',{'plan_sha256':saved['plan_sha256'],'checks':words})
        masters.append((master,plan,saved));summary.append({'chapter':chapter,'selected':selected,'unresolved':held})
        print('REPAIR',chapter,len(selected),'selected',len(held),'unresolved',flush=True)
    # Include unchanged chapters in one reference-gain calculation. A louder
    # take can reduce headroom for its whole reference, never only one clip.
    for source in sorted((folder/'audio-v4').glob('*/master')):
        if source.parent.name not in a.chapters or any(m.parent.name==source.parent.name for m,_,_ in masters):continue
        masters.append((source,read(source/'render-plan.json'),read(source/'receipts.json')))
    reference_gains=gains(masters)
    for master,plan,receipt in masters:assemble(master,plan,receipt,JOIN_POLICY,reference_gains)
    write_json(folder/'audio-v5/delivery-repair-report.json',{'chapters':summary,'production_approved':False})


if __name__=='__main__':main()
