import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {python as configuredPython} from '../scripts/python.mjs';

const root=path.resolve('.');

function linkTree(source,target){
  fs.mkdirSync(target,{recursive:true});
  for(const entry of fs.readdirSync(source,{withFileTypes:true})){
    const from=path.join(source,entry.name),to=path.join(target,entry.name);
    if(entry.isDirectory())linkTree(from,to);
    else {try{fs.linkSync(from,to);}catch{fs.copyFileSync(from,to);}}
  }
}

export async function isolatedBook2Board({full=false}={}){
  const boardFolder=full?'character-rb2-full':'character-rb2';
  const audioPrefix=full?'character-rb2/full':'character-rb2/pilot';
  const tempRoot=fs.mkdtempSync(path.join(os.tmpdir(),`rimskify-${boardFolder}-ui-`));
  fs.mkdirSync(path.join(tempRoot,'training'),{recursive:true});
  fs.copyFileSync(path.join(root,'training/voice_families.json'),path.join(tempRoot,'training/voice_families.json'));
  const liveBoard=path.join(root,'training-work',boardFolder,'fine-tune-all');
  const testBoard=path.join(tempRoot,'training-work',boardFolder,'fine-tune-all');
  linkTree(liveBoard,testBoard);
  const liveDraft=path.join(liveBoard,'review-draft.json');
  const testDraft=path.join(testBoard,'review-draft.json');
  fs.unlinkSync(testDraft);fs.copyFileSync(liveDraft,testDraft);
  assert.notEqual(fs.statSync(testDraft).ino,fs.statSync(liveDraft).ino);
  linkTree(path.join(root,'training-work',audioPrefix,'clips'),
           path.join(tempRoot,'training-work',audioPrefix,'clips'));
  const comparison=path.join(root,'manual-labeling/reality-benders-1/audio');
  if(fs.existsSync(comparison))linkTree(comparison,path.join(tempRoot,'manual-labeling/reality-benders-1/audio'));
  const legacyPython=path.join(root,'training-work/.venv-tts/Scripts/python.exe');
  const python=process.env.RIMSKY_PYTHON||(fs.existsSync(legacyPython)?legacyPython:configuredPython());
  const server=spawn(python,['-X','utf8','scripts/serve_label_pilot.py','--host','127.0.0.1','--port','0',
    '--board-folder',boardFolder,'--audio-prefix',audioPrefix,'--root',tempRoot],
    {cwd:root,stdio:['ignore','pipe','pipe']});
  const output=[];server.stderr.on('data',chunk=>output.push(chunk.toString()));
  const base=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('Isolated review server did not start: '+output.join(''))),15000);
    server.once('exit',code=>{clearTimeout(timer);reject(new Error(`Isolated review server exited ${code}: ${output.join('')}`));});
    server.stdout.on('data',chunk=>{const match=chunk.toString().match(/http:\/\/[^\s]+/);if(match){clearTimeout(timer);resolve(match[0]);}});
  });
  return {base,tempRoot,testBoard,close:async()=>{
    if(server.exitCode===null){server.kill();await new Promise(resolve=>server.once('exit',resolve));}
    fs.rmSync(tempRoot,{recursive:true,force:true});
  }};
}
