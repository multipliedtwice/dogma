"""Durable human stress decisions shared by every generation and renderer.

The shared user journal is canonical. Library reports retain original local
receipts; series state and immutable render snapshots are derived projections.
"""
from __future__ import annotations

import difflib
import contextlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from .name_stress import ACUTE, name_spans
from .util import AppError, digest, file_digest, now

POLICY = 'listener-stress-v2'
LEGACY_POLICY = 'listener-stress-v1'


def shared_profile() -> Path:
    """One app-local human profile, explicitly injected into dev and production."""
    override=os.environ.get('RIMSKY_CORRECTION_PROFILE')
    if override:return Path(override).expanduser().resolve()
    if os.name=='nt':base=Path(os.environ.get('LOCALAPPDATA',Path.home()/'AppData/Local'))
    elif sys.platform=='darwin':base=Path.home()/'Library/Application Support'
    else:base=Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))
    return base/'app.rimskifikator.desktop/pronunciation/user-corrections.sqlite3'


@contextlib.contextmanager
def _journal(store):
    path=Path(getattr(store,'correction_profile',store.root/'user-stress-corrections.sqlite3'))
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=30)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('''CREATE TABLE IF NOT EXISTS corrections (
        report_id TEXT PRIMARY KEY,book_key TEXT NOT NULL,series_id TEXT NOT NULL,
        created TEXT NOT NULL,raw TEXT NOT NULL,family TEXT,occurrence TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS correction_resolutions (
        id INTEGER PRIMARY KEY,report_id TEXT NOT NULL,created TEXT NOT NULL,
        family TEXT,occurrence TEXT,resolver_sha256 TEXT NOT NULL)''')
    try:
        with db:yield db
    finally:db.close()


def _book_key(book):
    return book.get('source_hash') or digest({'title':book.get('title'),'author':book.get('author'),
        'chapters':[[(b['id'],b['text']) for b in c['blocks']] for c in book['chapters']]})


def _attested_name_lemma(book,parses,lemmas):
    """Disambiguate name paradigms only with exclusive source spellings.

    A form shared by two names cannot vote for either. Rival exclusive forms
    indicate two possible characters and keep the correction occurrence-bound.
    """
    fold=lambda word:word.replace(ACUTE,'').casefold().replace('ё','е')
    lexicons={lemma:set() for lemma in lemmas}
    for parse in parses:
        if parse.normal_form in lexicons:
            number=getattr(getattr(parse,'tag',None),'number',None)
            # A singular man's nominative must not be treated as a plural
            # woman's genitive merely because both dictionary lexemes have it.
            lexicons[parse.normal_form].update(fold(form.word) for form in parse.lexeme
                if not number or form.tag.number==number)
    exclusive={lemma:forms-set().union(*(other for key,other in lexicons.items() if key!=lemma))
        for lemma,forms in lexicons.items()}
    attested=set()
    for chapter in book['chapters']:
        for block in chapter['blocks']:
            passage=block.get('text','')
            for match in re.finditer(r'(?<![\ẃ\-‐‑])[А-ЯЁ][А-Яа-яЁё́]*(?![\ẃ\-‐‑])',passage):
                word=fold(match[0])
                if not any(word in forms for forms in exclusive.values()):continue
                if not _ordinary_context({'selected_text':match[0],'text_offset':match.start(),'passage_text':passage}):
                    attested.add(word)
    supported=[lemma for lemma,forms in exclusive.items() if forms&attested]
    return supported[0] if len(supported)==1 else None


