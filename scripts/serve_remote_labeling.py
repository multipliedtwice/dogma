"""Password-protected proxy for a temporary HTTPS tunnel to the labeling app.

Bind only to loopback. Point cloudflared at this proxy, never the review server.
The review server remains responsible for conflict-safe, archived label saves.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hmac
import http.client
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


PAGE = '/character-rb2-full/fine-tune-all/'
ALLOWED = (PAGE, '/character-rb2/full/clips/',
           '/manual-labeling/reality-benders-1/audio/')
HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate',
               'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def log_message(self, format, *args):
        # Never log credentials or request headers.
        print(f'{self.command} {urlsplit(self.path).path}', flush=True)

    def reject(self, status, message):
        body = message.encode()
        self.send_response(status)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        if status == 401:
            self.send_header('WWW-Authenticate', 'Basic realm="Rimsky labeling", charset="UTF-8"')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def authenticated(self):
        try:
            scheme, _, value = self.headers.get('Authorization', '').partition(' ')
            return scheme.lower() == 'basic' and hmac.compare_digest(
                base64.b64decode(value, validate=True), self.server.credentials)
        except (binascii.Error, ValueError):
            return False

    def handle_request(self):
        remaining = int(self.server.expires_at - time.time())
        if remaining <= 0:
            self.reject(403, 'This labeling link has expired.')
            return
        parsed = urlsplit(self.path)
        if not self.authenticated():
            self.reject(401, 'Sign in to Rimsky labeling.')
            return
        path = unquote(parsed.path)
        if (not path.startswith(ALLOWED) or '\\' in path
                or '..' in path.split('/') or parsed.netloc or parsed.scheme):
            self.reject(404, 'Not found.')
            return
        body = None
        if self.command == 'POST':
            origin = self.headers.get('Origin', '')
            host = self.headers.get('Host', '')
            if path != PAGE + 'patch-review':
                self.reject(404, 'Not found.')
                return
            if not host or origin != 'https://' + host:
                self.reject(403, 'Unexpected request origin.')
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                size = 0
            if (not 0 < size <= 15_000_000
                    or self.headers.get('Transfer-Encoding')):
                self.reject(400, 'Invalid review size.')
                return
            body = self.rfile.read(size)
            if len(body) != size:
                self.reject(400, 'Incomplete review.')
                return
        headers = {key: value for key, value in self.headers.items()
                   if key.lower() in {'accept', 'accept-encoding', 'content-type',
                                      'range', 'if-range', 'if-none-match',
                                      'if-modified-since'}}
        upstream_host = f'127.0.0.1:{self.server.upstream_port}'
        headers['Host'] = upstream_host
        if self.command == 'POST':
            # The public origin was validated above. The local server checks
            # its own HTTP origin; forwarding HTTPS unchanged would reject saves.
            headers['Origin'] = 'http://' + upstream_host
        connection = http.client.HTTPConnection('127.0.0.1',
                                                self.server.upstream_port, timeout=60)
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in HOP_HEADERS | {'set-cookie', 'cache-control'}:
                    self.send_header(key, value)
            self.send_header('Cache-Control', 'private, no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            if self.command != 'HEAD':
                while block := response.read(256 * 1024):
                    self.wfile.write(block)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (OSError, http.client.HTTPException):
            # Do not expose local filesystem details or credentials.
            if 'response' not in locals():
                self.reject(502, 'The local labeling server is unavailable.')
        finally:
            connection.close()

    do_GET = do_HEAD = do_POST = handle_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=1440)
    parser.add_argument('--upstream-port', type=int, default=1439)
    parser.add_argument('--hours', type=float, default=12)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--username', default='rimsky')
    parser.add_argument('--password-file', type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.hours <= 24:
        parser.error('--hours must be between 0 and 24')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    password = args.password_file.read_text(encoding='utf-8-sig').strip()
    if not password or ':' in args.username:
        parser.error('Supply a nonempty password and a username without colons')
    server.credentials = f'{args.username}:{password}'.encode('utf-8')
    server.expires_at = time.time() + args.hours * 3600
    server.upstream_port = args.upstream_port
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps({
        'proxy_port': server.server_port, 'upstream_port': args.upstream_port,
        'username': args.username, 'expires_at': server.expires_at,
        'page': PAGE,
    }, indent=2), encoding='utf-8')
    print(f'Private labeling proxy: 127.0.0.1:{server.server_port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
