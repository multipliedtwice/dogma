from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from .util import AppError


@contextmanager
def library_lease(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    stream = (root / '.runtime.lock').open('a+b')
    acquired = False
    try:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError as error:
                raise AppError('This library is already open in another Dogma process.') from error
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as error:
                raise AppError('This library is already open in another Dogma process.') from error
        yield
    finally:
        if acquired:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()
