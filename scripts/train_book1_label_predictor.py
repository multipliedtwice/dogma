"""Train local voice/mood suggestion models on the listener's final Book 1 labels.

Feature encoders stay frozen. Whole chapters separate development folds and
evaluation. Predictions never edit reviews or imply corpus approval.
"""
from __future__ import annotations
import json
import argparse
import hashlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import joblib
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, classification_report
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from benchmark_book1_emotion2vec import ROOT, BOARD, ACOUSTIC_KEYS, prepare_snapshot, sha

FEATURE_SCHEMA = 1
SEED = 20260929
CACHE = ROOT / 'training-work/character-rb1/emotion2vec-benchmark-20260929/features-b318240bfe67'
TIMBRE_CACHE = ROOT / 'training-work/character-rb1/label-audit-20260929/audio-features.jsonl'

def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def matrix(features, name):
    return np.concatenate([features[key] for key in name.split('+')],axis=1)

def estimator(kind):
    if kind == 'trees':
        return make_pipeline(SimpleImputer(strategy='median', add_indicator=True),
            ExtraTreesClassifier(n_estimators=240,min_samples_leaf=3,max_features=.6,
                                 class_weight='balanced',random_state=SEED,n_jobs=4))
    return make_pipeline(SimpleImputer(strategy='median',add_indicator=True),StandardScaler(),
        LogisticRegression(C=.1,class_weight='balanced' if kind=='balanced_linear' else None,max_iter=1500))

def stats(actual, prob, classes):
    order=np.argsort(-prob,axis=1); pred=classes[order[:,0]]
    return {'top1':float(accuracy_score(actual,pred)),
            'top3':float(np.mean([y in classes[rank[:3]] for y,rank in zip(actual,order)])),
            'macro_f1':float(f1_score(actual,pred,average='macro',zero_division=0)),
            'balanced_accuracy':float(balanced_accuracy_score(actual,pred))}

def wilson_lower(correct,count):
    z=1.96;p=correct/count
    return (p+z*z/(2*count)-z*np.sqrt(p*(1-p)/count+z*z/(4*count*count)))/(1+z*z/count)

def choose_threshold(actual,prob,classes):
    pred=classes[prob.argmax(axis=1)]; confidence=prob.max(axis=1)
    candidates=[]
    for threshold in np.arange(.5,.991,.01):
        mask=confidence>=threshold; count=int(mask.sum())
        if count<30: continue
        correct=int((pred[mask]==actual[mask]).sum())
        if correct/count>=.90 and wilson_lower(correct,count)>=.80:
            candidates.append({'threshold':round(float(threshold),2),'predictions':count,
                'agreement':correct/count,'wilson_lower_95':wilson_lower(correct,count),
                'coverage':count/len(actual)})
    return max(candidates,key=lambda value:value['coverage']) if candidates else None

