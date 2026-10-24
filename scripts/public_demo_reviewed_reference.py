"""Private, development-only reference eligibility; never exports corpus data."""
from __future__ import annotations
import hashlib,json,importlib.metadata
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'runtime')]
import render_public_audiobook_demo as renderer
from rimsky.audio_join import boundary_check
from rimsky.util import file_digest

SELF=Path(__file__).resolve()
POLICY='public-demo-human-bucket-reference-override-v1'
DOSSIER_SHA256='41f11b3806a3bdc046487958ccfdc3bfb2812048c5804f1ad6b3bc9dba46cff2'
ALTERNATE_AUDIO_SHA256='5dd71ead1e7c66058e4bd9d7da4def999bdf7ec52f282851e6868c238b1aad23'

def local(value,base=None):
    base=ROOT if base is None else Path(base)
    p=Path(value);p=(p if p.is_absolute() else base/p).resolve()
    if not p.is_relative_to(ROOT):raise ValueError('Reference provenance must remain in this private workspace')
    return p

def checked(value,checksum,base=None):
    return renderer.checked(local(value,base),checksum)

def pcm_slice(bank,parent,span):
    import numpy as np
    import soundfile as sf
    bi,pi=sf.info(bank),sf.info(parent)
    if bi.subtype!='PCM_16' or pi.subtype!='PCM_16' or bi.channels!=1 or pi.channels!=1 or bi.samplerate!=pi.samplerate:
        raise ValueError('Reference relation must use unchanged mono PCM16 at the same rate')
    b,br=sf.read(bank,dtype='int16');p,pr=sf.read(parent,dtype='int16')
    start,end=span['start_sample'],span['end_sample_exclusive']
    if (type(start) is not int or type(end) is not int or not 0<=start<end<=len(p)
            or span!={'sample_rate_hz':br,'start_sample':start,'end_sample_exclusive':end,
                      'archive_sample_count':len(p),'bank_sample_count':len(b)}
            or not np.array_equal(b,p[start:end])):raise ValueError('Alternate is not the exact reviewed archive PCM sample slice')
    return {'kind':'exact-archive-pcm16-slice',**span}

def human_row(review,label,bucket):
    decision=review['decisions'][label['id']]
    if decision!=label['decision'] or decision.get('rejected') is not False:raise ValueError('Canonical human review differs or rejected this performance')
    # The closed dossier candidates have an explicit human voice correction and
    # a confirmed proposed mood. No predictor/identity proposal supplies either.
    if (decision.get('voice_verdict')!='no' or decision.get('corrected_voice_id')!=bucket['voice_family']
            or decision.get('mood_verdict')!='yes' or decision.get('corrected_mood') not in (None,'')
            or label['voice_family']!=bucket['voice_family'] or label['mood']!=bucket['mood']):
        raise ValueError('Reference lacks the exact same human-resolved family and confirmed mood')
    return {'review_row_sha256':renderer.sha(decision),'protected_label_row_sha256':renderer.sha(label),
            'same_human_bucket':True,'training_acceptance_implied':label['training_acceptance_implied']}

