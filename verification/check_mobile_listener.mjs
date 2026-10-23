import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import {chromium} from 'playwright';

const url=process.argv[2];
if(!url)throw new Error('Usage: node verification/check_mobile_listener.mjs <listener-url>');

const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
try{
  for(const width of [390,360]){
    const page=await browser.newPage({viewport:{width,height:844}});
    await page.goto(url,{waitUntil:'domcontentloaded',timeout:30_000});
    await page.waitForSelector('.reading-column',{timeout:30_000});
    await page.waitForTimeout(1_000);

    const layout=await page.evaluate(()=>{
      const box=(selector)=>document.querySelector(selector)?.getBoundingClientRect().toJSON();
      return {
        viewport:{width:innerWidth,height:innerHeight},
        documentWidth:document.documentElement.scrollWidth,
        sidebar:Boolean(document.querySelector('.sidebar')),
        main:box('.main'),
        reader:box('.reading-column'),
        player:box('.player-dock'),
        topbar:box('.topbar'),
      };
    });
    assert.equal(layout.sidebar,false,'the chapter drawer must start closed on a phone');
    assert.equal(layout.documentWidth,width,'the listener must not overflow horizontally');
    assert.ok(layout.main.width>=width-1,'the reader must use the full phone width');
    assert.ok(layout.reader.width>=width-1,'the reading column must use the full phone width');
    assert.ok(layout.player.width>=width-1,'the player must use the full phone width');
    assert.ok(layout.player.bottom<=layout.viewport.height+.5,'the player must remain inside the viewport');
    const touchTargets=await page.locator('.topbar button,.reader-toolbar button,.dock-transport button,.dock-options button,.dock-options select').evaluateAll(elements=>elements.filter(element=>element.getClientRects().length).map(element=>{
      const box=element.getBoundingClientRect();
      return {label:element.getAttribute('aria-label')||element.getAttribute('title')||element.tagName,width:box.width,height:box.height};
    }));
    assert.ok(touchTargets.every(target=>target.width>=44&&target.height>=44),`phone controls must have 44 px touch targets: ${JSON.stringify(touchTargets)}`);

    await page.locator('.topbar button').first().click();
    const drawer=await page.evaluate(()=>({
      sidebar:document.querySelector('.sidebar')?.getBoundingClientRect().toJSON(),
      scrim:document.querySelector('.sidebar-scrim')?.getBoundingClientRect().toJSON(),
    }));
    assert.ok(drawer.sidebar.width<=width*.85,'the chapter drawer must leave a dismiss area');
    assert.equal(drawer.sidebar.height,844,'the chapter drawer must fit the viewport');
    assert.equal(drawer.scrim.width,width,'the drawer scrim must cover the viewport');
    await page.locator('.sidebar-scrim').click({position:{x:width-3,y:400}});
    await page.waitForSelector('.sidebar',{state:'detached'});

    const passage=page.locator('.source-block').filter({has:page.locator('p')}).first();
    await passage.evaluate((element,{x,y})=>{
      const walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT);
      let text;
      while((text=walker.nextNode())&&!/[А-ЯЁа-яё]{3,}/.test(text.textContent||'')){}
      if(!text)throw new Error('No selectable Russian word found');
      const match=(text.textContent||'').match(/[А-ЯЁа-яё]{3,}/);
      const range=document.createRange();
      range.setStart(text,match.index);
      range.setEnd(text,match.index+match[0].length);
      const selection=getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      element.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:x,clientY:y}));
    },{x:width-8,y:836});
    const menu=await page.locator('.stress-menu').boundingBox();
    assert.ok(menu,'selecting a word must open the feedback sheet');
    assert.ok(menu.x>=7&&menu.x+menu.width<=width-7,'the feedback sheet must fit horizontally');
    assert.ok(menu.y>=0&&menu.y+menu.height<=844,'the feedback sheet must fit vertically');

    await page.screenshot({path:path.join(os.tmpdir(),`rimskify-mobile-listener-${width}.png`)});
    console.log(JSON.stringify({width,layout,drawer,stressMenu:menu,touchTargets}));
    await page.close();
  }
}finally{
  await browser.close();
}

console.log('PASS: production listener is usable at 390 px and 360 px phone widths.');