def _proper_family(book,report):
    """Resolve a reviewed proper noun even when character analysis missed it."""
    from .series_characters import _base_match,normalized
    selected=report['selected_text'].replace(ACUTE,'')
    if not selected[:1].isupper():return None
    # A supplied capitalized source occurrence is the evidence. A known
    # sentence-initial ordinary word must not become a global homograph rule.
    offset=report.get('text_offset')
    passage=report.get('passage_text','')
    initial=type(offset) is int and not re.search(r'[А-Яа-яЁё]',re.split(r'[.!?…]',passage[:offset])[-1])
    bases=[]
    try:
        from .proper_noun_stress import _morph
        parses=_morph().parse(selected.casefold())
        proper_parses=[p for p in parses if {'Name','Surn','Patr','Geox'} & set(p.tag.grammemes)]
        names=[p.normal_form for p in proper_parses]
        if initial and _morph().word_is_known(selected.casefold()) and not names:return None
        lemmas=list(dict.fromkeys(names or [p.normal_form for p in parses]))
        if len(lemmas)!=1:
            # Dictionary scores are not character identity. An unambiguous
            # capitalized case form elsewhere in this exact book can resolve
            # a nominative/oblique or gender collision without guessing.
            lemma=_attested_name_lemma(book,proper_parses,lemmas) if names else None
            if lemma is None:return None
            lemmas=[lemma]
        if not names and (not parses or any(p.tag.POS!='NOUN' for p in parses)):return None
        bases=[word[:1].upper()+word[1:] for word in dict.fromkeys(lemmas)]
    except ImportError:
        return None
    matches=[]
    for base in bases:
        intended=report['intended_stress']
        stress_index=intended.index(ACUTE)-1 if ACUTE in intended else next((i for i,c in enumerate(intended) if c in 'ёЁ'),-1)
        shared=next((i for i,(a,b) in enumerate(zip(selected.casefold(),base.casefold())) if a!=b),min(len(selected),len(base)))
        if stress_index<0 or stress_index>=shared:continue
        found=_base_match({'canonical_name':base},selected,report['intended_stress'])
        if found:matches.append(found)
    if not matches:return None
    shortest=min(len(base) for base,_ in matches)
    matches=list(dict.fromkeys(item for item in matches if len(item[0])==shortest))
    return {matches[0][0]:matches[0][1]} if len(matches)==1 else None


def _ordinary_context(report):
    """A name-shaped ordinary word is still an occurrence, never a family rule."""
    selected=report.get('selected_text','').replace(ACUTE,'')
    if not selected[:1].isupper():return True
    offset=report.get('text_offset');passage=report.get('passage_text','')
    initial=type(offset) is int and not re.search(r'[А-Яа-яЁё]',re.split(r'[.!?…]',passage[:offset])[-1])
    if not initial:return False
    try:
        from .proper_noun_stress import _morph
        morph=_morph()
        if not morph.word_is_known(selected.casefold()):return False
        parses=morph.parse(selected.casefold())
        return any(not ({'Name','Surn','Patr','Geox'} & set(p.tag.grammemes)) for p in parses)
    except ImportError:return False


def _legacy_family(store,book,report):
    """Resolve retained legacy evidence without inventing a source occurrence."""
    from .series_characters import _base_match,identity
    evidence=report.get('legacy_evidence') or {}
    fields=('book_id','generation_id','selected_text','intended_stress','created')
    valid=(evidence.get('source')=='listener' and evidence.get('report_id')==report.get('original_report_id')
        and all(evidence.get(key)==report.get(key) and evidence.get(key) is not None for key in fields)
        and report.get('legacy_series_id')==identity(store,book)['id'])
    with _journal(store) as db:
        original=db.execute('SELECT 1 FROM corrections WHERE report_id=?',(report.get('original_report_id'),)).fetchone()
    if original or not valid:return None,True
    selected=report.get('selected_text','').replace(ACUTE,'')
    if _ordinary_context(report):return None,True
    try:
        from .proper_noun_stress import _morph
        morph=_morph()
        # Old evidence without its passage cannot distinguish an ordinary
        # homograph from a cast name. Preserve it pending instead of guessing.
        if not report.get('passage_text') and morph.word_is_known(selected.casefold()) and any(
            not ({'Name','Surn','Patr','Geox'}&set(parse.tag.grammemes)) for parse in morph.parse(selected.casefold())):
            return None,True
    except ImportError:return None,False
    try:
        from .respelling import validate_respelled_stress
        validate_respelled_stress(selected,report['intended_stress'])
    except ValueError:return None,True
    found=_base_match(report.get('legacy_character') or {},selected,report['intended_stress'])
    return ({found[0]:found[1]} if found else None),not bool(found)


def _import_legacy_evidence(store,book,records):
    """Migrate only original retained listener evidence, never the latest value."""
    from .series_characters import identity
    series_id=identity(store,book)['id']
    with _journal(store) as db:
        canonical={row[0] for row in db.execute('SELECT report_id FROM corrections')}
    for row in records:
        state=row.get('state') or {}
        if state.get('pronunciation_source')!='listener':continue
        for evidence in state.get('pronunciation_evidence') or []:
            identifier=evidence.get('report_id')
            if not identifier or identifier in canonical:continue
            migration_id='legacy-family:'+digest([series_id,identifier])
            if migration_id in canonical:continue
            raw={key:evidence[key] for key in ('book_id','generation_id','selected_text','intended_stress','created') if key in evidence}
            if len(raw)!=5:continue
            # The namespace marks a migration receipt, not a fabricated user
            # report. The actual old report ID and evidence remain unchanged.
            raw.update(id=migration_id,kind='legacy_family',
                original_report_id=identifier,legacy_evidence=evidence,legacy_series_id=series_id,
                legacy_character={'canonical_name':row.get('canonical_name',''),'aliases':row.get('aliases') or []})
            record(store,book,raw)


