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
        output = Path(temporary) / 'reader-navigation.js'
        media = Path(temporary) / 'media.js'
        transpile = "const ts=require('typescript'),fs=require('fs');fs.writeFileSync(process.argv[2],ts.transpileModule(fs.readFileSync(process.argv[1],'utf8'),{fileName:process.argv[1],compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText)"
        for source, destination in [('src/lib/readerNavigation.ts', output), ('src/components/Media.tsx', media)]:
            subprocess.run(['node', '-e', transpile, source, str(destination)], cwd=ROOT, check=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.chromium or shutil.which('chromium'),
                                                 headless=True, args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1100, 'height': 850})
            page.emulate_media(reduced_motion='reduce')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_content('''<!doctype html><html><head><style>
#reader {height:600px;width:800px;overflow:auto;overflow-anchor:none;font:20px/1.8 serif;}
#before {height:350px} #after {height:1600px} p {margin:0}
</style></head><body><div id="reader"><div id="before" data-source-id="before"></div>
<p id="long" data-source-id="long"></p><div id="after" data-source-id="after"></div></div></body></html>''')
            page.evaluate('''source => {
                const module = {exports:{}};
                new Function('module','exports',source)(module,module.exports);
                window.helpers=module.exports;
                window.root=document.getElementById('reader');
                window.paragraph=document.getElementById('long');
                paragraph.textContent='An unusually long paragraph needs to retain its place after the reader changes font size or the window width. '.repeat(300);
                window.frame=()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
                window.topOf=element=>root.scrollTop+element.getBoundingClientRect().top-root.getBoundingClientRect().top;
                window.inset=()=>Math.min(120,root.clientHeight*.2);
                window.target=fraction=>topOf(paragraph)+paragraph.getBoundingClientRect().height*fraction-inset();
            }''', output.read_text('utf-8'))
            saved = page.evaluate('''async () => {
                root.scrollTop=target(.57); await frame();
                const saved=[];
                const binding=helpers.bindReadingPosition(root,()=>{},(source,offset,fraction)=>saved.push({source,offset,source_fraction:fraction}));
                root.dispatchEvent(new WheelEvent('wheel'));
                root.scrollTop+=24; await frame(); binding.flush(); binding.dispose();
                window.saved=saved.at(-1); return window.saved;
            }''')
            assert saved['source'] == 'long' and .55 < saved['source_fraction'] < .60, saved
            assert saved['offset'] > 3000, saved
            checks = ['Manual scrolling persists a source-relative point deep inside a real long paragraph']
            for font, width, height, preceding in [(31, 470, 600, 1750), (17, 720, 320, 700)]:
                result = page.evaluate('''async values => {
                    const [font,width,height,preceding]=values;
                    root.style.fontSize=font+'px'; root.style.width=width+'px'; root.style.height=height+'px';
                    document.getElementById('before').style.height=preceding+'px'; root.scrollTop=0;
                    await frame(); const expected=target(saved.source_fraction);
                    helpers.scrollToReadingPosition(root,saved.source,saved.offset,saved.source_fraction);
                    await frame(); return {error:Math.abs(root.scrollTop-expected),top:root.scrollTop,source:helpers.readingSourceAtViewport(root)};
                }''', [font, width, height, preceding])
                assert result['error'] < 1.1 and result['source'] == 'long', result
                checks.append(f'Restored source-relative location within one CSS pixel after font={font}, width={width}, viewport={height}, preceding={preceding}')
            legacy = page.evaluate('''async () => {
                root.scrollTop=target(.4); await frame(); const offset=root.scrollTop;
                root.scrollTop=0; helpers.scrollToReadingPosition(root,'long',offset); await frame();
                const unchanged=root.scrollTop;
                document.getElementById('before').style.height=(offset+2000)+'px'; await frame();
                helpers.scrollToReadingPosition(root,'long',offset); await frame();
                return {offset,unchanged,changed:root.scrollTop,paragraphTop:topOf(paragraph)};
            }''')
            assert abs(legacy['offset'] - legacy['unchanged']) < 1.1, legacy
            assert abs(legacy['changed'] - legacy['paragraphTop']) < 1.1, legacy
            checks.extend(['Legacy absolute position retains exact pixels in unchanged layout',
                           'Legacy position falls back to the saved source when old pixels identify a different block'])
            mounted = page.evaluate('''async mediaSource => {
                root.style.cssText='font-size:24px;width:680px;height:600px';
                document.getElementById('before').style.height='900px';
                const block=document.createElement('section');
                block.id='long'; block.dataset.sourceId='long'; block.style.padding='7px 0';
                const markup=document.createElement('div'); block.append(markup);
                paragraph.replaceWith(block); window.paragraph=block; root.scrollTop=0;
                const layouts=[], passives=[], cleanups=[];
                const require=name=>{
                    if(name==='react')return {
                        memo:fn=>fn,useRef:()=>({current:markup}),
                        useLayoutEffect:fn=>layouts.push(fn),useEffect:fn=>passives.push(fn)
                    };
                    if(name==='react/jsx-runtime')return {jsx:()=>null};
                    if(name==='dompurify')return {default:{sanitize:html=>{
                        const template=document.createElement('template'); template.innerHTML=html;
                        return template.content;
                    }}};
                    if(name.endsWith('imageCache'))return {createImageCache:()=>({}),whenVisible:()=>()=>{}};
                    return {};
                };
                const module={exports:{}};
                new Function('module','exports','require',mediaSource)(module,module.exports,require);
                module.exports.BookMarkup({book:'fixture',block:{html:'<p>'+('Long paragraph with a saved reading position. '.repeat(600))+'</p>'}});
                for(const effect of layouts)cleanups.push(effect());
                helpers.scrollToReadingPosition(root,'long',3000,.57);
                for(const effect of passives)cleanups.push(effect());
                await frame();
                const result={error:Math.abs(root.scrollTop-target(.57)),height:paragraph.getBoundingClientRect().height,source:helpers.readingSourceAtViewport(root)};
                for(const cleanup of cleanups)if(typeof cleanup==='function')cleanup();
                return result;
            }''', media.read_text('utf-8'))
            assert mounted['height'] > 3000 and mounted['error'] < 1.1 and mounted['source'] == 'long', mounted
            checks.append('BookMarkup inserts long-paragraph content before parent restoration; real DOM with controlled hook phases and clean HTML fixture')
            assert not errors, errors
            browser.close()
            print(json.dumps({'passed': len(checks), 'failed': 0, 'skipped': 0, 'checks': checks,
                              'scope': 'Real Chromium DOM/reflow; production navigation and BookMarkup callbacks. Hook scheduling and HTML sanitization are fixture adapters, not React/DOMPurify or bundled desktop acceptance.'}, indent=2))


if __name__ == '__main__':
    main()
