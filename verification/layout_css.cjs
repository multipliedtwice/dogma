const fs=require('node:fs');
const path=require('node:path');

async function main() {
  const output=process.argv[2];
  if(!output)throw Error('Provide the layout CSS output path.');
  const version=require('tailwindcss/package.json').version;
  const config=(await import('file://'+path.resolve('tailwind.config.js'))).default;
  const source=fs.readFileSync('src/styles.css','utf8');
  if(version.startsWith('3.')){
    const result=await require('postcss')([require('tailwindcss')(config),require('autoprefixer')]).process(source,{from:'src/styles.css'});
    fs.writeFileSync(output,result.css);
  } else {
    const folder=path.dirname(require.resolve('tailwindcss/package.json'));
    const files=[];
    const scan=dir=>{for(const item of fs.readdirSync(dir,{withFileTypes:true}))item.isDirectory()?scan(path.join(dir,item.name)):files.push(path.join(dir,item.name));};
    scan('src');
    const tokens=[...new Set(files.filter(file=>/tsx?$/.test(file)).flatMap(file=>fs.readFileSync(file,'utf8').split(/[\s"'`<>{}]+/)))];
    const input=fs.readFileSync(folder+'/theme.css','utf8')+'\n'+fs.readFileSync(folder+'/preflight.css','utf8')+'\n@config "layout-only";\n@tailwind utilities;\n'+source.replace(/@tailwind[^;]+;/g,'');
    const result=await require('tailwindcss').compile(input,{loadModule:async()=>({module:config,base:process.cwd()})});
    fs.writeFileSync(output,result.build(tokens));
  }
  console.log(JSON.stringify({scope:'source-layout only; not production frontend build',tailwind:version,pinned_version:'3.4.17',exact_version:version==='3.4.17'}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
