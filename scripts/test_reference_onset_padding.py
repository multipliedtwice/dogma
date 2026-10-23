"""A/B: does leading/trailing silence on a reference clip prevent clipped starts?

Measured 2026-10-16: all 151 clipped first takes in two Chapter 2-3 runs were
cloned from one reviewed narrator reference whose speech starts after 13 ms;
the 15 other references (37-546 ms of quiet) produced none. This renders the
same narrator sentences with the same synthesis plan and seed, changing only
the reference audio to a derived copy with PAD seconds of digital silence at
both ends (same reviewed voice and words; provenance recorded). Compared with
the existing first takes: clipped starts and word/first-word failures.
Changes no chapter audio. Run with the higgs-production venv (GPU free).
"""
import argparse, copy, json, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky.util import write_json, file_digest, digest
from render_higgs_book_trial import MODEL, RUNNER, sequence_request
from compare_omnivoice_one_shot import judge

OUT = ROOT/'training-work/reference-onset-padding-v1'
PAD = 0.15


def padded_reference(source, sha):
    import numpy as np, soundfile as sf
    x, rate = sf.read(source, dtype='float32')
    if file_digest(source) != sha:raise ValueError('Reviewed reference changed')
    pad = np.zeros((round(PAD*rate),) + x.shape[1:], dtype='float32')
    target = OUT/'references'/f'{sha[:16]}-pad{int(PAD*1000)}ms.wav'; target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, np.concatenate([pad, x, pad]), rate, subtype='PCM_16')
    write_json(target.with_suffix('.json'), {'derived_from_sha256': sha, 'derived_from': str(source), 'operation': f'{PAD} s digital silence before and after',
                                              'audio_sha256': file_digest(target), 'voice_and_words_unchanged': True})
    return target


def main():
    import soundfile as sf
    from rimsky.audio_join import boundary_check
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--generation', type=Path, required=True); p.add_argument('--chapter', default='c0003')
    p.add_argument('--reference-sha', default='7fe2248e46'); p.add_argument('--count', type=int, default=60)
    a = p.parse_args(); OUT.mkdir(parents=True, exist_ok=True)
    picks = []
    for take0 in sorted((a.generation/a.chapter).glob('chunk-*/**/take-0')):
        if any(x.startswith(('word-', 'batched', 'delegated')) for x in take0.relative_to(a.generation).parts[:-1]):continue
        rec = json.loads((take0/'receipts.json').read_text('utf8'))['takes']
        checks = json.loads((take0/'word-checks-1.7B-normalized.json').read_text('utf8'))['checks']
        for u in json.loads((take0/'render-plan.json').read_text('utf8'))['units']:
            s = u['synthesis']
            if u['id'] in rec and u['id'] in checks and s.get('reference', {}).get('sha256', '').startswith(a.reference_sha):
                x, rate = sf.read(take0/rec[u['id']]['audio'], dtype='float32'); edge = boundary_check(x, rate)
                picks.append({'unit': u, 'wav': str(take0/rec[u['id']]['audio']), 'clipped': bool(edge['unsafe_start']),
                              'heard': checks[u['id']].get('heard', '')})
    clipped = [r for r in picks if r['clipped']]; clean = [r for r in picks if not r['clipped']]
    picks = clipped[:a.count//2] + clean[:a.count - min(len(clipped), a.count//2)]
    ref = picks[0]['unit']['synthesis']['reference']
    padded = padded_reference(ROOT/ref['audio'], ref['sha256'])
    requests = []
    for r in picks:
        s = copy.deepcopy(r['unit']['synthesis']); s['reference'] = {**s['reference'], 'audio': str(padded.relative_to(ROOT)), 'sha256': file_digest(padded)}
        requests.append(sequence_request(r['unit']['id'], s))
    write_json(OUT/'sequence.json', {'requests': requests})
    t = time.time()
    with (OUT/'native.log').open('w', encoding='utf8') as log:
        subprocess.run([str(RUNNER), '--task', 'tts', '--family', 'higgs_audio_tts', '--model', str(MODEL), '--backend', 'cuda', '--device', '0',
                        '--threads', '4', '--request-sequence', str(OUT/'sequence.json'), '--out-dir', str(OUT), '--out-format', 'pcm16', '--metrics'],
                       cwd=RUNNER.parent, stdout=log, stderr=subprocess.STDOUT, check=False)
    wall = time.time() - t
    ids = [r['unit']['id'] for r in picks if (OUT/(r['unit']['id']+'.wav')).exists()]
    asr = subprocess.run([str(ROOT/'training-work/.venv-asr/Scripts/python.exe'), '-X', 'utf8', '-c', (
        'import json,sys,torch;from qwen_asr import Qwen3ASRModel;ids=json.loads(sys.argv[1]);'
        "m=Qwen3ASRModel.from_pretrained(sys.argv[2],device_map='cuda:0',dtype=torch.bfloat16,attn_implementation='sdpa',local_files_only=True,max_inference_batch_size=4,max_new_tokens=512);"
        "r=m.transcribe([sys.argv[3]+'/'+i+'.wav' for i in ids],language='Russian');print(json.dumps(dict(zip(ids,[x.text for x in r])),ensure_ascii=False))"),
        json.dumps(ids), str(ROOT/'training-work/models/Qwen3-ASR-1.7B'), str(OUT)], capture_output=True, text=True, encoding='utf8', check=True)
    heard = json.loads(asr.stdout.strip().splitlines()[-1])
    def first_word_ok(expected, transcript):
        from rimsky.speech_quality import spoken_word_tokens as tok
        e, h = tok(expected), tok(transcript)
        return bool(e) and bool(h) and e[0] == h[0]
    rows = []
    for r in picks:
        uid = r['unit']['id']; exp = r['unit']['spoken']
        if uid not in heard:continue
        x, rate = sf.read(OUT/(uid+'.wav'), dtype='float32'); edge = boundary_check(x, rate)
        rows.append({'id': uid, 'text': exp, 'original_clipped': r['clipped'], 'padded_clipped': bool(edge['unsafe_start']),
                     'original_words_ok': judge(exp, r['heard']), 'padded_words_ok': judge(exp, heard[uid]),
                     'original_first_word_ok': first_word_ok(exp, r['heard']), 'padded_first_word_ok': first_word_ok(exp, heard[uid]),
                     'padded_heard': heard[uid]})
    n = len(rows)
    summary = {'sentences': n, 'pad_seconds': PAD, 'render_wall_seconds': round(wall, 1),
               'clipped_starts': {'original': sum(r['original_clipped'] for r in rows), 'padded': sum(r['padded_clipped'] for r in rows)},
               'words_ok': {'original': sum(r['original_words_ok'] for r in rows), 'padded': sum(r['padded_words_ok'] for r in rows)},
               'first_word_ok': {'original': sum(r['original_first_word_ok'] for r in rows), 'padded': sum(r['padded_first_word_ok'] for r in rows)}}
    write_json(OUT/'results.json', {'summary': summary, 'rows': rows, 'padded_reference': str(padded)})
    print('SUMMARY', json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':main()
