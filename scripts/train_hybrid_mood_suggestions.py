"""Combine audio-model scores with the existing bucket comparison heuristics.

Chapter-separated folds select the mix; source text does not enter audio scoring.
Output remains proposals, never human decisions or corpus approval.
"""
import argparse,json,subprocess
from pathlib import Path
from collections import Counter
import numpy as np
from scipy.special import softmax
from sklearn.model_selection import StratifiedGroupKFold
from threadpoolctl import threadpool_limits
from train_book1_label_predictor import ROOT,SEED,estimator,stats,choose_threshold,evaluate_gate,save

def audio_row(row):
    return {k:row[k] for k in ['id','voice_family','mood','exact_audio_sha256','acoustics']}|{'source_id':row.get('book_id','reality-benders-1')+':'+row['source_id']}

def measured(training,queries):
    result=subprocess.run(['node',str(ROOT/'scripts/score_label_bucket_profiles.mjs')],input=json.dumps({'training':training,'queries':queries},ensure_ascii=False),text=True,encoding='utf-8',capture_output=True,check=True)
    return json.loads(result.stdout)

def combine(prob,profiles,classes,weight):
    output=prob.copy()
    for i,profile in enumerate(profiles):
        matches={m['mood']:m for m in profile['matches'] if m['mood'] in classes}
        if len(matches)<2:continue
        positions=[j for j,c in enumerate(classes) if c in matches]
        distance=np.array([matches[classes[j]]['score'] for j in positions])
        # Shared voice scales from the UI; no bucket wins by broad variance.
        logits=weight*np.log(np.maximum(prob[i,positions],1e-9))-(1-weight)*np.minimum(distance,12)**2/2
        output[i]=0;output[i,positions]=softmax(logits)
    return output

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT/'training-work/character-rb2/pilot')
    parser.add_argument('--frozen-report',type=Path)
    parser.add_argument('--candidate-top1',action='store_true',
                        help='Use an unpromoted frozen model top-1 only as a human-review proposal.')
    parser.add_argument('--book-id',default='reality-benders-2')
    parser.add_argument('--suggestions',type=Path,default=Path('suggestions-v2.jsonl'),
                        help='Prediction JSONL, relative to --out unless absolute.')
    args=parser.parse_args()
    if args.frozen_report:
        predict_with_frozen_report(args.out.resolve(),args.frozen_report.resolve(),
                                   args.candidate_top1,args.book_id,args.suggestions)
        return
    model_dir=ROOT/'training-work/character-rb1/label-predictor-3a743ea3d029'
    folder=ROOT/'training-work/character-rb2/pilot/hybrid';folder.mkdir(exist_ok=True)
    rows=[json.loads(l) for l in (model_dir/'benchmark-clips.jsonl').read_text(encoding='utf-8').splitlines()]
    report=json.loads((model_dir/'report.json').read_text(encoding='utf-8'));classes=np.asarray(report['tasks']['mood']['supported_labels'])
    labels=np.asarray([r['mood'] for r in rows]);groups=np.asarray([r['chapter_id'] for r in rows])
    held=report['heldout_chapters'];dev=np.flatnonzero(~np.isin(groups,held)&np.isin(labels,classes));test=np.flatnonzero(np.isin(groups,held)&np.isin(labels,classes))
    with np.load(model_dir/'training-features.npz',allow_pickle=False) as data:x=np.concatenate([data['wave'],data['emotion']],axis=1)
    minimal=[audio_row(r) for r in rows];splits=list(StratifiedGroupKFold(n_splits=3,shuffle=True,random_state=SEED).split(dev,labels[dev],groups[dev]))
    weights=[0,.25,.5,.75,1];oof={w:np.zeros((len(dev),len(classes))) for w in weights};coverage=0
    for fold,(a,b) in enumerate(splits):
        train,valid=dev[a],dev[b];model=estimator('linear')
        with threadpool_limits(limits=4):model.fit(x[train],labels[train]);p=model.predict_proba(x[valid])
        aligned=np.zeros((len(valid),len(classes)))
        for j,c in enumerate(model.classes_):aligned[:,np.flatnonzero(classes==c)[0]]=p[:,j]
        profile=measured([minimal[i] for i in train],[minimal[i] for i in valid])
        coverage+=sum(len(r['matches'])>=2 for r in profile)
        for w in weights:oof[w][b]=combine(aligned,profile,classes,w)
        print(f'Hybrid chapter fold {fold+1}/3',flush=True)
    metrics={w:stats(labels[dev],p,classes) for w,p in oof.items()}
    selected=max(weights,key=lambda w:np.mean([metrics[w][k] for k in ['top1','macro_f1','balanced_accuracy']]))
    threshold=choose_threshold(labels[dev],oof[selected],classes)
    model=estimator('linear')
    with threadpool_limits(limits=4):model.fit(x[dev],labels[dev]);p=model.predict_proba(x[test])
    profiles=measured([minimal[i] for i in dev],[minimal[i] for i in test])
    blended=combine(p,profiles,classes,selected)
    outcome={'review_sha256':report['review_sha256'],'method':'Same warning UI functions: voice-specific shared scales, grouped pitch cues, pitch shape, pace, pauses and volume; linear base-emotion scores; three separated chapter folds select a fixed mix.',
        'mood_comparison_voice':'Known human voice family during Book 1 evaluation; Book 2 uses predicted families, so transfer can be worse.',
        'development':{str(w):m for w,m in metrics.items()},'model_weight':selected,'development_profile_coverage':coverage/len(dev),'development_gate':threshold,
        'reserved_chapters':held,'reserved_scored':len(test),'reserved_model_only':stats(labels[test],p,classes),'reserved_hybrid':stats(labels[test],blended,classes),
        'reserved_gate':evaluate_gate(labels[test],blended,classes,threshold,len(test)),
        'automatic_acceptance':False,'book2_transfer_validated':False}
    save(folder/'report.json',outcome)
    predictions={r['id']:r for r in map(json.loads,(folder.parent/'suggestions-v2.jsonl').read_text(encoding='utf-8').splitlines())}
    features={r['id']:r for r in map(json.loads,(folder.parent/'audio-features.jsonl').read_text(encoding='utf-8').splitlines())}
    targets=[json.loads(l) for l in (folder.parent/'checked-clips.jsonl').read_text(encoding='utf-8').splitlines()]
    queries=[]
    for r in targets:
        pred=predictions[r['id']]['tasks']['voice_family']
        queries.append({'id':r['id'],'source_id':'reality-benders-2:'+r['source_id'],'voice_family':pred['top3'][0]['label'] if pred['meets_exploratory_score_threshold'] else 'voice_pending','acoustics':features[r['id']]})
    profiles=measured(minimal,queries)
    probabilities=np.asarray([[predictions[r['id']]['tasks']['mood']['scores'].get(str(c),0) for c in classes] for r in targets])
    combined=combine(probabilities,profiles,classes,selected)
    proposed=[]
    for r,profile,probability,raw in zip(targets,profiles,combined,probabilities):
        order=np.argsort(-probability)[:3];top=str(classes[order[0]])
        enough=len(profile['matches'])>=2
        heuristic=profile['matches'][0]['mood'] if profile['matches'] else None
        model_top=str(classes[raw.argmax()])
        agreed=bool(enough and top==heuristic==model_top)
        proposed.append({'id':r['id'],'top3':[{'label':str(classes[j]),'score':float(probability[j])} for j in order],
            'model_weight':selected,'agreement':agreed,'model_mood':model_top,'measured_mood':heuristic,
            'measurements':profile,'proposed_mood':top if enough and queries[len(proposed)]['voice_family']!='voice_pending' else 'mood_pending',
            'reason':'Model and measurements agree' if agreed else 'Model and measurements differ; compare examples' if enough else 'Not enough comparable source audio',
            'human_review_required':True,'automatic_acceptance':False})
    save(folder/'suggestions.json',{'schema':1,'clips':{r['id']:r for r in proposed},'report':outcome})
    print(json.dumps(outcome,indent=2),flush=True)


