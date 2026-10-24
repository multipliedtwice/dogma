"""Read-only loopback listening server exposing only sanitized demo/public assets."""
from __future__ import annotations
import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import unquote, urlsplit

BOOKS = {'en': ('en-gift-of-magi', 'The Gift of the Magi', 'English'),
         'ru': ('ru-chameleon', '\u0425\u0430\u043c\u0435\u043b\u0435\u043e\u043d', 'Russian')}
HASH_FILE = re.compile(r'(?:[a-f0-9]{64}\.(?:mp3|wav|json)|player-[a-f0-9]{64}\.js|recognition-[a-f0-9]{64}\.json)\Z')
TYPES = {'.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.json': 'application/json; charset=utf-8',
         '.js': 'text/javascript; charset=utf-8', '.html': 'text/html; charset=utf-8'}
LIGHT_CSS = (':root{color-scheme:light}body{background:#fbfaf7;color:#252c28}'
             'a{color:#22634f}a:hover{color:#154a73}'
             'article{background:#fff;border-color:#d6ded7}'
             '.language{color:#58675e}audio{color-scheme:light}'
             'p.active,[data-start].active{background:#e3efe6;color:#183b29}')


def light_page(payload):
    """Override presentation in the HTTP response, leaving frozen pages intact."""
    override = ('<style data-dogma-listening-theme="light">' + LIGHT_CSS + '</style>').encode('utf-8')
    styles = list(re.finditer(br'</style\s*>', payload, re.I))
    head = re.search(br'</head\s*>', payload, re.I)
    position = styles[-1].end() if styles else head.start() if head else len(payload)
    return payload[:position] + override + payload[position:]


def no_links(path):
    """Reject Windows junctions/reparse points as well as ordinary symlinks."""
    path = Path(os.path.abspath(path))
    for current in [*reversed(path.parents), path]:
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise PermissionError('Linked paths are not served')
    return path


def public_path(root, language, filename):
    if language not in BOOKS or filename not in ('index.html', 'latest.json') and not HASH_FILE.fullmatch(filename):
        raise PermissionError('Not a public listening asset')
    root = no_links(root)
    folder = root / BOOKS[language][0] / 'public'
    path = no_links(folder / filename)
    if path.resolve().parent != folder.resolve():
        raise PermissionError('Path leaves its public directory')
    return path


def open_public(root, language, filename):
    path = public_path(root, language, filename)
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        no_links(path)
        if not stat.S_ISREG(info.st_mode):
            raise PermissionError('Only regular public files are served')
        return os.fdopen(descriptor, 'rb'), info
    except Exception:
        os.close(descriptor)
        raise


def byte_range(header, size):
    """One RFC-style inclusive byte range; reject multiple/unsatisfiable ranges."""
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header.strip())
    if not match or not size or not any(match.groups()):
        raise ValueError('Unsatisfiable range')
    first, last = match.groups()
    if first:
        start, end = int(first), int(last) if last else size-1
        if start >= size or end < start:
            raise ValueError('Unsatisfiable range')
        return start, min(end, size-1)
    suffix = int(last)
    if suffix <= 0:
        raise ValueError('Unsatisfiable range')
    return max(0, size-suffix), size-1


def sanitized_status(root, language):
    try:
        with open_public(root, language, 'latest.json')[0] as stream:
            data = json.load(stream)
        filename = data.get('mp3')
        if not isinstance(filename, str) or not re.fullmatch(r'[a-f0-9]{64}\.mp3', filename):
            return None
        with open_public(root, language, filename)[0]:
            pass
        with open_public(root, language, 'index.html')[0]:
            pass
        # Never serialize the original metadata or any arbitrary supplied fields.
        return {'complete': data.get('complete') is True,
                'verified_units': max(0, int(data.get('verified_units', 0))),
                'total_units': max(0, int(data.get('total_units', 0))),
                'mp3': filename}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def hub(root):
    cards = []
    for language, (_, title, locale) in BOOKS.items():
        status = sanitized_status(root, language)
        text = 'Preparing narration. A verified preview will appear here when available.'
        link = ''
        if status:
            text = ('Complete' if status['complete'] else 'Verified preview') + ' — %s/%s passages' % (status['verified_units'], status['total_units'])
            link = '<p><a href="/%s/">Listen and read</a> · <a href="/%s/%s" download>Download MP3</a></p>' % (language, language, status['mp3'])
        cards.append('<article><p class="language">'+locale+'</p><h2>'+html.escape(title)+'</h2><p>'+html.escape(text)+'</p>'+link+'</article>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Dogma audiobook demos</title><style>body{max-width:850px;margin:3rem auto;padding:1rem;'
            'font:18px/1.6 system-ui}article{border:1px solid;'
            'border-radius:12px;margin:1rem 0;padding:1.5rem}' + LIGHT_CSS + '</style>'
            '<h1>Dogma audiobook demos</h1><p>Original public-domain stories. Machine-verified narration; '
            'human listening approval is not recorded. Refresh for the latest preview.</p>'+''.join(cards)+'</html>').encode('utf-8')


class ListeningServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, root, record=True):
        self.root = no_links(root)
        self.started = time.time()
        self.record_enabled = record
        super().__init__(address, ListeningHandler)
        self.url = 'http://%s:%s/' % (self.server_address[0], self.server_address[1])
        self.record('running')

    def record(self, status):
        if not self.record_enabled:
            return
        path = no_links(self.root / 'queue/listening-server.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = no_links(path.with_suffix('.json.tmp'))
        temporary.write_text(json.dumps({'schema': 1, 'status': status, 'pid': os.getpid(), 'url': self.url,
                                          'started_unix': self.started, 'updated_unix': time.time()}, indent=2)+'\n', 'utf-8')
        os.replace(temporary, path)

    def service_actions(self):
        self.record('running')

    def server_close(self):
        super().server_close()
        if hasattr(self, 'url'):
            self.record('stopped')


class ListeningHandler(BaseHTTPRequestHandler):
    server_version = 'DogmaListening/1'
    sys_version = ''

    def log_message(self, *args):
        pass  # Do not write private request paths or source passages into logs.

    def send_headers(self, status, size, kind, cache='no-store', content_range=None):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if kind.startswith('audio/'):
            self.send_header('Accept-Ranges', 'bytes')
        if content_range:
            self.send_header('Content-Range', content_range)
        self.end_headers()

    def reject(self, status):
        self.send_headers(status, 0, 'text/plain; charset=utf-8')

    def serve(self, head=False):
        try:
            route = unquote(urlsplit(self.path).path, errors='strict')
        except (UnicodeError, ValueError):
            self.reject(400)
            return
        if route == '/':
            payload = hub(self.server.root)
            self.send_headers(200, len(payload), 'text/html; charset=utf-8')
            if not head:
                self.wfile.write(payload)
            return
        match = re.fullmatch(r'/(en|ru)/(?:([A-Za-z0-9_.-]+))?', route)
        if not match:
            self.reject(404)
            return
        language, filename = match.groups()
        filename = filename or 'index.html'
        try:
            stream, info = open_public(self.server.root, language, filename)
        except PermissionError:
            self.reject(403)
            return
        except OSError:
            self.reject(404)
            return
        with stream:
            kind = TYPES[Path(filename).suffix]
            if filename == 'index.html':
                payload = light_page(stream.read())
                self.send_headers(200, len(payload), kind, cache='no-store')
                if not head:
                    try:
                        self.wfile.write(payload)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                return
            size, start, end, status = info.st_size, 0, info.st_size-1, 200
            range_header = self.headers.get('Range')
            content_range = None
            if range_header and kind.startswith('audio/'):
                try:
                    start, end = byte_range(range_header, size)
                except ValueError:
                    self.send_headers(416, 0, kind, content_range='bytes */'+str(size))
                    return
                status, content_range = 206, 'bytes %s-%s/%s' % (start, end, size)
            length = max(0, end-start+1)
            cache = 'public, max-age=31536000, immutable' if HASH_FILE.fullmatch(filename) else 'no-store'
            self.send_headers(status, length, kind, cache, content_range)
            if not head:
                stream.seek(start)
                remaining = length
                try:
                    while remaining:
                        chunk = stream.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

    def do_GET(self):
        self.serve()

    def do_HEAD(self):
        self.serve(head=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    try:
        address = ipaddress.ip_address(args.host)
    except ValueError:
        parser.error('Use a numeric loopback host such as 127.0.0.1')
    if not address.is_loopback or address.version != 4:
        parser.error('Local listening requires an IPv4 loopback address')
    if not 0 <= args.port <= 65535:
        parser.error('Port must be 0..65535')
    server = ListeningServer((args.host, args.port), args.root.absolute())
    print(server.url, flush=True)
    try:
        server.serve_forever(poll_interval=2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
