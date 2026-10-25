"""One current audiobook with atomic, independently replaceable audio parts."""
from __future__ import annotations
import json
import unicodedata
import os
from contextlib import contextmanager
from functools import lru_cache
from .util import AppError, now, uid

FIELDS=('generation_id','id','ordinal','chapter_id','source_id','start','end','text','spoken','speaker','delivery',
        'status','audio_hash','duration','cues','warning','performance')


def install(db):
    tables='''
    CREATE TABLE IF NOT EXISTS current_audiobooks (
        book_id TEXT PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
        generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS audiobook_replacements (
        generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
        segment_id TEXT NOT NULL,payload TEXT NOT NULL,created TEXT NOT NULL,
        PRIMARY KEY(generation_id,segment_id));
    CREATE TABLE IF NOT EXISTS audiobook_part_receipts (
        generation_id TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
        segment_id TEXT NOT NULL,audio_hash TEXT NOT NULL,payload TEXT NOT NULL,
        retired TEXT NOT NULL,PRIMARY KEY(generation_id,segment_id,audio_hash));
    CREATE TABLE IF NOT EXISTS audio_leases (
        id TEXT NOT NULL,audio_hash TEXT NOT NULL,pid INTEGER NOT NULL,birth REAL NOT NULL,
        PRIMARY KEY(id,audio_hash));
    CREATE TABLE IF NOT EXISTS export_snapshots (
        export_id TEXT PRIMARY KEY REFERENCES exports(id) ON DELETE CASCADE,payload TEXT NOT NULL);
    '''
    # executescript commits an existing transaction before running its SQL.
    # These fixed table declarations must participate in Store's migration.
    for statement in tables.split(';'):
        if statement.strip():db.execute(statement)
    payload='json_object('+','.join("'%s',OLD.%s"%(key,key)for key in FIELDS)+')'
    for operation,condition in (
        ('UPDATE',"NEW.status!='ready' OR "+' OR '.join(f'NEW.{key} IS NOT OLD.{key}'for key in FIELDS if key not in ('generation_id','id','status'))),
        ('DELETE','1')):
        db.execute(f'DROP TRIGGER IF EXISTS audiobook_keep_part_{operation.lower()}')
        db.execute(f'''CREATE TRIGGER audiobook_keep_part_{operation.lower()}
            BEFORE {operation} ON segments WHEN OLD.status='ready' AND OLD.audio_hash IS NOT NULL
            AND ({condition}) AND EXISTS(SELECT 1 FROM generations WHERE id=OLD.generation_id)
            AND NOT EXISTS(SELECT 1 FROM audiobook_replacements AS prior
                WHERE prior.generation_id=OLD.generation_id
                AND json_extract(prior.payload,'$.source_id')=OLD.source_id
                AND json_extract(prior.payload,'$.start')<OLD.end
                AND OLD.start<json_extract(prior.payload,'$.end'))
            BEGIN INSERT OR IGNORE INTO audiobook_replacements(generation_id,segment_id,payload,created)
            VALUES(OLD.generation_id,OLD.id,{payload},strftime('%Y-%m-%dT%H:%M:%fZ','now')); END''')


def decoded(payload):
    row=json.loads(payload)
    for key in ('cues','performance'):
        if isinstance(row.get(key),str):row[key]=json.loads(row[key])
    return row


def previous_parts(store,gid):
    return [decoded(row['payload'])for row in store.rows('SELECT payload FROM audiobook_replacements WHERE generation_id=? ORDER BY created,rowid',(gid,))]


def capture_parts(store,generation,segment_ids,*,verdicts=None):
    """Freeze playable parts and their old export eligibility before edits.

    Transaction-neutral: the caller owns the atomic desired-input mutation.
    Hidden ready children are absent from playable(), so never become fallback.
    """
    from .store import speaker_review_holds
    ids=set(segment_ids);gid=generation['id']
    shown=playable(store,gid,generation['segments'])
    analysis=generation.get('analysis')or{}
    held=speaker_review_holds(shown,analysis.get('speaker_resolution',[]),
        analysis.get('speaker_verdicts')if verdicts is None else verdicts)[1]
    for part in shown:
        if part['id']not in ids or part['status']!='ready'or not part.get('audio_hash'):continue
        payload={key:part[key]for key in FIELDS}
        payload['export_blocked']=part.get('export_blocked',part['id']in held)
        store.db.execute('''INSERT INTO audiobook_replacements VALUES(?,?,?,?)
            ON CONFLICT(generation_id,segment_id) DO UPDATE SET
                payload=json_set(audiobook_replacements.payload,'$.export_blocked',json(?))
            WHERE json_type(audiobook_replacements.payload,'$.export_blocked') IS NULL''',
            (gid,part['id'],json.dumps(payload,ensure_ascii=False),now(),json.dumps(payload['export_blocked'])))


