"""Legacy trial candidate controls and attribution hints.

Production direction is contextual in rimsky.scene_direction. These keyword
hints are not authoritative model corrections or listening qualifications.
"""
import re
from rimsky.speaker_resolution import resumes_after_insertion

def explicit_technique(text):
    text=text.lower()
    if re.search(r'\bбормот(?:ал|ала|али|нул|ала|ание)\b',text):return 'muttering'
    if re.search(r'\bхрип(?:ло|ловато)\b',text) and re.search(r'отозвал|сказал|ответил|произн|проговор|прошеп|бурк',text):return 'hoarse'
    if re.search(r'\b(?:прошептал|прошептала|шептал|шептала|шёпотом|шепотом)\b',text):return 'whispering'
    if re.search(r'\b(?:прокричал|прокричала|проорал|проорала|крикнул|крикнула|кричал|кричала)\b',text):return 'shouting'
    return None

def attribution_directions(units,blocks):
    """Apply the attribution to its performer, never to the narrator saying it.

    This is a high-confidence constraint for the director, not an emotion model.
    Resumed speech shares the constraint only within the same continuing turn.
    """
    by_id={b['id']:b['text'] for b in blocks if b.get('text')};result={}
    for i,u in enumerate(units):
        if u['mode']!='narrator':continue
        technique=explicit_technique(u['text'])
        if not technique or i==0:continue
        prior=units[i-1]
        if prior['mode']!='speech' or prior['source_id']!=u['source_id']:continue
        evidence=u['text'].strip()
        result[prior['id']]={'technique':technique,'evidence':evidence,'basis':'explicit_attribution'}
        full=by_id[u['source_id']]
        # A remark can comprise several complete sentences before its author
        # attribution. Sentence synthesis boundaries are not speech-turn ends.
        # Stop at narration, a paragraph boundary or a different performer.
        j=i-2
        while j>=0 and units[j]['source_id']==prior['source_id'] and units[j]['speaker']==prior['speaker'] and units[j]['mode']=='speech':
            result[units[j]['id']]=dict(result[prior['id']]);j-=1
        following=units[i+1] if i+1<len(units) else None
        punctuationless_insertion=(following is not None
            and following['mode']=='speech' and following['source_id']==prior['source_id']
            and following['speaker']==prior['speaker']
            and not u['text'].rstrip().endswith(('.','!','?')) and following['text'].lstrip().startswith('—'))
        if following is not None and (resumes_after_insertion(prior,u,following,full) or punctuationless_insertion):
            j=i+1
            while j<len(units) and units[j]['source_id']==prior['source_id'] and units[j]['speaker']==prior['speaker'] and units[j]['mode']=='speech':
                result[units[j]['id']]=dict(result[prior['id']]);j+=1
    return result

def delivery_profile(speaker,technique='ordinary',energy='conversational',profile_sha256=None,persona=None):
    result={'character_id':speaker,'reference_index':0,'baseline_tags':[],'technique_tags':[],
            'technique':technique,'energy':energy,'status':'candidate_controls','listening_approved':False}
    result['required_technique']=technique
    result['realization_status']='candidate' if technique in ('ordinary','whispering','shouting') else 'unmapped'
    persona=persona or {}
    if persona.get('baseline_register'):result['baseline_register']=persona['baseline_register']
    if persona.get('pitch')=='low':result['baseline_tags'].append('pitch_low')
    if persona.get('baseline_technique')=='hoarse':
        result['reference_index']=1
        result['baseline_technique']='hoarse'
        result['baseline_register']=persona.get('baseline_register','persistent rough register')
    if profile_sha256:result['character_profile_sha256']=profile_sha256
    if technique=='hoarse':
        result['realization_status']='approximation'
        result['technique_limit']='Keep the persistent reference; temporary hoarseness must not recast the speaker. Hoarseness remains a listening check.'
    elif technique=='muttering':
        result['realization_status']='approximation'
        result['technique_tags']=['expressive_low','speed_slow']
        result['technique_limit']='Restrained slow candidate; native Higgs has no muttering tag. Must be judged by ear.'
    elif technique in ('whispering','shouting'):result['technique_tags']=[technique]
    if 'pitch_low' in result['baseline_tags'] and technique=='shouting':
        # A low-reference control trial retains this character's low register.
        # Drop the contradictory expressive_low baseline for this departure.
        result['baseline_tags']=[tag for tag in result['baseline_tags'] if tag!='expressive_low']
        result['technique_tags']=['expressive_high','shouting']
        result['control_evidence']='Unqualified low-register shouting candidate; exact control tuple needs listening approval.'
    if energy=='subdued' and technique!='shouting':result['technique_tags'].append('expressive_low')
    return result


def scene_delivery_profile(speaker,direction,persona,profile_sha256):
    """Compile a complete scene proposal without discarding its audible act.

    These are trial controls. Production still requires the Higgs pack's exact
    model/reference/control listening qualification, not a tag-name assumption.
    """
    from rimsky.scene_direction import require_realization
    register=direction['register']
    energy={'ordinary':'conversational','subdued':'subdued','warm':'subdued',
            'animated':'animated','intense':'intense','strained':'subdued'}[register]
    profile=delivery_profile(speaker,direction['technique'],energy,profile_sha256,persona)
    profile['disable_emotion_tags']=register!='warm'
    # Warmth has several incompatible relationship intents. This legacy
    # candidate compiler cannot qualify one from a native tag's name.
    if register in ('animated','intense'):profile['technique_tags'].append('expressive_high')
    profile['technique_tags']=list(dict.fromkeys(profile['technique_tags']))
    profile['scene_register']=register
    profile['scene_request_sha256']=direction['request_sha256']
    profile['audible_stance']=direction['audible_stance']
    profile['scene_evidence']=direction['evidence']
    require_realization(direction,profile)
    return profile
