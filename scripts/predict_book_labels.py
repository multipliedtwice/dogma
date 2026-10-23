"""Use trusted, locally trained label predictors to write review suggestions.

Input NPZ has ids, audio_sha256 and wave/timbre/emotion matrices. A separate
JSONL manifest binds IDs to exact WAV hashes. No review file is modified.
"""
import argparse
import hashlib
import json
from pathlib import Path
import joblib
import numpy as np


FEATURE_ARRAYS = {
    'wave': 'wave',
    'timbre': 'timbre',
    'emotion': 'emotion',
    'emotion2vec-frame-mean': 'emotion',
}


def file_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def predict(directory, feature_file, manifest):
    rows=[json.loads(line) for line in manifest.read_text(encoding='utf-8').splitlines() if line.strip()]
    with np.load(feature_file,allow_pickle=False) as loaded:
        arrays={key:loaded[key] for key in loaded.files}
    ids=arrays['ids'].tolist();hashes=arrays['audio_sha256'].tolist()
    if len(ids)!=len(set(ids)) or len(hashes)!=len(ids):raise ValueError('Invalid feature IDs or hashes')
    positions={id:i for i,id in enumerate(ids)}
    if len(rows)!=len({row['id'] for row in rows}):raise ValueError('Duplicate manifest IDs')
    indices=[]
    for row in rows:
        i=positions[row['id']];expected=row.get('exact_audio_sha256') or row.get('audio_sha256')
        if not expected or expected!=hashes[i]:raise ValueError(f'Feature WAV hash does not match {row["id"]}')
        indices.append(i)
    predictions=[{'id':row['id'],'audio_sha256':hashes[i],'human_review_required':True,
                  'automatic_acceptance':False,'tasks':{}} for row,i in zip(rows,indices)]
    for task in ['voice_family','mood']:
        # Load only the fitted model produced by this local training workflow.
        artifact=joblib.load(directory/(task+'.joblib'))
        if artifact.get('schema')!=1 or artifact.get('task')!=task:raise ValueError('Unexpected predictor schema')
        blocks=[]
        feature_names=artifact['features'].split('+')
        for name in feature_names:
            array_name=FEATURE_ARRAYS.get(name)
            if array_name is None:
                raise ValueError(f'Unsupported predictor feature {name}')
            if name=='emotion2vec-frame-mean' and str(arrays.get('emotion_feature_kind',''))!='frame-mean':
                raise ValueError('Series mood predictor requires frame-mean emotion2vec features')
            if name in ('emotion','emotion2vec-frame-mean') and artifact.get('encoder_revisions') and str(arrays.get('emotion_revision',''))!=artifact['encoder_revisions']['emotion']:
                raise ValueError('Emotion features were produced by a different or unspecified encoder revision')
            block=arrays[array_name]
            dimensions=artifact.get('feature_dimensions',{}).get(name)
            if dimensions is not None and block.shape!=(len(ids),dimensions):raise ValueError(f'Invalid {name} feature dimensions')
            if block.ndim!=2 or block.shape[0]!=len(ids):raise ValueError(f'Invalid {name} feature dimensions')
            if np.isinf(block).any() or (name!='wave' and not np.isfinite(block).all()):raise ValueError(f'Invalid {name} values')
            blocks.append(block[indices])
        matrix=np.concatenate(blocks,axis=1)
        expected=getattr(artifact['model'],'n_features_in_',None)
        if expected is not None and matrix.shape[1]!=expected:
            raise ValueError(f'Predictor expects {expected} features, got {matrix.shape[1]}')
        scores=artifact['model'].predict_proba(matrix)
        for output,score in zip(predictions,scores):
            rank=np.argsort(-score)[:3];threshold=artifact.get('prefill_threshold')
            output['tasks'][task]={'top3':[{'label':str(artifact['model'].classes_[i]),'score':float(score[i])} for i in rank],
                'scores':{str(label):float(value) for label,value in zip(artifact['model'].classes_,score)},
                'meets_exploratory_score_threshold':bool(threshold is not None and score.max()>=threshold),
                'candidate_only':not artifact.get('production_promoted',False),
                'training_books':artifact.get('training_books',[]),
                'next_pristine_gate':artifact.get('next_pristine_gate'),
                'model_artifact_sha256':file_sha256(directory/(task+'.joblib')),
                'book2_transfer_validated':artifact.get('book2_transfer_validated',False),
                'encoder_revisions':artifact.get('encoder_revisions',{}),
                'review_sha256':artifact.get('review_sha256')}
    return predictions

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-directory',type=Path,required=True)
    parser.add_argument('--features',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    predictions=predict(args.model_directory,args.features,args.manifest)
    with args.output.open('x',encoding='utf-8') as stream:
        for row in predictions:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(f'Wrote {len(predictions)} suggestions; all require human review. No saved labels changed.')

if __name__=='__main__':main()