def record_cast_review(store,book,request):
    """Persist the actual explicit cast action without inventing a heard slot."""
    from .auto_pronunciation import validate_name_stress
    from .name_stress import VOWELS
    if (request.get('operation')!='cast_name_stress' or request.get('book_id')!=book['id']
        or not all(isinstance(request.get(key),str) and request[key] for key in ('id','created','generation_id','character_id'))):
        raise ValueError('Cast stress receipt does not bind its original human action')
    validate_name_stress(request['name'],request['stressed'])
    printed=re.findall(r'[А-Яа-яЁё]+',request['name'])
    marked=re.findall(r'[А-Яа-яЁё́]+',request['stressed'])
    if len(printed)!=len(marked):raise ValueError('Cast stress review changed name components')
    for index,(word,intended) in enumerate(zip(printed,marked)):
        if ACUTE not in intended and not any(letter in 'ёЁ' for letter in intended):
            vowels=[i for i,letter in enumerate(intended) if letter in VOWELS]
            if len(vowels)!=1:continue
            position=vowels[0]+1;intended=intended[:position]+ACUTE+intended[position:]
        raw={'id':'cast-review:'+digest([request['id'],index]),'book_id':book['id'],
            'generation_id':request['generation_id'],'segment_id':'','chapter_id':'','source_id':'',
            'selected_text':word,'intended_stress':intended,'created':request['created'],'kind':'stress',
            'text_offset':None,'audio_offset':None,'audio_hash':None,'passage_text':'','spoken_text':'',
            'comment':'','synthesis_snapshot':None,'feedback_source':'cast_name_stress','review_request':request}
        record(store,book,raw,{'status':'updated','canonical_name':word})


