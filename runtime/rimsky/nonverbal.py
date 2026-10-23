"""Source-backed animal vocalizations are audio events, not spoken letters."""
import re


def animal_vocalization(text, character):
    nature=str(character.get('nature','')).casefold()
    dog=character.get('gender_or_role')=='animal' and re.search(r'п[её]с|собак|dog',str(character.get('description','')).casefold())
    if re.search(r'\b(?:человек|human|man)\b|\b(?:не|not)\s+(?:п[её]с|собака|dog)\b',nature):return None
    dog=dog or bool(re.search(r'\b(?:п[её]с|собака|dog)\b',nature))
    if not dog:
        return None
    value=re.sub(r'^[\s—–-]+|[\s.!?…]+$','',text.casefold())
    if re.fullmatch(r'(?:г|g)?[рr](?:[-\s]*[рr]){2,}',value):
        return {'kind':'dog_growl','basis':'animal identity plus explicit growl onomatopoeia',
                'source_text':text,'spoken_words':False}
    return None


def asset_plan(intent,root):
    """Pin a licensed event source and every transformation, without TTS."""
    import json
    from pathlib import Path
    import numpy as np
    import soundfile as sf
    import scipy
    from scipy.signal import resample_poly
    from .util import file_digest
    from .audio_join import boundary_check
    if intent['kind']!='dog_growl':raise ValueError('No pinned asset for this animal vocalization')
    asset=root/'resources/nonverbal/dog/dog-growl.flac'
    proof=json.loads((root/'resources/nonverbal/dog-growl-source.json').read_text(encoding='utf-8'))
    if file_digest(asset)!=proof['source_sha256']:raise ValueError('Pinned event asset changed')
    x,sr=sf.read(asset,dtype='float32',always_2d=True);x=x.mean(axis=1)
    bounds=boundary_check(x,sr)
    if bounds['unsafe_start'] or bounds['unsafe_end']:raise ValueError('Event asset has unsafe edges')
    if sr!=96000:raise ValueError('Pinned event source sample rate changed')
    y=resample_poly(x,1,4)
    gain=10**(-30/20)/float(np.sqrt(np.mean(y*y)))
    return {'schema':1,'backend':'nonverbal_asset','text':intent['source_text'],'intent':intent,'source':proof,
            'processing':{'samplerate':24000,'mono':True,'asset_gain':gain,'padding_samples':960,
                          'resample_up':1,'resample_down':4,'resampler':'scipy '+scipy.__version__,'fade':False},
            'implementation_sha256':file_digest(Path(__file__)),'voice_family':'nonverbal:dog_growl',
            'reference':{'audio':asset.relative_to(root).as_posix(),'sha256':proof['source_sha256'],'text':''}}


def write_asset(plan,root,path):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from .util import file_digest
    from .audio_join import boundary_check
    source=root/plan['reference']['audio']
    if file_digest(source)!=plan['reference']['sha256']:raise ValueError('Event asset changed before rendering')
    x,sr=sf.read(source,dtype='float32',always_2d=True)
    if sr!=96000:raise ValueError('Event sample rate changed')
    processing=plan['processing']
    y=resample_poly(x.mean(axis=1),processing['resample_up'],processing['resample_down'])
    y=np.pad(y*processing['asset_gain'],(processing['padding_samples'],processing['padding_samples']))
    bounds=boundary_check(y,24000)
    if bounds['unsafe_start'] or bounds['unsafe_end']:raise ValueError('Prepared event has unsafe edges')
    sf.write(path,y,24000,subtype='PCM_16')
    return {'audio_sha256':file_digest(path),'duration':len(y)/24000,'sample_rate':24000,
            'audio_checks':bounds,'word_status':'nonverbal_not_spoken_text'}
