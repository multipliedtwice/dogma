from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import posixpath
import re
from urllib.parse import unquote
import zipfile

from lxml import etree


def normalized(text):
    return re.sub(r'\s+', ' ', text).strip()


def parse(raw):
    return etree.fromstring(raw, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))


def verify(source: Path, prepared: Path) -> dict:
    book = json.loads((prepared / 'book.json').read_text('utf-8'))
    inputs = [json.loads(line) for line in (prepared / 'speech-inputs.jsonl').read_text('utf-8').splitlines()]
    raw = source.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    assert source_hash == book['source_hash'], 'Source hash differs.'
    assert raw == (prepared / 'books' / book['id'] / 'source.epub').read_bytes(), 'Original source was changed.'
    expected, paths, code_locations = [], [], set()
    with zipfile.ZipFile(source) as archive:
        container = parse(archive.read('META-INF/container.xml'))
        package_path = container.xpath('//*[local-name()="rootfile"]')[0].get('full-path')
        package = parse(archive.read(package_path))
        base = posixpath.dirname(package_path)
        items = {item.get('id'): posixpath.normpath(posixpath.join(base, unquote(item.get('href', ''))))
                 for item in package.xpath('//*[local-name()="manifest"]/*[local-name()="item"]')}
        for ref in package.xpath('//*[local-name()="spine"]/*[local-name()="itemref"]'):
            path = items[ref.get('idref')]
            paths.append(path)
            document = parse(archive.read(path))
            bodies = document.xpath('//*[local-name()="body"]')
            assert len(bodies) == 1, 'Verification profile requires XHTML bodies.'
            for element in bodies[0].iter():
                if not isinstance(element.tag, str):
                    continue
                if etree.QName(element).localname in {'script', 'style', 'math', 'table', 'pre'}:
                    raise AssertionError('This independent verifier supports paragraph-based reflow text only.')
                if normalized(element.text or ''):
                    assert any(etree.QName(node).localname == 'p' for node in [element, *element.iterancestors()]), 'Visible text outside paragraphs requires another verifier.'
                if etree.QName(element).localname != 'p':
                    continue
                text = normalized(''.join(element.itertext()))
                if not text:
                    continue
                xpath = document.getroottree().getpath(element)
                expected.append((path, xpath, text))
                if any(etree.QName(parent).localname == 'code' for parent in element.iterancestors()):
                    code_locations.add((path, xpath))
        ncx_refs = package.xpath('//*[local-name()="manifest"]/*[local-name()="item"][@media-type="application/x-dtbncx+xml"]')
        ncx_labels = []
        if ncx_refs:
            ncx = parse(archive.read(items[ncx_refs[0].get('id')]))
            ncx_labels = [normalized(''.join(node.itertext())) for node in ncx.xpath('//*[local-name()="navPoint"]/*[local-name()="navLabel"]')]
    blocks = [block for chapter in book['chapters'] for block in chapter['blocks']]
    actual = [(block['location']['href'], block['location']['xpath'], block['text']) for block in blocks if block['text']]
    assert len(expected) == len(actual), 'Paragraph count differs.'
    for index, (left, right) in enumerate(zip(expected, actual)):
        assert left == right, f'Ordered paragraph or source-location mismatch at {index}.'
    assert list(dict.fromkeys(block['location']['href'] for block in blocks)) == paths, 'Spine order differs.'
    assert len({block['id'] for block in blocks}) == len(blocks), 'Duplicate passage IDs.'
    assert ncx_labels == [entry['title'] for entry in book['navigation']], 'NCX labels were changed or omitted.'
    by_id = {block['id']: block for block in blocks}
    for entry in book['navigation']:
        block = by_id[entry['source_id']]
        if entry['title'] == block['text']:
            assert block['kind'] == 'heading', 'An exact navigation heading remains plain prose.'
    for block in blocks:
        location = (block['location']['href'], block['location']['xpath'])
        if location in code_locations:
            assert 'code' in block.get('formatting', {}).get('enclosing', []), 'Enclosing code context is lost.'
            assert '<code>' in block['html'], 'Code formatting is not displayed.'
    expected_ids = [block['id'] for chapter in book['chapters'] if chapter['id'] in book['preparation']['default_chapters']
                    for block in chapter['blocks'] if block['text'] and block['kind'] not in ('figure', 'separator')]
    ranges = defaultdict(list)
    for ordinal, segment in enumerate(inputs):
        assert segment['ordinal'] == ordinal, 'Noncontiguous segment order.'
        block = by_id[segment['source_id']]
        assert segment['text'] == block['text'][segment['start']:segment['end']], 'Spoken input differs from the mapped source.'
        assert 0 < len(segment['text']) <= 420, 'Invalid initial segment size.'
        assert 'speaker' not in segment, 'Model-free preparation claimed speaker assignment.'
        ranges[segment['source_id']].append(segment)
    assert list(ranges) == expected_ids, 'Source coverage is missing or reordered.'
    for source_id, segments in ranges.items():
        cursor = 0
        for segment in segments:
            assert segment['start'] == cursor, 'Gap or overlap in a source passage.'
            cursor = segment['end']
        assert cursor == len(by_id[source_id]['text']), 'Truncated source passage.'
    return {'source_sha256': source_hash, 'title': book['title'], 'author': book['author'], 'language': book['language'],
            'spine_documents': len(paths), 'prepared_sections': len(book['chapters']), 'ncx_entries': len(ncx_labels),
            'nonempty_source_paragraphs': len(expected), 'prepared_passages': len(blocks),
            'prepared_characters': sum(len(item[2]) for item in expected), 'base_segments': len(inputs),
            'semantic_headings': sum(block['kind'] == 'heading' for block in blocks),
            'enclosing_code_passages': len(code_locations), 'asset_count': len(book['assets']),
            'kinds': dict(Counter(block['kind'] for block in blocks)),
            'checks': ['byte-identical source', 'independent ordered paragraph text and locations', 'declared spine order',
                       'NCX labels and targets', 'unique passage IDs', 'source code formatting',
                       'complete contiguous narration ranges', 'no automatic speaker assignment'],
            'scope': 'This file and its paragraph-based reflow profile; not arbitrary EPUB conformance or model quality.'}


def main():
    parser = argparse.ArgumentParser(description='Independently compare paragraph-based EPUB source to a prepared bundle.')
    parser.add_argument('source', type=Path)
    parser.add_argument('prepared', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.source, args.prepared), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
