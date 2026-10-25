"""Shared, source-bound human preparation feedback for external demo books.

Receipts imported from private preparation exports remain in the canonical
local journal. They carry no invented heard asset or listening approval.
"""
from __future__ import annotations

from contextlib import contextmanager
import difflib
import json
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime')]
from rimsky import listener_stress
from rimsky.store import Store
from rimsky.util import digest, file_digest

POLICY = 'external-book-listener-preparation-v1'
TOKEN = re.compile(r'(?:[^\W\d_]|\u0301)+', re.UNICODE)
ACUTE = '\u0301'


class SourceStore(Store):
    """A source book need not be copied into a user's listening library."""
    def stress_reports(self, book_id):
        return super().stress_reports(book_id) if self.rows('SELECT id FROM books WHERE id=?', (book_id,)) else []


@contextmanager
def source_store(root, correction_profile=None):
    store = SourceStore(root, correction_profile=correction_profile or listener_stress.shared_profile())
    try:
        yield store
    finally:
        store.close()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def _plain(text):
    return text.replace(ACUTE, '')


def _fold(text):
    return _plain(text).replace('ё', 'е').replace('Ё', 'Е').replace('э', 'е').replace('Э', 'Е')


def _blocks(book):
    return {block['id']: block['text'] for chapter in book['chapters'] for block in chapter['blocks']}


def _unit_block(unit, blocks):
    block = blocks[unit['source_id']]
    if not 0 <= unit['start'] < unit['end'] <= len(block) or block[unit['start']:unit['end']] != unit['text']:
        raise ValueError('Human preparation unit does not match its exact source slice')
    return block


def _raw(book, unit, block, selected, offset, intended, origin, index):
    from rimsky.respelling import validate_respelled_stress
    try:
        validate_respelled_stress(selected, intended)
        kind = 'stress'
    except ValueError:
        kind = 'pronunciation'
    identifier = 'preparation:' + digest([origin['sha256'], unit['id'], index])
    return {'id': identifier, 'book_id': book['id'], 'generation_id': '',
        'segment_id': unit['id'], 'chapter_id': unit['chapter_id'], 'source_id': unit['source_id'],
        'selected_text': selected, 'text_offset': offset, 'audio_offset': None, 'audio_hash': None,
        'passage_text': block, 'spoken_text': '', 'synthesis_snapshot': None,
        'created': origin['created'], 'kind': kind, 'intended_stress': intended if kind == 'stress' else None,
        'comment': origin['instruction'], 'feedback_source': 'imported_human_preparation',
        'preparation_origin': origin}


def _save(store, book, raw):
    with listener_stress._journal(store) as db:
        prior = db.execute('SELECT raw FROM corrections WHERE report_id=?', (raw['id'],)).fetchone()
    if prior:
        original = json.loads(prior['raw'])
        fields = ('book_id', 'source_id', 'selected_text', 'text_offset', 'passage_text',
            'kind', 'intended_stress', 'preparation_token_edit')
        if any(original.get(key) != raw.get(key) for key in fields):
            raise ValueError('An imported human instruction changed its original source binding')
        return  # Reimport/moving an export never rewrites the original receipt.
    listener_stress.record(store, book, raw, _project=False)


def import_overrides(store, book, assignment, path):
    """Extract only the actual changed tokens from a reviewed private export."""
    path = Path(path)
    document = _read(path)
    if document.get('schema') != 1 or document.get('human_approval') is not False:
        raise ValueError('Preparation export must explicitly carry no audio approval')
    units = {unit['id']: unit for unit in assignment['units']}
    blocks = _blocks(book)
    imported = []
    for change in document['changes']:
        unit = units[change['id']]
        block = _unit_block(unit, blocks)
        if change['source_text'] != unit['text'] or not change.get('basis'):
            raise ValueError('Historical listener edit lost its exact source/instruction')
        source_words, before, after = [list(TOKEN.finditer(text)) for text in
            (unit['text'], change['before'], change['after'])]
        if len(source_words) != len(before) or len(before) != len(after) or TOKEN.sub('', change['before']) != TOKEN.sub('', change['after']):
            raise ValueError('Only exact token-preserving listener edits can be imported')
        for index, (printed, old, new) in enumerate(zip(source_words, before, after)):
            if old[0] == new[0]:
                continue
            if _fold(printed[0]) != _fold(old[0]):
                raise ValueError('Listener edited token no longer matches its source word')
            origin = {'sha256': file_digest(path), 'file': str(path.resolve()), 'change': change,
                'created': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                'created_basis': 'original_preparation_export_mtime', 'instruction': change['basis'],
                'audio_approval': False}
            raw = _raw(book, unit, block, printed[0], unit['start'] + printed.start(), new[0], origin, index)
            if raw['kind'] == 'pronunciation':
                left, right = _plain(old[0]), _plain(new[0])
                if len(left) != len(right):
                    raise ValueError('This explicit token edit requires a length-preserving source binding')
                substitutions = [{'index': i, 'printed': a, 'spoken': b} for i, (a, b) in enumerate(zip(left, right)) if a != b]
                if not substitutions or any(printed[0][item['index']] != item['printed'] for item in substitutions):
                    raise ValueError('Phonetic substitutions do not bind the original source letters')
                raw['preparation_token_edit'] = {'policy': POLICY, 'substitutions': substitutions}
            _save(store, book, raw)
            imported.append(raw['id'])
    return imported


