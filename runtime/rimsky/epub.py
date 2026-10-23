from __future__ import annotations

import posixpath
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from lxml import etree

from . import books
from .books import HEADINGS, MAX_BLOCKS, MAX_TEXT, NOTE_SECTIONS, SVG_WARNING, extract_blocks, local, read_entry, remap_links, safe_archive, text_of, xml
from .preparation import narratable
from .util import AppError, language_name

EPUB_NS = 'http://www.idpf.org/2007/ops'
DC_NS = 'http://purl.org/dc/elements/1.1/'
CONTENT_TYPES = {'application/xhtml+xml', 'text/html', 'image/svg+xml', ''}
FONT_OBFUSCATION = {'http://www.idpf.org/2008/embedding', 'http://ns.adobe.com/pdf/enc#RC'}
AUXILIARY = {'notes', 'copyright-page', 'title-page', 'titlepage', 'halftitlepage', 'colophon', 'index', 'bibliography', 'imprint', 'imprimatur',
             'seriespage', 'errata', 'loi', 'lot', 'glossary', 'contributors', 'other-credits'} | NOTE_SECTIONS
TOC_ENTRY = re.compile(r'(?:[0-9]{1,4}[.)]?)?|.*(?:…|\.\.|·)', re.S)
FATAL_CODES = {'book_markup_limit', 'damaged_book_entry'}
CSS_BRACES = re.compile(r'([{}])')
CSS_SELECTOR = re.compile(r'\s*(?:[A-Za-z][A-Za-z0-9_-]*|\*)?\.([A-Za-z_][A-Za-z0-9_-]{0,99})(?:\s*:[A-Za-z-]+(?:\([^)]*\))?)*\s*')
CSS_STRONG = re.compile(r'(?:^|;)\s*font-weight\s*:\s*(?:bold|[6-9]00)\b', re.I)
CSS_EMPHASIS = re.compile(r'(?:^|;)\s*font-style\s*:\s*(?:italic|oblique)\b', re.I)
MAX_STYLESHEETS = books.MAX_ENTRY
STYLESHEET_WARNING = 'Some stylesheets exceed the safety limit; their bold and italic classes were not applied. All text was kept.'


def css_text(raw: bytes) -> str:
    text, parts, position = raw.decode('utf-8-sig', errors='ignore'), [], 0
    while (opening := text.find('/*', position)) >= 0:
        parts.append(text[position:opening])
        closing = text.find('*/', opening + 2)
        position = len(text) if closing < 0 else closing + 2
    return ''.join(parts) + text[position:]


def css_rules(text: str):
    tokens = CSS_BRACES.split(text)
    index = 0
    while index + 3 < len(tokens):
        if tokens[index] and tokens[index + 1] == '{' and tokens[index + 3] == '}':
            yield tokens[index], tokens[index + 2]
            index += 4
        else:
            index += 2


def stylesheet_class_formats(raw: bytes) -> dict[str, tuple[str, ...]]:
    """Extract bold/italic meaning from conservative, simple class selectors.

    Arbitrary CSS is never copied into prepared HTML. Complex selectors are
    ignored; converted FB2 EPUBs normally use rules such as ``.strong {...}``.
    """
    found: dict[str, list[str]] = {}
    for selectors, declarations in css_rules(css_text(raw)):
        styles = ['strong'] * bool(CSS_STRONG.search(declarations)) + ['emphasis'] * bool(CSS_EMPHASIS.search(declarations))
        for match in (CSS_SELECTOR.fullmatch(selector) for selector in selectors.split(',')) if styles else ():
            if match:
                found.setdefault(match.group(1), []).extend(styles)
    return {name: tuple(dict.fromkeys(styles)) for name, styles in found.items()}


