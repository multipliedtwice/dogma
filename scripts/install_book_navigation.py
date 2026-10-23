"""Install book switching without rebuilding candidates or touching reviews."""
from pathlib import Path
import gzip
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from rimsky.util import atomic_write

SELECTOR = '<label>Book <select id="book" aria-label="Book" disabled><option value="book1">Book 1</option><option value="book2">Book 2</option></select></label>\n'


def write(path, text):
    data = text.encode('utf-8')
    atomic_write(path, data)
    compressed = path.with_name(path.name + '.gz')
    if compressed.exists():
        atomic_write(compressed, gzip.compress(data, mtime=0))


def main():
    for folder in ('character-rb1', 'character-rb2', 'character-rb2-full'):
        board = ROOT / 'training-work' / folder / 'fine-tune-all'
        if not board.exists():
            continue
        for name in ('index.html', 'buckets.html'):
            page = board / name
            if not page.exists():
                continue
            text = page.read_text(encoding='utf-8')
            if 'id="book"' not in text:
                marker = '<section><div class="toolbar">\n'
                if text.count(marker) != 1:
                    raise ValueError(f'Cannot locate bucket toolbar: {page}')
                text = text.replace(marker, marker + SELECTOR)
            if 'src="book-navigation.js"' not in text:
                text, count = re.subn(r'<script src="bucket-review\.js(?:\?[^"<>]*)?">',
                                     lambda match: '<script src="book-navigation.js"></script>' + match.group(), text)
                if count != 1:
                    raise ValueError(f'Cannot locate bucket script: {page}')
            write(page, text)
        script = board / 'bucket-review.js'
        text = script.read_text(encoding='utf-8')
        if 'initialiseBookNavigation(' not in text:
            marker = '\n}\ninit().catch('
            if text.count(marker) != 1:
                raise ValueError(f'Cannot locate bucket initialization: {script}')
            text = text.replace(marker, '\n  initialiseBookNavigation(store,stopSequence);' + marker)
            write(script, text)
        write(board / 'book-navigation.js', (ROOT / 'scripts/book_navigation.js').read_text(encoding='utf-8'))
    print('Installed book selector; review and candidate data were not changed.')


if __name__ == '__main__':
    main()