def evaluate_gate(actual,prob,classes,threshold,total):
    if threshold is None:return {'enabled':False,'coverage_all_eligible':0,'predictions':0}
    mask=prob.max(axis=1)>=threshold['threshold']; count=int(mask.sum())
    pred=classes[prob.argmax(axis=1)]
    return {'enabled':True,'predictions':count,'coverage_all_eligible':count/total,
            'agreement':float(np.mean(pred[mask]==actual[mask])) if count else None,
            'threshold':threshold['threshold']}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--emotion-cache',type=Path,default=CACHE)
    parser.add_argument('--emotion-revision',default='b318240bfe67db81a8c572ecb37ce9c3759b81c9')
    parser.add_argument('--run-suffix',default='')
    args=parser.parse_args()
    if args.run_suffix and not all(c.isalnum() or c=='-' for c in args.run_suffix):raise ValueError('Invalid run suffix')
    review_hash=sha(BOARD/'review-draft.json')
    out=ROOT / 'training-work/character-rb1'/('label-predictor-'+review_hash[:12]+args.run_suffix)
    if out.exists():raise FileExistsError(f'Existing frozen training run: {out}')
    rows, provenance=prepare_snapshot(out)
    if provenance['review_sha256']!=review_hash:raise ValueError('Review changed before snapshot')
    save(out/'label-signoff.json',{'scope':'Saved Book 1 voice family and mood labels',
         'basis':'User stated all buckets are correct, then requested training a label model from these choices.',
         'review_sha256':review_hash,'per_clip_listening_flags_fabricated':False,
         'boundary_and_quality_holds_retained':True,'created_at':datetime.now(timezone.utc).isoformat()})
    taxonomy=(ROOT/'training/voice_families.json').read_bytes()
    (out/'voice-families.json').write_bytes(taxonomy)
    (out/'review-labels.js').write_bytes((ROOT/'scripts/book1_review_shared.js').read_bytes())
    # Cache rows may repeat as measurements were enriched. The latest hash wins.
    timbre={}
    for line in TIMBRE_CACHE.read_text(encoding='utf-8').splitlines():
        item=json.loads(line)
        if item.get('speaker_embedding'):timbre[item['audio_sha256']]=item['speaker_embedding']
    wave, emotions, speakers=[],[],[]
    for row in rows:
        wave.append([row['acoustics'].get(key,np.nan) for key in ACOUSTIC_KEYS]+
                    row['acoustics'].get('relative_pitch_curve_semitones',[None]*12))
        with np.load(args.emotion_cache/(row['exact_audio_sha256']+'.npz'),allow_pickle=False) as cache:
            emotions.append(cache['features'])
        speakers.append(timbre[row['exact_audio_sha256']])
    features={'wave':np.asarray(wave,dtype=float),'emotion':np.asarray(emotions,dtype=float),
              'timbre':np.asarray(speakers,dtype=float)}
    assert all(value.shape[0]==len(rows) for value in features.values())
    assert np.isfinite(features['emotion']).all() and np.isfinite(features['timbre']).all()
    np.savez_compressed(out/'training-features.npz',**features,ids=np.asarray([r['id'] for r in rows]),
                        audio_sha256=np.asarray([r['exact_audio_sha256'] for r in rows]))
    # These two chapters were designated as held-out in the existing corpus.
    heldout=set(json.loads((BOARD/'summary.json').read_text(encoding='utf-8'))['heldout_chapters'])
    groups=np.asarray([r['chapter_id'] for r in rows])
    development=np.flatnonzero(~np.isin(groups,list(heldout)))
    testing=np.flatnonzero(np.isin(groups,list(heldout)))
    report={**provenance,'created_at':datetime.now(timezone.utc).isoformat(),'clips':len(rows),'emotion_encoder_revision':args.emotion_revision,
         'feature_schema':FEATURE_SCHEMA,'feature_dimensions':{k:v.shape[1] for k,v in features.items()},
         'label_approval':'User declared grouped review complete; original per-clip flags remain unchanged.',
         'heldout_chapters':sorted(heldout),'development_clips':len(development),'evaluation_clips':len(testing),
         'method':'Three chapter-separated development folds select fixed candidate predictors by mean of accuracy, macro F1, and balanced accuracy. Evaluation chapters are excluded from selection and threshold fitting. Final suggestion models are then refitted on all eligible labeled clips.',
         'limits':['Only one book; Book 2 transfer remains untested.',
                   'The reserved chapters were included in an earlier exploratory benchmark; this is not a pristine new external test.',
                   'Confidence scores are uncalibrated; thresholds are exploratory and require independent Book 2 verification.',
                   'Classes need 10 development clips in 3 development chapters. Unsupported labels remain review cases.',
                   'Training fits classifiers over frozen audio encoders; it does not fine-tune Higgs, Qwen TTS, or an audio foundation encoder.',
                   'No source labels, audio, production settings, or corpus gates were changed.'], 'tasks':{}}
    predictions=[]
    for task,label in [('voice_family','voice_family'),('mood','mood')]:
        all_y=np.asarray([r[label] for r in rows]);counts=Counter(all_y[development])
        supported=sorted(key for key,count in counts.items() if count>=10 and len(set(groups[development][all_y[development]==key]))>=3)
        classes=np.asarray(supported);dev=development[np.isin(all_y[development],classes)]
        test=testing[np.isin(all_y[testing],classes)]
        y=all_y[dev]; gt=all_y[test]
        splits=list(StratifiedGroupKFold(n_splits=3,shuffle=True,random_state=SEED).split(dev,y,groups[dev]))
        configs=[('wave','linear'),('wave','balanced_linear'),('wave','trees'),
                 ('wave+emotion','linear'),('wave+emotion','balanced_linear'),('wave+emotion','trees')]
        if task=='voice_family':configs += [('wave+timbre','linear'),('wave+timbre','balanced_linear'),('wave+timbre','trees'),('wave+timbre+emotion','linear')]
        candidates=[];best=None
        for feature_name,kind in configs:
            x=matrix(features,feature_name);oof=np.zeros((len(dev),len(classes)))
            for train,valid in splits:
                assert not set(groups[dev[train]]).intersection(groups[dev[valid]])
                model=estimator(kind)
                with threadpool_limits(limits=4):model.fit(x[dev[train]],y[train]);prob=model.predict_proba(x[dev[valid]])
                for col,key in enumerate(model.classes_):oof[valid,np.flatnonzero(classes==key)[0]]=prob[:,col]
            metrics=stats(y,oof,classes);score=np.mean([metrics[k] for k in ['top1','macro_f1','balanced_accuracy']])
            candidates.append({'features':feature_name,'estimator':kind,'development':metrics,'selection_score':float(score)})
            if best is None or score>best['score']:best={'score':score,'features':feature_name,'estimator':kind,'oof':oof,'metrics':metrics}
            print(f'{task}: {feature_name}/{kind}: top1={metrics["top1"]:.3f} macroF1={metrics["macro_f1"]:.3f}',flush=True)
        threshold=choose_threshold(y,best['oof'],classes)
        x=matrix(features,best['features']);evaluation_model=estimator(best['estimator'])
        with threadpool_limits(limits=4):evaluation_model.fit(x[dev],y);test_prob=evaluation_model.predict_proba(x[test])
        evaluated=stats(gt,test_prob,evaluation_model.classes_)
        gate=evaluate_gate(gt,test_prob,evaluation_model.classes_,threshold,len(testing))
        majority=Counter(y).most_common(1)[0][0];baseline=np.asarray([majority]*len(gt))
        for row_index,prob in zip(test,test_prob):
            ranking=np.argsort(-prob)[:3]
            predictions.append({'id':rows[row_index]['id'],'task':task,'chapter':rows[row_index]['chapter_id'],
                'saved_label':all_y[row_index],'top3':[{'label':str(evaluation_model.classes_[col]),'score':float(prob[col])} for col in ranking],
                'proposed_prefill':bool(threshold and prob.max()>=threshold['threshold']),'human_reviewed':False})
        final_indices=np.flatnonzero(np.isin(all_y,classes));model=estimator(best['estimator'])
        with threadpool_limits(limits=4):model.fit(x[final_indices],all_y[final_indices])
        artifact={'schema':FEATURE_SCHEMA,'task':task,'model':model,'features':best['features'],
             'feature_dimensions':report['feature_dimensions'],'acoustic_keys':ACOUSTIC_KEYS,'contour_bins':12,
             'review_sha256':review_hash,'taxonomy_sha256':hashlib.sha256(taxonomy).hexdigest(),
             'supported_labels':supported,'prefill_threshold':threshold['threshold'] if threshold else None,
             'book2_transfer_validated':False,'automatic_acceptance':False,
             'encoder_revisions':{'emotion':args.emotion_revision,'timbre':'local Qwen3-TTS-12Hz-0.6B-Base speaker encoder'}}
        joblib.dump(artifact,out/(task+'.joblib'),compress=3)
        loaded=joblib.load(out/(task+'.joblib'))
        np.testing.assert_allclose(loaded['model'].predict_proba(x[final_indices[:5]]),model.predict_proba(x[final_indices[:5]]))
        report['tasks'][task]={'supported_labels':supported,'development_class_counts':dict(counts),
            'excluded_labels':{k:int(v) for k,v in Counter(all_y).items() if k not in supported},
            'development_scored':len(dev),'evaluation_scored':len(test),'evaluation_unsupported':len(testing)-len(test),
            'selected':{'features':best['features'],'estimator':best['estimator']},'candidates':candidates,
            'development':best['metrics'],'evaluation':evaluated,
            'evaluation_per_class':classification_report(gt,evaluation_model.predict(x[test]),output_dict=True,zero_division=0),
            'majority_baseline':{'label':str(majority),'top1':float(accuracy_score(gt,baseline)),
                'macro_f1':float(f1_score(gt,baseline,average='macro',zero_division=0))},
            'development_prefill_gate':threshold,'evaluation_prefill_gate':gate,
            'final_fit_clips':len(final_indices),'artifact':task+'.joblib'}
        print(f'{task}: selected {best["features"]}/{best["estimator"]}, evaluation={evaluated}, gate={gate}',flush=True)
    report['live_review_changed_during_run']=sha(BOARD/'review-draft.json')!=review_hash
    save(out/'report.json',report)
    (out/'evaluation-predictions.jsonl').write_text(''.join(json.dumps(p,ensure_ascii=False)+'\n' for p in predictions),encoding='utf-8')
    lines=['# Book 1 custom label predictors','',f'{len(rows)} unique quality-screened clips from the final saved listener labels.','',
           'Two fitted models: voice family and mood. Both provide suggestions; neither approves training data.','',
           '| Task | Selected features | Chapter evaluation top 1 | Top 3 | Macro F1 | Always-most-common label |',
           '|---|---|---:|---:|---:|---:|']
    for task,result in report['tasks'].items():
        m=result['evaluation'];b=result['majority_baseline']
        lines.append(f'| {task} | {result["selected"]["features"]} ({result["selected"]["estimator"]}) | {m["top1"]:.1%} | {m["top3"]:.1%} | {m["macro_f1"]:.3f} | {b["top1"]:.1%} |')
    lines+=['','## Evidence and next gate','',
       f'Reserved evaluation chapters: {", ".join(sorted(heldout))}. Development uses three separate chapter folds. Model selection never uses the evaluation predictions. Models saved for inference are subsequently fitted on all supported Book 1 clips.',
       '','Rare labels are listed explicitly in report.json and must remain review cases. High score does not prove correctness or establish that a Book 2 voice is familiar. The development confidence gate is exploratory; test it on a small independently reviewed Book 2 sample before enabling broad prefill.',
       '',f'Frozen review SHA-256: `{review_hash}`.','',
       '## Limits','']+['- '+limit for limit in report['limits']]
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'Saved fitted models and report: {out}',flush=True)

if __name__=='__main__':main()
