"""Existing-audio-only derivative of a completed public demo; canonical evidence stays unchanged."""
from __future__ import annotations
import argparse
import copy
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'runtime')]
import github_pages as pages
import package_public_audiobook_demos as package
import render_public_audiobook_demo as renderer
from rimsky.audio_join import prepare_clip
from rimsky.util import file_digest


def build(book, output, site_source, bed_rms_dbfs=-30, bed_gap_seconds=4):
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    book, output, site_source = book.resolve(), output.resolve(), site_source.resolve()
    if output == book or output.is_relative_to(book) or output == site_source or output.is_relative_to(site_source):
        raise ValueError('Use an isolated derivative output, outside the canonical book and website')
    if not math.isfinite(bed_rms_dbfs) or not -90 <= bed_rms_dbfs <= 0 or not math.isfinite(bed_gap_seconds) or bed_gap_seconds < 0:
        raise ValueError('Invalid explicit bed settings')
    payload, evidence = package.completed_book(book, 'en')
    latest = renderer.read(book/'public/latest.json')
    frozen = renderer.read(book/'frozen.json')
    assignment, manifest = frozen['assignment'], frozen['manifest']
    report = renderer.read(book/'public'/latest['recognition_report'])
    rate = 24000
    source_wav = renderer.checked(book/'public'/latest['wav'], latest['wav_sha256'])
    original, sr = sf.read(source_wav, dtype='float32', always_2d=True)
    if sr != rate or original.shape[1] != 2:
        raise ValueError('Unexpected canonical mix format')
    speech = np.zeros((round(latest['speech_end']*rate), 2), np.float32)
    opening, sr = sf.read(renderer.opening(assignment, manifest), dtype='float32', always_2d=True)
    if sr != rate or opening.shape != (8*rate, 2):
        raise ValueError('Approved opening changed')
    speech[:len(opening)] = opening
    selections = []
    for unit, caption, row in zip(assignment['units'], latest['captions'], report['units']):
        selected = row['selected']
        receipt_path = book/'takes'/unit['id']/('take-%02d.json' % selected['take'])
        receipt = renderer.read(receipt_path)
        if (receipt.get('quality_passed') is not True or receipt['unit'] != unit['id']
                or receipt['audio_sha256'] != selected['audio_sha256'] or receipt['audio_sha256'] != caption['raw_audio_sha256']):
            raise ValueError('Selected speech differs from completed source evidence')
        audio_path = renderer.checked(book/receipt['audio'], receipt['audio_sha256'])
        raw, sr = sf.read(audio_path, dtype='float32')
        if sr != rate or raw.ndim != 1:
            raise ValueError('Selected speech format changed')
        clip, _ = prepare_clip(raw, rate)
        start, end = round(caption['start']*rate), round(caption['end']*rate)
        if end-start != len(clip):
            raise ValueError('Selected speech no longer matches exact caption sample boundaries')
        speech[start:end] = clip[:, None]
        selections.append({'unit':unit['id'], 'take':selected['take'], 'audio_sha256':receipt['audio_sha256'],
                           'receipt_sha256':file_digest(receipt_path)})
    music = manifest['music']
    def asset(kind):
        data, sr = sf.read(renderer.music_asset(music[kind], kind), dtype='float32', always_2d=True)
        if sr != rate:
            divisor = math.gcd(sr, rate)
            data = resample_poly(data, rate//divisor, sr//divisor, axis=0)
        return np.repeat(data, 2, axis=1) if data.shape[1] == 1 else data
    bed, ending = asset('bed')[:20*rate].copy(), asset('ending').copy()
    fade = min(2*rate, len(bed)//2)
    bed[:fade] *= np.linspace(0, 1, fade)[:, None]
    bed[-fade:] *= np.linspace(1, 0, fade)[:, None]
    ending_gain = 10**(float(music.get('ending_rms_dbfs', -30))/20)/max(1e-9, float(np.sqrt(np.mean(ending**2))))
    for size, direction, tail in ((min(round(1.5*rate),len(ending)), (0,1), False), (min(7*rate,len(ending)), (1,0), True)):
        ending[-size:] *= np.linspace(*direction,size)[:,None] if tail else 1
        if not tail: ending[:size] *= np.linspace(*direction,size)[:,None]
    def mix(level, gap):
        audio = speech.copy()
        gain = 10**(level/20)/max(1e-9,float(np.sqrt(np.mean(bed**2))))
        starts = list(range(8*rate,len(audio),len(bed)+round(gap*rate)))
        for start in starts:
            count = min(len(bed),len(audio)-start)
            audio[start:start+count] += bed[:count]*gain
        return np.concatenate([audio,ending*ending_gain]), starts, bed*gain
    old_mix, _, _ = mix(float(music.get('bed_rms_dbfs',-38)),20)
    if old_mix.shape != original.shape:
        raise ValueError('Canonical audio timing differs from source reconstruction')
    residual = float(np.max(np.abs(old_mix-original)))
    if residual > 1/32768+1e-7:
        raise ValueError('Source reconstruction differs beyond PCM16 quantization: '+str(residual))
    audio, starts, mixed_bed = mix(bed_rms_dbfs,bed_gap_seconds)
    if audio.shape != original.shape or not np.isfinite(audio).all() or np.max(np.abs(audio)) > .999:
        raise ValueError('Derivative changed timing or has clipping/nonfinite samples')
    output.mkdir(parents=True,exist_ok=True)
    wav, temporary = output/'mix.wav', output/'mix.mp3'
    sf.write(wav,audio,rate,subtype='PCM_16')
    ffmpeg=manifest['ffmpeg']
    subprocess.run([ffmpeg,'-v','error','-y','-i',str(wav),'-c:a','libmp3lame','-b:a','192k',str(temporary)],check=True,timeout=180,**renderer.HIDDEN)
    decoded=output/'decoded.wav'
    subprocess.run([ffmpeg,'-v','error','-y','-i',str(temporary),'-c:a','pcm_f32le',str(decoded)],check=True,timeout=180,**renderer.HIDDEN)
    decoded_audio, decoded_sr=sf.read(decoded,dtype='float32',always_2d=True)
    if decoded_sr!=rate or decoded_audio.shape!=audio.shape or not np.isfinite(decoded_audio).all() or np.max(np.abs(decoded_audio))>=1:
        raise ValueError('Decoded derivative changed duration or clips')
    checksum=file_digest(temporary)
    version=renderer.sha({'source_version':latest['version'],'audio_sha256':checksum,'bed_rms_dbfs':bed_rms_dbfs,'bed_gap_seconds':bed_gap_seconds})
    mix_proof={'policy':'public-demo-existing-audio-music-remix-v1','source_version':latest['version'],
               'source_completion_sha256':file_digest(book/'complete.json'),'source_mp3_sha256':latest['mp3_sha256'],
               'source_wav_sha256':latest['wav_sha256'],'source_recognition_report_sha256':latest['recognition_report_sha256'],
               'speech_selections_sha256':renderer.sha(selections),'captions_sha256':renderer.sha(latest['captions']),
               'bed_rms_dbfs':bed_rms_dbfs,'bed_gap_seconds':bed_gap_seconds,'bed_phrase_seconds':len(bed)/rate,
               'bed_phrase_count':len(starts),'measured_bed_rms_dbfs':20*math.log10(float(np.sqrt(np.mean(mixed_bed**2)))),
               'source_reassembly_max_pcm_residual':residual,'allowed_pcm_quantization':1/32768,
               'duration':len(audio)/rate,'sample_peak':float(np.max(np.abs(audio))),'decoded_peak':float(np.max(np.abs(decoded_audio))),
               'decoded_clipped_samples':int(np.count_nonzero(np.abs(decoded_audio)>=1)),'speech_unchanged':True,'captions_unchanged':True,
               'opening_and_ending_unchanged':True,'new_synthesis':False,'new_recognition':False,'human_approval':False,
               'mp3_sha256':checksum,'wav_sha256':file_digest(wav)}
    public=output/'en-gift-of-magi/public';public.mkdir(parents=True,exist_ok=True)
    derivative={**latest,'version':version,'mp3':version+'.mp3','mp3_sha256':checksum,'wav':version+'.wav',
                'wav_sha256':file_digest(wav),'music_remix':mix_proof}
    for name,raw in payload.items():
        if name in (latest['mp3'],'latest.json',latest['version']+'.json','index.html'):continue
        (public/name).write_bytes(raw)
    shutil.copyfile(temporary,public/derivative['mp3']);shutil.copyfile(wav,public/derivative['wav'])
    markup=payload['index.html'].decode('utf-8').replace(latest['mp3'],derivative['mp3']).replace('"version": "'+latest['version']+'"','"version": "'+version+'"')
    (public/'index.html').write_text(markup,encoding='utf-8')
    renderer.write(public/'latest.json',derivative);renderer.write(public/(version+'.json'),derivative)
    renderer.write(output/'mix-receipt.json',{'mix':mix_proof,'source_package':evidence,'selections':selections})
    site=output/'website'
    if site.exists():raise ValueError('Use a fresh website staging output')
    shutil.copytree(site_source,site)
    catalog=pages.read(site/'demos/catalog.json');row=next(r for r in catalog if r['id']=='en')
    old_path=pages.local(site,row['audio']);old_path.relative_to(site)
    old_path.unlink()
    row.update(audio='demos/en/'+checksum+'.mp3',audio_sha256=checksum)
    (site/row['audio']).write_bytes(temporary.read_bytes())
    (site/'demos/catalog.json').write_bytes(pages.encoded(catalog))
    pages.check(site)
    renderer.write(output/'verification.json',mix_proof)
    print(json.dumps(mix_proof,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--book',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--site-source',type=Path,default=ROOT/'website')
    parser.add_argument('--bed-rms-dbfs',type=float,default=-30);parser.add_argument('--bed-gap-seconds',type=float,default=4)
    args=parser.parse_args();build(args.book,args.output,args.site_source,args.bed_rms_dbfs,args.bed_gap_seconds)
