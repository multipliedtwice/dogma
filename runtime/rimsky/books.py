from __future__ import annotations
import base64
import copy
import functools
import html
import html.entities
import io
import lzma
import posixpath
import re
import zipfile
import shutil
import tempfile
import zlib
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from lxml import etree
from PIL import Image, ImageOps
from .util import AppError, atomic_write, digest, display_text, language_name, language_tag, script_language

MAX_UPLOAD = 50 * 1024 * 1024
MAX_EXPANDED = 250 * 1024 * 1024
MAX_ENTRY = 20 * 1024 * 1024
MAX_TEXT = 2_000_000
MAX_BLOCKS = 100_000
MAX_DEPTH = 256
BLOCKS = {'hr', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'table', 'figure', 'blockquote', 'pre', 'li', 'math', 'image', 'img', 'svg', 'v', 'subtitle', 'dt', 'dd'}
STRUCTURE = BLOCKS | {'div'}
ALLOWED = {'p', 'em', 'strong', 'b', 'i', 'u', 's', 'sub', 'sup', 'br', 'span', 'div', 'blockquote', 'pre', 'code', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'caption', 'figure', 'figcaption', 'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'math', 'mi', 'mn', 'mo', 'mrow', 'msup', 'msub', 'msubsup', 'mfrac', 'msqrt', 'mroot', 'mtext', 'mtable', 'mtr', 'mtd', 'mfenced', 'mover', 'munder', 'munderover', 'semantics', 'annotation', 'svg', 'g', 'path', 'line', 'rect', 'circle', 'ellipse', 'polyline', 'polygon', 'text', 'tspan'}
SVG_ATTRS = {'viewBox', 'd', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'width', 'height', 'points', 'transform', 'text-anchor', 'font-size', 'stroke-width', 'preserveAspectRatio'}
FORMAT_TAGS = {'code': 'code', 'pre': 'code', 'blockquote': 'quotation', 'em': 'emphasis', 'i': 'emphasis', 'emphasis': 'emphasis', 'strong': 'strong', 'b': 'strong'}
SEMANTIC_CLASSES = {'strong', 'emphasis', 'cite', 'epigraph', 'poem', 'stanza',
                    'text-author', 'subtitle'}
FB2_CLASSES = {'epigraph': 'epigraph', 'poem': 'poem', 'stanza': 'stanza', 'v': 'poem', 'cite': 'cite', 'text-author': 'text-author', 'subtitle': 'subtitle'}
MAX_CLASS_HINTS = 50
SVG_NS = 'http://www.w3.org/2000/svg'
XLINK_HREF = '{http://www.w3.org/1999/xlink}href'
ROMAN = ((1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I'))
DROP = {'script', 'style', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'link', 'foreignObject', 'audio', 'video'}
EPUB_TYPE = '{http://www.idpf.org/2007/ops}type'
XML_ENTITIES = {'amp', 'lt', 'gt', 'quot', 'apos'}
NAMED_ENTITY = re.compile(rb'(<!\[CDATA\[.*?(?:\]\]>|\Z))|&([A-Za-z][A-Za-z0-9]{1,31});', re.S)
CUSTOM_ENTITY = re.compile(rb'&(?!(?:amp|lt|gt|quot|apos);)[A-Za-z_:][\w.:-]*;')
NOTE_MARKER = re.compile(r'[\[({]?(?:\d{1,4}|[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,4}|[*†‡]{1,3})[\])}]?|[↩↑⤴^][\ufe0e\ufe0f]?')
TYPED_MARKER = re.compile(NOTE_MARKER.pattern + r'|[\[({]?\s?(?:[*†‡]?\d{1,4}[.*†‡]?|[*†‡§¶‖#]{1,8}|[A-Za-z]|[ivx]{1,5}|[IVX]{1,5})\s?[\])}]?')
ROMAN_NUMERAL = re.compile(r'm{0,4}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})')
DROP_CAP = re.compile(r'drop|(?<![\w-])initial(?![\w-])|(?:first|initial).?letter|lettrine|(?<![a-z])cap(?:ital)?(?![a-z])', re.I)
DROP_CAP_STYLE = re.compile(r'float\s*:\s*(?:left|right)|initial-letter\s*:', re.I)
UKRAINIAN = re.compile('[а-яёіїєґ][іїєґ]|[іїєґ][а-яёіїєґ]', re.I)
CYRILLIC_WORD = re.compile('[Ѐ-Я]?[а-џ]+')
CYRILLIC_CAPS = re.compile('[Ѐ-Я]+')
NOTE_TYPES = {'footnote', 'endnote', 'rearnote', 'note'}
NOTE_SECTIONS = {'endnotes', 'footnotes', 'rearnotes'}
HEADINGS = ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')
SEPARATOR = re.compile(r'[⁂❧☙❦]|(?:[*＊∗⁎⁑✱✲✳✴✵✶✷✸✹✺✻✼✽❊❋•●○◦·∙⋅◆◇♦❖★☆⋆~〜～—–‒―_=#⁂❧☙❦-]\s*){3,}|[xхXХ](?:\s+[xхXХ]){2,}')
SENTENCE_BREAK = re.compile(r'([.!?…]+)[»”"\)\]]*\s+')
SENTENCE_START = re.compile(r'[«“"(\s—–-]*(\w)')
WORD_BEFORE = re.compile(r'\w+$')
LETTERS_BEFORE = re.compile(r'[^\W\d_]+$')
ABBREVIATIONS = {'г', 'гг', 'ул', 'им', 'т', 'д', 'др', 'стр', 'рис', 'табл', 'см', 'ст', 'mr', 'mrs', 'ms', 'dr', 'st', 'etc'}
IMAGE_FORMATS = ('PNG', 'JPEG', 'GIF', 'WEBP', 'BMP')
ENCODING_ERRORS = {31, 32, 81}
ENCODING_MESSAGE = 'The book text encoding could not be read.'
MAX_SVG_ATTRIBUTE = 32 * 1024
MAX_SVG_GEOMETRY = 8 * 1024 * 1024
SVG_WARNING = 'Some vector drawings were too large and were omitted.'
XML_LIMITS = {'elements': 200_000, 'attributes': 400_000, 'texts': 400_000}
XML_LIMIT_MESSAGE = 'Book markup exceeds the safety limit of 200,000 elements, 400,000 attributes or 400,000 text runs.'
FILE_ALT = re.compile(r'[\w\s().,-]*\.(?:jpe?g|png|gif|webp|bmp|svg|tiff?|avif)|(?:img|image|images|pic|picture|fig|figure|illus|illustration|cover|graphic)[\s_.-]*\d*', re.I)
XML_CHUNK = 64 * 1024
DECLARED_ENCODING = re.compile(rb'\s*<\?xml\b[^<>]*?\sencoding\s*=\s*["\']([A-Za-z0-9._:-]{1,40})["\']')
XML_DECLARATION = re.compile(r'\A\s*<\?xml\b[^<>]*\?>')
MARKUP = re.compile(r'<!--.*?(?:-->|\Z)|<!\[CDATA\[(?P<cdata>.*?)(?:\]\]>|\Z)|<(?P<raw>script|style)\b[^<>]*?(?<!/)>(?P<code>.*?)(?:</(?P=raw)\s*>|\Z)'
                    r'|<[!?][^<>]*>|<(?P<close>/?)(?P<tag>[A-Za-z][^\s/<>]*)(?:[^<>"\']|"[^"]*"|\'[^\']*\')*>', re.S | re.I)
BOUNDARY_TAGS = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'dt', 'dd', 'hr', 'br', 'img', 'div', 'blockquote', 'pre', 'table', 'tr', 'td', 'th', 'figure'}
XML_NAME = re.compile(r'[^\W\d][\w.-]*')
ENTITY_NAME = re.compile(r'&([A-Za-z][A-Za-z0-9]{0,31});')
HTML_ATTRIBUTES = {'epub:type': EPUB_TYPE, 'xlink:href': XLINK_HREF, 'xml:id': '{http://www.w3.org/XML/1998/namespace}id',
                   **{name.lower(): name for name in SVG_ATTRS if name != name.lower()}}

def local(node) -> str:
    return etree.QName(node).localname if isinstance(node.tag, str) else ''

def dropped(node) -> bool:
    return local(node) in DROP

def numeric_entities(data: bytes) -> bytes:
    def replace(match):
        if match.group(1) or match.group(2).decode() in XML_ENTITIES:
            return match.group(0)
        value = html.entities.html5.get(match.group(2).decode() + ';')
        return ''.join(f'&#{ord(character)};' for character in value).encode() if value else match.group(0)
    return NAMED_ENTITY.sub(replace, data)

def note_reference(node, internal=False) -> bool:
    if local(node) != 'a':
        return False
    typed = node.get('type') == 'note' or node.get('role') in ('doc-noteref', 'doc-backlink') or bool({'noteref', 'backlink'} & set((node.get(EPUB_TYPE) or '').split()))
    text = display_text(''.join(node.itertext()))
    return (internal or typed) and (typed_marker(text) if typed else bool(NOTE_MARKER.fullmatch(text)))

def typed_marker(text: str) -> bool:
    return bool(TYPED_MARKER.fullmatch(text)) or len(text) <= 6 and ' ' not in text and (
        any(map(str.isnumeric, text)) or all(len(word) < 3 or ROMAN_NUMERAL.fullmatch(word.lower()) for word in re.findall(r'[^\W\d_]+', text)))

def note_container(node, sections=False) -> bool:
    types = set((node.get(EPUB_TYPE) or '').split())
    return (bool(NOTE_TYPES & types) or node.get('role') in ('doc-footnote', 'doc-endnote')
            or sections and (bool(NOTE_SECTIONS & types) or node.get('role') == 'doc-endnotes'))

def drop_cap_hint(element) -> bool:
    return bool(DROP_CAP.search(' '.join(filter(None, map(element.get, ('class', EPUB_TYPE))))) or DROP_CAP_STYLE.search(element.get('style') or ''))

def letter_texts(root) -> dict:
    texts = {}
    for _, element in etree.iterwalk(root, events=('end',)):
        text = display_text(element.text or '')
        for child in element:
            text = (text + texts.get(child, '') + display_text(child.tail or '')).replace(' ', '')[:2]
        texts[element] = text.replace(' ', '')[:2]
    return texts

def drop_cap(node, origin, letter: str, texts) -> bool:
    if any(map(drop_cap_hint, node.iter(etree.Element))):
        return True
    for parent in origin.iterancestors():
        if texts().get(parent) != letter:
            return False
        if drop_cap_hint(parent):
            return True
    return False

def document_name(document: str) -> str:
    return re.sub(r'[\x00-\x1f\x7f]', '', document)[:200]

def encoding_failure(name: str) -> AppError:
    return AppError(f'The text encoding of book document {name} could not be read.' if name else ENCODING_MESSAGE, 'invalid_book_encoding', document=name)

def bounded_parse(data: bytes, parser, budget: dict | None):
    used = {key: (budget or {}).get(key, 0) for key in XML_LIMITS}
    depth = 0
    def count(events):
        nonlocal depth
        for event, node in events:
            if event == 'end':
                depth -= 1
                used['texts'] += bool(node[-1].tail if len(node) else node.text)
            else:
                previous = node.getprevious()
                depth += event == 'start'
                used['elements'] += 1
                used['attributes'] += len(node.attrib) if event == 'start' else 0
                used['texts'] += bool(previous is not None and previous.tail)
            if depth > MAX_DEPTH:
                raise AppError('Book XML nesting exceeds the safety limit.')
            if any(used[key] > limit for key, limit in XML_LIMITS.items()):
                raise AppError(XML_LIMIT_MESSAGE, 'book_markup_limit', limits=XML_LIMITS)
    for start in range(0, len(data), XML_CHUNK):
        parser.feed(data[start:start + XML_CHUNK])
        count(parser.read_events())
    root = parser.close()
    count(parser.read_events())
    if budget is not None:
        budget.update(used)
    return root

def xml(data: bytes, limit: int = MAX_ENTRY, huge: bool = False, document: str = '', encoding_error: bool = False, budget: dict | None = None):
    if len(data) > limit:
        raise AppError(f'A book document exceeds the {limit // (1024 * 1024)} MiB safety limit.')
    name = document_name(document)
    def invalid(line=None):
        where = f' at line {line}' if line else ''
        return AppError(f'Book document {name} is not well-formed XML{where}.' if name else f'The book is not well-formed XML{where}.',
                        'invalid_book_xml', document=name, line=line)
    data = numeric_entities(data)
    if b'<!ENTITY' in data and CUSTOM_ENTITY.search(data):
        raise AppError('Entity references are not accepted in books.')
    parser = etree.XMLPullParser(events=('start', 'end', 'pi'), resolve_entities=False, no_network=True, load_dtd=False, huge_tree=huge, remove_comments=True)
    try:
        root = bounded_parse(data, parser, budget)
    except etree.XMLSyntaxError as error:
        code, line = (error.code, error.lineno) if isinstance(error.lineno, int) and error.lineno > 0 else next(
            ((entry.type, entry.line) for entry in error.error_log if entry.line > 0), (error.code, None))
        if encoding_error and code in ENCODING_ERRORS:
            raise encoding_failure(name) from error
        raise invalid(line) from error
    if root is None:
        raise invalid()
    if any(isinstance(n, etree._Entity) for n in root.iter()):
        raise AppError('Entity references are not accepted in books.')
    return root

def decoded_document(data: bytes, name: str) -> str:
    declared = DECLARED_ENCODING.match(data)
    encoding = ('utf-8-sig' if data[:3] == b'\xef\xbb\xbf' else 'utf-16' if data[:2] in (b'\xff\xfe', b'\xfe\xff')
                else declared.group(1).decode() if declared else 'utf-8')
    try:
        return data.decode(encoding)
    except (LookupError, UnicodeError) as error:
        raise encoding_failure(name) from error

def character_data(text: str) -> tuple[str, Counter, bool]:
    parts, tags, end, unknown = [], Counter(), 0, False
    for match in [*MARKUP.finditer(text), None]:
        segment = text[end:match.start() if match else len(text)]
        unknown = unknown or any(f'{name};' not in html.entities.html5 for name in ENTITY_NAME.findall(segment))
        parts.append(html.unescape(segment))
        if match:
            parts.append(match.group('cdata') or match.group('code') or '')
            if match.group('tag') and not match.group('close'):
                tags[match.group('tag').lower()] += 1
            end = match.end()
    return re.sub(r'\s+', '', ''.join(parts)), tags, unknown

def html_document(data: bytes, document: str, budget: dict | None, malformed: AppError):
    name = document_name(document)
    text = XML_DECLARATION.sub('', decoded_document(data, name), 1)
    parser = etree.HTMLPullParser(events=('start', 'end'), encoding='utf-8', remove_comments=True, remove_pis=True, no_network=True)
    try:
        root = bounded_parse(text.encode(), parser, budget)
    except etree.XMLSyntaxError:
        root = None
    if root is None:
        raise malformed
    source, tags, unknown = character_data(text)
    found = Counter(node.tag.lower() for node in root.iter() if isinstance(node.tag, str))
    bodies = root.xpath('.//*[local-name()="body"]')
    body_match = re.search(r'<body(?:\s[^>]*)?>', text, re.I)
    body_close = re.search(r'</body\s*>', text[body_match.end():], re.I) if body_match else None
    source_body = (text[body_match.end():body_match.end() + body_close.start()]
                   if body_match and body_close else text[body_match.end():] if body_match else '')
    source_body_text = character_data(source_body)[0] if body_match else ''
    parsed_body_text = re.sub(r'\s+', '', ''.join(bodies[0].itertext())) if bodies else ''
    def recovered_name(node):
        return node.tag.rpartition('}')[2].rpartition(':')[2].lower() if isinstance(node.tag, str) else ''
    misplaced_title = bool(bodies and any(
        recovered_name(node) == 'title' and not any(recovered_name(parent) == 'svg' for parent in node.iterancestors())
        for node in bodies[0].iter(etree.Element)))
    if (unknown or not tags['html'] and not tags['svg'] or source != re.sub(r'\s+', '', ''.join(root.itertext()))
            or body_match and source_body_text != parsed_body_text
            or misplaced_title
            or any(found[tag] < tags[tag] for tag in BOUNDARY_TAGS)):
        raise malformed
    for node in root.iter(etree.Element):
        tag = node.tag.rpartition(':')[2]
        node.tag = tag if XML_NAME.fullmatch(tag) else 'span'
        for key in [key for key in node.attrib if key in HTML_ATTRIBUTES or not XML_NAME.fullmatch(key)]:
            value = node.attrib.pop(key)
            if key in HTML_ATTRIBUTES:
                node.set(HTML_ATTRIBUTES[key], value)
    return root

def read_entry(archive: zipfile.ZipFile, entry: zipfile.ZipInfo) -> bytes:
    try:
        return archive.read(entry)
    except (zipfile.BadZipFile, zlib.error, lzma.LZMAError, NotImplementedError, EOFError, OSError, ValueError, RuntimeError) as error:
        name = document_name(entry.filename)
        raise AppError(f'EPUB entry {name} is damaged and cannot be read.', 'damaged_book_entry', document=name) from error

def anchors_of(node) -> list[str]:
    return [value for value in (node.get('id'), node.get('{http://www.w3.org/XML/1998/namespace}id'),
                               node.get('name') if local(node) == 'a' else None) if value]

def whole_number(value, default: int) -> int:
    return int(value) if value and re.fullmatch(r'\s*[+-]?\d{1,9}\s*', value) else default

def list_number(value: int, style: str) -> str:
    if style in ('a', 'A') and value > 0:
        letters = ''
        while value:
            value, rest = divmod(value - 1, 26)
            letters = chr(97 + rest) + letters
        return letters.upper() if style == 'A' else letters
    if style in ('i', 'I') and 0 < value < 4000:
        numerals = ''
        for amount, symbol in ROMAN:
            count, value = divmod(value, amount)
            numerals += symbol * count
        return numerals if style == 'I' else numerals.lower()
    return str(value)

def list_labels(ordered) -> dict:
    items = [child for child in ordered if local(child) == 'li']
    reverse = ordered.get('reversed') is not None
    number = whole_number(ordered.get('start'), len(items) if reverse else 1)
    labels = {}
    for item in items:
        number = whole_number(item.get('value'), number)
        labels[item] = list_number(number, item.get('type') or ordered.get('type') or '1') + '. '
        number += -1 if reverse else 1
    return labels

def remap_links(blocks, targets: dict, link_targets: dict) -> None:
    def remap(match):
        target = targets.get(link_targets.get(match.group(1)))
        return f' data-source-link="{target}"' if target else ''
    for block in blocks:
        block['html'] = re.sub(r' data-source-link="(link[0-9]+)"', remap, block['html'])

def text_of(node) -> str:
    parts = []
    boundaries = {'br', 'hr', 'p', 'div', 'li', 'dt', 'dd', 'tr', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'v'}
    def visit(element):
        if element.get('data-note-ref') is not None or note_reference(element, (element.get(XLINK_HREF) or element.get('href') or '').startswith('#')):
            return
        tag = local(element)
        if tag in boundaries:
            parts.append(' ')
        if element.text:
            parts.append(element.text)
        for child in element:
            visit(child)
            if child.tail:
                parts.append(child.tail)
        if tag in boundaries:
            parts.append(' ')
    visit(node)
    return display_text(''.join(parts))

def markup_text(body: str) -> str:
    safe = etree.fromstring(('<div>' + body + '</div>').encode(), etree.XMLParser(recover=True, resolve_entities=False, no_network=True))
    return (text_of(safe) or display_text(''.join(safe.itertext()))) if safe is not None else ''

def first(root, name: str, default='') -> str:
    found = root.xpath(f'.//*[local-name()="{name}"]')
    return text_of(found[0]) if found else default

def image_asset(data: bytes, asset_root: Path, assets: dict, keep_jpeg: bool = False) -> str | None:
    if len(data) > MAX_ENTRY:
        return None
    try:
        with Image.open(io.BytesIO(data), formats=IMAGE_FORMATS) as im:
            if im.width * im.height > 30_000_000:
                return None
            jpeg = keep_jpeg and im.format in ('JPEG', 'MPO')
            im.load()
            im = ImageOps.exif_transpose(im)
            output = io.BytesIO()
            if jpeg:
                im.convert('L' if im.mode == 'L' else 'RGB').save(output, 'JPEG', quality=90, optimize=True)
            else:
                mode = 'RGBA' if im.mode in ('RGBA', 'LA', 'PA') or 'transparency' in im.info else 'RGB'
                im.convert(mode).save(output, 'PNG')
            raw = output.getvalue()
            if len(raw)>MAX_ENTRY:
                return None
    except Exception:
        return None
    key = digest(raw) + ('.jpg' if jpeg else '.png')
    if key in assets:
        return key
    if sum(asset.get('size', 0) for asset in assets.values()) + len(raw) > MAX_EXPANDED:
        raise AppError('Converted book image assets exceed the 250 MiB limit.')
    atomic_write(asset_root / key, raw)
    assets[key] = {'id': key, 'mime': 'image/jpeg' if jpeg else 'image/png', 'size': len(raw)}
    return key

def class_tokens(node) -> list[str]:
    return [token for token in (node.get('class') or '').split()
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,99}', token)]


def class_formatting(node, class_formats=None) -> list[str]:
    """Return safe display/analysis formatting declared by EPUB CSS classes."""
    styles = []
    mapping = class_formats or {}
    for token in class_tokens(node):
        styles.extend(mapping.get(token, ()))
        if token == 'strong':
            styles.append('strong')
        elif token == 'emphasis':
            styles.append('emphasis')
    inline = (node.get('style') or '').lower()
    if re.search(r'(?:^|;)\s*font-weight\s*:\s*(?:bold|[6-9]00)\b', inline):
        styles.append('strong')
    if re.search(r'(?:^|;)\s*font-style\s*:\s*(?:italic|oblique)\b', inline):
        styles.append('emphasis')
    return list(dict.fromkeys(styles))


def semantic_classes(node, class_formats=None) -> list[str]:
    known = SEMANTIC_CLASSES | set(class_formats or {})
    return [token for token in class_tokens(node) if token in known]


def bound_svg(root, budget: dict) -> bool:
    omitted = False
    for svg in [node for node in root.iter() if local(node) == 'svg' and not any(local(parent) == 'svg' for parent in node.iterancestors())]:
        sizes = [len(value) for node in svg.iter(etree.Element) for key, value in node.attrib.items()
                 if etree.QName(key).localname in SVG_ATTRS or etree.QName(key).localname in ('fill', 'stroke')]
        if max(sizes, default=0) <= MAX_SVG_ATTRIBUTE and budget.get('svg', 0) + sum(sizes) <= MAX_SVG_GEOMETRY:
            budget['svg'] = budget.get('svg', 0) + sum(sizes)
            continue
        omitted = True
        parent, previous, tail = svg.getparent(), svg.getprevious(), svg.tail
        if parent is None:
            svg.clear()
            svg.tag = 'span'
            continue
        if tail and previous is not None:
            previous.tail = (previous.tail or '') + tail
        elif tail:
            parent.text = (parent.text or '') + tail
        parent.remove(svg)
    return omitted

def readable_alt(alt: str, src: str) -> bool:
    alt, name = alt.strip(), posixpath.basename(unquote(urlsplit(src).path))
    return bool(alt) and not FILE_ALT.fullmatch(alt) and alt.casefold() not in (name.casefold(), posixpath.splitext(name)[0].casefold())

def bounded_classes(tokens) -> list[str]:
    tokens = list(dict.fromkeys(tokens))
    room = MAX_CLASS_HINTS - sum(token in SEMANTIC_CLASSES for token in tokens)
    return [token for token in tokens if token in SEMANTIC_CLASSES or (room := room - 1) >= 0]

def sanitized(node, resolve_image, resolve_link=None, class_formats=None) -> str:
    tag = local(node)
    if not tag or dropped(node):
        return ''
    if tag in ('img', 'image'):
        src = node.get('src') or node.get('{http://www.w3.org/1999/xlink}href') or node.get('href') or ''
        key = resolve_image(src)
        alt = html.escape(node.get('alt', ''), quote=True)
        svg_image = tag == 'image' and (etree.QName(node).namespace == SVG_NS or any(local(parent) == 'svg' for parent in node.iterancestors()))
        if svg_image:
            if not key:
                return '<g/>'
            attrs = ''
            for name in ('x', 'y', 'width', 'height', 'transform', 'preserveAspectRatio'):
                value = node.get(name)
                if value and not re.search(r'[<>"\x00-\x1f]|url\s*\(', value, re.I):
                    attrs += f' {name}="{html.escape(value, quote=True)}"'
            return f'<image data-asset="{key}"{attrs}/>'
        return f'<img data-asset="{key}" alt="{alt}" />' if key else f'<span>{alt}</span>' if readable_alt(node.get('alt', ''), src) else ''
    if tag in ('br', 'hr'):
        return f'<{tag}/>'
    content = html.escape(node.text or '')
    for child in node:
        content += sanitized(child, resolve_image, resolve_link, class_formats) + html.escape(child.tail or '')
    href = node.get('href') or node.get(XLINK_HREF)
    link = resolve_link(href) if tag == 'a' and href and resolve_link else None
    tag = {'v': 'p', 'subtitle': 'h3', 'a': 'span', 'emphasis': 'em', 'strikethrough': 's'}.get(tag, tag)
    if tag not in ALLOWED:
        return content
    # Converted FB2 books commonly express emphasis as CSS classes on spans.
    # Preserve the visual meaning without retaining arbitrary class/style data.
    css_styles = class_formatting(node, class_formats)
    if 'emphasis' in css_styles and tag not in ('em', 'i'):
        content = f'<em>{content}</em>'
    if 'strong' in css_styles and tag not in ('strong', 'b'):
        content = f'<strong>{content}</strong>'
    attrs = f' data-source-link="{html.escape(link, quote=True)}"' if link else ''
    if note_reference(node, bool(link) and '#' in href):
        attrs += ' data-note-ref=""'
    for key, value in node.attrib.items():
        key = etree.QName(key).localname
        if key in {'colspan', 'rowspan'} and value.isdigit() and int(value) <= 100:
            attrs += f' {key}="{value}"'
        if key in SVG_ATTRS and not re.search(r'[<>"\x00-\x1f]|url\s*\(', value, re.I):
            attrs += f' {key}="{html.escape(value, quote=True)}"'
        if key in ('fill', 'stroke') and re.fullmatch(r'#[0-9a-fA-F]{3,8}|none|currentColor|[a-zA-Z]{1,20}', value):
            attrs += f' {key}="{value}"'
    if tag == 'math':
        attrs += ' xmlns="http://www.w3.org/1998/Math/MathML"'
    if tag == 'svg':
        attrs += ' xmlns="http://www.w3.org/2000/svg"'
    return f'<{tag}{attrs}>{content}</{tag}>'


def fully_wrapped_text(markup: str, tag: str) -> bool:
    """Return whether every visible text node in a safe fragment is under tag."""
    root = etree.HTML(markup)
    if root is None:
        return False
    found = False
    for node in root.iter():
        if node.text and node.text.strip():
            found = True
            if not any(local(owner) == tag for owner in (node, *node.iterancestors())):
                return False
        if node.tail and node.tail.strip():
            found = True
            parent = node.getparent()
            if parent is None or not any(local(owner) == tag for owner in (parent, *parent.iterancestors())):
                return False
    return found

def element_paths(tree) -> dict:
    def label(node):
        prefix, qname = node.prefix, etree.QName(node)
        return '*' if qname.namespace and not prefix else f'{prefix}:{qname.localname}' if prefix else qname.localname
    def family(node):
        return '*' if label(node) == '*' else (label(node), etree.QName(node).namespace)
    root = tree.getroot()
    paths, pending = {root: '/' + label(root)}, [root]
    while pending:
        parent = pending.pop()
        children = [child for child in parent if isinstance(child.tag, str)]
        totals, seen = Counter(family(child) for child in children), Counter()
        for child in children:
            key = family(child)
            seen[key] += 1
            count, index = (len(children), sum(seen.values())) if key == '*' else (totals[key], seen[key])
            paths[child] = f'{paths[parent]}/{label(child)}' + (f'[{index}]' if count > 1 else '')
            pending.append(child)
    return paths

def extract_blocks(root, chapter_id: str, resolve_image, *, track_source=False, resolve_link=None, class_formats=None, locate=True) -> list[dict]:
    output = []
    tree = root.getroottree()
    positions = {node: index for index, node in enumerate(root.iter())}
    pending_anchors = []
    ordinals = {}
    lead = []
    initials = set()
    texts = functools.cache(lambda: letter_texts(root))
    paths = functools.cache(lambda: element_paths(tree))

    def ordinal(node):
        parent = node.getparent()
        if local(node) != 'li' or parent is None or local(parent) != 'ol':
            return None
        if node not in ordinals:
            ordinals.update(list_labels(parent))
        return ordinals[node]

    def append(node, tag, origin=None):
        origin = node if origin is None else origin
        ancestors = list(origin.iterancestors())
        enclosing = list(dict.fromkeys(
            [FORMAT_TAGS[local(parent)] for parent in ancestors if local(parent) in FORMAT_TAGS] +
            [style for parent in [*ancestors, origin]
             for style in class_formatting(parent, class_formats)]))
        inline = list(dict.fromkeys(
            [FORMAT_TAGS[local(child)] for child in node.iter() if local(child) in FORMAT_TAGS] +
            [style for child in node.iterdescendants()
             for style in class_formatting(child, class_formats)]))
        enclosing_classes = bounded_classes(
            token for parent in [*ancestors, origin]
            for token in semantic_classes(parent, class_formats))
        inline_classes = bounded_classes(
            token for child in node.iterdescendants()
            for token in semantic_classes(child, class_formats))
        render_node = node
        wrap = tag in ('p', 'v', 'subtitle') and enclosing
        if wrap or local(node) != tag:
            render_node = copy.deepcopy(node)
            render_node.tag = tag
        if wrap:
            for style in enclosing:
                wrapper_tag = {'code': 'code', 'emphasis': 'em', 'strong': 'strong'}.get(style)
                if wrapper_tag:
                    wrapper = etree.Element(wrapper_tag)
                    wrapper.text, render_node.text = render_node.text, None
                    for child in list(render_node):
                        wrapper.append(child)
                    render_node.append(wrapper)

        def render(prefix=''):
            target = render_node
            if prefix:
                target = copy.deepcopy(render_node)
                target.text = prefix + (target.text or '')
            body = sanitized(target, resolve_image, resolve_link, class_formats)
            if 'quotation' in enclosing and tag not in ('figure', 'image', 'img', 'svg'):
                body = f'<blockquote>{body}</blockquote>'
            return body, markup_text(body)

        body, text = render()
        if not text and 'data-asset' not in body and '<svg' not in body and tag != 'hr':
            if track_source:
                pending_anchors.extend(a for descendant in node.iter() for a in anchors_of(descendant))
            return
        kind = 'heading' if tag in HEADINGS or tag == 'subtitle' else 'figure' if tag in ('figure', 'image', 'img', 'svg') else 'equation' if tag == 'math' else 'table' if tag == 'table' else 'paragraph'
        if tag == 'hr' or kind in ('paragraph', 'heading') and SEPARATOR.fullmatch(text):
            kind = 'separator'
        elif kind in ('paragraph', 'heading') and any(note_container(item, kind == 'paragraph') for item in (origin, *origin.iterancestors())):
            kind = 'note'
        if lead and text and kind in ('paragraph', 'heading', 'note'):
            body, text = render(lead.pop())
        block = {'id': f'{chapter_id}-p{len(output)+1:05}', 'kind': kind, 'text': text, 'html': body, 'source': origin.get('id', ''), 'assets': re.findall(r'data-asset="([^"]+)"', body)}
        if enclosing or inline or enclosing_classes or inline_classes:
            block['formatting'] = {'enclosing': enclosing, 'inline': inline}
            # Distinguish a fully styled display from one styled phrase. Analysis
            # may use the former as supporting evidence without treating ordinary
            # inline emphasis as a system message.
            full = [style for style, tag_name in (('code', 'code'), ('strong', 'strong'), ('emphasis', 'em'))
                    if fully_wrapped_text(body, tag_name)]
            if full:
                block['formatting']['full'] = full
            if enclosing_classes or inline_classes:
                block['formatting']['classes'] = {'enclosing': enclosing_classes,
                                                  'inline': inline_classes}
        if track_source:
            found = pending_anchors + [a for descendant in node.iter() for a in anchors_of(descendant)]
            block['anchors'] = list(dict.fromkeys(found))
            if locate:
                block['location'] = {'xpath': paths()[origin], 'position': positions.get(origin, 0)}
            pending_anchors.clear()
        previous = output[-1] if output else None
        opening = re.match(r'(?:<blockquote>)?<p\b[^>]*>', body)
        if previous and previous['id'] in initials and opening and kind == 'paragraph' and text[:1].islower() and text[:1] != previous['text'].lower():
            initials.discard(previous['id'])
            rest = body[opening.end():]
            gap = re.match(r'(?:[ \t\r\n]|<[^>]*>)*', rest).group()
            if re.search(r'[\r\n]', re.sub(r'<[^>]*>', '', gap)):
                rest = ''.join(re.findall(r'<[^>]*>', gap)) + rest[len(gap):]
            merged = body[:opening.end()] + html.escape(previous['text']) + rest
            previous.update(text=markup_text(merged), html=merged, assets=block['assets'], **{key: block[key] for key in ('formatting',) if key in block})
            if track_source:
                previous['anchors'] = list(dict.fromkeys(previous['anchors'] + block['anchors']))
            return
        if kind == 'paragraph' and len(text) == 1 and text.isupper() and not block['assets'] and drop_cap(node, origin, text, texts):
            initials.add(block['id'])
        output.append(block)

    def walk(node):
        tag = local(node)
        if dropped(node):
            return
        if track_source:
            pending_anchors.extend(anchors_of(node))
        label = ordinal(node)
        if label:
            lead[:] = [label]
        nested = tag in ('blockquote', 'li', 'dt', 'dd') and any(local(child) in BLOCKS for child in node.iterdescendants())
        if tag in BLOCKS and not nested:
            append(node, 'p' if label or tag in ('dt', 'dd') else tag)
        elif tag == 'div' and not any(local(child) in STRUCTURE for child in node.iterdescendants()):
            append(node, 'p')
        else:
            gather(node)
        if label:
            lead.clear()

    def gather(node):
        pending = etree.Element('p')
        pending.text = node.text
        origin = node

        def flush():
            if len(pending) or (pending.text or '').strip():
                append(pending, 'p', origin)
            pending.clear()

        def add_text(text):
            if text:
                if len(pending):
                    pending[-1].tail = (pending[-1].tail or '') + text
                else:
                    pending.text = (pending.text or '') + text

        for child in node:
            if dropped(child):
                add_text(child.tail)
                continue
            if any(local(descendant) in STRUCTURE for descendant in child.iter()):
                flush()
                walk(child)
                origin = child
                add_text(child.tail)
            else:
                if not len(pending) and not (pending.text or '').strip():
                    origin = child
                pending.append(copy.deepcopy(child))
        flush()

    try:
        walk(root)
    finally:
        positions.clear()
        ordinals.clear()
        texts.cache_clear()
        paths.cache_clear()
    return output

def safe_archive(data: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise AppError('Not a valid EPUB archive.') from error
    entries = archive.infolist()
    for item in entries:
        if not item.flag_bits & 0x800:
            try:
                item.filename = item.filename.encode('cp437').decode('utf-8')
            except UnicodeError:
                pass
    names = [x.filename for x in entries]
    if len(entries) > 10000 or len(names) != len(set(names)) or sum(x.file_size for x in entries) > MAX_EXPANDED:
        raise AppError('EPUB archive exceeds safety limits or has duplicate entries.')
    for item in entries:
        parts = PurePosixPath(item.filename).parts
        if item.filename.startswith('/') or '\\' in item.filename or '..' in parts or ':' in item.filename or item.flag_bits & 1:
            raise AppError('Unsafe or encrypted EPUB entry.')
        if item.file_size > MAX_ENTRY or item.file_size > max(1024 * 1024, item.compress_size * 500):
            raise AppError('EPUB entry exceeds decompression limits.')
    return archive

def epub(data: bytes, asset_root: Path) -> dict:
    from .epub import prepare_epub
    return prepare_epub(data, asset_root)

def fb2_source(data: bytes) -> bytes:
    bom = data[:3] == b'\xef\xbb\xbf'
    body = data[3:] if bom else data
    if body.lstrip()[:5] == b'<?xml':
        body = body.lstrip()
    if bom or body[:2] in (b'\xff\xfe', b'\xfe\xff') or b'\x00' in body[:4] or re.match(rb'<\?xml[^>]*?\sencoding\s*=', body):
        source = (data[:3] if bom else b'') + body
        decoded_document(source, '')  # distinguish invalid bytes/encodings from malformed XML
        return source
    try:
        body.decode('utf-8')
        return body
    except UnicodeDecodeError:
        pass
    sample = body[re.search(rb'[\x80-\xff]', body).start():][:200_000]
    koi8 = 'koi8-u' if UKRAINIAN.search(sample.decode('koi8-u', 'replace')) else 'koi8-r'
    (other, _), (score, encoding) = sorted((cyrillic_score(sample.decode(encoding, 'replace')), encoding) for encoding in ('windows-1251', koi8))
    try:
        body.decode(encoding)
    except UnicodeDecodeError:
        score = 0
    if body.decode('utf-8', 'replace').count('\ufffd') * 20 < len(body.translate(None, bytes(range(128)))) or score < .5 or score - other < .3:
        raise AppError(ENCODING_MESSAGE, 'invalid_book_encoding', document='')
    declaration = re.match(rb'<\?xml[^>]*\?>', body)
    return f'<?xml version="1.0" encoding="{encoding}"?>'.encode() + body[declaration.end() if declaration else 0:]

def cyrillic_score(text: str) -> float:
    words = [word for word in re.findall(r'[^\W\d_]+', text) if not word.isascii()]
    letters = ''.join(word for word in words if CYRILLIC_WORD.fullmatch(word)).lower()
    malformed = sum(len(word) for word in words if not CYRILLIC_WORD.fullmatch(word) and not CYRILLIC_CAPS.fullmatch(word))
    if not letters or malformed > .05 * (len(letters) + malformed) or not .3 <= sum(map('аеёиоуыэюяєіїў'.__contains__, letters)) / len(letters) <= .6:
        return 0.0
    return sum(map('оеаинтсрвлкмдпу'.__contains__, letters)) / sum(not character.isascii() for character in text)

def fb2_structure(bodies) -> None:
    sections = []
    for section in (node for body in bodies for node in body.iter() if local(node) == 'section'):
        children = [child for child in section if isinstance(child.tag, str)]
        names = [local(child) for child in children] + ['']
        runs, pairs, start = [], 0, None
        for index, name in enumerate(names):
            if name == 'empty-line':
                start = index if start is None else start
            elif start is not None:
                if start and names[start - 1] == 'p' and name == 'p':
                    runs.append(children[start:index])
                start = None
            elif index and name == 'p' and names[index - 1] == 'p':
                pairs += 1
        sections.append((runs, pairs))
    dense = lambda runs, pairs, gaps: len(runs) + pairs >= gaps and sum(len(run) == 1 for run in runs) >= .9 * (len(runs) + pairs)
    spacing = dense([run for runs, _ in sections for run in runs], sum(pairs for _, pairs in sections), 30)
    breaks = {run[0] for runs, pairs in sections for run in runs if len(run) > 1 or not (spacing and (not pairs or dense(runs, pairs, 0)))}
    renamed = {'style': 'span', 'text-author': 'p', 'date': 'p'}
    for node in (node for body in bodies for node in body.iter()):
        name = local(node)
        if name in FB2_CLASSES:
            node.set('class', ' '.join(dict.fromkeys([*class_tokens(node), FB2_CLASSES[name]])))
        if name == 'empty-line' or name in renamed:
            node.tag = etree.QName(etree.QName(node).namespace, renamed.get(name) or ('hr' if node in breaks else 'br')).text

def text_sample(nodes, limit: int = 5000) -> str:
    parts, size = [], 0
    for chunk in (chunk for node in nodes for chunk in node.itertext()):
        parts.append(chunk)
        size += len(chunk)
        if size >= limit:
            break
    return ''.join(parts)[:limit]

def fb2(data: bytes, asset_root: Path) -> dict:
    root = xml(fb2_source(data), MAX_UPLOAD + 64, huge=True, encoding_error=True)
    if local(root) != 'FictionBook':
        raise AppError('This XML file is not an FB2 book.')
    bodies = root.xpath('./*[local-name()="body"]')
    fb2_structure(bodies)
    if sum(1 for node in root.iter() if local(node) in BLOCKS) > MAX_BLOCKS:
        raise AppError('FB2 content exceeds the 100,000-passage safety limit.')
    assets, binaries, warnings, budget = {}, {}, [], {}
    if [body for body in bodies if bound_svg(body, budget)]:
        warnings.append(SVG_WARNING)
    for node in root.xpath('./*[local-name()="binary"]'):
        try:
            binaries[node.get('id')] = image_asset(base64.b64decode(''.join((node.text or '').split()), validate=True), asset_root, assets, keep_jpeg=True)
        except (ValueError, TypeError):
            binaries[node.get('id')] = None
        if not binaries[node.get('id')]:
            warnings.append('An embedded image could not be displayed; its source remains in the FB2.')
    resolve_image = lambda source: binaries.get(source.lstrip('#'))
    link_targets = {}
    def resolve_link(source):
        if not source.startswith('#') or len(source) < 2:
            return None
        token = f'link{len(link_targets)}'
        link_targets[token] = unquote(source[1:])
        return token
    title_info = root.xpath('.//*[local-name()="title-info"]')
    info = title_info[0] if title_info else root
    authors = info.xpath('./*[local-name()="author"]')
    names = (display_text(' '.join(first(a, part) for part in ('first-name', 'middle-name', 'last-name'))) or first(a, 'nickname') for a in authors)
    author = ', '.join(dict.fromkeys(name for name in names if name))
    language = first(info, 'lang')[:100] or 'und'
    ru = (language_name(language) or not language_tag(language) and script_language(text_sample(bodies[:1]))) == 'Russian'
    chapters, carried, lead = [], [], []
    def append(node, fallback='', primary=True, parent=None):
        direct_title = node.xpath('./*[local-name()="title"]')
        heading = text_of(direct_title[0]) if direct_title else ''
        row = heading if SEPARATOR.fullmatch(heading) else ''
        inherited = not heading or bool(row)
        if parent is not None and lead and not inherited:
            parent[0] += 1
        pieces = parent if inherited and parent is not None else [0]
        heading = fallback if inherited else heading
        def name():
            return f'{heading} ({pieces[0] + 1})' if heading and pieces[0] else heading
        def untitled(blocks):
            words = next((block['text'] for block in blocks if block['kind'] == 'paragraph'), '').split()
            return f'{row} {" ".join(words[:6])}{"…" if len(words) > 6 else ""}'.strip() if row else f'Глава {len(chapters)+1}' if ru else f'Chapter {len(chapters)+1}'
        container = etree.Element('div')
        container.extend(lead)
        lead.clear()
        carried.extend(anchors_of(node))
        def flush():
            if len(container) or (container.text or '').strip():
                cid = f'c{len(chapters)+1:04}'
                blocks = extract_blocks(container, cid, resolve_image, track_source=True, resolve_link=resolve_link, locate=False)
                if any(block['kind'] != 'separator' for block in blocks):
                    blocks[0]['anchors'] = list(dict.fromkeys(carried + blocks[0]['anchors']))
                    carried.clear()
                    chapters.append({'id': cid, 'title': name() or untitled(blocks), 'source': node.get('id', ''),
                                     'linear': primary, 'role': 'body' if primary else 'auxiliary', 'default_narration': primary,
                                     'blocks': blocks})
                    pieces[0] += 1
                else:
                    carried.extend(anchor for item in container.iter() for anchor in anchors_of(item))
                container.clear()
        def note(section):
            title = section.xpath('./*[local-name()="title"]')
            marker = etree.SubElement(container, 'h3')
            marker.text = text_of(title[0]) if title else ''
            if anchors_of(section):
                marker.set('id', anchors_of(section)[0])
            for child in section:
                if local(child) == 'section':
                    note(child)
                elif local(child) != 'title':
                    container.append(copy.deepcopy(child))
        if node.text and node.text.strip():
            container.text = node.text
        for child in node:
            tag = local(child)
            if tag == 'section' and not primary:
                note(child)
            elif tag == 'section':
                if (container.text or '').strip() or any(local(item) != 'h2' for item in container):
                    flush()
                else:
                    lead.extend(container)
                append(child, heading if local(node) == 'section' else '', primary, pieces if local(node) == 'section' else None)
            elif tag == 'title':
                carried.extend(anchor for item in child.iter() for anchor in anchors_of(item))
                h = etree.SubElement(container, 'h2')
                h.text = text_of(child)
            else:
                container.append(copy.deepcopy(child))
        flush()
    labels = {'notes': 'Примечания' if ru else 'Notes', 'comments': 'Комментарии' if ru else 'Comments'}
    for index, body in enumerate(bodies):
        primary = index == 0 or not body.get('name')
        append(body, '' if primary else labels.get(body.get('name'), body.get('name')), primary)
    targets = {}
    for chapter in chapters:
        for block in chapter['blocks']:
            for anchor in block['anchors']:
                targets.setdefault(anchor, block['id'])
    remap_links((block for chapter in chapters for block in chapter['blocks']), targets, link_targets)
    if any(target not in targets for target in link_targets.values()):
        warnings.append('Some internal links have no displayable target. Their visible text was preserved without an active link.')
    if any(not chapter['default_narration'] for chapter in chapters):
        warnings.append('Notes and comments are preserved for reading but excluded from default narration. Select them explicitly to narrate their text.')
    covers = info.xpath('.//*[local-name()="coverpage"]/*[local-name()="image"]')
    cover = resolve_image(covers[0].get(XLINK_HREF, '')) if covers else None
    return {'title': (first(info, 'book-title') or 'Untitled book')[:500], 'author': (author or 'Unknown author')[:1000],
            'language': language, 'chapters': chapters, 'assets': list(assets.values()),
            'cover': cover, 'warnings': list(dict.fromkeys(warnings))}

def import_book(data: bytes, filename: str, destination: Path) -> dict:
    if not data or len(data) > MAX_UPLOAD:
        raise AppError('Choose an EPUB or FB2 of at most 50 MiB.')
    extension = Path(filename).suffix.lower()
    if extension not in ('.epub', '.fb2'):
        raise AppError('Only EPUB and FB2 are supported.')
    book_id = digest(data)[:32]
    destination.mkdir(parents=True, exist_ok=True)
    folder = destination / book_id
    with tempfile.TemporaryDirectory(prefix='.import-', dir=destination) as temporary:
        staging = Path(temporary)/'book'
        assets = staging/'assets'
        assets.mkdir(parents=True)
        result = epub(data, assets) if extension == '.epub' else fb2(data, assets)
        total = sum(len(b['text']) for c in result['chapters'] for b in c['blocks'])
        if not result['chapters'] or total == 0:
            raise AppError('Book has no readable text.')
        if total > MAX_TEXT:
            raise AppError('Book text exceeds two million characters.')
        atomic_write(staging/('source' + extension), data)
        if not folder.exists():
            staging.rename(folder)
        else:
            for asset in assets.iterdir():
                target = folder/'assets'/asset.name
                if not target.exists():
                    atomic_write(target, asset.read_bytes())
        result.update(id=book_id, filename=Path(filename).name, characters=total, source_hash=digest(data), format=extension[1:])
        from .preparation import preparation_report
        result['preparation'] = preparation_report(result)
        return result

def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Conservative sentence boundaries, retaining every source character.

    Dialogue attributions after a dash stay with the quoted sentence. A
    punctuation mark without a reliable following sentence start stays inside
    the current span, which is preferable to an audible mid-sentence join.
    """
    if not text:
        return []
    starts = [0]
    for match in SENTENCE_BREAK.finditer(text):
        next_word = SENTENCE_START.match(text, match.end())
        if not next_word or not (next_word.group(1).isupper() or next_word.group(1).isdigit()):
            continue
        window = text[max(0, match.start() - 40):match.start()]
        word, letters = WORD_BEFORE.search(window), LETTERS_BEFORE.search(window)
        if (word and word.group().casefold() in ABBREVIATIONS or letters and letters.group().casefold() in ABBREVIATIONS
                or match.group(1) == '.' and letters and len(letters.group()) == 1 and letters.group() != 'я'):
            continue
        starts.append(match.end())
    starts.append(len(text))
    return [(a, b) for a, b in zip(starts, starts[1:]) if a < b]


def split_text(text: str, limit: int = 420) -> list[tuple[int, int, str]]:
    if type(limit) is not int or limit < 1:
        raise AppError('Speech segment limit must be a positive integer.')
    result = []
    start = end = 0
    for a, b in sentence_spans(text):
        if end > start and b - start > limit:
            result.append((start, end, text[start:end]))
            start = a
        end = b
    if end > start:
        result.append((start, end, text[start:end]))
    return result
