from __future__ import annotations

from .books import split_text
from .util import AppError, digest, language_name, language_tag, script_language

PREPARATION_VERSION = 2


def narratable(block: dict) -> bool:
    return bool(block.get('text')) and block.get('kind') not in ('figure', 'separator', 'note') and block.get('exclude_from_narration') is not True


def audible(text: str) -> bool:
    return any(character.isalnum() for character in text)


def speech_language(book: dict) -> str:
    declared = language_name(book.get('language'))
    if not declared and language_tag(book.get('language')):
        return 'Auto'
    chapters = [chapter for chapter in book.get('chapters', []) if chapter.get('default_narration', True)] or book.get('chapters', [])
    sample, size = [], 0
    for block in (block for chapter in chapters for block in chapter['blocks'] if narratable(block)):
        if size >= 5000:
            break
        sample.append(block['text'])
        size += len(block['text'])
    return script_language(' '.join(sample)[:5000]) or declared or 'Auto'


def default_chapters(book: dict) -> list[str]:
    return [chapter['id'] for chapter in book['chapters']
            if chapter.get('default_narration', True) and any(narratable(block) for block in chapter['blocks'])]


def analysis_chapters(book: dict, explicit: list[str]) -> list[str]:
    """Chapters a generation analyses, in book order: default narration plus explicitly selected chapters."""
    chosen = {*default_chapters(book), *explicit}
    return [chapter['id'] for chapter in book['chapters'] if chapter['id'] in chosen]


def speech_sources(book: dict, selected: list[str]):
    chosen = set(selected)
    ordinal = 0
    for chapter in book['chapters']:
        if chapter['id'] not in chosen:
            continue
        scene = 0
        for block in chapter['blocks']:
            if block.get('kind') == 'separator':
                scene += 1
            if not narratable(block):
                continue
            for start, end, text in split_text(block['text']):
                yield {'id': f'{block["id"]}-{start}-{end}', 'ordinal': ordinal,
                       'chapter_id': chapter['id'], 'source_id': block['id'], 'kind': block['kind'],
                       'scene_id': f'{chapter["id"]}-scene{scene}',
                       'start': start, 'end': end, 'text': text,
                       **({'formatting': block['formatting']} if block.get('formatting') else {})}
                ordinal += 1


def preparation_report(book: dict) -> dict:
    selected = default_chapters(book)
    chapters = []
    source_ids = set()
    for chapter in book['chapters']:
        blocks = chapter['blocks']
        for block in blocks:
            if block['id'] in source_ids:
                raise AppError('Prepared book contains duplicate source passage identifiers.')
            source_ids.add(block['id'])
        audible = [block for block in blocks if narratable(block)]
        segments = sum(len(split_text(block['text'])) for block in audible)
        chapters.append({'id': chapter['id'], 'title': chapter['title'], 'source': chapter['source'],
                         'source_files': chapter.get('source_files', [chapter['source']]),
                         'role': chapter.get('role', 'body'), 'default_narration': chapter['id'] in selected,
                         'passages': len(blocks), 'narratable_passages': len(audible),
                         'characters': sum(len(block['text']) for block in audible),
                         'base_segments': segments,
                         'figures': sum(block['kind'] == 'figure' for block in blocks),
                         'equations': sum(block['kind'] == 'equation' for block in blocks),
                         'tables': sum(block['kind'] == 'table' for block in blocks),
                         'source_digest': digest([(block['id'], block['text']) for block in blocks])})
    narrated = [chapter for chapter in chapters if chapter['default_narration']]
    return {'schema': PREPARATION_VERSION, 'engine': 'bounded-epub-fb2',
            'source_hash': book.get('source_hash', ''), 'default_chapters': selected,
            'chapters': chapters, 'passages': len(source_ids),
            'narration_characters': sum(chapter['characters'] for chapter in narrated),
            'base_segments': sum(chapter['base_segments'] for chapter in narrated),
            'source_digest': digest([(chapter['id'], chapter['source_digest']) for chapter in chapters]),
            'warnings': book.get('warnings', []), 'requires_model': False,
            'note': 'Base segments are pre-analysis boundaries, not speaker assignments or a duration estimate.'}
