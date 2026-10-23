from __future__ import annotations

import argparse
import json
import secrets
import signal
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'verification'))

import server as runtime_server
from rimsky.boundaries import boundary_units
from rimsky.instance import library_lease
from rimsky.packs import load_pack
from rimsky.util import AppError, write_json
from test_core import NEUTRAL_CONTEXT, SyntheticEngine, agreeing_critic, narration as narrated, pack_fixture, write_wave
from make_fixtures import REVIEW_MIXED, REVIEW_RADIO, REVIEW_RADIO_QUOTE, REVIEW_SPEAKER, image_bytes

CONTROL_PREFIX = 'RIMSKIFY_E2E_CONTROL '
AUDIO_SECONDS = 24
PRESENTATION = {'schema': 1, 'narrator_id': 'n', 'channels': {'radio': {'mode': 'speaker', 'status': 'provisional',
                                                                       'evidence': {'reviewed_clips': 1, 'limitation': 'synthetic E2E rule'}}}}
CRITIC_REASON = 'Fixture critic: the text alone does not prove this speaker.'


class Gate:
    def __init__(self):
        self.condition = threading.Condition()
        self.remaining = None
        self.held = threading.Event()

    def hold(self, after=0):
        if type(after) is not int or not 0 <= after <= 50:
            raise ValueError('after must be an integer from 0 to 50')
        with self.condition:
            self.remaining = after
            self.held.clear()

    def release(self):
        with self.condition:
            self.remaining = None
            self.condition.notify_all()

    def enter(self, stop):
        with self.condition:
            while self.remaining == 0:
                self.held.set()
                if stop.is_set():
                    raise AppError('Controlled preparation paused.')
                self.condition.wait(.05)
            if stop.is_set():
                raise AppError('Controlled preparation paused.')
            if self.remaining is not None:
                self.remaining -= 1


def scripted_part(index, speaker, sid, context=NEUTRAL_CONTEXT):
    return {'first': index, 'last': index, 'speaker': speaker, 'delivery': 'neutral', 'evidence': [sid] if speaker not in ('narrator', 'unknown') else [], 'context': context}


def scripted_passage(segment, speaker):
    text, sid = segment['text'], segment['id']
    if text == REVIEW_RADIO:
        return {'id': sid, 'parts': [scripted_part(0, speaker, sid, {'mood': 'neutral', 'channel': 'radio', 'content_register': 'transmitted_speech', 'confidence': 'high',
                                                                     'evidence': [{'id': sid, 'quote': REVIEW_RADIO_QUOTE}]})]}
    if text == REVIEW_MIXED:
        if len(boundary_units(text)) != 3:
            raise AppError('The mixed fixture sentence must have three analysis units.')
        return {'id': sid, 'parts': [scripted_part(index, role, sid) for index, role in enumerate((speaker, 'narrator', 'unknown'))]}
    return narrated(segment)


def scripted_analysis(payload):
    known = {character['name']: character['id'] for character in payload['cast']}
    evidence = [segment['id'] for segment in payload['segments'] if segment['text'] in (REVIEW_RADIO, REVIEW_MIXED)]
    speaker = known.get(REVIEW_SPEAKER, 'new_1')
    characters = [] if REVIEW_SPEAKER in known or not evidence else [{
        'id': 'new_1', 'name': REVIEW_SPEAKER, 'aliases': [], 'description': '', 'evidence': evidence, 'kind': 'person',
        'gender': {'value': 'unknown', 'evidence': []}, 'age_band': {'value': 'unknown', 'evidence': []}}]
    return {'characters': characters, 'passages': [scripted_passage(segment, speaker) for segment in payload['segments']]}


def scripted_critic(payload):
    disputed = lambda target, part: {'index': part['index'], 'role_supported': True, 'speaker_supported': False, 'alternative': 'unknown',
                                     'evidence': [target['id']], 'reason': CRITIC_REASON}
    agreed = agreeing_critic(payload)
    return {'passages': [{'id': target['id'], 'reviews': [disputed(target, part) if part['speaker'] not in ('narrator', 'unknown') else review
                                                          for part, review in zip(target['proposal'], passage['reviews'])]}
                         for target, passage in zip(payload['request']['targets'], agreed['passages'])]}


class ControlledEngine(SyntheticEngine):
    def __init__(self, gate, rendered):
        super().__init__()
        self.gate = gate
        self.rendered = rendered

    def call(self, method, payload, stop):
        if method == 'render':
            self.gate.enter(stop)
            self.rendered.append(payload['text'])
            return write_wave(Path(payload['output']), seconds=AUDIO_SECONDS)
        if method == 'analyze':
            return scripted_analysis(payload)
        if method == 'critique':
            return scripted_critic(payload)
        return super().call(method, payload, stop)


def check_rendered(rendered, data):
    expected, forbidden = data.get('rendered', []), data.get('unrendered', [])
    if not all(isinstance(value, list) and all(isinstance(text, str) for text in value) for value in (expected, forbidden)):
        raise ValueError('check-rendered needs lists of texts.')
    done = set(rendered)
    missing = [text for text in expected if text not in done]
    leaked = [text for text in forbidden if text in done]
    if missing or leaked:
        raise AppError(f'Rendered texts differ. Missing: {missing!r}. Unexpectedly rendered: {leaked!r}.')


def fixture_pack(path):
    pack_fixture(path)
    manifest = json.loads((path / 'pack.json').read_text('utf-8'))
    write_json(path / 'pack.json', {**manifest, 'presentation_voices': PRESENTATION})
    return load_pack(str(path), verify=True)


