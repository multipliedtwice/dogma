"""Source-bound lexical stress notation for the spoken-text layer.

Meaning/stress decisions come from contextual analysis or explicit corrections;
this module applies them without changing words or the printed source.
"""
import re
import unicodedata
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


def yo_fold(text):
    return text.replace('ё','е').replace('Ё','Е')


def yo_bound_differences(a,b,words):
    """True when unmarked a (native, whose coordinates `words` use) equals b, or differs from it only by
    е<->ё letters inside a listener-edited word: each entry {'start','end','printed','intended'} must name
    a[start:end]==intended whose yo-folded form is the folded printed word."""
    if len(a)!=len(b) or yo_fold(a)!=yo_fold(b):return False
    spans=[(w['start'],w['end']) for w in words or ()
        if 0<=w['start']<w['end']<=len(a) and a[w['start']:w['end']]==unmarked(w['intended'])
        and yo_fold(a[w['start']:w['end']])==yo_fold(w['printed'])]
    return all(any(s<=i<e for s,e in spans) for i in range(len(a)) if a[i]!=b[i])


def validate_marks(text):
    for i,char in enumerate(text):
        if char==ACUTE and (not i or text[i-1] not in VOWELS):
            raise ValueError('Stress mark must follow a Russian vowel')


def _word_character(char):
    return char.isalnum()or char=='_'or char in '\u200c\u200d'or unicodedata.category(char).startswith('M')


def _whole_word(text,start,end):
    if start and _word_character(text[start-1])or end<len(text)and _word_character(text[end]):return False
    hyphens='-\u2010\u2011'
    if start>1 and text[start-1]in hyphens and _word_character(text[start-2]):return False
    if end+1<len(text)and text[end]in hyphens and _word_character(text[end+1]):return False
    return True


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
        if not _whole_word(text,start,end):
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
    """The exact synthesis input and source-bound preparation evidence.

    New recipes use frozen automatic text, final human corrections and phonetic
    spellings. Without a recipe the historical phrase/automatic flow and its
    cache inputs stay unchanged.
    """
    if pronunciation and pronunciation.get('policy')=='russian-preparation-v1':
        from .russian_preparation import resolve_entry
        from .russian_preparation_providers import module_digest
        from . import russian_pronunciation as phonetics
        entry=resolve_entry(text,pronunciation)
        final=entry['automatic_text']
        validate_marks(final)
        plan={'prepared_pronunciation':{'policy':pronunciation['policy'],
              'entry_sha256':entry['sha256'],'sound_sha256':entry['sound_sha256']}}
        if pronunciation.get('listener_stress'):
            from . import listener_stress
            final,listener=listener_stress.apply(final,pronunciation['listener_stress'],
                **pronunciation.get('listener_source',{}))
            listener['module_sha256']=module_digest('rimsky.listener_stress')
            plan['listener_stress']=listener
        before_phonetic=final
        final,records=phonetics.apply(before_phonetic)
        phonetics.validate(before_phonetic,final,records)
        plan['russian_phonetics']={'rule':phonetics.RULE,'prephonetic_source':before_phonetic,
            'records':records,'module_sha256':module_digest('rimsky.russian_pronunciation')}
        return final,plan
    text,plan=phrase_stress(text)
    if not pronunciation or not pronunciation.get('policy'):
        if pronunciation and pronunciation.get('listener_stress'):
            from .listener_stress import apply
            text,listener=apply(text,pronunciation['listener_stress'],**pronunciation.get('listener_source',{}))
            plan={**plan,'listener_stress':listener}
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
    if pronunciation.get('listener_stress'):
        from .listener_stress import apply
        # Exact source binding travels with each part. Human occurrences and
        # families both win after phrase rules and every automatic guard.
        final,listener=apply(final,pronunciation['listener_stress'],**pronunciation.get('listener_source',{}))
        plan={**plan,'listener_stress':listener}
    if respelled:
        from .respelling import disclosure
        plan={**plan,'listener_respelling':disclosure(respelled)}
    return final,{**plan,'automatic':{'policy':POLICY,'changed':final!=text}}


def cache_binding(receipt):
    """Per-part sound evidence, preserving the historical legacy key shape.

    Diagnostic preparation evidence and unrelated profile revisions never
    invalidate another part. Actual human applications remain sound inputs even
    when their intended spelling was already present in automatic text.
    """
    listener=receipt.get('listener_stress')or {}
    binding={}
    if listener.get('applications'):
        binding['listener_stress']={'policy':listener.get('policy'),
                                    'applications':listener['applications']}
    prepared=receipt.get('prepared_pronunciation')
    if prepared:
        if prepared.get('policy')!='russian-preparation-v1'or not prepared.get('sound_sha256'):
            raise ValueError('Invalid prepared pronunciation cache binding')
        binding['prepared_pronunciation']={'policy':prepared['policy'],'sound_sha256':prepared['sound_sha256']}
        if listener.get('edits'):
            binding['listener_stress']={**binding.get('listener_stress',{'policy':listener.get('policy')}),
                                        'edits':listener['edits']}
        if listener.get('applications')or listener.get('edits'):
            binding['listener_stress']['module_sha256']=listener.get('module_sha256')
        binding['russian_phonetics']=receipt['russian_phonetics']
    return binding
