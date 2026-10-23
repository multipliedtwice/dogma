"""Prepare source-linked Book 1 performance comparisons, without accepting labels."""
from __future__ import annotations

import hashlib
import html
import json
import math
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'training-work/character-rb1'
OUT = SOURCE / 'voice-evidence-atlas'
CLIPS = OUT / 'clips'
MANUAL = OUT / 'manual-source'
ROW_FILES = ('audio-candidates.jsonl', 'sentence-audio-candidates.jsonl',
             'recovered-audio-candidates.jsonl', 'missing-character-speech-candidates.jsonl')


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def copy_verified(path_text: str, expected: str, key: str) -> tuple[str, Path]:
    source = Path(path_text).resolve()
    if not source.is_relative_to(ROOT.resolve()) or not source.is_file():
        raise ValueError(f'Missing or external evidence audio: {key}')
    actual = digest(source)
    if expected and actual != expected:
        raise ValueError(f'Changed evidence audio: {key}')
    target = CLIPS / f'{key}.wav'
    if not target.is_file() or digest(target) != actual:
        shutil.copy2(source, target)
    return f'clips/{target.name}', source


def metrics(path: Path) -> dict:
    audio, rate = sf.read(path, dtype='float32', always_2d=True)
    mono = audio.mean(axis=1)
    duration = len(mono) / rate
    if rate != 16000:
        mono = librosa.resample(mono, orig_sr=rate, target_sr=16000)
    if len(mono) > 16000 * 12:
        mono = mono[:16000 * 12]
    rms = librosa.feature.rms(y=mono, frame_length=1024, hop_length=256)[0]
    f0 = librosa.yin(mono, fmin=65, fmax=380, sr=16000,
                     frame_length=1024, hop_length=256)
    n = min(len(rms), len(f0))
    voiced = f0[:n][(rms[:n] > max(float(np.max(rms)) * .15, .002)) &
                    (f0[:n] > 70) & (f0[:n] < 375)]
    return {'duration_seconds': round(duration, 2),
            'rms_dbfs': round(20 * math.log10(float(np.sqrt(np.mean(mono**2))) + 1e-8), 1),
            'median_f0_hz_screening_only': round(float(np.median(voiced)), 1) if len(voiced) > 10 else None}


def load_rows() -> tuple[dict, dict]:
    by_id = {}
    narrator = defaultdict(list)
    for name in ROW_FILES:
        for line in (SOURCE / name).read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            by_id[row['id']] = row
            if (row.get('role') == 'narration' and not row.get('flags') and
                    3 <= row.get('audio_end', 0) - row.get('audio_start', 0) <= 12 and
                    Path(row.get('audio', '')).is_file()):
                narrator[row['recording_id']].append(row)
    return by_id, narrator


def baseline(row: dict, narrator: dict) -> dict | None:
    choices = narrator.get(row['recording_id'], [])
    if not choices:
        return None
    center = (row['audio_start'] + row['audio_end']) / 2
    return min(choices, key=lambda item: abs((item['audio_start'] + item['audio_end']) / 2 - center))


