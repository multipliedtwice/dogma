"""Bound lexical-fidelity decoding profiles; no voice or prosody effects."""
import copy
from .util import digest,AppError

PROFILES={'careful':{'temperature':.55,'top_p':.9},
          'conservative':{'temperature':.35,'top_p':.8},
          'careful_full':{'temperature':.55,'top_p':.9},
          'conservative_full':{'temperature':.35,'top_p':.8},
          'balanced_full':{'temperature':.65,'top_p':.92}}

def execution_pack(pack,profile=None,text=None):
    if not profile:return pack
    if profile not in PROFILES:raise AppError('Unknown word-recovery decoding profile')
    view=copy.deepcopy(pack)
    sampling={**pack['speech']['sampling'],**PROFILES[profile]}
    from .speech_quality import word_tokens
    cap=pack['speech'].get('max_new_tokens',2048)
    # Preserve the first profiles' immutable receipts, but do not estimate a
    # sentence budget from word count for new recovery attempts. Slow reviewed
    # performances can require substantially more audio frames per word.
    if text and profile in ('careful','conservative'):
        cap=min(cap,max(384,20*len(word_tokens(text))+160))
    policy={'schema':1,'profile':profile,'base_pack_fingerprint':pack['_fingerprint'],
            'sampling':sampling,'max_new_tokens':cap,
            'purpose':'Source-word fidelity; unchanged voice, mood and references; token caps still fail closed'}
    view['speech']['sampling']=sampling
    view['speech']['max_new_tokens']=cap
    view['_fingerprint']=digest(policy)
    view['_decoding_recovery']=policy
    return view
