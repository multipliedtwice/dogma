"""Spoken respelling: the synthesis text may differ from the printed word only by е->э / е->ё.

Higgs reads a plain е after a consonant as soft (мь-е), so a hard reading
(Мозэс, тэк-купол) or a ё needs the other letter in the synthesis text. The
printed book is never changed. One validator decides what a respelling may be:
the same letters, except that selected е/Е may become э/Э or ё/Ё (case kept),
plus stress marks. ё is always stressed, so it is its own stress mark.
"""
from __future__ import annotations
import re
import unicodedata

ACUTE = '́'
RULE = 'listener-respelling-v1'
_ALLOWED = {'е': 'эё', 'Е': 'ЭЁ'}
_VOWELS = 'аеёиоуыэюяАЕЁИОУЫЭЮЯ'
_WORD = re.compile(r'[А-Яа-яЁё́]+')


def validate_respelling(printed: str, spoken: str) -> list[dict]:
    """[{'index','printed','spoken'}] for each substituted letter; ValueError on any other change."""
    plain = unicodedata.normalize('NFC', printed).replace(ACUTE, '')
    voiced = unicodedata.normalize('NFC', spoken).replace(ACUTE, '')
    if len(plain) != len(voiced):
        raise ValueError(f'Respelling changes the letters: {printed!r} -> {spoken!r}')
    substitutions = []
    for index, (a, b) in enumerate(zip(plain, voiced)):
        if a == b:
            continue
        if b not in _ALLOWED.get(a, ''):
            raise ValueError(f'Respelling may only replace е with э or ё: {printed!r} -> {spoken!r}')
        substitutions.append({'index': index, 'printed': a, 'spoken': b})
    return substitutions


def validate_respelled_stress(printed: str, spoken: str) -> list[dict]:
    """Validate the respelling and that the word carries exactly one stress (ё counts as one)."""
    substitutions = validate_respelling(printed, spoken)
    voiced = unicodedata.normalize('NFC', spoken)
    for match in re.finditer(ACUTE, voiced):
        before = voiced[:match.start()]
        if not before or before[-1] not in _VOWELS:
            raise ValueError('Stress mark must follow a Russian vowel')
    plain_index = lambda i: len(voiced[:i].replace(ACUTE, ''))
    yo = {s['index'] for s in substitutions if s['spoken'] in 'ёЁ'}
    for match in re.finditer(ACUTE, voiced):
        if plain_index(match.start()) - 1 in yo:
            raise ValueError('ё is always stressed; do not add a stress mark to it')
    if voiced.count(ACUTE) + len(yo) != 1:
        raise ValueError('Mark exactly one stressed vowel')
    return substitutions


def substitute(word: str, letters: tuple[tuple[int, str], ...] | list) -> str:
    """word with (index, 'э'|'ё') applied where the printed letter there is е/Е; others untouched."""
    chars = list(word)
    for index, letter in letters:
        if 0 <= index < len(chars) and chars[index] in 'еЕ':
            chars[index] = letter.upper() if chars[index] == 'Е' else letter.lower()
    return ''.join(chars)


def respelled_letters(spoken: str) -> tuple[tuple[int, str], ...]:
    """(index, letter) of every э/ё in a spoken form; applied only over a printed е at that index."""
    plain = spoken.replace(ACUTE, '')
    return tuple((i, ch.lower()) for i, ch in enumerate(plain) if ch in 'эЭёЁ')


def fold(word: str) -> str:
    return word.replace(ACUTE, '').casefold().replace('ё', 'е').replace('э', 'е')


