"""Queue source-bound listener stress corrections for an external Higgs preview.

The listener report remains the durable human evidence.  This module only
creates a render request bound to that report and starts the single-owner queue
worker; it never treats the new audio as listener-approved.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from .util import AppError, digest, file_digest, now, write_json
from .series_characters import propagation_targets


ROOT = Path(__file__).resolve().parents[2]
ENGINE = 'higgs_audio_tts_q8_external_preview'
ACTIVE = frozenset({'queued', 'running'})
_WORKERS = {}


def _origin(report):
    origin={key:report.get(key) for key in ('id','book_id','generation_id','segment_id','chapter_id','source_id',
        'selected_text','text_offset','audio_offset','audio_hash','passage_text','spoken_text','created','kind','comment','intended_stress','synthesis_snapshot')}
    # Preserve heard identity on new reports without changing historical hashes.
    origin.update({key:report[key]for key in ('heard_binding','heard_revision')if key in report})
    return origin


def _debug_pending(folder):
    ready=folder/'ready.json'
    compact=(folder/'current-output.json').exists()
    if compact and not ready.is_file():
        raise AppError('The current audiobook controls are missing. Repair its publication before applying a correction.')
    if not ready.is_file():return False
    try:document=json.loads(ready.read_text('utf8'))
    except (OSError,ValueError)as error:
        if compact:raise AppError('The current audiobook controls are invalid. Repair its publication before applying a correction.')from error
        raise
    if compact and (not isinstance(document,dict)or document.get('policy')!='current-book-output-v1'):
        raise AppError('The current audiobook controls have an incompatible policy. A correction cannot use the legacy renderer.')
    if document.get('policy')=='current-book-output-v1':
        manifest=json.loads((folder/'current-output.json').read_text('utf8'))
        if manifest.get('sha256')!=digest({key:value for key,value in manifest.items()if key!='sha256'}):
            raise AppError('The current audiobook manifest changed before its correction was queued.')
        order=manifest.get('unit_order',[]);parts=manifest.get('parts',{})
        if len(order)!=document.get('unit_count')or len(set(order))!=len(order)or set(order)!=set(parts):return True
        intent=folder/'publication-intent.json'
        return intent.is_file()and json.loads(intent.read_text('utf8')).get('status')=='pending'
    expected=document.get('unit_count')
    if type(expected) is not int or expected<1:return False
    if sum(1 for _ in (folder/'units').glob('*.json'))<expected:return True
    return json.loads((folder/'progress.json').read_text('utf8')).get('status')!='complete'


def _planned_report_targets(folder,report):
    """Unpublished units are absent from the Store's progressive projection."""
    path=folder/'ready.json'
    if not path.exists():return []
    ready=json.loads(path.read_text('utf8'));result=[]
    offset=report.get('text_offset');word=report['selected_text']
    if type(offset) is not int:return []
    for record in ready.get('plans',[]):
        plan=(folder/record['plan']).resolve()
        if not plan.is_relative_to(folder.resolve()) or file_digest(plan)!=record['plan_sha256']:
            raise AppError('The unpublished correction target has no valid frozen plan.')
        for unit in json.loads(plan.read_text('utf8')).get('units',[]):
            if (unit.get('source_id')==report['source_id'] and unit['start']<=offset
                and offset+len(word)<=unit['end']
                and unit['text'][offset-unit['start']:offset-unit['start']+len(word)]==word):
                result.append(unit)
    return result


def _inherit_development_barrier(folder,item):
    barrier=folder/'listener-stress-queue/development-dispatch-barrier.json'
    if barrier.exists():
        decision=json.loads(barrier.read_text('utf8'))
        if decision.get('state')=='awaiting_reviewed_release':
            item['development_dispatch_barrier']={'barrier_file':'listener-stress-queue/development-dispatch-barrier.json',
                'reason':decision.get('reason','Waiting for the reviewed preprocessing release.'),'required_coalescing':True}
    return item


