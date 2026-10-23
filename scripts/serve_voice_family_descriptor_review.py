"""Serve and durably save the narrator voice-family descriptor review."""
from __future__ import annotations

import argparse
import functools
import json
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from voice_family_registry import (DESCRIPTORS, RegistryConflict, ensure_archive,
                                   read_review, save_decision)


ROOT = Path(__file__).resolve().parents[1]
PAGE = '/voice-family-registry-evidence/vlad-rimsky-v1/'
BOARD = ROOT / 'training-work/voice-family-registry-evidence/vlad-rimsky-v1'
ARCHIVE = ROOT / 'manual-labeling/voice-family-registries/vlad-rimsky-v1'
class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      '.html': 'text/html; charset=utf-8',
                      '.json': 'application/json; charset=utf-8',
                      '.js': 'text/javascript; charset=utf-8', '.wav': 'audio/wav'}

    def allowed(self) -> bool:
        path = unquote(urlsplit(self.path).path)
        return ('..' not in Path(path).parts and
                (path.startswith(PAGE) or
                 path.startswith('/manual-labeling/reality-benders-1/audio/') or
                 path.startswith('/higgs-production/datasets/reviewed-20261012-v1/audio/')))

    def translate_path(self, path: str) -> str:
        clean = unquote(urlsplit(path).path)
        if clean.startswith('/manual-labeling/'):
            return str(ROOT / clean.lstrip('/'))
        return super().translate_path(path)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if not self.allowed():
            self.send_error(404)
            return
        if path == PAGE + 'review':
            self.reply(200, read_review(ROOT))
            return
        if path.lower().endswith('.wav'):
            self.send_range(False)
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if not self.allowed():
            self.send_error(404)
            return
        if urlsplit(self.path).path.lower().endswith('.wav'):
            self.send_range(True)
            return
        super().do_HEAD()

    def send_range(self, head_only: bool) -> None:
        source = Path(self.translate_path(self.path)).resolve()
        audio_roots = [(ROOT / 'manual-labeling/reality-benders-1/audio').resolve(),
                       (ROOT / 'training-work/higgs-production/datasets/reviewed-20261012-v1/audio').resolve()]
        if not any(source.is_relative_to(root) for root in audio_roots) or not source.is_file():
            self.send_error(404)
            return
        size, start, end = source.stat().st_size, 0, source.stat().st_size - 1
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            if not match or not any(match.groups()):
                self.send_error(416)
                return
            if match.group(1):
                start = int(match.group(1))
                if match.group(2):
                    end = min(int(match.group(2)), end)
            else:
                start = max(0, size - int(match.group(2)))
            if start >= size or end < start:
                self.send_error(416)
                return
        self.send_response(206 if requested else 200)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        if requested:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if head_only:
            return
        with source.open('rb') as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def do_POST(self) -> None:
        if urlsplit(self.path).path != PAGE + 'save-decision':
            self.send_error(404)
            return
        try:
            origin = self.headers.get('Origin')
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme != 'http' or parsed.netloc.casefold() != self.headers.get('Host', '').casefold():
                    raise ValueError('Unexpected request origin')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 100_000:
                raise ValueError('Invalid request size')
            result = save_decision(ROOT, json.loads(self.rfile.read(size)))
            self.reply(200, {'saved': True, 'review': result})
        except RegistryConflict as exc:
            self.reply(409, {'error': str(exc)})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self.reply(400, {'error': str(exc)})
        except OSError as exc:
            self.reply(500, {'error': str(exc)})

    def reply(self, status: int, value: dict) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def end_headers(self) -> None:
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=1440)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    if not (BOARD / 'index.html').is_file():
        raise FileNotFoundError('Run build_voice_family_registry_evidence.py first')
    ensure_archive(ROOT)
    server = ThreadingHTTPServer((args.host, args.port),
                                 functools.partial(Handler, directory=str(ROOT / 'training-work')))
    print(f'Voice family descriptor review: http://{args.host}:{server.server_port}{PAGE}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
