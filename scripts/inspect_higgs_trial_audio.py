"""Read-only boundary and casting diagnostics, pinned to actual trial takes."""
import argparse
import json
import sys
from pathlib import Path
import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import file_digest,write_json

def padding(x,rate):
    active=np.flatnonzero(np.abs(x)>.001)
    if not len(active):return (len(x)/rate,len(x)/rate)
    return (int(active[0])/rate,(len(x)-1-int(active[-1]))/rate)

def inspect(folder):
    reports=[]
    for chapter in ('c0001','c0002','c0003'):
        master=folder/'audio-v3'/chapter/'master-01'
        plan=json.loads((master/'render-plan.json').read_text('utf8'))
        receipts=json.loads((master/'receipts.json').read_text('utf8'))['takes']
        rows=[]
        for u in plan['units']:
            r=receipts[u['id']];p=master/r['audio']
            if file_digest(p)!=r['audio_sha256']:raise ValueError('Changed take')
            x,rate=sf.read(p,dtype='float32');head,tail=padding(x,rate)
            rows.append({'id':u['id'],'speaker':u['speaker'],'text':u['text'],'source_id':u['source_id'],
                         'mood':u['mood'],'voice':u['render_voice'],'head':head,'tail':tail,
                         'first_sample':float(x[0]),'last_sample':float(x[-1]),'audio_sha256':r['audio_sha256']})
        summary={k:{'median':float(np.median([r[k] for r in rows])),
                    'p95':float(np.percentile([r[k] for r in rows],95)),
                    'maximum':max(r[k] for r in rows)} for k in ('head','tail')}
        interesting=[r for r in rows if any(s in r['text'] for s in ('Оставь меня','Крест, крест'))]
        g=[r for r in rows if r['speaker']=='gundahar']
        print(chapter,'padding',summary,'Gundakhar',len(g),flush=True)
        for r in interesting:print(json.dumps(r,ensure_ascii=False),flush=True)
        print('worst endpoints',sorted(rows,key=lambda r:max(abs(r['first_sample']),abs(r['last_sample'])),reverse=True)[:2],flush=True)
        reports.append({'chapter':chapter,'plan_sha256':file_digest(master/'render-plan.json'),
                        'padding':summary,'units':rows})
    write_json(folder/'revision-01/boundary-diagnostics.json',reports)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True)
    inspect(p.parse_args().folder)
