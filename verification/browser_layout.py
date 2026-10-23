from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--screenshots', type=Path, required=True)
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    views = [('reader', 1440, 920), ('dark', 1440, 920), ('library', 1440, 920),
             ('reader', 900, 620), ('reader', 800, 700), ('reader', 600, 650),
             ('playback-settings', 960, 720), ('export', 960, 720), ('generations', 960, 720),
             ('illustrated', 1440, 920)]
    if (args.fixtures/'illustration.html').is_file():
        views += [('illustration',960,720),('illustrated',900,620),('illustrated',600,650)]
    script="const ts=require('typescript'),fs=require('fs');process.stdout.write(ts.transpileModule(fs.readFileSync('src/lib/readerNavigation.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)"
    navigation=subprocess.check_output(['node','-e',script],cwd=Path(__file__).resolve().parents[1],text=True)
    records = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        for name, width, height in views:
            page.set_viewport_size({'width': width, 'height': height})
            page.set_content((args.fixtures / f'{name}.html').read_text('utf-8'), wait_until='load')
            page.evaluate("source=>{const module={exports:{}};new Function('module','exports',source)(module,module.exports);window.readerCleanup=module.exports.bindReaderViewport(document.querySelector('.reading-scroll'));}",navigation)
            page.wait_for_timeout(100)
            bounds = page.evaluate('''() => {
                const rect = selector => {
                    const element = document.querySelector(selector);
                    if (!element) return null;
                    const r = element.getBoundingClientRect();
                    return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom};
                };
                return {horizontal:document.documentElement.scrollWidth > innerWidth,
                    reader:rect('.reading-scroll'), dock:rect('.player-dock'),
                    play:rect('.dock-play'), cc:rect('.caption-toggle'),
                    settings:rect('.dock-options > button:last-child'),
                    dialog:rect('[role="dialog"]'),
                    rows:[...document.querySelectorAll('.modal-body > .setting-row')].map(row => getComputedStyle(row).flexDirection)};
            }''')
            assert not bounds['horizontal'], (name, width, bounds)
            for selector in ('play', 'cc', 'settings', 'dialog'):
                value = bounds[selector]
                if value:
                    assert value['x'] >= 0 and value['y'] >= 0 and value['right'] <= width + 1 and value['bottom'] <= height + 1, (name, width, selector, value)
            if bounds['reader']:
                assert bounds['reader']['height'] >= 180, (name, width, bounds)
            assert all(direction == 'row' for direction in bounds['rows']), bounds
            if name == 'illustrated':
                fit=page.evaluate("()=>{const root=document.querySelector('.reading-scroll').getBoundingClientRect(),panel=document.querySelector('.paragraph-stage').getBoundingClientRect();return {top:panel.top,bottom:panel.bottom,rootTop:root.top,rootBottom:root.bottom};}")
                assert fit['top']>=fit['rootTop'] and fit['bottom']<=fit['rootBottom']+1,(width,fit)
                page.locator('.reading-scroll').evaluate("(node)=>node.scrollTo({top:850,behavior:'instant'})")
                page.wait_for_timeout(120)
                fit=page.evaluate("()=>{const root=document.querySelector('.reading-scroll').getBoundingClientRect(),panel=document.querySelector('.paragraph-stage').getBoundingClientRect();return {top:panel.top,bottom:panel.bottom,rootTop:root.top,rootBottom:root.bottom};}")
                assert fit['top']>=fit['rootTop'] and fit['bottom']<=fit['rootBottom']+1,(width,fit)
                page.locator('.reading-scroll').evaluate("(node)=>node.scrollTo({top:0,behavior:'instant'})")
            if name == 'illustrated':
                panel=page.locator('.paragraph-stage').bounding_box();image=page.locator('.paragraph-stage > .asset-image').bounding_box();button=page.locator('.paragraph-stage > button').bounding_box()
                assert image and image['height']>=56,(width,image)
                assert button and panel and button['y']>=panel['y'] and button['y']+button['height']<=panel['y']+panel['height']+1,(width,panel,button)
            if name == 'playback-settings':
                note = page.locator('.modal-body > .helper').bounding_box()
                assert note and note['y'] + note['height'] < height - 12, note
            page.evaluate('''() => {
                const label=document.createElement('span');
                label.textContent='SOURCE LAYOUT PREVIEW · CONTROLLED STATE';
                label.style.cssText='position:fixed;left:7px;bottom:3px;z-index:9999;font:7px system-ui;letter-spacing:.3px;color:#777;background:#fff9;padding:1px 4px;pointer-events:none';
                document.body.append(label);
            }''')
            page.screenshot(path=str(args.screenshots / f'{name}-{width}.png'), full_page=False)
            records.append({'view': name, 'viewport': [width, height], 'checks_passed': True, **bounds})
        browser.close()
    print(json.dumps({'scope': 'Actual application JSX/CSS with controlled state and simulated React/Radix/media/icon components. Layout only, not the production frontend or native interaction.', 'screens_checked': len(records), 'screens': records}, indent=2))


if __name__ == '__main__':
    main()