def _external_folder(store, generation):
    preview = generation.get('analysis', {}).get('external_preview') or {}
    name = preview.get('generation')
    compact=store.root.parent/('current-book-'+generation['book_id'])
    if (compact/'current-output.json').exists():
        if generation.get('config',{}).get('engine')!=ENGINE or name!=compact.name:
            raise AppError('The current audiobook publication binding is inconsistent. A correction cannot use another renderer.')
        _debug_pending(compact)
    if generation.get('config', {}).get('engine') != ENGINE or not isinstance(name, str) or not name:
        return None
    folder = (store.root.parent / name).resolve()
    if not folder.is_relative_to(store.root.parent.resolve()):
        raise AppError('The external listening generation is outside its workspace.')
    manifest = folder / 'manifest.json'
    progress = folder / 'progress.json'
    if not manifest.is_file() or not progress.is_file():
        raise AppError('The external listening generation cannot accept retakes.')
    if json.loads(progress.read_text('utf8')).get('generation') != name:
        raise AppError('The external listening checkpoint belongs to another generation.')
    return folder


def _python(folder=None):
    # Retakes reuse the production render pipeline, including its pinned
    # Russian stress preprocessor. The generic TTS venv lacks silero_stress.
    if folder is not None and (folder/'ready.json').is_file():
        full=ROOT/'training-work/.venv-asr/Scripts/python.exe'
        if full.is_file():return full
    for preferred in (ROOT / 'training-work/higgs-production/.venv/Scripts/python.exe',
                      ROOT / 'training-work/.venv-tts/Scripts/python.exe'):
        if preferred.is_file():
            return preferred
    return Path(sys.executable)


def _same_target(row, report):
    target = row.get('report') or {}
    return (row.get('generation_id') == report['generation_id']
            and target.get('segment_id') == report['segment_id']
            and (target.get('corrections') or [{k: target.get(k) for k in ('selected_text','intended_stress','text_offset')}])
                == (report.get('corrections') or [{k: report.get(k) for k in ('selected_text','intended_stress','text_offset')}]))


def _same_request(row, report):
    target = row.get('report') or {}
    return _same_target(row,report) and target.get('audio_hash') == report['audio_hash']


def _priority(store, generation, segment, reported_id):
    if segment['id'] == reported_id:
        return [0, 0, segment.get('ordinal', 0)]
    current = None
    rows = store.rows('SELECT value FROM positions WHERE book_id=?', (generation['book_id'],)) if hasattr(store, 'rows') else []
    if rows:
        with contextlib.suppress(ValueError, TypeError):
            position = json.loads(rows[0]['value'])
            if position.get('generation_id') == generation['id']:
                current = next((row for row in generation['segments'] if row['id'] == position.get('segment_id')), None)
    pivot = current.get('ordinal', 0) if current else 0
    ordinal = segment.get('ordinal', 0)
    return [1 if ordinal >= pivot else 2, abs(ordinal - pivot), ordinal]


def _target_reports(generation, report):
    state = report.get('character_state') or {}
    if state.get('status') != 'updated':
        return [(next(row for row in generation['segments'] if row['id'] == report['segment_id']), report)]
    targets = []
    for target in propagation_targets(generation, state):
        segment = target['segment']
        corrections = target['corrections']
        derived = {**report, 'segment_id': segment['id'], 'chapter_id': segment['chapter_id'],
                   'source_id': segment['source_id'], 'audio_hash': segment['audio_hash'],
                   'passage_text': segment['text'], 'spoken_text': segment['spoken'],
                   'selected_text': corrections[0]['selected_text'],
                   'intended_stress': corrections[0]['intended_stress'],
                   'text_offset': corrections[0]['text_offset'], 'corrections': corrections}
        targets.append((segment, derived))
    return targets or [(next(row for row in generation['segments'] if row['id'] == report['segment_id']), report)]


