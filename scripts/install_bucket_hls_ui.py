"""Install HLS transport into existing bucket UI assets without rebuilding user data."""
from __future__ import annotations

import argparse
import gzip
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MARKER = '// Bucket HLS transport hooks v1'


def patch_split_media(js: str) -> str:
    if 'BucketHls?.registerCut(media.url' not in js:
        old = '    this.media.set(range.id,media);return media;'
        if old not in js:
            raise ValueError('Split media adapter mismatch')
        js = js.replace(old, "    globalThis.BucketHls?.registerCut(media.url,{source:url,parent_audio_sha256:this.plan.clips[id].audio_sha256,first_frame:range.first_frame,last_frame:range.last_frame,sha256:media.sha256});\n" + old)
    return js


def patch_review(js: str) -> str:
    if MARKER in js:
        js = re.sub(r'<audio\b[^>]*?\bsrc=', lambda match: match.group(0)[:-4] + 'data-bucket-source=', js)
        if 'function acceptFullyPlayedProposal(audio){\n  if(globalThis.BucketHls?.isHls(audio))return;' not in js:
            js = js.replace('function acceptFullyPlayedProposal(audio){', 'function acceptFullyPlayedProposal(audio){\n  if(globalThis.BucketHls?.isHls(audio))return;')
        js = js.replace('const audio=field.querySelector(\'audio\');audio.src=media.url;audio.hidden=false;await audio.play();break;',
                        "const audio=field.querySelector('audio');audio.hidden=false;await globalThis.BucketHls.load(audio,media.url,{sha256:media.sha256});await audio.play();break;")
        return js
    # Convert only HTML audio source attributes, retaining original URLs for
    # split verification, immutable identity, downloads and explicit WAV access.
    js = re.sub(r'<audio\b[^>]*?\bsrc=', lambda match: match.group(0)[:-4] + 'data-bucket-source=', js)
    old = "audio.preload='metadata';audio.dataset.clip=id;audio.src=audioURL(precision[id].audio);audio.load();"
    if old not in js:
        raise ValueError('Sequence audio adapter mismatch')
    js = js.replace(old, "audio.preload='metadata';audio.dataset.clip=id;globalThis.BucketHls.load(audio,audioURL(precision[id].audio),{sha256:precision[id].audio_sha256,offset:time}).catch(onError);")
    js = js.replace('const cancel=()=>finish(false),onError=()=>finish(false);', 'const cancel=()=>{globalThis.BucketHls?.cancel(audio);finish(false);},onError=()=>finish(false);')
    js = js.replace('cursorTime=audio.currentTime', 'cursorTime=globalThis.BucketHls?.sourceTime(audio)??audio.currentTime')
    old_guard = 'if(audio.currentSrc!==new URL(audioURL(precision[id].audio),location.href).href)return;'
    if old_guard not in js:
        raise ValueError('Exact WAV listening guard mismatch')
    js = js.replace(old_guard, 'if(!globalThis.BucketHls?.isOriginal(audio,audioURL(precision[id].audio)))return;')
    if 'function acceptFullyPlayedProposal(audio){' in js:
        js = js.replace('function acceptFullyPlayedProposal(audio){', 'function acceptFullyPlayedProposal(audio){\n  if(globalThis.BucketHls?.isHls(audio))return;')
    js = js.replace('const audio=field.querySelector(\'audio\');audio.src=media.url;audio.hidden=false;await audio.play();break;',
                    "const audio=field.querySelector('audio');audio.hidden=false;await globalThis.BucketHls.load(audio,media.url,{sha256:media.sha256});await audio.play();break;")
    return MARKER + '\n' + js


def write_asset(path: Path, data: bytes) -> None:
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)
    zipped = path.with_name(path.name + '.gz')
    if zipped.exists():
        zipped.write_bytes(gzip.compress(data, mtime=0))


def install(board: Path) -> None:
    board = Path(board).resolve()
    review = board / 'bucket-review.js'
    if not review.is_file():
        raise FileNotFoundError(review)
    write_asset(review, patch_review(review.read_text(encoding='utf-8')).encode('utf-8'))
    splits = board / 'bucket-splits.js'
    if splits.exists():
        write_asset(splits, patch_split_media(splits.read_text(encoding='utf-8')).encode('utf-8'))
    write_asset(board / 'bucket-hls.js', (ROOT / 'scripts/bucket_hls.js').read_bytes())
    for name in ('index.html', 'buckets.html'):
        path = board / name
        if not path.exists():
            continue
        html = path.read_text(encoding='utf-8')
        if 'bucket-hls.js' not in html:
            scripts = '<script src="/_clip_hls/hls.min.js"></script><script src="bucket-hls.js?v=1"></script>'
            html = re.sub(r'(?=<script\b)', lambda _: scripts, html, count=1)
        html = html.replace('These are the exact sentence WAVs used for training. Playing a clip does not approve it automatically.',
            'Original WAVs remain the review and training sources. Playback uses an AAC/HLS listening copy; choose Original WAV for exact audio-edge checks. Playing does not approve training.')
        html = html.replace('completing playback records that review.', 'HLS playback records separate listening evidence; choose Original WAV for exact-media playback checks.')
        if '.bucket-hls-status{' not in html:
            html = html.replace('</style>', '.bucket-hls-status{margin:2px 0 10px}.bucket-hls-status button{font-size:.8rem;padding:3px 8px}\n</style>')
        write_asset(path, html.encode('utf-8'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board', action='append', required=True)
    for target in parser.parse_args().board:
        install(Path(target))