def record(store,book,report,character_state=None):
    """Append one raw human receipt; repeated backfill cannot change its meaning."""
    from .series_characters import identity
    eligible=report.get('kind')=='stress' and bool(report.get('intended_stress'))
    if eligible:
        from .respelling import validate_respelled_stress
        try:validate_respelled_stress(report.get('selected_text',''),report['intended_stress'])
        except ValueError:eligible=False
    legacy=report.get('kind')=='legacy_family'
    ordinary=eligible and _ordinary_context(report)
    family=None
    if legacy:family,ordinary=_legacy_family(store,book,report)
    if eligible and not legacy and not ordinary and not character_state:
        # A different library may lack the original cast identity. Preserve its
        # validated family base and rebind this report's own intended stress;
        # dictionary morphology must not silently change character identity.
        with _journal(store) as db:
            prior_binding=db.execute('SELECT family FROM corrections WHERE report_id=?',(report['id'],)).fetchone()
        if prior_binding and prior_binding['family']:
            from .series_characters import _base_match
            matches=[_base_match({'canonical_name':value.replace(ACUTE,'')},report['selected_text'],report['intended_stress'])
                for value in json.loads(prior_binding['family']).values()]
            matches=list(dict.fromkeys(match for match in matches if match))
            if len(matches)==1:family={matches[0][0]:matches[0][1]}
    if eligible and not legacy and not ordinary and (character_state or {}).get('status')=='updated':
        from .series_characters import _base_match
        candidates=[character_state.get('canonical_name',''),*(character_state.get('pronunciations') or {})]
        matches=[_base_match({'canonical_name':base},report['selected_text'],report['intended_stress'])
                 for base in candidates if base]
        matches=list(dict.fromkeys(row for row in matches if row))
        if len(matches)==1:family={matches[0][0]:matches[0][1]}
    if eligible and not legacy and not ordinary and not family:family=_proper_family(book,report)
    offset=report.get('text_offset');word=report.get('selected_text','');passage=report.get('passage_text','')
    occurrence=None
    if eligible and not legacy and not family and type(offset) is int and passage[offset:offset+len(word)]==word:
        occurrence={key:report[key] for key in ('source_id','selected_text','intended_stress')}
        occurrence.update(text_offset=offset,passage_sha256=digest(passage),report_id=report['id'])
    raw={k:v for k,v in report.items() if k not in ('character_state','retake_queue')}
    with _journal(store) as db:
        prior=db.execute('SELECT * FROM corrections WHERE report_id=?',(report['id'],)).fetchone()
        encoded=json.dumps(raw,ensure_ascii=False,sort_keys=True)
        if prior and json.loads(prior['raw'])!=raw:
            raise ValueError('A durable listener report changed during backfill')
        encoded_family=json.dumps(family,ensure_ascii=False) if family else None
        encoded_occurrence=json.dumps(occurrence,ensure_ascii=False) if occurrence else None
        if prior and prior['family'] and not family and not ordinary:
            # An unavailable resolver cannot erase an already source-validated
            # human family. Only a newer explicit report supersedes it.
            family=json.loads(prior['family']);encoded_family=prior['family'];encoded_occurrence=None
        db.execute('INSERT OR IGNORE INTO corrections VALUES(?,?,?,?,?,?,?)',
            (report['id'],'legacy-volume:'+report['book_id'] if legacy else _book_key(book),identity(store,book)['id'],report['created'],encoded,
             encoded_family,encoded_occurrence))
        if prior is None or (prior['family'],prior['occurrence'])!=(encoded_family,encoded_occurrence):
            if prior and not db.execute('SELECT 1 FROM correction_resolutions WHERE report_id=?',(report['id'],)).fetchone():
                db.execute('INSERT INTO correction_resolutions(report_id,created,family,occurrence,resolver_sha256) VALUES(?,?,?,?,?)',
                    (report['id'],now(),prior['family'],prior['occurrence'],'historical-resolution'))
            db.execute('INSERT INTO correction_resolutions(report_id,created,family,occurrence,resolver_sha256) VALUES(?,?,?,?,?)',
                (report['id'],now(),encoded_family,encoded_occurrence,file_digest(Path(__file__))))
            db.execute('UPDATE corrections SET family=?,occurrence=? WHERE report_id=?',
                (encoded_family,encoded_occurrence,report['id']))
    if family:
        return {'status':'updated','canonical_name':next(iter(family)),
                'pronunciations':family,'source':'listener_profile'}
    return {'status':'source_bound','family_status':'unresolved' if report['selected_text'][:1].isupper() else 'not_proper_noun'} if eligible else None


def notes(store,book):
    """Private audit view: original receipts plus explicit derived binding."""
    from .series_characters import identity
    with _journal(store) as db:
        rows=db.execute('SELECT * FROM corrections WHERE book_key=? OR series_id=? ORDER BY created,rowid',
            (_book_key(book),identity(store,book)['id'])).fetchall()
    return [{'report':json.loads(row['raw']),
             'family':json.loads(row['family']) if row['family'] else None,
             'occurrence':json.loads(row['occurrence']) if row['occurrence'] else None,
             'binding_status':'family' if row['family'] else 'occurrence' if row['occurrence'] else 'raw_note',
             'book_key':row['book_key']} for row in rows]


