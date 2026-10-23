"""Render read-only assessment; do not publish corrections to the corpus."""
from pathlib import Path
import html
import json
import re
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'training-work/character-rb1'
OUT = BASE / 'warned-audio-review-20260929'
data = json.loads((OUT / 'measurements.json').read_text(encoding='utf-8'))
snapshot = json.loads((OUT / 'snapshot.json').read_text(encoding='utf-8'))
rows = {row['id']:row for row in [json.loads(line) for line in (BASE/'fine-tune-all/candidates.jsonl').read_text(encoding='utf-8').splitlines()]}
precision = json.loads((BASE/'fine-tune-all/precision-v2/status.json').read_text(encoding='utf-8'))['clips']
shared = (ROOT/'scripts/book1_review_shared.js').read_text(encoding='utf-8')
mood_section = shared.split('const MOOD_LABELS = {',1)[1].split('};',1)[0]
mood_names = dict(re.findall(r"(\w+):'([^']+)'", mood_section))
mood_names.update({'neutral':'Normal','reflective':'Thinking','anxious':'Nervous / tense','perplexed':'Confused','solemn':'Serious'})
voice_names = {'narrator_baseline':'Narrator baseline','masculine_2':'Masculine voice 2 (like Kisly)',
               'masculine_4':'Masculine voice 4 (like Kirill)','feminine_3':'Feminine voice 3 (like Uline)',
               'mechanical_stats':'Game / mechanical voice'}
