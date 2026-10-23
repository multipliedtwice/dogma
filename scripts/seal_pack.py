from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from rimsky.packs import load_pack
from rimsky.util import AppError, file_digest, os_metadata, write_json

def seal(root:Path):
    root=root.resolve()
    manifest=root/'pack.json'
    pack=json.loads(manifest.read_text('utf-8'))
    paths=sorted(p for p in root.rglob('*') if p.is_file() and p!=manifest and not os_metadata(p.name))
    if any(p.is_symlink() or root not in p.resolve().parents for p in root.rglob('*')):
        raise AppError('Symlinks are not accepted in distributable packs.')
    pack['files']={p.relative_to(root).as_posix():file_digest(p) for p in paths}
    original=manifest.read_bytes()
    write_json(manifest,pack)
    try:
        result=load_pack(str(root),verify=True)
    except Exception:
        manifest.write_bytes(original)
        raise
    return {'name':result['name'],'revision':result['_fingerprint'],'files':len(paths)}

def main():
    parser=argparse.ArgumentParser(description='Seal an already prepared, local model and voice pack. Downloads nothing.')
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    print(json.dumps(seal(args.directory),ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