def eligible_reference(candidate,dossier,bucket,alternative=None):
    import soundfile as sf
    book_id,row_id=candidate['id'].split(':',1)
    protected=local(ROOT/'manual-labeling'/book_id);pins=dossier['sources']['protected_archives'][book_id]
    names={'review_sha256':'review.json','latest_sha256':'latest.json','archive_manifest_sha256':'archive-manifest.json',
           'labels_jsonl_sha256':'labels.jsonl','candidate_metadata_sha256':'metadata/candidates.jsonl','audio_index_sha256':'audio-index.json'}
    files={}
    for key,name in names.items():
        p=checked(protected/name,pins[key]);files[str(p)]=pins[key]
    review=renderer.read(protected/'review.json');latest=renderer.read(protected/'latest.json');archive=renderer.read(protected/'archive-manifest.json')
    if (latest['review_sha256']!=pins['review_sha256'] or archive['support_snapshot_review_sha256']!=pins['review_sha256']
            or review['candidates_sha256']!=pins['candidates_sha256'] or latest['candidates_sha256']!=pins['candidates_sha256']):
        raise ValueError('Protected review/latest/archive no longer describe the same canonical answers')
    labels=[json.loads(line) for line in (protected/'labels.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    matches=[row for row in labels if row['id']==row_id]
    if len(matches)!=1:raise ValueError('Reviewed candidate is absent or ambiguous in protected labels')
    label=matches[0]
    if label['review_sha256']!=pins['review_sha256']:raise ValueError('Derived label is stale')
    human=human_row(review,label,bucket)
    if (candidate.get('voice_basis')!='human' or candidate.get('mood_basis')!='human'
            or candidate['voice_family']!=bucket['voice_family'] or candidate['mood']!=bucket['mood']):
        raise ValueError('Bank bucket is not based on both human voice and mood')
    dataset=local(dossier['sources']['reference_dataset_path'])
    audio=checked(candidate['audio'],candidate['audio_sha256'],dataset)
    info=sf.info(audio);data,sr=sf.read(audio,dtype='float32')
    edges=boundary_check(data,sr)
    if (info.channels!=1 or sr!=24000 or info.subtype!='PCM_16' or edges['unsafe_start'] or edges['unsafe_end']
            or candidate['quality']['audio_sha256']!=candidate['audio_sha256']
            or candidate['quality']['edges']!=edges or abs(candidate['seconds']-info.duration)>1e-9):
        raise ValueError('Reviewed reference format, duration or safe native edges changed')
    files[str(audio)]=candidate['audio_sha256']
    parent=checked(label['audio']['archive'],label['audio']['sha256'],protected);files[str(parent)]=label['audio']['sha256']
    index=renderer.read(protected/'audio-index.json')['references']
    exact=[(name,row) for name,row in index.items() if row.get('sha256')==candidate['audio_sha256'] and row_id in name]
    if exact:
        name,row=exact[0];copy=checked(row['archive'],row['sha256'],protected);files[str(copy)]=row['sha256']
        relation={'kind':'protected-precision-copy-for-exact-reviewed-candidate','audio_index_row_sha256':renderer.sha(row),
                  'audio_index_source_sha256':hashlib.sha256(name.encode('utf-8')).hexdigest(),'protected_audio_sha256':row['sha256']}
    if alternative is not None or not exact:
        precision_relation=relation if exact else None
        proof=(alternative or {}).get('protected_provenance',{})
        if (proof.get('book_id')!=book_id or proof.get('review_row_id')!=row_id or proof.get('review_sha256')!=pins['review_sha256']
                or proof.get('protected_archive_audio_sha256')!=label['audio']['sha256']
                or local(proof['protected_archive_audio_path'])!=parent or proof.get('bank_audio_exact_pcm_subslice_of_archive') is not True
                or any(proof.get(k)!=label[k] for k in ('recording_id','recording_sha256','source_id','text_start','text_end'))):
            raise ValueError('Alternate lacks exact canonical source/recording/archive lineage')
        relation=pcm_slice(audio,parent,proof['pcm_subslice'])
        if precision_relation:relation['protected_precision_copy']=precision_relation
    return {'candidate_row_sha256':renderer.sha(candidate),'id':candidate['id'],'path':str(audio),
        'audio_sha256':candidate['audio_sha256'],'text':candidate['text'],
        'text_sha256':hashlib.sha256(candidate['text'].encode('utf-8')).hexdigest(),'quality':{'seconds':info.duration,'edges':edges},
        'human':human,'review_sha256':pins['review_sha256'],'archive_manifest_sha256':pins['archive_manifest_sha256'],
        'protected_parent_audio_sha256':label['audio']['sha256'],'archive_relation':relation,'files':files,
        'synthesis_listening_approved':False,'training_approval':False}

def reference_override(dossier_path,unit):
    path=checked(dossier_path,DOSSIER_SHA256);dossier=renderer.read(path)
    if unit['id']!='u0071':raise ValueError('Only u0071 has the reviewed alternative authorized here')
    entry=dossier['bucket_results'][unit['id']];bucket=entry['bucket'];original=unit['palette_reference']
    if (bucket!={'voice_family':unit['voice'],'mood':unit['mood']} or entry['alternate_candidate_count']!=1
            or entry['alternate_audio_sha256s']!=[ALTERNATE_AUDIO_SHA256]
            or entry['current_reference_audio_sha256']!=original['audio_sha256'] or entry['current_reference_id']!=original['evidence_id']):
        raise ValueError('Alternative bucket/source selection differs from the single reviewed choice')
    bank_path=checked(dossier['sources']['reference_bank_path'],dossier['sources']['reference_bank_sha256'])
    bank=renderer.read(bank_path);rows=[c for e in bank['entries'] for c in e['candidates']]
    before=[c for c in rows if c['id']==entry['current_reference_id'] and c['audio_sha256']==original['audio_sha256']]
    after=[c for c in rows if c['audio_sha256']==ALTERNATE_AUDIO_SHA256 and c['voice_family']==bucket['voice_family'] and c['mood']==bucket['mood']]
    if len(before)!=1 or len(after)!=1 or original['bank_sha256']!=file_digest(bank_path):raise ValueError('Original/alternate bank identity is ambiguous or changed')
    alt=next(x for x in dossier['distinct_alternates'] if x['bank_audio_sha256']==ALTERNATE_AUDIO_SHA256)
    if renderer.sha(after[0])!=alt['candidate_row_sha256']:raise ValueError('Alternative bank row changed')
    prior=eligible_reference(before[0],dossier,bucket);replacement=eligible_reference(after[0],dossier,bucket,alt)
    if original['text']!=prior['text'] or file_digest(local(original['path']))!=prior['audio_sha256']:raise ValueError('Frozen original reference no longer matches its protected bank candidate')
    files={str(path):DOSSIER_SHA256,str(bank_path):file_digest(bank_path),**prior['files'],**replacement['files']}
    actual={'path':replacement['path'],'audio_sha256':replacement['audio_sha256'],'text':replacement['text'],
        'reviewed':True,'evidence_id':replacement['id'],**bucket,'bank_sha256':file_digest(bank_path)}
    return {'policy':POLICY,'development_only':True,'dossier_sha256':DOSSIER_SHA256,'reference_bank_sha256':file_digest(bank_path),
        'bank_production_approved':bank['production_approved'],'runtime_versions':{name:importlib.metadata.version(name) for name in ('numpy','soundfile')},'bucket':bucket,'original_reference':original,
        'original':prior,'alternative':replacement,'actual_reference':actual,'files':files,
        'original_reference_unchanged':True,'human_approval':False,'training_approval':False,'synthesis_listening_approval':False}
