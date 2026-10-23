// Standalone synthetic data only: no real books, reviews or service writes.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import {chromium} from 'playwright';

const template=fs.readFileSync('scripts/local_home.html','utf8');
const prefix='/character-rb2-full/fine-tune-all/elirm-iv-mvp/ch123/';
const bookId='b'.repeat(32),recent='a'.repeat(24),saved='c'.repeat(24);
const generation=id=>({id,name:id===saved?'Saved version':'New version',status:'held',config:{chapters:['c1','c2','c3']},progress:{total:10,ready:10,duration:3144,held:1}});
const library=[{id:bookId,title:'A book <img src=x onerror=alert(1)>',author:'Fixture author',position:{generation_id:saved},generations:[generation(recent),generation(saved)]}];
let role='gateway',local=true,offline=false,origin;
const requests=[];
const server=http.createServer((request,response)=>{
  requests.push({method:request.method,url:request.url});
  if(request.url==='/'){
    const config={schema:1,role,local,apiPrefix:prefix,listeningOrigin:role==='labels'&&!local?null:origin,
      labeling:local?[1,2,3].map(n=>({id:'book'+n,title:'Book '+n,url:origin+'/label/'+n})):[]};
    response.setHeader('Content-Type','text/html; charset=utf-8');response.end(template.replace('__DOGMA_HOME_CONFIG__',JSON.stringify(config)));return;
  }
  if(request.url===prefix+'api'&&request.method==='POST'){
    let body='';request.on('data',chunk=>body+=chunk);request.on('end',()=>{
      assert.equal(JSON.parse(body).op,'library');response.statusCode=offline?503:200;response.setHeader('Content-Type','application/json');response.end(JSON.stringify(offline?{error:'offline'}:library));
    });return;
  }
  response.statusCode=404;response.end();
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
origin='http://127.0.0.1:'+server.address().port;
const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
try{
  const page=await browser.newPage({viewport:{width:1120,height:900}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(origin);
  await page.locator('.book .button').waitFor();
  assert.match(await page.locator('.book h3').innerText(),/<img src=x/);
  assert.equal(await page.locator('.book img').count(),0,'Book metadata must render as text');
  assert.equal(new URL(await page.locator('.book .button').getAttribute('href')).searchParams.get('generation'),saved);
  assert.equal(await page.locator('#label-links a').count(),3);
  assert.match(await page.locator('.book').innerText(),/3 chapters.*52 min/);
  assert.match(await page.locator('.book').innerText(),/held for review/);
  await page.locator('summary').click();
  assert.equal(new URL(await page.locator('details a').getAttribute('href')).searchParams.get('generation'),recent);
  fs.mkdirSync('training-work/local-home-audit',{recursive:true});
  await page.screenshot({path:'training-work/local-home-audit/home-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile page must not overflow horizontally');
  await page.screenshot({path:'training-work/local-home-audit/home-phone.png',fullPage:true});
  offline=true;await page.reload();await page.locator('#retry').waitFor();offline=false;await page.locator('#retry').click();await page.locator('.book .button').waitFor();
  role='labels';await page.reload();
  assert.equal(await page.locator('#books .button').getAttribute('href'),origin+'/#listen');
  local=false;await page.reload();assert.ok(await page.locator('#labeling').isHidden());assert.match(await page.locator('#books').innerText(),/local network/);
  role='gateway';await page.reload();await page.locator('.book .button').waitFor();assert.ok(await page.locator('#labeling').isHidden());
  assert.deepEqual(errors,[]);
  assert.ok(requests.every(r=>r.method==='GET'||r.method==='POST'&&r.url===prefix+'api'));
  console.log('PASS: Home navigation, saved version, rendered/held state, safe metadata, retry, phone layout and local-only labeling.');
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
