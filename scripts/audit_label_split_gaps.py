"""Read-only split audit of canonical reviews; outputs listening proposals only.

No labels, split plans, playback evidence, or acceptance records are written.
Text structure establishes review candidates, never an audible delivery verdict.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'training'), str(ROOT / 'runtime')]
from prepare_bucket_split_plan import parenthetical_spans, narrator_placeholder_spans
from rimsky_training.pairing import compound_tokens
from review_label_resolution import resolved_review_mood, resolved_review_speaker

WORDS = re.compile(r'[\w]+', re.UNICODE)
AUTHOR = re.compile(
    r'\b(?:сказал[аи]?|спросил[аи]?|ответил[аи]?|шепнул[аи]?|прошептал[аи]?|'
    r'закричал[аи]?|крикнул[аи]?|проговорил[аи]?|произн[её]с(?:ла)?|'
    r'пояснил[аи]?|уточнил[аи]?|признал(?:ся|ась)|усмехнул(?:ся|ась)|'
    r'обв[её]л|повернул(?:ся|ась)|указал[аи]?|поинтересовал(?:ся|ась)|'
    r'заверил[аи]?|упрекнул[аи]?|проворчал[аи]?|похвалил[аи]?|'
    r'перебил[аи]?|прервал[аи]?|смутилась|ощерился|огрызнулась|'
    r'засмущался|рассмеял(?:ся|ась)|заявил[аи]?|вклинил(?:ся|ась)|смутил(?:ся|ась)|'
    r'удивил(?:ся|ась)|не\s+унимался|раздался|с\s+этими\s+словами|'
    r'по\s+просьбе|в\s+мо[её]м\s+мозгу|признаться|я\s+(?:не\s+стал|остановил))\b', re.I)
THOUGHT = re.compile(r'\b(?:подумал|думал|мысленно|мысль|возвестили|крикнули)\b', re.I)
QUOTED_SPEECH = re.compile(r'\b(?:сказав|сказал[аи]?|шепнул[аи]?|бормотал[аи]?|'
                           r'возвестили|воскликнул[аи]?|проговорил[аи]?)\b', re.I)
BASIC_FAILURES = {'missing_or_changed_audio', 'unexpected_pcm_format', 'empty_audio',
                  'duration_mismatch', 'clipping', 'level_outlier'}
BOOKS = ('reality-benders-1', 'reality-benders-2-full', 'reality-benders-2')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def lexical_boundary(text, offset):
    """Same lexical edge despite punctuation placement in an older saved cut."""
    return sum(start < offset for _, start, _ in compound_tokens(text))


def remaining_edges(text, offsets, saved):
    count = len(compound_tokens(text))
    done = {lexical_boundary(text, x) for x in saved}
    return sorted({x for x in offsets if not any(a < x < b for _, a, b in compound_tokens(text))
                   and 0 < lexical_boundary(text, x) < count
                   and lexical_boundary(text, x) not in done})


def quote_depth(text, offset):
    return text[:offset].count('«') - text[:offset].count('»')


def structural_events(text, role='narration'):
    events = []
    known = {(x['start'], x['end']) for x in narrator_placeholder_spans(text)}
    for span in parenthetical_spans(text):
        specific = (span['start'], span['end']) in known
        events.append({'kind': 'spoken_placeholder' if specific else 'parenthetical_aside',
                       'priority': 'strong' if specific else 'listen',
                       'offsets': [span['start'], span['end']],
                       'evidence': text[span['start']:span['end']],
                       'reason': ('Printed placeholder embedded in other speech; review the narrator insert.'
                                  if specific else 'Parenthetical aside may use a different delivery; listen to confirm.')})
    # Require dialogue punctuation AND a recognisable author clause. This avoids
    # treating explanatory dashes, lists and quoted technical nouns as speakers.
    for match in re.finditer(r'[—–]\s*', text):
        before = text[:match.start()].rstrip()
        if not before or before[-1] not in ',.!?…' or quote_depth(text, match.start()) > 0:
            continue
        after = text[match.end():]
        clause = re.split(r'[—–]', after, maxsplit=1)[0]
        if AUTHOR.search(clause[:180]):
            events.append({'kind': 'speech_author_transition', 'priority': 'strong',
                           'offsets': [match.end()], 'evidence': clause.strip(),
                           'reason': 'Character speech changes to an author attribution/action.'})
            # The next dash can resume speech. An em dash inside this clause is
            # not blindly accepted as a speaker change; punctuation is required.
            next_dash = re.search(r'[,.;!?…]\s*[—–]\s*', after)
            if next_dash:
                events.append({'kind': 'author_speech_resumption', 'priority': 'strong',
                               'offsets': [match.end() + next_dash.end()],
                               'evidence': after[next_dash.end():].strip(),
                               'reason': 'Speech resumes after an author insertion.'})
    if THOUGHT.search(text) and (role == 'narration' or re.match(r'^\s*«', text)):
        for match in re.finditer(r'«[^«»]+»', text):
            events.append({'kind': 'quoted_thought_or_call', 'priority': 'listen',
                           'offsets': [match.start(), match.end()], 'evidence': match.group(),
                           'reason': 'Quoted thought/call within narration; its audible register needs listening.'})
    if role == 'narration' and not re.match(r'^\s*[—–]', text):
        for match in re.finditer(r':\s*(«[^«»]*(?:»|$))', text):
            if QUOTED_SPEECH.search(text[:match.start()]):
                a, b = match.span(1)
                events.append({'kind': 'quoted_speech_in_narration', 'priority': 'listen',
                               'offsets': [a, b], 'evidence': match.group(1),
                               'reason': 'Narration introduces quoted speech; listen for a change of performer/register.'})
    return events


def model_events(parts, start, end, text, reviewed=False):
    result = []
    relevant = [p for p in parts if p['end'] > start and p['start'] < end]
    for left, right in zip(relevant, relevant[1:]):
        a, b = left.get('speaker'), right.get('speaker')
        mode_change = left.get('mode') != right.get('mode') if reviewed else (a == 'narrator') != (b == 'narrator')
        if mode_change or a != b:
            offset = right['start'] - start
            result.append({'kind': 'reviewed_text_part_transition' if reviewed else 'model_part_transition',
                           'priority': 'strong' if reviewed else 'model', 'offsets': [offset],
                           'evidence': {'before': a, 'after': b},
                           'reason': ('Frozen reviewed Book 1 text spans change role/performer.' if reviewed
                                      else 'Frozen context proposes different performers; prediction is unverified.')})
        elif left.get('delivery') != right.get('delivery'):
            result.append({'kind': 'model_delivery_transition', 'priority': 'listen',
                           'offsets': [right['start'] - start],
                           'evidence': {'before': left.get('delivery'), 'after': right.get('delivery')},
                           'reason': 'Context proposes an internal delivery change; audio labels take precedence.'})
    return result


def frozen_passage_text(rows, source_id, start, end):
    """Recover a raw request from its contiguous normalized child rows."""
    matching = sorted((p for p in rows if p['source_id'] == source_id
                       and p['start'] >= start and p['end'] <= end), key=lambda p: p['start'])
    cursor, pieces = start, []
    for p in matching:
        if p['start'] != cursor or len(p['text']) != p['end'] - p['start']:
            return None
        cursor = p['end']
        pieces.append(p['text'])
    return ''.join(pieces) if cursor == end else None


class Inputs:
    def __init__(self):
        self.hashes = {}

    def read(self, path, lines=False):
        path = Path(path)
        data = path.read_bytes()
        self.hashes[path.relative_to(ROOT).as_posix()] = digest(data)
        return [json.loads(x) for x in data.decode('utf-8').splitlines() if x] if lines else json.loads(data)

    def canonical(self, folder):
        # Capture a self-consistent snapshot while the listener may be saving.
        for _ in range(20):
            review = self.read(folder / 'review.json')
            latest = self.read(folder / 'latest.json')
            if self.hashes[(folder / 'review.json').relative_to(ROOT).as_posix()] == latest['review_sha256']:
                return review
        raise ValueError(f'Could not capture matching review/latest checksums: {folder}')


def explicit_label(decision):
    return bool(decision.get('voice_verdict') or decision.get('mood_verdict')
                or decision.get('speaker_verdict'))


def audit_book(book, inputs, gold, raw, delivery_hints=None):
    folder = ROOT / 'manual-labeling' / book
    review = inputs.canonical(folder)
    candidates = inputs.read(folder / 'metadata/candidates.jsonl', lines=True)
    candidate_hash = inputs.hashes[(folder / 'metadata/candidates.jsonl').relative_to(ROOT).as_posix()]
    if candidate_hash != review['candidates_sha256']:
        raise ValueError(f'Candidate checksum does not match canonical review: {book}')
    plan_path = folder / 'metadata/split-plan.json'
    plan = inputs.read(plan_path) if plan_path.exists() else {'clips': {}}
    if plan.get('candidates_sha256', candidate_hash) != candidate_hash:
        raise ValueError('Split plan candidate hash changed')
    source_path = folder / 'metadata' / ('paragraph-sources.json' if book.endswith('-1') else 'source-context.json')
    sources = inputs.read(source_path)['sources'] if source_path.exists() else {}
    overlay_path = folder / 'metadata/voice-proposal-overrides.json'
    overlay = inputs.read(overlay_path) if overlay_path.exists() else {}
    if overlay.get('candidates_sha256', candidate_hash) != candidate_hash:
        raise ValueError('Voice overlay candidate checksum mismatch')
    precision = inputs.read(folder / 'metadata/precision-v2/status.json')['clips'] if book.endswith('-1') else {}
    quality = inputs.read(folder / 'metadata/quality-screen.json')['clips'] if book.endswith('-1') else {}
    findings, counts = [], Counter(candidates=len(candidates))
    for row in candidates:
        cid, text = row['id'], row['source_text']
        decision = review['decisions'].get(cid, {})
        split = decision.get('split_review', {})
        saved = split.get('cuts', []) if split.get('enabled') else []
        if saved and (split.get('parent_audio_sha256') != row['audio_sha256']
                      or split.get('candidates_sha256') != candidate_hash):
            raise ValueError(f'Saved split identity mismatch: {cid}')
        counts['enabled_split_parents'] += bool(split.get('enabled'))
        labelled = explicit_label(decision) or any(explicit_label(p) for p in split.get('parts', {}).values())
        counts['explicitly_labelled_parents'] += labelled
        p, q = precision.get(cid, {}), quality.get(cid, {})
        held = bool(decision.get('rejected') or decision.get('boundary_start') or decision.get('boundary_end'))
        if book.endswith('-1'):
            held |= (p.get('status') != 'precision_pass' or not p.get('audio') or row.get('role') == 'mixed'
                     or q.get('audio_sha256') != row['audio_sha256'] or bool(set(q.get('reasons', [])) & BASIC_FAILURES))
        status = 'already_held' if held else 'labelled' if labelled else 'not_explicitly_labelled'
        events = structural_events(text, row.get('role'))
        start, end = row['text_start'], row['text_end']
        case = gold.get(row['source_id']) if book.endswith('-1') else None
        paragraph = row.get('paragraph_text') or sources.get(row['source_id'], {}).get('text', '')
        if case and case['text'][start:end] == text:
            events += model_events(case['parts'], start, end, text, reviewed=True)
        elif case:
            counts['gold_text_mismatch_skipped'] += 1
        model_hints = []
        for passage in raw.get(book, {}).get(row['source_id'], []):
            if paragraph[passage['start']:passage['end']] != passage['text']:
                counts['context_text_mismatch_skipped'] += 1
                continue
            model_hints += model_events(passage['parts'], start - passage['start'], end - passage['start'], text)
        structural_offsets = {x for e in events for x in e['offsets']}
        delimiters = {m.end() for m in re.finditer(r'[—–]\s*', text)}
        delimiters |= {m.start() if m.group() == '«' else m.end() for m in re.finditer(r'[«»]', text)}
        delimiters |= structural_offsets
        rejected_hints = []
        for hint in model_hints:
            offset = hint['offsets'][0]
            if hint['kind'] == 'model_delivery_transition':
                # Weak text intent is not a missed audio split. Preserve it in
                # the machine-readable appendix, outside the listening queue.
                if delivery_hints is not None:
                    delivery_hints.append(dict(hint, book=book, clip_id=cid, text=text,
                                               status=status, saved_cuts=saved))
                counts['unverified_delivery_hints_separate'] += 1
                continue
            if hint['kind'] == 'model_part_transition' and not any(abs(x - offset) <= 4 for x in delimiters):
                # Known frozen-context attribution errors also produce bogus
                # internal cuts. Keep those as diagnostics, not split advice.
                rejected_hints.append(hint)
                counts['nonstructural_model_boundary_skipped'] += 1
            else:
                if delimiters:
                    edge = min(delimiters, key=lambda x: abs(x - offset))
                    hint = dict(hint, model_offset=offset, offsets=[edge])
                    if (edge < len(text) and text[edge] == '«') or (edge and text[edge - 1] == '»'):
                        hint['reason'] = ('Context proposes a register/performer change near a quotation. '
                                          'A nested quote can retain the outer speaker; listen before splitting.')
                events.append(hint)
        surviving = []
        for event in events:
            offsets = remaining_edges(text, event['offsets'], saved)
            if offsets:
                surviving.append(dict(event, offsets=offsets))
        if not surviving:
            counts['no_remaining_structural_flag'] += 1
            continue
        counts[status] += 1
        priority = min((x['priority'] for x in surviving), key={'strong': 0, 'model': 1, 'listen': 2}.get)
        counts[status + '_' + priority] += 1
        offsets = sorted({x for event in surviving for x in event['offsets']})
        planned = plan['clips'].get(cid, {})
        if planned and planned.get('audio_sha256') != row['audio_sha256']:
            raise ValueError(f'Split proposal audio mismatch: {cid}')
        boundaries = []
        for offset in offsets:
            equivalent = [x for x in planned.get('cuts', [])
                          if lexical_boundary(text, x['offset']) == lexical_boundary(text, offset)]
            edge = min(equivalent, key=lambda x: abs(x['offset'] - offset)) if equivalent else None
            boundaries.append({'text_offset': offset, 'before': text[:offset].strip(),
                               'after': text[offset:].strip(), 'word_aligned_proposal': edge,
                               'audio_edge_approved': False})
        actual_audio = p.get('audio') or row.get('exact_audio') or row.get('audio')
        expected_audio_hash = p.get('audio_sha256') or row['audio_sha256']
        path = ROOT / actual_audio if actual_audio else None
        measured = digest(path.read_bytes()) if path and path.exists() else None
        if measured is not None and measured != expected_audio_hash:
            raise ValueError(f'Audio changed: {actual_audio}')
        board = {'reality-benders-1': ('1437', 'character-rb1', 'buckets.html'),
                 'reality-benders-2-full': ('1439', 'character-rb2-full', ''),
                 'reality-benders-2': ('1438', 'character-rb2', '')}[book]
        voice = decision.get('corrected_voice_id') or decision.get('confirmed_voice_id')
        voice = voice or overlay.get('clips', {}).get(cid, {}).get('base_voice_id_proposed') or row.get('base_voice_id_proposed')
        mood = resolved_review_mood(', '.join(row.get('mood_proposed', [])), decision.get('mood_verdict'), decision.get('corrected_mood'))
        findings.append({'book': book, 'clip_id': cid, 'status': status, 'priority': priority,
                         'chapter': row.get('chapter_title') or sources.get(row['source_id'], {}).get('chapter_title') or row['chapter_id'],
                         'source_id': row['source_id'], 'text': text, 'paragraph': paragraph,
                         'saved_cuts': saved, 'enabled_split': bool(split.get('enabled')),
                         'events': surviving, 'proposed_boundaries': boundaries,
                         'nonstructural_model_hints_not_used': rejected_hints,
                         'shown_voice': voice, 'reviewed_mood': mood,
                         'speaker': resolved_review_speaker(row.get('character_id'), decision.get('speaker_verdict'), decision.get('corrected_character_id')),
                         'audio_path': str(path.relative_to(ROOT)).replace('\\', '/') if path else None,
                         'audio_sha256': expected_audio_hash, 'audio_verified': measured is not None,
                         'parent_audio_sha256': row['audio_sha256'],
                         'existing_audio_edge_hold': row.get('alignment_status') not in (None, 'exact_word_alignment'),
                         'board_url': f'http://127.0.0.1:{board[0]}/{board[1]}/fine-tune-all/{board[2]}?focus=1&clip={cid}',
                         'split_parts': split.get('parts', {})})
    findings.sort(key=lambda x: ({'labelled': 0, 'not_explicitly_labelled': 1, 'already_held': 2}[x['status']],
                                 {'strong': 0, 'model': 1, 'listen': 2}[x['priority']], x['source_id'], x['clip_id']))
    return dict(counts), findings


def write_html(report, output, server_mode=False):
    esc = lambda value: html.escape(str(value))
    cards = []
    for row in report['findings']:
        boundaries = ''.join(f'<li><b>{b["text_offset"]}</b>: {esc(b["before"][-65:])} <mark>│</mark> {esc(b["after"][:95])}'
                             f' — {"aligned proposal, needs edge review" if b["word_aligned_proposal"] else "text boundary only; audio alignment needed"}</li>'
                             for b in row['proposed_boundaries'])
        reasons = '<br>'.join(esc(e['reason']) for e in row['events'])
        audio = Path(ROOT / row['audio_path']) if row['audio_path'] else None
        src = Path(os.path.relpath(audio, output.parent)).as_posix() if audio else ''
        link = row['board_url']
        if server_mode:
            if row['book'] == 'reality-benders-2-full':
                src = '/' + row['audio_path'].removeprefix('training-work/')
                link = link.removeprefix('http://127.0.0.1:1439')
            elif row['book'] == 'reality-benders-1':
                archived = ROOT / 'manual-labeling/reality-benders-1/audio' / (row['audio_sha256'] + '.wav')
                src = '/manual-labeling/reality-benders-1/audio/' + archived.name if archived.exists() else ''
            else:
                src = ''  # Original pilot remains a separate local server.
        player = f'<audio controls preload="none" src="{esc(src)}"></audio>' if src else '<p>Audio is available in the local report.</p>'
        cards.append(f'<article data-status="{row["status"]}" data-priority="{row["priority"]}" data-book="{row["book"]}">'
                     f'<h2>{esc(row["chapter"])} · <a href="{esc(link)}">{esc(row["clip_id"])}</a></h2>'
                     f'<p>{esc(row["book"])} · {esc(row["status"])} · {esc(row["priority"])} · '
                     f'voice: {esc(row["shown_voice"])} · mood: {esc(row["reviewed_mood"])}</p>'
                     f'<blockquote>{esc(row["text"])}</blockquote><p>{reasons}</p>'
                     f'{player}<ul>{boundaries}</ul>'
                     f'<details><summary>Saved splits and paragraph</summary><p>Existing cuts: {esc(row["saved_cuts"])}</p>'
                     f'<p>{esc(row["paragraph"])}</p><small>Exact WAV: {esc(row["audio_sha256"])}</small></details></article>')
    summaries = ''.join(f'<li>{esc(k)}: {esc(json.dumps(v, ensure_ascii=False))}</li>' for k, v in report['counts'].items())
    page = '''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Book 1 and 2 — possible missed splits</title><style>body{font:17px system-ui;max-width:1000px;margin:auto;padding:20px;background:#faf9f5;color:#203127}article{padding:20px;background:white;border:1px solid #bcc7bd;border-radius:15px;margin:20px 0}h2{font-size:19px}blockquote{font-size:21px;margin:16px 0;white-space:pre-wrap}audio{width:100%}select{font:inherit;padding:10px;margin:4px}small{overflow-wrap:anywhere}li{margin:12px 0}mark{background:#ffd59b}header{position:sticky;top:0;background:#faf9f5;padding:10px;z-index:1}</style>
<h1>Possible missed splits</h1><p>Read-only proposals from text structure and the current saved review. Strong means strong structural evidence, not an audible verdict. Existing saved cuts are excluded. General asides and quoted thoughts need listening; the same performer can keep one delivery. No label or approval was changed.</p>
<p>Audit time: TIME. JSON contains every input hash and exact WAV hash. Covers candidate clips, not audio omitted from the boards. Subtle acoustic mood changes with no text cue are outside this audit.</p>
<details><summary>Counts and scope</summary><ul>SUMMARIES</ul></details>
<header><select id="status"><option value="labelled">Clips with explicit labels</option><option value="not_explicitly_labelled">Not explicitly labeled</option><option value="already_held">Already held (Book 1)</option><option value="">All</option></select><select id="book"><option value="">Both books + separate pilot</option><option value="reality-benders-1">Book 1</option><option value="reality-benders-2-full">Book 2 full edition</option><option value="reality-benders-2">Book 2 original pilot</option></select><select id="priority"><option value="">All reasons</option><option value="strong">Strong structure</option><option value="model">Model proposal</option><option value="listen">Listen to confirm</option></select><span id="count"></span></header>
CARDS<script>const selectors=['status','book','priority'];function filter(){let n=0;for(const a of document.querySelectorAll('article')){a.hidden=selectors.some(k=>document.getElementById(k).value&&a.dataset[k]!==document.getElementById(k).value);if(!a.hidden)n++}document.getElementById('count').textContent=n+' clips'}for(const k of selectors)document.getElementById(k).onchange=filter;filter();</script>'''
    output.write_text(page.replace('TIME', esc(report['created_at'])).replace('SUMMARIES', summaries).replace('CARDS', ''.join(cards)), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--publish-board', action='store_true', help='Publish a read-only report under the existing full Book 2 board; no restart or label writes.')
    args = parser.parse_args()
    inputs = Inputs()
    gold_doc = inputs.read(ROOT / 'manual-labeling/speaker-resolution-gold-v1/gold.json')
    gold = {x['source_id']: x for x in gold_doc['cases'] if x['book_id'] == 'reality-benders-1'}
    raw = {}
    # Existing local frozen analysis only. No providers or models are called.
    for book, work in [('reality-benders-2-full', 'full'), ('reality-benders-2', 'pilot')]:
        grouped = defaultdict(list)
        base = ROOT / 'training-work/character-rb2' / work
        for folder in sorted(base.glob('context*')):
            if not folder.is_dir() or folder.name == 'context-duplicate-checkpoints':
                continue
            for path in sorted(folder.glob('*.json')):
                document = inputs.read(path)
                for item in document['raw']['passages']:
                    sid, start, end = item['id'].rsplit('-', 2)
                    text = frozen_passage_text(document.get('rows', document.get('passages', [])),
                                               sid, int(start), int(end))
                    # Raw offsets are enough; exact source text is validated via
                    # the frozen normalized context before these hints are used.
                    grouped[sid].append(dict(item, start=int(start), end=int(end), text=text))
        raw[book] = grouped
    report = {'schema': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
              'policy': 'Listening proposals only; no human records or training acceptance changed.',
              'limitations': ['No fresh listening or acoustic change-point analysis.',
                              'Context predictions are evidence to check, not human judgments.',
                              'Candidate-board coverage only; omitted book audio is not evaluated.',
                              'Audio edges and audible voice/mood changes require listening.'],
              'counts': {}, 'findings': [], 'unverified_delivery_hints': []}
    for book in BOOKS:
        counts, findings = audit_book(book, inputs, gold, raw, report['unverified_delivery_hints'])
        report['counts'][book] = counts
        report['findings'].extend(findings)
    report['input_sha256'] = inputs.hashes
    output = args.output or ROOT / 'training-work/split-audit' / datetime.now().strftime('%Y%m%d-%H%M%S')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    write_html(report, output / 'index.html')
    published = None
    if args.publish_board:
        published = ROOT / 'training-work/character-rb2-full/fine-tune-all/split-audit' / output.name
        published.mkdir(parents=True, exist_ok=False)
        write_html(report, published / 'index.html', server_mode=True)
        (published / 'report.json').write_bytes((output / 'report.json').read_bytes())
    print(json.dumps({'output': str(output), 'published': str(published) if published else None,
                      'counts': report['counts']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
