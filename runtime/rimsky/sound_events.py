"""Inventory-driven nonverbal route; unavailable events hold one turn."""
import math
from .util import SpeechHoldError,inside,file_digest,digest,atomic_write
from .speech_backend import implementation_fingerprint

def plan(pack,direction,persona,signature,seed=0):
    kind=direction.get('event_kind','other')
    if direction.get('needs_adjudication') or kind in ('none','other'):
        raise SpeechHoldError('Sound event needs source-backed adjudication; turn held.')
    actor=signature.get('character_id')
    role='dog' if kind.startswith('dog_') else 'human' if kind.startswith('human_') else 'other'
    if persona.get('sound_role')!=role:
        raise SpeechHoldError('Sound species disagrees with the persistent persona; turn held.')
    bank=[v for v in pack.get('sound_events',[]) if v.get('kind')==kind and
          v.get('register')==direction['register'] and
          (v.get('character_id')==actor or kind.startswith('dog_') and v.get('character_id')=='*')]
    if not bank:raise SpeechHoldError('No inventory take serves this sound and scene; turn held.')
    bank=sorted(bank,key=lambda entry:entry['id'])
    variant_seed=seed+int(digest({'turn_id':direction.get('turn_id'),'actor':actor})[:8],16)
    entry=bank[variant_seed%len(bank)]
    path=inside(pack['_root'],entry['audio'])
    if file_digest(path)!=pack['files'].get(entry['audio']):raise SpeechHoldError('Sound-event asset changed.')
    evaluation=pack['speech'].get('purpose')=='evaluation_only'
    if not evaluation:
        import json
        review_path=inside(pack['_root'],pack['speech']['listening_review'])
        if file_digest(review_path)!=pack['files'][pack['speech']['listening_review']]:raise SpeechHoldError('Sound-event listening qualification changed.')
        review=json.loads(review_path.read_text('utf-8'))
        expected={'control_type':'sound_event','control_id':entry['id'],'asset_sha256':pack['files'][entry['audio']],
                  'kind':kind,'register':direction['register'],'gain':entry.get('gain',1.),'verdict':'approved'}
        if not any(all(r.get(k)==v for k,v in expected.items()) for r in review.get('reviews',[])):
            raise SpeechHoldError('Sound event has no matching listening approval.')
    return {'schema':1,'backend':'nonverbal_asset','pack':pack['_fingerprint'],'direction':direction,
            'character_profile':persona,'voice_signature':signature,
            'event':entry,'asset_sha256':pack['files'][entry['audio']],
            'sampling':{'seed':seed},'variant_count':len(bank),'qualification_bypassed':evaluation,
            'qualification_sha256':None if evaluation else pack['files'][pack['speech']['listening_review']],
            'implementation_sha256':implementation_fingerprint(__name__)}

def render(pack,planned,output):
    import numpy as np
    import soundfile as sf
    import io
    from scipy.signal import resample_poly
    from .audio_join import boundary_check
    path=inside(pack['_root'],planned['event']['audio'])
    if file_digest(path)!=planned['asset_sha256']:raise SpeechHoldError('Sound-event asset changed.')
    x,rate=sf.read(path,dtype='float32',always_2d=True)
    gain=planned['event'].get('gain',1.)
    if x.shape[1]!=1 or not np.isfinite(x).all() or not len(x) or not .05<=len(x)/rate<=20:
        raise SpeechHoldError('Invalid sound-event audio.')
    if type(gain) not in (int,float) or not math.isfinite(gain) or not .01<=gain<=2.82:
        raise SpeechHoldError('Invalid scene-qualified sound-event gain.')
    x=x[:,0]
    # Resampling and silence padding are fixed keyed operations; no pitch
    # shifting of the asset or invented variations of one recorded sound.
    divisor=math.gcd(rate,24000);x=resample_poly(x,24000//divisor,rate//divisor)*gain
    if float(np.max(np.abs(x)))>=.999:raise SpeechHoldError('Sound-event gain clips.')
    edge=boundary_check(x,24000)
    if edge['unsafe_start'] or edge['unsafe_end']:raise SpeechHoldError('Sound asset has an abrupt edge; another inventory take is required.')
    data=io.BytesIO();sf.write(data,x,24000,format='WAV',subtype='PCM_16');atomic_write(output,data.getvalue())
    return {'duration':len(x)/24000,'sample_rate':24000,'samples':len(x),'warning':'',
            'synthesis':{**planned,'plan_sha256':digest(planned)},
            'audio_checks':{'boundaries':edge,'sound_event':planned['event']['kind']},
            'word_check':{'status':'nonverbal_not_spoken_text','audio_sha256':file_digest(output),
                          'asset_sha256':planned['asset_sha256'],'event_kind':planned['event']['kind']}}
