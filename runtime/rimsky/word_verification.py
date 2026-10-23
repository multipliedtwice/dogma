"""Recomputable word receipts with narrow Russian spoken equivalences."""
import re
import difflib
from .speech_quality import word_tokens,transcript_check


def final_devoicing_equivalence(expected,heard):
    source=word_tokens(expected);recognised=word_tokens(heard)
    if len(source)!=len(recognised):return heard,[]
    pairs=dict(zip('бвгджзпфктшс','пфктшспфктшс'))
    corrected=[];changes=[]
    for i,(a,b) in enumerate(zip(source,recognised)):
        if a==b:corrected.append(b);continue
        soft=(len(a)>=3 and len(a)==len(b) and a.endswith('ь') and b.endswith('ь')
            and a[:-2]==b[:-2] and a[-2] in pairs and b[-2] in pairs and pairs[a[-2]]==pairs[b[-2]]
            and re.fullmatch('[а-яё]+',a) and re.fullmatch('[а-яё]+',b))
        if soft:
            corrected.append(a);changes.append({'index':i,'source_word':a,'heard_word':b,
                'rule':'russian-word-final-soft-paired-consonant-devoicing-v1'});continue
        if (len(a)<2 or len(a)!=len(b) or a[:-1]!=b[:-1]
            or not re.fullmatch('[а-яё]+',a) or not re.fullmatch('[а-яё]+',b)
            or a[-1] not in pairs or b[-1] not in pairs or pairs[a[-1]]!=pairs[b[-1]]):return heard,[]
        corrected.append(a);changes.append({'index':i,'source_word':a,'heard_word':b,
            'rule':'russian-word-final-paired-consonant-devoicing-v1'})
    return (' '.join(corrected) if changes else heard),changes


def proper_name_hyphen_expansion(expected,heard):
    """Restore a source-spelled compound name's boundary, never its letters.

    An independent recognizer may write Аз-Лаар as Азлаар. Require the
    capitalized, hyphenated spelling in the source and exact concatenation of
    all its components. Азлар (missing а), changed vowels, ordinary compounds
    and arbitrary adjacent words remain mismatches.
    """
    source=word_tokens(expected);recognised=word_tokens(heard);allowed={}
    pattern=r'(?<!\w)[А-ЯЁ][а-яё\u0301]+(?:[-‐‑][А-ЯЁ][а-яё\u0301]+)+(?!\w)'
    for match in re.finditer(pattern,expected):
        start=len(word_tokens(expected[:match.start()]));parts=word_tokens(match[0])
        allowed[(start,start+len(parts))]=(parts,match[0])
    expanded=[];changes=[]
    for tag,i,j,k,l in difflib.SequenceMatcher(a=source,b=recognised,autojunk=False).get_opcodes():
        entry=allowed.get((i,j))
        if tag=='replace' and l-k==1 and entry and recognised[k]==''.join(entry[0]):
            expanded.extend(entry[0]);changes.append({'source_name':entry[1],
                'source_tokens':entry[0],'heard_token':recognised[k],
                'rule':'exact-source-hyphenated-proper-name-boundary-v1'})
        else:expanded.extend(recognised[k:l])
    # Unchanged transcripts keep their own spelling (hyphenated sustained sounds such as У-у-у).
    return (' '.join(expanded) if changes else heard),changes


def reconcile(expected,raw,language):
    """Never accept a claimed status; first reproduce the complete raw check."""
    if not isinstance(raw,dict) or not isinstance(raw.get('heard'),str):return None
    recomputed=transcript_check(expected,raw['heard'],raw.get('audio_sha256'),raw.get('verifier_sha256'))
    if raw!=recomputed:return None
    if raw['status']=='asr_exact_match':return dict(raw)
    if str(language).casefold() not in ('ru','russian'):return dict(raw)
    canonical,boundaries=proper_name_hyphen_expansion(expected,raw['heard'])
    canonical,changes=final_devoicing_equivalence(expected,canonical)
    check=transcript_check(expected,canonical,raw['audio_sha256'],raw['verifier_sha256'])
    if not (changes or boundaries) or check['status']!='asr_exact_match':return dict(raw)
    return {**check,'raw_verification':dict(raw),'phonological_equivalences':changes,
        'normalization_language':'Russian','human_approval':False,
        **({'proper_name_boundaries':boundaries} if boundaries else {})}


def verified(expected,audit,audio_sha256,verifier_sha256,language):
    if not isinstance(audit,dict) or audit.get('audio_sha256')!=audio_sha256 or audit.get('verifier_sha256')!=verifier_sha256:return False
    raw=audit.get('raw_verification',audit)
    if not isinstance(raw,dict) or raw.get('audio_sha256')!=audio_sha256 or raw.get('verifier_sha256')!=verifier_sha256:return False
    reproduced=reconcile(expected,raw,language)
    return reproduced==audit and audit.get('status')=='asr_exact_match'
