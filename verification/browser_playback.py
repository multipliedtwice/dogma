from __future__ import annotations

import argparse
import base64
import json
import math
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chromium')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        transpile = "const ts=require('typescript'), fs=require('fs'); fs.writeFileSync(process.argv[1],ts.transpileModule(fs.readFileSync('src/lib/playback.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)"
        subprocess.run(['node', '-e', transpile, str(directory/'playback.js')], cwd=ROOT, check=True)
        for name, duration in [('one', 1.2), ('two', .8)]:
            with wave.open(str(directory/f'{name}.wav'), 'wb') as audio:
                audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b''.join(struct.pack('<h', int(1000*math.sin(2*math.pi*440*i/16000))) for i in range(round(duration*16000))))
        source = (directory/'playback.js').read_text('utf-8')
        fixtures = {name: base64.b64encode((directory/f'{name}.wav').read_bytes()).decode() for name in ['one', 'two']}
        setup = r'''({source,fixtures}) => {
const module={exports:{}};new Function('module','exports',source)(module,module.exports);
const {createPlaybackController}=module.exports;
const audio=new Audio();
const state={value:null,saves:[],loads:[],revoked:[]};
const controller=createPlaybackController(audio, {
  loadAudio:async(hash,signal)=>{state.loads.push(hash);signal.throwIfAborted();if(!fixtures[hash])throw new Error('Missing fixture');return new Blob([Uint8Array.from(atob(fixtures[hash]),value=>value.charCodeAt(0))],{type:'audio/wav'});},
  savePosition:async(bookId,value)=>{state.saves.push({bookId,...value});},
  notify:value=>{state.value=value;},
  revokeUrl:url=>{state.revoked.push(url);URL.revokeObjectURL(url);}
});
const segment=(id,hash,duration,status='ready')=>({id,chapter_id:'c1',source_id:id,start:0,end:4,text:'text',spoken:'text',duration,status,audio_hash:status==='ready'?hash:null,cues:[]});
const book={id:'book',position:{generation_id:'generation',segment_id:'s1',offset:.2}};
const generation={id:'generation',book_id:'book',segments:[segment('s1','one',1.2),segment('s2','two',.8,'pending')]};
controller.configure({playback_rate:1.5,volume:.35});controller.update(book,generation);
window.h={audio,state,controller,book,generation,segment};
}'''
        checks = []
        with sync_playwright() as playwright:
            executable = args.chromium or shutil.which('chromium')
            browser = playwright.chromium.launch(executable_path=executable, headless=True, args=['--no-sandbox', '--autoplay-policy=no-user-gesture-required'])
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_content('<!doctype html><html><head><title>Playback verification</title></head><body></body></html>')
            page.evaluate(setup, {'source': source, 'fixtures': fixtures})
            page.wait_for_function('window.h?.audio.readyState >= 1 && Math.abs(h.audio.currentTime-.2)<.03')
            assert page.evaluate('h.audio.paused && h.audio.playbackRate === 1.5 && h.audio.volume === .35')
            checks.append('Real WAV metadata restores position, speed and volume without autoplay')
            page.evaluate('h.controller.toggle()')
            page.wait_for_function('h.audio.currentTime > .3 && h.state.value.playing')
            page.evaluate('h.controller.pause()')
            before = page.evaluate('h.audio.currentTime')
            page.evaluate('h.controller.update({...h.book},{...h.generation})')
            assert page.evaluate('h.state.loads.length') == 1
            assert abs(page.evaluate('h.audio.currentTime') - before) < .05
            checks.append('Playing and polling preserve the current decoded audio')
            page.evaluate('h.controller.toggle();h.controller.pause();h.controller.toggle()')
            page.wait_for_function('h.state.value.playing && !h.audio.paused')
            page.wait_for_timeout(100)
            assert page.evaluate('h.state.value.error') == ''
            checks.append('Rapid Play/Pause/Play does not expose stale play-promise errors')
            page.evaluate('h.audio.pause();h.controller.update({...h.book},{...h.generation})')
            page.wait_for_timeout(100)
            assert page.evaluate('h.audio.paused && !h.state.value.playing')
            checks.append('Media-originated pause survives generation polling')
            page.evaluate('h.controller.seek(.85)')
            page.wait_for_function('Math.abs(h.audio.currentTime-.85)<.03')
            page.evaluate('h.controller.toggle()')
            page.wait_for_function("h.state.value.segment.id==='s2' && h.state.value.waiting")
            checks.append('Natural media completion waits for an unprepared next segment')
            page.evaluate("h.generation={...h.generation,segments:[h.generation.segments[0],h.segment('s2','two',.8)]};h.controller.update(h.book,h.generation)")
            page.wait_for_function("h.state.value.segment.id==='s2' && h.state.value.playing")
            page.wait_for_function('h.audio.ended && !h.state.value.playing')
            assert page.evaluate('h.state.value.waiting') is False
            checks.append('Ready next segment resumes and final media completion stops cleanly')
            page.evaluate("h.controller.select('s1',false,.4)")
            page.wait_for_function('Math.abs(h.audio.currentTime-.4)<.03 && h.audio.paused')
            page.evaluate("h.controller.select('s1',false,.15)")
            page.wait_for_function('Math.abs(h.audio.currentTime-.15)<.03 && h.audio.paused')
            page.wait_for_function('Math.abs(h.state.saves.at(-1).offset-.15)<.03')
            checks.append('Reselecting paused decoded audio saves the new offset immediately')
            page.evaluate('h.controller.toggle()')
            page.wait_for_function('h.state.value.playing && !h.audio.ended')
            page.evaluate('h.audio.dispatchEvent(new Event("ended"))')
            assert page.evaluate('h.state.value.segment.id === "s1" && h.state.value.playing')
            checks.append('Injected obsolete completion does not skip a currently playing decoded passage')
            page.evaluate('h.audio.dispatchEvent(new Event("error"))')
            assert page.evaluate('h.audio.error === null && h.state.value.playing && h.state.value.error === ""')
            checks.append('Injected obsolete error does not interrupt healthy decoded media')
            page.evaluate("h.controller.pause();h.generation={...h.generation,segments:[h.segment('s1','one',1.2),h.segment('held','',0,'review'),h.segment('s3','two',.8)]};h.controller.update(h.book,h.generation);h.controller.select('s1',true,1.1)")
            page.wait_for_function("h.state.value.segment.id==='s3' && h.state.value.playing")
            checks.append('Continuous playback skips held passages and continues with the next ready passage')
            page.evaluate("h.controller.pause();h.controller.select('s3',false,.6);h.generation={...h.generation,segments:[h.segment('s1','one',1.2),h.segment('held','',0,'review'),h.segment('s3','one',1.2)]};h.controller.update(h.book,h.generation)")
            page.wait_for_function("h.state.value.segment.id==='s3' && h.audio.readyState>=1 && Math.abs(h.audio.currentTime)<.03")
            page.wait_for_function('h.state.saves.some(item=>item.segment_id==="s3" && item.offset===0)')
            checks.append('Replacing passage audio resets and saves its playback offset')
            page.evaluate('h.controller.pause();h.controller.seek(.15)')
            page.evaluate('h.controller.dispose()')
            page.wait_for_function('h.state.saves.some(item=>item.segment_id==="s1" && Math.abs(item.offset-.15)<.03)')
            assert page.evaluate('!h.audio.hasAttribute("src") && h.state.revoked.length>=3')
            checks.append('Disposal saves source-bound position and revokes owned blob URLs')
            assert not errors, errors
            checks.append('No uncaught browser errors')
            browser.close()
        print(json.dumps({'scope': 'actual HTMLAudioElement controller; not the React application or a Tauri webview', 'checks_passed': len(checks), 'checks': checks}, indent=2))


if __name__ == '__main__':
    main()
