from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chromium')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / 'reader-selection.js'
        transpile = "const ts=require('typescript'),fs=require('fs');fs.writeFileSync(process.argv[1],ts.transpileModule(fs.readFileSync('src/lib/readerSelection.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)"
        subprocess.run(['node', '-e', transpile, str(output)], cwd=ROOT, check=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.chromium or shutil.which('chromium'), headless=True, args=['--no-sandbox'])
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_content('''<!doctype html><html lang="en"><body tabindex="0">
<button id="transport"><span id="nested">Play</span></button>
<input id="search" aria-label="Chapter search"><textarea id="notes"></textarea>
<div id="editable" contenteditable="true">Note</div>
<input id="volume" type="range" min="0" max="10" value="5">
<a id="link" href="#chapter">Chapter link</a>
</body></html>''')
            page.evaluate('''source => {
                const module={exports:{}};
                new Function('module','exports',source)(module,module.exports);
                window.helpers=module.exports;
                window.actions=[];window.clicks=0;
                document.getElementById('transport').addEventListener('click',()=>window.clicks++);
                window.addEventListener('keydown',event=>{
                    const action=helpers.playbackShortcut(event);
                    if(!action)return;
                    event.preventDefault();actions.push(action);
                });
                window.reset=()=>{actions.length=0;document.body.focus();};
            }''', output.read_text('utf-8'))
            checks = []
            page.evaluate('reset()')
            page.keyboard.down('Space')
            page.keyboard.down('Space')
            page.keyboard.up('Space')
            assert page.evaluate('actions') == ['toggle']
            checks.append('Held Space produces one playback request, not alternating Play/Pause')
            page.evaluate('reset()')
            page.keyboard.press('ArrowLeft')
            page.keyboard.press('ArrowRight')
            assert page.evaluate('actions') == ['back', 'forward']
            checks.append('Unmodified playback arrows remain available outside controls')
            page.evaluate('reset()')
            for combination in ['Control+ArrowLeft', 'Alt+ArrowRight', 'Meta+ArrowLeft', 'Shift+Space']:
                page.keyboard.press(combination)
            assert page.evaluate('actions') == []
            checks.append('Modified keys are not consumed by playback')
            page.evaluate('reset()')
            for selector in ['#search', '#notes', '#editable']:
                page.locator(selector).focus()
                page.keyboard.press('Space')
                page.keyboard.press('ArrowLeft')
            assert page.evaluate('actions') == []
            checks.append('Text inputs and editable text keep their typing/navigation keys')
            page.evaluate('reset()')
            page.locator('#transport').focus()
            page.keyboard.press('Space')
            assert page.evaluate('actions') == []
            assert page.evaluate('clicks') == 1
            page.locator('#nested').dispatch_event('keydown', {'code': 'Space', 'bubbles': True})
            assert page.evaluate('actions') == []
            checks.append('Focused buttons retain one native activation, including nested event targets')
            page.evaluate('reset()')
            page.locator('#volume').focus()
            page.keyboard.press('ArrowRight')
            assert page.locator('#volume').input_value() == '6'
            page.locator('#link').focus()
            page.keyboard.press('ArrowLeft')
            assert page.evaluate('actions') == []
            checks.append('Focused range and link controls do not trigger audiobook skips')
            page.evaluate('''() => {
                reset();
                document.body.dispatchEvent(new KeyboardEvent('keydown',{code:'Space',isComposing:true,bubbles:true}));
                const prevented=new KeyboardEvent('keydown',{code:'Space',bubbles:true,cancelable:true});
                prevented.preventDefault();document.body.dispatchEvent(prevented);
            }''')
            assert page.evaluate('actions') == []
            checks.append('Composition and already-handled events do not control playback')
            assert page.evaluate('''() => {
                const chapters=Array.from({length:31},(_,i)=>({id:String(i),title:`Глава ${i+1}`}));
                const result=helpers.visibleChapters(chapters,'  глава 30  ');
                return result.length===1&&result[0].number===30&&result[0].chapter.id==='29';
            }''')
            checks.append('Filtered Russian chapter labels retain their canonical book numbering')
            assert not errors, errors
            checks.append('No uncaught browser errors')
            browser.close()
    print(json.dumps({'scope': 'Actual reader helper with browser focus and keyboard events; not the React or Tauri application', 'checks_passed': len(checks), 'checks': checks}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