# Audio-supported observations and text-only hypotheses stay separate.
notes = [
 ('keep','Normal',
  'The second tracker confirms a high opening and a large downward movement at the end. Neural comparisons include both Normal and Calm; none of the existing bucket alternatives is a clear improvement.',
  'This introduces someone else\'s next utterance. The colon and sentence structure are plausible reasons for unusual phrase edges.',
  'Keep Normal provisionally. A strong sentence opening or ending alone does not establish Confident.'),
 ('keep','Normal',
  'Pitch median and range agree between trackers. The final fall becomes less exceptional with the second tracker. Nearby neural examples have mixed moods, including Normal.',
  'A long, matter-of-fact summary ending an enumeration of what was explained.',
  'Keep Normal. Treat the final fall as a phrase-ending feature unless a different mood is audible throughout.'),
 ('compare','Normal; possibly Annoyed',
  'The broad pitch range survives the second tracker: about 11.9 semitones. The generic emotion model suggests anger, but the nearby user labels are mixed; this is weak evidence of irritation, not proof.',
  'The paired negatives can produce emphatic contrast. The situation is mildly comic; that does not by itself mean sarcasm.',
  'Start with Normal with emphasis. Annoyed is a candidate only if the delivery sounds irritated, rather than just stressing the negatives. Do not move to Energetic from pitch range alone.'),
 ('compare','Normal / Soft; Relieved only if audible',
  'The second tracker confirms a relatively high ending. Cached audio comparisons include Soft and Calm, while the broad classifier is Neutral. Audio evidence does not uniquely support relief or excitement.',
  'He realizes the boss will not scold him. Relieved is a story-based possibility, not an audio finding.',
  'Compare Normal with Soft / quiet first. Use Relieved only if there is a clear relaxing or brightening change in Vlad\'s delivery.'),
 ('keep','Normal',
  'The large final fall is reproduced, though it weakens slightly. The audio model stays broadly neutral and nearest examples span several labels.',
  'The sleepy person being described is another character, not necessarily a sleepy narrator.',
  'Keep Normal. Do not assign Tired merely because the pilot is dozing.'),
 ('keep','Normal',
  'The wide pitch range and steep ending both survive. The median pitch is low, and the whole clip is relatively quiet. Nearby neural examples mostly support ordinary narration.',
  'The short appended judgment “не моё” can have a separate falling accent without changing the whole sentence\'s mood.',
  'Keep Normal provisionally. Mark the tail as a delivery feature; do not call the whole sentence Sad or Thinking from a low final pitch.'),
 ('compare','Normal / Thinking',
  'The falling ending is real but no longer reaches the same single-feature warning threshold with the second tracker. Pace proxy is high, about 5.15 sound peaks/s. Neural examples include both Thinking and Normal.',
  'The previous questions and trailing ellipsis leave an unresolved practical concern.',
  'Normal remains defensible. Thinking is an alternative if the concern is audible. A softened ellipsis tail should be recorded separately from the mood; this is not evidence that the entire clip is Calm.'),
 ('keep','Normal',
  'The second tracker reproduces the wide range and low ending. The broad classifier and several nearby examples support neutral narration.',
  'A transition back into the account after the bracelet inspection.',
  'Keep Normal. The ending can close the paragraph strongly without indicating a different emotion.'),
 ('keep','Calm',
  'A very large falling tail survives both trackers. Other sentences in this paragraph are also labeled Calm, and one measured neighbor has a similar median pitch.',
  'The repeated tournaments and trailing ellipsis can create a fading list ending.',
  'Keep Calm. Thinking or remembering should not be inferred from the ellipsis alone. Preserve the distinct ending as a delivery feature for later training.'),
 ('keep','Calm',
  'The high beginning is reproduced, but the median is only about 113 Hz. Neural comparisons include Calm. Adjacent narration in the paragraph has medians around 111–115 Hz, consistent with this clip.',
  'The narrator is becoming sleepy. This makes Tired plausible in meaning, but does not prove that Vlad performed it as tired.',
  'Keep Calm unless fatigue is clearly audible. The high opening is not a reason to choose Excited or Surprised.'),
 ('keep','Calm',
  'A strong opening and broad range survive; the pace proxy is low, about 3.12 peaks/s. The broad classifier is Neutral and nearby examples do not agree on a replacement mood.',
  'This returns to present narration after Kisly\'s account. Mentioning a historical account does not make this clip Historical prose.',
  'Keep Calm provisionally. Leave the voice family as Narrator baseline; historical prose needs a sustained distinct delivery, not just a reference to the past.'),
 ('compare','Confident / Surprised / Curious',
  'The strong ending survives the second tracker. This is the one flagged narrator clip where the generic classifier favors Surprised strongly (raw score 0.823); its nearest embedding examples disagree. That score is not a calibrated probability.',
  'The lid opens with just a touch, after he admires the capsule. Wonder or surprise would fit the context, but might not match the performed audio.',
  'Worth comparing against Surprised and Curious references. Do not keep Confident solely because the sentence is declarative, and do not replace it solely because the generic classifier says Surprised.'),
 ('compare','Confused / Normal / Explaining',
  'The rise on the final phrase is reproduced. Neural neighbors are predominantly Normal; the broad model says Angry, which is not a convincing replacement. Other labeled sentences in the same paragraph are Confident.',
  'This sentence explains a deliberate marker choice after a solution is found. “тупик” can be emphasized as clarification rather than confusion.',
  'Normal or Explaining is worth comparing with the current Confused. Do not use Surprised just because the final pitch rises.'),
 ('keep','Nervous / tense',
  'The ending warning falls below the old single-feature threshold with the second tracker. Neutral neural comparisons cannot reliably rule out mild tension.',
  'The speaker reports fear, but reported fear and a fearful vocal performance are different things.',
  'Keep your Nervous / tense label provisionally. The current measurements do not justify replacing an ear-based decision with Normal.'),
 ('keep','Surprised',
  'The ending rises with both trackers, but its magnitude drops from about 9.6 to 6.1 semitones. The question ending itself can explain part of that rise. Neural examples include Curious; no clear bucket alternative was found.',
  'The rhetorical question is followed by repeated “Хочу!”. Interest and surprise are plausible, but the next sentence\'s excitement should not automatically be copied here.',
  'Keep Surprised if that is what you heard. Curious is a secondary comparison, not a recommended automatic move.'),
 ('compare','Same voice: Normal / Explaining; check timbre',
  'Median pitch is genuinely high for this bucket: about 174 Hz against 145 Hz in its other clips. The other edge deviations weaken with the second tracker. Closest neural comparisons in this family include Friendly and Normal; no reliable different-family result is available.',
  'Part of a technical explanation about capsule towers, with stress on unlimited height.',
  'First compare the voice itself against adjacent source speech and a Kisly reference. If the timbre matches, retain the family and consider Normal / Explaining. High pitch alone does not establish a different masculine voice.'),
 ('keep','Normal',
  'The elevated ending is reproduced but reduced. A Normal sentence later in the same paragraph has a median around 139 Hz; this clip is about 134 Hz. Nearby neural moods are mixed.',
  'Contrasting the previously proposed direction with the chosen direction can accent “сюда”.',
  'Keep Normal. The contrastive final accent does not require Energetic, Serious, or a new voice family.'),
 ('keep','Energetic',
  'The high opening persists. This clip also has a relatively high pace proxy (4.95 peaks/s) and high whole-clip volume (-20.7 dBFS). Its closest same-family neural example is labeled Energetic.',
  'The speaker presses him to decide while the opportunity remains open; Warning or In a hurry are context possibilities.',
  'Energetic remains plausible. Compare Warning only if the delivery sounds cautioning rather than simply brisk and insistent.'),
 ('compare','Normal / Serious',
  'This is the only warning set that includes a non-pitch cue: pace is about 2.91 peaks/s versus 4.44 in peers. The low ending survives. Another Normal clip in this paragraph has a similar median pitch (about 161 Hz). Neural neighbors misleadingly favor faster or sharper moods.',
  'A short admission that expulsion was deserved can be sober or matter-of-fact; sadness or fatigue need separate audible evidence.',
  'Keep the Kirill voice family. Compare Normal with Serious for this slower aside. Do not move it to Energetic because of its neural neighbors.'),
 ('compare','Check Uline voice first; Normal / Explaining / Warning',
  'This is the strongest absolute-pitch outlier: about 178 Hz against 140 Hz in its peers, with a broad range of about 11.5 semitones. Same-family neural similarity is much lower than most narrator matches; the nearest labeled example is Explaining. Generic Disgusted output is not a trustworthy mood verdict.',
  'She gives information about nearby people and says she decided to warn him. That may be a neutral heads-up, rather than a performed alarm.',
  'Compare the voice with Uline references before deciding the mood. If the timbre is consistent, Explaining or Normal may fit; Warning needs an audible cautioning delivery. Do not switch family from pitch alone.'),
 ('keep','Game / mechanical voice · Normal',
  'The high opening is reproduced. All five closest same-family neural examples are Normal. Other game-stat sentences in the same paragraph have somewhat higher medians but the same selected family.',
  'This really is a game-rule statement in a paragraph of game rules. Context supports the role; it is not ordinary prose merely containing a game-related word.',
  'Keep Game / mechanical voice with Normal. Mechanical delivery can still have emphatic phrase beginnings. The broad Disgusted classifier output is unsuitable here.'),
]
assert len(notes) == len(data) == 21

