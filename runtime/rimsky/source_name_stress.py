"""Explicit source-name families outrank automatic guesses, below human notes."""
from __future__ import annotations
import re
from .name_stress import ACUTE,VOWELS,build,dictionary_name_forms,aligned_stress_index
from .auto_pronunciation import validate_name_stress
from .respelling import names_sources

RULE='source-cast-name-stress-v1'
WORD=re.compile('[А-Яа-яЁё́]+')


def _key(word):return word.casefold().replace('ё','е')


def explicit_mark(word):
    """One source stress position, retaining ё itself rather than inventing acute."""
    if not isinstance(word,str)or not re.fullmatch('[А-Яа-яЁё́]+',word):raise ValueError('Invalid explicit source-name word')
    from .proper_noun_stress import source_stress
    return source_stress(word)


def source_names(explicit_stress,names_rows,human_names):
    """Return effective cid->full spelling and source receipts; never edit rows.

    Inventory records carry exact block hashes/offsets from the source parser.
    They are not model proposals. Family identity and retained vowel mapping
    must both be unique; an authoritative human family suppresses inference.
    """
    effective={};families={}
    for cid,row in names_rows.items():
        validate_name_stress(row['name'],row['stressed']);effective[cid]=row['stressed']
        printed=list(WORD.finditer(row['name']));spoken=list(WORD.finditer(row['stressed']))
        if len(printed)!=len(spoken):raise ValueError('Cast name word coverage changed')
        overlay=row.get('series_listener_overlay')
        if overlay:
            names_sources({cid:row}) # validates complete display/sound checksum
            if overlay['binding']['character_id']!=cid:raise ValueError('Human word overlay belongs to another character')
        protected={(word['start'],word['end'])for word in (overlay or{}).get('words',[])}
        for plain,marked in zip(printed,spoken,strict=True):
            base=plain[0].replace(ACUTE,'');key=_key(base)
            group=families.setdefault(key,{'base':base,'forms':set(build({'probe':base})), 'slots':[]})
            group['forms'].update(_key(word)for word in dictionary_name_forms(base))
            group['slots'].append({'cid':cid,'start':marked.start(),'end':marked.end(),'printed':base,
                'before':marked[0],'protected':row.get('source')=='listener'or (plain.start(),plain.end())in protected})
    human_forms=build(human_names,implicit_single=True)
    human_slots={}
    for family,marked in human_names.items():
        for form,index in build({family:marked},implicit_single=True).items():human_slots.setdefault(form,set()).add(index)
    choices={};yo_choices={};evidence={};receipts=[]
    for source in explicit_stress:
        plain,index=explicit_mark(source['word'])
        if (source.get('plain')!=plain or source.get('stress')!=index
            or not isinstance(source.get('source_id'),str)or not source['source_id']
            or type(source.get('source_start'))is not int or type(source.get('source_end'))is not int
            or source['source_start']<0 or source['source_end']-source['source_start']!=len(source['word'])
            or not re.fullmatch('[0-9a-f]{64}',source.get('source_text_sha256',''))):
            raise ValueError('Explicit source-name inventory binding changed')
        key=_key(plain)
        matches=[family for family,group in families.items()if key in group['forms']]
        if plain[index]in 'ёЁ'and key in human_slots:
            if key not in human_forms or any(stress!=index for stress in human_slots[key]):
                raise ValueError('Explicit source ё conflicts with the canonical human name stress; pronunciation is held')
        if not matches:
            if key in human_forms:receipts.append({'rule':RULE,'status':'human_precedence','source':source,'families':[]})
            continue
        protected=key in human_forms or any(slot['protected']for family in matches for slot in families[family]['slots'])
        if protected:
            if plain[index]in 'ёЁ':
                human_indices=[human_forms[key]]if key in human_forms and human_forms[key]is not None else[]
                for family in matches:
                    for slot in families[family]['slots']:
                        if not slot['protected']:continue
                        word=slot['before']
                        if ACUTE in word or any(ch in word for ch in 'ёЁ'):
                            _,human_index=explicit_mark(word)
                        else:
                            vowels=[i for i,ch in enumerate(word)if ch in VOWELS]
                            if len(vowels)!=1:raise ValueError('Human name stress is not explicit')
                            human_index=vowels[0]
                        human_indices.append(aligned_stress_index(slot['printed'],plain,human_index))
                if any(human_index!=index for human_index in human_indices):
                    raise ValueError('Explicit source ё conflicts with the canonical human name stress; pronunciation is held')
            receipts.append({'rule':RULE,'status':'human_precedence','source':source,'families':matches});continue
        if len(matches)!=1:raise ValueError('Ambiguous explicit source-name cast family: '+plain)
        family=matches[0];base=families[family]['base'];mapped=aligned_stress_index(plain,base,index)
        if mapped is None:raise ValueError('Explicit source-name stress loses its retained base vowel: '+plain)
        choices.setdefault(family,set()).add(mapped);evidence.setdefault(family,[]).append(source)
        if plain[index]in 'ёЁ':yo_choices[family]=mapped
    replacements={}
    for family,indices in choices.items():
        if len(indices)!=1:raise ValueError('Conflicting explicit source-name cast stresses: '+families[family]['base'])
        index=next(iter(indices))
        for slot in families[family]['slots']:
            printed=slot['printed'];target=aligned_stress_index(families[family]['base'],printed,index)
            if target is None:raise ValueError('Explicit source-name cast spelling loses its stressed vowel')
            voiced=list(slot['before'].replace(ACUTE,''))
            # Hard-е decisions survive a stress-only change. A model ё on a
            # different vowel cannot coexist with the author's explicit stress.
            for i,ch in enumerate(voiced):
                if ch in 'ёЁ'and i!=target and printed[i]in 'еЕ':voiced[i]=printed[i]
            if family in yo_choices:
                if printed[target]not in 'еЕёЁ':raise ValueError('Explicit source ё does not bind the cast spelling')
                voiced[target]='Ё'if printed[target].isupper()else'ё'
            spoken=''.join(voiced)
            marked=spoken if spoken[target]in 'ёЁ'else spoken[:target+1]+ACUTE+spoken[target+1:]
            replacements.setdefault(slot['cid'],[]).append({**slot,'stressed':marked})
        receipts.append({'rule':RULE,'status':'source_override','family':family,'stress':index,'sources':evidence[family]})
    for cid,slots in replacements.items():
        text=effective[cid]
        for slot in sorted(slots,key=lambda row:row['start'],reverse=True):
            text=text[:slot['start']]+slot['stressed']+text[slot['end']:]
        validate_name_stress(names_rows[cid]['name'],text);effective[cid]=text
    return effective,receipts