def predict_with_frozen_report(source,report_path,candidate_top1=False,book_id='reality-benders-2',
                               suggestions_path=Path('suggestions-v2.jsonl')):
    """Apply the previously measured Book 1 mix without refitting it."""
    model_dir=ROOT/'training-work/character-rb1/label-predictor-3a743ea3d029'
    folder=source/'hybrid';folder.mkdir(exist_ok=True)
    base=json.loads(report_path.read_text(encoding='utf-8'))
    model_report=json.loads((model_dir/'report.json').read_text(encoding='utf-8'))
    if base['review_sha256']!=model_report['review_sha256']:
        raise ValueError('Frozen mix does not match Book 1 human labels')
    training=[audio_row(r) for r in map(json.loads,(model_dir/'benchmark-clips.jsonl').read_text(encoding='utf-8').splitlines())]
    prediction_file=suggestions_path if suggestions_path.is_absolute() else source/suggestions_path
    predictions={r['id']:r for r in map(json.loads,prediction_file.read_text(encoding='utf-8').splitlines())}
    # The frozen hybrid weights were measured on the Book 1-supported labels,
    # but a later predictor can legitimately add a label (Book 2 added
    # ``stunned``).  Preserve every score emitted by the pinned predictor rather
    # than silently dropping classes absent from the older Book 1 report.
    predicted_moods={str(label) for row in predictions.values()
                     for label in row['tasks']['mood'].get('scores',{})}
    classes=np.asarray(sorted(set(model_report['tasks']['mood']['supported_labels'])|predicted_moods))
    model_artifacts={}
    for task in ('voice_family','mood'):
        hashes={row.get('tasks',{}).get(task,{}).get('model_artifact_sha256')
                for row in predictions.values()}
        if len(hashes)!=1 or None in hashes:
            raise ValueError(f'Prediction rows do not bind one immutable {task} model artifact')
        model_artifacts[task]=hashes.pop()
    features={r['id']:r for r in map(json.loads,(source/'audio-features.jsonl').read_text(encoding='utf-8').splitlines())}
    targets=[json.loads(l) for l in (source/'checked-clips.jsonl').read_text(encoding='utf-8').splitlines()]
    queries=[]
    for r in targets:
        pred=predictions[r['id']]['tasks']['voice_family']
        queries.append({'id':r['id'],'source_id':book_id+':'+r['source_id'],
            'voice_family':pred['top3'][0]['label'] if (pred['meets_exploratory_score_threshold'] or candidate_top1) else 'voice_pending',
            'acoustics':features[r['id']]})
    profiles=measured(training,queries)
    probabilities=np.asarray([[predictions[r['id']]['tasks']['mood']['scores'].get(str(c),0) for c in classes] for r in targets])
    blended=combine(probabilities,profiles,classes,base['model_weight'])
    proposals=[]
    for i,(r,profile,prob,raw) in enumerate(zip(targets,profiles,blended,probabilities)):
        order=np.argsort(-prob)[:3]
        top=str(classes[order[0]])
        enough=len(profile['matches'])>=2
        measured_mood=profile['matches'][0]['mood'] if profile['matches'] else None
        model_mood=str(classes[raw.argmax()])
        agreement=bool(enough and top==measured_mood==model_mood)
        proposals.append({'id':r['id'],'top3':[{'label':str(classes[j]),'score':float(prob[j])} for j in order],
            'model_weight':base['model_weight'],'agreement':agreement,'model_mood':model_mood,
            'measured_mood':measured_mood,'measurements':profile,
            'prediction_model_artifacts':model_artifacts,
            'proposed_mood':top if enough and queries[i]['voice_family']!='voice_pending' else 'mood_pending',
            'reason':'Model and measurements agree' if agreement else 'Model and measurements differ; compare examples' if enough else 'Not enough comparable source audio',
            'human_review_required':True,'automatic_acceptance':False})
    report={**base,'application_scope':f'{book_id} review proposals; transfer accuracy remains unvalidated',
        'target_clips':len(targets),'candidate_top1':candidate_top1,
        'prediction_file':prediction_file.name,'prediction_model_artifacts':model_artifacts,
        'automatic_acceptance':False}
    save(folder/'report.json',report)
    save(folder/'suggestions.json',{'schema':1,'clips':{r['id']:r for r in proposals},'report':report})
    print(f'Prepared {len(proposals)} full-book mood proposals from a frozen mix.',flush=True)

if __name__=='__main__':main()