def _start_worker(folder, data_dir, generation_id):
    prior=_WORKERS.get(str(folder))
    if prior is not None and prior.poll() is None:return
    state=folder/'listener-stress-queue/worker-state.json'
    if state.exists():
        with contextlib.suppress(OSError,ValueError,KeyError,ImportError):
            import psutil
            identity=json.loads(state.read_text('utf8'))
            with contextlib.suppress(psutil.Error):
                process=psutil.Process(identity['pid'])
                command=process.cmdline()
                if (abs(process.create_time()-identity['create_time'])<.01
                    and str(ROOT/'scripts/process_listener_stress_queue.py') in command
                    and str(folder) in command):return
    logs = folder / 'listener-stress-queue/logs'
    logs.mkdir(parents=True, exist_ok=True)
    output = (logs / 'worker.stdout.log').open('ab')
    errors = (logs / 'worker.stderr.log').open('ab')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        child=subprocess.Popen([
            str(_python(folder)), '-X', 'utf8', '-u',
            str(ROOT / 'scripts/process_listener_stress_queue.py'),
            '--generation', str(folder), '--data-dir', str(data_dir),
            '--generation-id', generation_id,
        ], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
           creationflags=flags, close_fds=True)
        _WORKERS[str(folder)]=child
    finally:
        output.close()
        errors.close()


