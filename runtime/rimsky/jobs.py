from __future__ import annotations
import contextlib
import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tempfile
import time
import unicodedata
import wave
from collections import Counter, defaultdict
from functools import lru_cache
from itertools import chain, islice
from pathlib import Path
from .analysis import ANALYSIS_CONTRACT, INVALID_STREAK_LIMIT, MIXED_WARNING, SPEAKERS, TRANSITION_WARNING, DEGRADED_HOLD, DEGRADED_WARNING, UNRESOLVED_HOLD, UNRESOLVED_WARNING, accepted_speakers, alias_list, analyse_sources, cast_voices, common_words, evidence_ids, merge_character, registry, without_source_ranges
from .preparation import analysis_chapters, audible, default_chapters, speech_language, speech_sources
from .presentation_voice import presentation_decision
from .speaker_resolution import resolution_metrics, valid_character_label
from .store import HOLD_NOTICE, NEIGHBOUR_NOTICE, PRESENTATION_NOTICE, PREVIEW_NOTICE, speaker_review_holds, trusted_inputs
from .casting import ALIAS_COMPARISON_LIMIT, CASTING_SCHEMA, cast_summary, casting_pool, character_kind, compatible, contains, kind_totals, kind_votes, speech_volume, voice_details, voice_fields, voice_identities, voice_role
from .casting import KINDS, NOTICES, age_fit, conflict_graph, dialogue_contexts, gender_fit, gender_targets, resolve_kind
from .integrity import accepted_audio, read_audio, validate_coverage, validate_cues
from .packs import PackChangedError, bundled_pack, load_pack, pack_unchanged, require_context, resolve_binary, runtime_problem
from .cli_process import terminate_process_tree
from .util import ERROR_CODES, AppError, AnalysisCapacityError, EngineTransportError, GenerationCapError, SpeechHoldError, append_warning, atomic_write, bounded_text, digest, inside, now, require_id, uid, write_json

STDERR_TAIL = 3000
SILENCE = (.3, 24000)
from .narration_transitions import POLICY as TRANSITION_POLICY
SCENE_PAUSE = TRANSITION_POLICY['scene_seconds']
COMMA_PAUSE = .35
BACKEND_NOTICE = 'Held: the speech model reached its generation limit for this passage. Shorten or correct its spoken text; the text was not clipped.'
CAPABILITY_NOTICE = 'This model pack cannot control the planned pace; the desired performance plan was kept and this passage used the pack default pace.'
MIXED_EDIT_NOTICE = 'Held: this sentence mixes voices, so its edited spoken text cannot be split into parts. Restore the source text or correct each part.'
SHORTEST_SPEECH = (.02, 1.0)
LONGEST_SPEECH = (.8, 20.0)
RENDER_FAILED = 'A passage could not be synthesized after a retry. Open the marked passage to correct its spoken text, then resume.'
WORKER_FAILED = 'The speech worker stopped twice while rendering a passage. Resume to try again.'
IMPLAUSIBLE = 'Speech duration looks unusual for this passage. Use New take if it sounds wrong.'
ALIGNER_EXITED = 'Word alignment stopped because the local aligner exited; passage subtitles are kept.'
ALIGNMENT_UNAVAILABLE = 'Word alignment is unavailable; passage subtitles are retained.'
REFERENCE_FALLBACK = 'Requested delivery has no reference variant; neutral reference was used.'
DELIVERY_NOTICE = 'This model pack has no delivery control; {count} expressive passage(s) use neutral delivery.'
NOT_IN_REVIEW = 'Cast changes are possible only while the generation waits for cast review.'
PACE_NOTICE = 'This model pack has no pace control; {count} passage(s) keep their desired plan and use the pack default pace.'


def bind_voice_family_guidance(analysis, resources, pack=None):
    """Freeze the current merged family catalog once for a new generation."""
    if 'voice_family_guidance' in analysis or resources is None:
        return analysis
    from .voice_descriptors import casting_guidance
    available={voice.get('family',voice['id']) for voice in pack.get('voices',[])} if pack else None
    return {**analysis,'voice_family_guidance':casting_guidance(
        Path(resources).parent,available_families=available)}


def plausible(duration, text):
    letters = sum(character.isalnum() for character in text)
    return SHORTEST_SPEECH[0] * letters - SHORTEST_SPEECH[1] <= duration <= LONGEST_SPEECH[0] * letters + LONGEST_SPEECH[1]


def pause_seconds(text):
    value = text.strip()
    if re.fullmatch(r'(?:[＊*]\s*){3,}|⁂', value):
        return SCENE_PAUSE
    if value and re.fullmatch(r'[,，،\s]+', value):
        return COMMA_PAUSE
    return SILENCE[0]


def part_performance(part):
    context = part.get('context') or {}
    return {'presentation_channel': context.get('channel', 'direct'), 'content_register': context.get('content_register', 'prose'),
            'context_confidence': context.get('confidence', 'low'), 'context_evidence': context.get('evidence', [])}


def render_parts(segment):
    performance = segment.get('performance') or {}
    parts = performance.get('analysis_parts') or []
    if performance.get('internal_transition') and len(parts) > 1:
        if segment['spoken'] != segment['text']:
            return None
        return [{**part, 'performance': {**part_performance(part), **(part.get('performance') or {})}} for part in parts]
    return [{'speaker': segment['speaker'], 'delivery': segment['delivery'], 'text': segment['spoken'], 'performance': performance}]


def render_work_ids(generation):
    """Playback coverage and requested replacement work are separate scopes."""
    selected=generation['config'].get('render_scope')
    if selected is None:return None # Historical frozen scheduling semantics.
    if selected!='pending-parts-v1':raise AppError('Unknown narration work-scope policy.')
    return {s['id']for s in generation['segments']
        if s['status']not in ('ready','review')or s['status']=='ready'and not s.get('audio_hash')}


def name_semantics(spec):
    """Only lexical decisions/authority invalidate frozen book-name preparation."""
    return {key:{'name':row.get('name'),'stressed':row.get('stressed'),
        'human':row.get('source')=='listener',
        'protected':[{k:word.get(k)for k in ('start','end','printed','stressed')}
            for word in(row.get('series_listener_overlay')or{}).get('words',[])]}
        for key,row in(spec.get('names')or{}).items()}


def invalidate_name_preparation(spec):
    result=dict(spec)
    for key in ('proper_noun_preparation','source_names','source_name_receipts'):
        result.pop(key,None)
    if 'prepared_chapters'in result:result['prepared_chapters']={}
    # Source-bound automatic entries remain evidence until replaced; only
    # affected pending owners are re-prepared, never ready neighboring audio.
    return result


def merged_descriptor_holds(inputs, merges, cast, common):
    from .analysis import named, scene_of
    by_id = {character['id']: character for character in cast}
    records = []
    for entry in inputs:
        target = by_id.get(merges.get(entry.get('speaker')))
        if target is None or named(target, common) or not target.get('scopes') or scene_of(entry) in target['scopes']:
            continue
        records.append({'target_id': entry['source_id'], 'source_id': entry['source_id'], 'start': entry['start'], 'end': entry['start'] + entry['characters'],
                        'speaker': target['id'], 'primary_speaker': entry['speaker'], 'tier': 'descriptor', 'triggers': ['descriptor_identity'], 'status': 'review'})
    return records


def rename_speakers(db, gid, mapping):
    if not mapping:
        return
    for source, target in mapping.items():
        db.execute('UPDATE segments SET speaker=? WHERE generation_id=? AND speaker=?', (target, gid, source))
    for row in db.execute("SELECT id,performance FROM segments WHERE generation_id=? AND performance LIKE '%analysis_parts%'", (gid,)).fetchall():
        performance = json.loads(row['performance'])
        parts = performance.get('analysis_parts') or []
        if any(part.get('speaker') in mapping for part in parts):
            performance['analysis_parts'] = [{**part, 'speaker': mapping.get(part.get('speaker'), part.get('speaker'))} for part in parts]
            db.execute('UPDATE segments SET performance=? WHERE generation_id=? AND id=?', (json.dumps(performance, ensure_ascii=False), gid, row['id']))


def transition_warning(segment):
    performance = segment.get('performance') or {}
    if not performance.get('internal_transition'):
        return ''
    speakers = {part.get('speaker') for part in performance.get('analysis_parts') or []} - set(SPEAKERS)
    return MIXED_WARNING if len(speakers) > 1 else TRANSITION_WARNING


def concatenate(paths, destination, pauses=()):
    """Join part audio with silence; pauses are either (before, after) or one gap per junction plus both ends."""
    frames, params = [], None
    for path in paths:
        with wave.open(str(path), 'rb') as audio:
            current = audio.getparams()[:3]
            if params is not None and current != params:
                raise AppError('Speech parts of one sentence use different audio formats.')
            params = current
            frames.append(audio.readframes(audio.getnframes()))
    gaps = list(pauses)
    if len(gaps) == 2 and len(frames) != 1:
        gaps = [gaps[0], *([0] * (len(frames) - 1)), gaps[1]]
    gaps = (gaps + [0] * (len(frames) + 1))[:len(frames) + 1]
    gap = lambda seconds: bytes(params[0] * params[1] * round(seconds * params[2]))
    body = gap(gaps[0]) + b''.join(frame + gap(gaps[index + 1]) for index, frame in enumerate(frames))
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(params[0])
        audio.setsampwidth(params[1])
        audio.setframerate(params[2])
        audio.writeframes(body)
    atomic_write(destination, output.getvalue())
    return {**read_audio(destination), 'warning': '', 'reset_cues': True}


def silence(path, seconds=SILENCE[0]):
    rate = SILENCE[1]
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(bytes(2 * round(seconds * rate)))
    atomic_write(path, output.getvalue())
    return {**read_audio(path), 'warning': '', 'reset_cues': True}


def applied_delivery(pack, voice, delivery):
    from .speech_backend import is_higgs
    if is_higgs(pack):
        return delivery
    if pack['speech']['variant'] == 'clone':
        return delivery if delivery in voice.get('references', {}) else 'neutral'
    return delivery if pack['speech'].get('instruction_control', False) else 'neutral'


def synthesis_voice(pack, voice, delivery):
    if pack['speech']['variant'] != 'clone':
        return ['speaker', voice['speaker']]
    reference = voice['references'][delivery]
    root = Path(pack['_root'])
    relative = inside(root, reference['audio']).relative_to(root.resolve()).as_posix()
    return ['reference', pack['files'].get(relative, relative), reference.get('text', '')]


def render_key(pack, config, text, voice, delivery, language, take, controls=None):
    if not audible(text):
        seconds = pause_seconds(text)
        return digest({'schema': 3, 'silence': SILENCE}) if seconds == SILENCE[0] else digest({'schema': 4, 'silence': [seconds, SILENCE[1]]})
    from .speech_backend import is_higgs
    if is_higgs(pack):
        from .higgs_backend import synthesis_plan
        from .spoken_pronunciation import synthesis_text,cache_binding
        text,pronunciation = synthesis_text(text, config.get('_pronunciation'))
        return digest({'schema': 5, 'language': language,
                       'plan': synthesis_plan(pack, voice, text, delivery, controls, config.get('resolved_speech'), take),
                       **cache_binding(pronunciation)})
    return digest({'schema': 3, 'pack': pack['_fingerprint'], 'device': config['device'], 'text': text, 'voice': synthesis_voice(pack, voice, delivery),
                   'delivery': delivery, 'language': language, 'qwen_tts': '0.1.1', 'take': take, **({'controls': controls} if controls else {})})


def paced(pack):
    return pack['speech']['variant'] != 'clone' and pack['speech'].get('instruction_control', False)


def part_plan(part, segment):
    from .performance import performance_plan
    if part.get('performance', {}).get('schema'):
        return part['performance']
    return performance_plan({'text': part['text'], 'delivery': part['delivery'], 'speaker': part['speaker'], 'context': part.get('context') or {},
                             'kind': None, 'source_id': segment['source_id']})


def render_controls(pack, part, segment):
    plan = part_plan(part, segment)
    from .speech_backend import is_higgs
    if is_higgs(pack):
        if plan.get('higgs_direction'):
            return {'direction':plan['higgs_direction'],
                    'character_profile':plan.get('character_profile',{}),
                    'voice_signature':plan.get('voice_signature',{}),
                    'pace':plan.get('pace','normal'),'ending_pace':plan.get('ending_pace','normal')},False
        return {key: value for key, value in (('mood', plan.get('assigned_mood') or part['delivery']),
                ('pace', plan.get('pace', 'normal')), ('ending_pace', plan.get('ending_pace', 'normal'))) if value}, False
    wanted = {key: value for key, value in (('pace', plan.get('pace')), ('ending_pace', plan.get('ending_pace'))) if value and value != 'normal'}
    return (wanted if paced(pack) else {}), bool(wanted) and not paced(pack)


def segment_pauses(segment):
    if not audible(segment['spoken']):
        return []
    performance = segment.get('performance') or {}
    values = [performance.get('pause_before_seconds') or 0, performance.get('pause_after_seconds') or 0]
    return [round(float(value), 3) for value in values] if any(values) else []


def segment_timeline(segment, parts):
    """Silence before, between and after the rendered parts of one segment, in seconds."""
    if not audible(segment['spoken']):
        return []
    outer = segment_pauses(segment) or [0.0, 0.0]
    if len(parts) == 1:
        return outer if any(outer) else []
    plans = [part_plan(part, segment) for part in parts]
    inner = [max(plans[index].get('pause_after_seconds') or 0, plans[index + 1].get('pause_before_seconds') or 0) for index in range(len(parts) - 1)]
    gaps = [outer[0], *inner, max(outer[1], plans[-1].get('pause_after_seconds') or 0)]
    gaps = [round(float(value), 3) for value in gaps]
    return gaps if any(gaps) else []


def legacy_language(book):
    code = (book.get('language') or '').lower()
    return 'Russian' if code.startswith('ru') else 'English' if code.startswith('en') else 'Auto'


def legacy_key(config, book, segment, voice_id):
    return digest({'schema': 2, 'pack': config['pack_fingerprint'], 'device': config['device'], 'text': segment['spoken'], 'voice': voice_id,
                   'delivery': segment['delivery'], 'language': legacy_language(book), 'qwen_tts': '0.1.1'})


def segment_voice(segment, assigned, narrators):
    if segment['speaker'] in ('narrator', 'unknown'):
        return narrators.get(segment['chapter_id']) or narrators.get('') or assigned.get('narrator')
    return assigned.get(segment['speaker'])


def routed_voice(pack,analysis,assigned,part,segment):
    voices={v['id']:v for v in pack['voices']};narrators=analysis.get('narrator_voices') or {}
    frame=(analysis.get('narrative_frames') or {}).get('by_source',{}).get(segment['source_id'])
    if frame and frame.get('register')=='mechanical':
        from .narrative_frames import requested_voice
        # A checked block route wins over sentence-level presentation guesses,
        # character proposals and a chapter's normal narration register.
        selected=voices[requested_voice(frame,pack['voices'],None)]
        return selected,'',False,selected
    base=voices.get(segment_voice({**segment,'speaker':part['speaker']},assigned,narrators))
    if base is None:raise AppError('A speech passage has no frozen character voice. Its identity was not replaced with narration.')
    if part['speaker']=='narrator' and segment['chapter_id'] not in narrators and '' not in narrators:
        from .narrative_frames import requested_voice
        base=voices[requested_voice(frame,pack['voices'],base['id'])]
    chosen,warning,review=presentation_decision(part.get('performance') or {},pack['voices'],base['id'],pack.get('presentation_voices'))
    return voices.get(chosen,base),warning,review,base


def analysis_provider(config, pack):
    from .packs import resolve_character_analysis
    chosen = resolve_character_analysis(config.get('character_analysis', 'local'))
    return chosen if chosen in ('codex_cli', 'claude_cli') else 'local'


def unresolved(generation):
    selected = set(generation['config'].get('chapters', []))
    segments = [segment for segment in generation['segments'] if not selected or segment['chapter_id'] in selected]
    if any(segment['status'] == 'review' for segment in segments):
        return True
    return bool(speaker_review_holds(segments, generation['analysis'].get('speaker_resolution', []), generation['analysis'].get('speaker_verdicts'))[0])


def clipped(text, limit=2000):
    head = limit // 4
    return text if len(text) <= limit else text[:head] + ' … ' + text[len(text) - limit + head + 3:]


def cast_member(cast, cid):
    found = next((character for character in cast if character['id'] == cid and cid != 'narrator'), None)
    if found is None:
        raise AppError('Unknown speaker.')
    return found


def cast_flags(cast, pack, inputs):
    identities = voice_identities(pack['voices'], pack['speech']['variant'], pack['files'], pack['_root'])
    voices = {voice['id']: voice for voice in pack['voices']}
    counts = speech_volume(inputs)
    usage = Counter(identities[character['voice_id']] for character in cast
                    if character['id'] != 'narrator' and character['id'] in counts and character.get('voice_id') in identities)
    return [character if character['id'] == 'narrator' or character.get('voice_id') not in voices else
            {**character, 'voice_reused': usage[identities[character['voice_id']]] - (character['id'] in counts) > 0,
             'voice_compatible': compatible(character, voices[character['voice_id']])} for character in cast]


LEGACY_ALIAS = {code: re.compile(re.escape(NOTICES[code]).replace(re.escape('{count}'), r'(\d+)').replace(re.escape('{limit}'), r'\d+'))
                for code in ('alias_unmerged_capacity', 'alias_skipped_limit')}


def previous_notices(analysis):
    if 'casting_notices' in analysis:
        return {notice['code']: notice.get('count', 0) for notice in analysis['casting_notices'] if isinstance(notice, dict) and 'code' in notice}
    return {code: int(match[1]) for warning in analysis.get('casting_warnings', []) if isinstance(warning, str)
            for code, pattern in LEGACY_ALIAS.items() if (match := pattern.fullmatch(warning))}


def recast(cast, cid, pack, inputs, narrators=None):
    voices, characters, narrators = pack['voices'], [character for character in cast if character['id'] != 'narrator'], narrators or {}
    keys = voice_identities(voices, pack['speech']['variant'], pack['files'], pack['_root'])
    character = next(item for item in characters if item['id'] == cid)
    pools = {item['id']: casting_pool(item, voices) for item in characters}
    counts = speech_volume(inputs)
    target = gender_targets(characters, pools, counts).get(cid)
    neighbours = conflict_graph(dialogue_contexts(inputs), characters)[cid]
    placed = {item['id']: keys[item['voice_id']] for item in characters if item['id'] != cid and item.get('voice_id') in keys}
    narrator = next((item.get('voice_id') for item in cast if item['id'] == 'narrator'), None)
    narration = {keys.get(narrators.get(chapter) or narrators.get('') or narrator)
                 for chapter in {item['chapter_id'] for item in inputs if item['speaker'] == cid} or {''}}
    def rank(voice):
        key, gender = keys[voice['id']], gender_fit(character, voice, target)
        return (gender == 2, sum(weight for other, weight in neighbours.items() if placed.get(other) == key), key in narration, gender,
                age_fit(character, voice), sum(counts[other] for other, used in placed.items() if used == key), voice['id'])
    return min(pools[cid], key=rank)


def casting_summary(pack, config, segments, cast, analysis, unmerged=None, skipped=None):
    previous = previous_notices(analysis)
    degraded = {item.get('source') for item in analysis.get('degraded', []) if isinstance(item, dict)}
    summary = cast_summary(cast, pack['voices'], analysis.get('casting_inputs') or segments, pack['files'], pack['_root'], pack['speech']['variant'],
                           rendered_chapters=config['chapters'],
                           unmerged=previous.get('alias_unmerged_capacity', 0) if unmerged is None else unmerged,
                           skipped=previous.get('alias_skipped_limit', 0) if skipped is None else skipped,
                           degraded=sum(segment['id'] in degraded and segment['speaker'] == 'unknown' for segment in segments),
                           degraded_sources=degraded, narrators=analysis.get('narrator_voices') or {})
    expressive = sum(segment['delivery'] != 'neutral' for segment in segments)
    if expressive and pack['speech']['variant'] != 'clone' and not pack['speech'].get('instruction_control', False):
        summary['notices'].append({'code': 'delivery_not_supported', 'count': expressive, 'params': {'count': expressive}})
        summary['warnings'].append(DELIVERY_NOTICE.format(count=expressive))
    paced_parts = sum(any(render_controls(pack, part, segment)[1] for part in (render_parts(segment) or ())) for segment in segments)
    if paced_parts:
        summary['notices'].append({'code': 'pace_not_supported', 'count': paced_parts, 'params': {'count': paced_parts}})
        summary['warnings'].append(PACE_NOTICE.format(count=paced_parts))
    return {'casting_conflicts': summary['conflicts'], 'casting_warnings': summary['warnings'], 'casting_notices': summary['notices']}


