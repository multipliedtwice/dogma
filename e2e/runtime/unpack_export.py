from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path
from zipfile import ZipFile


def unpack(source, destination):
    with ZipFile(source) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if not entries or len(entries) > 32 or len(set(names)) != len(names):
            raise ValueError('Invalid chapter archive entry count or duplicate names.')
        if sum(item.file_size for item in entries) > 128 * 1024 * 1024:
            raise ValueError('Chapter fixture export exceeds 128 MiB.')
        for item in entries:
            if not re.fullmatch(r'(?:chapters\.json|\d{3}\.(?:mp3|vtt))', item.filename):
                raise ValueError(f'Unexpected chapter archive entry: {item.filename}')
            if item.is_dir() or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('Chapter archive must contain regular files only.')
        damaged = archive.testzip()
        if damaged:
            raise ValueError(f'Invalid chapter archive CRC: {damaged}')
        destination.mkdir(parents=True, exist_ok=False)
        for item in entries:
            with (destination / item.filename).open('xb') as output:
                output.write(archive.read(item))
    return names


def main():
    parser = argparse.ArgumentParser(description='Validate and unpack only the synthetic chapter export.')
    parser.add_argument('archive', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(unpack(args.archive, args.destination)))


if __name__ == '__main__':
    main()
