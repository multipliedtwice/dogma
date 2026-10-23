"""Serve the local Book 1 voice review page and its WAV clips over localhost."""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from book1_review_persistence import save_review, ReviewConflict
from manual_labeling_archive import atomic_bytes


ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / 'training-work/character-rb1/voice-evidence-atlas'
SERVED_ROOT = ROOT / 'training-work'
PAGE = '/character-rb1/voice-evidence-atlas/'
ALL_PAGE = '/character-rb1/fine-tune-all/'
RECORDING_PAGE = PAGE + 'recordings/'
PASSAGE_PAGE = ALL_PAGE + 'passage-audio/'


class ReviewHandler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      '.wav': 'audio/wav', '.html': 'text/html; charset=utf-8',
                      '.json': 'application/json; charset=utf-8'}

    def allowed(self) -> bool:
        path = unquote(urlsplit(self.path).path)
        if '..' in Path(path).parts:
            return False
        return path.startswith(('/character-rb1/voice-evidence-atlas/',
                                '/character-rb1/fine-tune-all/',
                                '/sentence-clips/', '/full-book-pilot/clips/',
                                '/higgs-tts-3-pilot/ellipsis-pilot/review/'))

    def do_GET(self) -> None:
        if not self.allowed():
            self.send_error(404)
            return
        if urlsplit(self.path).path.startswith(RECORDING_PAGE):
            self.send_recording(head_only=False)
            return
        if urlsplit(self.path).path.startswith(PASSAGE_PAGE) and not self.prepare_passage():
            return
        if urlsplit(self.path).path.lower().endswith('.wav'):
            self.send_wave(head_only=False)
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if not self.allowed():
            self.send_error(404)
            return
        if urlsplit(self.path).path.startswith(RECORDING_PAGE):
            self.send_recording(head_only=True)
            return
        if urlsplit(self.path).path.startswith(PASSAGE_PAGE) and not self.prepare_passage():
            return
        if urlsplit(self.path).path.lower().endswith('.wav'):
            self.send_wave(head_only=True)
            return
        super().do_HEAD()

    def prepare_passage(self) -> bool:
        name = urlsplit(self.path).path.removeprefix(PASSAGE_PAGE)
        match = re.fullmatch(r'(rb1-\d{2})/(\d+\.\d{3})-(\d+\.\d{3})\.wav', name)
        if not match:
            self.send_error(404)
            return False
        recording_id, raw_start, raw_end = match.groups()
        start, end = float(raw_start), float(raw_end)
        workspace = getattr(self, 'workspace_root', ROOT)
        source = workspace / 'training-work/character-rb1/voice-evidence-atlas/recordings' / f'{recording_id}.mp3'
        if not source.is_file() or not 0 <= start < end or end-start > 120:
            self.send_error(404)
            return False
        target = workspace / 'training-work/character-rb1/fine-tune-all/passage-audio' / name
        if target.is_file():
            return True
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix='.passage-', suffix='.wav', dir=target.parent)
        os.close(handle)
        try:
            command = ['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source),
                       '-ss', raw_start, '-t', f'{end-start:.3f}', '-map', '0:a:0',
                       '-vn', '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', '-y', temporary]
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=180)
            if result.returncode or os.path.getsize(temporary) < 100:
                self.send_error(500, 'Could not decode passage audio')
                return False
            os.replace(temporary, target)
        except (OSError, subprocess.TimeoutExpired):
            self.send_error(500, 'Could not decode passage audio')
            return False
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True

    def send_recording(self, head_only: bool) -> None:
        name = urlsplit(self.path).path.removeprefix(RECORDING_PAGE)
        if not re.fullmatch(r'rb1-\d{2}\.mp3', name):
            self.send_error(404)
            return
        source = ATLAS / 'recordings' / name
        self.send_file_range(source, 'audio/mpeg', head_only)

    def send_wave(self, head_only: bool) -> None:
        source = Path(self.translate_path(self.path))
        if not source.resolve().is_relative_to((ROOT / 'training-work').resolve()):
            self.send_error(404)
            return
        self.send_file_range(source, 'audio/wav', head_only)

    def send_file_range(self, source: Path, content_type: str, head_only: bool) -> None:
        if not source.is_file():
            self.send_error(404)
            return
        size = source.stat().st_size
        start, end = 0, size - 1
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
                suffix = int(match.group(2))
                start = max(0, size - suffix)
            if start >= size or end < start:
                self.send_error(416)
                return
        self.send_response(206 if requested else 200)
        self.send_header('Content-Type', content_type)
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
        path = urlsplit(self.path).path
        if path not in (PAGE + 'submit-review', ALL_PAGE + 'save-review',
                        ALL_PAGE + 'patch-review'):
            self.send_error(404)
            return
        try:
            origin = self.headers.get('Origin')
            if origin and origin != f'http://127.0.0.1:{self.server.server_port}':
                raise ValueError('Unexpected request origin')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 15_000_000:
                raise ValueError('Invalid review size')
            review = json.loads(self.rfile.read(size))
            if path in (ALL_PAGE + 'save-review', ALL_PAGE + 'patch-review'):
                self.save_candidate_review(review, patch=path.endswith('patch-review'))
                return
            manifest = json.loads((ATLAS / 'manifest.json').read_text(encoding='utf-8'))
            import hashlib
            digest = hashlib.sha256((ATLAS / 'manifest.json').read_bytes()).hexdigest()
            valid_ids = {case['id'] for case in manifest['cases']}
            decisions = review.get('decisions')
            if (review.get('kind') != 'voice_evidence_review'
                    or review.get('manifest_sha256') != digest
                    or not isinstance(decisions, dict)
                    or not set(decisions).issubset(valid_ids)):
                raise ValueError('Review does not match this atlas')
            reviews = ATLAS / 'reviews'
            reviews.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            target = reviews / f'book1-voice-evidence-review-{stamp}-{os.getpid()}.json'
            target.write_text(json.dumps(review, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            book_id=manifest['book']
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]*',book_id):
                raise ValueError('Invalid archive book ID')
            atomic_bytes(ROOT/'manual-labeling'/book_id/'history/character-rb1/voice-evidence-atlas/reviews'/target.name,
                         target.read_bytes())
            data = json.dumps({'saved': True, 'file': str(target.relative_to(ROOT)),
                               'reviewed': sum(bool(d.get('verdict')) for d in decisions.values())}).encode()
            self.send_response(201)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self.send_error(400, str(exc))
        except OSError as exc:
            self.json_response(500, {'error': 'Could not complete the durable label backup: '+str(exc)})

    def json_response(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def save_candidate_review(self, review: dict, patch=False) -> None:
        folder = ROOT / 'training-work/character-rb1/fine-tune-all'
        try:
            result = save_review(folder, review, patch=patch)
        except ReviewConflict as exc:
            self.json_response(409, {'error': str(exc), 'conflicts': exc.conflicts})
            return
        except OSError as exc:
            self.json_response(500, {'error': 'Could not complete the durable label backup: '+str(exc)})
            return
        self.json_response(200, {'saved': True, 'decisions': len(result['decisions']), 'review': result})

    def end_headers(self) -> None:
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=1437)
    args = parser.parse_args()
    if not ATLAS.joinpath('index.html').is_file():
        raise FileNotFoundError(ATLAS / 'index.html')
    handler = functools.partial(ReviewHandler, directory=str(SERVED_ROOT))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler)
    print(f'Book 1 voice review: http://127.0.0.1:{args.port}{PAGE}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