def esc(value): return html.escape(str(value))
def bucket_link(id):
    return '/character-rb1/fine-tune-all/buckets.html?' + urlencode({'clip':id,'focus':'1'})
def url(path):
    p = Path(path.replace('\\','/'))
    return '/' + p.relative_to('training-work').as_posix()
def player(path):
    return f'<audio controls preload="none" src="{esc(url(path))}"></audio>'
def reference(id, caption):
    if id not in rows or id not in precision: return ''
    return f'<section class="reference"><b>{esc(caption)}</b><p lang="ru">{esc(rows[id]["source_text"])}</p>{player(precision[id]["audio"])}</section>'

md = ['# Book 1: review of all 21 warned clips', '',
      f'Snapshot: {snapshot["captured_at"]}. This is a read-only instrumented review, not direct listening. No saved labels were changed.', '',
      '## Findings', '',
      '- Every flagged item has a pitch warning; only one also has a pace warning. These are not 21 confirmed labeling mistakes.',
      '- Eight clips have specific alternatives worth comparing. Thirteen have no persuasive reason to change your current label from these measurements.',
      '- Two absolute-pitch outliers deserve a voice comparison before mood decisions: the “кукурузины” explanation and Uline’s heads-up.',
      '- Seven warning sets become marginal with a second pitch estimator. That is measurement sensitivity, not proof that the original warning is false.',
      '- None of the existing alternative-bucket comparisons meets the stronger-match rule.', '',
      '## Limits and method', '',
      'Verified SHA-256 of every exact WAV; measured voiced pitch with pYIN using the same frequency range, frame sizes, activity mask, and contour windows as the existing YIN pass. Used cached emotion2vec+ features to retrieve same-family examples from other paragraphs, with the frozen current labels. These comparisons are not validated mood classifiers. Generic classifier scores are raw scores, not probabilities of our custom labels. Story context is shown separately and never substitutes for the recorded performance.', '',
      'The threshold comparison below substitutes this clip’s second pitch estimate into the existing peer means and scales. Peers were not all remeasured with pYIN, so it is a diagnostic of sensitivity, not a replacement warning pass. “Keep” means no measurement-based case for overriding your decision; it does not certify the label.', '']
