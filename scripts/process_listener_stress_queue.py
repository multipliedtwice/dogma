"""Render and publish queued listener stress corrections in listening priority order."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime'), str(ROOT / 'scripts')]

from rimsky.spoken_pronunciation import ACUTE, unmarked, validate_marks, yo_fold, yo_bound_differences
from rimsky.respelling import validate_respelled_stress
from rimsky.production_higgs_model import resolve_production_higgs_model
from rimsky.util import AppError, digest, file_digest, now, write_json


WORD = re.compile(r'[А-Яа-яЁё́]+(?:[-‐‑][А-Яа-яЁё́]+)*')
LETTER = 'А-Яа-яЁё́'


def read(path):
    return json.loads(Path(path).read_text('utf8'))


def _same_target(left, right):
    a = left.get('report') or {}
    b = right.get('report') or {}
    fields = ('generation_id', 'segment_id')
    corrections = lambda row: row.get('corrections') or [{key: row.get(key) for key in
                                                           ('selected_text', 'intended_stress', 'text_offset')}]
    return all(a.get(key) == b.get(key) for key in fields) and corrections(a) == corrections(b)


def reuse_applied(folder, item_path, item):
    """Late queue duplicates reuse an already installed identical correction."""
    for path in (folder / 'listener-stress-queue').glob('*.json'):
        if path == item_path:
            continue
        with contextlib.suppress(OSError, ValueError, TypeError):
            prior = read(path)
            if prior.get('status') != 'applied' or not _same_target(prior, item):
                continue
            registry = read(folder / 'spoken-take-revisions.json')['units'][item['report']['segment_id']]
            item.update(status='applied', updated=now(), completed=now(), error='',
                        reused_from=prior['id'], instruction=prior.get('instruction'),
                        instruction_sha256=prior.get('instruction_sha256'),
                        replacement_audio_sha256=registry['replacement']['receipt']['audio_sha256'],
                        human_audio_approval=False)
            write_json(item_path, item)
            return True
    return False


def next_seed_offset(folder, item_path, item):
    """Continue after bounded failed seeds without rewriting their evidence."""
    next_offset = int(item.get('seed_offset', 0))
    queue = folder / 'listener-stress-queue'
    for path in queue.glob('*.json'):
        if path == item_path:
            continue
        with contextlib.suppress(OSError, ValueError, TypeError):
            prior = read(path)
            if prior.get('status') != 'held' or not _same_target(prior, item):
                continue
            instruction = queue / 'instructions' / f"{prior['id']}.json"
            if not instruction.is_file():
                continue
            base = int(read(instruction).get('seed_offset', 0))
            report = folder / 'pronunciation-retry-reports' / f'{file_digest(instruction)}.json'
            attempts = read(report).get('attempts', []) if report.is_file() else []
            if attempts and all(row.get('status') == 'held' for row in attempts):
                next_offset = max(next_offset, base + len(attempts))
    if not 0 <= next_offset <= 1000:
        raise AppError('Listener stress retry exhausted the supported seed range.')
    return next_offset


def _selection_matches(text, selected):
    """Return original-string spans while matching against its unmarked text."""
    positions=[]
    plain=[]
    # ё is folded to е (same length) so a spoken ё still finds its printed е.
    for index, character in enumerate(yo_fold(text)):
        if character == ACUTE:
            continue
        positions.append(index)
        plain.append(character)
    pattern = re.compile(rf'(?<![{LETTER}]){re.escape(yo_fold(selected))}(?![{LETTER}])')
    result=[]
    for match in pattern.finditer(''.join(plain)):
        start=positions[match.start()]
        end=positions[match.end()-1]+1
        while end < len(text) and text[end] == ACUTE:
            end += 1
        result.append((start,end))
    return result


def _locked(path, attempts=21):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    if path.stat().st_size == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    for attempt in range(attempts):
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except OSError:
            if attempt + 1 == attempts:
                handle.close()
                return None
            time.sleep(.1)


def _unlock(handle):
    if handle is None:
        return
    handle.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN)
    handle.close()


def _selected_take(folder, unit_id):
    registry = folder / 'spoken-take-revisions.json'
    if registry.exists():
        previous = read(registry).get('units', {}).get(unit_id)
        # A candidate is recorded before the chapter PCM transaction so its
        # evidence survives an install failure.  It becomes selectable only
        # after the exact sentence patch writes its installation proof.
        applied = folder / 'pronunciation-revisions' / unit_id / 'applied.json'
        if previous and applied.is_file():
            return previous['replacement'], True
    matches = []
    for path in folder.glob('c*/chunk-*/**/selected.json'):
        rows = read(path)
        if isinstance(rows, dict) and unit_id in rows:
            matches.append(rows[unit_id])
    if not matches:
        raise AppError(f'The published take for {unit_id} was not found.')
    keys = {(row['receipt']['key'], row['receipt']['audio_sha256']) for row in matches}
    if len(keys) != 1:
        raise AppError(f'Multiple different published takes were found for {unit_id}.')
    return matches[0], False


def stress_native_edits(before, report):
    """Return (native text, yo_edits): one or more source-bound marks on one synthesis unit.

    yo_edits lists each selected word whose е/ё letter differs from the heard input, in native
    unmarked coordinates; the final guard allows differences only inside them."""
    corrections = report.get('corrections') or [{key: report.get(key) for key in
                                                  ('selected_text', 'intended_stress', 'text_offset')}]
    unit = before['unit']
    source = unit['text']
    # Edit the exact marked input that made the heard audio. Display `spoken`
    # text intentionally omits stress marks and would silently discard all
    # unrelated automatic pronunciation decisions in this sentence.
    spoken = unit['synthesis']['text']
    edits = []
    for correction in corrections:
        selected = correction['selected_text']
        intended = correction['intended_stress']
        if not isinstance(correction.get('text_offset'), int):
            raise AppError('The queued stress word has no exact source-text offset.')
        validate_marks(intended)
        try:
            substitutions = validate_respelled_stress(selected, intended)
        except ValueError as error:
            raise AppError('The queued stress choice changes the selected source word.') from error
        # Only е->ё is orthographic here; э respellings belong to the names path.
        if any(row['spoken'] not in 'ёЁ' for row in substitutions):
            raise AppError('The queued stress choice changes the selected source word.')
        local = correction['text_offset'] - unit['start']
        # A listener may select the lexical component of a compound name
        # (Хакону inside Ар-Хакону). Hyphens are component boundaries; letters
        # are not, so a selection can never bind inside an unrelated word.
        source_matches = _selection_matches(source,selected)
        source_index = next((index for index, match in enumerate(source_matches)
                             if match[0] == local), None)
        if source_index is None or unmarked(source[local:source_matches[source_index][1]]) != selected:
            raise AppError('The queued stress word is not bound to this synthesis unit.')
        spoken_matches = _selection_matches(spoken,selected)
        if source_index >= len(spoken_matches):
            raise AppError('The selected source word has no unambiguous spoken-text occurrence.')
        target = spoken_matches[source_index]
        edits.append((target[0], target[1], intended, selected))
    native = ''
    position = 0
    yo_edits = []
    for start, end, intended, selected in sorted(edits):
        native += spoken[position:start]
        if unmarked(spoken[start:end]) != unmarked(intended):
            offset = len(unmarked(native))
            yo_edits.append({'start': offset, 'end': offset + len(unmarked(intended)),
                             'printed': selected, 'intended': unmarked(intended)})
        native += intended
        position = end
    native += spoken[position:]
    if not yo_bound_differences(unmarked(native), unmarked(spoken), yo_edits):
        raise AppError('The stress correction changed the spoken words.')
    return native, yo_edits


def stress_native_text(before, report):
    return stress_native_edits(before, report)[0]


def build_revision(folder, item):
    report = item['report']
    before, revised = _selected_take(folder, report['segment_id'])
    manifest = read(folder / 'manifest.json')
    native_text, yo_edits = stress_native_edits(before, report)
    request = {
        'schema': 1,
        'generation': folder.name,
        'source_sha256': manifest['source_sha256'],
        'native_text': native_text,
        'before': before,
        'seed_offset': int(item.get('seed_offset', 0)),
        'listener_report': {
            'id': report['id'],
            'generation_id': report['generation_id'],
            'audio_hash': report['audio_hash'],
            'selected_text': report['selected_text'],
            'intended_stress': report['intended_stress'],
            'text_offset': report['text_offset'],
            'corrections': report.get('corrections'),
        },
        'human_audio_approval': False,
    }
    previous_native = before['unit']['synthesis']['text']
    if yo_edits:
        request['listener_yo_edits'] = yo_edits
    if yo_bound_differences(unmarked(native_text), unmarked(previous_native), yo_edits):
        request.update(recovery_kind='listener_pronunciation_from_synthesis',
                       previous_native_text=previous_native)
    if revised:
        request['published_before'] = before
    return request


def source_file(data_dir, book_id):
    candidates = [data_dir.parent / 'source/books' / book_id / 'source.fb2',
                  data_dir.parent / 'source/books' / book_id / 'source.epub']
    files = [path for path in candidates if path.is_file()]
    if len(files) != 1:
        raise AppError('The exact source book for the production resync is unavailable.')
    return files[0]


def replacement_environment():
    """Pin new audio to production while the request retains the heard take."""
    pinned = resolve_production_higgs_model(ROOT)
    return {**os.environ, 'RIMSKY_HIGGS_MODEL': str(pinned.path)}


def debug_source_state(folder,ready):
    """Physical receipt coverage, publication completion and verified owners."""
    import psutil
    expected=ready.get('unit_count')
    if type(expected) is not int or expected<1:raise AppError('Deferred source has no valid frozen unit count.')
    receipts=list((folder/'units').glob('*.json'))
    rows=[read(path) for path in receipts]
    rendered=sum(row.get('status')=='rendered' for row in rows)
    failures=[row.get('unit') for row in rows if row.get('status')!='rendered']
    progress=read(folder/'progress.json') if (folder/'progress.json').exists() else {}
    terminal=read(folder/'listener-repair-terminal.json') if (folder/'listener-repair-terminal.json').exists() else {}
    complete=len(rows)==expected and rendered==expected and progress.get('status')=='complete'
    repaired=read(folder/'repair.json') if (folder/'repair.json').exists() else {}
    base=Path(repaired.get('base',folder)).resolve()
    owners=[]
    allowed={str((ROOT/'scripts'/name).resolve()).casefold():flag for name,flag in (
        ('repair_debug_listener_stress.py','--generation'),('debug_higgs_one_shot.py','--output'),
        ('supervise_debug_higgs_one_shot.py','--output'))}
    for process in psutil.process_iter(['pid','create_time','cmdline']):
        command=process.info.get('cmdline') or []
        try:
            scripts=[(argument,allowed.get(str(Path(argument).resolve()).casefold())) for argument in command if argument.endswith('.py')]
            matched=False
            for script,flag in scripts:
                if flag and flag in command:
                    target=Path(command[command.index(flag)+1]).resolve()
                    expected_target=base if Path(script).name=='repair_debug_listener_stress.py' else folder.resolve()
                    matched=target==expected_target
                if matched:break
            if matched:owners.append({'pid':process.pid,'create_time':process.create_time(),'command':command})
        except (OSError,IndexError,psutil.Error):continue
    paths=[*receipts,*(folder/'native').glob('*/native.log'),*(folder/'native').glob('*/physical-result.json')]
    activity=max((path.stat().st_mtime for path in paths if path.exists()),default=0)
    return {'complete':complete,'expected':expected,'rendered':rendered,'technical_failures':failures,
        'publication_status':progress.get('status'),'terminal_status':terminal.get('status'),
        'owners':owners,'activity_unix':activity}


def wait_for_debug_source(folder,ready,item_path,*,poll_seconds=10,owner_grace_seconds=300,stall_seconds=1800):
    """Wait for an owned render, then repair automatically without a new report.

    There is no total audiobook duration cap. Missing ownership and absence of
    real output movement have separate bounded failure deadlines.
    """
    began=time.monotonic();last_activity=None;last_movement=began;owner_missing=None
    while True:
        state=debug_source_state(folder,ready)
        if state['complete']:return
        item=read(item_path)
        if state['terminal_status']=='held' or state['technical_failures'] and not state['owners']:
            raise AppError('The source render failed before the queued listener correction could run.','listener_source_failed')
        marker=(state['rendered'],state['activity_unix'])
        if marker!=last_activity:last_activity=marker;last_movement=time.monotonic()
        if state['owners']:owner_missing=None
        elif owner_missing is None:owner_missing=time.monotonic()
        if owner_missing is not None and time.monotonic()-owner_missing>=owner_grace_seconds:
            raise AppError('The source render has no live verified owner; its saved listener correction is held.','listener_source_owner_missing')
        if time.monotonic()-last_movement>=stall_seconds:
            raise AppError('The source render stopped producing audio or native evidence; its listener correction is held.','listener_source_stalled')
        item.update(status='queued',phase='awaiting_render_completion',updated=now(),
            reason='Waiting for the current frozen render to finish; this correction will then run automatically.',
            source_progress=state,wait_elapsed_seconds=round(time.monotonic()-began,2),error='')
        write_json(item_path,item)
        time.sleep(poll_seconds)


def rebind_deferred_report(folder,data_dir,generation_id,item_path):
    """Keep original evidence immutable while binding the now-published take."""
    from rimsky.listener_stress import shared_profile,snapshot,notes
    from rimsky.store import Store
    item=read(item_path);origin=item.get('origin_report')
    if not origin or item.get('origin_report_sha256')!=digest(origin):
        raise AppError('Deferred correction has no valid immutable originating report.')
    store=Store(data_dir,correction_profile=shared_profile())
    try:
        generation=store.generation(generation_id);book=store.book(generation['book_id'])
        snapshot(store,book)
        canonical=next((row['report'] for row in notes(store,book) if row['report']['id']==origin['id']),None)
        if canonical is None or any(canonical.get(key)!=value for key,value in origin.items()):
            raise AppError('Deferred correction does not match its saved human report.')
        report=item['report'];source=report['source_id'];offset=report.get('text_offset')
        blocks={b['id']:b['text'] for chapter in book['chapters'] for b in chapter['blocks']}
        original_block=blocks.get(origin['source_id'])
        if original_block!=origin['passage_text'] or type(origin.get('text_offset')) is not int or (
                original_block[origin['text_offset']:origin['text_offset']+len(origin['selected_text'])]!=origin['selected_text']):
            raise AppError('Deferred report source passage changed before publication.')
        targets=[s for s in generation['segments'] if s['source_id']==source and type(offset) is int
            and s['start']<=offset and offset+len(report['selected_text'])<=s['end']
            and s.get('audio_hash') and s['status']=='ready']
        if len(targets)!=1:raise AppError('Completed source has no exact published segment for the deferred correction.')
        segment=targets[0]
        item['report']={**report,'generation_id':generation_id,'segment_id':segment['id'],
            'chapter_id':segment['chapter_id'],'audio_hash':segment['audio_hash'],
            'spoken_text':segment['spoken'],'passage_text':blocks[source]}
        item.update(phase='ready_for_correction',rebound_at=now(),
            rebound_source_audio_hash=segment['audio_hash'],origin_verified_sha256=digest(origin),error='')
        write_json(item_path,item)
    finally:store.close()


def wait_for_development_release(folder,item_path,*,poll_seconds=10):
    """Honor an explicit development release handoff without dispatching old code.

    Ordinary user correction jobs have no barrier. Development can attach one
    while an immutable render finishes and its reviewed replacement is released.
    """
    while True:
        item=read(item_path)
        global_path=folder/'listener-stress-queue/development-dispatch-barrier.json'
        global_barrier=read(global_path) if global_path.exists() else {}
        if global_barrier.get('state')=='released':return
        barrier=item.get('development_dispatch_barrier') or (global_barrier if global_barrier.get('state')=='awaiting_reviewed_release' else None)
        if not barrier:return
        release=folder/'listener-stress-queue/dispatch-release.json'
        if release.exists():
            decision=read(release)
            if (decision.get('generation')!=folder.name
                or not ((item.get('origin_report') or {}).get('id') in decision.get('report_ids',[])
                    or decision.get('allow_future_canonical_reports') is True
                    and bool(item.get('origin_report'))
                    and item.get('origin_report_sha256')==digest(item['origin_report'])
                    and item.get('origin_verified_sha256')==item.get('origin_report_sha256'))
                or decision.get('helper_sha256')!=file_digest(ROOT/'scripts/repair_debug_listener_stress.py')
                or decision.get('listener_module_sha256')!=file_digest(ROOT/'runtime/rimsky/listener_stress.py')):
                raise AppError('The development correction release does not bind this saved report and reviewed implementation.')
            item.update(dispatch_release_sha256=file_digest(release),phase='ready_for_correction',updated=now())
            write_json(item_path,item)
            return
        item.update(status='queued',phase='awaiting_preparation_release',updated=now(),
            reason='Saved correction is waiting for the reviewed preprocessing release; it will run automatically afterwards.',error='')
        write_json(item_path,item)
        time.sleep(poll_seconds)


@contextlib.contextmanager
def book_correction_lock(data_dir,item_path,poll_seconds=10):
    """Serialize derived repairs for one book even when old tabs use old queues."""
    item=read(item_path);book_id=(item.get('report') or {}).get('book_id')
    if not book_id:raise AppError('Listener correction has no source book identity.')
    lock_path=data_dir/'listener-stress-locks'/(digest(book_id)+'.lock')
    handle=None
    try:
        while handle is None:
            item=read(item_path)
            if item.get('status') in ('applied','held'):
                yield False
                return
            handle=_locked(lock_path,attempts=1)
            if handle is None:
                item.update(status='queued',phase='awaiting_book_correction',updated=now(),
                    reason='Another saved correction for this book is being published; this request will follow automatically.')
                write_json(item_path,item)
                time.sleep(poll_seconds)
        yield True
    finally:_unlock(handle)


def latest_delivered_debug_base(folder,data_dir,generation_id):
    """Follow only successful delivery receipts; never choose a display name."""
    from rimsky.store import Store
    from rimsky.listener_stress_retake import _external_folder
    store=Store(data_dir)
    try:
        book_id=store.generation(generation_id)['book_id'];visited={generation_id}
        while True:
            candidates=[]
            for path in (folder/'listener-stress-queue').glob('*.json'):
                row=read(path);result=row.get('result') or {}
                identifier=row.get('published_generation_id') or result.get('published_generation_id')
                if row.get('status')!='applied' or not identifier or identifier in visited:continue
                generation=store.generation(identifier)
                if generation['book_id']!=book_id:raise AppError('Listener delivery points to another source book.')
                target=_external_folder(store,generation)
                if target is None:raise AppError('Listener delivery has no bound external generation.')
                ready=target/'ready.json'
                if not ready.is_file() or read(target/'progress.json').get('status')!='complete':
                    raise AppError('Listener delivery has not finished publishing its source.')
                terminal=target/'listener-repair-terminal.json'
                if terminal.exists() and read(terminal).get('status')!='applied':
                    raise AppError('Listener delivery did not complete successfully.')
                candidates.append((row.get('completed',''),row.get('updated',''),identifier,target))
            if not candidates:return folder,generation_id
            _,_,generation_id,folder=max(candidates,key=lambda value:value[:3]);visited.add(generation_id)
    finally:store.close()


def current_book_debug_base(folder, data_dir, generation_id):
    """Old-tab queues follow the current book, retaining their heard evidence."""
    from rimsky.store import Store
    from rimsky.listener_stress_retake import _external_folder
    if not (data_dir/'library.sqlite3').is_file():return folder,generation_id
    store=Store(data_dir)
    try:
        book_id=store.generation(generation_id)['book_id']
        generation=store.current_generation(book_id)
        if generation is None:return folder,generation_id
        identifier=generation['id']
        target=_external_folder(store,generation)
        if target is None:return folder,generation_id
        ready=target/'ready.json'
        if not ready.is_file():return folder,generation_id
        frozen=read(ready)
        if frozen.get('policy')=='current-book-output-v1':
            from current_higgs_output import load
            view=load(store,data_dir,book_id)
            if view is None or view['generation_id']!=identifier:raise AppError('Current book output is not published.')
            return target,identifier
        if not frozen.get('plans') or not frozen.get('native_manifest'):return folder,generation_id
        return target,identifier
    finally:store.close()


def process(folder, data_dir, generation_id, item_path):
    # The full-render coordinator takes this same lock. Legacy queues must wait
    # too, and choose their destination after waiting rather than racing it.
    if read(item_path).get('status') in ('applied','held'):return
    # A preparation barrier must never monopolize the book lock needed by the
    # full render that will satisfy it.
    wait_for_development_release(folder,item_path)
    initial,initial_id=current_book_debug_base(folder,data_dir,generation_id)
    if initial_id!=generation_id:wait_for_development_release(initial,item_path)
    with book_correction_lock(data_dir,item_path) as acquired:
        if not acquired:return
        target,target_id=current_book_debug_base(folder,data_dir,generation_id)
        if target_id!=generation_id:
            rebind_deferred_report(target,data_dir,target_id,item_path)
            item=read(item_path)
            previous=item.pop('development_dispatch_barrier',None)
            if previous:
                item.setdefault('prior_dispatch_barriers',[]).append({'generation_id':generation_id,'barrier':previous})
                write_json(item_path,item)
        _process_locked(target,data_dir,target_id,item_path)


def _process_locked(folder, data_dir, generation_id, item_path):
    item = read(item_path)
    # A coalesced correction can publish several requests while this owner is
    # working through an earlier scan. Never dispatch an already covered item.
    if item.get('status') in ('applied','held'):return
    ready=folder/'one-shot-ready.json'
    if not ready.exists():ready=folder/'ready.json'
    debug_ready=read(ready) if ready.exists() else {}
    if ((folder/'current-output.json').exists() or debug_ready.get('current_output_manifest')
            or debug_ready.get('policy')=='current-book-output-v1') and (
            debug_ready.get('policy')!='current-book-output-v1'
            or debug_ready.get('current_output_manifest')!='current-output.json'):
        raise AppError('Current audiobook publication controls are incomplete or invalid; legacy correction is disabled.')
    if debug_ready.get('policy')=='current-book-output-v1':
        # The compact current book has no old unit tree or chapter copies.
        # Its helper commits only successful parts into the existing Store ID.
        item.update(status='running',updated=now(),attempts=int(item.get('attempts',0))+1,
            dispatch_source_generation_id=generation_id,dispatch_source_folder=str(folder))
        write_json(item_path,item)
        result_path=folder/'listener-stress-queue/results'/f"{item['id']}.json"
        result_path.parent.mkdir(parents=True,exist_ok=True)
        process_result=subprocess.run([sys.executable,'-X','utf8','-u',str(ROOT/'scripts/repair_debug_listener_stress.py'),
            '--generation',str(folder),'--data-dir',str(data_dir),'--generation-id',generation_id,
            '--queue-item',str(item_path),'--result',str(result_path)],cwd=ROOT,check=False)
        result=read(result_path)
        if (getattr(process_result,'returncode',0) or result.get('status')!='applied'
                or result.get('published_generation_id')!=generation_id):
            raise AppError('The current book correction was not published to its existing output: '
                           + str(result.get('error') or result.get('failures') or result.get('status')))
        item=read(item_path)
        item.update(status='applied',phase='delivered',updated=now(),completed=now(),error='',
            result=result,result_sha256=file_digest(result_path),published_generation_id=generation_id,
            revision=result.get('revision'),human_audio_approval=False)
        write_json(item_path,item)
        return
    if ready.exists() and debug_ready.get('plans') and debug_ready.get('native_manifest') and (folder/'units').is_dir():
        if (not debug_source_state(folder,debug_ready)['complete'] or item.get('phase')=='awaiting_render_completion'
            or item.get('development_dispatch_barrier') or (folder/'listener-stress-queue/development-dispatch-barrier.json').exists()):
            wait_for_debug_source(folder,debug_ready,item_path)
            rebind_deferred_report(folder,data_dir,generation_id,item_path)
            item=read(item_path)
        wait_for_development_release(folder,item_path)
        target,target_id=latest_delivered_debug_base(folder,data_dir,generation_id)
        if target_id!=generation_id:rebind_deferred_report(target,data_dir,target_id,item_path)
        item=read(item_path)
        item.update(status='running',updated=now(),attempts=int(item.get('attempts',0))+1,
            dispatch_source_generation_id=target_id,dispatch_source_folder=str(target))
        write_json(item_path,item)
        result_path=folder/'listener-stress-queue/results'/f"{item['id']}.json"
        result_path.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run([sys.executable,'-X','utf8','-u',str(ROOT/'scripts/repair_debug_listener_stress.py'),
            '--generation',str(target),'--data-dir',str(data_dir),'--generation-id',target_id,
            '--queue-item',str(item_path),'--result',str(result_path)],cwd=ROOT,check=True)
        result=read(result_path)
        if result.get('status')!='applied':raise AppError('The one-shot listener correction was not published.')
        item=read(item_path)
        item.update(status='applied',updated=now(),completed=now(),error='',
            result=result,result_sha256=file_digest(result_path),human_audio_approval=False)
        write_json(item_path,item)
        return
    if reuse_applied(folder, item_path, item):
        return
    item['seed_offset'] = next_seed_offset(folder, item_path, item)
    item.update(status='running', updated=now(), attempts=int(item.get('attempts', 0)) + 1)
    write_json(item_path, item)
    request = build_revision(folder, item)
    instructions = folder / 'listener-stress-queue/instructions'
    instructions.mkdir(parents=True, exist_ok=True)
    request_path = instructions / f"{item['id']}.json"
    if request_path.exists() and read(request_path) != request:
        raise AppError('The immutable listener stress instruction changed.')
    if not request_path.exists():
        write_json(request_path, request)
    # `request.before` remains the immutable heard-model/audio evidence.  It is
    # deliberately not used as a model selector for newly synthesized audio.
    environment = replacement_environment()
    subprocess.run([
        sys.executable, '-X', 'utf8', '-u', str(ROOT / 'scripts/revise_higgs_spoken_take.py'),
        '--folder', str(folder), '--request', str(request_path),
    ], cwd=ROOT, env=environment, check=True, timeout=3600)
    subprocess.run([
        sys.executable, '-X', 'utf8', '-u', str(ROOT / 'scripts/sync_higgs_preview_runtime.py'),
        '--workspace', str(ROOT),
        '--source', str(source_file(data_dir, item['report']['book_id'])),
        '--prepared-book', str(data_dir.parent / 'source/book.json'),
        '--generation', str(folder), '--data-dir', str(data_dir),
    ], cwd=ROOT, check=True, timeout=600)
    registry = read(folder / 'spoken-take-revisions.json')['units'][item['report']['segment_id']]
    item.update(status='applied', updated=now(), completed=now(), error='',
                instruction=str(request_path), instruction_sha256=file_digest(request_path),
                replacement_audio_sha256=registry['replacement']['receipt']['audio_sha256'],
                human_audio_approval=False)
    write_json(item_path, item)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generation', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--generation-id', required=True)
    args = parser.parse_args()
    folder = args.generation.resolve()
    data_dir = args.data_dir.resolve()
    if folder.name != read(folder / 'progress.json').get('generation'):
        raise AppError('The listener stress queue belongs to another generation.')
    lock = _locked(folder / 'listener-stress-queue/worker.lock')
    if lock is None:
        return
    try:
        import psutil
        self_process=psutil.Process(os.getpid())
        write_json(folder/'listener-stress-queue/worker-state.json',{'pid':os.getpid(),
            'create_time':self_process.create_time(),'command':self_process.cmdline(),'started':now(),
            'dispatcher_sha256':file_digest(Path(__file__)),
            'listener_module_sha256':file_digest(ROOT/'runtime/rimsky/listener_stress.py')})
        queue = folder / 'listener-stress-queue'
        # Re-scan after every batch. A correction can arrive while a take is
        # rendering; the existing owner must consume it even if the newly
        # launched helper observed this lock and exited.
        while True:
            pending = []
            for path in queue.glob('*.json'):
                item = read(path)
                if (item.get('generation_id') == args.generation_id
                        and item.get('status') in ('queued', 'running')):
                    pending.append((item.get('priority', [3, 0, 0]), item.get('created', ''), path.name, path))
            if not pending:
                # Cover the small handoff race between an enqueue and unlock.
                time.sleep(.25)
                pending = []
                for path in queue.glob('*.json'):
                    item = read(path)
                    if (item.get('generation_id') == args.generation_id
                            and item.get('status') in ('queued', 'running')):
                        pending.append((item.get('priority', [3, 0, 0]), item.get('created', ''), path.name, path))
                if not pending:
                    break
            for _, _, _, path in sorted(pending):
                try:
                    process(folder, data_dir, args.generation_id, path)
                except Exception as error:
                    item = read(path)
                    item.update(status='held', updated=now(), error=f'{type(error).__name__}: {error}',
                                traceback=traceback.format_exc(limit=8), human_audio_approval=False)
                    if getattr(error,'code','').startswith('listener_source_'):
                        item.update(phase='awaiting_source_repair',deferred_hold_code=error.code)
                    write_json(path, item)
    finally:
        _unlock(lock)


if __name__ == '__main__':
    main()