def respell_text(text: str, names: dict, common=frozenset(), terms: dict | None = None,
                 implicit_single: bool = True, sources: dict | None = None) -> tuple[str, list[dict]]:
    """Apply term and name respellings (no stress marks) before the stress passes.

    Run on the synthesis input ahead of auto_pronunciation: afterwards the
    words differ from the printed ones only by the recorded substitutions.
    Returns (text, records); records[i] = {rule, kind, printed, spoken[, term_id][, source]}.
    `sources` ({folded printed base word: 'model'|'listener'}, from apply_respellings) tags each
    name record with who decided the respelling.
    """
    records: list[dict] = []
    if terms and terms.get('entries'):
        from .spoken_terms import apply_terms
        text, term_records = apply_terms(text, terms)
        records += term_records
    if names and any(ch in value for value in names.values() for ch in 'эЭёЁ'):
        from .name_stress import name_spans
        pieces = []; cursor = 0
        for start, end, marked in name_spans(text, names, common, implicit_single):
            word = text[start:end]
            spoken = marked.replace(ACUTE, '')
            if spoken == word.replace(ACUTE, '') or ACUTE in word:
                continue
            validate_respelling(word, spoken)
            pieces += [text[cursor:start], spoken]; cursor = end
            records.append({'rule': RULE, 'kind': 'name', 'printed': word, 'spoken': spoken,
                            **({'source': source_of(word, sources)} if source_of(word, sources) else {})})
        text = ''.join(pieces) + text[cursor:] if pieces else text
    return text, records


def disclosure(records: list[dict]) -> dict | None:
    """The synthesis-plan entry for a unit's respellings (None when there are none)."""
    if not records:
        return None
    return {'rule': RULE, 'records': [{k: r[k] for k in ('kind', 'printed', 'spoken', 'term_id', 'source') if k in r} for r in records]}


def apply_listener_names(names: dict, document: dict) -> dict:
    """Overlay a listener-respelling document on the name-stress rows of a generation spec.

    `names` is {character_id: {'name','stressed',...}}; the document is
    {'schema':1,'entries':[{'character_id','printed','stressed','provenance'}]}.
    Each entry must name the same character spelling and pass the respelling
    validator; the model's answer is kept as `model_stressed`.
    """
    if document.get('schema') != 1:
        raise ValueError('Unknown listener respelling document')
    result = dict(names)
    for entry in document.get('entries', []):
        row = result.get(entry['character_id'])
        if row is None:
            raise ValueError(f"Unknown character {entry['character_id']}")
        if row['name'] != entry['printed']:
            raise ValueError(f"Respelling does not belong to {row['name']!r}")
        from .auto_pronunciation import validate_name_stress
        validate_name_stress(row['name'], entry['stressed'])
        stressed = entry['stressed']
        result[entry['character_id']] = {**row, 'stressed': stressed, 'source': 'listener',
                                         'model_stressed': row.get('model_stressed', row['stressed']),
                                         'listener_respelling': {'printed': entry['printed'], 'spoken': entry['stressed'],
                                                                 'provenance': entry.get('provenance', '')}}
    return result


MODEL_RULE = 'hard-e-respelling-v1'


def _stem(word: str) -> str:
    return word[:-1] if word[-1] in 'аяоейь' and len(word) > 3 else word


def source_of(printed: str, sources: dict | None) -> str | None:
    """Who decided the respelling of an inflected printed name form: the longest matching base word wins."""
    if not sources:
        return None
    form = fold(printed)
    best = None
    for base, source in sources.items():
        stem = _stem(base)
        if form.startswith(stem) and len(form) - len(stem) <= 4 and (best is None or len(stem) > len(_stem(best[0]))):
            best = (base, source)
    return best[1] if best else None


def _changed_words(printed: str, spoken: str) -> list[str]:
    """Folded printed words of `printed` whose spoken form carries an е->э/ё respelling."""
    plain = [m[0] for m in _WORD.finditer(printed)]
    voiced = [m[0].replace(ACUTE, '') for m in _WORD.finditer(spoken)]
    return [fold(a) for a, b in zip(plain, voiced) if a != b]


def _model_document(document: dict | None) -> dict | None:
    if not document:
        return None
    if document.get('schema') != 1 or document.get('rule') != MODEL_RULE:
        raise ValueError('Unknown hard-е respelling document')
    return document


