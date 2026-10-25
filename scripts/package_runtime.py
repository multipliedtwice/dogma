from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from rimsky.russian_preparation import RUACCENT_REQUIRED_FILES,local_model_pins


def require_preparation_assets(models):
    missing=[name for name in RUACCENT_REQUIRED_FILES if not(models/name).is_file()]
    if missing:raise RuntimeError('Install the reviewed local RUAccent preparation assets before packaging; missing: '+', '.join(missing))
    return models

def source_digest(root:Path=ROOT)->str:
    runtime=root/'runtime'
    digest=hashlib.sha256()
    for path in sorted([*runtime.glob('*.py'),*(runtime/'rimsky').glob('*.py')],key=lambda item:item.relative_to(runtime).as_posix()):
        data=path.read_bytes().replace(b'\r\n',b'\n')
        digest.update(f'{path.relative_to(runtime).as_posix()}\0{len(data)}\0'.encode()+data)
    return digest.hexdigest()

def app_version(root:Path=ROOT)->str:
    match=re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']',(root/'runtime'/'rimsky'/'__init__.py').read_text('utf-8'),re.M)
    if not match:
        raise RuntimeError('runtime/rimsky/__init__.py does not declare __version__.')
    return match.group(1)

def build(kind:str,output:Path):
    modes={'core':('server.py','rimsky-runtime',('lxml','PIL','numpy','soundfile','certifi','pymorphy3','pymorphy3_dicts_ru')),'infer':('worker.py','rimsky-infer',('qwen_tts','qwen_asr','llama_cpp','torch','transformers','librosa','soundfile','numpy','silero_stress','ruaccent','onnxruntime','wordfreq','pymorphy3','pymorphy3_dicts_ru')),'align':('align_worker.py','rimsky-align',('qwen_asr','torch','transformers','soundfile','numpy'))}
    script,name,modules=modes[kind]
    missing=[m for m in ('PyInstaller',*modules) if importlib.util.find_spec(m) is None]
    if missing:
        raise RuntimeError('Activate the appropriate build environment. Missing: '+', '.join(missing))
    target=output/kind
    if target.exists():
        raise RuntimeError(f'{target} already exists; remove it deliberately before rebuilding.')
    info={'component':kind,'platform':platform.system(),'machine':platform.machine(),'python':platform.python_version(),'version':app_version(),'source_digest':source_digest()}
    command=[sys.executable,'-m','PyInstaller','--clean','--noconfirm','--onedir','--console','--name',name,'--paths',str(ROOT/'runtime'),'--distpath',str(ROOT/'build'/'runtime-dist'/kind),'--workpath',str(ROOT/'build'/'pyinstaller'/kind),'--specpath',str(ROOT/'build'/'specs'/kind)]
    for module in modules:
        command+=['--collect-all',module]
    if kind=='infer':
        command+=['--copy-metadata','qwen-tts','--copy-metadata','llama-cpp-python']
        models=require_preparation_assets(ROOT/'training-work/models/ruaccent-1.5.8.3')
        info['russian_preparation_models']=local_model_pins()
        command+=['--add-data',str(models)+':russian-preparation/ruaccent-1.5.8.3']
        for package in ('silero-stress','ruaccent','wordfreq','pymorphy3','pymorphy3-dicts-ru'):
            command+=['--copy-metadata',package]
    if kind=='align':
        command+=['--copy-metadata','qwen-asr']
    command.append(str(ROOT/'runtime'/script))
    subprocess.run(command,check=True,cwd=ROOT)
    output.mkdir(parents=True,exist_ok=True)
    shutil.copytree(ROOT/'build'/'runtime-dist'/kind/name,target)
    (target/'build-info.json').write_text(json.dumps(info,indent=2),'utf-8')
    print(f'Packaged {kind} into {target}. Native execution still needs verification on the target machine.')

def main():
    parser=argparse.ArgumentParser(description='Build one local runtime component in its isolated, already installed environment.')
    parser.add_argument('component',choices=['core','infer','align','media'])
    parser.add_argument('--output',type=Path,default=ROOT/'resources'/'runtime')
    parser.add_argument('--ffmpeg',type=Path)
    parser.add_argument('--ffprobe',type=Path)
    args=parser.parse_args()
    if args.component=='media':
        if not args.ffmpeg or not args.ffprobe or not args.ffmpeg.is_file() or not args.ffprobe.is_file():
            parser.error('media requires existing --ffmpeg and --ffprobe binaries for the target platform')
        target=args.output/'media';target.mkdir(parents=True,exist_ok=True)
        for source,name in [(args.ffmpeg,'ffmpeg'),(args.ffprobe,'ffprobe')]:
            destination=target/(name+'.exe' if source.suffix.lower()=='.exe' else name)
            if destination.exists():
                raise RuntimeError(f'{destination} already exists.')
            shutil.copy2(source,destination)
        print('Copied media binaries. Include their exact build licences and required libraries before release.')
    else:
        build(args.component,args.output)

if __name__=='__main__':
    main()
