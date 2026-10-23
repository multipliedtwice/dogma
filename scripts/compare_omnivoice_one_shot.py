"""One-shot comparison: OmniVoice vs Higgs first takes on the sentences that caused retakes.

Sentences come from a finished Higgs run: every unit flagged on its first take,
plus sentence-initial с/в/к and interjection lines. OmniVoice renders each once
in audio.cpp with the same reference clip and transcript (plain text, no
stress marks). Both engines' first takes are judged the same way: primary
Qwen3-ASR transcript under the current sound-alike, proper-noun and
interjection rules, plus the с/в/к phone check. Publishes a listening page.
Changes no chapter audio and approves nothing. Run with the higgs-production venv.
"""
import argparse, html, json, re, shutil, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'runtime'), str(ROOT/'scripts')]
from rimsky.util import write_json, file_digest, atomic_write
from render_higgs_book_trial import RUNNER

MODEL = ROOT/'training-work/models/audio-cpp-gguf/OmniVoice-GGUF/omnivoice-q8_0.gguf'
OUT = ROOT/'training-work/omnivoice-one-shot-v1'
PAGE = ROOT/'training-work/character-rb2-full/fine-tune-all/elirm-iv-mvp/omnivoice'
MEDIA = ROOT/'training-work/character-rb2/full/clips/elirm-iv-mvp/omnivoice'
INTERJECTION = re.compile(r'^\W*(?:[А-Яа-яЁё]{1,3}(?:-[А-Яа-яЁё]{1,3})+|угу|ага|хм|мда|эм|кхм)\b', re.I)


