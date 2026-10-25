"""Password-protected gateway for the production listener UI.

The public tunnel terminates here. Static production UI files and a deliberately
small part of the local runtime API are relayed to a loopback-only listener
runtime. Its bearer token never reaches the browser.
"""
import argparse
import base64
import hmac
import http.client
import ipaddress
import json
import posixpath
from pathlib import Path
from urllib.parse import parse_qs, urlencode, unquote, urlsplit

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from local_home import serve_home


PAGE = '/character-rb2-full/fine-tune-all/elirm-iv-mvp/'
PRODUCTION_PAGE = PAGE + 'ch123/'
MEDIA = '/character-rb2/full/clips/elirm-iv-mvp/'
STATIC_PATHS = ('assets/',)
STATIC_FILES = frozenset({'column.svg', 'favicon.ico'})
READ_OPS = frozenset({
    'library', 'book', 'generation', 'generation_poll', 'visuals',
    'visuals_poll', 'visual_brief', 'illustration_history',
    'illustration_status', 'settings', 'health', 'stress_reports',
    'music_cues', 'recommendation_genres', 'playback_streams',
})
WRITE_OPS = frozenset({'report_stress', 'save_position', 'save_reading_position'})
LISTENER_SETTING_KEYS = frozenset({
    'theme', 'font_size', 'line_height', 'language', 'playback_rate',
    'volume', 'follow', 'music', 'music_volume',
})
MAX_API_BODY = 64 * 1024
LAN_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
))


def clean_path(value):
    """Normalize URL paths without allowing encoded separators to escape scope."""
    decoded = unquote(urlsplit(value).path)
    if '\\' in decoded or '\x00' in decoded:
        return ''
    return posixpath.normpath(decoded)


