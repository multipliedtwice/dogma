"""One bounded seed-retake pass; publish only independently word-matched gains."""
from __future__ import annotations
import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json
from higgs_trial_io import read_json
from render_higgs_book_trial import MODEL_PIN,render,assemble
from normalize_higgs_word_audit import normalize

def candidates(plan,checks,limit):
    selected=[]
    for unit in plan['units']:
        check=checks[unit['id']]
        if check['status']=='asr_exact_match':continue
        expected=check['expected_words'];heard=check['heard_words']
        # Growls/letter sounds have no dependable orthographic ASR target.
        if expected and all(len(w)<=1 for w in expected):continue
        score=0
        for kind,a,b,c,d in difflib.SequenceMatcher(a=expected,b=heard,autojunk=False).get_opcodes():
            if kind!='equal':score+=(b-a)*2+(d-c)
        selected.append((score,unit))
    return [u for _,u in sorted(selected,key=lambda x:(-x[0],x[1]['id']))[:limit]]

def retake(folder,limit):
    plan_path=folder/'render-plan.json';plan=read_json(plan_path);receipt=read_json(folder/'receipts.json')
    audited=normalize(folder);binding=file_digest(plan_path)
    if receipt['plan_sha256']!=binding or audited['plan_sha256']!=binding:
        raise ValueError('First-take bindings changed')
    checks=audited['checks']
    if set(checks)!={u['id'] for u in plan['units']}:raise ValueError('First word audit is incomplete')
    for unit in plan['units']:
        take=receipt['takes'][unit['id']];check=checks[unit['id']]
        if file_digest(folder/take['audio'])!=check['audio_sha256'] or check['synthesis_key']!=take['key']:
            raise ValueError('First take changed before retakes')
    chosen=candidates(plan,checks,limit)
    if not chosen:print('No retakes needed',flush=True);return
    retakes=folder/'seed-retakes-01';retakes.mkdir(exist_ok=True)
    keys=('id','chapter_id','source_id','start','end','text','voice','mood','mode','kind','scene','analysis_parts',
          'sentence_index','speaker','review_warning')
    units=[{**{k:u[k] for k in keys if k in u},'render_seed':(u['synthesis']['sampling']['seed']+1)%2**31,
            'retake_number':1,'retake_of_key':u['key']} for u in chosen]
    snapshot=retakes/'assignment.json'
    assignment={'purpose':'one_seed_retake','first_plan_sha256':binding,'units':units}
    if snapshot.exists() and read_json(snapshot)!=assignment:raise ValueError('Frozen retake selection changed')
    write_json(snapshot,assignment)
    print('WORD RETAKES',len(units),'same words, references and controls',flush=True)
    render(snapshot,retakes,list(dict.fromkeys(u['chapter_id'] for u in units)),limit=len(units))
    retake_plan=read_json(retakes/'render-plan.json');new_units={u['id']:u for u in retake_plan['units']}
    for old in chosen:
        actual=new_units[old['id']]['synthesis'];first=old['synthesis']
        if (actual.get('model_sha256')!=MODEL_PIN.sha256 or
            any(actual[k]!=first[k] for k in ('text','prompt','reference','mood','pace','mappings','runner_sha256','max_tokens','chunk_mode','chunk_size','threads'))):
            raise ValueError('A word retake did not use the pinned model or changed controls, voice or text')
    subprocess.run([str(ROOT/'training-work/.venv-asr/Scripts/python.exe'),'-X','utf8','-u',
                    str(ROOT/'scripts/audit_higgs_book_cuda.py'),'--folders',str(retakes)],check=True)
    new_checks=normalize(retakes);new_receipt=read_json(retakes/'receipts.json')
    if new_checks['verifier']!=audited['verifier']:raise ValueError('Retake word verifier changed')
    master=folder/'master-01';master.mkdir(exist_ok=True)
    selected=[];master_units=[];master_takes={};master_checks={}
    for old in plan['units']:
        key=old['id'];check=new_checks['checks'].get(key)
        if check and check['status']=='asr_exact_match':
            unit=new_units[key];take=new_receipt['takes'][key];audio='../seed-retakes-01/'+take['audio']
            if check['audio_sha256']!=take['audio_sha256'] or check['synthesis_key']!=take['key']:
                raise ValueError('Retake check is not bound to selected take')
            selected.append(key)
        else:
            unit=old;take=receipt['takes'][key];check=checks[key];audio='../'+take['audio']
        master_units.append({**unit,'audio':audio})
        master_takes[key]={**take,'audio':audio,'word_status':check['status'],'word_verifier_sha256':check['verifier_sha256']}
        master_checks[key]={**check,'audio':audio}
    master_plan={**plan,'units':master_units,'first_plan_sha256':binding,
                 'retake_plan_sha256':file_digest(retakes/'render-plan.json'),'selection':selected}
    write_json(master/'render-plan.json',master_plan)
    master_binding=file_digest(master/'render-plan.json')
    master_receipt={'plan_sha256':master_binding,'takes':master_takes}
    write_json(master/'receipts.json',master_receipt)
    write_json(master/'word-checks-1.7B.json',{**audited,'plan_sha256':master_binding,'checks':master_checks})
    exact=sum(c['status']=='asr_exact_match' for c in master_checks.values())
    write_json(master/'chapter-quality.json',{'word_checked':len(master_checks),'asr_exact':exact,
        'asr_flags':len(master_checks)-exact,'seed_retakes':len(units),'word_verified_improvements':len(selected),
        'flags_are_not_proven_synthesis_errors':True,'production_approved':False,'word_verifier':audited['verifier']})
    assemble(master,master_plan,master_receipt)
    print('MASTER',folder.name,'improved',len(selected),'word matched',exact,'/',len(master_checks),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--limit',type=int,default=120);a=p.parse_args();retake(a.folder.resolve(),a.limit)