def apply_model_names(names: dict, document: dict | None) -> dict:
    """Overlay the automatic hard-е document on the name-stress rows; the model's own answer stays as `model_stressed`."""
    document = _model_document(document)
    result = dict(names)
    if not document:
        return result
    from .auto_pronunciation import validate_name_stress
    for entry in document.get('entries', []):
        row = result.get(entry['character_id'])
        if row is None or row['name'] != entry['printed']:
            raise ValueError(f"Hard-е respelling does not belong to {entry['character_id']}")
        validate_name_stress(row['name'], entry['stressed'])
        if any(s['spoken'] not in 'эЭ' for s in validate_respelling(row['name'], entry['stressed'])):
            raise ValueError('The automatic respelling may only make е hard (э)')
        if entry['stressed'].replace('э', 'е').replace('Э', 'Е') != row['stressed'].replace('э', 'е').replace('Э', 'Е'):
            raise ValueError(f"Hard-е respelling changes the stress of {entry['character_id']}")
        result[entry['character_id']] = {**row, 'stressed': entry['stressed'],
                                         'model_stressed': row.get('model_stressed', row['stressed']),
                                         'hard_e': {'printed': entry['printed'], 'spoken': entry['stressed'],
                                                    'provenance': entry.get('provenance', {})}}
    return result


def apply_model_nouns(proper_nouns: dict, document: dict | None) -> dict:
    """Overlay the automatic hard-е decisions on a built proper-noun / coinage lexicon (keys 'pn:Base' / 'cw:base')."""
    document = _model_document(document)
    result = dict(proper_nouns or {})
    if not document:
        return result
    for entry in document.get('proper_nouns', []):
        key = entry['key']
        if key not in result:
            continue   # this policy's lexicon does not contain the word
        unhard = lambda w: w.replace('э', 'е').replace('Э', 'Е')
        if unhard(entry['stressed']) != unhard(result[key]) or any(s['spoken'] not in 'эЭ' for s in validate_respelling(key[3:], entry['stressed'])):
            raise ValueError(f'Hard-е respelling does not belong to {key} (letters or stress differ)')
        result[key] = entry['stressed']
    return result


def apply_listener_nouns(proper_nouns: dict, document: dict | None) -> dict:
    """Listener respellings of proper nouns ({'proper_nouns': [{'key','stressed'}]}); the listener wins."""
    result = dict(proper_nouns or {})
    for entry in (document or {}).get('proper_nouns', []):
        if entry['key'] in result:
            validate_respelling(entry['key'][3:], entry['stressed'])
            result[entry['key']] = entry['stressed']
    return result


def apply_name_respellings(names: dict, model_doc: dict | None, listener_doc: dict | None) -> tuple[dict, dict]:
    """(names, sources): model respellings first, then the listener's, which win per character."""
    names = apply_model_names(names, model_doc)
    if listener_doc:
        names = apply_listener_names(names, listener_doc)
    sources = {}
    for row in names.values():
        key = 'listener_respelling' if row.get('listener_respelling') else 'hard_e'
        if row.get(key):
            for word in _changed_words(row['name'], row[key]['spoken']):
                sources[word] = 'listener' if key == 'listener_respelling' else 'model'
    return names, sources


def apply_noun_respellings(proper_nouns: dict, model_doc: dict | None, listener_doc: dict | None,
                           sources: dict | None = None) -> tuple[dict, dict]:
    sources = dict(sources or {})
    model = apply_model_nouns(proper_nouns, model_doc)
    final = apply_listener_nouns(model, listener_doc)
    for key in final:
        if final[key] != (proper_nouns or {}).get(key):
            for word in _changed_words(key[3:], final[key]):
                sources[word] = 'model' if final[key] == model[key] else 'listener'
    return final, sources


def apply_respellings(names: dict, proper_nouns: dict, model_doc: dict | None, listener_doc: dict | None) -> tuple[dict, dict, dict]:
    """(names, proper_nouns, sources): the automatic hard-е document applied first and the listener's over it."""
    names, sources = apply_name_respellings(names, model_doc, listener_doc)
    nouns, sources = apply_noun_respellings(proper_nouns, model_doc, listener_doc, sources)
    return names, nouns, sources


def names_sources(names: dict) -> dict:
    """{folded printed word: 'model'|'listener'} for the final name rows (a listener or series row wins over a model row)."""
    sources = {}
    for row in names.values():
        listener = row.get('source') in ('listener', 'series_listener') or row.get('listener_respelling')
        if listener or row.get('hard_e'):
            for word in _changed_words(row['name'], row['stressed']):
                sources[word] = 'listener' if listener else 'model'
    return sources
