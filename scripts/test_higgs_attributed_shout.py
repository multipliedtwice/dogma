"""Render isolated, hash-bound control candidates without publishing a chapter."""
import argparse
import copy
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json
from higgs_trial_io import read_json
from render_higgs_book_trial import render
from regenerate_higgs_book_trial import wait_for_audio_resources


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--folder',type=Path,required=True)
    a=p.parse_args();folder=a.folder.resolve()
    source=folder/'audio-v4/c0001/master/render-plan.json'
    original=read_json(source)
    cases=[]
    # Check both a short exclamation and a longer sentence; a single pitch
    # measurement is not a reliable test of a character/control combination.
    variants={'shout_low':['pitch_low','shouting'],
              'shout_low_expressive':['pitch_low','expressive_high','shouting'],
              'emphatic_low':['pitch_low','expressive_high']}
    for ident in ('c0001-u0652','c0001-u0654'):
        base=next(u for u in original['units'] if u['id']==ident)
        for name,tags in variants.items():
            for take in range(2):
                unit={k:copy.deepcopy(v) for k,v in base.items() if k not in ('key','synthesis','audio','spoken','render_voice')}
                unit['id']=ident+'-'+name+'-'+str(take)
                profile=unit['delivery_profile']
                profile.pop('blocked_controls',None);profile.pop('control_limitation',None)
                profile['baseline_tags']=[];profile['technique_tags']=tags
                profile['control_test_variant']=name
                unit['render_seed']=base['synthesis']['sampling']['seed']+take
                unit['retake_number']=take
                cases.append(unit)
    out=folder/'attribution-control-test-01';out.mkdir(exist_ok=True)
    snapshot=out/'assignment.json'
    data={'purpose':'evaluation_only','source_plan_sha256':file_digest(source),'units':cases}
    if snapshot.exists() and read_json(snapshot)!=data:
        raise ValueError('Control test input changed')
    write_json(snapshot,data)
    render(snapshot,out,['c0001'],limit=len(cases),execution_guard=wait_for_audio_resources)


if __name__=='__main__':main()
