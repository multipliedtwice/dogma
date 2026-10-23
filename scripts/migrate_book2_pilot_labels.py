"""Carry only exact text-and-audio human decisions into full-edition review."""
import hashlib
import json
from pathlib import Path

from book1_review_persistence import save_review
from prepare_book_label_pilot import ROOT, sha, save


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def main():
    old = ROOT/'manual-labeling/reality-benders-2'
    new = ROOT/'training-work/character-rb2-full/fine-tune-all'
    latest=json.loads((old/'latest.json').read_text(encoding='utf-8'))
    if sha(old/'review.json')!=latest['review_sha256']:
        raise ValueError('Book 2 pilot human archive is not current')
    reviewed=json.loads((old/'review.json').read_text(encoding='utf-8'))['decisions']
    originals={r['id']:r for r in rows(old/'metadata/candidates.jsonl')}
    audio_index=json.loads((old/'audio-index.json').read_text(encoding='utf-8'))
    targets=rows(new/'candidates.jsonl')
    by_evidence={}
    for r in targets:
        key=(r['recording_sha256'],r['audio_sha256'],r['source_text'])
        by_evidence.setdefault(key,[]).append(r)
    current=json.loads((new/'review-draft.json').read_text(encoding='utf-8'))
    transferred=[];held=[]
    for old_id,choice in reviewed.items():
        source=originals[old_id]
        archive=old/audio_index[old_id]['archive_path']
        if sha(archive)!=source['audio_sha256']:
            raise ValueError('Protected pilot audio changed: '+old_id)
        key=(source['recording_sha256'],source['audio_sha256'],source['source_text'])
        matches=by_evidence.get(key,[])
        if len(matches)!=1:
            held.append({'old_id':old_id,'reason':'No unique exact text and audio match'})
            continue
        target=matches[0]
        if sha(ROOT/target['exact_audio'])!=source['audio_sha256']:
            raise ValueError('Full-edition audio changed: '+target['id'])
        if target['id'] in current['decisions']:
            held.append({'old_id':old_id,'new_id':target['id'],'reason':'Existing full-edition answer kept'})
            continue
        current['decisions'][target['id']]={**choice,'migrated_from_pilot':old_id,
            'pilot_review_sha256':latest['review_sha256']}
        transferred.append({'old_id':old_id,'new_id':target['id'],
            'source_text_sha256':hashlib.sha256(source['source_text'].encode('utf-8')).hexdigest(),
            'audio_sha256':source['audio_sha256']})
    if transferred:
        save_review(new,{'schema':1,'candidates_sha256':current['candidates_sha256'],
                         'decisions':current['decisions'],'reviewer':'local-listener'})
    report={'schema':1,'pilot_review_sha256':latest['review_sha256'],
            'full_candidates_sha256':current['candidates_sha256'],
            'transferred':transferred,'held':held,
            'rule':'Only identical recording, WAV hash, and exact source text transfer human choices.'}
    save(new/'pilot-label-migration.json',report)
    archived=ROOT/'manual-labeling/reality-benders-2-full/metadata/pilot-label-migration.json'
    save(archived,report)
    manifest_path=archived.parents[1]/'archive-manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['metadata']['pilot-label-migration.json']=sha(archived)
    # Bind the support archive to the exact durable review produced above.
    full_latest=json.loads((archived.parents[1]/'latest.json').read_text(encoding='utf-8'))
    manifest['review_sha256']=full_latest['review_sha256']
    manifest.pop('review_snapshot',None)
    manifest['review_decisions']=full_latest['decisions']
    save(manifest_path,manifest)
    print(f'Migrated {len(transferred)} verified pilot answers; kept {len(held)} in the pilot only.')


if __name__=='__main__':main()
