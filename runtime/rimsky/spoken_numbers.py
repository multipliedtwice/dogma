"""Versioned Russian numeric surfaces; no lexical or source-book rewriting."""
import re
POLICY='grouped-digits-v2'
# v3 (2026-10-16): digits with an ordinal suffix (2058-м, 5-му, 90-х, 1990-е, 2-я)
# become the ordinal in the case the suffix and preceding preposition select.
# v2 read '2058-м' as 'две тысячи пятьдесят восемь-м' and Higgs ran away on it.
POLICY_V3='grouped-digits-v3'
_SUFFIX=re.compile(r'(?<![\w\d])(\d+)-(ыми|ими|ого|его|ому|ему|ой|ей|ую|юю|ые|ие|ых|их|ым|им|ом|ем|ая|яя|ий|ый|ой|го|му|ми|й|я|е|х|м|ю)(?![\w])')
_PREPOSITIONAL={'в','во','на','о','об','обо','при'}
_DATIVE={'к','ко','по'}
_GENITIVE={'с','со','из','от','до','у','для','без','около','после','кроме','вокруг','среди','из-за','из-под'}
_INSTRUMENTAL={'с','со','за','над','под','перед','между'}


def _ordinal(number,suffix,before):
    """Ordinal words for a digit with suffix: only the last word inflects (две тысячи пятьдесят восьмом)."""
    from num2words import num2words
    import pymorphy3
    words=num2words(number,lang='ru',to='ordinal').split()
    morph=_ordinal.morph=getattr(_ordinal,'morph',None) or pymorphy3.MorphAnalyzer()
    parse=next((p for p in morph.parse(words[-1]) if 'ADJF' in p.tag or 'NUMR' in p.tag or 'Anum' in p.tag),None)
    if parse is None:return None
    forms=[f for f in parse.lexeme if f.word.endswith(suffix)]
    if not forms:return None
    preference=(['loct'] if before in _PREPOSITIONAL else ['datv'] if before in _DATIVE else
                ['gent','ablt'] if before in _GENITIVE else ['ablt'] if before in _INSTRUMENTAL else [])+['nomn','accs','gent','datv','ablt','loct']
    # Round decades with a plural-capable suffix are decades (1990-е, 90-х): plural first.
    plural=number%10==0 and suffix in ('е','х','ми','ые','ых','ыми')
    forms.sort(key=lambda f:(0 if ('plur' in f.tag)==plural else 1,min((preference.index(c) for c in preference if c in f.tag),default=99)))
    return ' '.join(words[:-1]+[forms[0].word.replace('ё','е') if 'ё' not in words[-1] else forms[0].word])


def _ordinals(text):
    def replace(m):
        before=re.findall(r'[А-Яа-яЁё-]+',text[:m.start()])
        word=_ordinal(int(m[1]),m[2],before[-1].casefold() if before else '')
        return word if word else m[0]
    return _SUFFIX.sub(replace,text)


def expand(text,policy=POLICY):
    if not re.search(r'\d',text):return text
    from num2words import num2words
    if policy==POLICY_V3:
        text=_ordinals(text);policy=POLICY
    if policy==POLICY:
        text=re.sub(r'(?<![\w\d])\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?![\w\d])',
            lambda m:re.sub(r'[ \u00a0\u202f]','',m[0]),text)
    elif policy!='legacy-v1':raise ValueError('Unknown spoken-number policy')
    text=re.sub(r'^(Глава)\s+(\d+)\s*$',lambda m:m[1]+' '+num2words(int(m[2]),lang='ru',to='ordinal',gender='f'),text)
    text=re.sub(r'(\d+)-го\b',lambda m:num2words(int(m[1]),lang='ru',to='ordinal',case='genitive'),text)
    text=re.sub(r'(\d+)-й\b',lambda m:num2words(int(m[1]),lang='ru',to='ordinal'),text)
    text=re.sub(r'(?<!\w)(\d[\d ]*\d|\d)[,.](\d+)(?!\w)',
        lambda m:num2words(m[0].replace(' ','').replace(',','.'),lang='ru'),text)
    text=re.sub(r'(?<!\w)(\d+)([А-ЯA-Z])(?!\w)',lambda m:num2words(int(m[1]),lang='ru')+' '+m[2],text)
    return re.sub(r'(?<!\w)\d+(?!\w)',lambda m:num2words(int(m[0]),lang='ru'),text)

def same_surface(expected,native):
    return bool(re.search(r'\d',native)) and not re.search(r'[<>{}\[\]]',native) and expand(native)==expected