def resource_reference(value: str, document: str, names: set[str]) -> tuple[str, str] | None:
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        path = unquote(parsed.path, errors='strict')
        fragment = unquote(parsed.fragment, errors='strict')
    except (ValueError, UnicodeError):
        return None
    if parsed.scheme or parsed.netloc or parsed.query or path.startswith('/') or '\\' in path:
        return None
    if any(ord(char) < 32 for char in path + fragment):
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(document), path)) if path else document
    if resolved.startswith('../') or resolved not in names:
        return None
    return resolved, fragment


def structural_types(node) -> set[str]:
    return set(node.get(f'{{{EPUB_NS}}}type', '').split())


def font_resource(path: str, media_type: str) -> bool:
    return (media_type.startswith(('font/', 'application/font-', 'application/x-font-')) or media_type == 'application/vnd.ms-opentype'
            or path.lower().endswith(('.otf', '.ttf', '.woff', '.woff2')))


def notes_document(body) -> bool:
    notes = lambda node: books.note_container(node, True)
    children = [child for child in body if isinstance(child.tag, str) and local(child) not in HEADINGS]
    return notes(body) or bool(children) and all(map(notes, children))


def document_types(body) -> set[str]:
    children = [structural_types(child) for child in body if isinstance(child.tag, str) and local(child) not in HEADINGS]
    return structural_types(body) | (set.intersection(*children) if children else set())


def semantic_types(marks, body, blocks, targeted, linked) -> set[str]:
    marks = {(kind, fragment) for kind, fragment in marks if kind in ('text', 'bodymatter', 'toc', 'cover') or kind in AUXILIARY}
    if any(fragment and kind not in ('text', 'bodymatter') for kind, fragment in marks):
        nodes, anchors = list(body.iter()), {}
        for index, node in enumerate(nodes):
            if isinstance(node.tag, str):
                for anchor in books.anchors_of(node):
                    anchors.setdefault(anchor, index)
        positions = [block['location']['position'] for block in blocks]
        first, last = min(positions), nodes[max(positions)]
        enclosing, end = {last, *last.iterancestors()}, first + sum(1 for _ in nodes[first].iter())
        opening = end if positions.count(first) == 1 and not any(first < position < end for position in positions) else first + 1
        texts = [block for block in blocks if narratable(block)]
        starts = set() if targeted else {kind for kind, paged in (('toc', 2 * sum(map(linked, texts)) > len(texts)),
                                                                  ('cover', all(block['kind'] == 'heading' for block in texts))) if paged}
        scoped = lambda kind, fragment: fragment in anchors and (kind in starts and anchors[fragment] < opening
                                                                 or anchors[fragment] <= first and nodes[anchors[fragment]] in enclosing)
    kinds = {kind for kind, fragment in marks if not fragment or kind in ('text', 'bodymatter') or scoped(kind, fragment)}
    return set() if kinds & {'text', 'bodymatter'} else kinds


