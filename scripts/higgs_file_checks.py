"""Reuse verified hashes only while the exact on-disk file identity is unchanged."""
from pathlib import Path
from rimsky.util import file_digest
_CACHE={}


def cached_sha(path):
    path=Path(path).resolve();stat=path.stat()
    signature=(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,stat.st_ino)
    old=_CACHE.get(path)
    if old and old[0]==signature:return old[1]
    value=file_digest(path);after=path.stat()
    if signature!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns,after.st_ino):
        raise ValueError('File changed during checksum verification: '+str(path))
    _CACHE[path]=(signature,value);return value


def persistent_sha(path):
    """cached_sha across processes: model files are re-verified only when their identity changes.

    Recognizer audits run as fresh processes and hashed about 3.4 GB of model
    files every time (0.8 min per 12-minute clip, 2026-10-16). The cache keys on
    the same identity signature as cached_sha and stores nothing else.
    """
    import json, os
    path = Path(path).resolve(); stat = path.stat()
    signature = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino]
    store = Path(__file__).resolve().parents[1] / 'training-work' / '.file-sha-cache.json'
    try: cache = json.loads(store.read_text('utf-8'))
    except (OSError, ValueError): cache = {}
    entry = cache.get(str(path))
    if entry and entry[0] == signature: return entry[1]
    value = cached_sha(path)
    cache[str(path)] = [signature, value]
    temporary = store.with_suffix('.tmp.' + str(os.getpid()))
    temporary.write_text(json.dumps(cache), 'utf-8'); os.replace(temporary, store)
    return value