def judge(expected, heard):
    """Primary transcript under the current rules (text-only, same for both engines)."""
    from rimsky.speech_quality import transcript_check, spoken_word_tokens as tok
    from rimsky.spoken_homophones import homophone_equivalence, elongation_equivalence
    from rimsky.name_tolerance import tolerance
    from render_higgs_book_trial import spoken_text
    try:heard = spoken_text(heard)
    except ValueError:pass
    if transcript_check(expected, heard, '', '', normalize_elongations=True)['status'] == 'asr_exact_match':return True
    words, _ = elongation_equivalence(expected, tok(expected), tok(heard))
    words, _ = homophone_equivalence(tok(expected), words)
    words, _ = tolerance(tok(expected), words, expected)
    return transcript_check(expected, ' '.join(words), '', '', normalize_elongations=True)['status'] == 'asr_exact_match'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--generation', type=Path, required=True); p.add_argument('--chapter', default='c0003')
    a = p.parse_args(); OUT.mkdir(parents=True, exist_ok=True)
    picks = {}
    for take0 in sorted((a.generation/a.chapter).glob('chunk-*/**/take-0')):
        if any(x.startswith(('word-', 'batched', 'delegated')) for x in take0.relative_to(a.generation).parts[:-1]):continue
        plan = {u['id']: u for u in json.loads((take0/'render-plan.json').read_text('utf8'))['units']}
        checks = json.loads((take0/'word-checks-1.7B-normalized.json').read_text('utf8'))['checks']
        receipts = json.loads((take0/'receipts.json').read_text('utf8'))['takes']
        for uid, unit in plan.items():
            if unit['synthesis'].get('backend') == 'nonverbal_asset' or uid not in checks or uid not in receipts:continue
            text = unit['spoken']; first = text.split()[0].casefold().strip('—–-«"') if text.split() else ''
            reason = ('flagged first take' if checks[uid]['status'] != 'asr_exact_match' else
                      'sentence-initial с/в/к' if first in ('с', 'в', 'к') else 'interjection' if INTERJECTION.match(text) else None)
            if reason:picks[uid] = {'unit': unit, 'higgs_wav': str(take0/receipts[uid]['audio']), 'higgs_heard': checks[uid].get('heard', ''), 'reason': reason}
    syn = lambda u: u['synthesis']
    requests = [{'id': uid, 'text': syn(r['unit'])['text'].replace('́', ''), 'voice_ref': str(ROOT/syn(r['unit'])['reference']['audio']),
                 'reference_text': syn(r['unit'])['reference']['text'], 'seed': syn(r['unit'])['sampling']['seed']} for uid, r in picks.items()]
    write_json(OUT/'sequence.json', {'requests': requests})
    start = time.time()
    with (OUT/'native.log').open('w', encoding='utf8') as log:
        subprocess.run([str(RUNNER), '--task', 'tts', '--family', 'omnivoice', '--model', str(MODEL), '--backend', 'cuda', '--device', '0',
                        '--threads', '4', '--request-sequence', str(OUT/'sequence.json'), '--out-dir', str(OUT), '--out-format', 'pcm16', '--metrics'],
                       cwd=RUNNER.parent, stdout=log, stderr=subprocess.STDOUT, check=False)
    wall = time.time() - start
    import soundfile as sf
    rendered = {uid for uid in picks if (OUT/(uid+'.wav')).exists()}
    audio = sum(sf.info(str(OUT/(u+'.wav'))).duration for u in rendered)
    # Primary recognizer on OmniVoice takes (Higgs takes already have theirs).
    asr = subprocess.run([str(ROOT/'training-work/.venv-asr/Scripts/python.exe'), '-X', 'utf8', '-c', (
        'import json,sys,torch;from qwen_asr import Qwen3ASRModel;ids=json.loads(sys.argv[1]);'
        "m=Qwen3ASRModel.from_pretrained(sys.argv[2],device_map='cuda:0',dtype=torch.bfloat16,attn_implementation='sdpa',local_files_only=True,max_inference_batch_size=4,max_new_tokens=512);"
        "r=m.transcribe([sys.argv[3]+'/'+i+'.wav' for i in ids],language='Russian');print(json.dumps(dict(zip(ids,[x.text for x in r])),ensure_ascii=False))"),
        json.dumps(sorted(rendered)), str(ROOT/'training-work/models/Qwen3-ASR-1.7B'), str(OUT)], capture_output=True, text=True, encoding='utf8', check=True)
    omni_heard = json.loads(asr.stdout.strip().splitlines()[-1])
    from preposition_check import check, merged
    rows = []
    for uid, r in picks.items():
        expected = r['unit']['spoken']
        def prep(wav):
            miss = check(expected, wav) or []
            return not [m for m in miss if not merged(m[1], m[2].split()[1] if len(m[2].split()) > 1 else '')]
        row = {'id': uid, 'reason': r['reason'], 'text': expected, 'higgs_heard': r['higgs_heard'], 'higgs_ok': judge(expected, r['higgs_heard']),
               'higgs_prepositions_ok': prep(r['higgs_wav'])}
        if uid in rendered:
            row.update(omni_heard=omni_heard[uid], omni_ok=judge(expected, omni_heard[uid]), omni_prepositions_ok=prep(OUT/(uid+'.wav')))
        rows.append(row)
    summary = {'sentences': len(picks), 'omnivoice_rendered': len(rendered), 'omnivoice_wall_seconds': round(wall, 1),
               'omnivoice_audio_seconds': round(audio, 1), 'omnivoice_x_realtime': round(wall/audio, 2) if audio else None,
               'higgs_first_take_ok': sum(r['higgs_ok'] for r in rows), 'omnivoice_first_take_ok': sum(r.get('omni_ok', False) for r in rows),
               'higgs_prepositions_ok': sum(r['higgs_prepositions_ok'] for r in rows), 'omnivoice_prepositions_ok': sum(r.get('omni_prepositions_ok', False) for r in rows)}
    write_json(OUT/'results.json', {'summary': summary, 'rows': rows, 'listener_verdict': None})
    PAGE.mkdir(parents=True, exist_ok=True); MEDIA.mkdir(parents=True, exist_ok=True)
    def player(path):
        name = file_digest(path)[:16]+'.wav'; shutil.copyfile(path, MEDIA/name)
        return f'<audio controls preload="none" src="/character-rb2/full/clips/elirm-iv-mvp/omnivoice/{name}"></audio>'
    items = ''.join(f'<section><p><b>{html.escape(r["text"])}</b> <small>({r["reason"]})</small></p>'
                    f'<p>Higgs {"✓" if r["higgs_ok"] else "✗ «"+html.escape(r["higgs_heard"])+"»"}</p>{player(picks[r["id"]]["higgs_wav"])}'
                    + (f'<p>OmniVoice {"✓" if r.get("omni_ok") else "✗ «"+html.escape(r.get("omni_heard",""))+"»"}</p>{player(OUT/(r["id"]+".wav"))}' if r['id'] in rendered else '')
                    + '</section>' for r in rows)
    atomic_write(PAGE/'index.html', ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>OmniVoice vs Higgs</title><style>:root{color-scheme:light dark}body{font:17px system-ui;max-width:820px;margin:auto;padding:16px;line-height:1.4}'
        'audio{width:100%}section{border-top:1px solid #8886;padding:6px 0}small{opacity:.7}</style>'
        f'<h1>OmniVoice против Higgs: с первого раза</h1><p>{summary["sentences"]} трудных фраз главы 3. Слова верны с первого раза: '
        f'Higgs {summary["higgs_first_take_ok"]}, OmniVoice {summary["omnivoice_first_take_ok"]}. Скорость OmniVoice: {summary["omnivoice_x_realtime"]}× от длительности. '
        'OmniVoice не дообучен на голосе Влада.</p>' + items + '</html>').encode('utf8'))
    print('SUMMARY', json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':main()
