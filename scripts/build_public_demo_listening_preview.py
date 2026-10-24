"""Make an explicitly requested local listening preview from existing audio only.

This does not change recognition decisions, production gates or complete.json.
"""
from __future__ import annotations
import argparse
import html
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
import render_public_audiobook_demo as renderer
from rimsky.audio_join import prepare_clip, gap_seconds
from rimsky.util import file_digest


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def build(book, choices, output, bed_rms_dbfs=None, bed_gap_seconds=None, previous_audio_sha256=None):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    book, output = book.resolve(), output.resolve()
    if output == book or output.is_relative_to(book):
        raise ValueError('Use a separate review workspace, never the canonical book')
    assignment = renderer.read(book/'assignment.json')
    manifest = renderer.read(book/'manifest.json')
    frozen = renderer.read(book/'frozen.json')
    snapshot = renderer.read(book/'finishing/current-resolution.json')
    report = snapshot['report']
    if (frozen['assignment'] != assignment or frozen['manifest'] != manifest
            or renderer.sha(report) != snapshot['report_sha256']):
        raise ValueError('Frozen source or saved recognition report differs')
    rows = {r['id']: r for r in report['units']}
    overrides = {r['unit']: r for r in renderer.read(choices)['units']}
    held = {u['id'] for u in assignment['units'] if not rows[u['id']]['selected']}
    if set(overrides) != held:
        raise ValueError('Every unresolved source unit needs an explicit review selection')
    rate = 24000
    intro, intro_rate = sf.read(renderer.opening(assignment, manifest), dtype='float32', always_2d=True)
    if intro_rate != rate or intro.shape != (8*rate, 2):
        raise ValueError('Approved Russian opening format differs')
    chunks, captions, evidence = [intro], [], []
    position, previous, previous_edge = 8.0, None, None
    for index, unit in enumerate(assignment['units']):
        selected = rows[unit['id']]['selected']
        take = selected['take'] if selected else overrides[unit['id']]['take']
        path = book/'takes'/unit['id']/('take-%02d.json' % take)
        receipt = renderer.read(path)
        if receipt.get('unit') != unit['id'] or receipt.get('quality_passed') is not True:
            raise ValueError('Review selection lacks a source-bound quality-passing take')
        if not selected and file_digest(path) != overrides[unit['id']]['receipt_sha256']:
            raise ValueError('Explicit unresolved selection changed')
        if selected and receipt['audio_sha256'] != selected['audio_sha256']:
            raise ValueError('Saved verified selection changed')
        audio_path = renderer.checked(book/receipt['audio'], receipt['audio_sha256'])
        renderer.quality(audio_path)
        audio, sample_rate = sf.read(audio_path, dtype='float32')
        if sample_rate != rate or audio.ndim != 1:
            raise ValueError('Unexpected selected speech format')
        clip, edge = prepare_clip(audio, rate)
        descriptor = {**unit, 'sentence_index': index, 'scene': unit.get('chapter_id')}
        gap = gap_seconds(previous, descriptor, previous_edge, edge) if previous else 0
        silence = round(gap*rate)
        chunks.append(np.zeros((silence, 2), dtype=np.float32))
        position += silence/rate
        captions.append({'id': unit['id'], 'text': unit['text'], 'start': position,
                         'end': position+len(clip)/rate, 'review_flag': unit['id'] in held})
        chunks.append(np.repeat(clip[:, None], 2, axis=1))
        position += len(clip)/rate
        evidence.append({'unit': unit['id'], 'take': take, 'receipt_sha256': file_digest(path),
                         'audio_sha256': receipt['audio_sha256'], 'recognition_unresolved': unit['id'] in held})
        previous, previous_edge = descriptor, edge
    speech_end = position
    audio = np.concatenate(chunks)
    music = manifest['music']
    bed_level = float(music.get('bed_rms_dbfs', -38) if bed_rms_dbfs is None else bed_rms_dbfs)
    bed_gap = float(music.get('bed_gap_seconds', 20) if bed_gap_seconds is None else bed_gap_seconds)
    if not math.isfinite(bed_level) or not -90 <= bed_level <= 0 or not math.isfinite(bed_gap) or bed_gap < 0:
        raise ValueError('Preview bed level must be finite -90..0 dBFS and gap finite nonnegative seconds')
    if previous_audio_sha256 is not None and (len(previous_audio_sha256) != 64 or any(c not in '0123456789abcdef' for c in previous_audio_sha256)):
        raise ValueError('Previous audio identity must be a lowercase SHA-256')
    def asset(kind):
        data, source_rate = sf.read(renderer.music_asset(music[kind], kind), dtype='float32', always_2d=True)
        if source_rate != rate:
            divisor = math.gcd(source_rate, rate)
            data = resample_poly(data, rate//divisor, source_rate//divisor, axis=0)
        return np.repeat(data, 2, axis=1) if data.shape[1] == 1 else data
    bed, ending = asset('bed')[:20*rate].copy(), asset('ending').copy()
    fade = min(2*rate, len(bed)//2)
    bed[:fade] *= np.linspace(0, 1, fade)[:, None]
    bed[-fade:] *= np.linspace(1, 0, fade)[:, None]
    gain = 10**(bed_level/20)/max(1e-9, float(np.sqrt(np.mean(bed**2))))
    mixed_bed_rms = float(np.sqrt(np.mean((bed*gain)**2)))
    bed_period_samples = len(bed)+round(bed_gap*rate)
    bed_starts = list(range(8*rate, len(audio), bed_period_samples))
    for start in bed_starts:
        count = min(len(bed), len(audio)-start)
        audio[start:start+count] += bed[:count]*gain
    gain = 10**(float(music.get('ending_rms_dbfs', -30))/20)/max(1e-9, float(np.sqrt(np.mean(ending**2))))
    fade_in, fade_out = min(round(1.5*rate), len(ending)), min(7*rate, len(ending))
    ending[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    ending[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    audio = np.concatenate([audio, ending*gain])
    if not np.isfinite(audio).all() or np.max(np.abs(audio)) > .999:
        raise ValueError('Review mix has clipping or nonfinite samples')
    public = output/'public'
    public.mkdir(parents=True, exist_ok=True)
    wav = output/'review-mix.wav'
    sf.write(wav, audio, rate, subtype='PCM_16')
    temporary = output/'review-mix.mp3'
    ffmpeg = manifest['ffmpeg']
    subprocess.run([ffmpeg, '-v', 'error', '-y', '-i', str(wav), '-c:a', 'libmp3lame', '-b:a', '192k', str(temporary)],
                   check=True, timeout=180, **renderer.HIDDEN)
    subprocess.run([ffmpeg, '-v', 'error', '-i', str(temporary), '-f', 'null', '-'], check=True, timeout=180, **renderer.HIDDEN)
    checksum = file_digest(temporary)
    mp3 = public/(checksum+'.mp3')
    if mp3.exists() and file_digest(mp3) != checksum:
        raise ValueError('Existing immutable audio differs')
    if not mp3.exists():
        temporary.replace(mp3)
    status = {'schema': 1, 'preview_only': True, 'complete': False, 'machine_verified': False,
              'source_generation': renderer.sha(frozen),
              'title': assignment['title'], 'language': 'Russian', 'total_units': len(captions),
              'verified_units': report['verified_units'], 'open_recognition_flags': len(held),
              'human_approval': False, 'mp3': mp3.name, 'audio_sha256': checksum,
              'duration': len(audio)/rate, 'speech_end': speech_end, 'captions': captions,
              'mix': {'bed_rms_dbfs': bed_level, 'measured_bed_phrase_rms_dbfs': 20*math.log10(max(mixed_bed_rms, 1e-12)),
                      'bed_gap_seconds': bed_gap, 'bed_phrase_seconds': len(bed)/rate,
                      'bed_period_seconds': bed_period_samples/rate, 'bed_phrase_count': len(bed_starts),
                      'bed_audio_sha256': music['bed']['sha256'], 'ending_audio_sha256': music['ending']['sha256'],
                      'sample_peak': float(np.max(np.abs(audio)))}}
    if previous_audio_sha256 is not None:
        status['previous_audio_sha256'] = previous_audio_sha256
    write(public/'latest.json', status)
    write(output/'review-selection.json', {'saved_report_sha256': snapshot['report_sha256'],
          'user_requested_unresolved_listening_preview': True, 'selections': evidence, 'status': status})
    (public/'index.html').write_text(page(status), encoding='utf-8')
    print(json.dumps({k: status[k] for k in ('mp3', 'duration', 'total_units', 'open_recognition_flags', 'preview_only')}, indent=2))


def page(status):
    data = json.dumps(status, ensure_ascii=False).replace('<', '\\u003c')
    flags = ''.join('<button data-jump="'+str(c['start'])+'">'+html.escape(c['text'])+'</button>'
                    for c in status['captions'] if c['review_flag'])
    paragraphs = ''.join('<p id="'+c['id']+'" data-start="'+str(c['start'])+'" class="'+('flagged' if c['review_flag'] else '')+'">'
                         +html.escape(c['text'])+'</p>' for c in status['captions'])
    return '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Хамелеон — прослушивание · Dogma</title><style>
:root{color-scheme:light dark}body{max-width:850px;margin:2rem auto;padding:0 1rem;font:18px/1.65 system-ui;background:light-dark(#faf8f1,#171b19);color:light-dark(#202822,#e6e9df)}
h1{line-height:1.2}a{color:light-dark(#286443,#a7d8b5)}audio{width:100%}.player{position:sticky;top:0;background:light-dark(#faf8f1,#171b19);padding:1rem 0;z-index:1}button,select{font:inherit;border:1px solid #819180;border-radius:6px;padding:.35rem .65rem;cursor:pointer;background:transparent;color:inherit}.flags button{display:block;margin:.6rem 0;text-align:left}.note{font-size:.9rem;opacity:.8}#reader p{cursor:pointer;padding:.3rem .65rem;margin:.2rem 0;border-left:3px solid transparent}#reader .flagged{border-left-color:#b99140}#reader .active{background:light-dark(#e1ebdd,#2c3d2e);border-radius:5px}footer{margin:3rem 0;font-size:.85rem}details{margin:1rem 0}
</style><h1>Хамелеон</h1><p>Антон Чехов · Полная запись для прослушивания</p>
<p class="note">Запись собрана целиком, с заставкой и музыкой. Четыре расхождения автоматического распознавания оставлены для вашей проверки. Они отмечены в тексте. Повторная генерация остановлена.</p>
<div class="player"><audio id="audio" controls preload="metadata" src="'''+status['mp3']+'''"></audio><label>Скорость <select id="rate"><option>0.75</option><option selected>1</option><option>1.25</option><option>1.5</option><option>2</option></select>×</label> · <a download href="'''+status['mp3']+'''">Скачать MP3</a></div>
<details><summary>Перейти к четырём отмеченным фрагментам</summary><div class="flags">'''+flags+'''</div></details>
<p class="note">Нажмите на абзац, чтобы слушать с этого места. Позиция сохраняется в этом браузере.</p><div id="reader">'''+paragraphs+'''</div>
<footer><p><a href="https://ru.wikisource.org/wiki/Хамелеон_(Чехов)/Версия_2">Оригинал на Викитеке</a> · Dogma</p><p>Музыка: <a href="https://huggingface.co/m-a-p/YuE2-3B">YuE2-3B, Multimodal Art Projection</a>, <a href="https://huggingface.co/audio-cpp/Yue2-3B-GGUF">GGUF для audio.cpp</a>. <a href="https://creativecommons.org/licenses/by-nc/4.0/">CC BY-NC 4.0</a>, некоммерческое использование с указанием авторства.</p><p>Версия для прослушивания, без подтверждения полной автоматической проверки или одобрения человеком.</p></footer>
<script>const story='''+data+''';const a=document.querySelector('#audio'),key='dogma-human-review-ru-'+(story.source_generation||story.audio_sha256);let active;
document.querySelector('#rate').onchange=e=>a.playbackRate=Number(e.target.value);
document.querySelectorAll('[data-jump]').forEach(b=>b.onclick=()=>{a.currentTime=Number(b.dataset.jump);a.play()});
document.querySelectorAll('#reader p').forEach(p=>p.onclick=()=>{a.currentTime=Number(p.dataset.start);a.play()});
a.addEventListener('loadedmetadata',()=>{try{let stored=localStorage.getItem(key);if(stored===null&&story.previous_audio_sha256){stored=localStorage.getItem('dogma-human-review-ru-'+story.previous_audio_sha256);if(stored!==null)localStorage.setItem(key,stored)}const saved=Number(stored);if(saved>0&&saved<a.duration-1)a.currentTime=saved}catch{}});
a.addEventListener('timeupdate',()=>{try{localStorage.setItem(key,String(a.currentTime))}catch{}const c=story.captions.find(x=>x.start<=a.currentTime&&x.end>a.currentTime);if(c?.id!==active){document.querySelector('.active')?.classList.remove('active');if(c)document.getElementById(c.id).classList.add('active');active=c?.id}});
</script></html>'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book', type=Path, required=True)
    parser.add_argument('--choices', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bed-rms-dbfs', type=float, help='Preview-only bed phrase RMS; defaults to the manifest level')
    parser.add_argument('--bed-gap-seconds', type=float, help='Preview-only gap after each bed phrase; defaults to manifest or 20 seconds')
    parser.add_argument('--previous-audio-sha256', help='Previous preview identity for browser bookmark migration')
    args = parser.parse_args()
    build(args.book, args.choices, args.output, args.bed_rms_dbfs, args.bed_gap_seconds, args.previous_audio_sha256)