cards=[]
report=[]
weak=[]
for index,(item,note) in enumerate(zip(data,notes),1):
    action, choice, audio_note, context_note, recommendation = note
    distances=[]
    differences=[]
    for d in item['warning']['differences']:
        new = item['second_pitch'].get(d['key']) if 'pitch' in d['key'] else d.get('value')
        if new is not None and d.get('value') is not None and d.get('distance') and d.get('average') is not None:
            scale = abs((d['value']-d['average'])/d['distance'])
            dist = abs((new-d['average'])/scale) if scale else 0
            distances.append((d.get('group',d['key']),dist))
        else: dist=None
        differences.append({**d,'second_estimate':new,'diagnostic_distance':dist})
    groups={}
    for group,value in distances: groups[group]=max(value,groups.get(group,0))
    sensitive = not (any(value>=3 for value in groups.values()) or sum(value>=2.5 for value in groups.values())>=2)
    if sensitive: weak.append(index)
    row=item['row']; current=voice_names[item['labels']['voice']]+' · '+mood_names.get(item['labels']['mood'],item['labels']['mood'])
    result={'number':index,'id':row['id'],'labels_at_snapshot':item['labels'],'action':action,
            'suggested_comparisons':choice,'audio_observation':audio_note,'context_only':context_note,
            'recommendation':recommendation,'threshold_sensitive':sensitive,'differences':differences,
            'audio':item['precision']['audio'],'audio_sha256':item['precision']['audio_sha256']}
    report.append(result)
    md += [f'## {index}. {row["source_text"].strip()}', '',f'`{row["id"]}` · {current}', '',
           f'[Open this clip in bucket UI](http://127.0.0.1:1437{bucket_link(row["id"])})', '',
           f'**Suggestion:** {recommendation}', '',f'**Audio measurements:** {audio_note}', '',
           f'**Context only:** {context_note}', '',f'**Comparison choices:** {choice}', '',
           f'**Second-tracker threshold check:** {"marginal / sensitive" if sensitive else "deviation remains"}.', '']
    measurements=''.join(f'<tr><td>{esc(d["label"])}</td><td>{d.get("value",0):.2f}</td><td>{d.get("average",0):.2f}</td><td>{d["second_estimate"]:.2f}</td></tr>' for d in differences if d['second_estimate'] is not None)
    references=''
    if item['current_reference']:
        references+=reference(item['current_reference']['example'],'Current bucket: measured comparison')
    for suggestion in item['suggestions'][:2]:
        mood=suggestion['bucket'].split('|')[1]
        references+=reference(suggestion['example'],mood_names.get(mood,mood)+' comparison (uncertain)')
    # A same-paragraph reference is especially useful when a voice looks different.
    if item['same_paragraph']:
        references+=reference(item['same_paragraph'][0]['id'],'Same paragraph: neighboring source sentence')
    cards.append(f'''<article id="clip-{index}" class="{action}"><header><h2>{index}. {"Compare alternatives" if action=="compare" else "Keep provisionally"}</h2><span>{esc(current)}</span></header>
<p class="text" lang="ru">{esc(row['source_text'])}</p>{player(item['precision']['audio'])}
<p><a href="{esc(bucket_link(row['id']))}" target="_blank" rel="noopener"><strong>Open this clip in bucket UI →</strong></a></p>
<p><strong>Suggestion:</strong> {esc(recommendation)}</p><p><strong>Audio measurements:</strong> {esc(audio_note)}</p>
<p><strong>Context only:</strong> {esc(context_note)}</p><p><strong>Choices to compare:</strong> {esc(choice)}</p>
<details><summary>Measurements and comparison audio</summary><p>{"This warning weakens with the second tracker." if sensitive else "The deviation is reproduced by the second tracker."} This does not decide the mood.</p>
<table><thead><tr><th>Warning measurement</th><th>First tracker</th><th>Peer mean</th><th>Second tracker</th></tr></thead><tbody>{measurements}</tbody></table>
<div class="references">{references}</div><h3>Full source paragraph</h3><p lang="ru">{esc(item['context'].get('text',''))}</p>
<p class="small">{esc(row['id'])} · exact WAV SHA-256 {esc(item['precision']['audio_sha256'])}</p></details></article>''')

