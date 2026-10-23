"""Render a read-only interpretation of the frozen warning snapshot."""
from pathlib import Path
import html
import json
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'training-work/character-rb1'
OUT = BASE / 'warned-audio-review-20260929'
items = json.loads((OUT/'measurements.json').read_text(encoding='utf-8'))
snapshot = json.loads((OUT/'snapshot.json').read_text(encoding='utf-8'))
board = BASE/'fine-tune-all'
candidates = {r['id']:r for r in map(json.loads,(board/'candidates.jsonl').read_text(encoding='utf-8').splitlines())}
precision = json.loads((board/'precision-v2/status.json').read_text(encoding='utf-8'))['clips']
MOODS = {'neutral':'Normal','calm':'Calm','confident':'Confident','reflective':'Thinking','energetic':'Energetic',
         'curious':'Curious','anxious':'Nervous / tense','perplexed':'Confused','surprised':'Surprised',
         'solemn':'Serious','soft':'Soft / quiet','explaining':'Explaining','tired':'Tired','excited':'Excited'}
NOTES = [
('keep','Normal is still reasonable.',
 'Both estimators find an elevated beginning and a large final fall. This is a contour warning; there is no separate pace or loudness warning.',
 'This introduces another person’s next utterance after a colon. A change leading into that quotation can explain the shape without changing narrator mood.'),
('keep','Keep Normal provisionally.',
 'The final fall weakens from -7.8 to -5.7 semitones with pYIN. Nearby neural examples have mixed labels, including Normal.',
 'The sentence describes giving a detailed account. Nothing in these audio measurements specifically establishes Thinking or Confident.'),
('compare','Normal with emphasis; compare Annoyed only if that is audible.',
 'The wider pitch range is confirmed: about 11.9 semitones versus the bucket mean of 6.9. Generic emotion2vec calls it Angry, but that classifier was unreliable in our benchmark.',
 'The repeated “ни … ни тем более …” invites emphasis. Emphasis is not automatically anger, excitement, or a different voice.'),
('compare','Compare Calm / Normal first. Relieved is a context hypothesis.',
 'The raised ending is reproduced by both trackers. The closest neural examples lean quiet or calm, but are not consistent enough to decide.',
 'The character feels reassured. That makes Relieved plausible in the story, but it must not replace your heard label unless Vlad actually performs relief.'),
('keep','Keep Normal provisionally.',
 'The final fall is reproduced but becomes slightly smaller with pYIN; the single-metric warning falls just below its original cutoff.',
 'Describing a sleepy person does not make the narrator Tired. The audio evidence does not establish a new mood.'),
('keep','Keep Normal; the ending may be a dismissive aside.',
 'The range and final fall remain large with pYIN. Three of the five nearby neural examples are Normal; those comparisons are weak evidence.',
 '“— не моё” is a short conclusion after a dash. Its drop in pitch need not imply sadness or tiredness.'),
('compare','Compare Thinking / Normal; do not use Energetic from pace alone.',
 'The ending falls, while the pace proxy is relatively high at 5.15 peaks/s. pYIN weakens the warning below the original single-cue cutoff.',
 'This follows questions about equipment left behind and ends with an ellipsis. The ellipsis can justify a trailing ending independently of mood.'),
('keep','Keep Normal provisionally.',
 'Both trackers confirm a wide range and low ending. Four of the five nearby neural examples are Normal or Energetic, so the embedding does not settle the question.',
 'This is a transition back to recounting events. Strong sentence closure can explain its pitch shape.'),
('keep','Keep Calm; consider Thinking only if listening supports it.',
 'The final drop is strongly reproduced, about -10.7 semitones. Another Calm clip from this same paragraph has a similarly low median pitch.',
 'The repeated tournaments and ellipsis form a trailing retrospective ending. This is not evidence for Confident just because that bucket is numerically closer.'),
('compare','Calm remains plausible. Compare Tired if the voice itself sounds sleepy.',
 'The high beginning is reproduced, but the median is low, about 113 Hz, and pace is 3.51 peaks/s. Neighboring narration in the paragraph is also near 111–115 Hz.',
 '“меня стало клонить в сон” gives a Tired hypothesis. It does not prove that Vlad uses a tired delivery.'),
('keep','Keep Calm; do not switch to Historical prose merely because of “прошлое”.',
 'pYIN confirms the wider range and elevated beginning. There is no independent pace or loudness warning.',
 'This closes another character’s account of the past; it is not itself the historical passage. Compare Historical prose only if the softer narration is actually audible.'),
('compare','Compare Surprised / Curious against Confident.',
 'The unusually large final fall is reproduced. This is the one flagged clip where the generic classifier’s strongest category is Surprised (~0.82), giving weak additional audio evidence.',
 'The capsule opens immediately when touched. Context supports surprise or curiosity, but the model score is not a calibrated probability and cannot establish the label.'),
('compare','Compare Normal / Explaining against Confused.',
 'Both trackers reproduce a rising final fragment. Four of the five closest neural examples are Normal or Energetic, rather than Confused.',
 'He has chosen a plan and explains what his marker means. The rise on “тупик” could be emphasis, not uncertainty. This is a plausible misplaced Confused label, not a confirmed correction.'),
('compare','Compare Normal / Nervous; avoid changing to Excited from the current hint.',
 'The final fall drops from -6.5 to -4.4 semitones with pYIN, weakening the only warning. Four nearby neural examples are Normal and one is Calm.',
 'The text explicitly says he became frightened. If Vlad reads it matter-of-factly, Normal is valid; a fear word is not sufficient evidence for nervous delivery.'),
('keep','Keep Surprised provisionally; compare Curious only if the audio is mainly questioning.',
 'Both estimators find a rising ending, but pYIN reduces it from +9.6 to +6.1 semitones. The warning no longer reaches the original single-cue cutoff.',
 'This is a question followed by “Хочу! Хочу!! Хочу!!!”. A rising question ending is not by itself a label error. Do not inherit the following sentence’s excitement automatically.'),
('compare','Keep Masculine voice 2 pending comparison; consider Explaining, not a pitch-based family change.',
 'The high median is confirmed at about 174 Hz versus a 145 Hz bucket mean. Two edge deviations weaken with pYIN, so the original warning is less stable.',
 'This continues a long explanation by the same speaker. A higher register within that explanation can occur without changing the voice family. No timbre-based family verdict was obtained.'),
('keep','Keep Masculine voice 2 / Normal provisionally.',
 'The raised ending weakens slightly with pYIN. A subsequent sentence in the same paragraph, also labeled Normal, has a median of 139 Hz versus about 134 Hz here.',
 'This contrasts his preferred plan with the group’s decision. Stress on “сюда” may explain the raised ending; it does not require a different family or mood.'),
('keep','Energetic is defensible; Warning / In a hurry are optional listening comparisons.',
 'The elevated beginning is confirmed, with a pace proxy of 4.95 peaks/s and relatively loud RMS of -20.7 dBFS. The closest neural example is also Energetic.',
 'The first words “Только думай скорее” urge quick action. The existing label need not be wrong just because that emphasis differs from the bucket average.'),
('compare','Compare Serious against Normal; keep Masculine voice 4.',
 'This is the only warning combining pitch with a separate pace cue: 2.91 peaks/s versus the bucket’s 4.44. The low ending is reproduced.',
 'The character briefly admits there was a reason for expulsion. A serious aside is plausible. Another Normal sentence in this paragraph has a similar median pitch, supporting caution about changing family.'),
('compare','Recheck Uline’s voice/register first; compare Warning / Explaining only after that.',
 'This is the strongest absolute-pitch outlier: about 178 Hz versus the 140 Hz bucket mean. Both trackers confirm the wide range. Neural similarity to same-family examples is relatively low, but that is not a reliable family classifier.',
 'The text is a cautious heads-up, not necessarily a shouted alarm. Do not move it to another female family solely because it is higher pitched. Compare Uline’s surrounding dialogue before changing voice.'),
('keep','Keep Game voice / Normal provisionally.',
 'The elevated beginning is reproduced, but the median pitch is lower than two neighboring game instructions from the same paragraph (144 versus 154–157 Hz).',
 'This really is a game-rule passage, supported by its surrounding instructions, unlike the earlier regex mistake. Emphasis on death resetting progress need not be an emotional mood.'),
]
assert len(items)==len(NOTES)==21
def esc(value): return html.escape(str(value))
def url(path):
    relative=(ROOT/path).resolve().relative_to((ROOT/'training-work').resolve())
    return '/'+relative.as_posix()
