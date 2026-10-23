// Pure navigation check: no server, board, or review file is opened or changed.
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';

const source=await readFile('scripts/book_navigation.js','utf8');

function fixture(pathname, fetchImpl=async()=>({ok:true,type:'basic'}), saveImpl=async()=>{}){
  const selector={value:'',disabled:true,options:[],replaceChildren(...options){this.options=options;}};
  const status={textContent:''};
  const surfaces=[{inert:false},{inert:false}];
  const toolbar={home:null,prepend(node){this.home=node;}};
  const storage=new Map();
  const location={pathname,search:'?bucket=test',hostname:'127.0.0.1',origin:'http://127.0.0.1:9000',assigned:null,
    assign(target){this.assigned=String(target);}};
  const store={reviewer:'listener',baseReviewer:'listener',save:saveImpl,changes:()=>({})};
  const context={
    Option:class Option{constructor(label,value){this.label=label;this.value=value;}},
    URL, Object, Error, location, fetch:fetchImpl,
    sessionStorage:{setItem:(key,value)=>storage.set(key,value),getItem:key=>storage.get(key)||null},
    document:{querySelector:selectorText=>selectorText==='#book'?selector:selectorText==='#save-status'?status:selectorText==='.toolbar'?toolbar:selectorText==='#dogma-home'?toolbar.home:null,
      querySelectorAll:()=>surfaces,createElement:()=>({style:{}})},
    reviewConflictControls:()=>{},
  };
  vm.createContext(context);vm.runInContext(source,context);
  context.initialiseBookNavigation(store,()=>{});
  return {selector,status,surfaces,location,storage,home:toolbar.home};
}

{
  const state=fixture('/character-rb3-full/fine-tune-all/');
  assert.deepEqual(state.selector.options.map(option=>[option.value,option.label]),[
    ['book1','Book 1'],['book2','Book 2'],['book3','Book 3']]);
  assert.equal(state.selector.value,'book3');
  assert.equal(state.selector.disabled,false);
  state.selector.value='book2';await state.selector.onchange();
  assert.equal(state.location.assigned,'/character-rb2-full/fine-tune-all/');
  assert.equal(state.storage.get('rimsky-book-location-book3'),'/character-rb3-full/fine-tune-all/?bucket=test');
}

{
  const requests=[];
  const state=fixture('/character-rb1/fine-tune-all/buckets.html',async target=>{
    requests.push(String(target));
    return requests.length===1?{ok:false,type:'basic'}:{ok:false,type:'opaque'};
  });
  state.selector.value='book3';await state.selector.onchange();
  assert.deepEqual(requests,[
    '/character-rb3-full/fine-tune-all/',
    'http://127.0.0.1:1440/character-rb3-full/fine-tune-all/'
  ]);
  assert.equal(state.location.assigned,requests[1]);
}

{
  const state=fixture('/character-rb2-full/fine-tune-all/',async()=>({ok:true,type:'basic'}),async()=>{throw Error('fixture save failure');});
  state.selector.value='book3';await state.selector.onchange();
  assert.equal(state.location.assigned,null);
  assert.equal(state.selector.value,'book2');
  assert.equal(state.selector.disabled,false);
  assert.ok(state.status.textContent.includes('Could not switch books'));
  assert.ok(state.surfaces.every(surface=>surface.inert===false));
}

console.log('PASS: three-book options, Book 3 route and local fallback, saved position, and failed-save retention.');

{
  let saved=0,prevented=false;
  const state=fixture('/character-rb3-full/fine-tune-all/',undefined,async()=>{saved++;});
  assert.equal(state.home.href,'/');
  await state.home.onclick({button:0,preventDefault(){prevented=true;}});
  assert.ok(prevented);assert.equal(saved,1);assert.equal(state.location.assigned,'/');
  const failed=fixture('/character-rb2-full/fine-tune-all/',undefined,async()=>{throw Error('unsaved fixture');});
  await failed.home.onclick({button:0,preventDefault(){}});
  assert.equal(failed.location.assigned,null);assert.match(failed.status.textContent,/Could not open Home/);
}