def read_navigation(archive, names, manifest, package, warnings, landmarks=None, budget=None):
    result = []
    nav_paths = set()
    ncx_item = None
    spines = package.xpath('./*[local-name()="spine"]')
    if spines and spines[0].get('toc'):
        ncx_item = manifest.get(spines[0].get('toc'))
    for item in manifest.values():
        if 'nav' not in item['properties']:
            continue
        path = item['path']
        if not path:
            warnings.append('An EPUB navigation document is missing; available reading order was retained.')
            continue
        nav_paths.add(path)
        try:
            root = xml(read_entry(archive, names[path]), document=path, budget=budget)
            for nav in (node for node in root.iter() if landmarks is not None and local(node) == 'nav' and 'landmarks' in structural_types(node)):
                for link in nav.iter():
                    target = resource_reference(link.get('href', ''), path, names) if local(link) == 'a' else None
                    if target:
                        landmarks.setdefault(target[0], set()).update((kind, target[1]) for kind in structural_types(link))
            navs = [node for node in root.iter() if local(node) == 'nav' and 'toc' in structural_types(node)]
            if not navs:
                warnings.append('Navigation has no declared table of contents; using NCX or headings instead.')
            for nav in navs[:1]:
                for link in nav.iter():
                    if local(link) != 'a':
                        continue
                    target = resource_reference(link.get('href', ''), path, names)
                    label = text_of(link)
                    if not target or not label:
                        warnings.append('An invalid table-of-contents link was ignored; source content was retained.')
                        continue
                    depth = max(0, sum(local(parent) == 'li' for parent in link.iterancestors()) - 1)
                    result.append({'title': label[:300], 'href': target[0], 'fragment': target[1], 'depth': depth})
        except AppError as error:
            if error.code in FATAL_CODES:
                raise
            warnings.append('The EPUB navigation XML is invalid; using NCX or headings instead.')
        if result:
            break
    if result:
        return result, nav_paths, 'epub3-nav'
    if ncx_item is None:
        ncx_item = next((item for item in manifest.values() if item['media_type'] == 'application/x-dtbncx+xml'), None)
    if ncx_item and ncx_item['path']:
        try:
            path = ncx_item['path']
            root = xml(read_entry(archive, names[path]), document=path, budget=budget)
            maps = root.xpath('./*[local-name()="navMap"]')
            for navmap in maps:
                for point in navmap.iter():
                    if local(point) != 'navPoint':
                        continue
                    content = point.xpath('./*[local-name()="content"]')
                    labels = point.xpath('./*[local-name()="navLabel"]')
                    target = resource_reference(content[0].get('src', ''), path, names) if content else None
                    label = text_of(labels[0]) if labels else ''
                    if not target or not label:
                        warnings.append('An invalid NCX link was ignored; source content was retained.')
                        continue
                    depth = sum(local(parent) == 'navPoint' for parent in point.iterancestors())
                    result.append({'title': label[:300], 'href': target[0], 'fragment': target[1], 'depth': depth})
        except AppError as error:
            if error.code in FATAL_CODES:
                raise
            warnings.append('The EPUB NCX is invalid; chapter headings are used instead.')
    return result, nav_paths, 'ncx' if result else 'headings'


def validate_encryption(archive, names, manifest, warnings, budget=None):
    if 'META-INF/encryption.xml' not in names:
        return
    data = read_entry(archive, names['META-INF/encryption.xml'])
    if not re.sub(rb'<\?xml[^>]*\?>|\s|\xef\xbb\xbf', b'', data):
        return
    entries = xml(data, document='META-INF/encryption.xml', budget=budget).xpath('.//*[local-name()="EncryptedData"]')
    if not entries:
        return
    types = {item['path']: item['media_type'] for item in manifest.values() if item['path']}
    for entry in entries:
        methods = entry.xpath('./*[local-name()="EncryptionMethod"]')
        references = entry.xpath('./*[local-name()="CipherData"]/*[local-name()="CipherReference"]')
        reference = resource_reference(references[0].get('URI', ''), '', names) if len(references) == 1 else None
        if (len(methods) != 1 or methods[0].get('Algorithm') not in FONT_OBFUSCATION
                or not reference or not font_resource(reference[0], types.get(reference[0], ''))):
            raise AppError('Encrypted EPUB content is not supported. No decryption was attempted.')
    warnings.append('Obfuscated embedded fonts were not loaded. Book text uses the application font.')


