from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT_FILES = frozenset({
    '.gitignore', '.gitattributes', 'README.md', 'STATUS.md', 'THIRD_PARTY.md',
    'components.json', 'index.html', 'package.json', 'package-lock.json',
    'pnpm-lock.yaml', 'yarn.lock', 'postcss.config.js', 'tailwind.config.js',
    'tsconfig.json', 'vite.config.ts', 'playwright.config.ts',
})
DOCS = frozenset({
    'ARCHITECTURE.md', 'EPUB-PREPARATION.md', 'MODEL_PACKS.md', 'PACKAGING.md',
    'PARAGRAPH-ILLUSTRATIONS.md', 'TRAINING.md', 'TRAINING-DATA.md', 'VERIFICATION.md', 'E2E.md',
    'LABELING.md',
})
OMIT_DIRS = frozenset({
    'node_modules', 'target', 'dist', 'build', '__pycache__', 'coverage',
    'screenshots', 'previews', 'test-results', 'playwright-report', 'blob-report',
})
SOURCE_SUFFIXES = frozenset({'.py', '.ts', '.tsx', '.js', '.cjs', '.mjs', '.css', '.html', '.json', '.sql'})
IMAGE_SUFFIXES = frozenset({'.svg', '.png', '.ico', '.icns', '.jpg', '.jpeg', '.webp'})


def included(path: PurePosixPath) -> bool:
    parts = path.parts
    name = path.name
    if any(part in OMIT_DIRS or part.startswith('.') for part in parts[:-1]):
        return False
    if len(parts) == 1:
        return name in ROOT_FILES or name.upper().startswith(('LICENSE', 'NOTICE', 'COPYING'))
    if name.startswith('.'):
        return path.as_posix() in {'resources/runtime/.gitkeep', 'resources/packs/.gitkeep'}
    if parts[0] == 'docs':
        return len(parts) == 2 and name in DOCS
    if parts[0] == 'e2e':
        return ((len(parts) == 2 and (path.suffix in {'.ts', '.mjs'} or name == 'tsconfig.json'))
                or (len(parts) == 3 and parts[1] == 'runtime' and path.suffix == '.py')
                or (len(parts) == 3 and parts[1] == 'specs' and name.endswith('.spec.ts')))
    if parts[0] == 'resources':
        return (len(parts) == 4 and parts[1:3] == ('branding', 'dogma-opening-v1')
                and name in {'manifest.json', 'ru-speech.wav', 'ru-music.wav', 'en-speech.wav', 'en-music.wav'})
    if parts[0] == 'packs':
        return len(parts) == 2 and name.endswith('.example.json')
    if parts[0] == 'training':
        return ((len(parts) == 2 and name.startswith('requirements') and path.suffix == '.txt')
                or (parts[1] == 'recipes' and path.suffix == '.json')
                or (parts[1] == 'rimsky_training' and path.suffix == '.py'))
    if parts[0] == 'src-tauri':
        return ((len(parts) == 2 and name in {'Cargo.toml', 'Cargo.lock', 'build.rs', 'tauri.conf.json'})
                or (parts[1] == 'src' and path.suffix == '.rs')
                or (parts[1] == 'icons' and path.suffix in IMAGE_SUFFIXES)
                or (parts[1] == 'capabilities' and path.suffix == '.json'))
    if parts[0] in {'src', 'public', 'runtime', 'scripts', 'verification'}:
        return (path.suffix in SOURCE_SUFFIXES
                or (name.startswith('requirements') and path.suffix == '.txt')
                or (parts[0] in {'src', 'public'} and path.suffix in IMAGE_SUFFIXES))
    return False


def source_files(root: Path) -> list[Path]:
    selected: list[Path] = []
    for directory, subdirs, names in os.walk(root, followlinks=False):
        subdirs[:] = [name for name in subdirs if name not in OMIT_DIRS and not name.startswith('.')]
        for name in names:
            path = Path(directory) / name
            if not included(PurePosixPath(path.relative_to(root).as_posix())):
                continue
            if path.is_symlink():
                raise ValueError(f'Source symlinks are not supported: {path.relative_to(root)}')
            if path.is_file():
                selected.append(path)
    return sorted(selected, key=lambda path: path.relative_to(root).as_posix())


def add_file(archive: ZipFile, name: str, content: bytes, executable: bool = False) -> None:
    item = ZipInfo(f'rimskifikator/{name}', date_time=(1980, 1, 1, 0, 0, 0))
    item.create_system = 3
    item.external_attr = (0o100755 if executable else 0o100644) << 16
    archive.writestr(item, content, compress_type=ZIP_DEFLATED, compresslevel=9)


def package_source(root: Path, output: Path) -> dict[str, int | str]:
    root = root.resolve(strict=True)
    if not (root / 'package.json').is_file():
        raise ValueError('Source root must contain package.json')
    selected = source_files(root)
    with output.open('xb') as stream:
        try:
            with ZipFile(stream, 'w') as archive:
                manifest = []
                for path in selected:
                    name = path.relative_to(root).as_posix()
                    content = path.read_bytes()
                    add_file(archive, name, content, bool(path.stat().st_mode & 0o111))
                    manifest.append(f'{hashlib.sha256(content).hexdigest()}  {name}\n')
                add_file(archive, 'SOURCE_MANIFEST.sha256', ''.join(manifest).encode('utf-8'))
        except BaseException:
            stream.close()
            output.unlink(missing_ok=True)
            raise
    return {'archive': str(output.resolve()), 'files': len(selected) + 1, 'bytes': output.stat().st_size}


def main() -> None:
    parser = argparse.ArgumentParser(description='Create a source-only ZIP without historical audit output or private assets.')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = package_source(Path(__file__).resolve().parents[1], args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