def main() -> None:
    CLIPS.mkdir(parents=True, exist_ok=True)
    MANUAL.mkdir(parents=True, exist_ok=True)
    rows, narrator = load_rows()
    bank = json.loads((SOURCE / 'provisional-voice-reference-bank.json').read_text(encoding='utf-8'))
    starter = json.loads((SOURCE / 'benchmark/curated_cast_v2.json').read_text(encoding='utf-8'))
    descriptions = {item['id']: item['description'] for item in starter['characters']}
    metric_cache = {}
    recording_hash_cache = {}
    cases = []
    role_coverage = []

    def feature(path: Path):
        if path not in metric_cache:
            metric_cache[path] = metrics(path)
        return metric_cache[path]

    def recording_hash(recording_id: str) -> str | None:
        if recording_id not in recording_hash_cache:
            number = recording_id.rsplit('-', 1)[-1]
            paths = list((ROOT / 'content/reality-benders-1').glob(number + '_*.mp3'))
            recording_hash_cache[recording_id] = digest(paths[0]) if len(paths) == 1 else None
        return recording_hash_cache[recording_id]

    def manual_excerpt(case_id: str, recording_id: str, start: float, end: float) -> Path:
        number = recording_id.rsplit('-', 1)[-1]
        source = next((ROOT / 'content/reality-benders-1').glob(number + '_*.mp3'))
        target = MANUAL / (case_id + '.wav')
        if not target.is_file():
            subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                            '-ss', str(start), '-t', str(end-start), '-i', str(source),
                            '-ar', '24000', '-ac', '1', '-y', str(target)], check=True)
        return target

    def add_case(*, case_id: str, family: str, proposed_claim: str, role_id: str,
                 row: dict, audio_path: str, audio_hash: str, compare_row: dict | None,
                 context: str = '', comparison_kind: str = 'nearby_narration'):
        source_rel, source = copy_verified(audio_path, audio_hash, case_id)
        comparison = None
        if compare_row:
            compare_rel, compare_file = copy_verified(compare_row['audio'],
                                                     compare_row.get('audio_sha256', ''),
                                                     'baseline_' + compare_row['id'])
            comparison = {'id': compare_row['id'], 'audio': compare_rel,
                          'metrics': feature(compare_file),
                          'text': compare_row.get('text', compare_row.get('source_text', '')),
                          'recording_id': compare_row['recording_id'],
                          'audio_start': compare_row['audio_start'],
                          'audio_end': compare_row['audio_end']}
        item = {'id': case_id, 'family': family, 'proposed_claim': proposed_claim,
                'role_id': role_id, 'context': context,
                'source_id': row.get('source_id'), 'recording_id': row.get('recording_id'),
                'recording_sha256': recording_hash(row['recording_id']),
                'audio_start': row.get('audio_start'), 'audio_end': row.get('audio_end'),
                'text': row.get('text', row.get('source_text', '')),
                'source_audio': source_rel, 'source_audio_sha256': digest(source),
                'source_metrics': feature(source), 'comparison_kind': comparison_kind,
                'comparison': comparison, 'evidence_status': 'audio_located',
                'feature_review_status': 'unreviewed', 'training_accepted': False}
        cases.append(item)

    for entry in bank['references']:
        role = entry['character_id']
        candidates = [ref for ref in entry['candidate_references'] if ref['id'] in rows]
        source_ids = {rows[ref['id']].get('source_id') for ref in candidates}
        chapters = {rows[ref['id']].get('chapter_id') for ref in candidates}
        clean = sum(not ref.get('flags') for ref in candidates)
        role_coverage.append({'role_id': role, 'reference_candidates': len(candidates),
                              'distinct_source_passages': len(source_ids),
                              'distinct_chapters': len(chapters),
                              'unflagged_candidates': clean,
                              'stable_profile_supported_by_audio_review': False})
        for ref in candidates:
            row = rows[ref['id']]
            if row.get('audio_start') is None or row.get('audio_end') is None:
                continue
            add_case(case_id=ref['id'], family='character_role',
                     proposed_claim='Does Vlad give this role a repeatable sound distinct from narration?',
                     role_id=role, row=row, audio_path=ref['audio'],
                     audio_hash=ref['audio_sha256'], compare_row=baseline(row, narrator),
                     context=descriptions.get(role, '') +
                     (f" Clip flags: {', '.join(ref.get('flags', []))}." if ref.get('flags') else ''))

    presentation = json.loads((SOURCE / 'presentation-audio-review.json').read_text(encoding='utf-8'))
    selected_channels = set()
    for item in presentation['items']:
        channel = item['delivery']
        if channel in selected_channels or channel == 'written_text':
            continue
        selected_channels.add(channel)
        row = {'source_id': item['source_id'], 'recording_id': item['recording_id'],
               'audio_start': item['audio_start'], 'audio_end': item['audio_end'],
               'text': item['text']}
        add_case(case_id='channel_' + channel, family='presentation_channel',
                 proposed_claim=f'Is {channel} audibly distinct in Vlad or the recording?',
                 role_id=item['speaker'], row=row, audio_path=item['preview_audio'],
                 audio_hash=item['preview_sha256'], compare_row=baseline(row, narrator),
                 context='Text-side channel identification; source audio remains unreviewed.')

    game = json.loads((SOURCE / 'provisional-game-ui-reference-bank.json').read_text(encoding='utf-8'))
    if game['candidate_references']:
        ref = game['candidate_references'][0]
        row = rows.get(ref['id'])
        if row:
            add_case(case_id='game_ui_' + ref['id'], family='presentation_channel',
                     proposed_claim='Is this stat/game UI passage performed mechanically?',
                     role_id='mechanical_game_voice', row=row, audio_path=ref['audio'],
                     audio_hash=ref['audio_sha256'], compare_row=baseline(row, narrator))

    enumeration = next((row for row in rows.values()
                        if row.get('source_id') == 'c0004-p00002' and
                        row.get('id', '').startswith('sentence_') and not row.get('flags')), None)
    if enumeration:
        add_case(case_id='enumeration_opening', family='rhetorical_delivery',
                 proposed_claim='Is this list item brisk relative to ordinary narration?',
                 role_id='narrator', row=enumeration, audio_path=enumeration['audio'],
                 audio_hash=enumeration['audio_sha256'], compare_row=baseline(enumeration, narrator),
                 context='Opening first-contact enumeration; compare speed and grouping.')

    for case_id, family, claim, role, source_id, recording_id, start, end, spoken in (
        ('heading_first_chapter', 'book_structure', 'Does Vlad give this chapter heading space before and after?',
         'narrator', 'c0005-p00001', 'rb1-01', 14.45, 17.5, 'Глава первая. Сетевой турнир'),
        ('excited_protagonist', 'momentary_delivery', 'Is Kirill audibly triumphant and addressing his friends?',
         'rb1_kirill', 'c0005-p00024', 'rb1-01', 682.02, 683.65, '— Как я его! Вы видели?!'),
        ('stunned_protagonist', 'momentary_delivery', 'Does the short ending suspend or soften the voice?',
         'narrator', 'c0005-p00025', 'rb1-01', 689.18, 690.28, 'И замер…'),
    ):
        source = manual_excerpt(case_id, recording_id, start, end)
        row = {'source_id': source_id, 'recording_id': recording_id,
               'audio_start': start, 'audio_end': end, 'text': spoken}
        add_case(case_id=case_id, family=family, proposed_claim=claim,
                 role_id=role, row=row, audio_path=str(source), audio_hash=digest(source),
                 compare_row=baseline(row, narrator),
                 context='Source recording excerpt selected from aligned word timings; audio review pending.')

    priority_roles = {'rb1_kirill', 'rb1_tyulenev', 'rb1_vasiliadi',
                      'rb1_filira', 'rb1_irishka', 'rb1_minno',
                      'rb1_uline', 'rb1_kisly', 'rb1_tamara', 'rb1_kosta'}
    chosen_roles = set()
    for case in cases:
        priority = case['family'] in {'rhetorical_delivery', 'book_structure', 'momentary_delivery'}
        priority |= case['id'] in {'channel_radio', 'channel_public_address'}
        priority |= case['role_id'] == 'mechanical_game_voice'
        if case['family'] == 'character_role' and case['role_id'] in priority_roles:
            if case['role_id'] not in chosen_roles:
                priority = True
                chosen_roles.add(case['role_id'])
        case['priority_first_pass'] = bool(priority)

    acoustic_screening = []
    for role in role_coverage:
        comparisons = []
        for case in cases:
            if case['family'] != 'character_role' or case['role_id'] != role['role_id'] or not case['comparison']:
                continue
            source_f0 = case['source_metrics']['median_f0_hz_screening_only']
            baseline_f0 = case['comparison']['metrics']['median_f0_hz_screening_only']
            if source_f0 and baseline_f0:
                comparisons.append({'case_id': case['id'],
                                    'relative_f0_semitones': round(12 * math.log2(source_f0 / baseline_f0), 1),
                                    'recording_id': case['recording_id']})
        shifts = [entry['relative_f0_semitones'] for entry in comparisons]
        acoustic_screening.append({'role_id': role['role_id'], 'comparisons': comparisons,
                                   'median_relative_f0_semitones': round(float(np.median(shifts)), 1) if shifts else None,
                                   'interpretation': 'Screening measurement only; no voice or emotion verdict.'})

    source_files = {'provisional-voice-reference-bank.json': digest(SOURCE / 'provisional-voice-reference-bank.json'),
                    'presentation-audio-review.json': digest(SOURCE / 'presentation-audio-review.json')}
    payload = {'schema': 1, 'book': 'reality-benders-1', 'purpose': 'audio evidence candidates, not approved labels',
               'source_files_sha256': source_files,
               'recording_sha256': recording_hash_cache,
               'roles': role_coverage, 'acoustic_screening': acoustic_screening, 'cases': cases,
               'summary': {'roles': len(role_coverage), 'cases': len(cases),
                           'priority_first_pass_cases': sum(x['priority_first_pass'] for x in cases),
                           'roles_with_two_chapters': sum(x['distinct_chapters'] >= 2 for x in role_coverage),
                           'roles_with_one_candidate': sum(x['reference_candidates'] == 1 for x in role_coverage),
                           'audio_confirmed_cases': 0}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'manifest.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    manifest_sha256 = digest(OUT / 'manifest.json')
    cards = []
    for case in cases:
        comparison = case['comparison']
        compare_html = (f'<div class="audio-item"><strong>Nearby narrator baseline</strong><audio controls preload="metadata" src="{comparison["audio"]}"></audio>'
                        '<button type="button" class="play-audio">Play baseline</button><span class="play-status"></span>'
                        f'<small>{html.escape(comparison["text"])} · {comparison["recording_id"]} '
                        f'{comparison["audio_start"]:.2f}–{comparison["audio_end"]:.2f}s</small></div>') if comparison else '<p>No same-recording narration baseline found.</p>'
        f0 = case['source_metrics']['median_f0_hz_screening_only']
        base_f0 = comparison['metrics']['median_f0_hz_screening_only'] if comparison else None
        metric = f'Median pitch screen: {f0} Hz vs baseline {base_f0} Hz. ' if f0 and base_f0 else ''
        cards.append(f'<article data-id="{html.escape(case["id"])}" data-family="{case["family"]}" '
                     f'data-priority="{str(case["priority_first_pass"]).lower()}">'
                     f'<h2>{html.escape(case["role_id"])} <small>{html.escape(case["family"])}</small></h2>'
                     f'<p>{html.escape(case["proposed_claim"])} {html.escape(case["context"])}</p>'
                     f'<div class="pair"><div class="audio-item"><strong>Original source</strong><audio controls preload="metadata" src="{case["source_audio"]}"></audio>'
                     '<button type="button" class="play-audio">Play source</button><span class="play-status"></span>'
                     f'<small>{html.escape(case["text"])} · {case["recording_id"]} '
                     f'{case["audio_start"]:.2f}–{case["audio_end"]:.2f}s · {case["source_id"]}</small></div>'
                     f'{compare_html}</div><p class="metric">{metric}Pitch and level are screening measurements, not proof of a character voice.</p>'
                     '<div class="decision"><select aria-label="Audible verdict"><option value="">Unreviewed</option>'
                     '<option value="distinct">Distinct performance heard</option><option value="no_distinct_change_heard">No distinct change heard</option>'
                     '<option value="uncertain">Uncertain / bad clip</option></select>'
                     '<input aria-label="Observed audible features and reviewer notes" placeholder="What exactly sounds different?" /></div></article>')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>Book 1 voice evidence atlas</title>
<style>body{font:16px/1.45 system-ui;background:#f6f5ef;color:#1e2924;max-width:1100px;margin:30px auto;padding:0 20px}
h1{font-size:2rem}article{background:white;margin:16px 0;padding:20px;border-radius:12px;border:1px solid #ddd}
h2{margin:0}h2 small{font-size:.55em;color:#607168}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}
audio{width:100%;display:block;margin:8px 0}small{display:block;color:#54615a}.decision{display:flex;gap:12px}
input{flex:1}select,input,button{padding:10px;font:inherit;border:1px solid #adb9b0;border-radius:8px}
.play-audio{margin:0 8px 8px 0;background:#e8eee8;color:#203c2b;cursor:pointer}.play-status{font-size:.85em;color:#53655a}
.metric{font-size:.85em;color:#61716a}header{background:#f6f5ef;padding:10px 0}
body:not(.show-all) article[data-priority="false"]{display:none}
@media(max-width:700px){.pair{grid-template-columns:1fr}.decision{display:block}input{width:95%;margin-top:8px}}</style>
<header><h1>Book 1 · original voice evidence</h1><p>These are candidates. Compare source speech with nearby narration;
record what is actually audible. An EPUB description or pitch number alone does not verify a voice.</p>
<p id="file-warning" hidden>For audio playback, open this page through the local server URL printed by
<code>scripts/serve_book1_voice_evidence_atlas.py</code>.</p>
<p>Role clips, presentation channels, and one enumeration are here. <a href="../../higgs-tts-3-pilot/ellipsis-pilot/review/index.html">Six ellipsis A/B examples</a> are a separate focused board.</p>
<label>Reviewer <input id="reviewer" placeholder="Your name" /></label>
<button id="scope">Show all cases</button> <button id="submit" type="button">Submit review</button> <button id="export">Export review JSON</button> <span id="progress"></span> <span id="submit-status" role="status"></span></header>''' + ''.join(cards) + '''
<script>const KEY='rb1-voice-evidence-v1';let saved={};try{saved=JSON.parse(localStorage.getItem(KEY)||'{}')}catch{}
if(location.protocol==='file:')document.querySelector('#file-warning').hidden=false;
for(const button of document.querySelectorAll('.play-audio'))button.addEventListener('click',async()=>{
const holder=button.closest('.audio-item'),audio=holder.querySelector('audio'),status=holder.querySelector('.play-status');
for(const other of document.querySelectorAll('audio'))if(other!==audio)other.pause();
try{await audio.play();status.textContent='Playing';}catch(error){status.textContent='Playback failed: '+String(error);}
});
for(const card of document.querySelectorAll('article')){const id=card.dataset.id,sel=card.querySelector('select'),note=card.querySelector('input');
sel.value=saved[id]?.verdict||'';note.value=saved[id]?.note||'';
for(const el of [sel,note])el.addEventListener('input',()=>{saved[id]={verdict:sel.value,note:note.value};localStorage.setItem(KEY,JSON.stringify(saved));count()})}
function count(){document.querySelector('#progress').textContent=`${Object.values(saved).filter(v=>v.verdict).length} of ${document.querySelectorAll('article').length} reviewed`};count();
document.querySelector('#scope').onclick=()=>{document.body.classList.toggle('show-all');document.querySelector('#scope').textContent=document.body.classList.contains('show-all')?'Show first pass':'Show all cases'};
document.querySelector('#reviewer').value=localStorage.getItem(KEY+'-reviewer')||'';
document.querySelector('#reviewer').addEventListener('input',e=>localStorage.setItem(KEY+'-reviewer',e.target.value));
function reviewPayload(){return {schema:1,book:'reality-benders-1',kind:'voice_evidence_review',
reviewer:document.querySelector('#reviewer').value,exported_at:new Date().toISOString(),
manifest_sha256:'__MANIFEST_SHA__',decisions:saved}}
document.querySelector('#submit').onclick=async()=>{const button=document.querySelector('#submit'),status=document.querySelector('#submit-status');
button.disabled=true;status.textContent='Saving review…';
try{const response=await fetch('submit-review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(reviewPayload())});
if(!response.ok)throw new Error('HTTP '+response.status);const result=await response.json();
status.textContent=`Saved ${result.reviewed} answers to ${result.file}`;
}catch(error){status.textContent='Could not save review: '+String(error)+' — use Export review JSON';}
finally{button.disabled=false}};
document.querySelector('#export').onclick=()=>{const payload=reviewPayload();const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});
const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='book1-voice-evidence-review.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};
</script></html>'''
    page = page.replace('__MANIFEST_SHA__', manifest_sha256)
    (OUT / 'index.html').write_text(page, encoding='utf-8')
    print(json.dumps(payload['summary'], indent=2))
    print(OUT / 'index.html')


if __name__ == '__main__':
    main()