def chapter_ranges(blocks, entries, path, warnings):
    anchor_blocks = {anchor: index for index, block in enumerate(blocks) for anchor in block.get('anchors', [])}
    boundaries = {}
    for entry in entries:
        if entry['href'] != path:
            continue
        index = anchor_blocks.get(entry['fragment']) if entry['fragment'] else 0
        if index is None:
            warnings.append('A table-of-contents fragment cannot be located; no text was removed.')
            continue
        if index not in boundaries or entry['depth'] >= boundaries[index]['depth']:
            boundaries[index] = entry
    if not boundaries:
        headings = [(index, int(block['html'][2])) for index, block in enumerate(blocks)
                    if block['kind'] == 'heading' and block['html'].startswith(tuple(f'<h{n}' for n in range(1, 7)))]
        if headings:
            levels = sorted(set(level for _, level in headings))
            level = levels[0]
            if sum(item_level == level for _, item_level in headings) == 1 and len(levels) > 1:
                next_level = levels[1]
                if sum(item_level == next_level for _, item_level in headings) > 1:
                    level = next_level
            for index, item_level in headings:
                if item_level == level and index not in boundaries:
                    boundaries[index] = {'title': blocks[index]['text'][:300], 'depth': 0, 'fragment': blocks[index]['source']}
    starts = sorted({0, *boundaries})
    return [(start, starts[index + 1] if index + 1 < len(starts) else len(blocks), boundaries.get(start))
            for index, start in enumerate(starts)]


def mark_navigation_heading(blocks, label):
    if not label or 'href' not in label or not blocks:
        return
    block = next((block for block in blocks if block['kind'] != 'figure'), None)
    if block is None or block['kind'] != 'paragraph' or not block['text'] or block['text'] != label['title']:
        return
    node = xml(block['html'].encode())
    if local(node) != 'p':
        return
    node.tag = 'h2'
    block['kind'] = 'heading'
    block['html'] = etree.tostring(node, encoding='unicode', with_tail=False)
    block['heading_source'] = 'navigation-label'


def metadata(package):
    containers = package.xpath('./*[local-name()="metadata"]')
    root = containers[0] if containers else package
    def values(name):
        nodes = root.xpath(f'./*[local-name()="{name}"]')
        preferred = [node for node in nodes if etree.QName(node).namespace == DC_NS]
        return [text_of(node) for node in preferred or nodes if text_of(node)]
    titles, creators, languages = values('title'), values('creator'), values('language')
    return {'title': (titles[0] if titles else 'Untitled book')[:500],
            'author': ', '.join(dict.fromkeys(creators))[:1000] or 'Unknown author',
            'language': languages[0] if languages else 'und'}



def reading_order(package, manifest, warnings):
    result, seen_ids, seen_paths = [], set(), set()
    references = package.xpath('./*[local-name()="spine"]/*[local-name()="itemref"]')
    for index, ref in enumerate(references):
        identity = ref.get('idref')
        if identity in seen_ids or identity not in manifest:
            warnings.append('An EPUB reading-order entry is missing from the manifest or repeated; it was skipped.')
            continue
        seen_ids.add(identity)
        item = manifest[identity]
        fallbacks = {identity}
        while item['media_type'] not in CONTENT_TYPES:
            identity = item['fallback']
            if identity not in manifest or identity in fallbacks:
                raise AppError('EPUB reading-order media has no supported text fallback.')
            fallbacks.add(identity)
            item = manifest[identity]
            warnings.append('A declared content fallback is used for unsupported reading-order media.')
        path = item['path']
        if not path or path in seen_paths:
            warnings.append('An EPUB reading-order document is missing from the archive or repeated; it was skipped.')
            continue
        seen_paths.add(path)
        result.append({'path': path, 'linear': ref.get('linear', 'yes') != 'no', 'spine_index': index})
    if not result:
        raise AppError('EPUB has no readable documents in its declared reading order.' if references else 'EPUB has no declared reading order.')
    return result