def import_source_notes(store, book, assignment, path):
    """Import explicit stress instructions with exact unit/word/source evidence."""
    path = Path(path)
    document = _read(path)
    if document.get('schema') != 1 or document.get('audio_approval') is not False:
        raise ValueError('Source notes require explicit no-audio-approval provenance')
    units = {unit['id']: unit for unit in assignment['units']}
    blocks = _blocks(book)
    imported = []
    for index, note in enumerate(document['notes']):
        unit = units[note['unit']]
        block = _unit_block(unit, blocks)
        matches = [token for token in TOKEN.finditer(unit['text']) if token[0] == note['selected_text']]
        if unit['text'] != note['source_text'] or len(matches) != 1 or not note.get('instruction'):
            raise ValueError('Explicit preparation instruction lacks a unique source occurrence')
        origin = {'sha256': file_digest(path), 'file': str(path.resolve()), 'note': note,
            'created': document['created'], 'created_basis': document['created_basis'],
            'instruction': note['instruction'], 'audio_approval': False}
        raw = _raw(book, unit, block, note['selected_text'],
            unit['start'] + matches[0].start(), note['intended_stress'], origin, index)
        if raw['kind'] != 'stress':
            raise ValueError('Source stress note is not a valid marked spelling')
        _save(store, book, raw)
        imported.append(raw['id'])
    return imported


def freeze(store, book):
    frozen = listener_stress.snapshot(store, book)
    edits = []
    for row in listener_stress.notes(store, book):
        raw = row['report']
        if row['book_key'] == listener_stress._book_key(book) and raw.get('preparation_token_edit'):
            edits.append(raw)
    result = {'policy': POLICY, 'stress': frozen, 'token_edits': edits}
    return {**result, 'sha256': digest(result)}


def apply(text, frozen, *, source_id, source_text, source_start, source_end):
    if frozen['sha256'] != digest({key: value for key, value in frozen.items() if key != 'sha256'}):
        raise ValueError('Human preparation snapshot checksum mismatch')
    human, receipt = listener_stress.apply(text, frozen['stress'], source_id=source_id,
        source_text=source_text, source_start=source_start, source_end=source_end)
    plain, starts, ends = listener_stress._plain_map(human)
    source = source_text[source_start:source_end]
    matcher = difflib.SequenceMatcher(None, _fold(source), _fold(plain), autojunk=False)
    latest = {}
    for raw in frozen['token_edits']:
        if raw['source_id'] != source_id or raw['passage_text'] != source_text:
            continue
        start = raw['text_offset'] - source_start
        end = start + len(raw['selected_text'])
        if end <= 0 or start >= len(source):
            continue
        if start < 0 or end > len(source) or source[start:end] != raw['selected_text']:
            raise ValueError('Human token edit crosses/mismatches the exact source unit')
        latest[(start, end)] = raw
    records = []
    for (start, end), raw in latest.items():
        matches = [(block.b + start - block.a, block.b + end - block.a) for block in matcher.get_matching_blocks()
            if block.a <= start and end <= block.a + block.size]
        if len(matches) != 1:
            raise ValueError('Human token edit cannot be mapped uniquely into prepared text')
        left, right = matches[0]
        before = human[starts[left]:ends[right - 1]]
        letters, word_starts, _ = listener_stress._plain_map(before)
        replacement = list(before)
        for substitution in raw['preparation_token_edit']['substitutions']:
            index = substitution['index']
            if letters[index] != substitution['printed']:
                raise ValueError('Human phonetic edit no longer matches its exact printed letter')
            replacement[word_starts[index]] = substitution['spoken']
        records.append({'start': starts[left], 'end': ends[right - 1], 'before': before,
            'after': ''.join(replacement), 'report_id': raw['id'], 'raw_sha256': digest(raw)})
    for record in sorted(records, key=lambda item: item['start'], reverse=True):
        human = human[:record['start']] + record['after'] + human[record['end']:]
    result = {'policy': POLICY, 'before': text, 'after': human, 'stress': receipt,
        'token_edits': records, 'snapshot_sha256': frozen['sha256'],
        'source_id': source_id, 'source_text': source_text, 'source_start': source_start, 'source_end': source_end}
    return human, {**result, 'sha256': digest(result)}


def validate(receipt, frozen):
    if receipt['sha256'] != digest({key: value for key, value in receipt.items() if key != 'sha256'}):
        raise ValueError('Human preparation receipt checksum mismatch')
    expected, record = apply(receipt['before'], frozen, **{key: receipt[key] for key in
        ('source_id', 'source_text', 'source_start', 'source_end')})
    if record != receipt or expected != receipt['after']:
        raise ValueError('Human preparation receipt does not replay exact shared feedback')
