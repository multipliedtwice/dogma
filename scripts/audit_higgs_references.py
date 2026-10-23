"""Audit frozen reviewed Higgs reference files without altering labels or media."""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.higgs_reference import check_reference
from rimsky.util import file_digest,write_json,SpeechHoldError
from train_higgs_local import verify_dataset


def audit(dataset,output):
    dataset=Path(dataset).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Use a fresh report path; frozen reports are preserved')
    pinned=verify_dataset(dataset)
    references=json.loads((dataset/'references.json').read_text('utf-8'));rows=[]
    for family,items in references.items():
        for index,reference in enumerate(items):
            row={'voice_family':family,'reference_index':index,'reference':reference,
                 'scope':'synthesis_reference_only','human_labels_changed':False}
            try:row.update(status='pass',quality=check_reference(dataset/reference['audio'],reference['audio_sha256']))
            except SpeechHoldError as exc:row.update(status='held',reason=str(exc))
            rows.append(row)
    value={'schema':1,'dataset_manifest_sha256':pinned,'references_sha256':file_digest(dataset/'references.json'),
           'implementation_sha256':file_digest(Path(__file__)),'counts':{state:sum(r['status']==state for r in rows) for state in ('pass','held')},
           'limits':'An active-edge flag is a conservative reference hold, not a new human label or proof of a cut phoneme. Passing acoustics does not approve voice or delivery.',
           'rows':rows}
    output.parent.mkdir(parents=True,exist_ok=True);write_json(output,value);return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=ROOT/'training-work/higgs-production/datasets/reviewed-20261012-v1')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(audit(args.dataset,args.output)['counts']))