(OUT/'assessment.json').write_text(json.dumps({'schema':1,'snapshot_at':snapshot['captured_at'],
    'method':'Instrumented audio analysis; not direct auditory listening',
    'candidates_sha256':snapshot['candidates_sha256'],'saved_labels_modified':False,
    'threshold_sensitive_clip_numbers':weak,'clips':report},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
(OUT/'REPORT.md').write_text('\n'.join(md)+'\n',encoding='utf-8')
page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Book 1 · Warned clip review</title>
<style>body{margin:0;background:#f7f7f2;color:#213c32;font:17px/1.55 system-ui,sans-serif}main{max-width:1050px;margin:auto;padding:22px}nav{position:sticky;top:0;background:#f7f7f2;border-bottom:1px solid #ccc;padding:8px 20px;z-index:2;font-size:14px}a{color:#275c45}article{background:white;border:1px solid #c7d0c9;border-radius:12px;padding:22px;margin:20px 0;scroll-margin-top:55px}article.compare{border-left:5px solid #b97522}h1,h2,h3{line-height:1.3}h2{margin:0;font-size:21px}header{display:flex;gap:20px;justify-content:space-between;flex-wrap:wrap}header span,.small{font-size:14px;color:#586a60}.text{font:24px/1.5 Georgia,serif;background:#f4f5f1;padding:18px}audio{width:100%}summary{cursor:pointer;font-weight:600}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:8px;border-bottom:1px solid #ddd}.references{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}.reference{background:#f4f5f1;padding:14px;margin-top:14px}.reference p{font-family:Georgia,serif}.small{overflow-wrap:anywhere}</style>
<nav><a href="/character-rb1/fine-tune-all/buckets.html">Back to grouped verification</a> · <label>Jump to clip <select id="jump-clip" style="max-width:480px;width:45vw;font:inherit"><option value="">Choose a phrase</option>JUMP_OPTIONS</select></label></nav><main>
<h1>Book 1: all warned clips</h1><p><strong>This is audio measurement analysis, not direct listening.</strong> Your saved labels have not been changed.</p>
<p>Eight clips have alternatives worth comparing. For thirteen, there is no persuasive measurement-based reason to override your label. Every warning involves pitch; only one also involves pace. None of the existing alternative-bucket comparisons is a clear enough improvement to recommend an automatic move.</p>
<p>Pitch was checked with a second estimator, and cached neural audio features were compared with your labeled examples. Seven warning sets weaken under the second estimator. Voice timbre and subtle moods remain uncertain; the generic emotion model has not passed our custom-label benchmark. Context is kept separate below.</p>
<p class="small">Snapshot: SNAPSHOT. Labels can have changed since then. The players use the exact training WAVs at normal playback speed. This page cannot save or move clips.</p>
<h3>Clips with specific alternatives</h3><ul>PRIORITIES</ul>CARDS</main><script>document.getElementById('jump-clip').addEventListener('change',event=>{if(event.target.value)location.hash=event.target.value});document.addEventListener('play',event=>{if(event.target.tagName==='AUDIO')document.querySelectorAll('audio').forEach(audio=>{if(audio!==event.target)audio.pause()})},true)</script></html>'''
priorities=''.join(f'<li><a href="#clip-{r["number"]}">{esc(data[r["number"]-1]["row"]["source_text"].strip())}</a></li>' for r in report if r['action']=='compare')
jump_options=''.join(f'<option value="clip-{i}">{i}. {esc(item["row"]["source_text"].strip()[:75])}</option>' for i,item in enumerate(data,1))
page=page.replace('SNAPSHOT',esc(snapshot['captured_at'])).replace('PRIORITIES',priorities).replace('JUMP_OPTIONS',jump_options).replace('CARDS',''.join(cards))
(OUT/'index.html').write_text(page,encoding='utf-8')
(BASE/'fine-tune-all/warned-audio-review.html').write_text(page,encoding='utf-8')
print(json.dumps({'clips':len(report),'compare':sum(r['action']=='compare' for r in report),
                  'keep':sum(r['action']=='keep' for r in report),'threshold_sensitive':weak,
                  'html':str(OUT/'index.html')},indent=2))
