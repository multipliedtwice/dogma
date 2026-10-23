from __future__ import annotations
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--screenshots', type=Path, required=True)
    parser.add_argument('--chromium', default='/usr/bin/chromium')
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium, headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        for width, height in [(1440, 920), (900, 620), (600, 650)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.set_content(args.fixture.read_text(), wait_until='load')
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'), width
            consent = page.locator('.illustration-run .consent input')
            assert consent.count() == 1
            assert not consent.is_checked()
            assert page.get_by_role('button', name='Retry unfinished images', exact=True).is_disabled()
            final = page.get_by_role('button', name='Finish saved run', exact=True)
            assert final.is_enabled()
            for control in [consent, final]:
                control.scroll_into_view_if_needed()
                box = control.bounding_box()
                dialog = page.get_by_role('dialog').bounding_box()
                assert box and dialog and box['width'] > 0 and box['height'] > 0
                assert 0 <= box['x'] <= box['x'] + box['width'] <= width + 1
                assert dialog['y'] <= box['y'] and box['y'] + box['height'] <= dialog['y'] + dialog['height'] + 1
            page.locator('.illustration-run').last.scroll_into_view_if_needed()
            page.evaluate("""() => {
                const note=document.createElement('div');
                note.textContent='SOURCE LAYOUT PREVIEW · CONTROLLED STATE · NO ONLINE GENERATION';
                note.style.cssText='position:fixed;bottom:0;left:0;z-index:9999;font:8px system-ui;background:white;color:black';
                document.body.append(note);
            }""")
            page.screenshot(path=str(args.screenshots / f'illustration-recovery-{width}.png'))
            checks.append({'width': width, 'height': height, 'explicit_retry': True, 'local_finish': True, 'controls_reachable': True})
        browser.close()
    print(json.dumps({'scope': 'Actual source-driven layout, controlled state. Not React effects, Radix focus lifecycle, native execution, or real artwork.', 'configurations': checks}, indent=2))


if __name__ == '__main__':
    main()
