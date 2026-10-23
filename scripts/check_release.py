from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
sys.path.append(str(Path(__file__).resolve().parent))
from package_runtime import source_digest
from rimsky import __version__
from rimsky.packs import load_pack
from rimsky.util import AppError
from rimsky.opening import load_opening_pack

ROOT=Path(__file__).resolve().parents[1]
TARGETS=('windows','macos','linux')
COMPONENTS=('core','infer','align')
START_CHECKS=(('core','rimsky-runtime',('--version',)),('infer','rimsky-infer',()),('align','rimsky-align',()),('media','ffmpeg',('-version',)),('media','ffprobe',('-version',)))
HOMEBREW=('/opt/homebrew/','/usr/local/Cellar/','/usr/local/opt/')
FFMPEG_LIBRARIES=('libav','libsw','libpostproc')

def host_target():
    return 'windows' if sys.platform.startswith('win') else 'macos' if sys.platform=='darwin' else 'linux'

def packaged(runtime:Path, folder:str, name:str, target:str):
    return runtime/folder/(name+'.exe' if target=='windows' else name)

def executable_issue(runtime:Path, folder:str, name:str, target:str):
    path=packaged(runtime,folder,name,target)
    if not path.is_file():
        return f'Missing packaged executable: runtime/{folder}/{path.name}'
    if target!='windows' and os.name!='nt' and not path.stat().st_mode&0o111:
        return f'Packaged executable is not executable: runtime/{folder}/{path.name}'
    return None

def run_check(command, timeout=120):
    try:
        result=subprocess.run([str(part) for part in command],stdin=subprocess.DEVNULL,capture_output=True,timeout=timeout)
    except subprocess.TimeoutExpired:
        return None,'',f'no exit after {timeout} s'
    except OSError as error:
        return None,'',str(error)
    return result.returncode,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace')

def brief(text:str):
    return ' '.join(text.split())[-300:]

def build_issues(root:Path, runtime:Path):
    current=source_digest(root)
    digests={}
    issues=[]
    for folder in COMPONENTS:
        if not (runtime/folder).is_dir():
            continue
        try:
            info=json.loads((runtime/folder/'build-info.json').read_text('utf-8'))
        except (OSError,ValueError):
            info=None
        value=info.get('source_digest') if isinstance(info,dict) else None
        if isinstance(value,str) and len(value)==64:
            digests[folder]=value
        else:
            issues.append(f'runtime/{folder}/build-info.json does not record a source digest. Rebuild this component with scripts/package_runtime.py.')
    if len(set(digests.values()))>1:
        issues.append('Runtime components were built from different sources ('+', '.join(f'{folder} {value[:12]}' for folder,value in digests.items())+'). Rebuild them from one checkout.')
    stale=[folder for folder,value in digests.items() if value!=current]
    if stale:
        issues.append(f'Runtime components do not match the current source {current[:12]}: {", ".join(stale)}. Rebuild them.')
    return issues

def start_issues(runtime:Path, target:str):
    issues=[]
    for folder,name,arguments in START_CHECKS:
        if executable_issue(runtime,folder,name,target):
            continue
        path=packaged(runtime,folder,name,target)
        code,output,error=run_check([path,*arguments])
        label=f'runtime/{folder}/{path.name}'
        if code!=0:
            issues.append(f'{label} failed its start check: {brief(error or output) or f"exit status {code}"}')
        elif folder=='core':
            try:
                reported=json.loads(output)['version']
            except (ValueError,TypeError,KeyError):
                reported=None
            if reported!=__version__:
                issues.append(f'{label} reports version {reported!r}, but the source declares {__version__}.')
    return issues

def native_files(runtime:Path, target:str):
    names={packaged(runtime,folder,name,target).name for folder,name,_ in START_CHECKS}
    for folder in ('core','infer','align','media'):
        for path in sorted((runtime/folder).rglob('*')) if (runtime/folder).is_dir() else ():
            if path.is_file() and not path.is_symlink() and (path.name in names or path.suffix in ('.so','.dylib') or '.so.' in path.name):
                yield path

def foreign_libraries(path:Path, output:str, target:str):
    entries=[line.strip() for line in output.splitlines() if line.startswith('\t')]
    if target=='macos':
        found=[entry.split(' (')[0] for entry in entries if entry.startswith(HOMEBREW)]
        if found:
            _,identity,_=run_check(['otool','-D',path])
            found=[entry for entry in found if entry not in identity.splitlines()[1:]]
        return found
    media=str(path.parent.resolve())
    return [entry.split(' =>')[0] for entry in entries if '=> not found' in entry
            or path.parent.name=='media' and entry.startswith(FFMPEG_LIBRARIES) and '=>' in entry and not entry.split('=>',1)[1].strip().startswith(media)]

