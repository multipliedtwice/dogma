"""Source-bound lexical stress notation for the spoken-text layer.

Meaning/stress decisions come from contextual analysis or explicit corrections;
this module applies them without changing words or the printed source.
"""
import re
from .util import digest

ACUTE='\u0301'
VOWELS=frozenset('аеёиоуыэюяАЕЁИОУЫЭЮЯ')

PHRASE_POLICY='ru-feet-preposition-v1'
# Narrow movement idiom; looking at legs and clothing on legs are not covered.
_GET_UP=re.compile(r'\b(?:встал[аи]?|вста[вл][а-яё]*|встать|встан[а-яё]*|'
                   r'вскоч[а-яё]+|вскаки[а-яё]+|поднял[а-яё]*|подним[а-яё]+|'
                   r'поднят[а-яё]*|постав[а-яё]+|став[а-яё]+)\b',re.I)
_FEET=re.compile(r'(?<![\w\u0301])(на)\s+ноги\b',re.I)
_BETWEEN=frozenset(('он она они его её их меня тебя нас вас себя снова вновь наконец '
                    'сразу быстро резко тяжело едва медленно уже сам сама сами с трудом').split())


def phrase_stress(text):
    """Apply the listener-approved getting-onto-one's-feet idiom to native text.

    No scene inference, broad homograph replacement or punctuation edits.
    Explicit stress takes precedence. Evidence and offsets refer to input bytes.
    """
    edits=[]
    for match in _FEET.finditer(text):
        end=match.end()
        if end<len(text) and text[end]==ACUTE:continue
        # Bound to the same clause and a nearby movement verb. Do not bridge a
        # quotation, narrator insertion, comma, or another sentence.
        prefix=re.split(r'[.!?…,:;—\n«»"]',text[:match.start()])[-1]
        words=list(re.finditer(r'[А-Яа-яЁё]+',prefix))
        nearby=prefix[words[-5].start():] if len(words)>5 else prefix
        actions=list(_GET_UP.finditer(nearby))
        if not actions:continue
        tail=re.findall(r'[А-Яа-яЁё]+',nearby[actions[-1].end():].casefold())
        if any(word not in _BETWEEN for word in tail):continue
        # A mark on either syllable of the following word is an explicit choice.
        if ACUTE in text[match.end():match.end()+1] or re.search(r'но́ги|ноги́',text[match.start():match.end()+2],re.I):continue
        word=match[1]
        edits.append({'start':match.start(1),'end':match.end(1),'word':word,
                      'marked':word+ACUTE,'sense':'getting or putting onto feet',
                      'evidence':nearby+match[0],'rule_id':PHRASE_POLICY})
    plan={'policy':PHRASE_POLICY,'source_text_sha256':digest(text.encode('utf8')),'edits':edits}
    return apply_stress(text,plan),plan


def unmarked(text):
    return text.replace(ACUTE,'')


def validate_marks(text):
    for i,char in enumerate(text):
        if char==ACUTE and (not i or text[i-1] not in VOWELS):
            raise ValueError('Stress mark must follow a Russian vowel')


def apply_stress(text,plan):
    if plan.get('source_text_sha256')!=digest(text.encode('utf8')):
        raise ValueError('Stress plan belongs to different spoken text')
    edits=plan.get('edits',[]);last=0;parts=[]
    for edit in sorted(edits,key=lambda e:e['start']):
        start,end=edit['start'],edit['end'];word=edit['word'];marked=edit['marked']
        if (type(start) is not int or type(end) is not int or not last<=start<end<=len(text)
            or text[start:end]!=word or unmarked(marked)!=word or marked.count(ACUTE)!=1
            or not re.fullmatch('[А-Яа-яЁё]+',word)):
            raise ValueError('Invalid exact-word stress edit')
        if (start and text[start-1].isalpha()) or (end<len(text) and text[end].isalpha()):
            raise ValueError('Stress edit must cover an entire word')
        if not edit.get('sense') or not edit.get('evidence'):
            raise ValueError('Stress edit requires a lexical sense and evidence')
        validate_marks(marked)
        parts.extend([text[last:start],marked]);last=end
    parts.append(text[last:]);return ''.join(parts)


_RESOLVER=None
_RESOLVED={}


def set_resolver(resolver):
    """The app resolves automatic pronunciation in its inference worker (torch lives there)."""
    global _RESOLVER
    _RESOLVER=resolver


def _automatic(text,pronunciation):
    key=(digest({k:pronunciation.get(k) for k in ('policy','names','common','terms')}),text)
    if key not in _RESOLVED:
        if len(_RESOLVED)>50000:_RESOLVED.clear()
        if _RESOLVER is not None:
            result=_RESOLVER([text],pronunciation)[0]
        else:
            from .auto_pronunciation import auto_pronunciation
            names={cid:row['stressed'] for cid,row in pronunciation.get('names',{}).items()}
            result=auto_pronunciation(text,names,pronunciation.get('common',[]))[0]
        from .speech_quality import word_tokens
        if word_tokens(unmarked(result))!=word_tokens(unmarked(text)):
            raise ValueError('Automatic pronunciation changed the words')
        validate_marks(result)
        _RESOLVED[key]=result
    return _RESOLVED[key]


def synthesis_text(text, pronunciation=None):
    """The exact synthesis input for one passage: phrase rule, then automatic pronunciation.

    `pronunciation` is the generation's analysis['pronunciation'] (policy, names,
    common name words). Without it the result equals phrase_stress, so older
    generations keep their cache keys.
    """
    text,plan=phrase_stress(text)
    if not pronunciation or not pronunciation.get('policy'):
        return text,plan
    from .auto_pronunciation import POLICY
    if pronunciation['policy']!=POLICY:
        raise ValueError('Unknown pronunciation policy')
    # Listener respellings (е->э/ё: Мозэс, тэк-купол) come first, so the stress passes and their
    # word checks see the spoken spelling; the printed passage text never changes.
    respelled=[]
    if pronunciation.get('terms') or pronunciation.get('names'):
        from .respelling import respell_text
        text,respelled=respell_text(text,{cid:row['stressed'] for cid,row in pronunciation.get('names',{}).items()},
                                    pronunciation.get('common',[]),pronunciation.get('terms'),False,
                                    pronunciation.get('respelling_sources'))
    final=_automatic(text,pronunciation)
    if respelled:
        from .respelling import disclosure
        plan={**plan,'listener_respelling':disclosure(respelled)}
    return final,{**plan,'automatic':{'policy':POLICY,'changed':final!=text}}
