"""Measure pinned emotion2vec+ large for the same Book 1 labels and Book 2 pilot."""
import argparse,json
from prepare_book_label_pilot import ROOT,sha,save
import benchmark_book1_emotion2vec as benchmark

def main():
    p=argparse.ArgumentParser();p.add_argument('--book',choices=['book1','book2'],default='book1');p.add_argument('--limit',type=int,default=0);a=p.parse_args()
    model=ROOT/'training-work/models/emotion2vec-plus-large'
    provenance=json.loads((model/'download-provenance.json').read_text(encoding='utf-8'))
    for f in provenance['files']:
        if f['name']=='model.pt' and sha(model/f['name'])!=f['lfs_sha256']:raise ValueError('Large weights checksum failed')
    if a.book=='book1':
        old=ROOT/'training-work/character-rb1/label-predictor-3a743ea3d029'
        rows=[json.loads(l) for l in (old/'benchmark-clips.jsonl').read_text(encoding='utf-8').splitlines()]
        out=ROOT/'training-work/character-rb1/emotion2vec-large-comparison'
        latest=json.loads((ROOT/'manual-labeling/reality-benders-1/latest.json').read_text(encoding='utf-8'))
        if latest['review_sha256']!=sha(old/'review-snapshot.json'):raise ValueError('Human labels changed; refresh training snapshot')
    else:
        out=ROOT/'training-work/character-rb2/pilot/emotion-large'
        rows=[json.loads(l) for l in (out.parent/'checked-clips.jsonl').read_text(encoding='utf-8').splitlines()]
    out.mkdir(parents=True,exist_ok=True)
    if a.limit:rows=rows[:a.limit]
    for row in rows:
        if sha(ROOT/row['exact_audio'])!=row['exact_audio_sha256']:raise ValueError('Changed source WAV')
    benchmark.MODEL=model;benchmark.MODEL_REVISION=provenance['revision'];benchmark.OUT=out
    features,counts,devices=benchmark.extract(rows,device='cuda',expected_dimension=1024)
    save(out/'extraction.json',{'model':provenance['repo'],'revision':provenance['revision'],'dimension':1024,'clips':len(features),'generic_emotions':counts,'devices':devices,'source_labels_unchanged':True})

if __name__=='__main__':main()
