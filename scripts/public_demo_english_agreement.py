"""Disclosed English source/cast name slots and exact phoneme orthography.

No audio is inferred from text. Two independent audio-only recognizers still
have to pass the caller's complete-source gate. Name tolerance cannot certify
acoustic stress. Ordinary word substitutions and missing words stay held.
"""
from __future__ import annotations
from functools import lru_cache
import copy
import importlib.metadata
import json
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime'))
from rimsky.util import file_digest
from rimsky.speech_quality import word_tokens

ADAPTER = 'english-source-cast-name-slot-v2'
PHONE_RULE = 'english-exact-stressed-phonemes-v1'
BOUNDARY_RULE = 'english-exact-letter-phoneme-boundary-v1'
VOICE = 'en-us'
VERSION = '1.0.4'
DLLS = {'espeak-ng-x64.dll':'b392514e0a43465cabde56d4d534414243200d616ed78907d837ea7afa5f54b8',
        'espeak-ng-x86.dll':'9a5f2a6d4fefe9eefaf7b8c5fdb53937388448269444f3d281a72ed08e05fb99'}
OPTIONS = dict(phoneme_separator='_', word_separator=' ', no_stress=False,
               keep_clause_breakers=False, keep_language_flags=False)
TITLES = frozenset('madame madam mister missus mr mrs mme narrator'.split())
PRONOUNS = frozenset('her he she they it we i you him them us my your his its our their me'.split())
VOWELS = frozenset('aeiouyæɐɑɒɛɜəɞɘɤɨɪɔɵœøʉʊʌʏɚɝ')
_PROVENANCE_CACHE = {}


def _package_fingerprint(folder):
    rows = []
    for path in sorted(folder.rglob('*')):
        if '__pycache__' in path.parts:
            continue
        details = path.stat()
        if stat.S_ISREG(details.st_mode):
            rows.append((path.relative_to(folder).as_posix(), details.st_size, details.st_mtime_ns,
                         details.st_ino, details.st_dev, details.st_ctime_ns))
    return tuple(rows)


def engine_provenance():
    import espeak_phonemizer as module
    from render_public_audiobook_demo import sha
    version = importlib.metadata.version('espeak-phonemizer-windows')
    if version != VERSION:
        raise ValueError('Unapproved English phonemizer package version')
    module_path = Path(module.__file__).resolve()
    folder = module_path.parent
    fingerprint = _package_fingerprint(folder)
    key = (str(module_path), version, VOICE, json.dumps(OPTIONS, sort_keys=True), fingerprint)
    cached = _PROVENANCE_CACHE.get(key)
    if cached is not None:
        return copy.deepcopy(cached)
    files = {row[0]:file_digest(folder/row[0]) for row in fingerprint}
    if _package_fingerprint(folder) != fingerprint:
        raise ValueError('English phonemizer package changed during provenance hashing')
    dlls = {name:files.get('espeak-ng/'+name) for name in DLLS}
    if dlls != DLLS:
        raise ValueError('English phonemizer DLL pin mismatch')
    result = {'package':'espeak-phonemizer-windows', 'version':VERSION, 'dlls':dlls,
            'package_files_sha256':sha(files), 'file_count':len(files),
            'module_sha256':files[module_path.relative_to(folder).as_posix()], 'voice':VOICE, 'options':copy.deepcopy(OPTIONS)}
    _PROVENANCE_CACHE.clear()
    _PROVENANCE_CACHE[key] = copy.deepcopy(result)
    return result


@lru_cache(maxsize=1)
def _engine():
    engine_provenance()
    from espeak_phonemizer import Phonemizer
    return Phonemizer(default_voice=VOICE)


@lru_cache(maxsize=2048)
def phones(text):
    return _engine().phonemize(text, **OPTIONS)


def segments(value):
    return re.sub(r'[\s_ˈˌ]', '', value)


def syllables(value):
    return sum(bool(re.sub('[ˈˌ]', '', token)) and re.sub('[ˈˌ]', '', token)[0] in VOWELS
               for token in value.replace(' ', '_').split('_'))


