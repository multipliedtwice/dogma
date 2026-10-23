"""Small explicit performance palettes, with audited substitutions."""
from .util import SpeechHoldError, file_digest, inside
import json

def select(pack,voice,direction,pace='normal',signature=None):
    """Choose only an approved entry or an explicitly reviewed substitution.

    A numeric distance between moods would invent equivalence. `serves` records
    the allowed semantic substitutions from the listening qualification instead.
    """
    if direction.get('needs_adjudication'):raise SpeechHoldError('Scene direction is unresolved.')
    if direction.get('audio_kind')!='speech':raise SpeechHoldError('This turn needs the sound-event route.')
    wanted={k:direction[k] for k in ('register','technique')};wanted['pace']=pace
    if wanted['register']=='warm':
        if direction.get('warmth') not in ('companionable','reassuring','intimate'):
            raise SpeechHoldError('Warm delivery has no resolved relationship intent.')
        wanted['warmth']=direction['warmth']
    entries=voice.get('higgs_controls',{}).get('combinations',[])
    candidates=[]
    for entry in entries:
        if entry.get('tags') and pack['speech'].get('effects_policy','disabled')=='disabled':continue
        if entry.get('permanent_tags',[])!=(signature or {}).get('permanent_tags',[]):continue
        actual={k:entry.get(k) for k in wanted}
        exact=actual==wanted
        if not exact and wanted not in entry.get('serves',[]):continue
        reference=entry.get('reference','neutral')
        if signature and reference!=signature.get('reference','neutral'):continue
        candidates.append((not exact,entry.get('id',''),entry,actual))
    if not candidates:raise SpeechHoldError('No qualified performance combination serves this scene; turn held.')
    review_path=inside(pack['_root'],pack['speech']['listening_review'])
    if file_digest(review_path)!=pack['files'][pack['speech']['listening_review']]:
        raise SpeechHoldError('Listening qualification changed.')
    reviews=json.loads(review_path.read_text('utf-8'))
    for substituted,_,entry,actual in sorted(candidates,key=lambda c:(c[0],len(c[2].get('tags',[])),c[1])):
        ref=voice.get('references',{}).get(entry.get('reference','neutral'))
        if ref is None:continue
        expected={'control_type':'combination','control_id':entry['id'],
            'voice_family':voice.get('family',voice['id']),'reference_sha256':pack['files'][ref['audio']],
            'tags':entry['tags'],'verdict':'approved',**actual,'serves':entry.get('serves',[])}
        if entry.get('permanent_tags'):expected['permanent_tags']=entry['permanent_tags']
        if reviews.get('model_sha256')!=pack['files'][pack['speech']['model']]:continue
        if not any(all(row.get(k)==v for k,v in expected.items()) for row in reviews.get('reviews',[])):continue
        return {'combination':entry['id'],'requested':wanted,'applied':actual,
                'substituted':substituted,'qualification_sha256':file_digest(review_path)}
    raise SpeechHoldError('Performance palette has no matching model/reference/control approval.')
