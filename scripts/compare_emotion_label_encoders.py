"""Paired base/large comparison, identical labels, features and chapter split."""
import json
from pathlib import Path
import numpy as np
from threadpoolctl import threadpool_limits
from train_book1_label_predictor import ROOT,estimator,stats,save

def main():
    parent=ROOT/'training-work/character-rb1'
    folders={'base':parent/'label-predictor-3a743ea3d029','large':parent/'label-predictor-3a743ea3d029-large-v2'}
    rows=[json.loads(l) for l in (folders['base']/'benchmark-clips.jsonl').read_text(encoding='utf-8').splitlines()]
    report=json.loads((folders['base']/'report.json').read_text(encoding='utf-8'))
    classes=np.asarray(report['tasks']['mood']['supported_labels']);held=set(report['heldout_chapters'])
    labels=np.asarray([r['mood'] for r in rows]);groups=np.asarray([r['chapter_id'] for r in rows])
    train=np.flatnonzero(~np.isin(groups,list(held))&np.isin(labels,classes));test=np.flatnonzero(np.isin(groups,list(held))&np.isin(labels,classes))
    matrices={};hashes=None;baseline_wave=None
    for name,folder in folders.items():
        with np.load(folder/'training-features.npz',allow_pickle=False) as data:
            if hashes is not None:np.testing.assert_array_equal(data['audio_sha256'],hashes);np.testing.assert_allclose(data['wave'],baseline_wave,equal_nan=True)
            hashes=data['audio_sha256'].copy();baseline_wave=data['wave'].copy()
            matrices[name]=np.concatenate([data['wave'],data['emotion']],axis=1)
    result={'scope':'Same frozen Book 1 labels and reserved chapters, identical classifier hyperparameters; not an independent external benchmark',
            'review_sha256':report['review_sha256'],'chapters':sorted(held),'supported_moods':classes.tolist(),'development':len(train),'reserved':len(test),'encoders':{}}
    predictions={}
    for name,x in matrices.items():
        model=estimator('linear')
        with threadpool_limits(limits=4):model.fit(x[train],labels[train]);p=model.predict_proba(x[test])
        result['encoders'][name]=stats(labels[test],p,model.classes_)
        predictions[name]=model.classes_[p.argmax(axis=1)]
    base_correct=predictions['base']==labels[test];large_correct=predictions['large']==labels[test]
    result['paired']={'base_only_correct':int((base_correct&~large_correct).sum()),'large_only_correct':int((~base_correct&large_correct).sum()),'both_correct':int((base_correct&large_correct).sum()),'both_wrong':int((~base_correct&~large_correct).sum())}
    result['mood_prefill_enabled']=False
    save(parent/'emotion2vec-large-comparison/paired-comparison.json',result)
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