def snapshot(store, book: dict, cast: list[dict] | None = None) -> dict:
    """Reconcile old reports, then freeze listener names and exact occurrences."""
    from .series_characters import record_listener_stress, series_state, sync_generation
    if cast:
        sync_generation(store, book, {'cast': cast, 'analysis': {}})
    _, records = series_state(store, book)
    seen = {e.get('report_id') for row in records
            for e in (row.get('state') or {}).get('pronunciation_evidence', [])}
    reports = list(reversed(store.stress_reports(book['id'])))
    for row in store.rows('SELECT id,analysis FROM generations WHERE book_id=?',(book['id'],)):
        pronunciation=json.loads(row['analysis']).get('pronunciation') or {}
        for name in (pronunciation.get('names') or {}).values():
            if name.get('source') in ('listener','series_listener') and name.get('listener_review') and name['listener_review'].get('generation_id')==row['id']:
                record_cast_review(store,book,name['listener_review'])
    promoted_reports = set(seen)
    for report in reports:
        if report.get('kind') != 'stress' or not report.get('intended_stress') or report['id'] in seen:
            continue
        # Historical generations may have been deleted. The source-bound note
        # still survives and is used below, even without a character identity.
        try:
            result = record_listener_stress(store, report)
            if result.get('status') == 'updated':
                promoted_reports.add(report['id'])
        except (AppError, KeyError, ValueError):
            continue
    _, records = series_state(store, book)
    names, promoted = {}, promoted_reports
    for row in records:
        state = row.get('state') or {}
        if state.get('pronunciation_source') != 'listener':
            continue
        # Series state is only an identity/resolution projection. A stale
        # projection must never introduce unbacked human family authority.
        promoted.update(e.get('report_id') for e in state.get('pronunciation_evidence', []))
    occurrences = {}
    for report in reports:
        if report.get('kind') != 'stress' or not report.get('intended_stress') or report['id'] in promoted:
            continue
        offset = report.get('text_offset')
        passage, word = report.get('passage_text', ''), report.get('selected_text', '')
        if type(offset) is not int or passage[offset:offset + len(word)] != word:
            # Never guess which repeated homograph a positionless old note meant.
            continue
        row = {key: report[key] for key in ('source_id', 'selected_text', 'intended_stress')}
        row.update(text_offset=offset, passage_sha256=digest(passage), report_id=report['id'])
        occurrences[(row['source_id'], row['passage_sha256'], offset, word)] = row
    # Local raw receipts are backfilled exactly once into the one shared human
    # profile. The journal is authoritative; series state is only a projection.
    for report in reports:
        state=None
        for row in records:
            character=row.get('state') or {}
            if any(e.get('report_id')==report['id'] for e in character.get('pronunciation_evidence',[])):
                state={'status':'updated','canonical_name':row.get('canonical_name',''),
                       'pronunciations':character.get('pronunciations')}
                break
        record(store,book,report,state)
    _import_legacy_evidence(store,book,records)
    from .series_characters import identity
    with _journal(store) as db:
        journal=db.execute('SELECT rowid AS journal_order,* FROM corrections WHERE book_key=? OR series_id=? ORDER BY created,rowid',
            (_book_key(book),identity(store,book)['id'])).fetchall()
    local_ids={report['id'] for report in reports}
    for row in journal:
        if row['report_id'] not in local_ids:
            # Another library may own the original receipt. Recompute only its
            # derived binding from canonical raw evidence, retaining raw bytes,
            # original identity/order and the versioned resolution history.
            record(store,book,json.loads(row['raw']))
    with _journal(store) as db:
        journal=db.execute('SELECT rowid AS journal_order,* FROM corrections WHERE book_key=? OR series_id=? ORDER BY created,rowid',
            (_book_key(book),identity(store,book)['id'])).fetchall()
    occurrences={}
    family_evidence={}
    for row in journal:
        if row['family']:
            family=json.loads(row['family']);names.update(family)
            raw=json.loads(row['raw'])
            for base,stressed in family.items():
                family_evidence[base]={'report_id':row['report_id'],'created':row['created'],
                    'journal_order':row['journal_order'],
                    'source_id':raw.get('source_id'),'text_offset':raw.get('text_offset'),
                    'passage_sha256':digest(raw.get('passage_text','')),'stressed':stressed}
        if row['book_key']==_book_key(book) and row['occurrence']:
            occurrence=json.loads(row['occurrence'])
            occurrence.update(created=row['created'],journal_order=row['journal_order'])
            occurrences[(occurrence['source_id'],occurrence['passage_sha256'],occurrence['text_offset'],occurrence['selected_text'])]=occurrence
    from .name_stress import build
    forms={base:build({base:stressed},implicit_single=True) for base,stressed in names.items()}
    superseded={}
    raw_by_id={row['report_id']:json.loads(row['raw']) for row in journal}
    for key,occurrence in list(occurrences.items()):
        word=occurrence['selected_text'].replace(ACUTE,'')
        if not word[:1].isupper() or _ordinary_context(raw_by_id[occurrence['report_id']]):continue
        family_key=word.casefold().replace('ё','е')
        matches=[base for base,lexicon in forms.items() if family_key in lexicon and base in family_evidence]
        if len(matches)!=1:continue
        authority=family_evidence[matches[0]]
        earlier=(occurrence['created'],occurrence['journal_order'])
        later=(authority['created'],authority['journal_order'])
        if later>earlier:
            superseded[occurrence['report_id']]=authority['report_id']
            occurrences.pop(key)
    result = {'policy': POLICY, 'book_id': book['id'], 'names': names,
              'occurrences': list(occurrences.values()),'family_evidence':family_evidence,
              'note_statuses':[{'report_id':row['report_id'],
                    'status':'superseded' if row['report_id'] in superseded else 'family' if row['family'] else 'occurrence' if row['occurrence'] else 'pending',
                    **({'superseded_by_report_id':superseded[row['report_id']],
                        'reason':'A later explicit correction to the same proper-noun family is authoritative.'}
                       if row['report_id'] in superseded else {}),
                    **({'reason':'No structured, unambiguous stress binding; original note retained.'}
                       if not row['family'] and not row['occurrence'] else {})} for row in journal]}
    return {**result, 'sha256': digest(result)}


