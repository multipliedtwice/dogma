from __future__ import annotations

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path

from .util import AppError


def open_shared_read(path: Path):
    """A caller-owned read handle that survives atomic replacement and deletion."""
    if os.name != 'nt':
        return path.open('rb')

    # The CRT's ordinary open denies deletion on Windows. A delete-sharing handle
    # retains the old file without making concurrent repair's os.replace fail.
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # GENERIC_READ; FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    # OPEN_EXISTING; FILE_ATTRIBUTE_NORMAL. The handle is not inheritable.
    handle = create(str(path), 0x80000000, 0x7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        close(handle)
        raise
    try:
        return os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def open_audio_pair(path: Path):
    """Open only; decoding, metadata reads and hashing belong outside store.lock."""
    with ExitStack() as resources:
        try:
            audio = resources.enter_context(open_shared_read(path))
        except OSError as error:
            raise AppError('A saved audio segment is missing or cannot be opened.') from error
        try:
            metadata = resources.enter_context(open_shared_read(path.with_suffix('.json')))
        except OSError as error:
            raise AppError('Saved audio metadata is missing or invalid.') from error
        yield audio, metadata