def _enqueue_generation(store, report):
    """Return queue metadata, or None when this is not an external stress report."""
    if report.get('kind') != 'stress' or not report.get('intended_stress'):
        return None
    generation = store.generation(report['generation_id'])
    folder = _external_folder(store, generation)
    if folder is None:
        return None
    queue = folder / 'listener-stress-queue'
    queue.mkdir(parents=True, exist_ok=True)
    existing_rows = []
    for path in queue.glob('*.json'):
        with contextlib.suppress(OSError, ValueError, TypeError):
            existing_rows.append(json.loads(path.read_text('utf8')))
    reported = next((row for row in generation['segments'] if row['id'] == report['segment_id']), None)
    applied_original = next((row for row in existing_rows
                             if row.get('status') == 'applied' and _same_target(row,report)), None)
    if (reported is None or reported.get('audio_hash') != report.get('audio_hash')) and not (
            (report.get('character_state') or {}).get('status') == 'updated' and applied_original):
        raise AppError('The reported take changed before its stress retake was queued.')
    created, reused = [], []
    targets = _target_reports(generation, report)
    for index, (segment, target_report) in enumerate(targets):
        duplicate = next((row for row in existing_rows if row.get('status') in ACTIVE | {'applied'}
                          and (_same_request(row, target_report)
                               or row.get('status') == 'applied' and _same_target(row,target_report))), None)
        if duplicate:
            reused.append(duplicate)
            continue
        identifier = report['id'] if segment['id'] == report['segment_id'] else report['id'] + '-' + segment['id']
        kept = {key: target_report.get(key) for key in (
            'id', 'book_id', 'generation_id', 'segment_id', 'chapter_id', 'source_id',
            'selected_text', 'text_offset', 'audio_offset', 'audio_hash', 'passage_text',
            'spoken_text', 'created', 'kind', 'comment', 'intended_stress', 'corrections')}
        item = {
            'schema': 2,
            'id': identifier,
            'status': 'queued',
            'created': now(),
            'updated': now(),
            'priority': _priority(store, generation, segment, report['segment_id']),
            'generation_id': report['generation_id'],
            'external_generation': folder.name,
            'report': kept,
            'series_character': report.get('character_state') if len(targets) > 1 else None,
            'publication': (segment.get('performance') or {}).get('external_preview') or {},
            'human_audio_approval': False,
        }
        origin=report.get('_origin_report') or _origin(report)
        item.update(origin_report=origin,origin_report_sha256=digest(origin))
        if _debug_pending(folder):
            item.update(phase='awaiting_render_completion',reason='The current frozen render will finish before this saved correction is applied automatically.')
        _inherit_development_barrier(folder,item)
        path = queue / f"{item['id']}.json"
        if path.exists():
            # A previously held immutable attempt keeps its evidence. A repaired
            # implementation gets a distinct request instead of rewriting it.
            item['id'] = report['id'] + '-retry-' + hashlib.sha256(
                json.dumps([segment['id'], kept.get('corrections'),
                    file_digest(ROOT / 'scripts/process_listener_stress_queue.py'),
                    file_digest(ROOT / 'scripts/revise_higgs_spoken_take.py')],
                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
            path = queue / f"{item['id']}.json"
            if path.exists():
                raise AppError('A different stress retake already uses this retry identifier.')
        write_json(path, item)
        created.append(item)
    _start_worker(folder, store.root, report['generation_id'])
    first = created[0] if created else reused[0]
    return {'id': first['id'], 'status': first['status'], 'existing': not created,
            'targets': len(targets), 'queued': len(created), 'reused': len(reused),
            'character': (report.get('character_state') or {}).get('canonical_name'),
            **({key:first[key] for key in ('phase','reason') if key in first}),
            'family_status': (report.get('character_state') or {}).get('family_status')}


def enqueue_listener_stress_retake(store, report):
    """Apply a note to the current book, even when an old tab reported it."""
    if report.get('kind')!='stress' or not report.get('intended_stress'):return None
    if not hasattr(store,'generation_list'):
        return _enqueue_generation(store,report)
    current=store.current_generation(report['book_id'])if hasattr(store,'current_generation')else None
    generations=store.generation_list(report['book_id'])
    candidates=[current['id']]if current else [row['id'] for row in generations[:1]]
    # A saved listening position can belong to an immutable historical version.
    # Its heard take remains the report's origin, not a second render target.
    receipts=[]
    for gid in dict.fromkeys(candidates):
        generation=store.generation(gid)
        folder=_external_folder(store,generation)
        if folder is None:continue
        targets=[s for s in generation['segments'] if s['source_id']==report['source_id']
            and type(report.get('text_offset')) is int
            and s['start']<=report['text_offset']<s['end'] and s.get('audio_hash')]
        if len(targets)!=1:
            # The current external generation has already frozen its plans.
            # It cannot pretend a note was applied merely because the heard
            # passage has not been published here yet. Keep a durable refresh
            # hold for the renderer and the listening UI instead of sending a
            # nonexistent receipt to the legacy retake worker.
            pending=[s for s in generation['segments'] if s['source_id']==report['source_id']
                and type(report.get('text_offset')) is int
                and s['start']<=report['text_offset']<s['end']]
            if not pending:pending=_planned_report_targets(folder,report)
            queue=folder/'listener-stress-queue';queue.mkdir(parents=True,exist_ok=True)
            identifier=report['id']+'-preparation-refresh'
            path=queue/(identifier+'.json')
            reason=('The listener correction is saved, but this passage has no published take in the current '
                    'frozen generation. Its pending preparation must be refreshed before synthesis.')
            if not pending:reason=('The listener correction is saved, but the reported source passage is outside '
                    'the current frozen generation coverage. Preparation refresh needs source coverage review.')
            bound={**report,'generation_id':gid,
                'reported_generation_id':report['generation_id'],
                'segment_id':pending[0]['id'] if len(pending)==1 else report['segment_id']}
            deferred=bool(pending and (folder/'ready.json').exists())
            origin=_origin(report)
            item={'schema':2,'id':identifier,'kind':'preparation_refresh_required','status':'queued' if deferred else 'held',
                'created':now(),'updated':now(),'generation_id':gid,'external_generation':folder.name,
                'report':bound,'pending_segment_ids':[s['id'] for s in pending],
                'origin_report':origin,'origin_report_sha256':digest(origin),
                'phase':'awaiting_render_completion' if deferred else 'source_coverage_hold',
                'reason':reason,'error':'' if deferred else reason,'human_audio_approval':False}
            existed=path.exists()
            _inherit_development_barrier(folder,item)
            if not existed:write_json(path,item)
            else:item=json.loads(path.read_text('utf8'))
            if item['status']=='queued':_start_worker(folder,store.root,gid)
            receipts.append({'id':item['id'],'status':item['status'],'existing':existed,
                'targets':len(pending),'queued':1 if item['status']=='queued' else 0,'reused':0,
                'kind':item['kind'],'error':item['error'],'phase':item['phase'],'reason':item['reason']})
            continue
        segment=targets[0]
        derived={**report,'generation_id':gid,'segment_id':segment['id'],
            'chapter_id':segment['chapter_id'],'audio_hash':segment['audio_hash'],
            'spoken_text':segment['spoken'],'reported_generation_id':report['generation_id'],
            '_origin_report':_origin(report)}
        receipts.append(_enqueue_generation(store,derived))
    if not receipts:return None
    first=next((row for row in receipts if row.get('status')=='held'),receipts[0])
    return {**first,'targets':sum(r['targets'] for r in receipts),
        'queued':sum(r['queued'] for r in receipts),'generations':len(receipts)}


def resume_external_listener_queues(store):
    """Restart durable explicit correction owners; worker locks prevent duplicates."""
    for generation in store.rows('SELECT id,book_id,config,analysis FROM generations'):
        config=json.loads(generation['config'])
        if config.get('engine')!=ENGINE:continue
        full={**generation,'config':config,'analysis':json.loads(generation['analysis'])}
        with contextlib.suppress(AppError,OSError,ValueError):
            folder=_external_folder(store,full)
            if folder is None:continue
            pending=False
            for path in (folder/'listener-stress-queue').glob('*.json'):
                row=json.loads(path.read_text('utf8'))
                if not (row.get('report') or {}).get('id'):continue
                previous_barrier=row.get('development_dispatch_barrier')
                _inherit_development_barrier(folder,row)
                if row.get('development_dispatch_barrier')!=previous_barrier:write_json(path,row)
                if (row.get('status')=='held' and row.get('phase')=='awaiting_source_repair'
                    and row.get('deferred_hold_code','').startswith('listener_source_') and not _debug_pending(folder)):
                    # Source repair/resume has now completed. Reuse the saved
                    # human request; no listener resubmission or semantic vote.
                    history=folder/'listener-stress-queue/hold-history'/(row['id']+'-'+digest(row)[:12]+'.json')
                    if not history.exists():write_json(history,row)
                    row.update(status='queued',phase='awaiting_render_completion',updated=now(),error='',
                        resumed_after_source_repair=history.name)
                    write_json(path,row)
                if row.get('status') in ACTIVE:pending=True;break
            if pending:_start_worker(folder,store.root,generation['id'])


def _queue_status(rows,runtime_jobs):
    if runtime_jobs:
        status=next((state for state in ('held','running','queued','paused') if any(r['status']==state for r in runtime_jobs)),'applied')
        return {'status':status,'targets':sum(len(json.loads(row['segment_ids'])) for row in runtime_jobs),
            'applied':sum(len(json.loads(row['segment_ids'])) for row in runtime_jobs if row['status']=='applied'),
            **({'error':next(row['error'] for row in runtime_jobs if row['error'])} if any(row['error'] for row in runtime_jobs) else {})}
    if not rows:return None
    effective={}
    for row in sorted(rows,key=lambda item:(item.get('created',''),item.get('updated',''),item.get('id',''))):
        target=row.get('report') or {}
        # A newer attempt for the same passage supersedes the old held result;
        # its immutable historical request stays on disk.
        key=(target.get('source_id'),target.get('segment_id'),target.get('text_offset'))
        effective[key]=row
    rows=list(effective.values())
    status=next((state for state in ('held','running','queued') if any(r.get('status')==state for r in rows)),'applied')
    errors=[r['error'] for r in rows if r.get('error')]
    published=[r for r in rows if r.get('status')=='applied' and
        (r.get('published_generation_id') or (r.get('result') or {}).get('published_generation_id'))]
    delivery={}
    if published:
        latest=max(published,key=lambda r:(r.get('updated',''),r.get('created',''),r.get('id','')))
        result=latest.get('result') or {}
        delivered=latest.get('published_generation_id') or result['published_generation_id']
        siblings=[r for r in published if (r.get('published_generation_id') or (r.get('result') or {}).get('published_generation_id'))==delivered]
        delivery={'published_generation_id':delivered,
            'affected_segment_ids':sorted({value for r in siblings for value in (r.get('affected_segment_ids') or (r.get('result') or {}).get('affected_segment_ids') or [])}),
            'affected_source_ids':sorted({value for r in siblings for value in (r.get('affected_source_ids') or (r.get('result') or {}).get('affected_source_ids') or [])})}
    origins=sorted({r['report']['generation_id'] for r in rows if (r.get('report') or {}).get('generation_id')})
    return {'status':status,'targets':len(rows),'applied':sum(r.get('status')=='applied' for r in rows),
            'origin_generation_ids':origins,**delivery,
            **({'phase':'awaiting_preparation_release','reason':'Waiting for the reviewed preprocessing release; correction will run automatically afterwards.'}
               if status in ACTIVE and any(r.get('phase')=='awaiting_preparation_release' for r in rows) else {}),
            **({'phase':'awaiting_render_completion','reason':'Waiting for the current frozen render to finish; the saved correction will run automatically.'}
               if status in ACTIVE and any(r.get('phase')=='awaiting_render_completion' for r in rows) else {}),
            **({'error':errors[0]} if errors else {})}


def record_enqueue_failure(store, report, error):
    """Persist the hold even when broken publication controls cannot host a queue."""
    identifier='enqueue-failure-'+digest({'report_id':report['id'],'book_id':report['book_id']})[:24]
    generation_id=store.current_generation_id(report['book_id']) or report['generation_id']
    reason=str(error)[:4000]
    stamp=now()
    with store.lock,store.transaction():
        store.db.execute('''INSERT OR IGNORE INTO listener_correction_jobs
            (id,report_id,book_id,generation_id,segment_ids,status,created,updated,error)
            VALUES(?,?,?,?,?,'held',?,?,?)''',
            (identifier,report['id'],report['book_id'],generation_id,'[]',stamp,stamp,reason))
    return {'id':identifier,'status':'held','targets':0,'queued':0,'error':reason,
            'kind':'enqueue_failure','durable':True}


def queue_status_index(store,book_id):
    """Read each queue once; never load whole generation segment arrays to poll notes."""
    external={};jobs={};enqueue_failures={}
    for job in store.rows('SELECT * FROM listener_correction_jobs WHERE book_id=?',(book_id,)):
        target=enqueue_failures if job['id'].startswith('enqueue-failure-') else jobs
        target.setdefault(job['report_id'],[]).append(job)
    for row in store.rows('SELECT id,book_id,config,analysis FROM generations WHERE book_id=?',(book_id,)):
        generation={**row,'config':json.loads(row['config']),'analysis':json.loads(row['analysis'])}
        if generation['config'].get('engine')!=ENGINE:continue
        with contextlib.suppress(AppError,OSError,ValueError):
            folder=_external_folder(store,generation)
            if folder is None:continue
            for path in (folder/'listener-stress-queue').glob('*.json'):
                item=json.loads(path.read_text('utf8'));identifier=(item.get('report') or {}).get('id')
                if identifier:external.setdefault(identifier,[]).append(item)
    # A real accepted queue owns retry/delivery status. Keep the failed enqueue
    # receipt as history, but never let it mask subsequent successful work.
    return {identifier:_queue_status(external.get(identifier,[]),jobs.get(identifier,[]) or
                (enqueue_failures.get(identifier,[]) if not external.get(identifier) else []))
        for identifier in external.keys()|jobs.keys()|enqueue_failures.keys()}


def report_queue_status(store, report, index=None):
    """Read durable queue outcomes without rewriting the original human note."""
    return (queue_status_index(store,report['book_id']) if index is None else index).get(report['id'])
