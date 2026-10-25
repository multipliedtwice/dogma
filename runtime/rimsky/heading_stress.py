"""Source-bound lexical stress for otherwise unmarked Russian headings.

This fills dictionary words only. Existing source/name/HS/ё marks stay intact;
final human feedback is applied by the caller after this automatic layer.
"""
from __future__ import annotations
import copy,re
from .util import digest
from . import homograph_stress as hs
from .name_stress import ACUTE,VOWELS

POLICY='russian-heading-lexical-stress-v1'
_WORD=re.compile(r'[А-Яа-яЁё́]+')

def _seal(value):return {**value,'sha256':digest(value)}

def _check(value):
    if value.get('policy')!=POLICY or value.get('sha256')!=digest({k:v for k,v in value.items()if k!='sha256'}):
        raise ValueError('Heading stress witness changed')

def analyze(text,source,*,kind,names=None,silero=None,ruaccent=None,known=None):
    """Freeze dense tagger witnesses; no providers or acoustic inference here."""
    from .russian_preparation import source_binding
    from .spoken_pronunciation import _whole_word,validate_marks
    source=source_binding(source);validate_marks(text)
    base={'policy':POLICY,'source':source,'text_sha256':digest(text.encode('utf8')),
          'source_kind':kind,'text':text,'rows':[],'disagreements':[]}
    if kind not in ('heading','title'):return _seal(base)
    from .name_stress import name_spans
    protected=name_spans(text,names or{},implicit_single=True)
    if known is None:
        from .proper_noun_stress import _morph
        known=_morph().word_is_known
    todo=[]
    for match,key,index in hs.occurrences(text):
        word=match[0]
        if (not _whole_word(text,match.start(),match.end())or ACUTE in word or 'ё'in word.casefold()
            or sum(c in VOWELS for c in word)<2
            or any(a<match.end()and match.start()<b for a,b,_ in protected)
            or not known(word.casefold())):continue
        todo.append((match,key,index))
    if not todo:return _seal(base)
    if silero is None:
        from .auto_pronunciation import _silero
        silero=lambda value:_silero(value,[],every_word=True,yo=False)
    dense=silero(text);validate_marks(dense)
    if dense.replace(ACUTE,'')!=text.replace(ACUTE,''):
        raise ValueError('Heading stress tagger changed source letters or spacing')
    tagged=hs.occurrences(dense)
    original=hs.occurrences(text)
    if [(k,i)for _,k,i in tagged]!=[(k,i)for _,k,i in original]:
        raise ValueError('Heading stress tagger changed source words')
    theirs=(ruaccent or hs.ruaccent_stresses)(text)
    hits={(k,i):m[0]for m,k,i in tagged}
    for match,key,index in todo:
        word=match[0];si=hs.stress_index(hits[(key,index)]);ri=theirs.get((key,index))
        for position in (si,ri):
            if position is not None and (type(position)is not int or not 0<=position<len(word)or word[position]not in VOWELS):
                raise ValueError('Heading tagger stress is not a source vowel')
        row={'sha256':digest(text.encode('utf8')),'word':key,'index':index,'text':text,'sentence':text,
             'surface':word,'start':match.start(),'end':match.end(),
             'silero':hs.mark_at(word,si),'ruaccent':None if ri is None else hs.mark_at(word,ri),
             'silero_stress':si,'ruaccent_stress':ri,'agreement':si is not None and si==ri}
        if si is None and ri is None:raise ValueError('Unresolved dictionary heading stress: '+word)
        base['rows'].append(row)
        if not row['agreement']:base['disagreements'].append(copy.deepcopy(row))
    base['silero_text']=dense
    return _seal(base)

def request(witness):
    _check(witness)
    value=hs.request(witness['disagreements'])
    return {**value,'task':'russian_heading_lexical_stress_adjudication'}

def apply(text,witness,answer=None):
    """Apply agreed/once-adjudicated marks; reject missing or unmarked decisions."""
    _check(witness)
    if digest(text.encode('utf8'))!=witness['text_sha256']or text!=witness['text']:
        raise ValueError('Heading stress belongs to another source input')
    req=request(witness);answer=answer if answer is not None else{'decisions':[]}
    hs.validate(answer,req)
    choices={d['id']:d for d in answer['decisions']}
    adjudicated={(r['word'],r['index']):choices['h'+str(i).zfill(4)]
                 for i,r in enumerate(witness['disagreements'])}
    edits=[]
    for row in witness['rows']:
        if text[row['start']:row['end']]!=row['surface']:raise ValueError('Heading word source binding changed')
        if row['agreement']:marked=row['silero'];authority='silero_ruaccent_agreement'
        else:
            decision=adjudicated[(row['word'],row['index'])];marked=decision['stressed'];authority='opus_adjudication'
        if marked.replace(ACUTE,'')!=row['surface']or marked.count(ACUTE)!=1:
            raise ValueError('Heading lexical stress remains unresolved: '+row['surface'])
        edits.append({'start':row['start'],'end':row['end'],'before':row['surface'],'after':marked,'authority':authority})
    final=text
    for edit in reversed(edits):final=final[:edit['start']]+edit['after']+final[edit['end']:]
    return final,_seal({'policy':POLICY,'source':witness['source'],'witness_sha256':witness['sha256'],
        'text_sha256':witness['text_sha256'],'result_sha256':digest(final.encode('utf8')),
        'answer_sha256':digest(answer),'edits':edits})

def derive_entry(entry,text,witness,answer=None,*,provider_receipts=(),module_sha256):
    """Preserve the base frozen entry and make the extra lexical layer explicit."""
    from .russian_preparation import validate_entry,sound_binding
    validate_entry(entry)
    if entry['automatic_text']!=text or entry['source']!=witness['source']:
        raise ValueError('Heading witness belongs to another frozen prepared entry')
    final,receipt=apply(text,witness,answer)
    derived=copy.deepcopy(entry)
    derived.update(automatic_text=final,heading_stress={'policy':POLICY,'base_entry':copy.deepcopy(entry),
        'base_entry_sha256':entry['sha256'],'witness':copy.deepcopy(witness),'answer':copy.deepcopy(answer),
        'provider_receipts':copy.deepcopy(list(provider_receipts)),'application':receipt})
    derived['module_pins']={**derived['module_pins'],'heading_stress':module_sha256}
    derived.pop('sha256');derived['sound_sha256']=digest(sound_binding(derived));derived['sha256']=digest(derived)
    validate_entry(derived);return derived