def prepare_epub(data: bytes, asset_root: Path) -> dict:
    assets, warnings, decoded_images, xml_budget = {}, [], {}, {}
    link_targets = {}
    with safe_archive(data) as archive:
        names = {entry.filename: entry for entry in archive.infolist() if not entry.is_dir()}
        if 'META-INF/container.xml' not in names:
            raise AppError('EPUB container.xml is missing.')
        container = xml(read_entry(archive, names['META-INF/container.xml']), document='META-INF/container.xml', budget=xml_budget)
        roots = container.xpath('.//*[local-name()="rootfile"]')
        rootfile = next((node for node in roots if node.get('media-type', 'application/oebps-package+xml') == 'application/oebps-package+xml'), None)
        target = resource_reference(rootfile.get('full-path', ''), '', names) if rootfile is not None else None
        if not target or target[1]:
            raise AppError('EPUB package document is missing or invalid.')
        package_path = target[0]
        package = xml(read_entry(archive, names[package_path]), document=package_path, budget=xml_budget)
        if local(package) != 'package':
            raise AppError('EPUB package document has an invalid root.')
        details = metadata(package)
        manifest = {}
        for node in package.xpath('./*[local-name()="manifest"]/*[local-name()="item"]'):
            identity = node.get('id')
            if not identity or identity in manifest:
                raise AppError('EPUB manifest has missing or duplicate resource IDs.')
            reference = resource_reference(node.get('href', ''), package_path, names) if node.get('href') else None
            manifest[identity] = {'path': reference[0] if reference and not reference[1] else None,
                                  'media_type': node.get('media-type', ''),
                                  'properties': node.get('properties', '').split(), 'fallback': node.get('fallback')}
        class_formats, stylesheet_bytes = {}, 0
        for item in manifest.values():
            if item['path'] and item['media_type'] == 'text/css':
                stylesheet_bytes += names[item['path']].file_size
                if stylesheet_bytes > MAX_STYLESHEETS:
                    warnings.append(STYLESHEET_WARNING)
                    continue
                for name, styles in stylesheet_class_formats(read_entry(archive, names[item['path']])).items():
                    class_formats[name] = tuple(dict.fromkeys(
                        [*class_formats.get(name, ()), *styles]))
        declared_covers = [item for item in manifest.values() if 'cover-image' in item['properties']]
        for node in package.xpath('./*[local-name()="metadata"]/*[local-name()="meta"]'):
            if node.get('name') == 'cover' and node.get('content') in manifest:
                declared_covers.append(manifest[node.get('content')])
        validate_encryption(archive, names, manifest, warnings, xml_budget)
        landmarks = {}
        navigation, nav_paths, nav_kind = read_navigation(archive, names, manifest, package, warnings, landmarks, xml_budget)
        for node in package.xpath('./*[local-name()="guide"]/*[local-name()="reference"]'):
            reference = resource_reference(node.get('href', ''), package_path, names)
            if reference:
                landmarks.setdefault(reference[0], set()).add(((node.get('type') or '').strip().lower(), reference[1]))
        svg_budget, demoted = {}, set()

        def load_image(path):
            if path not in decoded_images:
                decoded_images[path] = books.image_asset(read_entry(archive, names[path]), asset_root, assets, keep_jpeg=True)
                if not decoded_images[path]:
                    warnings.append('An embedded image could not be displayed; its source remains in the EPUB.')
            return decoded_images[path]

        documents = reading_order(package, manifest, warnings)
        scheduled = {item['path'] for item in documents}
        declared_text = {item['path'] for item in manifest.values() if item['path'] and item['media_type'] in CONTENT_TYPES}

        def retain_auxiliary(path):
            if path not in scheduled and path in declared_text:
                scheduled.add(path)
                documents.append({'path': path, 'linear': False, 'spine_index': None})
                warnings.append('Linked content outside the spine is retained after the main reading order as an optional section.')

        for entry in navigation:
            retain_auxiliary(entry['href'])
        chapters, document_blocks, toc_chapters, nav_targets = [], {}, set(), {entry['href'] for entry in navigation}
        text_count, block_count = 0, 0
        for document_index, document in enumerate(documents):
            path = document['path']
            raw, malformed = read_entry(archive, names[path]), None
            try:
                root = xml(raw, document=path, encoding_error=True, budget=xml_budget)
            except AppError as error:
                if error.code != 'invalid_book_xml':
                    raise
                malformed = error
                root = books.html_document(raw, path, xml_budget, error)
            if local(root) not in ('html', 'svg'):
                raise AppError('EPUB reading-order resource is not an HTML or SVG content document.')
            bodies = root.xpath('.//*[local-name()="body"]')
            body = bodies[0] if bodies else root
            if books.bound_svg(body, svg_budget):
                warnings.append(SVG_WARNING)
            def resolve_image(source):
                reference = resource_reference(source, path, names)
                return load_image(reference[0]) if reference else None
            def resolve_link(source):
                reference = resource_reference(source, path, names)
                if not reference:
                    return None
                retain_auxiliary(reference[0])
                token = f'link{len(link_targets)}'
                link_targets[token] = reference
                return token
            blocks = extract_blocks(body, f'd{document_index + 1:04}', resolve_image,
                                    track_source=True, resolve_link=resolve_link,
                                    class_formats=class_formats)
            if malformed:
                if not any(block['text'] for block in blocks):
                    raise malformed
                warnings.append(f'Book document {malformed.details["document"]} is not well-formed XML; it was read in recovery mode and all of its text was kept.')
            seen_anchors = set()
            for block in blocks:
                kept = [anchor for anchor in block['anchors'] if anchor not in seen_anchors]
                if len(kept) != len(block['anchors']):
                    warnings.append('Repeated anchor IDs in a content document were ignored after their first use; links go to the first occurrence.')
                seen_anchors.update(kept)
                block['anchors'] = kept
            notes = notes_document(body)
            if notes:
                for block in blocks:
                    block['kind'] = 'paragraph' if block['kind'] == 'note' else block['kind']
            text_count += sum(len(block['text']) for block in blocks)
            block_count += len(blocks)
            if text_count > MAX_TEXT or block_count > MAX_BLOCKS:
                raise AppError('EPUB prepared text exceeds two million characters or 100,000 passages.')
            if not blocks or all(block['kind'] == 'separator' or not (block['text'].strip() or block.get('assets') or '<svg' in block.get('html', '')) for block in blocks):
                warnings.append('An empty reading-order document has no displayable passages.')
                continue
            document_blocks[path] = blocks
            linear = document['linear']
            types = document_types(body)
            if (len(blocks) == 1 and blocks[0]['kind'] == 'figure' and not blocks[0]['text']
                    and any(decoded_images.get(item['path']) in blocks[0]['assets'] for item in declared_covers if item['path'])):
                types.add('cover')
            kinds = semantic_types(landmarks.get(path, ()), body, blocks, path in nav_targets, lambda block: any(
                link_targets[token][0] != path and (not note or bool(TOC_ENTRY.fullmatch(block['text'])))
                for token, note in re.findall(r'data-source-link="(link[0-9]+)"( data-note-ref="")?', block['html'])))
            role = ('navigation' if path in nav_paths or 'toc' in kinds else 'cover' if 'cover' in types or 'cover' in kinds
                    else 'auxiliary' if not linear or notes or (types | kinds) & AUXILIARY else 'body')
            continuation = (chapters and chapters[-1]['id'] in toc_chapters and linear and role == 'body'
                            and document['spine_index'] is not None and chapters[-1]['linear']
                            and chapters[-1]['role'] == 'body'
                            and chapters[-1].get('spine_end_index') == document['spine_index'] - 1
                            and not any(entry['href'] == path for entry in navigation)
                            and not any(block['kind'] == 'heading' for block in blocks))
            if continuation:
                previous = chapters[-1]
                for index, block in enumerate(blocks, len(previous['blocks']) + 1):
                    block['id'] = f'{previous["id"]}-p{index:05}'
                    block['location']['href'] = path
                previous['blocks'].extend(blocks)
                previous['source_files'].append(path)
                previous['spine_end_index'] = document['spine_index']
                warnings.append('Heading-free spine continuations without a new TOC target were joined to the preceding TOC chapter; all source locations were retained.')
                continue
            for start, end, label in chapter_ranges(blocks, navigation, path, warnings):
                cid = f'c{len(chapters) + 1:04}'
                if label and 'href' in label:
                    toc_chapters.add(cid)
                chapter_blocks = blocks[start:end]
                mark_navigation_heading(chapter_blocks, label)
                heading = next((block['text'] for block in chapter_blocks if block['kind'] == 'heading'), '')
                title_nodes = root.xpath('./*[local-name()="head"]/*[local-name()="title"]')
                document_title = text_of(title_nodes[0]) if title_nodes else ''
                ru = language_name(details['language']) == 'Russian'
                fallback = f'Раздел {len(chapters) + 1}' if ru else f'Section {len(chapters) + 1}'
                if role == 'cover':
                    fallback = 'Обложка' if ru else 'Cover'
                elif not label and len(chapter_blocks) <= 3 and sum(len(block['text']) for block in chapter_blocks) <= 300:
                    if any(block['text'] == details['title'] for block in chapter_blocks):
                        fallback = details['title']
                for index, block in enumerate(chapter_blocks):
                    block['id'] = f'{cid}-p{index + 1:05}'
                    block['location']['href'] = path
                if role != 'body' and linear and not notes and path not in nav_paths:
                    demoted.add(cid)
                chapters.append({'id': cid, 'title': (label['title'] if label else heading or (fallback if role == 'cover' else document_title or fallback))[:300],
                                 'source': path, 'fragment': label.get('fragment', '') if label else '',
                                 'spine_index': document['spine_index'], 'spine_end_index': document['spine_index'],
                                 'source_files': [path], 'navigation_depth': label.get('depth', 0) if label else 0,
                                 'linear': linear, 'role': role, 'default_narration': linear and role == 'body',
                                 'blocks': chapter_blocks})
        if not chapters:
            raise AppError('EPUB has no readable content in its declared reading order.')
        narrated = lambda chapter: any(map(narratable, chapter['blocks']))
        if demoted and not any(chapter['default_narration'] and narrated(chapter) for chapter in chapters):
            for chapter in chapters:
                if chapter['id'] in demoted and narrated(chapter):
                    chapter.update(role='body', default_narration=True)
        targets = {}
        for path, blocks in document_blocks.items():
            targets[(path, '')] = blocks[0]['id']
            for block in blocks:
                for anchor in block['anchors']:
                    targets[(path, anchor)] = block['id']
        remap_links((block for blocks in document_blocks.values() for block in blocks), targets, link_targets)
        if any(target not in targets for target in link_targets.values()):
            warnings.append('Some internal links have no displayable target. Their visible text was preserved without an active link.')
        resolved_navigation = []
        for entry in navigation:
            source_id = targets.get((entry['href'], entry['fragment']))
            if source_id:
                resolved_navigation.append({**entry, 'source_id': source_id})
            else:
                warnings.append('A navigation target is outside the prepared reading order; the original EPUB is preserved.')
        cover = None
        for item in declared_covers:
            if item['path']:
                cover = load_image(item['path'])
                if cover:
                    break
        if cover is None:
            cover = next((asset for chapter in chapters if chapter['role'] == 'cover'
                          for block in chapter['blocks'] for asset in block['assets']), None)
        if any(not chapter['default_narration'] for chapter in chapters):
            warnings.append('Navigation, cover, notes and non-linear sections are preserved for reading but excluded from default narration. Select them explicitly to narrate their text.')
        if nav_kind == 'headings':
            warnings.append('No usable table of contents was found; chapters follow headings within the EPUB spine.')
        return {**details, 'chapters': chapters, 'assets': list(assets.values()), 'cover': cover,
                'warnings': list(dict.fromkeys(warnings)), 'navigation': resolved_navigation,
                'epub': {'package': package_path, 'version': package.get('version', ''), 'navigation': nav_kind}}