def install_fakes(app, narration, illustration, rendered):
    fail_image = threading.Event()
    app.jobs.engine_factory = lambda _binaries: ControlledEngine(narration, rendered)
    app.illustrations.preflight = lambda: None

    def render_image(_brief, _style, stop, _root):
        illustration.enter(stop)
        if fail_image.is_set():
            fail_image.clear()
            raise AppError('Controlled illustration failure. Retry this saved run explicitly.')
        return image_bytes()

    app.illustrations.renderer = render_image
    return fail_image


def remove_audio(app, data):
    with app.jobs.lock, app.store.lock:
        if app.jobs.active:
            raise AppError('Pause generation before removing fixture audio.')
        generation = app.store.generation(data['generation_id'])
        segment = next((item for item in generation['segments'] if item['id'] == data['segment_id']), None)
        if not segment or segment['status'] != 'ready' or not segment['audio_hash']:
            raise AppError('Choose a ready segment in this fixture library.')
        path, _mime = app.asset('audio', segment['audio_hash'])
        path.unlink()


def serve(data_dir, static):
    if not (static / 'index.html').is_file():
        raise AppError('Built dist/index.html is missing. Run npm run build before the fixture.')
    first_start = not (data_dir / 'library.sqlite3').exists()
    pack_path = data_dir.parent / 'pack'
    if (pack_path / 'pack.json').is_file():
        load_pack(str(pack_path), verify=True)
    else:
        fixture_pack(pack_path)
    app = runtime_server.Application(data_dir, data_dir.parent / 'empty-binaries')
    narration, illustration, rendered = Gate(), Gate(), []
    try:
        app.store.update_settings({
            'pack_path': str(pack_path), 'device': 'cpu', 'align_words': False, 'cast_review': False,
            **({'language': 'en', 'online_images': False} if first_start else {}),
        })
        fail_image = install_fakes(app, narration, illustration, rendered)
    except BaseException:
        app.close()
        raise
    real_health = runtime_server.health

    def fixture_health(settings, binaries, verification_cache=None):
        result = real_health(settings, binaries, verification_cache)
        result.update(ready=not bool(result['pack_error']), missing=[], word_alignment=False)
        return result

    def no_remote_image(*_args, **_kwargs):
        raise AppError('Real Image API requests are disabled in the E2E fixture.')

    token = secrets.token_urlsafe(32)
    server = None
    stopping = threading.Event()

    def stop(*_args):
        if stopping.is_set():
            return
        stopping.set()
        app.closing.set()
        app.jobs.stop.set()
        app.illustrations.stop.set()
        narration.release()
        illustration.release()
        if server:
            threading.Thread(target=server.shutdown, daemon=True).start()

    def controls():
        try:
            while line := sys.stdin.readline(16385):
                rid = None
                try:
                    if len(line) > 16384:
                        raise ValueError('Fixture control exceeds 16 KiB.')
                    value = json.loads(line)
                    if not isinstance(value, dict) or set(value) - {'id', 'op', 'data'}:
                        raise ValueError('Invalid fixture control envelope.')
                    rid = value.get('id')
                    if type(rid) is not int or rid < 1:
                        raise ValueError('Fixture control needs a positive integer id.')
                    data = value.get('data', {})
                    if not isinstance(data, dict):
                        raise ValueError('Fixture control data must be an object.')
                    op = value.get('op')
                    if op in ('hold-render', 'hold-illustration'):
                        (narration if op == 'hold-render' else illustration).hold(data.get('after', 0))
                    elif op in ('release-render', 'release-illustration'):
                        (narration if op == 'release-render' else illustration).release()
                    elif op in ('wait-render-held', 'wait-illustration-held'):
                        gate = narration if op == 'wait-render-held' else illustration
                        if not gate.held.wait(10):
                            raise AppError(f'{op}: renderer did not reach the hold point.')
                    elif op == 'fail-next-illustration':
                        fail_image.set()
                    elif op == 'delete-audio':
                        remove_audio(app, data)
                    elif op == 'check-rendered':
                        check_rendered(rendered, data)
                    elif op == 'shutdown':
                        stop()
                    else:
                        raise ValueError('Unknown fixture control.')
                    reply = {'id': rid, 'ok': True}
                except Exception as error:
                    reply = {'id': rid, 'ok': False, 'error': str(error)}
                print(CONTROL_PREFIX + json.dumps(reply), file=sys.stderr, flush=True)
        finally:
            stop()

    try:
        with patch.object(runtime_server, 'health', fixture_health), patch.object(runtime_server, 'generate_visual', no_remote_image), patch.object(runtime_server, 'codex_path', lambda: 'codex'):
            server = ThreadingHTTPServer(('127.0.0.1', 0), runtime_server.handler_for(app, token, False, static))
            server.daemon_threads = True
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            threading.Thread(target=controls, daemon=True, name='e2e-stdin').start()
            print(json.dumps({'port': server.server_port, 'token': token}), flush=True)
            server.serve_forever(poll_interval=.1)
    finally:
        stop()
        if server:
            server.server_close()
        app.close()


def main():
    parser = argparse.ArgumentParser(description='Test-only real HTTP runtime with local synthetic model boundaries.')
    parser.add_argument('--data-dir', required=True, type=Path)
    parser.add_argument('--static', required=True, type=Path)
    args = parser.parse_args()
    args.data_dir.parent.mkdir(parents=True, exist_ok=True)
    with library_lease(args.data_dir):
        serve(args.data_dir.resolve(), args.static.resolve())


if __name__ == '__main__':
    main()
