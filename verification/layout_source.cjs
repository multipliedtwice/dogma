const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const root = path.resolve(__dirname, '..');
const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const flatten = value => Array.isArray(value) ? value.flat(Infinity).filter(v => v !== null && v !== undefined && v !== false) : [value];
const classNames = (...args) => args.flat(Infinity).filter(Boolean).map(v => typeof v === 'object' ? Object.keys(v).filter(k => v[k]).join(' ') : v).join(' ');

function sourceTree(file, fixture = {}, props = {}) {
  const cache = new Map();
  const Fragment = Symbol('Fragment');
  const h = (tag, props, ...children) => ({tag, props: {...props, children: children.length ? children : props?.children}});
  const refs = [];
  const paths = {
    Play:'M8 5v14l11-7Z', Pause:'M8 5v14M16 5v14', SkipBack:'M5 5v14M19 5v14L8 12Z', SkipForward:'M19 5v14M5 5v14l11-7Z',
    ChevronDown:'m6 9 6 6 6-6', ChevronRight:'m9 6 6 6-6 6', ChevronLeft:'m15 6-6 6 6 6',
    RotateCcw:'M3 9V3m0 6h6M3 9a9 9 0 1 1 0 6', Plus:'M12 5v14M5 12h14', X:'m6 6 12 12M6 18 18 6',
    Check:'m5 12 4 4L19 6', CheckCheck:'m2 12 4 4L16 6m-4 10 8-10', ArrowRight:'M4 12h16m-6-6 6 6-6 6',
    Search:'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14m5 12 6 6', BookOpen:'M12 5v16M12 5C8 2 3 3 3 3v16s5-1 9 2c4-3 9-2 9-2V3s-5-1-9 2Z',
    Library:'M4 3v18M9 3v18m5-17 5 16', Headphones:'M4 14v-3a8 8 0 0 1 16 0v3M4 13h3v8H4Zm13 0h3v8h-3Z',
    Download:'M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4', Upload:'M12 16V3m-5 5 5-5 5 5M5 17v4h14v-4',
    Settings2:'M4 7h16M4 17h16M9 4v6m6 4v6', Image:'M3 3h18v18H3Zm0 13 5-5 5 5 4-4 4 4',
    Timer:'M9 2h6m-3 3v2m0 4v5m-8-1a8 8 0 1 0 16 0 8 8 0 0 0-16 0',
    Bookmark:'M6 3h12v18l-6-4-6 4Z', Trash2:'M3 6h18M8 6V3h8v3M5 6l1 15h12l1-15M10 10v7m4-7v7',
    PanelLeftClose:'M3 3h18v18H3Zm5 0v18m8-14-4 5 4 5', PanelLeftOpen:'M3 3h18v18H3Zm5 0v18m4-14 4 5-4 5',
    Type:'M4 4h16m-8 0v16m-4 0h8', Users:'M8 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6m-6 17v-3a6 6 0 0 1 12 0v3m2-16a3 3 0 0 1 0 6m1 3a5 5 0 0 1 5 5v2',
    ShieldCheck:'M12 3 3 6v6c0 5 9 9 9 9s9-4 9-9V6Zm-5 9 3 3 6-6',
  };
  const icons = new Proxy({}, {get: (_, name) => ({size = 16, children, ...rest}) => h('svg', {width:size,height:size,viewBox:'0 0 24 24',fill:'none',stroke:'currentColor',strokeWidth:1.7,strokeLinecap:'round',strokeLinejoin:'round','aria-hidden':'true',...rest},h('path',{d:paths[name] || 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18M8 12h8'}))});
  const React = {useCallback: fn => fn, useMemo: fn => fn(), useRef: value => {const ref={current:value};refs.push(ref);return ref;}, useState: value => [typeof value==='function'?value():value,()=>{}], useEffect:()=>{}, useLayoutEffect:()=>{}, useSyncExternalStore:(_subscribe,getSnapshot)=>getSnapshot(), memo:fn=>fn, Fragment,
    forwardRef: fn => props => fn(props,null)};
  const state = (file, name, initial) => [Object.hasOwn(fixture.states || {}, `${file}:${name}`) ? fixture.states[`${file}:${name}`] : Object.hasOwn(fixture.states || {}, name) ? fixture.states[name] : typeof initial==='function'?initial():initial,()=>{}];
  const expand = value => {
    if (value===null || value===undefined || typeof value==='boolean') return null;
    if (Array.isArray(value)) return value.map(expand).flat(Infinity).filter(v=>v!==null);
    if (typeof value !== 'object') return value;
    if (value.tag===Fragment) return expand(value.props.children);
    if (typeof value.tag==='function') return expand(value.tag(value.props || {}));
    return {...value,props:{...value.props,children:expand(value.props.children)}};
  };
  const load = filename => {
    filename=path.resolve(root,filename);
    if(cache.has(filename))return cache.get(filename);
    const relative=path.relative(root,filename).replaceAll(path.sep,'/');
    const source=fs.readFileSync(filename,'utf8');
    const transformer=context=>{
      const visit=node=>{
        if(ts.isVariableDeclaration(node)&&ts.isArrayBindingPattern(node.name)&&node.initializer&&ts.isCallExpression(node.initializer)&&node.initializer.expression.getText()==='useState'){
          const name=node.name.elements[0].name.text;
          return ts.factory.updateVariableDeclaration(node,node.name,node.exclamationToken,node.type,
            ts.factory.createCallExpression(ts.factory.createIdentifier('__layoutState'),undefined,[ts.factory.createStringLiteral(relative),ts.factory.createStringLiteral(name),node.initializer.arguments[0] || ts.factory.createIdentifier('undefined')]));
        }
        return ts.visitEachChild(node,visit,context);
      };return node=>ts.visitNode(node,visit);
    };
    const code=ts.transpileModule(source,{fileName:filename,transformers:{before:[transformer]},compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,jsxFactory:'h',jsxFragmentFactory:'Fragment',esModuleInterop:true}}).outputText;
    const requireSource=request=>{
      if(request==='react')return React;
      if(request==='lucide-react')return icons;
      if(request==='clsx')return {clsx:classNames};
      if(request==='tailwind-merge')return {twMerge:value=>value};
      if(request==='class-variance-authority')return {cva:(base,config)=>options=>classNames(base,...Object.entries(config.variants).map(([key,values])=>values[options?.[key]||config.defaultVariants[key]]),options?.className)};
      if(request==='@radix-ui/react-slot')return {Slot:props=>h('span',props,props.children)};
      if(request==='@radix-ui/react-dialog')return {Root:props=>props.open?props.children:null,Portal:props=>props.children,Overlay:props=>h('div',props),Content:props=>h('section',{role:'dialog',...props},props.children),Close:props=>h('button',props,props.children),Title:props=>h('h2',props,props.children),Description:props=>h('p',props,props.children)};
      if(request.startsWith('@tauri-apps'))return {invoke:async()=>{throw Error('No native calls in a layout fixture');}};
      const resolved=path.resolve(path.dirname(filename),request);
      if(resolved===path.join(root,'src/lib/usePlayback'))return {usePlayback:()=>({...fixture.player,select:()=>{},seekChapterTrack:()=>{},stop:async()=>{},toggle:()=>{},skip:()=>{},seek:()=>{},setSleepAt:()=>{}})};
      if(resolved===path.join(root,'src/lib/api'))return {native:()=>fixture.native===true,hostedListenerConfig:()=>fixture.listener||{enabled:false,base:'',target:null},api:async()=>{throw Error('No network in a layout fixture');},isRuntimeLoss:()=>false,runtimeConnection:{subscribe:()=>()=>{},lost:()=>fixture.runtimeLost===true}};
      if(resolved===path.join(root,'src/components/Media'))return {
        AssetImage:props=>h('span',{className:'asset-image '+(props.className||'')},h('img',{alt:props.alt||'',src:fixture.assets?.[props.id]||'',loading:'eager'})),
        BookMarkup:({block})=>h('div',{dangerouslySetInnerHTML:{__html:block.html}}),
      };
      return load(fs.existsSync(resolved+'.tsx')?resolved+'.tsx':resolved+'.ts');
    };
    const module={exports:{}};
    new Function('require','module','exports','h','Fragment','__layoutState',code)(requireSource,module,module.exports,h,Fragment,state);
    cache.set(filename,module.exports);
    return module.exports;
  };
  const exported=load(file);
  return {tree:expand(h(exported.default || exported[props.component || Object.keys(exported)[0]],props)),refs};
}

function html(node) {
  if(node===null||node===undefined||typeof node==='boolean')return '';
  if(Array.isArray(node))return node.map(html).join('');
  if(typeof node!=='object')return escape(node);
  let inner=node.props?.dangerouslySetInnerHTML?.__html ?? html(node.props?.children);
  if(node.tag==='select'){
    const wanted=String(node.props.value);
    const children=flatten(node.props.children);
    inner=children.map(child=>html(child&&typeof child==='object'&&child.tag==='option'?{...child,props:{...child.props,selected:String(child.props.value)===wanted}}:child)).join('');
  }
  const attrs=Object.entries(node.props||{}).filter(([key,value])=>value!==undefined&&value!==null&&!['children','dangerouslySetInnerHTML','ref','key','asChild'].includes(key)&&!key.startsWith('on')&&typeof value!=='function').map(([key,value])=>{
    if(key==='className')key='class';
    if(key==='htmlFor')key='for';
    if(key==='style')value=Object.entries(value).map(([k,v])=>`${k.startsWith('--')?k:k.replace(/[A-Z]/g,l=>'-'+l.toLowerCase())}:${typeof v==='number'&&!['lineHeight','opacity','zIndex','flex','order','fontWeight'].includes(k)?v+'px':v}`).join(';');
    if(typeof value==='boolean'&&!key.startsWith('aria-'))return value?` ${key}`:'';
    return ` ${key}="${escape(value)}"`;
  }).join('');
  return `<${node.tag}${attrs}>`+(['input','img','br','hr','meta','link'].includes(node.tag)?'':inner+`</${node.tag}>`);
}

module.exports={sourceTree,html};

if(require.main===module){
  const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
  const rendered=sourceTree('src/App.tsx',fixture);
  fs.writeFileSync(process.argv[3],`<!doctype html><html lang="${fixture.states.settings.language}" data-theme="${fixture.states.settings.theme}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Dogma — source layout fixture</title><style>${fs.readFileSync(process.argv[4],'utf8')}\n${fs.readFileSync(path.join(root,'src/player.css'),'utf8')}</style></head><body>${html(rendered.tree)}</body></html>`);
}