class Engine:
    def __init__(self, binaries: Path, component='infer'):
        name = 'rimsky-align' if component == 'align' else 'rimsky-infer'
        script = 'align_worker.py' if component == 'align' else 'worker.py'
        bundled=resolve_binary(name,binaries)
        command=[bundled] if bundled else [sys.executable,str(Path(__file__).parents[1]/script)]
        # Windows cannot execute a shebang script directly. Development and
        # protocol tests may provide an extensionless Python worker in the same
        # shape as the bundled Unix worker, so route that explicit case through
        # the active interpreter. Packaged Windows workers remain native .exe
        # files and keep the normal direct-launch path.
        if bundled and os.name == 'nt' and Path(bundled).suffix.lower() != '.exe':
            try:
                shebang = Path(bundled).read_bytes()[:2] == b'#!'
            except OSError:
                shebang = False
            if shebang:
                command=[sys.executable,bundled]
        if getattr(sys,'frozen',False) and not bundled:
            raise AppError('Bundled inference runtime is missing.')
        if problem:=runtime_problem(binaries,resolve_binary('rimsky-infer',binaries)):
            raise AppError(problem)
        process_group = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
                         else {'start_new_session': True})
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',bufsize=1,
                                      env={**os.environ,'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'}, **process_group)
        self.output=b''
        self.responses=queue.Queue()
        self.close_lock=threading.Lock()
        self.reader=threading.Thread(target=self._reader,daemon=True)
        self.reader.start()
        self.drain=threading.Thread(target=self._drain,daemon=True)
        self.drain.start()

    def _drain(self):
        try:
            while chunk := self.process.stderr.buffer.read1(4096):
                self.output=(self.output+chunk)[-STDERR_TAIL:]
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(OSError, ValueError):
                self.process.stderr.close()

    def _exited(self, message):
        self.close()
        self.drain.join(timeout=1)
        output=' '.join(self.output.decode('utf-8','replace').split())
        return EngineTransportError(f'{message} Process output: {output}' if output else message, 'exit')

    def _reader(self):
        try:
            while line := self.process.stdout.readline(8*1024*1024+1):
                if len(line)>8*1024*1024:
                    self.responses.put({'protocol_error': 'Oversized inference response.'})
                    break
                try:
                    result = json.loads(line)
                    self.responses.put(result if isinstance(result, dict) else {'protocol_error':'Non-object inference response.'})
                except ValueError:
                    continue
        except (OSError, ValueError):
            pass
        finally:
            self.process.stdout.close()
            self.responses.put(None)

    def call(self, method, data, stop, timeout=1800):
        if stop.is_set():
            self.close()
            raise AppError('Preparation paused.')
        if self.process.poll() is not None:
            raise self._exited('Local inference process exited.')
        deadline = time.monotonic() + timeout
        rid = uid()
        message = json.dumps({'id': rid, 'method': method, 'data': data}, ensure_ascii=False) + '\n'
        if len(message.encode('utf-8')) > 4 * 1024 * 1024:
            raise AppError('Local inference request exceeds the 4 MiB limit.')
        sent = queue.Queue(maxsize=1)
        def send():
            try:
                self.process.stdin.write(message)
                self.process.stdin.flush()
                sent.put(None)
            except (OSError, ValueError) as error:
                sent.put(error)
        sender = threading.Thread(target=send, name='rimsky-inference-send', daemon=True)
        sender.start()
        try:
            while True:
                self._check_call(stop, deadline, timeout)
                try:
                    error = sent.get(timeout=min(.2, max(.001, deadline-time.monotonic())))
                except queue.Empty:
                    continue
                if error is not None:
                    raise self._exited('Local inference process is unavailable.') from error
                break
        finally:
            sender.join(timeout=1)
        while True:
            self._check_call(stop, deadline, timeout)
            try:
                result=self.responses.get(timeout=min(.2,max(.001,deadline-time.monotonic())))
            except queue.Empty:
                continue
            if result is None:
                raise self._exited('Local inference process exited before completing its request.')
            envelope = set(result)
            coded = envelope == {'id', 'error', 'error_code'} and result.get('error_code') in ERROR_CODES
            if result.get('id') != rid or (envelope not in ({'id','result'},{'id','error'}) and not coded):
                self.close()
                raise EngineTransportError('Invalid or uncorrelated local inference response.', 'protocol')
            if 'error' in result:
                if not isinstance(result['error'],str):
                    self.close()
                    raise EngineTransportError('Invalid local inference error response.', 'protocol')
                if coded:
                    raise ERROR_CODES[result['error_code']](result['error'])
                raise AppError(result['error'])
            return result['result']
    def _check_call(self, stop, deadline, timeout):
        if stop.is_set():
            self.close()
            raise AppError('Preparation paused.')
        if time.monotonic() >= deadline:
            self.close()
            raise EngineTransportError(f'Local inference exceeded its {timeout}-second operation limit.', 'timeout')

    def close(self):
        with self.close_lock:
            if self.process.poll() is None:
                terminate_process_tree(self.process)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass
            if threading.current_thread() is not self.reader:
                self.reader.join(timeout=1)


def base_segments(book, selected):
    return list(speech_sources(book, selected))



def analysis_order(segments, bases, selected):
    ranges = {}
    for source in bases:
        if source['chapter_id'] in selected:
            ranges.setdefault(source['source_id'], []).append(source)
    ordered = {}
    for segment in segments:
        source = next((base for base in ranges.get(segment['source_id'], [])
                       if base['start'] <= segment['start'] < segment['end'] <= base['end']), None)
        if source is None or source['chapter_id'] != segment['chapter_id']:
            return None
        ordered.setdefault(source['ordinal'], []).append(segment)
    if any(len(parts) > 99 for parts in ordered.values()):
        return None
    return [(ordinal * 100 + index, part['id']) for ordinal, parts in ordered.items()
            for index, part in enumerate(sorted(parts, key=lambda value: value['start']), 1)]


def normalize_analysis_order(store, generation_id, bases, selected):
    updates = analysis_order(store.generation(generation_id)['segments'], bases, selected)
    if updates is None:
        raise AppError('Unfinished analysis contains an invalid source range.')
    with store.lock, store.db:
        store.db.executemany('UPDATE segments SET ordinal=? WHERE generation_id=? AND id=?', [(order, generation_id, sid) for order, sid in updates])


def placeholder_segments(store, generation_id, bases, selected):
    with store.lock, store.db:
        if store.db.execute("SELECT 1 FROM segments WHERE generation_id=? AND (status='ready' OR audio_hash IS NOT NULL) LIMIT 1", (generation_id,)).fetchone():
            raise AppError('Unfinished analysis contains an invalid source range.')
        store.db.execute('DELETE FROM segments WHERE generation_id=?', (generation_id,))
        store.db.executemany('INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken) VALUES(?,?,?,?,?,?,?,?,?)',
                             [(generation_id, source['id'], source['ordinal'] * 100 + 1, source['chapter_id'], source['source_id'],
                               source['start'], source['end'], source['text'], source['text']) for source in bases if source['chapter_id'] in selected])


ALIAS_PHRASES = 31
EXPANDED_WORDS = 6
VARIANT_WORDS = 3
ENDINGS = tuple(sorted(('ого его ому ему ыми ими ией ием ьей ьем еем еей аем аей оем ой ей ий ый ай ая яя ое ее ые ие ую юю ым им ом ем ою ею '
                        'аю ае аи оя еи ия ии ию ья ье ьи ью ея их ых а я у ю е и ы ь й').split(), key=len, reverse=True))
FLEETING = ('ел', 'ев', 'ек', 'ок', 'ец')
PATRONYMIC = ('ич', 'овн', 'евн', 'ичн')
VOWELS = 'аеиоуыэюя'
NAME_FORMS = ('александр:саша,шура,саня алексей:леша анна:аня мария:маша иван:ваня дмитрий:дима михаил:миша николай:коля '
              'екатерина:катя елена:лена ольга:оля наталья:наташа сергей:сережа владимир:володя,вова евгений:женя татьяна:таня '
              'павел:паша петр:петя кирилл:киря константин:костя анастасия:настя юлия:юля ксения:ксюша григорий:гриша '
              'василий:вася федор:федя борис:боря виктор:витя андрей:андрюша ирина:ира светлана:света людмила:люда галина:галя '
              'валентина:валя варвара:варя елизавета:лиза дарья:даша яков:яша георгий:жора,гоша станислав:стас вячеслав:слава '
              'ярослав:слава ростислав:слава леонид:леня эдуард:эдик вадим:вадик роман:рома тимофей:тима глеб:глебушка олег:олежка')


def subphrases(phrase):
    sizes = range(1, len(phrase) + 1) if len(phrase) <= EXPANDED_WORDS else (len(phrase),)
    return (phrase[start:start + size] for size in sizes for start in range(len(phrase) - size + 1))


def name_tokens(text):
    text = unicodedata.normalize('NFD', unicodedata.normalize('NFKC', text).casefold().replace('ё', 'е'))
    return tuple(re.findall(r'\w+', unicodedata.normalize('NFC', ''.join(char for char in text if char == '\u0306' or not unicodedata.combining(char)))))


def name_stems(token):
    if len(token) < 4 or not re.fullmatch('[а-я]+', token):
        return ()
    return tuple(dict.fromkeys(token[:-len(ending)] for ending in ENDINGS if token.endswith(ending) and len(token) - len(ending) >= 3))


def squeezed(stem):
    if len(stem) < 3 or stem[-2:] not in FLEETING or stem[-3] in 'ьъй' or not re.fullmatch('[а-я]+', stem):
        return stem
    if stem[-3] in VOWELS:
        return f'{stem[:-2]}й{stem[-1]}' if stem[-2:] == 'ец' else stem
    soft = stem[-2] == 'е' and (stem[-3] == 'л' or stem[-1] == 'к' and stem[-3] not in 'жчшщ')
    return stem[:-2] + ('ь' if soft else '') + stem[-1]


def declined(name):
    if name[-1] not in 'ая':
        return (name, *(name + ending for ending in ('а', 'у', 'ом', 'е')))
    soft = name[-1] == 'я' or name[-2] in 'жчшщ'
    return (name, *(name[:-1] + ending for ending in ('и' if soft or name[-2] in 'гкх' else 'ы', 'е', 'ю' if name[-1] == 'я' else 'у',
                                                      'ей' if soft else 'ой', 'ею' if soft else 'ою')))


def name_table(text):
    table = defaultdict(set)
    for full, short in (entry.split(':') for entry in text.split()):
        for name in short.split(','):
            for key in declined(name):
                table[key].add(full)
    return {key: tuple(sorted(names)) for key, names in table.items()}


DIMINUTIVES = name_table(NAME_FORMS)


@lru_cache(maxsize=8192)
def name_forms(token, proper=False):
    stems = name_stems(token) or (token,)
    return tuple(dict.fromkeys([squeezed(stems[0]), token, *stems, *(name_forms(name)[0] for name in DIMINUTIVES.get(token, ()) if proper)]))


@lru_cache(maxsize=8192)
def name_prefixes(token):
    base = (name_stems(token) or (token,))[0]
    if not base.isalpha() or base.endswith(PATRONYMIC):
        return ()
    return tuple(base[:size] for size in range(max(4, len(base) - 3), len(base)))


def canonical_name(phrase):
    return tuple(name_forms(token)[0] for token in phrase)


def phrase_keys(phrase, inner=False, proper=False, prefixes=False):
    keys = {}
    for part in subphrases(phrase) if inner else (phrase,):
        canonical = canonical_name(part)
        keys[canonical] = None
        for index, token in enumerate(part if len(part) <= VARIANT_WORDS else ()):
            for other in name_forms(token, proper)[1:] + (name_prefixes(token) if prefixes else ()):
                keys[canonical[:index] + (other,) + canonical[index + 1:]] = None
    return keys


def candidate_index(owners, inside, order):
    ranked = sorted((key for key in owners if len(inside[key]) > 1),
                    key=lambda key: (len(inside[key]), -len(key), min(map(order.get, owners[key])), key))
    owned, within = defaultdict(list), defaultdict(set)
    for index, key in enumerate(ranked):
        for cid in owners[key]:
            owned[cid].append(index)
        for cid in inside[key]:
            within[cid].add(index)
    return ranked, owned, within, inside


def alias_candidates(characters, speech=None):
    order = {character['id']: index for index, character in enumerate(characters)}
    owners, inside, loose = defaultdict(set), defaultdict(set), defaultdict(set)
    for character in characters:
        names = defaultdict(bool)
        for label, primary in [(character.get('name', ''), True), *((item, False) for item in character.get('aliases', []))]:
            if not valid_character_label(label, registered=primary):
                continue
            names[name_tokens(label)] |= any(map(str.isupper, label))
        for phrase in [phrase for phrase in names if phrase][:ALIAS_PHRASES]:
            for key in phrase_keys(phrase, False, names[phrase]):
                owners[key].add(character['id'])
            for key in phrase_keys(phrase, True, names[phrase]):
                inside[key].add(character['id'])
            for key in phrase_keys(phrase, True, names[phrase], True):
                loose[key].add(character['id'])
    speaking = {cid for cid in order if speech is None or speech.get(cid, 0) > 0}
    def shared(one, two, index, tier):
        _, owned, within, _ = tier
        return any(other < index and other in within[two] for other in owned[one]) or any(
            other < index and other in within[one] for other in owned[two])
    def pairs(tier, strict=None):
        ranked, members_of = tier[0], tier[3]
        for levels in ((0, 1), (2,)):
            for index, key in enumerate(ranked):
                members = sorted(members_of[key], key=order.get)
                talking = sum(cid in speaking for cid in members)
                possible = (talking > 1, 0 < talking < len(members), len(members) - talking > 1)
                for level in levels:
                    if possible[level]:
                        yield from ((one, two) for at, one in enumerate(members) for two in members[at + 1:]
                                    if 2 - (one in speaking) - (two in speaking) == level and (one in owners[key] or two in owners[key])
                                    and not shared(one, two, index, tier) and not (strict and shared(one, two, len(strict[0]), strict)))
    strict = candidate_index(owners, inside, order)
    yield from pairs(strict)
    yield from pairs(candidate_index(owners, loose, order), strict)


def alias_decision(result, evidence):
    if not isinstance(result, dict) or set(result) != {'same', 'evidence'} or not isinstance(result['same'], bool):
        raise AppError('Invalid alias decision.')
    if not result['same']:
        return False
    first, second = (set(passages) for passages in evidence.values())
    if not evidence_ids(result['evidence'], first | second, required=True):
        raise AppError('Alias merge needs valid source evidence.')
    if not set(result['evidence']) & first or not set(result['evidence']) & second:
        raise AppError('Alias merge evidence must cite passages of both characters.')
    return True


def merge_aliases(characters, merges, common=frozenset()):
    known = {character['id']: dict(character) for character in characters}
    for cid, root in merges.items():
        known[root] = merge_character(known[root], known[cid], common)
    return [character for cid, character in known.items() if cid not in merges]


def saved_decisions(saved, known):
    items = saved.get('decisions') if isinstance(saved, dict) else None
    return {frozenset(item[:2]): item[2] for item in (items if isinstance(items, list) else [])
            if isinstance(item, list) and len(item) == 3 and all(isinstance(cid, str) and cid in known for cid in item[:2])
            and item[0] != item[1] and (item[2] is None or isinstance(item[2], bool))}


