"""Recover provisional receipts after an interrupted experimental batch.

Only completed, exact-request-bound local trial WAVs can be rebound. Earlier
receipt contents and their invalidated hashes are retained in a repair report.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.util import digest,file_digest,write_json
from render_higgs_book_trial import quality,sequence_request
from higgs_trial_io import read_json

def repair(folder):
    path=folder/'receipts.json';receipt=read_json(path);plan=read_json(folder/'render-plan.json')
    if receipt['plan_sha256']!=file_digest(folder/'render-plan.json'):raise ValueError('Receipt plan binding changed')
    units={r['id']:r for r in plan['units']}
    changes=[]
    for key,take in receipt['takes'].items():
        actual=file_digest(folder/take['audio'])
        if actual==take['audio_sha256']:continue
        evidence=None
        for seq in folder.glob('sequence-*.json'):
            if seq.name.endswith(('.manifest.json','.failures.json')):continue
            requests=read_json(seq).get('requests',[])
            request=next((r for r in requests if r.get('id')==key),None)
            manifest=seq.with_name(seq.stem+'.manifest.json')
            if request!=sequence_request(key,units[key]['synthesis']) or not manifest.exists():continue
            result=quality(folder/take['audio'],units[key]['synthesis'])
            completed=next((r for r in read_json(manifest)['requests'] if r.get('id')==key),None)
            if not completed or completed['samples']!=round(result['duration']*result['sample_rate']):continue
            evidence={'sequence':seq.name,'sequence_sha256':file_digest(seq),
                      'manifest':manifest.name,'manifest_sha256':file_digest(manifest)}
            break
        if evidence is None:raise ValueError('Changed take has no completed matching request: '+key)
        changes.append({'id':key,'previous_receipt':dict(take),'completed_take':result,'evidence':evidence})
    if not changes:return 0
    before=file_digest(path)
    report={'reason':'Provisional poll observed an older uncommitted WAV before a resumed batch overwrote it.',
            'previous_receipts_sha256':before,'plan_sha256':receipt['plan_sha256'],'changes':changes,
            'human_labels_changed':False,'production_approval':False}
    report_path=folder/('receipt-recovery-'+before[:16]+'.json')
    if report_path.exists() and read_json(report_path)!=report:raise ValueError('Recovery report already exists with different contents')
    write_json(report_path,report)
    if file_digest(path)!=before:raise ValueError('Receipt writer is still active; stop it before recovery')
    for change in changes:
        key=change['id'];take=receipt['takes'][key]
        if file_digest(folder/take['audio'])!=change['completed_take']['audio_sha256']:
            raise ValueError('WAV changed during recovery')
        take.update(change['completed_take'],word_status='pending',receipt_recovery=report_path.name)
    write_json(path,receipt)
    return len(changes)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True)
    a=p.parse_args();print('Recovered provisional receipts',repair(a.folder.resolve()))
