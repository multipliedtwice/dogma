"""Bind each published speech asset to an immutable timeline in one checkpoint."""
import json,os,time
from contextlib import contextmanager
from pathlib import Path
from higgs_trial_io import read_json as read
from rimsky.util import digest,file_digest,atomic_write,write_json

@contextmanager
def publication_lock(generation,blocking=True):
    path=generation/'publication.lock';path.parent.mkdir(parents=True,exist_ok=True)
    handle=path.open('a+b')
    if path.stat().st_size==0:handle.write(b'0');handle.flush()
    acquired=False;deadline=time.monotonic()+60
    try:
        while not acquired:
            handle.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                acquired=True
            except OSError:
                if not blocking:break
                if time.monotonic()>deadline:raise TimeoutError('Another publisher holds the chapter checkpoint')
                time.sleep(.05)
        yield acquired
    finally:
        if acquired:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)
        handle.close()


def read_timeline(generation,chapter,ready,require_quality=True):
    binding=ready.get('timeline_asset')
    if binding:
        path=(generation/binding['file']).resolve()
        if (not path.is_relative_to((generation/chapter).resolve())
            or binding['audio_sha256']!=ready['wav_sha256'] or file_digest(path)!=binding['sha256']):
            raise ValueError('Published timeline differs from its bound audio asset')
    else:path=generation/chapter/(chapter+'.timeline.json')
    rows=read(path)
    if require_quality and len(rows)!=ready['units']:raise ValueError('Published timeline has different unit coverage')
    flags=sum(r.get('word_status') not in ('asr_exact_match','nonverbal_not_spoken_text') for r in rows)
    if require_quality and flags!=ready['word_flags']:raise ValueError('Published timeline disagrees with the audio flag checkpoint')
    if rows and ready.get('seconds') is not None and max(r['end'] for r in rows)>ready['seconds']+1/24000:
        raise ValueError('Published timeline runs beyond its audio asset')
    return rows


def pin_timeline(generation,chapter,ready,rows):
    encoded=json.dumps(rows,ensure_ascii=False,indent=2).encode('utf8');sha=digest(encoded)
    path=generation/chapter/(chapter+'-'+ready['wav_sha256'][:16]+'-'+sha[:16]+'.timeline.json')
    if path.exists():
        if file_digest(path)!=sha:raise ValueError('Immutable published timeline changed')
    else:atomic_write(path,encoded)
    ready['timeline_asset']={'file':path.relative_to(generation).as_posix(),'sha256':sha,'audio_sha256':ready['wav_sha256']}


def commit_publication(generation,chapter,ready,rows,state):
    with publication_lock(generation):
        _commit_publication(generation,chapter,ready,rows,state)


def _commit_publication(generation,chapter,ready,rows,state):
    from render_higgs_palette_preview import PAGE,MEDIA,page
    from higgs_preview_music import attach_music
    pin_timeline(generation,chapter,ready,rows)
    state['chapters'][chapter]=ready
    # This is the atomic speech commit. A failed music mix cannot roll speech
    # or its flags back to an older waveform with different timeline offsets.
    write_json(generation/'progress.json',state)
    write_json(generation/chapter/(chapter+'.timeline.json'),rows)
    try:attach_music(generation/chapter,chapter,state,PAGE,MEDIA)
    except Exception as exc:
        ready['music_pending']='Verified speech published; music mixing needs retry.'
        write_json(generation/chapter/'music-publication-failure.json',{'error':str(exc),'speech_sha256':ready['wav_sha256']})
    write_json(generation/'progress.json',state);write_json(PAGE/'progress.json',state);page()
