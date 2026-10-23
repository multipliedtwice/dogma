"""Publish a read-only audio comparison on the existing labeling server.

WAVs are embedded in the page, so this needs no new routes, server restart or
changes to canonical labels. A separate experiment folder receives only HTML.
"""
import argparse
import base64
import gzip
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def publish(folder, destination):
    content = (folder / 'index.html').read_text('utf-8')
    def embed(match):
        name = match.group(1)
        path = (folder / name).resolve()
        if not path.is_relative_to(folder.resolve()) or path.suffix != '.wav':
            raise ValueError('Listening page references an unexpected audio path.')
        return 'src="data:audio/wav;base64,' + base64.b64encode(path.read_bytes()).decode('ascii') + '"'
    content = re.sub(r'src="([^"]+\.wav)"', embed, content)
    data = content.encode('utf-8')
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'index.html').write_bytes(data)
    (destination / 'index.html.gz').write_bytes(gzip.compress(data, mtime=0))
    return destination / 'index.html'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--destination', type=Path, default=ROOT / 'training-work/character-rb2-full/fine-tune-all/higgs-listening-voice-lora-v1')
    args = parser.parse_args()
    print(publish(args.folder.resolve(), args.destination.resolve()))