def linkage_issues(runtime:Path, target:str):
    tool={'macos':['otool','-L'],'linux':['ldd']}.get(target)
    files=list(native_files(runtime,target)) if tool else []
    if not files:
        return []
    if not shutil.which(tool[0]):
        return [f'{tool[0]} is not available, so packaged shared-library dependencies were not inspected.']
    issues=[]
    for path in files:
        code,output,error=run_check([*tool,path])
        label=f'runtime/{path.relative_to(runtime).as_posix()}'
        found=foreign_libraries(path,output,target) if code==0 else []
        if found:
            issues.append(f'{label} loads libraries from outside the package: {", ".join(found[:4])}')
        elif code!=0 and target=='macos':
            issues.append(f'{label} could not be inspected with otool: {brief(error or output) or f"exit status {code}"}')
    return issues

def release_issues(root:Path, require_acceptance=True, target=None, execute=None):
    target=target or host_target()
    if target not in TARGETS:
        raise ValueError(f'Unknown target platform: {target}')
    issues=[]
    runtime=root/'resources'/'runtime'
    try:
        load_opening_pack(root / 'resources')
    except AppError as error:
        issues.append(f'Approved Dogma branding is missing or invalid: {error}')
    for folder,name in [('core','rimsky-runtime'),('infer','rimsky-infer'),('media','ffmpeg'),('media','ffprobe')]:
        issue=executable_issue(runtime,folder,name,target)
        if issue:
            issues.append(issue)
    issues.extend(build_issues(root,runtime))
    if execute is None:
        execute=target==host_target()
    if execute:
        issues.extend(start_issues(runtime,target)+linkage_issues(runtime,target))
    elif require_acceptance:
        issues.append(f'Packaged binaries were not started on this host; run the release check on {target}.')
    manifests=list((root/'resources'/'packs').glob('*/pack.json'))
    if not manifests:
        issues.append('No complete offline model/voice pack is bundled.')
    for manifest in manifests:
        try:
            pack=load_pack(str(manifest.parent),verify=True)
            align=pack.get('aligner') and executable_issue(runtime,'align','rimsky-align',target)
            if align:
                issues.append(f'{manifest.parent.name}: alignment model is included but the isolated alignment runtime is unusable: {align}')
            if not isinstance(pack.get('rights'),dict) or not pack['rights'].get('voice_assets'):
                issues.append(f'{manifest.parent.name}: record the basis for distributing voice assets in rights.voice_assets.')
            if pack.get('training') and (not isinstance(pack.get('rights'), dict) or pack['rights'].get('redistribution_authorized') is not True):
                issues.append(f'{manifest.parent.name}: trained narrator pack is private; redistribution authorization is not recorded.')
            if not (manifest.parent/'LICENSES').is_dir() or not any((manifest.parent/'LICENSES').iterdir()):
                issues.append(f'{manifest.parent.name}: missing model/voice licence files.')
        except (AppError,ValueError,TypeError,KeyError) as error:
            issues.append(f'{manifest}: {error}')
    if not (runtime/'LICENSES').is_dir() or not any((runtime/'LICENSES').iterdir()):
        issues.append('Include exact runtime/FFmpeg dependency licence files in resources/runtime/LICENSES.')
    if not require_acceptance:
        return issues
    evidence=root/'release-acceptance.json'
    if not evidence.is_file():
        issues.append('Missing release-acceptance.json with recorded target-device narration and offline-install results.')
    else:
        try:
            data=json.loads(evidence.read_text('utf-8'))
            if not isinstance(data, dict):
                raise ValueError('not an object')
        except (ValueError, OSError):
            issues.append('Release acceptance is not a readable JSON object.')
            return issues
        for field in ('native_build','offline_install','local_inference','russian_casting','voice_continuity','exports','resume'):
            if data.get(field) is not True:
                issues.append(f'Release acceptance not recorded: {field}')
        if not data.get('hardware') or not data.get('model_revisions'):
            issues.append('Acceptance must record actual hardware and model revisions.')
    return issues

def main():
    parser=argparse.ArgumentParser(description='Fail closed when an offline installer lacks required resources or acceptance evidence.')
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--resources-only',action='store_true',help='Check assets before a native acceptance build; does not approve distribution.')
    parser.add_argument('--target',choices=TARGETS,default=host_target(),help='Platform whose executable names and permissions are required.')
    args=parser.parse_args()
    issues=release_issues(args.root,require_acceptance=not args.resources_only,target=args.target)
    if issues:
        print('Not ready to package an offline application:')
        for issue in issues:
            print(' - '+issue)
        raise SystemExit(1)
    print('Resource checks passed; distribution acceptance is still required.' if args.resources_only else 'Resource and recorded-acceptance checks passed. This does not replace platform signing or hands-on verification.')

if __name__=='__main__':
    main()
