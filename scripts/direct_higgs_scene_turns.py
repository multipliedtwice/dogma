"""Complete primary/critic scene direction, bound before audiobook synthesis."""
import argparse
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.scene_direction import request,validate,direct
from rimsky.util import file_digest,write_json
from higgs_trial_io import read_json as read
from direct_higgs_book_revision import frozen_call


def direct_chapter(folder,chapter,units,blocks,profiles,run=False):
    payload=request(units,blocks,profiles)
    if not payload['turns']:
        result={'schema':2,'complete':True,'by_unit':{},'production_approved':False}
        write_json(folder/'scene-direction-v2'/chapter/'direction.json',result)
        return result
    out=folder/'scene-direction-v3'/chapter
    counter={'primary':0,'critic':0,'adjudication':0}
    class PlanOnly(Exception):pass
    def infer(system,body,contract):
        phase='adjudication' if 'proposals' in body else 'critic' if counter['primary'] else 'primary'
        counter[phase]+=1
        result=frozen_call(out,phase,system,body,contract,lambda answer:validate(answer,body),run)
        if result is None:raise PlanOnly()
        return result
    try:
        return direct(payload,infer,save=lambda state:write_json(out/'direction.json',state))
    except PlanOnly:return None


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--chapter',default='c0001');p.add_argument('--run',action='store_true');a=p.parse_args()
    folder=a.folder.resolve();book=read(folder/'source/book.json');chapter=next(c for c in book['chapters'] if c['id']==a.chapter)
    source=folder/'audio-v7'/a.chapter/'master/render-plan.json'
    units=read(source)['units'];profiles=read(folder/'revision-01/character-profiles.json')['profiles']
    direct_chapter(folder,a.chapter,units,chapter['blocks'],profiles,a.run)