def audio(path): return f'<audio controls preload="none" src="{esc(url(path))}"></audio>'
def label(bucket):
    voice,mood=bucket.split('|',1)
    return voice.replace('_',' ')+' · '+MOODS.get(mood,mood)
def reference(identifier, heading):
    row=candidates.get(identifier); p=precision.get(identifier,{})
    if not row or not p.get('audio'): return ''
    return f'<div class="reference"><b>{esc(heading)}</b><p lang="ru">{esc(row["source_text"])}</p>{audio(p["audio"])}</div>'
weak=[]
for number,(item,note) in enumerate(zip(items,NOTES),1):
    category, advice, evidence, context = note
    deltas=[]
    for difference in item['warning']['differences']:
        if difference['key'] not in item['second_pitch']: continue
        value=item['second_pitch'][difference['key']]
        if value is None: continue
        original=difference['value']; mean=difference['average']
        scale=abs((original-mean)/difference['distance'])
        deltas.append({'key':difference['key'], 'original':original,'pyin':value,'bucket_mean':mean,
                       'distance_with_original_peer_scale':abs(value-mean)/scale,
                       'group':difference.get('group') or difference['key']})
    # Diagnostic only: replacement target pitch against original YIN peer scales.
    groups={}
    for d in deltas: groups[d['group']]=max(groups.get(d['group'],0),d['distance_with_original_peer_scale'])
    for d in item['warning']['differences']:
        if 'pitch' not in d['key']: groups[d.get('group') or d['key']]=abs(d['distance'])
    persistent=max(groups.values(),default=0)>=3 or sum(v>=2.5 for v in groups.values())>=2
    if not persistent: weak.append(number)
    item['review']={'number':number,'category':category,'advice':advice,'audio_evidence':evidence,'context_hypothesis':context,
                    'second_tracker_warning_persists_against_original_peer_scales':persistent,'pitch_comparisons':deltas}