def replacement_state(store,gid,working):
    """An old part remains current until its entire source range is ready."""
    previous=previous_parts(store,gid)
    if not previous:return [],[]
    by_source={}
    for row in working:by_source.setdefault(row['source_id'],[]).append(row)
    pending=[];complete=[]
    generation=store._generation_metadata(gid)
    blocks={b['id']:b['text']for c in store._book_manifest(generation['book_id'])['chapters']for b in c['blocks']}
    for old in previous:
        candidates=[row for row in by_source.get(old['source_id'],[])
                    if row['start']<old['end'] and old['start']<row['end']]
        flags=bytearray(old['end']-old['start']);validated=False
        for row in candidates:
            if row['status']!='ready' or not row['audio_hash']:continue
            if (blocks.get(row['source_id'],'')[row['start']:row['end']]!=row['text']or
                row['audio_hash']==old['audio_hash']and any(row[key]!=old[key]for key in ('spoken','speaker','delivery'))):continue
            try:
                audio=store.audio_path(row['audio_hash']);metadata=audio.with_suffix('.json')
                actual=checked_audio(str(audio),audio.stat().st_mtime_ns,audio.stat().st_size,metadata.stat().st_mtime_ns)
                if abs(row['duration']-actual['duration'])>1/actual['sample_rate']:continue
            except (AppError,OSError):continue
            validated=True
            for i in range(max(old['start'],row['start']),min(old['end'],row['end'])):flags[i-old['start']]=1
        covered=validated and all(flags[i]or ch.isspace()or unicodedata.category(ch).startswith('P')for i,ch in enumerate(old['text']))
        (complete if covered else pending).append(old)
    return pending,complete


@lru_cache(maxsize=8192)
def checked_audio(path,mtime,size,metadata_mtime):
    from pathlib import Path
    from .integrity import accepted_audio
    return accepted_audio(Path(path))


def playable(store,gid,working):
    pending,_=replacement_state(store,gid,working)
    # A replacement child hidden by an older whole part was never current.
    # Historical nested snapshots can be ignored, but overlapping whole WAVs
    # which cannot form a source partition must never be played twice or cut.
    selected=[]
    for old in sorted(pending,key=lambda row:(row['source_id'],row['start'],-row['end'])):
        previous=next((row for row in reversed(selected)if row['source_id']==old['source_id']),None)
        if previous and old['start']<previous['end']:
            if old['end']<=previous['end']:continue
            raise AppError('Saved audiobook fallback parts overlap. Repair their source coverage before playback.')
        selected.append(old)
    pending=selected
    rows=[row for row in working if not any(old['source_id']==row['source_id']and
          old['start']<row['end']and row['start']<old['end']for old in pending)]
    for old in pending:
        current=next((row for row in working if row['id']==old['id']),None)
        rows.append({**old,'replacement_status':current['status']if current else 'pending'})
    return sorted(rows,key=lambda row:row['ordinal'])


def release_completed(store,gid):
    working=store._generation_segments(gid)
    _,completed=replacement_state(store,gid,working)
    for old in completed:
        archive_part(store,gid,old)
        store.db.execute('DELETE FROM audiobook_replacements WHERE generation_id=? AND segment_id=?',(gid,old['id']))
    return len(completed)


def archive_part(store,gid,old):
    """Keep heard source/input identity without pinning retired audio bytes."""
    if not old.get('audio_hash'):return
    compact={key:old[key]for key in ('id','chapter_id','source_id','start','end','text','spoken','duration','audio_hash')}
    synthesis=old.get('performance',{}).get('synthesis_snapshot')
    if synthesis is None:
        metadata=store.audio_path(old['audio_hash']).with_suffix('.json')
        try:
            saved=json.loads(metadata.read_text('utf-8'))
            synthesis=saved.get('synthesis')or({'parts':saved['synthesis_parts']}if saved.get('synthesis_parts')else None)
        except (OSError,ValueError):pass
    compact['performance']={'synthesis_snapshot':synthesis}
    store.db.execute('INSERT OR IGNORE INTO audiobook_part_receipts(generation_id,segment_id,audio_hash,payload,retired) VALUES(?,?,?,?,?)',
        (gid,old['id'],old['audio_hash'],json.dumps(compact,ensure_ascii=False),now()))


def heard_part(store,gid,sid,audio_hash):
    if not audio_hash:return None
    if not store.generation(gid)['analysis'].get('retired'):
        for row in store.playback_generation(gid)['segments']:
            if row['id']==sid and row['audio_hash']==audio_hash:return row
    rows=store.rows('SELECT payload FROM audiobook_part_receipts WHERE generation_id=? AND segment_id=? AND audio_hash=?',(gid,sid,audio_hash))
    if not rows:raise AppError('The reported audio does not match this passage. Refresh the audiobook and select the heard passage again.')
    return json.loads(rows[0]['payload'])


@contextmanager
def audio_lease(store,hashes):
    import psutil
    identifier=uid();pid=os.getpid();birth=psutil.Process(pid).create_time()
    with store.lock,store.transaction():
        store.db.executemany('INSERT INTO audio_leases VALUES(?,?,?,?)',[(identifier,key,pid,birth)for key in set(hashes)if key])
    try:yield
    finally:
        with store.lock,store.transaction():store.db.execute('DELETE FROM audio_leases WHERE id=?',(identifier,))


def active_leases(store):
    import psutil
    kept=set()
    for row in store.rows('SELECT * FROM audio_leases'):
        try:alive=abs(psutil.Process(row['pid']).create_time()-row['birth'])<.01
        except psutil.NoSuchProcess:alive=False
        except psutil.AccessDenied:alive=True
        if alive:kept.add(row['audio_hash'])
        else:store.db.execute('DELETE FROM audio_leases WHERE id=?',(row['id'],))
    return kept
