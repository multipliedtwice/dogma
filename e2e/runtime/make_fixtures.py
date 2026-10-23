from __future__ import annotations

import argparse
import io
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from PIL import Image

EPUB_TITLE = 'E2E Harbor Book'
FB2_TITLE = 'E2E Forest Book'
LANTERN_TEXT = 'R&D <lantern> shines beside the water. Café readers say: «Добрый вечер». '
REVIEW_TITLE = 'E2E Review Book'
REVIEW_SPEAKER = 'Anna'
REVIEW_RADIO = '“Tower, this is Anna, we are landing now.”'
REVIEW_RADIO_QUOTE = 'Tower, this is Anna'
REVIEW_MIXED = '“Hold the rope,” Anna said, and a stranger called back, “Who is there?”'
REVIEW_CHAPTERS = (
    ('night', 'Radio night', ('The night shift began in the quiet control room.', REVIEW_RADIO, 'The lights on the runway blinked twice.',
                              'Rain moved across the field for a long time.', REVIEW_MIXED, 'Nobody answered from the dark hangar.')),
    ('morning', 'Calm morning', ('Morning came slowly over the empty airfield.', 'The coffee in the tower had gone cold.')),
)


def image_bytes():
    image = Image.new('RGB', (24, 24), (40, 90, 160))
    for y in range(8, 16):
        for x in range(8, 16):
            image.putpixel((x, y), (240, 200, 80))
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def epub_bytes():
    paragraphs = ''.join(
        f'<p id="harbor-{number}">Harbor passage {number}. '
        'The reader watches the quiet boats near the stone steps. '
        'Each boat has a different name, and the open book stays on the wooden table. '
        'A bell sounds across the water while the morning light fills the harbor.</p>'
        for number in range(1, 9)
    )
    entries = {
        'mimetype': 'application/epub+zip',
        'META-INF/container.xml': '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        'OEBPS/content.opf': f'''<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0" unique-identifier="book-id">
<metadata><dc:identifier id="book-id">rimskify-e2e-harbor</dc:identifier><dc:title>{EPUB_TITLE}</dc:title><dc:creator>Synthetic Fixture</dc:creator><dc:language>en</dc:language><meta property="dcterms:modified">2020-01-01T00:00:00Z</meta></metadata>
<manifest><item id="harbor" href="harbor.xhtml" media-type="application/xhtml+xml"/><item id="lantern" href="lantern.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest><spine><itemref idref="harbor"/><itemref idref="lantern"/></spine></package>''',
        'OEBPS/nav.xhtml': '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol><li><a href="harbor.xhtml">Harbor morning</a></li><li><a href="lantern.xhtml">Lantern evening</a></li></ol></nav></body></html>',
        'OEBPS/harbor.xhtml': f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Harbor morning</title></head><body><h1 id="harbor">Harbor morning</h1><p>Start beside the harbor. <a href="lantern.xhtml#lantern-note">Read the lantern note</a>.</p>{paragraphs}</body></html>',
        'OEBPS/lantern.xhtml': f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Lantern evening</title></head><body><h1 id="lantern">Lantern evening</h1><p id="lantern-note">{escape(LANTERN_TEXT)}The lantern note ends here.</p><p>The second evening paragraph follows the small boat home.</p><p>The final evening paragraph closes the book without closing the library.</p></body></html>',
    }
    return archive_bytes(entries)


def archive_bytes(entries):
    output = io.BytesIO()
    with ZipFile(output, 'w') as archive:
        for name, text in entries.items():
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED if name == 'mimetype' else ZIP_DEFLATED
            archive.writestr(info, text.encode('utf-8'))
    return output.getvalue()


def review_epub_bytes():
    items = ''.join(f'<item id="{name}" href="{name}.xhtml" media-type="application/xhtml+xml"/>' for name, _, _ in REVIEW_CHAPTERS)
    spine = ''.join(f'<itemref idref="{name}"/>' for name, _, _ in REVIEW_CHAPTERS)
    toc = ''.join(f'<li><a href="{name}.xhtml">{title}</a></li>' for name, title, _ in REVIEW_CHAPTERS)
    return archive_bytes({
        'mimetype': 'application/epub+zip',
        'META-INF/container.xml': '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        'OEBPS/content.opf': f'''<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0" unique-identifier="book-id">
<metadata><dc:identifier id="book-id">rimskify-e2e-review</dc:identifier><dc:title>{REVIEW_TITLE}</dc:title><dc:creator>Synthetic Fixture</dc:creator><dc:language>en</dc:language><meta property="dcterms:modified">2020-01-01T00:00:00Z</meta></metadata>
<manifest>{items}<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/></manifest><spine>{spine}</spine></package>''',
        'OEBPS/nav.xhtml': f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol>{toc}</ol></nav></body></html>',
        **{f'OEBPS/{name}.xhtml': f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head><body><h1 id="{name}">{title}</h1>'
           + ''.join(f'<p id="{name}-{number}">{escape(text)}</p>' for number, text in enumerate(paragraphs, 1)) + '</body></html>'
           for name, title, paragraphs in REVIEW_CHAPTERS},
    })


def fb2_bytes():
    return f'''<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info><genre>prose</genre><author><first-name>Synthetic</first-name><last-name>Fixture</last-name></author><book-title>{FB2_TITLE}</book-title><lang>en</lang></title-info></description><body><section id="pine"><title><p>Pine trail</p></title><p>A reader follows the <emphasis>pine trail</emphasis> into the forest.</p><p>{escape(LANTERN_TEXT)}The path turns toward the river.</p></section><section id="river"><title><p>River crossing</p></title><p>The bridge crosses a narrow river.</p><p>The last page waits on the other side.</p></section></body></FictionBook>'''.encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description='Write generated, synthetic E2E inputs. No user books.')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, content in {
        'harbor.epub': epub_bytes(), 'forest.fb2': fb2_bytes(), 'review.epub': review_epub_bytes(), 'paragraph.png': image_bytes(),
        'invalid.txt': b'Not an ebook.', 'broken.epub': b'Not a ZIP archive.',
        'broken.fb2': b'<FictionBook><broken>',
    }.items():
        (args.output / name).write_bytes(content)


if __name__ == '__main__':
    main()
