"""Index actual reviewed performances before considering synthetic style tags.

Development evidence only. Source character and presentation route remain
separate; model-attributed identity is disclosed, not promoted to human gold.
No labels, WAVs, frozen datasets or production packs are overwritten.
"""
import argparse
from collections import Counter,defaultdict
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.higgs_reference import check_reference
from rimsky.util import file_digest,write_json,SpeechHoldError
from train_higgs_local import verify_dataset


def build(dataset,benchmark,output):
    if output.exists():raise ValueError('Use a fresh reference-bank output')
    dataset_hash=verify_dataset(dataset)
    evidence_path=benchmark/'synthesis-evidence.jsonl'
    manifest=json.loads((benchmark/'evidence-manifest.json').read_text('utf-8'))
    if file_digest(evidence_path)!=manifest['outputs'][evidence_path.name]:raise ValueError('Synthesis evidence changed')
    evidence={r['book']+':'+r['id']:r for line in evidence_path.read_text('utf-8').splitlines()
              if line.strip() for r in [json.loads(line)]}
    groups=defaultdict(list);rejected=[];cache={}
    source=dataset/'train.jsonl'
    records=[json.loads(line) for line in source.read_text('utf-8').splitlines() if line.strip()]
    reserved=json.loads((dataset/'references.json').read_text('utf-8'))
    seen={r['id'] for r in records}
    for family,references in reserved.items():
        for reference in references:
            if reference['evidence_id'] in seen:continue
            seen.add(reference['evidence_id'])
            records.append({'id':reference['evidence_id'],'audio':reference['audio'],'text':reference['text'],
                'metadata':{'audio_sha256':reference['audio_sha256'],'voice_family':family,
                            'mood':reference['mood'],'voice_basis':'human','mood_basis':'human'}})
    for row in records:
        meta=row['metadata'];origin=evidence.get(row['id'])
        if (not origin or origin.get('boundary_held') or meta['voice_basis']!='human' or meta['mood_basis']!='human'
            or origin['audio_sha256']!=meta['audio_sha256'] or origin['text']!=row['text']
            or origin['voice']!=meta['voice_family'] or origin['mood']!=meta['mood']):
            raise ValueError('Reference candidate does not match gated human synthesis evidence: '+row['id'])
        hashed=meta['audio_sha256']
        if hashed not in cache:
            try:cache[hashed]=check_reference(dataset/row['audio'],hashed)
            except SpeechHoldError as exc:cache[hashed]={'held':str(exc)}
        quality=cache[hashed]
        if 'held' in quality:
            rejected.append({'id':row['id'],'audio_sha256':hashed,'reason':quality['held']});continue
        key=(meta['voice_family'],origin['identity'],origin['route'],meta['mood'])
        groups[key].append({'id':row['id'],'audio':row['audio'],'audio_sha256':hashed,'text':row['text'],
            'seconds':quality['seconds'],'quality':quality,'voice_basis':'human','mood_basis':'human',
            'identity_basis':origin.get('speaker_basis','proposal'),'character':origin['identity'],
            'voice_family':meta['voice_family'],'route':origin['route'],'mood':meta['mood'],
            'synthesis_listening_approved':False})
    entries=[]
    for (family,identity,route,mood),items in sorted(groups.items()):
        ranked=sorted(items,key=lambda r:(not 2<=r['seconds']<=10,abs(r['seconds']-5),r['id']))
        entries.append({'voice_family':family,'character':identity,'route':route,'mood':mood,
                        'available':len(items),'candidates':ranked[:3]})
    result={'schema':1,'purpose':'reference_first_development_bank','production_approved':False,
        'inputs':{'dataset_manifest_sha256':dataset_hash,'train_sha256':file_digest(source),
                  'synthesis_evidence_sha256':file_digest(evidence_path),
                  'reserved_references_sha256':file_digest(dataset/'references.json'),
                  'evidence_manifest_sha256':file_digest(benchmark/'evidence-manifest.json'),
                  'builder_sha256':file_digest(Path(__file__))},
        'policy':{'primary_conditioning':'Reviewed recording demonstrating the assigned voice and delivery.',
                  'controls':'Optional only for a demonstrated gap; never infer performance from a tag name.',
                  'character_continuity':'Choose a character-compatible reference set; source characters and routes are not pooled.',
                  'evaluation':'Training-origin reference candidates are not a held-out benchmark or synthesized-audio approval.'},
        'counts':{'safe_recordings':sum(len(g) for g in groups.values()),'reference_edge_holds':len(rejected),
                  'voice_families':len({key[0] for key in groups}),'character_route_mood_groups':len(entries)},
        'coverage':dict(Counter(e['voice_family'] for e in entries)),
        'entries':entries,'reference_only_holds':rejected}
    output.parent.mkdir(parents=True,exist_ok=True);write_json(output,result);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=ROOT/'training-work/higgs-production/datasets/reviewed-20261012-v1')
    parser.add_argument('--benchmark',type=Path,default=ROOT/'training-work/assignment-benchmark/m1-20261012')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(build(args.dataset.resolve(),args.benchmark.resolve(),args.output.resolve())['counts']))
