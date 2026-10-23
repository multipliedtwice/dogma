from __future__ import annotations
import argparse
import json
import shutil
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]

def module(file):
    command="const ts=require('typescript'),fs=require('fs');process.stdout.write(ts.transpileModule(fs.readFileSync(process.argv[1],'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)"
    return subprocess.check_output(['node','-e',command,str(ROOT/file)],text=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--chromium');args=parser.parse_args()
    checks=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=args.chromium or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1000,'height':760},reduced_motion='reduce');page.set_default_timeout(5000);errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.set_content('''<!doctype html><html><body><div id="foreign" tabindex="-1">Not this book</div><div class="reading-scroll" id="reader" style="height:420px;overflow:auto;border:1px solid"><section id="p1" tabindex="-1" style="height:800px">Paragraph one</section><section id="p2" tabindex="-1" style="height:800px">Paragraph two<figure data-illustration-source="p2">Attached artwork</figure></section><section id="p3" tabindex="-1" style="height:800px">Paragraph three</section></div></body></html>''')
        for name,file in [('navigation','src/lib/readerNavigation.ts'),('images','src/lib/imageCache.ts'),('imports','src/lib/importFiles.ts')]:
            page.evaluate("([name,source])=>{const module={exports:{}};new Function('module','exports',source)(module,module.exports);window[name]=module.exports;}",[name,module(file)])
        assert page.evaluate("navigation.scrollToSource(document.getElementById('reader'),'p2')")
        assert page.evaluate("document.activeElement.id==='p2'&&document.getElementById('reader').scrollTop>400")
        assert page.evaluate("Math.abs(document.getElementById('p2').getBoundingClientRect().top-document.getElementById('reader').getBoundingClientRect().top)<5")
        checks.append('Paragraph navigation shows the start of its text, not the middle of a tall illustrated block')
        assert not page.evaluate("navigation.scrollToSource(document.getElementById('reader'),'foreign')")
        assert page.evaluate("document.activeElement.id==='p2'")
        checks.append('Navigation rejects another root rather than jumping outside the selected book')
        assert not page.evaluate("navigation.scrollToSource(document.getElementById('reader'),'new-chapter')")
        page.evaluate("document.getElementById('reader').innerHTML='<section id=\"new-chapter\" tabindex=\"-1\">Newly mounted chapter</section>'")
        assert page.evaluate("navigation.scrollToSource(document.getElementById('reader'),'new-chapter')")
        assert page.evaluate("document.activeElement.id==='new-chapter'")
        checks.append('Cross-chapter navigation succeeds after the target is mounted')
        page.evaluate('''() => {const root=document.getElementById('reader');root.innerHTML='<div style="height:2000px"></div><div id="image" style="height:200px">Image frame</div>';root.scrollTop=0;window.visibility=[];window.disposeVisibility=images.observeVisibility(document.getElementById('image'),value=>visibility.push(value));}''')
        page.wait_for_function('visibility.length>0');assert not page.evaluate('visibility.at(-1)')
        page.evaluate("document.getElementById('reader').scrollTop=1900");page.wait_for_function('visibility.at(-1)===true')
        checks.append('Offscreen image stays unloaded until near the scrolling reading viewport')
        page.evaluate("document.getElementById('reader').scrollTop=0");page.wait_for_function('visibility.at(-1)===false')
        checks.append('Moving away releases visibility so decoded image URLs can be unloaded')
        page.evaluate("disposeVisibility();window.count=visibility.length;document.getElementById('reader').scrollTop=1900")
        page.wait_for_timeout(50);assert page.evaluate('visibility.length===count')
        checks.append('Observer cleanup prevents updates after image unmount')
        page.evaluate("() => { window.stopViewport=navigation.bindReaderViewport(document.getElementById('reader')); }")
        assert page.evaluate("document.getElementById('reader').style.getPropertyValue('--reading-viewport')==='420px'")
        page.evaluate("document.getElementById('reader').style.height='310px'")
        page.wait_for_function("document.getElementById('reader').style.getPropertyValue('--reading-viewport')==='310px'")
        page.evaluate('stopViewport()')
        checks.append('Illustration panel viewport tracks reading area resize without a playback-clock render')
        page.evaluate('''() => {window.drops=[];window.hovers=[];window.blocked=false;window.rejections=0;window.stopDrop=imports.bindBookDrop(window,{blocked:()=>blocked,hover:value=>hovers.push(value),drop:files=>drops.push(files.map(file=>file.name)),rejected:()=>rejections++});window.fireDrop=(type,names=['one.epub','two.fb2'])=>{const data=new DataTransfer();for(const name of names)data.items.add(new File(['fixture'],name));const event=new DragEvent(type,{dataTransfer:data,bubbles:true,cancelable:true});document.body.dispatchEvent(event);return event.defaultPrevented;};}''')
        assert page.evaluate("fireDrop('dragenter')");assert page.evaluate('hovers.at(-1)===true')
        assert page.evaluate("fireDrop('drop')");assert page.evaluate("JSON.stringify(drops)==='[[\"one.epub\",\"two.fb2\"]]'&&hovers.at(-1)===false")
        checks.append('Browser file drop prevents navigation and passes all dropped books')
        page.evaluate("blocked=true;fireDrop('dragenter');fireDrop('drop')")
        assert page.evaluate('rejections===1&&drops.length===1&&hovers.at(-1)===false')
        checks.append('Dropping over an open dialog is refused explicitly, without importing or navigating')
        page.evaluate("stopDrop();blocked=false;fireDrop('drop')");assert page.evaluate('drops.length===1')
        checks.append('Disposed drop handling cannot import a second time')
        page.evaluate('''() => {window.reads=0;window.cache=images.createImageCache(async()=>{reads++;return new Blob(['fixture']);});window.a=cache.acquire('visual_preview','same');window.b=cache.acquire('visual_preview','same');}''')
        assert page.evaluate('''async()=>{const [a,b]=await Promise.all([window.a.promise,window.b.promise]);window.a.release();window.b.release();return a===b&&reads===1;}''')
        checks.append('Reader and illustrated panel share one image request')
        assert not errors,errors
        checks.append('No uncaught browser exceptions')
        browser.close()
    print(json.dumps({'scope':'Actual navigation/cache/drop helpers with browser DOM and synthetic file events; no full React or native Tauri runtime','checks':checks,'passed':len(checks)},indent=2))

if __name__=='__main__':main()
