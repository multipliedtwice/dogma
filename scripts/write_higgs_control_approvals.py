"""Write combined-control approvals from explicit, hash-bound listening answers.

This never creates a listening verdict or alters corpus labels. The input is a
small qualification experiment's cases and separately saved human decisions.
"""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json,digest,inside
from rimsky.higgs_backend import SENTENCE_TAGS

def write_approvals(cases_path,answers_path,output,max_per_voice=12):
    cases=json.loads(cases_path.read_text('utf-8'));answers=json.loads(answers_path.read_text('utf-8'))
    if answers.get('cases_sha256')!=file_digest(cases_path):raise ValueError('Listening answers refer to different cases')
    entries=cases['cases'];lookup={c['id']:c for c in entries}
    if len(lookup)!=len(entries):raise ValueError('Duplicate qualification case')
    counts={};reviews=[];seen=set()
    for case in entries:
        family=case.get('voice_family',case.get('kind','sound_event'));counts[family]=counts.get(family,0)+1
        if counts[family]>max_per_voice:raise ValueError('Qualification palette exceeds the bounded listening budget')
        if case['model_sha256']!=cases['model_sha256']:raise ValueError('Mixed models in one qualification')
        if not all(t in SENTENCE_TAGS for t in case.get('tags',[])):raise ValueError('Inline sound events need separate placement qualification')
        path=inside(cases_path.parent,case['audio'])
        if file_digest(path)!=case['audio_sha256']:raise ValueError('Qualification audio changed')
    for answer in answers['answers']:
        cid=answer['case_id']
        if cid in seen or cid not in lookup:raise ValueError('Duplicate or unexpected listening answer')
        seen.add(cid);case=lookup[cid]
        if answer.get('case_sha256')!=digest(case) or answer.get('source')!='explicit_listener':
            raise ValueError('Approval requires an explicit answer bound to this exact case')
        verdict=answer['verdict']
        if verdict not in ('approved','rejected','not_sure'):raise ValueError('Invalid listening verdict')
        scope=answer.get('approval_scope','combination')
        if scope not in ('combination','take'):raise ValueError('Invalid approval scope')
        if scope=='combination' and verdict=='approved' and case.get('register')=='warm' and case.get('warmth') not in ('companionable','reassuring','intimate'):
            raise ValueError('Warm approval must specify the relationship intent actually auditioned')
        kind=case.get('control_type','combination')
        if kind not in ('combination','sound_event'):raise ValueError('Unknown qualification case type')
        keys=('voice_family','reference_sha256','tags','register','technique','pace') if kind=='combination' else ('asset_sha256','kind','register','gain')
        reviews.append({'control_type':'take' if scope=='take' else kind,'control_id':cid,
            'approval_scope':scope,
            **{k:case[k] for k in keys},
            'permanent_tags':case.get('permanent_tags',[]),'vocal_traits':case.get('vocal_traits',{}),
            **({'warmth':case['warmth']} if 'warmth' in case else {}),
            'serves':case.get('serves',[]),'verdict':verdict,'audio_sha256':case['audio_sha256'],
            'case_sha256':digest(case),'answer_sha256':digest(answer)})
    result={'schema':2,'model_sha256':cases['model_sha256'],'cases_sha256':file_digest(cases_path),
            'answers_sha256':file_digest(answers_path),'max_cases_per_voice':max_per_voice,'reviews':reviews}
    write_json(output,result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases',type=Path,required=True);p.add_argument('--answers',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=write_approvals(a.cases.resolve(),a.answers.resolve(),a.output.resolve())
    print(json.dumps({'reviews':len(result['reviews']),'output':str(a.output)},ensure_ascii=False))