def private_cache_control(upstream):
    """Keep the runtime's cache policy but never let a response become shared-cacheable."""
    directives = [item.strip() for item in (upstream or '').split(',') if item.strip()]
    names = {item.split('=')[0].strip().lower() for item in directives}
    if not directives or 'no-store' in names or 'public' in names or 's-maxage' in names:
        return 'private, no-store'
    if 'private' not in names:
        directives.insert(0, 'private')
    return ', '.join(directives)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        return

    def authorize(self):
        try:
            client = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            client = None
        # Direct home-LAN traffic is trusted. Cloudflared connects to this
        # gateway through loopback, so public requests still require Basic auth.
        if client is not None and any(client in network for network in LAN_NETWORKS):
            return True
        supplied = self.headers.get('Authorization', '').encode()
        if hmac.compare_digest(supplied, self.server.authorization):
            return True
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Rimsky listening", charset="UTF-8"')
        self.send_header('Content-Length', '0')
        self.end_headers()
        return False

    def same_origin(self):
        origin = urlsplit(self.headers.get('Origin', ''))
        host = self.headers.get('Host', '')
        return origin.scheme in ('http', 'https') and origin.netloc.casefold() == host.casefold()

    def relay_response(self, response, head=False):
        self.send_response(response.status)
        upstream_cache = ''
        for key, value in response.getheaders():
            if key.lower() == 'cache-control':
                upstream_cache = value
            elif key.lower() not in ('connection', 'server', 'date', 'transfer-encoding',
                                     'keep-alive', 'upgrade', 'proxy-authenticate'):
                self.send_header(key, value)
        self.send_header('Cache-Control', private_cache_control(upstream_cache))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if not head:
            while chunk := response.read(65536):
                self.wfile.write(chunk)

    def redirect(self, location):
        self.send_response(302)
        self.send_header('Location', location)
        self.send_header('Cache-Control', 'private, no-store')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def runtime_request(self, method, path, body=None, authenticated=False, head=False):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.runtime_port, timeout=60)
        try:
            headers = {}
            if authenticated:
                headers['Authorization'] = 'Bearer ' + self.server.runtime_token
            if body is not None:
                headers['Content-Type'] = 'application/json'
                headers['Content-Length'] = str(len(body))
            for key in ('Range', 'If-Range', 'If-None-Match', 'Accept-Encoding'):
                if self.headers.get(key):
                    headers[key] = self.headers[key]
            connection.request(method, path, body=body, headers=headers)
            self.relay_response(connection.getresponse(), head)
        except (ConnectionError, TimeoutError, OSError):
            self.send_error(502, 'The private listener runtime is unavailable.')
        finally:
            connection.close()

    def runtime_selection(self):
        """Resolve the current book output; a launch-time generation is no pin."""
        body = json.dumps({'op': 'library', 'data': {}}).encode()
        connection = http.client.HTTPConnection('127.0.0.1', self.server.runtime_port, timeout=10)
        try:
            connection.request('POST', '/api', body=body, headers={
                'Authorization': 'Bearer ' + self.server.runtime_token,
                'Content-Type': 'application/json',
                'Content-Length': str(len(body)),
            })
            response = connection.getresponse()
            raw = response.read()
            if response.status != 200:
                return None
            books = json.loads(raw)
            if not isinstance(books, list):
                return None
            configured = self.server.listener_book_id
            selected = [book for book in books if book.get('id') == configured] if configured else books
            if len(selected) != 1:
                return None
            book = selected[0]
            generations = book.get('generations')
            if not isinstance(generations, list) or not generations:
                return None
            current = book.get('current_generation_id')
            if current:
                generation = next((row for row in generations if row.get('id') == current), None)
                if generation is None:
                    return None
            else:
                generation = next((row for row in generations if row.get('status') in (None, 'ready', 'rendering')), None)
            return (book.get('id'), generation.get('id')) if generation else None
        except (ConnectionError, TimeoutError, OSError, ValueError, TypeError):
            return None
        finally:
            connection.close()

    def production_get(self, head=False):
        parsed = urlsplit(self.path)
        decoded = unquote(parsed.path)
        path = clean_path(self.path)
        if path == PRODUCTION_PAGE.rstrip('/') and not parsed.path.endswith('/'):
            query = ('?' + parsed.query) if parsed.query else ''
            self.redirect(PRODUCTION_PAGE + query)
            return True
        if path == PRODUCTION_PAGE.rstrip('/') and parsed.path.endswith('/'):
            query = parse_qs(parsed.query)
            # Old bookmarks still open the current book. Internal physical hashes
            # invalidate audio caches; listeners never choose an output version.
            if 'generation' in query:
                query.pop('generation')
                query['listener'] = ['1']
                self.redirect(PRODUCTION_PAGE + (('?' + urlencode(query, doseq=True)) if query else ''))
                return True
            if query.get('book', [''])[0] and query.get('listener') != ['1']:
                query['listener'] = ['1']
                self.redirect(PRODUCTION_PAGE + '?' + urlencode(query, doseq=True))
                return True
            if not query.get('book', [''])[0]:
                selected = self.runtime_selection()
                if selected and all(selected):
                    self.redirect(PRODUCTION_PAGE + '?' + urlencode({'listener': '1', 'book': selected[0]}))
                    return True
            self.runtime_request('HEAD' if head else 'GET', '/', head=head)
            return True
        relative = decoded[len(PRODUCTION_PAGE):] if decoded.startswith(PRODUCTION_PAGE) else None
        if relative is not None and (relative.startswith(STATIC_PATHS) or relative in STATIC_FILES):
            target = '/' + relative + (('?' + parsed.query) if parsed.query else '')
            self.runtime_request('HEAD' if head else 'GET', target, head=head)
            return True
        if relative == 'asset':
            target = '/asset' + (('?' + parsed.query) if parsed.query else '')
            self.runtime_request('HEAD' if head else 'GET', target, authenticated=True, head=head)
            return True
        if decoded.startswith(PRODUCTION_PAGE):
            self.send_error(404)
            return True
        return False

    def legacy_get(self, head=False):
        path = clean_path(self.path)
        allowed = path.startswith((PAGE, MEDIA)) or path == PAGE.rstrip('/')
        # The production route above owns ch123. Remaining preview paths stay
        # available temporarily while old review links are retired.
        if not allowed or path.startswith(PRODUCTION_PAGE):
            self.send_error(404)
            return
        connection = http.client.HTTPConnection('127.0.0.1', self.server.upstream_port, timeout=60)
        try:
            headers = {key: self.headers[key] for key in ('Range', 'If-Range', 'If-None-Match', 'Accept-Encoding') if self.headers.get(key)}
            connection.request('HEAD' if head else 'GET', self.path, headers=headers)
            self.relay_response(connection.getresponse(), head)
        except (ConnectionError, TimeoutError, OSError):
            self.send_error(502, 'The legacy preview server is unavailable.')
        finally:
            connection.close()

    def forward_get(self, head=False):
        if not self.authorize():
            return
        if serve_home(self,role='gateway',head_only=head):
            return
        if not self.production_get(head):
            self.legacy_get(head)

    def do_GET(self):
        self.forward_get()

    def do_HEAD(self):
        self.forward_get(True)

    def do_POST(self):
        if not self.authorize():
            return
        if clean_path(self.path) != (PRODUCTION_PAGE + 'api') or urlsplit(self.path).query:
            self.send_error(405)
            return
        if not self.same_origin():
            self.send_error(403)
            return
        if not self.headers.get('Content-Type', '').casefold().startswith('application/json'):
            self.send_error(415)
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self.send_error(400)
            return
        if not 0 < size <= MAX_API_BODY:
            self.send_error(413)
            return
        body = self.rfile.read(size)
        try:
            request = json.loads(body)
            if not isinstance(request, dict) or set(request) - {'op', 'data'} or not isinstance(request.get('data', {}), dict):
                raise ValueError
            op = request.get('op')
            data = request.get('data', {})
        except (UnicodeDecodeError, ValueError, TypeError):
            self.send_error(400)
            return
        allowed = op in READ_OPS or op in WRITE_OPS
        if op == 'update_settings':
            allowed = bool(data) and not (set(data) - LISTENER_SETTING_KEYS)
        if not allowed:
            self.send_error(403, 'This operation is unavailable in the public listener.')
            return
        self.runtime_request('POST', '/api', body=body, authenticated=True)

    def do_OPTIONS(self):
        self.send_error(405)

    def do_PUT(self):
        self.send_error(405)

    def do_PATCH(self):
        self.send_error(405)

    def do_DELETE(self):
        self.send_error(405)


def runtime_token(path):
    token = path.read_text(encoding='ascii').strip()
    if not (40 <= len(token) <= 128 and all(char.isalnum() or char in '-_' for char in token)):
        raise ValueError('The listener runtime token file is invalid.')
    return token


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=1442)
    parser.add_argument('--upstream-port', type=int, default=1437)
    parser.add_argument('--runtime-port', type=int, default=1443)
    parser.add_argument('--runtime-token-file', type=Path, required=True)
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--book-id', default='')
    parser.add_argument('--generation-id', default='')
    args = parser.parse_args()
    credentials = json.loads(args.credentials.read_text('utf8'))
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.authorization = b'Basic ' + base64.b64encode((credentials['username'] + ':' + credentials['password']).encode())
    server.upstream_port = args.upstream_port
    server.runtime_port = args.runtime_port
    server.runtime_token = runtime_token(args.runtime_token_file)
    server.listener_book_id = args.book_id
    server.listener_generation_id = args.generation_id
    print('Production listening gateway on', args.host, args.port, flush=True)
    server.serve_forever()