def _plain_map(text):
    plain, starts, ends = [], [], []
    for index, char in enumerate(text):
        if char == ACUTE:
            if ends:
                ends[-1] = index + 1
            continue
        plain.append(char)
        starts.append(index)
        ends.append(index + 1)
    return ''.join(plain), starts, ends


def _fold(text):
    return text.replace('ё', 'е').replace('Ё', 'Е').replace('э', 'е').replace('Э', 'Е')


def apply(text: str, frozen: dict | None, *, source_id=None, source_text=None,
          source_start=0, source_end=None) -> tuple[str, dict]:
    """Overlay human decisions after automatic marks; printed text is untouched.

    Source offsets are coordinates in the original block. Non-name corrections
    never spread to another occurrence or to a changed edition of the block.
    """
    if not frozen:
        return text, {'policy': POLICY, 'edits': []}
    if frozen.get('policy') not in (POLICY,LEGACY_POLICY):
        raise ValueError('Unknown listener stress policy')
    if frozen.get('sha256') and frozen['sha256']!=digest({k:v for k,v in frozen.items() if k!='sha256'}):
        raise ValueError('Listener stress snapshot checksum mismatch')
    plain, starts, ends = _plain_map(text)
    edits = {}
    applications=[]
    for start, end, marked in name_spans(plain, frozen.get('names') or {}, implicit_single=True):
        edits[(start, end)] = {'start': start, 'end': end, 'intended_stress': marked,
                               'source': 'listener_character_family'}
        from .name_stress import build
        key=plain[start:end].casefold().replace('ё','е')
        witnesses=[{'family':base,**evidence} for base,evidence in frozen.get('family_evidence',{}).items()
            if key in build({base:frozen['names'][base]},implicit_single=True)]
        applications.append({**edits[(start,end)],'family_evidence':witnesses})
    relevant = [row for row in frozen.get('occurrences', []) if row['source_id'] == source_id]
    if relevant and source_text is None:
        raise ValueError('Listener occurrence requires its exact source passage')
    if source_text is not None:
        source_end = len(source_text) if source_end is None else source_end
        if not 0 <= source_start <= source_end <= len(source_text):
            raise ValueError('Invalid listener source range')
        fragment = source_text[source_start:source_end]
        matcher = None
        for row in relevant:
            if row['passage_sha256'] != digest(source_text):
                continue
            start = row['text_offset'] - source_start
            end = start + len(row['selected_text'])
            if end <= 0 or start >= len(fragment):
                continue
            if start < 0 or end > len(fragment) or fragment[start:end] != row['selected_text']:
                raise ValueError('Listener occurrence crosses or mismatches a synthesis part')
            if _fold(fragment) == _fold(plain):
                target = (start, end)
            else:
                # Number/symbol expansion may shift the word. Only a contiguous
                # exact matching block can establish its new coordinates.
                if matcher is None:
                    matcher = difflib.SequenceMatcher(None, _fold(fragment), _fold(plain), autojunk=False)
                matches = [(b.b + start - b.a, b.b + end - b.a)
                           for b in matcher.get_matching_blocks() if b.a <= start and end <= b.a + b.size]
                if len(matches) != 1:
                    raise ValueError('Cannot bind listener stress to transformed spoken text')
                target = matches[0]
            edits[target] = {'start': target[0], 'end': target[1],
                             'intended_stress': row['intended_stress'],
                             'source': 'listener_source_occurrence', 'report_id': row['report_id']}
            applications.append(edits[target])
    changed = []
    output = text
    for (start, end), row in sorted(edits.items(), reverse=True):
        original = text[starts[start]:ends[end - 1]]
        if original == row['intended_stress']:
            continue
        output = output[:starts[start]] + row['intended_stress'] + output[ends[end - 1]:]
        changed.append({**row, 'before': original})
    evidence = {'policy': frozen['policy'], 'edits': list(reversed(changed)),'applications':applications}
    return output, {**evidence, 'sha256': digest(evidence)}
