from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
sys.path.insert(0, str(ROOT / 'verification'))
from rimsky.books import import_book
from test_epub_real_shapes import with_cover


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chromium')
    parser.add_argument('--prepared', type=Path)
    parser.add_argument('--screenshot', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        if args.prepared:
            book = json.loads((args.prepared / 'book.json').read_text('utf-8'))
            assets = args.prepared / 'books' / book['id'] / 'assets'
        else:
            book = import_book(with_cover(), 'fixture.epub', directory / 'books')
            assets = directory / 'books' / book['id'] / 'assets'
        block = book['chapters'][0]['blocks'][0]
        assert '<image ' in block['html'], 'Cover was not prepared as an SVG image.'
        image_id = block['assets'][0]
        fixture = base64.b64encode((assets / image_id).read_bytes()).decode()
        compiler = "const ts=require('typescript'), fs=require('fs'); fs.writeFileSync(process.argv[1],ts.transpileModule(fs.readFileSync('src/lib/bookAssets.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)"
        subprocess.run(['node', '-e', compiler, str(directory / 'assets.js')], cwd=ROOT, check=True)
        source = (directory / 'assets.js').read_text('utf-8')
        checks = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.chromium or shutil.which('chromium'),
                                                 headless=True, args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1000, 'height': 1080})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_content('<!doctype html><html><head><title>Isolated source-asset verification</title><style>body{font:18px Georgia;margin:35px}svg{width:300px;height:480px;display:block}img{width:150px}code{font-family:monospace}#view{display:flex;gap:30px;align-items:start}</style></head><body><h1>Prepared EPUB asset verification</h1><div id="view"></div></body></html>')
            page.evaluate('''({source, fixture, id, html}) => {
const module={exports:{}};new Function('module','exports',source)(module,module.exports);
window.hydrate=module.exports.hydrateBookAssets;
window.blob=()=>new Blob([Uint8Array.from(atob(fixture),v=>v.charCodeAt(0))],{type:'image/png'});
window.state={loads:[],created:[],revoked:[]};
const make=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL);
URL.createObjectURL=b=>{const url=make(b);state.created.push(url);return url;};
URL.revokeObjectURL=url=>{state.revoked.push(url);revoke(url);};
const view=document.getElementById('view');view.innerHTML=html+'<img data-asset="'+id+'" alt="Same cover">';
for(const image of view.querySelectorAll('img,image')) image.addEventListener('load',()=>image.setAttribute('data-loaded','yes'));
window.stop=hydrate(view, async key=>{state.loads.push(key);return blob();});
}''', {'source': source, 'fixture': fixture, 'id': image_id, 'html': block['html']})
            page.wait_for_function("document.querySelectorAll('#view [data-loaded=yes]').length === 2")
            assert page.evaluate("document.querySelector('#view svg image').namespaceURI === 'http://www.w3.org/2000/svg'")
            assert page.evaluate("document.querySelector('#view svg image').getAttribute('width') === '500' && document.querySelector('#view svg image').getAttribute('height') === '800'")
            assert page.evaluate('state.loads.length === 1 && state.created.length === 1')
            checks.append('Real PNG decodes in both SVG and HTML; geometry remains inside SVG; repeated asset is fetched once')
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.screenshot))
            page.evaluate('stop();stop()')
            assert page.evaluate("state.revoked.length === 1 && !document.querySelector('#view image').hasAttribute('href') && !document.querySelector('#view img').hasAttribute('src')")
            checks.append('Cleanup revokes its owned URL once and clears both image bindings')
            page.evaluate('''id=>{
const root=document.getElementById('view');root.innerHTML='<img data-asset="'+id+'">';
window.resolvePending=null;window.lateStop=hydrate(root,()=>new Promise(resolve=>window.resolvePending=resolve));
}''', image_id)
            page.wait_for_function('resolvePending !== null')
            created = page.evaluate('state.created.length')
            page.evaluate('lateStop();resolvePending(blob())')
            page.wait_for_timeout(50)
            assert page.evaluate('state.created.length') == created
            assert not page.evaluate("document.querySelector('#view img').hasAttribute('src')")
            checks.append('Disposed pending loads cannot create leaked URLs or update the new view')
            page.evaluate('''()=>{
const root=document.getElementById('view');root.innerHTML='<img data-asset="https://outside.invalid/file">';
window.invalidStop=hydrate(root,()=>{throw new Error('Invalid ID reached loader');});
}''')
            page.wait_for_timeout(30)
            assert not page.evaluate("document.querySelector('#view img').hasAttribute('src')")
            checks.append('Untrusted asset identifiers do not reach the loader')
            page.evaluate('''id=>{
invalidStop();const root=document.getElementById('view');root.innerHTML='<svg><image data-asset="'+id+'" width="50" height="80"/></svg><img data-asset="'+id+'" alt="Original alternate text">';
window.failedStop=hydrate(root,async()=>{throw new Error('Missing local asset');});
}''', image_id)
            page.wait_for_function("document.querySelector('#view image').getAttribute('aria-label') === 'Image unavailable'")
            assert page.evaluate("document.querySelector('#view img').getAttribute('alt') === 'Original alternate text'")
            page.evaluate('failedStop()')
            checks.append('Missing images expose a local fallback without unhandled rejections or losing alternate text')
            assert not errors, errors
            browser.close()
        print(json.dumps({'checks': checks, 'count': len(checks), 'scope': 'Actual asset-binding module and backend markup in Chromium, not full React/Tauri or DOMPurify integration.'}, indent=2))


if __name__ == '__main__':
    main()
