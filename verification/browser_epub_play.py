from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from unittest.mock import patch

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'verification')]

from test_core import SyntheticEngine, pack_fixture, write_wave
from rimsky.util import AppError
from server import Application, handler_for
from rimsky.packs import health as real_health


class PausableFixtureEngine(SyntheticEngine):
    def __init__(self):
        super().__init__()
        self.second_requested = threading.Event()
        self.release = threading.Event()

    def call(self, method, payload, stop):
        if method == 'render':
            if self.rendered == 1:
                self.second_requested.set()
                while not self.release.wait(.05):
                    if stop.is_set():
                        raise AppError('Controlled preparation paused')
            self.rendered += 1
            return write_wave(Path(payload['output']), seconds=.9)
        return super().call(method, payload, stop)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('epub', type=Path)
    parser.add_argument('--chromium', default=shutil.which('chromium'))
    args = parser.parse_args()
    raw = args.epub.read_bytes()
    with tempfile.TemporaryDirectory(prefix='rimsky-epub-play-') as temporary:
        root = Path(temporary)
        app = Application(root / 'library', root / 'binaries')
        server = None
        thread = None
        token = secrets.token_urlsafe(32)
        engine = PausableFixtureEngine()
        health_override = None

        def start_server(application):
            result = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(application, token, dev=True))
            worker = threading.Thread(target=result.serve_forever, daemon=True)
            worker.start()
            return result, worker

        def request(path, body=None, mime='application/json'):
            headers = {'Authorization': 'Bearer ' + token, 'Content-Type': mime}
            with urlopen(Request(f'http://127.0.0.1:{server.server_port}{path}', data=body, headers=headers), timeout=10) as response:
                return response.read()

        def api(op, data=None):
            return json.loads(request('/api', json.dumps({'op': op, 'data': data or {}}).encode()))

        def bridge(kind, data):
            if kind == 'asset':
                return base64.b64encode(request('/asset?' + urlencode({'kind': 'audio', 'id': data['id']}))).decode()
            return api(data['op'], data.get('data'))

        checks = []
        try:
            server, thread = start_server(app)
            pack_fixture(root / 'controlled-pack')
            api('update_settings', {'pack_path': str(root / 'controlled-pack'), 'cast_review': False})
            app.jobs.engine_factory = lambda _: engine
            book = json.loads(request('/import?name=' + quote(args.epub.name), raw, 'application/epub+zip'))
            chapter = next(item['id'] for item in book['preparation']['chapters']
                           if item['default_narration'] and item['base_segments'] >= 3)

            def synthetic_capability(settings, binaries):
                value = real_health(settings, binaries)
                value.update(ready=True, missing=[])
                return value

            health_override = patch('server.health', side_effect=synthetic_capability)
            health_override.start()
            created = api('generate', {'book_id': book['id'], 'chapters': [chapter]})
            assert engine.second_requested.wait(15), 'Controlled generation did not reach its second audio request'
            checks.append('Actual authenticated EPUB import and generate route reach persisted first audio')
            transpile = "const ts=require('typescript'),fs=require('fs');fs.writeFileSync(process.argv[1],ts.transpileModule(fs.readFileSync('src/lib/playback.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)"
            subprocess.run(['node', '-e', transpile, str(root / 'playback.js')], cwd=ROOT, check=True)
            source = (root / 'playback.js').read_text()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=args.chromium, headless=True,
                    args=['--no-sandbox', '--autoplay-policy=no-user-gesture-required'])
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.set_content('<html><head><title>EPUB playback verification</title></head><body></body></html>')
                page.expose_function('coreBridge', bridge)
                page.evaluate('''async ({source,bookId,generationId}) => {
const module={exports:{}};new Function('module','exports',source)(module,module.exports);
const {createPlaybackController}=module.exports;
const state={value:null,loads:[],revoked:[]};
const api=(op,data)=>window.coreBridge('api',{op,data});
const create=()=>{
 const audio=new Audio();
 const controller=createPlaybackController(audio,{
  loadAudio:async(id,signal)=>{
   signal.throwIfAborted();state.loads.push(id);
   const encoded=await window.coreBridge('asset',{id});signal.throwIfAborted();
   return new Blob([Uint8Array.from(atob(encoded),c=>c.charCodeAt(0))],{type:'audio/wav'});
  },
  savePosition:(id,position)=>api('save_position',{book_id:id,position}),
  notify:value=>state.value=value,
  revokeUrl:url=>{state.revoked.push(url);URL.revokeObjectURL(url);}
 });
 return {audio,controller};
};
window.h={state,api,bookId,generationId,create,...create()};
h.reload=async()=>{h.book=await api('book',{id:bookId});h.generation=await api('generation',{id:generationId});h.controller.update(h.book,h.generation);};
await h.reload();
}''', {'source': source, 'bookId': book['id'], 'generationId': created['id']})
                page.wait_for_function('h.audio.readyState>=1 && !h.state.value.waiting')
                assert page.evaluate('h.audio.paused')
                page.evaluate('h.controller.toggle()')
                page.wait_for_function('h.state.value.playing && h.audio.currentTime>.1')
                page.wait_for_function("h.state.value.waiting && h.state.value.segment.id===h.generation.segments[1].id")
                checks.append('Real decoded first WAV advances and waits on the pending second passage')
                engine.release.set()
                app.jobs.thread.join(15)
                assert not app.jobs.thread.is_alive()
                completed = api('generation', {'id': created['id']})
                assert completed['status'] == 'ready', completed['error']
                page.evaluate('h.reload()')
                page.wait_for_function('h.state.value.playing && h.audio.currentTime>.1')
                assert page.evaluate('h.state.value.segment.text===h.generation.segments[1].text')
                checks.append('Polling actual generation data resumes the next saved audio and its matching source text')
                page.evaluate('h.controller.pause()')
                saved = api('book', {'id': book['id']})['position']
                assert saved['segment_id'] == completed['segments'][1]['id']
                assert saved['offset'] > .05
                assert saved['source_id'] == completed['segments'][1]['source_id']
                page.evaluate('h.controller.dispose();h.controller.persist()')
                before_replay = (engine.rendered, engine.analyzed)
                checks.append('Awaited Pause persists the source, generation, passage and actual playback offset through HTTP')
                health_override.stop()
                health_override = None
                shutil.rmtree(root / 'controlled-pack')
                server.shutdown()
                server.server_close()
                thread.join(5)
                app.close()
                app = Application(root / 'library', root / 'binaries')
                server, thread = start_server(app)
                assert not api('health')['ready']
                page.evaluate('Object.assign(h,h.create());h.reload()')
                page.wait_for_function('h.audio.readyState>=1 && h.state.value.segment.id===h.book.position.segment_id')
                assert page.evaluate('h.audio.paused')
                assert abs(page.evaluate('h.audio.currentTime') - saved['offset']) < .04
                checks.append('Actual core restart restores listening position without any available inference model')
                page.evaluate('h.controller.toggle()')
                page.wait_for_function('h.state.value.playing')
                page.evaluate('h.controller.pause();h.controller.select(h.generation.segments[0].id,true,.1)')
                page.wait_for_function('h.state.value.playing && h.state.value.segment.id===h.generation.segments[0].id')
                assert before_replay == (engine.rendered, engine.analyzed)
                checks.append('Replay and seeking use stored audio without additional synthesis or analysis')
                page.evaluate('h.controller.pause()')
                damaged = completed['segments'][1]
                (root / 'library/audio' / f"{damaged['audio_hash']}.wav").unlink()
                page.evaluate('h.controller.select(h.generation.segments[1].id,true)')
                page.wait_for_function('Boolean(h.state.value.error) && !h.state.value.waiting')
                checks.append('Missing saved audio is refused through the real HTTP asset endpoint, not silently skipped')
                pack_fixture(root / 'controlled-pack')
                app.jobs.engine_factory = lambda _: engine
                page.evaluate('h.api("repair_audio",{id:h.generationId})')
                app.jobs.thread.join(15)
                assert not app.jobs.thread.is_alive()
                repaired = api('generation', {'id': created['id']})
                assert repaired['status'] == 'ready', repaired['error']
                assert repaired['cast'] == completed['cast']
                assert engine.rendered == before_replay[0] + 1
                assert engine.analyzed == before_replay[1]
                page.evaluate('h.reload()')
                page.evaluate('h.controller.toggle()')
                page.wait_for_function('h.state.value.playing && !h.state.value.error')
                checks.append('Explicit HTTP repair restores one missing artifact with the frozen cast; Play works afterward')
                page.evaluate('h.controller.pause();h.controller.dispose();h.controller.persist()')
                assert not page.evaluate('h.audio.hasAttribute("src")')
                assert not errors, errors
                checks.append('Browser media cleanup completes without uncaught errors')
                browser.close()
            original = root / 'library/books' / book['id'] / 'source.epub'
            assert original.read_bytes() == raw
            checks.append('Original user EPUB remains byte-identical')
            print(json.dumps({
                'scope': 'Actual uploaded EPUB, local HTTP Application/Store/Jobs and browser playback controller; synthetic narrator-only model responses and tone audio. Not full React/Tauri acceptance.',
                'browser_transport': 'Loopback navigation is blocked by this environment. A Playwright binding forwards controller requests through real authenticated Python HTTP requests; it does not fabricate server replies.',
                'model_boundary': 'Controlled engine and explicit fixture capability check only; no Qwen/llama model execution.',
                'source_sha256': hashlib.sha256(raw).hexdigest(),
                'prepared_source_characters': book['preparation']['narration_characters'],
                'prepared_base_segments': book['preparation']['base_segments'],
                'rendered_chapters': 1, 'selected_chapter_segments': len(completed['segments']),
                'checks_passed': len(checks), 'checks': checks,
                'synthesis_calls': engine.rendered, 'analysis_calls': engine.analyzed,
                'replay_inference_calls': 0, 'repair_synthesis_calls': engine.rendered - before_replay[0], 'repair_analysis_calls': engine.analyzed - before_replay[1], 'errors': errors,
            }, indent=2))
        finally:
            if health_override:
                health_override.stop()
            engine.release.set()
            if server:
                server.shutdown()
                server.server_close()
            if thread:
                thread.join(5)
            app.close()


if __name__ == '__main__':
    main()