def build_policy(book, assignment, book_sha256):
    from render_public_audiobook_demo import sha
    cast = set()
    for cid, character in assignment['cast'].items():
        if cid == 'narrator':
            continue
        cast.update(re.findall('[a-z]+', character['name'].casefold()))
        if re.fullmatch('[A-Za-z]+', cid):
            cast.add(cid.casefold())
    found = {}
    for chapter in book['chapters']:
        for block in chapter['blocks']:
            initial = True
            for match in re.finditer(r'Mrs\.|Mr\.|Mme\.|[A-Za-z]+|[.!?…]', block.get('text','')):
                token = match[0]
                if token in ('Mr.', 'Mrs.', 'Mme.'):
                    # These source-recorded title expansions have a full stop
                    # inside an abbreviation, not a new sentence boundary.
                    initial = False
                    continue
                if token in '.!?…':
                    initial = True
                    continue
                key = token.casefold()
                if not initial and token[:1].isupper() and key in cast and key not in TITLES:
                    found.setdefault(key, []).append({'source_id':block['id'], 'start':match.start(),
                                                      'end':match.end(), 'source':token})
                initial = False
    return {'rule':'proper-noun-slot-v1', 'adapter':ADAPTER, 'book_sha256':book_sha256,
            'cast_sha256':sha(assignment['cast']), 'allowed_names':sorted(found), 'source_evidence':found,
            'phonemizer':engine_provenance(), 'scope':'Exact mid-sentence source occurrences intersecting frozen cast; no sentence-initial exception or lowercase invention; not acoustic stress certification'}


def source_positions(unit):
    """Map spoken word indices back to exact printed source spans."""
    spans, cursor = [], 0
    changes = unit['spoken_conversion']['changes']
    def untouched(start, end):
        for match in re.finditer(r'[^\W_]+', unit['text'][start:end]):
            spans.append({'source_start':unit.get('start',0)+start+match.start(),
                          'source_end':unit.get('start',0)+start+match.end(), 'source':match[0]})
    for change in changes:
        untouched(cursor, change['start'])
        for _ in word_tokens(change['spoken']):
            spans.append({'source_start':unit.get('start',0)+change['start'],
                          'source_end':unit.get('start',0)+change['end'], 'source':change['source'],
                          'conversion_rule':change['rule']})
        cursor = change['end']
    untouched(cursor, len(unit['text']))
    if len(spans) != len(word_tokens(unit['spoken'])):
        raise ValueError('English source positions no longer match spoken words')
    return spans


def options(unit, wanted, actual, i, j, policy=None):
    """Non-exact DP edges: (source end, heard end, disclosed change)."""
    if i >= len(wanted) or j >= len(actual) or wanted[i] == actual[j]:
        return []
    source_map = source_positions(unit)
    context = {'source_word_index':i, 'heard_word_index':j, 'source_id':unit.get('source_id'),
               **source_map[i]}
    result = []
    a, b = wanted[i], actual[j]
    pa, pb = phones(a), phones(b)
    if a not in PRONOUNS and b not in PRONOUNS and pa and pa == pb:
        result.append((i+1,j+1,{**context, 'rule':PHONE_RULE, 'source_tokens':[a], 'heard_tokens':[b],
                                'source_phones':pa, 'heard_phones':pb, 'kind':'orthographic_equivalence'}))
    spoken_slots = list(re.finditer(r'[^\W_]+', unit['spoken']))
    occurrence = next((item for item in (policy or {}).get('source_evidence', {}).get(a, [])
                       if item['source_id'] == unit.get('source_id')
                       and item['start'] == context['source_start'] and item['end'] == context['source_end']
                       and item['source'] == context['source']), None)
    if (policy and occurrence and a in policy['allowed_names'] and spoken_slots[i][0][:1].isupper()
            and a not in TITLES and a not in PRONOUNS and b not in PRONOUNS and b not in TITLES
            and b.isalpha()):
        sa, sb = syllables(pa), syllables(pb)
        if sa and sb and abs(sa-sb) <= 1:
            result.append((i+1,j+1,{**context, 'rule':'proper-noun-slot-v1', 'adapter':ADAPTER,
                                    'source_tokens':[a], 'heard_tokens':[b], 'source_phones':pa,'heard_phones':pb,
                                    'source_syllables':sa,'heard_syllables':sb,
                                    'source_occurrence':occurrence,
                                    'kind':'proper_noun_not_spelling_checked',
                                    'user_instructions':['Proper nouns are not spelling-checked; non-empty heard slot within one syllable; every other word must match.'],
                                    'acoustic_stress_certified':False}))
    for source_count, heard_count in [(1,2),(1,3),(2,1),(3,1)]:
        source, heard = wanted[i:i+source_count], actual[j:j+heard_count]
        if len(source) != source_count or len(heard) != heard_count or not all(w.isalpha() for w in source+heard):
            continue
        if ''.join(source) != ''.join(heard):
            continue
        ps, ph = phones(' '.join(source)), phones(' '.join(heard))
        if ps and segments(ps) == segments(ph):
            result.append((i+source_count,j+heard_count,{**context, 'rule':BOUNDARY_RULE,
                            'source_end':source_map[i+source_count-1]['source_end'],
                            'source_tokens':source,'heard_tokens':heard,'source_phones':ps,'heard_phones':ph,
                            'stress_marks_differ':ps!=ph,'kind':'source_identical_letter_word_boundary',
                            'acoustic_stress_certified':False}))
    return result