summary={'created_at':datetime.now(timezone.utc).isoformat(), 'snapshot_at':snapshot['captured_at'],
         'items':len(items),'keep_provisionally':sum(n[0]=='keep' for n in NOTES),
         'compare_possible_alternatives':sum(n[0]=='compare' for n in NOTES),
         'tracker_sensitive_items':weak,
         'scope':'Instrumented audio assessment, not direct listening; no labels changed.',
         'limitations':['pYIN and YIN are related pitch estimators, not independent emotion recognizers.',
                        'Second-tracker diagnostic uses original peer means/scales; it is not a replacement full-corpus warning run.',
                        'Emotion2vec scores and embedding neighbors performed poorly on these custom moods in the preceding chapter-held-out benchmark.',
                        'Text meaning suggests hypotheses; it does not prove what the narrator performed.',
                        'This is a frozen warning snapshot. The user may continue changing the live labels.']}
(OUT/'review.json').write_text(json.dumps({'summary':summary,'items':items},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
intro=f'''# Book 1: review of all {len(items)} warned clips

Snapshot: {snapshot['captured_at']}.

This is an **instrumented audio assessment, not direct listening**. I checked the exact WAV hashes, a second pitch estimate (pYIN), pace/loudness measurements, nearby labeled audio examples, and story context. No saved labels, warnings, training gates, or audio files were changed.

## Findings

- {summary['keep_provisionally']} clips: current label remains reasonable provisionally.
- {summary['compare_possible_alternatives']} clips: useful alternative or voice-register comparison; none is a confirmed automatic correction.
- Every warning involves pitch; only item 19 also has an independent pace warning.
- {len(weak)} warnings weaken below the current rule when replacing target pitch values with pYIN: items {', '.join(map(str,weak))}. Peer baselines still use the original tracker, so this is sensitivity evidence, not a definitive false-positive count.
- Most urgent comparisons: item 20 (Uline’s register), 19 (slower Kirill aside), 12 (capsule opening), and 13 (Confused versus a matter-of-fact explanation).
- Emotion2vec does not decide these labels reliably. Its broad labels and highly similar embeddings can group distinct moods. The Surprised result for item 12 is weak corroboration, not an automatic verdict.

## Clip notes
'''
markdown=[intro]; cards=[]
for item in items:
    r=item['review']; row=item['row']; f=item['features']; p=item['second_pitch']
    current=label(item['warning']['bucket'])
    markdown.append(f"\n### {r['number']}. {row['source_text'].strip()}\n\nID: `{row['id']}` · {row['source_id']} · {current}\n\n**Suggestion:** {r['advice']}\n\n**Audio evidence:** {r['audio_evidence']}\n\n**Context only:** {r['context_hypothesis']}\n\n")
    values=''.join(f'<tr><td>{esc(d["key"].replace("_"," "))}</td><td>{d["original"]:.2f}</td><td>{d["pyin"]:.2f}</td><td>{d["bucket_mean"]:.2f}</td></tr>' for d in r['pitch_comparisons'])
    references=''
    if item.get('current_reference'): references+=reference(item['current_reference']['example'],'Comparison in current bucket')
    for suggestion in item['suggestions'][:2]:
        references+=reference(suggestion['example'],'Compare '+label(suggestion['bucket'])+' (uncertain match)')
    cards.append(f'''<article id="clip-{r['number']}" data-category="{r['category']}">
    <header><h2>{r['number']}. {esc(current)}</h2><span>{'Keep provisionally' if r['category']=='keep' else 'Possible alternative'}</span></header>
    <p class="source" lang="ru">{esc(row['source_text'])}</p>{audio(item['precision']['audio'])}
    <p><b>Suggestion:</b> {esc(r['advice'])}</p><p><b>Audio evidence:</b> {esc(r['audio_evidence'])}</p>
    <p><b>Context only:</b> {esc(r['context_hypothesis'])}</p>
    <details><summary>Measurements and paragraph context</summary>
    <p>Median pitch: YIN {f['median_pitch_hz']:.1f} Hz; pYIN {p['median_pitch_hz']:.1f} Hz. Pace proxy: {f['envelope_peaks_per_second']:.2f} sound peaks/s. Whole-clip RMS: {f['average_level_dbfs']:.1f} dBFS.</p>
    <p>pYIN voiced frames: {p['voiced_fraction_of_active_frames']:.0%} of active frames; beginning {p['beginning_voiced_frames']}, ending {p['ending_voiced_frames']}. These are tracker support counts, not confidence in a mood.</p>
    <table><tr><th>Flagged pitch metric</th><th>YIN</th><th>pYIN</th><th>Original peer mean</th></tr>{values}</table>
    <p>{'Warning persists' if r['second_tracker_warning_persists_against_original_peer_scales'] else 'Warning weakens below cutoff'} in the second-tracker diagnostic against original peer scales. Not a recomputed production warning.</p>
    <p lang="ru">{esc(item['context'].get('text',''))}</p></details>
    <details><summary>Playable comparisons</summary>{references or '<p>Too few measured alternative buckets for this voice.</p>'}</details>
    <p class="small">{esc(row['id'])} · {esc(row['source_id'])} · {esc(item['context'].get('chapter_title',''))}</p></article>''')
(OUT/'REPORT.md').write_text(''.join(markdown),encoding='utf-8')
page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Book 1 · Warned audio review</title>
<style>body{font:18px/1.6 system-ui;margin:0;background:#f5f5f0;color:#213d32}main{max-width:1050px;margin:auto;padding:24px}article,.intro{background:white;border:1px solid #ced8d1;border-radius:14px;padding:24px;margin:22px 0}h1{font-size:28px}h2{font-size:22px;margin:0}header{display:flex;gap:15px;justify-content:space-between;flex-wrap:wrap}header span{font-size:14px;background:#edf1e9;padding:4px 10px;border-radius:8px}.source{font:24px/1.6 Georgia,serif;background:#f6f6f2;padding:16px}audio{width:100%;max-width:800px}summary{cursor:pointer;padding:8px 0}td,th{padding:8px;text-align:left;border-bottom:1px solid #ddd}.small{font-size:14px;color:#62736a}.reference{padding:18px 0;border-bottom:1px solid #ddd}a{color:#246b4e}table{font-size:14px;width:100%;overflow-wrap:anywhere}</style><main>
<h1>Book 1 · All 21 warned clips</h1><p><a href="/character-rb1/fine-tune-all/buckets.html">Back to grouped verification</a></p>
<section class="intro"><b>Read-only audio measurement review — not direct listening.</b><p>All 21 exact WAVs checked. Your labels remain unchanged. Suggestions are cautious hypotheses, not corrections.</p>
<p>11 current labels remain reasonable provisionally; 10 have useful alternatives to compare. Seven warnings weaken when the pitch estimator changes. A warning means unusual acoustics, not necessarily a misplaced mood.</p>
<p>Priority comparisons: <a href="#clip-20">20 · Uline</a>, <a href="#clip-19">19 · Kirill</a>, <a href="#clip-12">12 · Capsule</a>, <a href="#clip-13">13 · Marker</a>.</p>
<p class="small">Frozen snapshot, 29 September 2026. Every warning involves pitch; only item 19 also has an independent pace warning. Emotion2vec comparisons are weak evidence and do not determine the suggested labels. Context is shown separately because the actual performance may differ from the story’s emotion.</p></section>'''+''.join(cards)+'''</main><script>document.addEventListener('play',event=>{if(event.target.tagName==='AUDIO')document.querySelectorAll('audio').forEach(player=>{if(player!==event.target)player.pause()})},true);</script></html>'''
(OUT/'index.html').write_text(page,encoding='utf-8')
(board/'warned-audio-review.html').write_text(page,encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