class Jobs:
    def __init__(self,store,binaries: Path,engine_factory=Engine,verified=None,resources=None):
        from .spoken_pronunciation import set_resolver
        set_resolver(lambda texts,spec:self._call('pronounce',{'texts':texts,'pronunciation':spec})['texts'])
        self.store=store
        self.binaries=binaries
        self.engine_factory=engine_factory
        self.verified=verified
        self.resources=resources
        self.active=None
        self.stop=threading.Event()
        self.lock=threading.RLock()
        self.thread=None
        self.engine=None
        self.closed=False
        self.listener_queue=[]

    def listener_stress(self, report):
        """Refresh current book audio after an explicit durable listener edit.

        This is a requested correction, independent of optional automatic
        recognition/retake mode. Historical generations are not rewritten.
        """
        from .listener_stress import snapshot, apply
        with self.lock:
            book=self.store.book(report['book_id'])
            # Old listening positions and legacy creation order are heard
            # evidence, not additional replacement targets. An active owner
            # participates only when it is this canonical current output.
            current_id=self.store.current_generation_id(book['id'])
            current_ids={current_id} if current_id else set()
            count=0
            for gid in current_ids- {None}:
                generation=self.store.generation(gid)
                if generation['config'].get('engine')=='higgs_audio_tts_q8_external_preview':continue
                job_id=digest([report['id'],gid])[:24]
                if self.store.rows('SELECT id FROM listener_correction_jobs WHERE id=?',(job_id,)):continue
                frozen=snapshot(self.store,book,generation['cast'])
                blocks={b['id']:b['text'] for c in book['chapters'] for b in c['blocks']}
                targets=[]
                for segment in generation['segments']:
                    native,evidence=apply(segment['spoken'],frozen,source_id=segment['source_id'],
                        source_text=blocks[segment['source_id']],source_start=segment['start'],source_end=segment['end'])
                    previous=(segment.get('performance') or {}).get('listener_stress') or {}
                    reported_family=(report.get('character_state') or {}).get('status')=='updated'
                    from .name_stress import name_spans,ACUTE
                    family_match=bool(name_spans(segment['spoken'].replace(ACUTE,''),
                        (report.get('character_state') or {}).get('pronunciations') or {},implicit_single=True)) if reported_family else False
                    explicit_target=family_match or (
                        segment['source_id']==report['source_id'] and type(report.get('text_offset')) is int
                        and segment['start']<=report['text_offset']<segment['end'])
                    if explicit_target or evidence['edits'] and previous.get('sha256')!=evidence.get('sha256'):
                        targets.append((segment,evidence))
                if not targets:continue
                analysis={**generation['analysis'],'pronunciation':{
                    **generation['analysis'].get('pronunciation',{}),'listener_stress':frozen}}
                if (report.get('character_state')or{}).get('status')=='updated':
                    old_families=(generation['analysis'].get('pronunciation',{}).get('listener_stress')or{}).get('names',{})
                    families=(report.get('character_state')or{}).get('pronunciations')or{}
                    if any(old_families.get(key)!=frozen.get('names',{}).get(key)for key in families):
                        analysis['pronunciation']=invalidate_name_preparation(analysis['pronunciation'])
                takes=dict(analysis.get('takes') or {})
                for segment,_ in targets:takes[segment['id']]=takes.get(segment['id'],0)+1
                analysis['takes']=takes
                with self.store.lock,self.store.db:
                    from .current_audiobook import capture_parts
                    capture_parts(self.store,generation,[s['id']for s,_ in targets])
                    self.store.db.execute('UPDATE generations SET analysis=?,updated=? WHERE id=?',
                        (json.dumps(analysis,ensure_ascii=False),now(),gid))
                    for segment,evidence in targets:
                        performance={**(segment.get('performance') or {}),'listener_stress':evidence}
                        self.store.db.execute("UPDATE segments SET status=CASE WHEN status='ready' THEN 'pending' ELSE status END,audio_hash=NULL,duration=0,cues='[]',performance=? WHERE generation_id=? AND id=?",
                            (json.dumps(performance,ensure_ascii=False),gid,segment['id']))
                    self.store.db.execute("UPDATE generations SET status=CASE WHEN status='ready' THEN 'paused' ELSE status END WHERE id=?",(gid,))
                    self.store.db.execute('INSERT INTO listener_correction_jobs(id,report_id,book_id,generation_id,segment_ids,status,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                        (job_id,report['id'],book['id'],gid,json.dumps([s['id'] for s,_ in targets]),'queued',now(),now()))
                count+=len(targets)
                if gid not in self.listener_queue:self.listener_queue.append(gid)
                if self.active==gid:self.stop.set()
            if not self.active:self._start_listener_queue()
            states=self.store.rows("SELECT status,error,segment_ids FROM listener_correction_jobs WHERE report_id=? AND id NOT LIKE 'enqueue-failure-%'",(report['id'],))
            held=next((state for state in states if state['status']=='held'),None)
            pending=next((state['status'] for state in states if state['status'] in ('queued','running','paused')),None)
            return {'id':report['id'],'status':'held' if held else pending or ('queued' if count else 'applied'),
                **({'error':held['error']} if held else {}),
                'targets':count or sum(len(json.loads(state['segment_ids'])) for state in states),
                'queued':count,'character':(report.get('character_state') or {}).get('canonical_name')}

    def _start_listener_queue(self):
        if self.closed or self.active:return
        for row in self.store.rows("SELECT generation_id FROM listener_correction_jobs WHERE status IN ('queued','running') ORDER BY created,id"):
            if row['generation_id'] not in self.listener_queue:self.listener_queue.append(row['generation_id'])
        if not self.listener_queue:return
        gid=self.listener_queue.pop(0)
        try:
            self.start(gid)
            # start may return a legitimate cast-review hold instead of work.
            if self.active==gid:
                self.store.execute("UPDATE listener_correction_jobs SET status='running',updated=? WHERE generation_id=? AND status IN ('queued','running')",(now(),gid))
            elif self.store.generation(gid)['status']!='ready':
                self.store.execute("UPDATE listener_correction_jobs SET status='held',error=?,updated=? WHERE generation_id=? AND status IN ('queued','running')",
                    ('The correction is waiting for narration preparation or cast review.',now(),gid))
        except Exception as error:
            self.store.status(gid,'failed',str(error))
            self.store.execute("UPDATE listener_correction_jobs SET status='held',error=?,updated=? WHERE generation_id=? AND status IN ('queued','running')",(str(error),now(),gid))

    def resume_listener_corrections(self):
        """Explicit human repair intent survives a runtime restart independently."""
        with self.lock:
            self.store.execute("UPDATE listener_correction_jobs SET status='queued',updated=? WHERE status='running'",(now(),))
            self._start_listener_queue()
            return self.active

    def resume_interrupted(self):
        """Resume the one job that was active when the desktop runtime stopped.

        An explicit user pause clears this intent.  A graceful application quit
        leaves it set, as does a crash, so committed analysis and audio are reused
        on the next launch without installing an OS background service.
        """
        if self.resume_listener_corrections():return self.store.generation(self.active)
        rows = self.store.rows(
            "SELECT id FROM generations WHERE resume_on_launch=1 AND status='paused' ORDER BY updated DESC,rowid DESC LIMIT 1"
        )
        if not rows:
            return None
        gid = rows[0]['id']
        try:
            return self.start(gid)
        except Exception as error:
            if self.closed:
                return None
            with self.store.lock, self.store.db:
                self.store.db.execute(
                    "UPDATE generations SET resume_on_launch=0,error=?,updated=? WHERE id=?",
                    (f'Automatic resume could not start: {error}'[:4000], now(), gid),
                )
            return None

    def start(self,gid,approve=False):
        with self.lock:
            if self.closed:
                raise AppError('Local preparation is closing; new work cannot start.')
            if self.active:
                if self.active==gid:
                    return self.store.generation(gid)
                raise AppError('Another book is being prepared. Pause it before starting this generation.')
            generation=self.store.generation(gid)
            analysis=generation['analysis']
            waiting=bool(analysis.get('complete')) and analysis.get('approved') is False
            if approve and (not waiting or generation['status']!='review'):
                raise AppError(NOT_IN_REVIEW)
            if generation['status']=='ready':
                return generation
            if waiting and not approve:
                if generation['status']!='review':
                    self.store.status(gid,'review')
                return self.store.generation(gid)
            pack=self._pack(generation['config'])
            if not analysis.get('complete'):
                require_context(pack, analysis_provider(generation['config'], pack))
            device=self.store.settings()['device']
            with self.store.lock, self.store.db:
                self.store.db.execute('UPDATE generations SET resume_on_launch=0 WHERE resume_on_launch!=0')
                self.store.db.execute('UPDATE generations SET resume_on_launch=1 WHERE id=?',(gid,))
                self.store.db.execute("UPDATE listener_correction_jobs SET status='running',updated=?,error='' WHERE generation_id=? AND status IN ('held','paused','queued') AND id NOT LIKE 'enqueue-failure-%'",(now(),gid))
                if generation['config'].get('device')!=device:
                    self.store.db.execute('UPDATE generations SET config=? WHERE id=?',(json.dumps({**generation['config'],'device':device},ensure_ascii=False),gid))
                if approve:
                    reviewed_cast = [{key: value for key, value in character.items() if key != 'name_review'}
                                     for character in generation['cast']]
                    records = [record for record in analysis.get('speaker_resolution', [])
                               if 'function_word_name' not in record.get('triggers', [])]
                    approved = {**{key:value for key,value in analysis.items() if key!='progress'},
                                'approved':True, 'speaker_resolution': records}
                    self.store.db.execute('UPDATE generations SET analysis=?,cast=? WHERE id=?',
                                          (json.dumps(approved), json.dumps(reviewed_cast, ensure_ascii=False), gid))
            self.store.status(gid, 'queued')
            self.stop.clear()
            self.active = gid
            try:
                self.thread = threading.Thread(target=self._run, args=(gid,), daemon=True)
                self.thread.start()
            except Exception as error:
                self.active = None
                self.thread = None
                self.stop.set()
                with self.store.lock, self.store.db:
                    self.store.db.execute(
                        "UPDATE generations SET status='failed',error=?,updated=?,resume_on_launch=0 WHERE id=?",
                        (f'Preparation could not start: {error}'[:4000], now(), gid),
                    )
                raise AppError('Local preparation could not start. Resume to retry.') from error
            return self.store.generation(gid)

    def start_narration(self, gid):
        return self.start(gid, approve=True)

    def _review(self, gid, change):
        with self.lock:
            generation = self.store.generation(gid)
            if self.active == gid or generation['status'] != 'review' or generation['analysis'].get('approved') is not False:
                raise AppError(NOT_IN_REVIEW)
            pack = self._pack(generation['config'])
            cast, analysis, speakers = change(generation, pack)
            cast = cast_flags(cast, pack, analysis.get('casting_inputs') or generation['segments'])
            analysis = {**analysis, **casting_summary(pack, generation['config'], generation['segments'], cast, analysis)}
            if name_semantics(analysis.get('pronunciation')or{})!=name_semantics(generation['analysis'].get('pronunciation')or{}):
                analysis={**analysis,'pronunciation':invalidate_name_preparation(analysis.get('pronunciation')or{})}
            changed=[]
            if generation['config'].get('render_scope')=='pending-parts-v1':
                from .name_stress import name_spans,ACUTE
                before={c['id']:c.get('voice_id')for c in generation['cast']}
                after={c['id']:c.get('voice_id')for c in cast}
                old_names=(generation['analysis'].get('pronunciation')or{}).get('names',{})
                new_names=(analysis.get('pronunciation')or{}).get('names',{})
                stresses={cid:row['stressed']for cid,row in new_names.items()
                    if row.get('stressed')!=(old_names.get(cid)or{}).get('stressed')}
                for segment in generation['segments']:
                    if segment['status']!='ready' or not segment['audio_hash']:continue
                    parts=render_parts(segment)or[]
                    voice_changed=False
                    for part in parts:
                        new_part={**part,'speaker':speakers.get(part['speaker'],part['speaker'])}
                        new_segment={**segment,'speaker':speakers.get(segment['speaker'],segment['speaker'])}
                        frozen_controls=part.get('performance')or{}
                        voice_changed|=new_part['speaker']!=part['speaker']and bool(frozen_controls.get('higgs_direction'))
                        try:
                            old_voice=routed_voice(pack,generation['analysis'],before,part,segment)[0]['id']
                            new_voice=routed_voice(pack,analysis,after,new_part,new_segment)[0]['id']
                            voice_changed|=old_voice!=new_voice
                        except AppError:voice_changed=True # unresolved routes must be held, never reused
                    if voice_changed or stresses and name_spans(segment['spoken'].replace(ACUTE,''),stresses,implicit_single=True):
                        changed.append(segment['id'])
                if changed:
                    takes=dict(analysis.get('takes')or{})
                    analysis={**analysis,'takes':{**takes,**{sid:takes.get(sid,0)+1 for sid in changed}}}
            with self.store.lock, self.store.db:
                if changed:
                    from .current_audiobook import capture_parts
                    capture_parts(self.store,generation,changed)
                    self.store.db.executemany("UPDATE segments SET status='pending',audio_hash=NULL,duration=0,cues='[]' WHERE generation_id=? AND id=?",
                        [(gid,sid)for sid in changed])
                rename_speakers(self.store.db, gid, speakers)
                self.store.db.execute('UPDATE generations SET cast=?,analysis=?,updated=? WHERE id=?',
                                      (json.dumps(cast, ensure_ascii=False), json.dumps(analysis), now(), gid))
            return self.store.generation(gid)

    def cast_rename(self, gid, character_id, name):
        name = bounded_text(name, 200)
        def change(generation, pack):
            old = cast_member(generation['cast'], character_id)
            renamed = {**old, 'name': name, 'aliases': alias_list([old.get('name', ''), *old.get('aliases', [])], name)}
            return [renamed if character['id'] == character_id else character for character in generation['cast']], generation['analysis'], {}
        return self._review(gid, change)

    def cast_merge(self, gid, source_id, target_id):
        def change(generation, pack):
            source, target = cast_member(generation['cast'], source_id), cast_member(generation['cast'], target_id)
            if source_id == target_id:
                raise AppError('Choose two different characters to merge.')
            merged = merge_character(target, source)
            merged = {**merged, 'name': target['name'], 'kind': character_kind(target),
                      'aliases': alias_list([source.get('name', ''), *merged['aliases']], target['name'])}
            analysis = {**generation['analysis'], **{key: [{**entry, 'speaker': target_id} if entry.get('speaker') == source_id else entry
                                                           for entry in generation['analysis'].get(key, [])]
                                                     for key in ('casting_inputs', 'speaker_resolution')}}
            cast = [merged if character['id'] == target_id else character for character in generation['cast'] if character['id'] != source_id]
            return cast, analysis, {source_id: target_id}
        return self._review(gid, change)

    def cast_name_stress(self, gid, character_id, stressed):
        """Listener override of one automatic name-stress guess, during cast review."""
        from .auto_pronunciation import validate_name_stress
        import secrets
        def change(generation, pack):
            pronunciation = generation['analysis'].get('pronunciation') or {}
            current = pronunciation.get('names', {}).get(character_id)
            if current is None:
                raise AppError('This character has no automatic name stress to change.')
            try:
                validate_name_stress(current['name'], stressed)
            except ValueError as error:
                raise AppError(str(error)) from error
            row = {**current, 'stressed': stressed, 'source': 'listener',
                   'listener_review':{'id':secrets.token_hex(12),'created':now(),'operation':'cast_name_stress',
                       'generation_id':gid,'book_id':generation['book_id'],'character_id':character_id,
                       'name':current['name'],'stressed':stressed},
                   **({'model_stressed': current.get('model_stressed', current['stressed'])} if current.get('source') == 'model' else {})}
            names = {**pronunciation['names'], character_id: row}
            return generation['cast'], {**generation['analysis'], 'pronunciation': {**pronunciation, 'names': names}}, {}
        updated = self._review(gid, change)
        from .listener_stress import record_cast_review
        book=self.store.book(updated['book_id'])
        record_cast_review(self.store,book,updated['analysis']['pronunciation']['names'][character_id]['listener_review'])
        from .series_characters import sync_generation
        sync_generation(self.store, book, updated,
                        updated['analysis'].get('pronunciation') or {})
        return updated

    def cast_voice(self, gid, character_id, voice_id):
        def change(generation, pack):
            character = cast_member(generation['cast'], character_id)
            voice = next((voice for voice in casting_pool(character, pack['voices']) if voice['id'] == voice_id), None)
            if voice is None:
                raise AppError('This voice cannot be used for this character.')
            return ([{**item, **voice_fields(voice)} if item['id'] == character_id else item for item in generation['cast']],
                    generation['analysis'], {})
        return self._review(gid, change)

    def cast_kind(self, gid, character_id, kind):
        if kind not in KINDS:
            raise AppError('Unknown character kind.')
        def change(generation, pack):
            old = cast_member(generation['cast'], character_id)
            votes, totals = dict(kind_votes(old)), kind_totals(old)
            if resolve_kind(totals) != kind:
                votes = {**votes, kind: max(totals.values(), default=0) + 1}
            cast = [{**item, 'kind': kind, 'kind_votes': votes} if item['id'] == character_id else item for item in generation['cast']]
            if all(voice['id'] != old.get('voice_id') for voice in casting_pool({'kind': kind}, pack['voices'])):
                voice = recast(cast, character_id, pack, generation['analysis'].get('casting_inputs') or generation['segments'],
                               generation['analysis'].get('narrator_voices'))
                cast = [{**item, **voice_fields(voice)} if item['id'] == character_id else item for item in cast]
            return cast, generation['analysis'], {}
        return self._review(gid, change)

    def cast_narrator(self, gid, chapter_id, voice_id):
        def change(generation, pack):
            if chapter_id != '' and chapter_id not in generation['config']['chapters']:
                raise AppError('Unknown chapter.')
            voice = next((voice for voice in pack['voices'] if voice['id'] == voice_id and voice_role(voice) in ('narrator', 'narrator_variant')), None)
            if voice is None:
                raise AppError('Choose a narrator voice of this model pack.')
            analysis = generation['analysis']
            analysis = {**analysis, 'narrator_voices': {**(analysis.get('narrator_voices') or {}), chapter_id: voice_id}}
            return ([{**item, **voice_fields(voice)} if item['id'] == 'narrator' and not chapter_id else item for item in generation['cast']],
                    analysis, {})
        return self._review(gid, change)

    def pack_voices(self, gid):
        rows = self.store.rows('SELECT config FROM generations WHERE id=?', (require_id(gid),))
        if not rows:
            raise AppError('Generation not found.')
        return [voice_details(voice) for voice in self._pack(json.loads(rows[0]['config']))['voices']]

    def revise(self, gid, changes, name=''):
        revision = self.store.revise(gid, changes, name)
        try:
            summary = casting_summary(self._pack(revision['config']), revision['config'], revision['segments'], revision['cast'], revision['analysis'])
        except (AppError, KeyError, TypeError, ValueError):
            return revision
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps({**revision['analysis'], **summary}), revision['id']))
        return self.store.generation(revision['id'])

    def repair(self, gid):
        with self.lock:
            if self.closed:
                raise AppError('Local preparation is closing; audio repair cannot start.')
            if self.active:
                raise AppError('Pause preparation before checking and repairing saved audio.')
            generation = self.store.generation(gid)
            if not generation['analysis'].get('complete'):
                raise AppError('Finish character analysis before repairing saved audio.')
            if generation['analysis'].get('approved') is False:
                return self.start(gid)
            book = self.store.book(generation['book_id'])
            validate_coverage(book, generation['config']['chapters'], generation['segments'])
            self.stop.clear()
            damaged = []
            inspected = {}
            for segment in generation['segments']:
                self._check()
                key = segment['audio_hash']
                if key and key not in inspected:
                    try:
                        require_id(key)
                        inspected[key] = accepted_audio(self.store.root / 'audio' / f'{key}.wav')
                    except (AppError, ValueError, OSError):
                        inspected[key] = None
                result = inspected.get(key)
                if segment['status'] == 'review':
                    continue
                if (segment['status'] not in ('ready', 'review') or not result or result['reset_cues']
                        or abs(segment['duration'] - result['duration']) > .0001):
                    damaged.append(segment['id'])
            unaligned = set(generation['analysis'].get('alignment_failed', []))
            alignment_missing = generation['config'].get('align_words') and any(
                segment['status'] == 'ready' and not segment['cues'] and audible(segment['spoken']) and segment['audio_hash'] not in unaligned for segment in generation['segments'])
            if not damaged and not alignment_missing:
                return generation if generation['status'] in ('ready', 'held') else self.start(gid)
            if not damaged:
                self.store.status(gid, 'paused')
                return self.start(gid)
            self._pack(generation['config'], verify=True, stop=self.stop)
            with self.store.lock, self.store.db:
                if self.store._generation_segments(gid) != generation['segments']:
                    raise AppError('Saved audio changed while it was being checked. Retry the audio check.')
                from .current_audiobook import capture_parts
                capture_parts(self.store,generation,damaged)
                self.store.db.executemany(
                    "UPDATE segments SET status='pending',duration=0,cues='[]' WHERE generation_id=? AND id=?",
                    [(gid, sid) for sid in damaged])
                self.store.db.execute(
                    "UPDATE generations SET status='paused',error='',updated=? WHERE id=?", (now(), gid))
            return self.start(gid)

    def pause(self,gid):
        with self.lock:
            self.store.execute("UPDATE listener_correction_jobs SET status='paused',updated=? WHERE generation_id=? AND status IN ('queued','running')",(now(),gid))
            self.listener_queue=[item for item in self.listener_queue if item!=gid]
            self.store.execute('UPDATE generations SET resume_on_launch=0 WHERE id=?',(gid,))
            if self.active==gid:
                self.stop.set()
            else:
                generation=self.store.generation(gid)
                if generation['status'] not in ('ready','review','held'):
                    self.store.status(gid,'paused')
        return {'requested':True}

    def _check(self):
        if self.stop.is_set():
            raise AppError('Preparation paused.')

    def _revive(self, error):
        process = getattr(self.engine, 'process', None)
        if process is None or process.poll() is None:
            return
        try:
            self.engine.close()
            with self.lock:
                self._check()
                self.engine = self.engine_factory(self.binaries)
        except Exception as failure:
            raise AppError(str(error)) from failure

    def _replace(self):
        if self.engine:
            self.engine.close()
        with self.lock:
            self._check()
            self.engine = self.engine_factory(self.binaries)

    timed_out = False

    def _guarded(self, method, payload):
        pack = payload.get('pack')
        chapter_checked = method in ('analyze', 'critique', 'render', 'narrative_frame', 'performance_direction')
        if pack is not None:
            pack_unchanged(pack, quick=chapter_checked)
        try:
            return self.engine.call(method, payload, self.stop)
        finally:
            if pack is not None:
                pack_unchanged(pack, quick=chapter_checked)

    def _call(self, method, payload):
        timed_out, self.timed_out = self.timed_out, False
        for attempt in range(2):
            try:
                return self._guarded(method, payload)
            except EngineTransportError as exc:
                if self.stop.is_set() or exc.code != 'timeout' and attempt:
                    raise
                if exc.code == 'timeout' and timed_out:
                    raise AppError(str(exc), 'timeout') from exc
                self.timed_out = exc.code == 'timeout'
                self._revive(exc)
                if self.timed_out:
                    raise

    def _corrected(self, method, payload, validate):
        error = ''
        for attempt in range(2):
            result = self._call(method, {**payload, 'retry_error': error})
            try:
                return validate(result)
            except AppError as exc:
                if attempt == 1 or self.stop.is_set():
                    raise
                error = str(exc)

    def _reconcile(self, arguments, cast, texts, speech=None, saved=None, save=None):
        order = {character['id']: index for index, character in enumerate(cast)}
        by_id = {character['id']: character for character in cast}
        evidence = {cid: {pid: texts[pid] for pid in character.get('evidence', []) if pid in texts} for cid, character in by_id.items()}
        labels = {cid: name_tokens(character.get('name', '')) for cid, character in by_id.items()}
        names = {cid: canonical_name(label) for cid, label in labels.items()}
        genders = {cid: value for cid, character in by_id.items() if (value := (character.get('gender') or {}).get('value')) in ('male', 'female')}
        stored = saved_decisions(saved, by_id)
        parent, members, apart, decided = {}, {cid: [cid] for cid in by_id}, defaultdict(set), {}
        decisions, failed, unresolved = [], [], []
        skipped = compared = fresh = planned = 0
        def root(cid):
            while cid in parent:
                cid = parent[cid]
            return cid
        def representative(top):
            return max(members[top], key=lambda cid: (len(names[cid]), -order[cid]))
        def distinct(first, second):
            return not contains(names[first], names[second]) and not contains(names[second], names[first])
        def opposed(one, two):
            return one in genders and two in genders and genders[one] != genders[two] and labels[one] != labels[two]
        def clash(first, second):
            return any(opposed(one, two) for one in members[first] for two in members[second])
        def progress():
            return {'progress': {'stage': 'reconcile', 'done': compared, 'total': max(planned, compared)}}
        def record():
            return {'decisions': list(decisions), 'compared': compared, 'skipped': skipped}
        def compare(pair):
            nonlocal compared, fresh
            key = frozenset(pair)
            if key not in decided:
                self._check()
                if key in stored:
                    decided[key] = stored[key]
                else:
                    if save and not fresh:
                        save(progress())
                    supplied = {cid: evidence[cid] for cid in pair}
                    try:
                        decided[key] = self._corrected('reconcile', {**arguments, 'characters': registry([by_id[cid] for cid in pair]), 'evidence': supplied},
                                                       lambda proposal: alias_decision(proposal, supplied))
                    except AnalysisCapacityError:
                        decided[key] = None
                    except EngineTransportError as exc:
                        if exc.code != 'timeout':
                            raise
                        self._revive(exc)
                        decided[key] = None
                    fresh += 1
                compared += 1
                decisions.append([*pair, decided[key]])
                if decided[key] is None:
                    failed.append(pair)
                elif not decided[key]:
                    apart[root(pair[0])].add(root(pair[1]))
                    apart[root(pair[1])].add(root(pair[0]))
                if save and key not in stored:
                    save({'reconcile': record(), **progress()})
            return decided[key]
        pairs = (pair for pair in alias_candidates(cast, speech) if evidence[pair[0]] and evidence[pair[1]] and not opposed(*pair))
        head = list(islice(pairs, ALIAS_COMPARISON_LIMIT))
        planned = len(head)
        for pair in chain(head, pairs):
            self._check()
            first, second = root(pair[0]), root(pair[1])
            if first == second or second in apart[first] or clash(first, second):
                continue
            check = tuple(sorted((representative(first), representative(second)), key=order.get))
            confirm = check != pair and distinct(*check) and decided.get(frozenset(check)) is not True
            if decided.get(frozenset(pair), True) is not True or confirm and frozenset(check) in decided:
                continue
            if compared + (frozenset(pair) not in decided) + confirm > ALIAS_COMPARISON_LIMIT:
                skipped += 1
                if len(unresolved) < ALIAS_COMPARISON_LIMIT:
                    unresolved.append(pair)
                continue
            if not compare(pair) or confirm and not compare(check):
                continue
            first, second = sorted((first, second), key=order.get)
            parent[second] = first
            members[first] += members.pop(second)
            for other in apart.pop(second, set()):
                apart[other].discard(second)
                apart[other].add(first)
                apart[first].add(other)
        merges = {cid: root(cid) for cid in parent}
        def separate(found):
            roots = (tuple(sorted((root(one), root(two)), key=order.get)) for one, two in found)
            return list(dict.fromkeys(pair for pair in roots if pair[0] != pair[1] and pair[1] not in apart[pair[0]] and not clash(*pair)))
        unmerged = separate(failed)
        state = {'reconcile': record(), 'unmerged_pairs': [list(pair) for pair in separate(failed + unresolved)[:ALIAS_COMPARISON_LIMIT]]}
        return merge_aliases(cast, merges, common_words(texts.values()) if merges else frozenset()), merges, len(unmerged), skipped, state

    def _run(self,gid):
        status,message='failed',''
        try:
            status=self._prepare(gid)
        except Exception as error:
            paused=self.stop.is_set()
            status,message=('paused','') if paused else ('failed',str(error))
            self.store.execute("UPDATE segments SET status='pending' WHERE generation_id=? AND status='rendering'",(gid,))
        finally:
            try:
                if self.engine:
                    self.engine.close()
            except Exception as error:
                status,message='paused' if self.stop.is_set() else 'failed',f'Runtime cleanup failed: {error}'
            finally:
                self.engine=None
                with self.lock:
                    self.active=None
                    with self.store.lock, self.store.db:
                        parts={s['id']:s for s in self.store._generation_segments(gid)}
                        corrections=self.store.rows("SELECT id,segment_ids FROM listener_correction_jobs WHERE generation_id=? AND status IN ('queued','running')",(gid,))
                        correction_targets=[parts.get(sid)for job in corrections for sid in json.loads(job['segment_ids'])]
                        correction_review=any(s and s['status']=='review'for s in correction_targets)
                        pending_correction=any(not s or s['status']not in ('ready','review')or s['status']=='ready'and not s['audio_hash']
                            for s in correction_targets)
                        late_pending=status=='ready'and pending_correction
                        if status=='ready':
                            if self.stop.is_set()and not corrections:status='paused'
                            elif correction_review or any(s['status']=='review'for s in parts.values()):status='held'
                            elif self.stop.is_set()or pending_correction or any(s['status']not in ('ready','review')for s in parts.values()):
                                status='paused'
                        self.store.db.execute(
                            "UPDATE generations SET status=?,error=?,updated=?,resume_on_launch=CASE WHEN ?='paused' THEN resume_on_launch ELSE 0 END WHERE id=?",
                            (status, message[:4000], now(), status, gid),
                        )
                        for job in corrections:
                            targets=[parts.get(sid)for sid in json.loads(job['segment_ids'])]
                            delivered=bool(targets)and all(s and s['status']=='ready'and s['audio_hash']for s in targets)
                            bound=bool(targets)and all(targets)
                            review=any(s and s['status']=='review'for s in targets)
                            correction_status='applied'if delivered else'queued'if bound and not review and (self.stop.is_set()or status=='paused'or late_pending)else'held'
                            error=''if delivered else message[:4000]
                            if not bound:error='The correction target passages are missing; rebind the saved report before retrying.'
                            elif review and not error:error='The correction target is waiting for passage review.'
                            self.store.db.execute('UPDATE listener_correction_jobs SET status=?,error=?,updated=? WHERE id=?',
                                (correction_status,error,now(),job['id']))
        if not self.closed:
            self.store.complete_replacements(gid)
            self.store.collect_audio()
            with self.lock:self._start_listener_queue()

    def _pack_paths(self, config):
        yield config['pack_path']
        selected = self.store.settings()['pack_path']
        if selected and Path(selected).expanduser().resolve() != Path(config['pack_path']).expanduser().resolve():
            yield selected
        if self.resources and (bundled := bundled_pack(self.resources, config['pack_fingerprint'])):
            yield bundled

    def _pack(self, config, verify=False, stop=None):
        load = lambda path: self.verified(path, stop, pinned=True) if verify and self.verified else load_pack(path, verify=verify, stop=stop, pinned=True)
        changed, failure = AppError('Model pack changed. This generation is pinned to the original revision.'), None
        for path in self._pack_paths(config):
            try:
                pack = load(path)
            except AppError as error:
                if stop is not None and stop.is_set():
                    raise
                failure = failure if failure is changed else error
                continue
            if pack['_fingerprint'] == config['pack_fingerprint']:
                return pack
            failure = changed
        raise failure

    def _prepare(self,gid):
        generation=self.store.generation(gid)
        config=generation['config']
        from .post_processing import mode,enabled
        config={**config,'post_processing':mode(config)}
        pack = self._pack(config, verify=True, stop=self.stop)
        self._check()
        pack_unchanged(pack)
        self.engine=self.engine_factory(self.binaries)
        book=self.store.book(generation['book_id'])
        arguments={'pack':pack,'device':config['device'],
                   'analysis_mode':analysis_provider(config,pack),
                   'tie_breaker':config.get('speaker_tie_breaker','none')}
        resolved_speech = self._call('probe',arguments)
        from .speech_backend import is_higgs
        if is_higgs(pack):
            if not isinstance(resolved_speech, dict) or resolved_speech.get('backend') != 'higgs_audio_cpp':
                raise AppError('Inference did not resolve the Higgs backend.')
            config = {**config, 'resolved_speech': resolved_speech}
            self.store.execute('UPDATE generations SET config=? WHERE id=?', (json.dumps(config, ensure_ascii=False), gid))
        analysis=generation['analysis']
        cast=generation['cast']
        if not analysis.get('complete'):
            self.store.status(gid,'analyzing')
            bases=base_segments(book,analysis_chapters(book,config['chapters']))
            order=analysis_order(self.store.generation(gid)['segments'],bases,config['chapters'])
            if analysis.get('casting_schema') != CASTING_SCHEMA or analysis.get('analysis_contract', ANALYSIS_CONTRACT if not analysis else None) != ANALYSIS_CONTRACT or order is None:
                superseded = self._preserve_checkpoint(gid, analysis, cast) if analysis else None
                if analysis or order is None:
                    placeholder_segments(self.store,gid,bases,config['chapters'])
                from .series_characters import seed_cast
                analysis, cast = ({'superseded_checkpoint': superseded} if superseded else {}), seed_cast(self.store,book)
            normalize_analysis_order(self.store, gid, bases, config['chapters'])
            if is_higgs(pack) and 'voice_family_guidance' not in analysis:
                analysis=bind_voice_family_guidance(analysis,self.resources,pack)
                if 'voice_family_guidance' in analysis:
                    self.store.execute('UPDATE generations SET analysis=? WHERE id=?',(json.dumps(analysis,ensure_ascii=False),gid))
            if is_higgs(pack):
                from .narrative_frames import SYSTEM,request_for,schema_for,checked_ranges,reconcile,adjudicate
                from .speech_backend import implementation_fingerprint
                binding=digest({'source':book.get('source_hash'),'source_chapters':book['chapters'],
                    'chapters':config['chapters'],'provider':arguments['analysis_mode'],
                    'implementation':implementation_fingerprint('rimsky.narrative_frames')})
                frames=analysis.get('narrative_frames') or {}
                if frames.get('binding')!=binding:frames={'binding':binding,'chapters':{},'by_source':{}}
                for chapter_id in config['chapters']:
                    self._check()
                    if chapter_id in frames['chapters']:continue
                    request=request_for(book,chapter_id);schema=schema_for(request)
                    primary=self._corrected('narrative_frame',{**arguments,'phase':'primary','request':request,'schema':schema},
                                           lambda value:checked_ranges(value,request))
                    critic=self._corrected('narrative_frame',{**arguments,'phase':'critic','request':request,'schema':schema},
                                          lambda value:checked_ranges(value,request))
                    report=reconcile(primary,critic,request)
                    if any(f['status']=='needs_adjudication' for f in report['by_source'].values()):
                        final=self._corrected('narrative_frame',{**arguments,'phase':'adjudication',
                            'request':{**request,'primary':primary,'critic':critic,'checked':report['by_source']},'schema':schema},
                            lambda value:(adjudicate(report,value,request),value)[1])
                        report=adjudicate(report,final,request)
                    frames['chapters'][chapter_id]=report;frames['by_source'].update(report['by_source'])
                    analysis={**analysis,'narrative_frames':frames}
                    self.store.execute('UPDATE generations SET analysis=? WHERE id=?',(json.dumps(analysis,ensure_ascii=False),gid))
            narrative_frames=analysis.get('narrative_frames')
            completed=set(analysis.get('completed_batches',[]))
            completed_sources=set(analysis.get('completed_sources',[]))
            degraded=list(analysis.get('degraded',[]))
            validation_guard = analysis.get('validation_guard') if isinstance(analysis.get('validation_guard'), dict) else {}
            previous_trip = validation_guard.get('tripped') if isinstance(validation_guard.get('tripped'), dict) else None
            if previous_trip is None and validation_guard.get('consecutive', 0) >= INVALID_STREAK_LIMIT - 1:
                # Checkpoints written by the first breaker implementation did
                # not include the structured `tripped` record.  Treat their
                # persisted near-limit streak as a prior trip so they can
                # recover instead of becoming permanently complete-but-held.
                previous_trip = {'key': validation_guard.get('key', ''),
                                 'failures': validation_guard.get('consecutive', 0),
                                 'window': len(validation_guard.get('recent', []))}
            # A new run gets a fresh rate window. The failed passage was committed
            # before the stop, so resume advances instead of paying for the same
            # answer forever. Previously degraded passages receive one later retry.
            if previous_trip:
                validation_guard = {'key': '', 'consecutive': 0, 'recent': [], 'previous_trip': previous_trip}
            attempted_degraded = {item for item in analysis.get('degraded_retry_attempted', []) if isinstance(item, str)}
            resolutions=list(analysis.get('speaker_resolution',[]))
            critiques=list(analysis.get('critic_reviews',[]))
            structure=dict(analysis.get('speech_structure',{}))
            superseded=analysis.get('superseded_checkpoint')
            timeouts=Counter({tid:count for tid,count in (analysis.get('timeouts') if isinstance(analysis.get('timeouts'),dict) else {}).items() if type(count) is int})
            sources={source['id'] for source in bases}
            trusted=trusted_inputs(analysis.get('casting_inputs',[]),analysis.get('speaker_resolution',[]),self.store.generation(gid)['segments'],analysis.get('speaker_verdicts'))
            shared={'cast':cast,'accepted':accepted_speakers(bases,trusted),'volume':speech_volume(trusted),'trusted':trusted,
                     'inputs':list(analysis.get('casting_inputs',[])),'sources':{source['id']:source for source in bases},'slow':frozenset(tid for tid,count in timeouts.items() if count>1)-completed_sources,
                    'validation_guard':dict(validation_guard), 'degraded_retry_attempted': attempted_degraded,
                    'retrying_degraded': False}
            names=lru_cache(maxsize=16384)(lambda text: canonical_name(name_tokens(text)))
            def commit(done, parts, failed, key, records=(), fixed=None, reviews=()):
                nonlocal analysis, degraded, resolutions, critiques
                with self.store.lock,self.store.db:
                    for original in done:
                        if original['chapter_id'] in config['chapters']:
                            self.store.db.execute('DELETE FROM segments WHERE generation_id=? AND source_id=? AND start>=? AND end<=?',(gid,original['source_id'],original['start'],original['end']))
                    suborders=Counter()
                    for part in parts:
                        if part['chapter_id'] in config['chapters']:
                            suborders[part['ordinal']]+=1
                            if suborders[part['ordinal']]>99:
                                raise AppError('A source passage produced more than 99 synthesis units; no text was dropped and the batch was not saved.')
                            self.store.db.execute('INSERT INTO segments(generation_id,id,ordinal,chapter_id,source_id,start,end,text,spoken,speaker,delivery,warning,performance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(gid,part['id'],part['ordinal']*100+suborders[part['ordinal']],part['chapter_id'],part['source_id'],part['start'],part['end'],part['text'],part['spoken'],part['speaker'],part['delivery'],part['warning'],json.dumps(part.get('performance') or {},ensure_ascii=False)))
                    if key:
                        completed.add(key)
                    ids={source['id'] for source in done}
                    completed_sources.update(ids)
                    degraded = [item for item in degraded if not isinstance(item, dict) or item.get('source') not in ids]
                    degraded.extend(failed)
                    resolutions=[item for item in resolutions if item.get('target_id') not in ids]+list(records)
                    critiques=[item for item in critiques if item.get('target_id') not in ids]+list(reviews)
                    structure.update(fixed or {})
                    analysis={'completed_batches': sorted(completed), 'completed_sources': sorted(completed_sources), 'complete': False,
                              'chapter': done[0]['chapter_id'], 'casting_schema': CASTING_SCHEMA, 'analysis_contract': ANALYSIS_CONTRACT, 'casting_inputs': shared['inputs'],
                               'degraded': list(degraded), 'speaker_resolution': resolutions, 'speech_structure': structure, 'critic_reviews': critiques,
                               'validation_guard': shared['validation_guard'],
                               'degraded_retry_attempted': sorted(shared['degraded_retry_attempted']),
                              'progress': {'stage': 'analysis', 'done': len(completed_sources & sources), 'total': len(sources)},
                              **({'timeouts': dict(timeouts)} if timeouts else {}), **({'superseded_checkpoint': superseded} if superseded else {}),
                              **({'narrative_frames':narrative_frames} if narrative_frames else {}),
                              **({'voice_family_guidance':analysis['voice_family_guidance']} if 'voice_family_guidance' in analysis else {})}
                    self.store.db.execute('UPDATE generations SET cast=?,analysis=?,updated=? WHERE id=?',(json.dumps(shared['cast'],ensure_ascii=False),json.dumps(analysis),now(),gid))
            def call(payload):
                nonlocal analysis
                try:
                    return self._call('analyze',{**arguments,**payload,
                        **({'narrative_frames':narrative_frames['by_source']} if narrative_frames else {})})
                except AppError as error:
                    if error.code=='timeout':
                        timeouts.update(s['id'] for s in payload['segments'])
                        analysis={**analysis,'casting_schema':CASTING_SCHEMA,'analysis_contract':ANALYSIS_CONTRACT,'timeouts':dict(timeouts)}
                        with self.store.lock,self.store.db:
                            self.store.db.execute('UPDATE generations SET cast=?,analysis=?,updated=? WHERE id=?',(json.dumps(shared['cast'],ensure_ascii=False),json.dumps(analysis),now(),gid))
                    raise
            common=common_words(source['text'] for source in bases)
            def analyze_chapters():
                for chapter in book['chapters']:
                    inputs=[s for s in bases if s['chapter_id']==chapter['id']]
                    analyse_sources(inputs,inputs,shared,call,commit,self._check,names,common,completed,completed_sources,
                                    lambda request,schema:self._call('critique',{**arguments,'request':request,'schema':schema}),
                                    sentence_sized=is_higgs(pack))
                    pack_unchanged(pack)
            analyze_chapters()
            retry_degraded = ({item.get('source') for item in degraded if isinstance(item, dict)} -
                              shared['degraded_retry_attempted']) if previous_trip else set()
            retry_degraded.discard(None)
            if retry_degraded:
                completed.clear()
                completed_sources.difference_update(retry_degraded)
                retry_ranges = [source for source in bases if source['id'] in retry_degraded]
                shared['inputs'][:] = without_source_ranges(shared['inputs'], retry_ranges)
                shared['trusted'][:] = without_source_ranges(shared['trusted'], retry_ranges)
                for source_id in retry_degraded:
                    shared['accepted'].pop(source_id, None)
                shared['volume'] = speech_volume(shared['trusted'])
                shared['validation_guard'] = {'key': '', 'consecutive': 0, 'recent': []}
                shared['retrying_degraded'] = True
                analyze_chapters()
            cast=shared['cast']
            current=self.store.generation(gid)
            self.store.status(gid,'casting')
            def save(update):
                analysis.update(update)
                with self.store.lock, self.store.db:
                    self.store.db.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps(analysis), gid))
            speech = speech_volume(analysis.get('casting_inputs', []))
            cast,merges,unmerged,skipped,state=self._reconcile(arguments,cast,{s['id']:s['text'] for s in bases},speech,analysis.get('reconcile'),save)
            # The series registry is cumulative, while a book cast is not. Keep
            # inherited identities only when their name occurs or analysis used
            # their stable ID as a speaker in this volume.
            from .analysis import mentioned_characters
            mentioned={item['id'] for item in mentioned_characters(cast,(s['text'] for s in bases))}
            used={entry.get('speaker') for entry in analysis.get('casting_inputs',[])}
            cast=[{k:v for k,v in character.items() if k!='_series_seed'} for character in cast
                  if not character.get('_series_seed') or character['id'] in mentioned or character['id'] in used]
            save({**state, 'progress': {'stage': 'casting', 'done': 0, 'total': 1}})
            analysis['speaker_resolution']=[*analysis.get('speaker_resolution',[]), *merged_descriptor_holds(analysis.get('casting_inputs',[]), merges, cast, common)]
            for entry in [*analysis.get('casting_inputs',[]), *analysis.get('speaker_resolution',[])]:
                entry['speaker']=merges.get(entry.get('speaker'),entry.get('speaker'))
            context_inputs = analysis.get('casting_inputs') or current['segments']
            if is_higgs(pack):
                personas=self._character_direction(gid,book,cast,arguments)
                analysis=self.store.generation(gid)['analysis']
                cast=[{**c,'description':c.get('description','')+'\nSource-backed vocal persona: '+
                       json.dumps(personas['profiles'][c['id']],ensure_ascii=False)} for c in cast]
            cast = cast_voices(cast, pack['voices'], context_inputs, pack['files'], pack['_root'], pack['speech']['variant'], self.stop,
                               same_hints=[tuple(pair) for pair in state['unmerged_pairs']],
                               family_guidance=(analysis.get('voice_family_guidance') or {}).get('families'))
            if is_higgs(pack):
                # Every generation guesses name stress automatically; listeners may override later.
                analysis['pronunciation']=self._name_stress(gid,book,cast,arguments)
            analysis.update(casting_summary(pack, config, current['segments'], cast, analysis, unmerged, skipped))
            analysis['speaker_resolution_metrics'] = resolution_metrics(analysis.get('speaker_resolution', []))
            analysis.update(complete=True, approved=not self.store.settings()['cast_review'], progress={'stage': 'casting', 'done': 1, 'total': 1})
            if analysis['approved']:
                del analysis['progress']
            with self.store.lock, self.store.db:
                rename_speakers(self.store.db, gid, merges)
                self.store.db.execute('UPDATE generations SET cast=?,analysis=? WHERE id=?', (json.dumps(cast,ensure_ascii=False),json.dumps(analysis),gid))
        if analysis.get('approved') is False:
            return 'review'
        self._check()
        self.store.status(gid,'rendering')
        language=self._speech_language(gid,book)
        work_ids=render_work_ids(self.store.generation(gid))
        if work_ids is not None:config={**config,'_render_work_ids':sorted(work_ids)}
        if config.get('russian_preparation') and language=='Russian' and work_ids!=set():
            self._ensure_russian_names(gid,book,arguments)
        if is_higgs(pack):
            self._scene_direction(gid,pack,config,book,arguments)
        rendered_scope=self._render(gid,pack,config,book,language)
        if rendered_scope is not None:config={**config,'_render_work_ids':sorted(rendered_scope)}
        # Exhausted machine word checks receive two independent recovery
        # rounds before this job settles. Human/casting holds are never cleared.
        for recovery_round in range(2 if enabled(config)else 0):
            if not self._retry_word_holds(gid):break
            self._check()
            recovered_scope=self._render(gid,pack,config,book,language)
            if recovered_scope is not None:
                config={**config,'_render_work_ids':sorted(set(config.get('_render_work_ids',[]))|recovered_scope)}
        self.engine.close()
        self.engine=None
        if config.get('align_words'):
            pack_unchanged(pack)
            self._align(gid,pack,language,config)
        self._check()
        missing=self.store.rows("SELECT count(*) n FROM segments WHERE generation_id=? AND status NOT IN ('ready','review')",(gid,))[0]['n']
        if missing:
            waiting=self.store.rows("SELECT count(*) n FROM segments WHERE generation_id=? AND status NOT IN ('ready','review') AND audio_hash IS NULL",(gid,))[0]['n']
            if not is_higgs(pack) or waiting:
                raise AppError('Generation is incomplete; missing audio was not published.')
        final=self.store.generation(gid)
        validate_coverage(book, config['chapters'], final['segments'])
        return 'held' if missing or unresolved(final) else 'ready'

    def _direction_infer(self,arguments,kind):
        from .analysis import mismatch
        def infer(system,request,schema):
            phase='adjudication' if 'proposals' in request else 'critic' if 'Independently' in system or 'independently' in system else 'primary'
            error=''
            for attempt in range(2):
                self._check()
                answer=self._call('performance_direction',{**arguments,'kind':kind,'phase':phase,
                    'request':request,'schema':schema,'retry_error':error})
                try:
                    problem=mismatch(answer,schema,'direction')
                    if problem:raise ValueError(problem)
                    module=__import__('rimsky.'+('character_direction' if kind=='profile' else 'scene_direction'),fromlist=['validate'])
                    module.validate(answer,request)
                    return answer
                except ValueError as exc:
                    error=str(exc)[:1500]
                    if attempt:raise AppError('Invalid performance direction: '+error) from exc
        return infer

    def _ensure_russian_names(self,gid,book,arguments):
        """Normalize complete display/alias bindings without repeating casting."""
        from .series_characters import apply_pronunciations,pronunciation_display
        from .name_stress_analysis import validator
        current=self.store.generation(gid);cast=current['cast']
        characters={c['id']:pronunciation_display(c)for c in cast if c['id']!='narrator'and c.get('name')}
        normalized=apply_pronunciations(self.store,book,cast,current['analysis'].get('pronunciation')or{})
        holds=normalized.get('listener_projection_holds')or[]
        if any(item.get('refresh_name_analysis')is not True for item in holds):
            raise AppError('Name pronunciation authority is held: '+'; '.join(item['reason']for item in holds))
        try:
            validator(characters)({'names':[dict(row,character_id=cid)for cid,row in normalized.get('names',{}).items()]})
            invalid=False
        except (ValueError,KeyError,TypeError):invalid=True
        if invalid or holds:
            normalized=self._name_stress(gid,book,cast,arguments,force=True)
            normalized=apply_pronunciations(self.store,book,cast,normalized)
            holds=normalized.get('listener_projection_holds')or[]
            if holds:raise AppError('Name pronunciation authority is held: '+'; '.join(item['reason']for item in holds))
            validator(characters)({'names':[dict(row,character_id=cid)for cid,row in normalized.get('names',{}).items()]})
        latest=self.store.generation(gid)['analysis']
        if name_semantics(normalized)!=name_semantics(latest.get('pronunciation')or{}):
            normalized=invalidate_name_preparation(normalized)
        if normalized!=latest.get('pronunciation'):
            self.store.execute('UPDATE generations SET analysis=? WHERE id=?',
                (json.dumps({**latest,'pronunciation':normalized},ensure_ascii=False),gid))
        return normalized

    def _name_stress(self,gid,book,cast,arguments,*,force=False):
        """Always-on educated guess of every character's name stress (listener overrides kept)."""
        from .analysis import mismatch
        from .auto_pronunciation import POLICY
        from .name_stress_analysis import SCHEMA,common_words,request,validator
        from .util import digest
        characters={c['id']:c['name']+(' ('+', '.join(c['aliases'])+')' if c.get('aliases') else '')
                    for c in cast if c['id']!='narrator' and c.get('name')}
        saved=self.store.generation(gid)['analysis'].get('pronunciation') or {}
        from .series_characters import apply_pronunciations, sync_generation
        sync_generation(self.store,book,{'cast':cast,'analysis':{}},saved)
        if not force and saved.get('policy')==POLICY and saved.get('characters_sha256')==digest(characters):
            return self._respelling_sources(apply_pronunciations(self.store,book,cast,saved))
        texts=[block['text'] for chapter in book['chapters'] for block in chapter['blocks'] if block.get('text')]
        payload=request(characters,texts);check=validator(characters);error=''
        for attempt in range(2):
            self._check()
            answer=self._call('performance_direction',{**arguments,'kind':'name_stress','phase':'primary',
                'request':payload,'schema':SCHEMA,'retry_error':error})
            try:
                problem=mismatch(answer,SCHEMA,'name_stress')
                if problem:raise ValueError(problem)
                check(answer);break
            except ValueError as exc:
                error=str(exc)[:1500]
                if attempt:raise AppError('Invalid name-stress analysis: '+error) from exc
        names={row['character_id']:{'name':row['name'],'stressed':row['stressed'],'confidence':row['confidence'],
                                    'basis':row['basis'],'source':'model'} for row in answer['names']}
        for cid,row in saved.get('names',{}).items():
            if row.get('source')=='listener' and cid in names and row.get('name')==names[cid]['name']:
                names[cid]=row
        names,hard_e=self._hard_e(names,texts,arguments,saved)
        lexicon={cid:row['stressed'] for cid,row in names.items()}
        result={'policy':POLICY,'names':names,'common':common_words(lexicon,texts),
                'characters_sha256':digest(characters),'provider':arguments.get('analysis_mode'),'human_approval':False,
                **({'hard_e':hard_e} if hard_e else {})}
        result=self._respelling_sources(apply_pronunciations(self.store,book,cast,result))
        sync_generation(self.store,book,{'cast':cast,'analysis':{}},result)
        return result

    @staticmethod
    def _respelling_sources(result):
        """Record who decided each respelled name word (model hard-е or listener); series listener pronunciations win."""
        from .respelling import names_sources
        sources=names_sources(result.get('names') or {})
        return {**result,'respelling_sources':sources} if sources else result

    def _hard_e(self,names,texts,arguments,saved):
        """Automatic hard-е pass (rule hard-e-respelling-v1) through the generation's pinned analysis provider.

        Plug and play: no review gate. Listener-edited rows are never candidates and listener or series
        pronunciations are applied afterwards, so they win. The decision is reused while the request is unchanged."""
        from .analysis import mismatch
        from . import hard_e
        from .respelling import apply_model_names
        from .util import digest
        candidates={cid:row for cid,row in names.items() if row.get('source') not in ('listener','series_listener')}
        rows=hard_e.candidates(candidates,{})
        if not rows:return names,None
        hard_e.evidence(rows,texts); payload=hard_e.request(rows); sha=digest(payload)
        previous=saved.get('hard_e') or {}
        if previous.get('rule')==hard_e.RULE and previous.get('request_sha256')==sha:decisions=previous['decisions']
        else:
            ids=[item['id'] for item in payload['items']];contract=hard_e.schema(ids);error=''
            for attempt in range(2):
                self._check()
                answer=self._call('performance_direction',{**arguments,'kind':'hard_e','phase':'primary',
                    'request':payload,'schema':contract,'retry_error':error})
                try:
                    problem=mismatch(answer,contract,'hard_e')
                    if problem:raise ValueError(problem)
                    hard_e.validate(answer,payload);break
                except ValueError as exc:
                    error=str(exc)[:1500]
                    if attempt:raise AppError('Invalid hard-е analysis: '+error) from exc
            decisions={d['id']:d['respelled'] for d in answer['decisions']}
        provenance={'source':'model','rule':hard_e.RULE,'provider':arguments.get('analysis_mode'),'request_sha256':sha}
        document=hard_e.document(rows,decisions,candidates,{},provenance)
        info={'rule':hard_e.RULE,'request_sha256':sha,'decisions':decisions,'provider':arguments.get('analysis_mode'),'human_approval':False}
        return apply_model_names(names,{**document}),info

    def _character_direction(self,gid,book,cast,arguments):
        from .character_direction import request,direct
        from .speech_backend import implementation_fingerprint
        current=self.store.generation(gid);saved=current['analysis'].get('character_direction')
        if not any(c['id']=='narrator' for c in cast):
            cast=[*cast,{'id':'narrator','name':'Narrator','aliases':[],
                        'description':'Narrative performer; keep presentation routes separate from dialogue identities.'}]
        def save(state):
            current=self.store.generation(gid)
            self.store.execute('UPDATE generations SET analysis=? WHERE id=?',
                (json.dumps({**current['analysis'],'character_direction':state},ensure_ascii=False),gid))
        payload={**request(book,cast),'provider':arguments['analysis_mode'],
                 'implementation_sha256':implementation_fingerprint('rimsky.character_direction')}
        return direct(payload,self._direction_infer(arguments,'profile'),saved,save)

    def _scene_direction(self,gid,pack,config,book,arguments):
        from .scene_direction import request,direct
        from .speech_backend import implementation_fingerprint
        current=self.store.generation(gid)
        work_ids=render_work_ids(current)
        if work_ids==set():return
        profiles=current['analysis'].get('character_direction')
        if not profiles or not profiles.get('complete'):
            profiles=self._character_direction(gid,book,current['cast'],arguments)
        personas=profiles['profiles'];voices={v['id']:v for v in pack['voices']}
        cast={c['id']:c for c in current['cast']}
        assigned={cid:c['voice_id'] for cid,c in cast.items()}
        signatures=current['analysis'].get('voice_signatures',{})
        # Gains are frozen pack calibration values for each identity/route,
        # never estimates from whichever chapters happened to finish first.
        for chapter in book['chapters']:
            if chapter['id'] not in config['chapters']:continue
            if work_ids is not None and not any(s['id']in work_ids and s['chapter_id']==chapter['id']for s in current['segments']):continue
            units=[];owners={}
            for segment in current['segments']:
                if segment['chapter_id']!=chapter['id']:continue
                if work_ids is not None and segment['id']not in work_ids:continue
                for index,part in enumerate(render_parts(segment) or []):
                    if part['speaker'] not in personas:
                        self._render_hold(gid,segment,'Persistent speaker persona or signature is unresolved.')
                        continue
                    try:voice,*_=routed_voice(pack,current['analysis'],assigned,part,segment)
                    except AppError as exc:
                        self._render_hold(gid,segment,str(exc));continue
                    route_key=part['speaker']+':'+voice['id']
                    from .character_direction import signature_for
                    try:signature=signature_for(voice,personas[part['speaker']],part['speaker'],pack['_fingerprint'])
                    except SpeechHoldError as exc:
                        self._render_hold(gid,segment,str(exc));continue
                    signatures[route_key]=signature
                    key=segment['id']+':'+str(index);owners[key]=(segment,index,part['speaker'],route_key)
                    units.append({**part,'id':key,'source_id':segment['source_id'],
                                  'mode':'narrator' if part['speaker']=='narrator' else 'speech'})
            payload=request(units,chapter['blocks'],personas)
            # Pin provider and director implementation in the checkpoint input.
            payload['provider']=arguments['analysis_mode']
            payload['implementation_sha256']=implementation_fingerprint('rimsky.scene_direction')
            def save(state):
                latest=self.store.generation(gid)['analysis']
                if work_ids is not None:
                    previous=latest.get('scene_direction',{}).get(chapter['id'],{})
                    state={**state,'by_unit':{**previous.get('by_unit',{}),**state.get('by_unit',{})},'work_checkpoint':state}
                chapters={**latest.get('scene_direction',{}),chapter['id']:state}
                self.store.execute('UPDATE generations SET analysis=? WHERE id=?',
                    (json.dumps({**latest,'scene_direction':chapters,'voice_signatures':signatures},ensure_ascii=False),gid))
            saved=self.store.generation(gid)['analysis'].get('scene_direction',{}).get(chapter['id'])
            if work_ids is not None:saved=(saved or{}).get('work_checkpoint')
            if not payload['turns']:
                save({'complete':True,'by_unit':{},'production_approved':False});continue
            try:
                state=direct(payload,self._direction_infer(arguments,'scene'),saved,save,
                             local=arguments['analysis_mode']=='local')
            except AppError as exc:
                # A failed request is not a missing/default direction. Hold
                # its affected turns, retain the checkpoint, continue chapters.
                latest=self.store.generation(gid)['analysis']
                saved=latest.get('scene_direction',{}).get(chapter['id'],{})
                if work_ids is not None:saved=saved.get('work_checkpoint')or{}
                save({**saved,'complete':False,'error':str(exc)})
                for segment,_,_,_ in owners.values():self._render_hold(gid,segment,'Scene direction unavailable: '+str(exc))
                continue
            updates={}
            for key,direction in state['by_unit'].items():
                segment,index,cid,route_key=owners[key]
                performance=updates.setdefault(segment['id'],json.loads(json.dumps(segment.get('performance') or {})))
                fields={'higgs_direction':direction,'character_profile':personas[cid],
                        'voice_signature':signatures[route_key],'higgs_presentation_route':route_key}
                if performance.get('internal_transition'):
                    part=performance['analysis_parts'][index]
                    part['performance']={**(part.get('performance') or {}),**fields}
                else:performance.update(fields)
            with self.store.lock,self.store.db:
                self.store.db.executemany('UPDATE segments SET performance=? WHERE generation_id=? AND id=?',
                    [(json.dumps(value,ensure_ascii=False),gid,key) for key,value in updates.items()])

    def _speech_language(self, gid, book):
        with self.store.lock, self.store.db:
            current = self.store.generation(gid)
            analysis = current['analysis']
            language = analysis.get('speech_language')
            if language not in ('Russian', 'English', 'Auto'):
                assigned, narrators = {character['id']: character.get('voice_id') for character in current['cast']}, analysis.get('narrator_voices') or {}
                saved = any(segment['audio_hash'] or segment['status'] == 'ready' for segment in current['segments']) or 'approved' not in analysis and any(
                    self._legacy(current['config'], book, segment, segment_voice(segment, assigned, narrators), analysis.get('takes') or {})
                    for segment in current['segments'])
                language = legacy_language(book) if saved else speech_language(book)
                self.store.db.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps({**analysis, 'speech_language': language}), gid))
        return language

    def _holds(self, gid, pack, config, current, voice_for):
        analysis = current['analysis']
        targets={s['id']for s in current['segments']}
        from .current_audiobook import capture_parts
        with self.store.lock,self.store.transaction():
            capture_parts(self.store,current,[s['id']for s in current['segments']if PREVIEW_NOTICE in s['warning']])
        preview = config.get('unresolved_policy', 'hold_flagged_segments') == 'render_with_warning'
        records = [dict(item) for item in analysis.get('speaker_resolution', [])]
        degraded = {item.get('source') for item in analysis.get('degraded', []) if isinstance(item, dict)}
        def presentation(item, segment):
            return 'presentation_review' in item.get('triggers', []) and item.get('source_id') == segment['source_id'] and item.get('start') == segment['start'] and item.get('end') == segment['end']
        def covered(segment, start, end):
            return any(item.get('status') == 'review' and item.get('source_id') == segment['source_id'] and item.get('start', end) <= start and end <= item.get('end', start)
                       for item in records)
        for segment in current['segments']:
            if segment['status']=='review' and any(r.get('target_id')==segment['id'] and
                    'render_degraded' in r.get('triggers',[]) for r in records):continue
            if segment['status'] == 'ready' and segment['audio_hash'] and PREVIEW_NOTICE not in segment['warning']:
                continue
            parts = render_parts(segment) or [{**part, 'performance': part_performance(part)} for part in (segment.get('performance') or {}).get('analysis_parts') or [segment]]
            decisions = [voice_for(part, segment) for part in parts]
            needs = any(decision[2] for decision in decisions)
            # Capability gaps describe the renderer, not an unresolved human
            # decision.  Drop legacy locked records and let rendering preserve
            # the desired plan while attaching a visible best-effort notice.
            records = [item for item in records
                       if 'performance_capability' not in item.get('triggers', [])
                       or not(item.get('target_id')==segment['id']or item.get('source_id')==segment['source_id']
                           and item.get('start',segment['end'])<segment['end']and segment['start']<item.get('end',segment['start']))]
            mixed = len(parts) > 1
            binding = [{'part': index if mixed else None, 'start': part.get('start', segment['start']), 'end': part.get('end', segment['end']),
                        'voice': voice['id'], 'base_voice': base['id'], 'channel': (part['performance'] or {}).get('presentation_channel', 'direct'),
                        'register': (part['performance'] or {}).get('content_register', 'prose'), 'confidence': (part['performance'] or {}).get('context_confidence', 'low'),
                        'evidence': [dict(item) for item in (part['performance'] or {}).get('context_evidence') or []], 'pack': pack['_fingerprint']}
                       for index, (part, (voice, _, review, base)) in enumerate(zip(parts, decisions)) if review]
            performance = segment.get('performance') or {}
            if (performance.get('presentation_binding') or []) != binding:
                updated = {**performance, 'presentation_binding': binding} if binding else {key: value for key, value in performance.items() if key != 'presentation_binding'}
                self.store.execute('UPDATE segments SET performance=? WHERE generation_id=? AND id=?', (json.dumps(updated, ensure_ascii=False), gid, segment['id']))
            records = [item for item in records if not (presentation(item, segment) and item.get('status') == 'review' and not needs)]
            if needs and not any(presentation(item, segment) for item in records):
                records.append({'target_id': segment['id'], 'source_id': segment['source_id'], 'start': segment['start'], 'end': segment['end'],
                                'speaker': segment['speaker'], 'primary_speaker': segment['speaker'], 'tier': 'presentation',
                                'triggers': ['presentation_review'], 'status': 'review'})
            spans = [(part.get('start', segment['start']), part.get('end', segment['end']), part['speaker']) for part in (segment.get('performance') or {}).get('analysis_parts') or [segment]]
            for start, end, speaker in spans:
                trigger = 'degraded_analysis' if speaker == 'unknown' and (segment['id'] in degraded or DEGRADED_HOLD in segment['warning'] or DEGRADED_WARNING in segment['warning']) else 'unresolved_speaker' if speaker == 'unknown' else ''
                if trigger and not covered(segment, start, end):
                    records.append({'target_id': segment['id'], 'source_id': segment['source_id'], 'start': start, 'end': end, 'speaker': 'unknown',
                                    'primary_speaker': 'unknown', 'tier': 'legacy', 'triggers': [trigger], 'status': 'review'})
        current = {**current, 'segments': self.store.generation(gid)['segments']}
        flagged, held = speaker_review_holds(current['segments'], records, analysis.get('speaker_verdicts'))
        _, other_held = speaker_review_holds(current['segments'],
            [item for item in records if set(item.get('triggers') or ()) != {'render_degraded'}], analysis.get('speaker_verdicts'))
        # Provisional takes with audio are intentionally playable. Resuming work
        # elsewhere must not turn them into silent gaps; the open hold still
        # blocks export. Identity/presentation/neighbour holds retain priority.
        playable_degraded = {s['id'] for s in current['segments'] if s['status'] == 'ready' and s['audio_hash']
                             and s['id'] in held and s['id'] not in other_held}
        presentation = {item['target_id'] for item in records if item.get('status') == 'review' and 'presentation_review' in item.get('triggers', [])}
        backend = {sid: key for sid, key in (analysis.get('backend_holds') or {}).items()
                   if any(segment['id'] == sid and segment['status'] == 'review' for segment in current['segments'])}
        reasons = Counter()
        for segment in current['segments']:
            if segment['id']in backend and segment['id']not in held:
                reasons['backend_limit']+=1
            elif segment['id']in held and not preview and segment['id']not in playable_degraded:
                reasons['presentation_review'if segment['id']in presentation else'speaker_review'if segment['id']in flagged else'neighbour']+=1
        with self.store.lock, self.store.db:
            from .current_audiobook import capture_parts
            capture_parts(self.store,current,[s['id']for s in current['segments']
                if s['id']in targets and (s['status']=='review'or PREVIEW_NOTICE in s['warning']or s['id']in held)
                and not (s['id']in playable_degraded or preview and s['id']in held)])
            for segment in current['segments']:
                if segment['id']not in targets:continue
                previewed = PREVIEW_NOTICE in segment['warning']
                if previewed and preview and segment['id'] in held:
                    continue
                if segment['status'] != 'review' and not previewed:
                    continue
                if segment['id'] in backend and segment['id'] not in held:
                    continue
                warning = segment['warning']
                for notice in (HOLD_NOTICE, NEIGHBOUR_NOTICE, PRESENTATION_NOTICE, PREVIEW_NOTICE, MIXED_EDIT_NOTICE, CAPABILITY_NOTICE):
                    warning = warning.replace(notice, '')
                if previewed:
                    self.store.db.execute("UPDATE segments SET status='pending',audio_hash=NULL,duration=0,cues='[]',warning=? WHERE generation_id=? AND id=?",
                                          (' '.join(warning.split()), gid, segment['id']))
                else:
                    self.store.db.execute("UPDATE segments SET status='pending',warning=? WHERE generation_id=? AND id=?", (' '.join(warning.split()), gid, segment['id']))
            for segment in current['segments']:
                if segment['id']not in targets:continue
                if segment['id'] not in held or preview or segment['id'] in playable_degraded:
                    continue
                notice = (PRESENTATION_NOTICE if segment['id'] in presentation
                          else HOLD_NOTICE if segment['id'] in flagged else NEIGHBOUR_NOTICE)
                self.store.db.execute(
                    "UPDATE segments SET status='review',warning=CASE WHEN warning='' THEN ? WHEN instr(warning,?)=0 THEN warning || ' ' || ? ELSE warning END "
                    "WHERE generation_id=? AND id=?", (notice, notice, notice, gid, segment['id']))
            summary = {'flagged_segments': len(flagged), 'held_with_neighbours': len(held), 'reasons': dict(reasons), 'policy': 'preview' if preview else 'hold',
                       'flagged_ids': sorted(flagged), 'held_ids': sorted(held)}
            updated = {**json.loads(self.store.db.execute('SELECT analysis FROM generations WHERE id=?', (gid,)).fetchone()['analysis']),
                       'speaker_resolution': records, 'speaker_resolution_metrics': resolution_metrics(records), 'speaker_review_holds': summary,
                       'backend_holds': backend}
            self.store.db.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps(updated, ensure_ascii=False), gid))
        return held&targets if preview else set()

    def _publish_audio(self,candidate,destination,immutable=True):
        from .util import file_digest
        sha=file_digest(candidate)
        if not immutable:
            # Legacy repair replaces a damaged same-key file and resets every
            # generation sharing its offsets. Higgs candidates remain immutable.
            atomic_write(destination,candidate.read_bytes())
            return sha
        try:os.link(candidate,destination)
        except FileExistsError:
            if file_digest(destination)!=sha:raise SpeechHoldError('Immutable audio key already contains different bytes; it was preserved.')
        return sha

    def _candidate_audio(self,gid,segment,request,proposed_key,spoken,config):
        from .post_processing import mode
        request={**request,'post_processing':mode(config)}
        staging=self.store.root/'render-candidates';staging.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='take-',dir=staging) as folder:
            candidate=Path(folder)/'candidate.wav'
            result=self._synthesize(gid,segment,request,candidate) if spoken else silence(candidate,pause_seconds(request['text']))
            selected_take=result.get('automatic_retake',{}).get('selected_take',request.get('take',0))
            native=result.get('automatic_retake',{}).get('native_text',request['text'])
            from .speech_backend import is_higgs
            key=proposed_key
            if spoken and is_higgs(request['pack']):
                voice=next(v for v in request['pack']['voices'] if v['id']==request['voice_id'])
                from .recovery_decoding import execution_pack
                effective=execution_pack(request['pack'],result.get('automatic_retake',{}).get('decoding_profile'),native)
                key=render_key(effective,{**config,'_pronunciation':request.get('_pronunciation',config.get('_pronunciation'))},native,voice,request['delivery'],request['language'],selected_take,request.get('controls'))
            destination=self.store.root/'audio'/f'{key}.wav'
            self._publish_audio(candidate,destination,immutable=is_higgs(request['pack']))
            # Durable accepted metadata precedes the database selection record.
            write_json(destination.with_suffix('.json'),result)
            return destination,result,key,native,selected_take

    def _composite_audio(self,paths,destination,pauses,immutable=True):
        from .util import file_digest
        staging=self.store.root/'render-candidates';staging.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='join-',dir=staging) as folder:
            candidate=Path(folder)/'composite.wav';result=concatenate(paths,candidate,pauses)
            sha=self._publish_audio(candidate,destination,immutable=immutable)
            return {**result,'composite_proof':{'audio_sha256':sha,
                'parts':[{'key':p.stem,'audio_sha256':file_digest(p)} for p in paths],
                'pauses':pauses}}

    def _prepare_russian_chapter(self,gid,book,chapter_id,recipe,*,work_ids=None,human_names=None):
        """Freeze bounded preparation before rendering this chapter, retaining resume evidence."""
        from .russian_preparation import POLICY,BATCH,validate_config,entry_key,validate_entry,resolve_entry,book_binding,proper_inventory,names_input_digest,module_pins
        validate_config(recipe)
        current=self.store.generation(gid);spec=dict(current['analysis'].get('pronunciation')or{})
        if spec.get('listener_projection_holds'):
            raise AppError('Name pronunciation authority is held; validate the full name baseline before preparation.')
        book_sha=book_binding(book)
        if spec.get('book_sha256')not in(None,book_sha):
            # Source metadata (including heading kind) is part of the immutable
            # book binding; never reuse derived entries from the old binding.
            spec=invalidate_name_preparation(spec)
        source_kinds={b['id']:b.get('kind','paragraph')for c in book['chapters']for b in c['blocks']}
        blocks={b['id']:b['text']for c in book['chapters']for b in c['blocks']}
        from .name_stress import name_spans
        source_names_text='. '.join(blocks.values())
        def relevant(families):
            return {key:value for key,value in families.items()
                if name_spans(source_names_text,{key:value},implicit_single=True)}
        protected_human=relevant(human_names or{})
        previous_human=spec.get('prepared_human_names')
        if previous_human is None:
            previous_human=relevant((spec.get('listener_stress')or{}).get('names',{}))
        if protected_human!=previous_human:
            spec=invalidate_name_preparation(spec)
        units=[]
        for segment in current['segments']:
            if segment['chapter_id']!=chapter_id or segment['status']=='review':continue
            if work_ids is not None and segment['id']not in work_ids:continue
            for part in render_parts(segment)or[]:
                if not audible(part['text']):continue
                units.append({'text':part['text'],'kind':source_kinds[segment['source_id']],'source':{'source_id':segment['source_id'],
                    'source_text':blocks[segment['source_id']],'source_start':part.get('start',segment['start']),
                    'source_end':part.get('end',segment['end'])}})
        # Identical source/text may be shared, but homographs in different source
        # passages never share an occurrence binding.
        units=list({entry_key(u['text'],u['source']):u for u in units}.values())
        source_input_sha=digest({'units':units,'recipe':recipe,'book_sha256':book_sha,'module_pins':module_pins()})
        saved=(spec.get('prepared_chapters')or{}).get(chapter_id)
        if (saved and saved.get('source_input_sha256')==source_input_sha
                and all(entry_key(u['text'],u['source'])in spec.get('prepared_units',{})for u in units)):
            for unit in units:
                resolve_entry(unit['text'],{**spec,'book_sha256':book_sha,'listener_source':unit['source']})
            return spec
        names={key:row['stressed']for key,row in spec.get('names',{}).items()}
        name_evidence=spec.get('proper_noun_preparation')or{}
        if name_evidence:
            if (name_evidence.get('book_sha256')!=book_sha
                or name_evidence.get('sha256')!=digest({k:v for k,v in name_evidence.items()if k!='sha256'})):
                raise AppError('Frozen book-wide proper-noun evidence changed')
        else:
            inventory=proper_inventory(book,spec.get('names',{}))
            from .name_stress import name_spans
            candidates='. '.join([*inventory['capitalized'],*inventory['coinages'],*[row['name']for row in spec.get('names',{}).values()]])
            protected={key:value for key,value in(human_names or{}).items()if name_spans(candidates,{key:value},implicit_single=True)}
            request={'inventory':inventory,'names':names,'names_rows':spec.get('names',{}),'human_names':protected,'book_sha256':book_sha}
            lexicon_input=names_input_digest(request)
            name_evidence=self._call('prepare_russian_names',request)
            if (name_evidence.get('input_sha256')!=lexicon_input or name_evidence.get('book_sha256')!=book_sha
                    or name_evidence.get('sha256')!=digest({k:v for k,v in name_evidence.items()if k!='sha256'})):
                raise AppError('Book-wide proper-noun preparation returned invalid evidence')
        spec={**spec,'proper_nouns':name_evidence['proper_nouns'],'proper_noun_preparation':name_evidence,'book_sha256':book_sha,
            'prepared_human_names':protected_human,
            'source_names':name_evidence.get('source_names',{}),'source_name_receipts':name_evidence.get('source_name_receipts',[])}
        # A later provider hold must not lose the book-wide name decisions.
        if spec!=current['analysis'].get('pronunciation'):
            latest=self.store.generation(gid)['analysis']
            self.store.execute('UPDATE generations SET analysis=? WHERE id=?',
                (json.dumps({**latest,'pronunciation':spec},ensure_ascii=False),gid))
        input_sha=digest({'units':units,'recipe':recipe,'book_sha256':book_sha,
            'pronunciation':{k:spec.get(k)for k in ('names','common','terms','proper_nouns','respelling_sources','source_names','source_name_receipts')}})
        entries={};batches=[]
        cache=self.store.root/'russian-preparation'
        for start in range(0,len(units),BATCH):
            self._check()
            latest=self.store.generation(gid)['analysis']
            self.store.execute('UPDATE generations SET analysis=? WHERE id=?',(json.dumps({**latest,
                'progress':{'stage':'russian_preparation','chapter':chapter_id,'done':start,'total':len(units)}},ensure_ascii=False),gid))
            lexical={k:spec.get(k)for k in ('names','common','terms','proper_nouns','respelling_sources','source_names','source_name_receipts')}
            result=self._call('prepare_russian',{'units':units[start:start+BATCH],'pronunciation':lexical,
                'recipe':recipe,'book_sha256':book_sha,'cache_dir':str(cache)})
            if result.get('policy')!=POLICY or result.get('sha256')!=digest({k:v for k,v in result.items()if k!='sha256'}):
                raise AppError('Russian preparation returned invalid frozen evidence')
            expected={entry_key(u['text'],u['source'])for u in units[start:start+BATCH]}
            if set(result['prepared_units'])!=expected:raise AppError('Russian preparation omitted source parts')
            for key,entry in result['prepared_units'].items():
                entries[key]=validate_entry(entry)
                if entry['book_sha256']!=book_sha:raise AppError('Preparation returned a different source book')
            for unit in units[start:start+BATCH]:
                resolve_entry(unit['text'],{**spec,'prepared_units':entries,'listener_source':unit['source']})
            batches.append({'request_sha256':result['request_sha256'],'sha256':result['sha256']})
        latest=self.store.generation(gid)['analysis']
        prepared={**spec,'policy':POLICY,'prepared_units':{**spec.get('prepared_units',{}),**entries},
            'prepared_chapters':{**spec.get('prepared_chapters',{}),chapter_id:{'input_sha256':input_sha,
                'source_input_sha256':source_input_sha,'batches':batches}}}
        latest={**latest,'pronunciation':prepared};latest.pop('progress',None)
        self.store.execute('UPDATE generations SET analysis=? WHERE id=?',(json.dumps(latest,ensure_ascii=False),gid))
        return prepared

    def _render(self, gid, pack, config, book, language):
        from .speech_backend import is_higgs
        from .post_processing import enabled,disclosure
        higgs = is_higgs(pack)
        gated=higgs and enabled(config)
        if higgs:
            config = {**config, '_speech_language': language}
        current = self.store.generation(gid)
        work_ids=render_work_ids(current)
        pending_ids=work_ids
        if higgs and work_ids is not None:
            # Resume must revalidate this job's completed caches against its
            # resolved execution plan and optional verifier. Retained playback
            # chapters belong to an earlier request and stay untouched.
            owned=set(current['config'].get('render_owned_chapters',current['config']['chapters']))
            extra=set(current['config'].get('render_owned_parts',[]))|{s['id']for s in current['segments']if s['id']in work_ids and s['chapter_id']not in owned}
            if extra!=set(current['config'].get('render_owned_parts',[])):
                current={**current,'config':{**current['config'],'render_owned_parts':sorted(extra)}}
                self.store.execute('UPDATE generations SET config=? WHERE id=?',(json.dumps(current['config'],ensure_ascii=False),gid))
            work_ids=work_ids|extra|{s['id']for s in current['segments']if s['chapter_id']in owned}
        if work_ids is not None:config={**config,'_render_work_ids':sorted(work_ids)}
        if higgs:
            current={**current,'analysis':{**current['analysis'],'post_processing':disclosure(config)}}
            self.store.execute('UPDATE generations SET analysis=? WHERE id=?',
                (json.dumps(current['analysis'],ensure_ascii=False),gid))
        if higgs:
            from .listener_stress import snapshot
            frozen_listener=snapshot(self.store,book,current['cast'])
            pronunciation={**current['analysis'].get('pronunciation',{}),'listener_stress':frozen_listener}
            config={**config,'_pronunciation':pronunciation}
            source_blocks={b['id']:b['text'] for c in book['chapters'] for b in c['blocks']}
        russian_recipe=config.get('russian_preparation') if higgs and language=='Russian' else None
        if higgs and current['analysis'].get('pronunciation'):
            config = {**config, '_pronunciation': pronunciation}
        accepted_chapters={}
        if gated:
            from .chapter_gate import music_released,require,generation_binding
            # Candidate audio remains inaccessible through the normal asset
            # endpoint until its chapter passes; old ready flags aren't a gate.
            for chapter in config['chapters']:
                if work_ids is not None and not any(s['id']in work_ids and s['chapter_id']==chapter for s in current['segments']):continue
                gate=current['analysis'].get('chapter_acceptance',{}).get(chapter) or {}
                chapter_segments=[s for s in current['segments'] if s['chapter_id']==chapter]
                try:
                    require(gate,generation_binding(chapter_segments,pack['_fingerprint'],self.store.root))
                    accepted_chapters[chapter]=music_released(current,chapter,self.store.root)
                except (SpeechHoldError,KeyError,TypeError):accepted_chapters[chapter]=False
                if not accepted_chapters[chapter]:
                    if work_ids is None:
                        self.store.execute("UPDATE segments SET status='pending' WHERE generation_id=? AND chapter_id=? AND status='ready'",(gid,chapter))
                    else:
                        from .current_audiobook import capture_parts
                        targets=[s['id']for s in chapter_segments if s['id']in work_ids and s['status']=='ready']
                        with self.store.lock,self.store.db:
                            capture_parts(self.store,current,targets)
                            self.store.db.executemany("UPDATE segments SET status='pending' WHERE generation_id=? AND id=? AND status='ready'",[(gid,sid)for sid in targets])
        validate_coverage(book, config['chapters'], current['segments'])
        analysis = current['analysis']
        voices = {voice['id']: voice for voice in pack['voices']}
        assigned = {character['id']: character['voice_id'] for character in current['cast']}
        narrators = analysis.get('narrator_voices') or {}
        profile = pack.get('presentation_voices')
        def voice_for(part, segment):
            return routed_voice(pack,analysis,assigned,part,segment)
        hold_input=current if work_ids is None else{**current,'segments':[s for s in current['segments']if s['id']in work_ids]}
        previews = self._holds(gid, pack, config, hold_input, voice_for)
        current = self.store.generation(gid)
        analysis = current['analysis']
        takes = analysis.get('takes') or {}
        automatic_takes = analysis.get('automatic_takes') or {}
        degraded = {item.get('source') for item in analysis.get('degraded', []) if isinstance(item, dict)}
        reuse = not higgs and (language == legacy_language(book) or pack['speech'].get('language_mode', 'book') == 'auto')
        checked_chapter = None
        chapter_ends={a['id'] for a,b in zip(current['segments'],current['segments'][1:])
                      if a['chapter_id']!=b['chapter_id']}
        for segment in current['segments']:
            self._check()
            if work_ids is not None and segment['id']not in work_ids:continue
            if segment['chapter_id'] != checked_chapter:
                if gated and checked_chapter is not None:
                    self._chapter_acceptance(gid,pack,book,{**config,'chapters':[checked_chapter]})
                pack_unchanged(pack)
                checked_chapter = segment['chapter_id']
                if russian_recipe and (pending_ids is None or any(s['id']in pending_ids and s['chapter_id']==checked_chapter for s in current['segments'])):
                    prepared=self._prepare_russian_chapter(gid,book,checked_chapter,russian_recipe,
                        work_ids=work_ids,human_names=frozen_listener.get('names',{}))
                    config={**config,'_pronunciation':{**prepared,'listener_stress':frozen_listener}}
            if segment['status'] == 'review':
                continue
            if higgs and segment['id'] in chapter_ends and audible(segment['spoken']):
                performance={**(segment.get('performance') or {})}
                performance.update(chapter_transition_pause=TRANSITION_POLICY['chapter_seconds'],
                    pause_after_seconds=max(performance.get('pause_after_seconds') or 0,TRANSITION_POLICY['chapter_seconds']))
                segment={**segment,'performance':performance}
                self.store.execute('UPDATE segments SET performance=? WHERE generation_id=? AND id=?',
                    (json.dumps(performance,ensure_ascii=False),gid,segment['id']))
            parts = render_parts(segment)
            if parts is None:
                self._hold(gid, segment, MIXED_EDIT_NOTICE)
                continue
            if higgs:
                from .listener_stress import apply
                corrected=[]
                for part in parts:
                    if russian_recipe:
                        # Frozen automatic input binds the original spoken text;
                        # human corrections are authoritative at the final layer.
                        native,evidence=part['text'],{}
                    else:
                        native,evidence=apply(part['text'],frozen_listener,source_id=segment['source_id'],
                            source_text=source_blocks[segment['source_id']],
                            source_start=part.get('start',segment['start']),source_end=part.get('end',segment['end']))
                    binding={'source_id':segment['source_id'],'source_text':source_blocks[segment['source_id']],
                        'source_start':part.get('start',segment['start']),'source_end':part.get('end',segment['end'])}
                    part_spec=config['_pronunciation']
                    if russian_recipe and audible(part['text']):
                        from .russian_preparation import entry_key
                        unit_key=entry_key(part['text'],binding)
                        part_spec={**part_spec,'prepared_chapters':{},'prepared_units':{unit_key:part_spec['prepared_units'][unit_key]}}
                    corrected.append({**part,'text':native,'_listener_stress':evidence,
                        '_pronunciation':{**part_spec,'listener_source':binding}})
                parts=corrected
            take = takes.get(segment['id'], 0)
            plans, controls, part_takes = [], {}, {}
            try:
                for index, part in enumerate(parts):
                    voice, channel_warning, *_ = voice_for(part, segment)
                    spoken = audible(part['text'])
                    frame=(analysis.get('narrative_frames') or {}).get('by_source',{}).get(segment['source_id'])
                    fixed_readout=frame and frame.get('register')=='mechanical'
                    delivery = applied_delivery(pack, voice, 'neutral' if fixed_readout else part['delivery']) if spoken else 'neutral'
                    controls[index] = render_controls(pack, part, segment)[0] if spoken else {}
                    actual_take=take
                    automatic=automatic_takes.get(segment['id'],{}).get(str(index),{})
                    decoding_profile=None
                    if higgs and enabled(config) and automatic.get('requested_take')==take and type(automatic.get('selected_take')) is int:
                        actual_take=max(take,automatic['selected_take'])
                        decoding_profile=automatic.get('decoding_profile')
                        if automatic.get('spoken_text_plan'):
                            from .spoken_edits import apply_spoken_edit
                            from .spoken_pronunciation import synthesis_text
                            baseline,_=synthesis_text(part['text'],part.get('_pronunciation',config.get('_pronunciation')))
                            bound=(automatic.get('original_text_sha256')==digest(part['text'])
                                and automatic.get('pack_fingerprint')==pack['_fingerprint']
                                and automatic.get('voice_id')==voice['id']
                                and automatic.get('speaker')==segment['speaker'])
                            if bound:
                                from .util import file_digest
                                diagnosis=Path(automatic.get('diagnosis_path','')).resolve()
                                bound=(diagnosis.is_relative_to((self.store.root/'automatic-word-recovery').resolve())
                                    and diagnosis.is_file() and file_digest(diagnosis)==automatic['spoken_text_plan'].get('evidence',{}).get('diagnosis_sha256'))
                                if bound:
                                    from .word_recovery import recipe_bound
                                    bound=recipe_bound(diagnosis,segment['id'],baseline,automatic.get('native_text'))
                            if bound:
                                try:native,_=apply_spoken_edit(baseline,automatic['spoken_text_plan'])
                                except ValueError:bound=False
                            if bound and native==automatic.get('native_text'):
                                part={**part,'text':native,'_source_text':part['text'],'_spoken_repair':automatic}
                            else:actual_take=take;decoding_profile=None
                    if decoding_profile:part={**part,'_decoding_profile':decoding_profile}
                    from .recovery_decoding import execution_pack
                    effective_pack=execution_pack(pack,decoding_profile,part['text']) if higgs else pack
                    plans.append((part, voice, delivery, spoken, channel_warning,
                                  render_key(effective_pack, {**config,'_pronunciation':part.get('_pronunciation',config.get('_pronunciation'))}, part['text'], voice, delivery, language, actual_take, controls[index])))
                    part_takes[index]=actual_take
            except (GenerationCapError, SpeechHoldError) as error:
                if isinstance(error, SpeechHoldError):
                    self._render_hold(gid, segment, str(error))
                    continue
                self._backend_hold(gid, segment, digest({'pack': pack['_fingerprint'], 'take': take, 'id': segment['id']}), str(error))
                continue
            pauses = segment_timeline(segment, parts)
            single = len(plans) == 1 and not pauses
            composite = digest({'schema': 3, 'parts': [plan[5] for plan in plans], **({'pauses': pauses} if pauses else {})})
            expected_key = plans[0][5] if single else composite
            key = expected_key if higgs else segment['audio_hash'] or expected_key
            result = self._stored(key)
            if higgs and result is not None and not self._higgs_cached(result, plans, pack, config):
                result = None
            if result is None and single and reuse and (found := self._legacy(config, book, segment, plans[0][1]['id'], takes)):
                key, result = found
            if result is None and not single and not higgs:
                key = composite
                result = self._stored(key)
            wav = self.store.root / 'audio' / f'{key}.wav'
            meta = wav.with_suffix('.json')
            fresh = result is None or segment['status'] != 'ready'
            if result is None:
                self.store.execute("UPDATE segments SET status='rendering' WHERE generation_id=? AND id=?", (gid, segment['id']))
                try:
                    pieces = []
                    for index, (part, voice, delivery, spoken, _, part_key) in enumerate(plans):
                        piece = wav if single else self.store.root / 'audio' / f'{part_key}.wav'
                        found = None if single else self._stored(part_key)
                        if higgs and found is not None and not self._higgs_cached(found, [plans[index]], pack, config):
                            found = None
                        if found is None and not single and len(plans) == 1 and reuse and (legacy := self._legacy(config, book, segment, voice['id'], takes)):
                            piece, found = self.store.root / 'audio' / f'{legacy[0]}.wav', legacy[1]
                        if found is None:
                            actual_take=part_takes[index]
                            actual_controls=controls[index]
                            piece,found,selected_key,native,selected_take = self._candidate_audio(gid, segment, {'pack': pack, 'device': config['device'], 'voice_id': voice['id'], 'text': part['text'],
                                                                    '_source_text':part.get('_source_text',part['text']),
                                                                    **({'_pronunciation':part.get('_pronunciation',config['_pronunciation'])} if config.get('_pronunciation') else {}),
                                                                    '_decoding_profile':part.get('_decoding_profile'),
                                                                    **({'_spoken_repair':part['_spoken_repair']} if part.get('_spoken_repair') else {}),
                                                                    **({'resolved_speech': config['resolved_speech'], 'take': actual_take} if higgs else {}),
                                                                    'delivery': delivery, 'language': language, **({'controls': actual_controls} if actual_controls else {})},
                                                     key if single else part_key,spoken,config)
                            plans[index]=({**part,'text':native},*plans[index][1:5],selected_key)
                            if higgs and selected_take!=actual_take:
                                with self.store.lock,self.store.db:
                                    row=self.store.db.execute('SELECT analysis FROM generations WHERE id=?',(gid,)).fetchone()
                                    saved=json.loads(row['analysis']);auto=saved.get('automatic_takes') or {}
                                    selected={**auto.get(segment['id'],{}),str(index):{**found['automatic_retake'],'requested_take':take}}
                                    self.store.db.execute('UPDATE generations SET analysis=? WHERE id=?',
                                        (json.dumps({**saved,'automatic_takes':{**auto,segment['id']:selected}},ensure_ascii=False),gid))
                            if not single:
                                write_json(piece.with_suffix('.json'), {**found, 'reset_cues': False})
                        pieces.append((piece, found))
                    if higgs:
                        key=plans[0][5] if single else digest({'schema':3,'parts':[p[5] for p in plans],**({'pauses':pauses} if pauses else {})})
                        wav=self.store.root/'audio'/f'{key}.wav';meta=wav.with_suffix('.json')
                    result = pieces[0][1] if single else {**self._composite_audio([piece for piece, _ in pieces], wav, pauses,immutable=higgs),'reset_cues':True,
                                                         **({'synthesis_parts': [found.get('synthesis') for _, found in pieces],
                                                             'audio_checks_parts': [found.get('audio_checks') for _, found in pieces],
                                                             'word_checks_parts': [found.get('word_check') for _, found in pieces]} if higgs else {}),
                                                         'warning': append_warning('', *(found.get('warning', '') for _, found in pieces))}
                except (GenerationCapError, SpeechHoldError) as error:
                    if isinstance(error, SpeechHoldError):
                        self._render_hold(gid, segment, str(error))
                        continue
                    self._backend_hold(gid, segment, key, str(error))
                    continue
                write_json(meta, result)
            base = append_warning(DEGRADED_WARNING if segment['id'] in degraded and segment['speaker'] == 'unknown' else '', transition_warning(segment)) if fresh else segment['warning']
            fallback = UNRESOLVED_WARNING if any(part['speaker'] == 'unknown' for part in parts) and DEGRADED_WARNING not in base else ''
            reference = REFERENCE_FALLBACK if not higgs and any(spoken and pack['speech']['variant'] == 'clone' and delivery != part['delivery']
                                                  for part, _, delivery, spoken, _, _ in plans) else ''
            notice = PREVIEW_NOTICE if segment['id'] in previews else ''
            capability_notice = CAPABILITY_NOTICE if any(
                spoken and render_controls(pack, part, segment)[1]
                for part, _, _, spoken, _, _ in plans) else ''
            warning = append_warning(base, fallback, reference, *(plan[4] for plan in plans), capability_notice,
                                     notice, result.get('warning', ''))
            with self.store.lock:
                with self.store.transaction():
                    self._check()
                    latest=self.store.db.execute('SELECT analysis FROM generations WHERE id=?',(gid,)).fetchone()
                    if (json.loads(latest['analysis']).get('takes')or{}).get(segment['id'],0)!=take:
                        raise AppError('A newer correction replaced this pending audio request. Resume the current audiobook.')
                    if result.get('reset_cues'):
                        self.store.db.execute("UPDATE segments SET duration=?,cues='[]' WHERE audio_hash=?",
                                              (result['duration'],key))
                        self.store.db.execute("UPDATE segments SET cues='[]' WHERE generation_id=? AND id=?",
                                              (gid, segment['id']))
                    self.store.db.execute("UPDATE segments SET status=?,audio_hash=?,duration=?,warning=? WHERE generation_id=? AND id=?",
                                          ('pending' if gated and not (accepted_chapters.get(segment['chapter_id']) and
                                              segment['status']=='ready' and segment['audio_hash']==key) else 'ready',
                                           key, result['duration'], warning, gid, segment['id']))
                    if not single:
                        self.store.db.execute('UPDATE segments SET performance=? WHERE generation_id=? AND id=?',
                                              (json.dumps({**(segment.get('performance') or {}), 'render_parts': [plan[5] for plan in plans]}, ensure_ascii=False), gid, segment['id']))
                    elif (segment.get('performance') or {}).get('render_parts'):
                        self.store.db.execute('UPDATE segments SET performance=? WHERE generation_id=? AND id=?',
                            (json.dumps({k:v for k,v in segment['performance'].items() if k!='render_parts'},ensure_ascii=False),gid,segment['id']))
                    self.store.complete_replacements(gid)
                if result.get('reset_cues'):
                    write_json(meta, {**result, 'reset_cues': False})
            self._check()
        if checked_chapter is not None:
            pack_unchanged(pack)
            if gated:self._chapter_acceptance(gid,pack,book,{**config,'chapters':[checked_chapter]})
        return work_ids

    def _chapter_acceptance(self,gid,pack,book,config):
        from .post_processing import enabled
        if not enabled(config):return
        from .chapter_gate import measure,report,audio_binding
        from .util import file_digest
        current=self.store.generation(gid)
        target_ids=set(config['_render_work_ids'])if'_render_work_ids'in config else render_work_ids(current)
        for chapter in book['chapters']:
            if chapter['id'] not in config['chapters']:continue
            segments=[s for s in current['segments'] if s['chapter_id']==chapter['id']]
            rows=[];takes={};scene=0;scenes={}
            for block in chapter['blocks']:
                if block['kind']=='separator':scene+=1
                scenes[block['id']]=chapter['id']+':'+str(scene)
            for segment in segments:
                key=segment['audio_hash'];path=self.store.root/'audio'/f'{key}.wav' if key else None
                metadata=json.loads(path.with_suffix('.json').read_text('utf-8')) if path and path.with_suffix('.json').exists() else {}
                plans=metadata.get('synthesis_parts',[metadata.get('synthesis')])
                audits=metadata.get('word_checks_parts',[metadata.get('word_check')])
                part_keys=(segment.get('performance') or {}).get('render_parts') or [key]
                parts=render_parts(segment) or []
                for index,part in enumerate(parts):
                    if not audible(part['text']):continue
                    part_key=part_keys[index] if index<len(part_keys) else None
                    piece=self.store.root/'audio'/f'{part_key}.wav' if part_key else None
                    available=segment['status']!='review' and piece and piece.exists()
                    synthesis=plans[index] if index<len(plans) else {}
                    checks=audits[index] if index<len(audits) else {}
                    rid=segment['id']+':'+str(index)
                    rows.append({'id':rid,'path':piece if available else None,
                        'audio_sha256':file_digest(piece) if available else None,'synthesis':synthesis,
                        'word_check':checks,'text':part['text'],'speaker':part['speaker'],
                        'language':config.get('_speech_language'),
                        'source_id':segment['source_id'],'scene':scenes[segment['source_id']],
                        'presentation_route':(part.get('performance') or {}).get('higgs_presentation_route','direct'),
                        'direction':(part.get('performance') or {}).get('higgs_direction')})
                takes[segment['id']]={'key':key,'audio':str(path) if path else None,
                    'audio_sha256':file_digest(path) if path and path.exists() else None}
            bound_segments=[{k:s[k] for k in ('id','source_id','start','end','spoken','speaker','performance')} for s in segments]
            binding=audio_binding(digest({'segments':bound_segments,'pack':pack['_fingerprint']}),takes)
            checks=measure(rows,pack.get('cast_pair_reviews',[]),pack['files'][pack['speech']['model']])
            gate=report(binding,checks,pack['speech'].get('purpose')=='evaluation_only')
            with self.store.lock,self.store.db:
                latest_generation=self.store.generation(gid)
                latest=latest_generation['analysis']
                latest_segments=[s for s in latest_generation['segments'] if s['chapter_id']==chapter['id']]
                from .chapter_gate import generation_binding
                if generation_binding(latest_segments,pack['_fingerprint'],self.store.root)!=binding:
                    raise SpeechHoldError('Chapter changed during verification; no stale audio was published.')
                if not gate['passed']:
                    from .current_audiobook import capture_parts
                    capture_parts(self.store,latest_generation,[s['id']for s in latest_segments if target_ids is None or s['id']in target_ids])
                self.store.db.execute('UPDATE generations SET analysis=? WHERE id=?',
                    (json.dumps({**latest,'chapter_acceptance':{**latest.get('chapter_acceptance',{}),chapter['id']:gate}},ensure_ascii=False),gid))
                if gate['passed']:
                    for segment in segments:
                        if target_ids is not None and segment['id']not in target_ids:continue
                        if segment['audio_hash']:
                            self.store.db.execute("UPDATE segments SET status='ready' WHERE generation_id=? AND id=? AND status='pending' AND audio_hash=?",(gid,segment['id'],segment['audio_hash']))
                else:
                    # Candidate audio remains pending behind this gate. Do not
                    # turn healthy passages into terminal speaker-review holds.
                    if target_ids is None:
                        self.store.db.execute("UPDATE segments SET status='pending' WHERE generation_id=? AND chapter_id=? AND status='ready'",(gid,chapter['id']))
                    else:
                        self.store.db.executemany("UPDATE segments SET status='pending' WHERE generation_id=? AND id=? AND status='ready'",
                            [(gid,s['id'])for s in latest_segments if s['id']in target_ids])

    def _hold(self, gid, segment, notice):
        self.store.execute(
            "UPDATE segments SET status='review',warning=CASE WHEN warning='' THEN ? WHEN instr(warning,?)=0 THEN warning || ' ' || ? ELSE warning END "
            "WHERE generation_id=? AND id=?", (notice, notice, notice, gid, segment['id']))

    def _backend_hold(self, gid, segment, key, message):
        self._hold(gid, segment, BACKEND_NOTICE)
        with self.store.lock, self.store.db:
            analysis = json.loads(self.store.db.execute('SELECT analysis FROM generations WHERE id=?', (gid,)).fetchone()['analysis'])
            holds = {**(analysis.get('backend_holds') or {}), segment['id']: key}
            self.store.db.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps({**analysis, 'backend_holds': holds}, ensure_ascii=False), gid))

    def _render_hold(self, gid, segment, message):
        self._hold(gid, segment, message)
        with self.store.lock, self.store.db:
            analysis = json.loads(self.store.db.execute('SELECT analysis FROM generations WHERE id=?', (gid,)).fetchone()['analysis'])
            records = [r for r in analysis.get('speaker_resolution', []) if not (r.get('target_id') == segment['id'] and 'render_degraded' in r.get('triggers', []))]
            records.append({'target_id': segment['id'], 'source_id': segment['source_id'], 'start': segment['start'], 'end': segment['end'],
                            'speaker': segment['speaker'], 'primary_speaker': segment['speaker'], 'tier': 'backend',
                            'triggers': ['render_degraded'], 'status': 'review', 'reason': message,
                            **({'automatic_word_recovery':{'source_text_sha256':digest(segment['text'])}}
                               if message.startswith(('Automatic quality retakes exhausted','Automatic word diagnosis failed',
                                                      'Immutable audio key already contains different bytes')) else {})})
            self.store.db.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps({**analysis, 'speaker_resolution': records}, ensure_ascii=False), gid))

    def _retry_word_holds(self,gid):
        with self.store.lock,self.store.db:
            return self._retry_word_holds_locked(gid)

    def _retry_word_holds_locked(self,gid):
        current=self.store.generation(gid);analysis=current['analysis']
        records=analysis.get('speaker_resolution',[])
        rounds=dict(analysis.get('automatic_word_rounds') or {})
        takes=dict(analysis.get('takes') or {});retry=[]
        automatic=dict(analysis.get('automatic_takes') or {})
        for segment in current['segments']:
            matching=[r for r in records if r.get('target_id')==segment['id'] and r.get('status')=='review']
            if segment['status']!='review' or not matching or rounds.get(segment['id'],0)>=2:continue
            if not all(set(r.get('triggers',[]))=={'render_degraded'} and
                       r.get('automatic_word_recovery',{}).get('source_text_sha256')==digest(segment['text']) for r in matching):continue
            retry.append(segment);rounds[segment['id']]=rounds.get(segment['id'],0)+1
            previous_take=takes.get(segment['id'],0);takes[segment['id']]=previous_take+9
            automatic[segment['id']]={index:{**record,'requested_take':previous_take+9,
                'selected_take':max(previous_take,record.get('selected_take',previous_take))+9}
                for index,record in automatic.get(segment['id'],{}).items()}
        if not retry:return False
        ids={s['id'] for s in retry}
        history=[*analysis.get('automatic_word_hold_history',[]),*[r for r in records if r.get('target_id') in ids]]
        with self.store.lock,self.store.db:
            for segment in retry:
                warning=segment['warning']
                for record in records:
                    if record.get('target_id')==segment['id']:warning=warning.replace(record.get('reason',''),'')
                self.store.db.execute("UPDATE segments SET status='pending',warning=? WHERE generation_id=? AND id=? AND status='review'",(' '.join(warning.split()),gid,segment['id']))
            updated={**analysis,'takes':takes,'automatic_takes':automatic,'automatic_word_rounds':rounds,'automatic_word_hold_history':history,
                'speaker_resolution':[r for r in records if r.get('target_id') not in ids]}
            self.store.db.execute('UPDATE generations SET analysis=? WHERE id=?',(json.dumps(updated,ensure_ascii=False),gid))
        return True

    def _preserve_checkpoint(self, gid, analysis, cast):
        segments = self.store.generation(gid)['segments']
        name = f'{gid}-{digest([analysis, cast, segments])[:16]}.json'
        path = self.store.root / 'superseded-analysis' / name
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            atomic_write(path, json.dumps({'generation_id': gid, 'preserved': now(), 'analysis': analysis, 'cast': cast, 'segments': segments},
                                          ensure_ascii=False).encode('utf-8'))
        return {'file': f'superseded-analysis/{name}', 'casting_schema': analysis.get('casting_schema'), 'analysis_contract': analysis.get('analysis_contract')}

    def _stored(self, key):
        wav = self.store.root / 'audio' / f'{key}.wav'
        if wav.exists() and wav.with_suffix('.json').exists():
            with contextlib.suppress(AppError, ValueError, OSError):
                result = accepted_audio(wav)
                metadata = json.loads(wav.with_suffix('.json').read_text('utf-8'))
                return {**result, **{field: metadata[field] for field in ('synthesis', 'audio_checks', 'word_check',
                        'synthesis_parts', 'audio_checks_parts', 'word_checks_parts',
                        'automatic_retake', 'composite_proof', 'warning') if field in metadata}}
        return None

    def _higgs_cached(self, result, plans, pack, config):
        from .higgs_backend import synthesis_plan
        from .speech_quality import transcript_check
        from .post_processing import enabled
        checked=enabled(config)
        retake=result.get('automatic_retake')or{}
        if not checked and (retake.get('selected_take',retake.get('requested_take'))!=retake.get('requested_take')
                or retake.get('decoding_profile') or retake.get('spoken_text_plan')):return False
        syntheses = result.get('synthesis_parts', [result.get('synthesis')])
        audits = result.get('word_checks_parts', [result.get('word_check')])
        if not isinstance(syntheses, list) or not isinstance(audits, list) or len(syntheses) != len(plans) or len(audits) != len(plans):
            return False
        if 'synthesis_parts' in result:
            from .util import file_digest
            proof=result.get('composite_proof') or {}
            if proof.get('audio_sha256')!=result['sha256']:return False
            pieces=proof.get('parts')
            if not isinstance(pieces,list) or len(pieces)!=len(plans):return False
            for (_,_,_,spoken,_,key),piece,audit in zip(plans,pieces,audits):
                path=self.store.root/'audio'/f'{key}.wav'
                if piece.get('key')!=key or not path.is_file() or file_digest(path)!=piece.get('audio_sha256'):return False
                if spoken and (not isinstance(audit,dict) or audit.get('audio_sha256')!=piece['audio_sha256']):return False
        if checked:
            prefix = pack['speech']['verifier']['path'].rstrip('/') + '/'
            verifier = digest({k: v for k, v in pack['files'].items() if k.startswith(prefix)})
        for (part, voice, delivery, spoken, _, key), synthesis, audit in zip(plans, syntheses, audits):
            if not spoken:
                continue
            from .spoken_pronunciation import synthesis_text,cache_binding
            native_text,pronunciation=synthesis_text(part['text'],part.get('_pronunciation',config.get('_pronunciation')))
            if not isinstance(synthesis, dict) or not isinstance(audit, dict):return False
            if checked and not isinstance(audit.get('heard'), str):
                if not (isinstance(synthesis,dict) and synthesis.get('backend')=='nonverbal_asset' and isinstance(audit,dict)
                        and audit.get('status')=='nonverbal_not_spoken_text' and
                        audit.get('asset_sha256')==synthesis.get('asset_sha256') and
                        audit.get('event_kind')==synthesis.get('event',{}).get('kind')):
                    return False
            if synthesis.get('backend')=='nonverbal_asset':
                if (audit.get('status')!='nonverbal_not_spoken_text'
                    or audit.get('asset_sha256')!=synthesis.get('asset_sha256')
                    or audit.get('event_kind')!=synthesis.get('event',{}).get('kind')):return False
                saved_plan={k:v for k,v in synthesis.items() if k!='plan_sha256'}
                if synthesis.get('plan_sha256')!=digest(saved_plan) or key!=digest({'schema':5,
                        'language':config.get('_speech_language'),'plan':saved_plan,**cache_binding(pronunciation)}):return False
                if 'synthesis_parts' not in result and audit.get('audio_sha256')!=result['sha256']:return False
                continue
            from .word_verification import verified
            if checked and not verified(native_text,audit,audit.get('audio_sha256'),verifier,config.get('_speech_language')):
                return False
            if 'synthesis_parts' not in result and audit.get('audio_sha256') != result['sha256']:
                return False
            # Cache key already binds controls/take. The saved plan must also
            # reproduce that key, so removing/changing provenance forces a retake.
            saved_plan = {k: v for k, v in synthesis.items() if k != 'plan_sha256'}
            if synthesis.get('plan_sha256') != digest(saved_plan):
                return False
            if key != digest({'schema': 5, 'language': config.get('_speech_language'), 'plan': saved_plan,
                    **cache_binding(pronunciation)}):
                # Language is stored by the caller below; do not reuse a plan
                # under an unknown or changed language conditioning.
                return False
        return True

    def _legacy(self, config, book, segment, voice_id, takes):
        if audible(segment['spoken']) and not takes.get(segment['id'], 0):
            key = legacy_key(config, book, segment, voice_id)
            if (found := self._stored(key)) is not None:
                return key, {**found, 'warning': ''}
        return None

    def _speak(self, request, wav):
        from .speech_backend import is_higgs
        from .post_processing import enabled,disclosure
        if is_higgs(request['pack']):
            if not enabled(request)and(request.get('_decoding_profile')or request.get('_spoken_repair')):
                raise SpeechHoldError('Automatic recovery input is disabled for this one-shot generation.')
            from .recovery_decoding import execution_pack
            request={**request,'pack':execution_pack(request['pack'],request.get('_decoding_profile'),request['text'])}
            from .spoken_pronunciation import synthesis_text
            text,pronunciation=synthesis_text(request['text'],request.get('_pronunciation'))
            request={**request,'text':text}
        result = self._guarded('render', {**request, 'output': str(wav)})
        if not isinstance(result, dict) or not isinstance(result.get('warning', ''), str):
            raise AppError('Inference returned invalid audio metadata.')
        from .speech_backend import is_higgs
        if is_higgs(request['pack']):
            from .util import file_digest
            from .speech_quality import transcript_check
            from .higgs_backend import synthesis_plan
            voice = next(v for v in request['pack']['voices'] if v['id'] == request['voice_id'])
            planned = synthesis_plan(request['pack'], voice, request['text'], request['delivery'], request.get('controls'), request['resolved_speech'], request.get('take', 0))
            if result.get('synthesis') != {**planned, 'plan_sha256': digest(planned)}:
                raise SpeechHoldError('Higgs did not verify the requested voice, text and controls.')
            if planned.get('backend')=='nonverbal_asset':
                audit=result.get('word_check') or {}
                if (audit.get('status')!='nonverbal_not_spoken_text' or audit.get('audio_sha256')!=file_digest(wav)
                    or audit.get('asset_sha256')!=planned['asset_sha256']):
                    raise SpeechHoldError('Sound-event provenance does not bind to the actual audio.')
                return {**read_audio(wav),'warning':result.get('warning',''),
                        'synthesis':result['synthesis'],'audio_checks':result['audio_checks'],'word_check':audit}
            if not enabled(request):
                return {**read_audio(wav),'warning':result.get('warning',''),
                    'synthesis':result['synthesis'],'phrase_pronunciation':pronunciation,
                    'audio_checks':{**result.get('audio_checks',{}),'spoken_words':'not_checked'},
                    'word_check':{'status':'not_checked','audio_sha256':file_digest(wav)},
                    'post_processing':disclosure(request)}
            audit = self._guarded('verify_audio', {'pack': request['pack'], 'audio': str(wav), 'text': request['text'], 'language': request['language']})
            if not isinstance(audit, dict) or audit.get('audio_sha256') != file_digest(wav) or not isinstance(audit.get('heard'), str):
                raise SpeechHoldError('Independent word check did not bind to this exact take.')
            prefix = request['pack']['speech']['verifier']['path'].rstrip('/') + '/'
            verifier_sha256 = digest({k: v for k, v in request['pack']['files'].items() if k.startswith(prefix)})
            checked = transcript_check(request['text'], audit['heard'], audit['audio_sha256'], verifier_sha256)
            from .word_verification import reconcile,verified
            reconciled=reconcile(request['text'],audit,request['language'])
            if reconciled is not None:audit=reconciled
            if not verified(request['text'],audit,file_digest(wav),verifier_sha256,request['language']):
                if checked==audit:
                    from .word_recovery import WordCheckFailure
                    evidence=self.store.root/'word-recovery-evidence'/digest({'synthesis':planned,'audio':audit['audio_sha256']})
                    evidence.parent.mkdir(parents=True,exist_ok=True)
                    snapshot=evidence.with_suffix('.wav')
                    if not snapshot.exists():atomic_write(snapshot,wav.read_bytes())
                    if file_digest(snapshot)!=audit['audio_sha256']:raise SpeechHoldError('Word-recovery evidence changed.')
                    write_json(evidence.with_suffix('.json'),{'word_check':audit,'synthesis':planned,'audio':snapshot.name,
                        'human_approval':False,'publication_allowed':False})
                    wav.unlink(missing_ok=True)
                    raise WordCheckFailure('Independent transcript differs from requested words.',audit,planned,snapshot)
                wav.unlink(missing_ok=True)
                raise SpeechHoldError('Independent transcript differs from requested words; take is held for verification or retake.')
            result['word_check'] = audit
            result['phrase_pronunciation'] = pronunciation
            result['audio_checks'] = {**result.get('audio_checks', {}), 'spoken_words': 'asr_exact_match'}
        return {**read_audio(wav), 'warning': result.get('warning', ''),
                **{key: result[key] for key in ('synthesis', 'audio_checks', 'word_check', 'phrase_pronunciation') if key in result}}

    def _intact(self, pack, wav):
        try:
            pack_unchanged(pack, quick=True)
        except AppError:
            with contextlib.suppress(OSError):
                wav.unlink(missing_ok=True)
            raise

    def _check_immutable_candidate(self,request,wav):
        from .util import file_digest
        voice=next(v for v in request['pack']['voices'] if v['id']==request['voice_id'])
        from .recovery_decoding import execution_pack
        key=render_key(execution_pack(request['pack'],request.get('_decoding_profile'),request['text']),request,request['text'],voice,request['delivery'],
                       request['language'],request.get('take',0),request.get('controls'))
        published=self.store.root/'audio'/f'{key}.wav'
        if published.exists() and file_digest(published)!=file_digest(wav):
            from .post_processing import enabled
            raise SpeechHoldError('This synthesis key already has different audio; '+
                ('trying a new seed.'if enabled(request)else'the one-shot output is held without an automatic retake.'))

    def _synthesize(self, gid, segment, request, wav):
        from .speech_backend import is_higgs
        if is_higgs(request['pack']):
            from .post_processing import enabled,disclosure
            if not enabled(request):
                self._check()
                try:
                    result=self._speak(request,wav)
                    self._intact(request['pack'],wav)
                    self._check_immutable_candidate(request,wav)
                    return {**result,'reset_cues':True,'post_processing':disclosure(request)}
                except (AppError,OSError):
                    self._intact(request['pack'],wav)
                    raise
            failures=[];word_failures=[];cap_failure=None;requested_take=request.get('take',0)
            for offset in range(3):
                self._check()
                candidate={**request,'take':requested_take+offset}
                try:
                    result=self._speak(candidate,wav)
                    if not plausible(result['duration'],request['text']):
                        raise SpeechHoldError('Higgs output has an implausible speaking rate.')
                    self._intact(request['pack'],wav)
                    self._check_immutable_candidate(candidate,wav)
                    return {**result,'reset_cues':True,'automatic_retake':{'requested_take':requested_take,
                        **request.get('_spoken_repair',{}),
                        'selected_take':requested_take+offset,'failures':failures,
                        'decoding_profile':request.get('_decoding_profile'),
                        'policy':'source_bound_word_recovery_v2' if request.get('_spoken_repair') else 'preserve_words_voice_controls_v1'}}
                except (SpeechHoldError,GenerationCapError) as error:
                    from .word_recovery import WordCheckFailure
                    if isinstance(error,WordCheckFailure):word_failures.append(error)
                    if isinstance(error,GenerationCapError):cap_failure=error
                    self._intact(request['pack'],wav)
                    with contextlib.suppress(OSError):wav.unlink(missing_ok=True)
                    failures.append(str(error))
                except EngineTransportError as error:
                    self._intact(request['pack'],wav);failures.append(str(error));self._replace()
            if word_failures and not request.get('_decoding_profile'):
                # A different remedy after seed-only attempts: reduce lexical
                # sampling variability, while keeping the whole sentence and palette.
                for offset,profile in enumerate(('careful_full','conservative_full','balanced_full'),3):
                    self._check();candidate={**request,'take':requested_take+offset,'_decoding_profile':profile}
                    try:
                        result=self._speak(candidate,wav)
                        if not plausible(result['duration'],request['text']):raise SpeechHoldError('Recovered speech has an implausible speaking rate.')
                        self._intact(request['pack'],wav);self._check_immutable_candidate(candidate,wav)
                        return {**result,'reset_cues':True,'automatic_retake':{
                            **request.get('_spoken_repair',{}),'requested_take':requested_take,'selected_take':requested_take+offset,
                            'failures':failures,'policy':'source_word_decoding_recovery_v1','decoding_profile':profile}}
                    except (SpeechHoldError,GenerationCapError) as error:
                        from .word_recovery import WordCheckFailure
                        if isinstance(error,WordCheckFailure):word_failures.append(error)
                        self._intact(request['pack'],wav)
                        with contextlib.suppress(OSError):wav.unlink(missing_ok=True)
                        failures.append(str(error))
                    except EngineTransportError as error:
                        self._intact(request['pack'],wav);failures.append(str(error));self._replace()
            if word_failures:
                from .word_recovery import SYSTEM,validate,schema
                from .util import file_digest
                from .spoken_pronunciation import synthesis_text
                from .spoken_edits import apply_spoken_edit
                generation=self.store.generation(gid);book=self.store.book(generation['book_id'])
                chapter=next((c for c in book['chapters'] if c['id']==segment['chapter_id']),None)
                paragraph=next((b['text'] for b in chapter['blocks'] if b['id']==segment['source_id']),None) if chapter else None
                if paragraph is None:raise SpeechHoldError('Word-recovery source block is missing; no audio was published.')
                quote=paragraph[segment['start']:segment['end']]
                if quote!=segment['text']:raise SpeechHoldError('Word-recovery source range changed; no audio was published.')
                original_text=request.get('_source_text',request['text'])
                baseline,_=synthesis_text(original_text,request.get('_pronunciation'));last=word_failures[-1]
                packet={'source_sha256':digest({'book_id':book['id'],'source_id':segment['source_id'],'paragraph':paragraph}),
                    'citation_contract':'source-slice-v3','units':[{'unit_id':segment['id'],'spoken':baseline,'source_quote':quote,
                        'paragraph':paragraph,'heard':last.audit['heard'],'audio_sha256':last.audit['audio_sha256'],
                        'synthesis_key':digest(last.synthesis),'voice':request['voice_id'],'mood':request['delivery'],
                        'prior_attempts':[{'heard':f.audit['heard'],'audio_sha256':f.audit['audio_sha256'],
                            'synthesis_key':digest(f.synthesis),'evidence_path':f.evidence_path} for f in word_failures]}]}
                destination=self.store.root/'automatic-word-recovery'/digest({'generation':gid,'segment':segment['id'],
                    'request':packet,'contract':'source-slice-v3','system':SYSTEM,'model':'claude-opus-5-5','effort':'high'})
                self._check()
                try:
                    diagnosis=self._guarded('word_recovery',{'folder':str(destination),'request':packet})
                    validate(diagnosis['answer'],packet,schema([segment['id']],[quote]))
                except (AppError,ValueError,KeyError,TypeError,IndexError) as error:
                    raise SpeechHoldError('Automatic word diagnosis failed; no unverified audio was published. '+str(error)) from error
                row=diagnosis['answer']['diagnoses'][0]
                for index,native in enumerate(row['native_candidates']):
                    repair={'expected_spoken':baseline,'native_text':native,'recovery_kind':'opus_word_recovery',
                        'evidence':{'diagnosis_sha256':file_digest(destination/'answer.json'),'source_evidence':row['source_evidence']},
                        'scope':'Automatic source-preserving candidate; word and edge gates remain required'}
                    try:native,_=apply_spoken_edit(baseline,repair)
                    except ValueError as error:
                        failures.append('Rejected invalid native repair: '+str(error));continue
                    for seed in range(2):
                        self._check();selected_take=requested_take+3+index*2+seed
                        candidate={**request,'text':native,'take':selected_take}
                        try:
                            result=self._speak(candidate,wav)
                            if not plausible(result['duration'],native):raise SpeechHoldError('Recovered speech has an implausible speaking rate.')
                            self._intact(request['pack'],wav)
                            self._check_immutable_candidate(candidate,wav)
                            return {**result,'reset_cues':True,'automatic_retake':{
                                'requested_take':requested_take,'selected_take':selected_take,'failures':failures,
                                'policy':'source_bound_word_recovery_v2','native_text':native,'spoken_text_plan':repair,
                                'decoding_profile':request.get('_decoding_profile'),
                                'original_text_sha256':digest(original_text),'pack_fingerprint':request['pack']['_fingerprint'],
                                'diagnosis_path':str(destination/'answer.json'),
                                'voice_id':request['voice_id'],'speaker':segment['speaker']}}
                        except (SpeechHoldError,GenerationCapError) as error:
                            self._intact(request['pack'],wav)
                            with contextlib.suppress(OSError):wav.unlink(missing_ok=True)
                            failures.append(str(error))
                        except EngineTransportError as error:
                            self._intact(request['pack'],wav);failures.append(str(error));self._replace()
            if cap_failure is not None and not word_failures:raise cap_failure
            raise SpeechHoldError('Automatic quality retakes exhausted; no failed audio was published. '+failures[-1])
        failure = None
        for attempt in range(2):
            try:
                result = self._speak(request, wav)
                break
            except SpeechHoldError:
                self._intact(request['pack'], wav)
                self._check()
                raise
            except GenerationCapError:
                self._intact(request['pack'], wav)
                self._check()
                if attempt:
                    raise
            except AppError as error:
                self._intact(request['pack'], wav)
                self._check()
                failure = error
                if not attempt:
                    self._replace()
        else:
            if isinstance(failure, EngineTransportError):
                raise AppError(f'{WORKER_FAILED} {failure}') from failure
            self.store.execute("UPDATE segments SET status='failed',warning=? WHERE generation_id=? AND id=?", (clipped(str(failure)), gid, segment['id']))
            raise AppError(RENDER_FAILED) from failure
        if not plausible(result['duration'], request['text']):
            from .speech_backend import is_higgs
            if is_higgs(request['pack']):
                self._intact(request['pack'], wav)
                wav.unlink(missing_ok=True)
                raise SpeechHoldError('Higgs output has an implausible speaking rate; render is held for a retake.')
            first = wav.read_bytes()
            try:
                result = self._speak(request, wav)
            except AppError as error:
                self._intact(request['pack'], wav)
                self._check()
                atomic_write(wav, first)
                result = {**read_audio(wav), 'warning': result['warning']}
                if isinstance(error, EngineTransportError):
                    self._replace()
            if not plausible(result['duration'], request['text']):
                result['warning'] = append_warning(result['warning'], IMPLAUSIBLE)
        self._intact(request['pack'], wav)
        return {**result, 'reset_cues': True}

    def _unaligned(self, gid, segments, notice, failed=(), aligned=()):
        notes = [(notice, notice, gid, segment['id'], notice) for segment in segments]
        with self.store.lock, self.store.db:
            self.store.db.executemany("UPDATE segments SET warning=CASE WHEN warning='' THEN ? ELSE warning || ' ' || ? END WHERE generation_id=? AND id=? AND instr(warning,?)=0", notes)
            analysis = json.loads(self.store.db.execute('SELECT analysis FROM generations WHERE id=?', (gid,)).fetchone()['analysis'])
            recorded = set(analysis.get('alignment_failed', []))
            updated = recorded - set(aligned) | {segment['audio_hash'] for segment in [*segments, *failed]}
            if updated != recorded:
                self.store.db.execute('UPDATE generations SET analysis=? WHERE id=?', (json.dumps({**analysis, 'alignment_failed': sorted(updated)}), gid))

    def _align(self,gid,pack,language,config):
        import importlib.util
        pending = [segment for segment in self.store.generation(gid)['segments'] if segment['status'] == 'ready' and not segment['cues'] and audible(segment['spoken'])
            and ('_render_work_ids'not in config or segment['id']in config['_render_work_ids'])]
        if not pending:
            return
        executable = resolve_binary('rimsky-align', self.binaries)
        available = executable or not getattr(sys, 'frozen', False) and importlib.util.find_spec('qwen_asr') is not None
        if not available or not pack.get('aligner'):
            self._unaligned(gid, pending, ALIGNMENT_UNAVAILABLE)
            return
        self.store.status(gid, 'aligning')
        aligner, stopped, failed, aligned = None, [], [], []
        try:
            try:
                aligner = Engine(self.binaries, component='align')
            except (AppError, OSError):
                stopped = pending
            else:
                self.engine = aligner
                for index, segment in enumerate(pending):
                    self._check()
                    request = {'model': str(inside(Path(pack['_root']), pack['aligner']['path'])),
                               'audio': str(self.store.root/'audio'/f'{segment["audio_hash"]}.wav'),
                               'text': segment['spoken'], 'language': language, 'device': config['device']}
                    try:
                        pack_unchanged(pack)
                        try:
                            result = aligner.call('align', request, self.stop, timeout=900)
                        finally:
                            pack_unchanged(pack)
                        cues = validate_cues(result, segment['spoken'], segment['duration'])
                        self.store.execute('UPDATE segments SET cues=? WHERE generation_id=? AND id=?',
                                           (json.dumps(cues, ensure_ascii=False), gid, segment['id']))
                        aligned.append(segment['audio_hash'])
                    except PackChangedError:
                        raise
                    except (AppError, ValueError, KeyError, TypeError, OSError) as error:
                        self._check()
                        if aligner.process.poll() is not None:
                            stopped = pending[index:]
                            break
                        warning = append_warning(segment['warning'], str(error), 'Passage subtitles are retained.')
                        self.store.execute('UPDATE segments SET warning=? WHERE generation_id=? AND id=?', (warning, gid, segment['id']))
                        failed.append(segment)
            self._unaligned(gid, stopped, ALIGNER_EXITED, failed, aligned)
        finally:
            if aligner:
                try:
                    aligner.close()
                finally:
                    if self.engine is aligner:
                        self.engine = None

    def shutdown(self):
        with self.lock:
            self.closed = True
            self.stop.set()
            engine = self.engine
            thread = self.thread
        try:
            if engine:
                engine.close()
        finally:
            if thread:
                thread.join(timeout=8)
                if thread.is_alive():
                    raise AppError('Local preparation has not stopped; its database remains open until process exit.')
