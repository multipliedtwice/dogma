from __future__ import annotations
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

class AppError(Exception):
    def __init__(self, message: str = '', code: str = '', **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details

class AnalysisCapacityError(AppError):
    pass

class AnalysisInvalidError(AppError):
    """A provider answered, but its analysis could not pass validation."""
    pass

class EngineTransportError(AppError):
    pass

class GenerationCapError(AppError):
    pass

class SpeechHoldError(AppError):
    """A requested voice/control or output needs a new verified take."""
    pass

class ProviderUnavailableError(AppError):
    pass

class ProviderTransportError(AppError):
    pass

ERROR_CODES = {'analysis_capacity': AnalysisCapacityError, 'analysis_invalid': AnalysisInvalidError,
               'generation_cap': GenerationCapError,
               'speech_hold': SpeechHoldError,
               'provider_unavailable': ProviderUnavailableError, 'provider_transport': ProviderTransportError}

def signal_group(pid: int, signal_number: int) -> bool:
    killpg = getattr(os, 'killpg', None)
    if killpg is None:
        return False
    try:
        killpg(pid, signal_number)
        return True
    except (ProcessLookupError, PermissionError):
        return False

def now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def uid() -> str:
    return os.urandom(12).hex()

def digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()

def check_stop(stop) -> None:
    if stop is not None and stop.is_set():
        raise AppError('Preparation paused.')

def file_digest(path: Path, stop=None) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            check_stop(stop)
            h.update(chunk)
    check_stop(stop)
    return h.hexdigest()

CODEC_FRAME_RATE = 12.5
OS_METADATA = frozenset({'.ds_store', 'thumbs.db', 'desktop.ini'})

def os_metadata(name: str) -> bool:
    return name.casefold() in OS_METADATA or name.startswith('._')

def reached_generation_cap(samples: int, rate: int, max_new_tokens: int) -> bool:
    return samples >= max_new_tokens * rate / CODEC_FRAME_RATE * .98

def append_warning(existing: str, *parts: str) -> str:
    return ' '.join(filter(None, [existing, *(part for part in dict.fromkeys(parts) if part and part not in existing)]))

def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_replace(source, destination) -> None:
    """Replace a file while readers retain the previous Windows file handle."""
    if os.name == 'nt' and Path(destination).exists():
        import ctypes
        replace = ctypes.WinDLL('kernel32', use_last_error=True).ReplaceFileW
        replace.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                            ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        replace.restype = ctypes.c_int
        # Python/antivirus readers can briefly hold a Windows handle without
        # FILE_SHARE_DELETE. Retry the atomic operation; never delete the old
        # file as a workaround or publish a partially written replacement.
        for attempt in range(20):
            if replace(str(destination), str(source), None, 0, None, None):
                return
            error = ctypes.get_last_error()
            if error in (2, 3):
                break
            if error not in (5, 32, 33, 1175, 1224) or attempt == 19:
                raise ctypes.WinError(error)
            time.sleep(min(.01 * (attempt + 1), .1))
    os.replace(source, destination)

def write_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2).encode())

def inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise AppError('Path is outside the permitted directory.')
    return path

def require_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', value):
        raise AppError('Invalid identifier.')
    return value

def bounded_text(value: Any, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise AppError(f'Expected nonempty text of at most {maximum} characters.')
    return value.strip()

INVISIBLE = re.compile('[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb'
                       '\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0001\U000e0020-\U000e007f]')

def display_text(value: str) -> str:
    return re.sub(r'\s+', ' ', INVISIBLE.sub('', value)).strip()

LANGUAGE_NAMES = {'ru': 'Russian', 'rus': 'Russian', 'russian': 'Russian', 'русский': 'Russian',
                  'en': 'English', 'eng': 'English', 'english': 'English', 'английский': 'English'}

def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(re.match(r'[^\W\d_]*', (code or '').strip().lower()).group(), '')

def language_tag(code: str) -> bool:
    return bool(re.fullmatch(r'(?!(?:und|mul|mis|zxx)(?![a-z]))[a-z]{2,3}(?:[-_][a-z0-9]{1,8})*', (code or '').strip().lower()))

def script_language(text: str) -> str:
    cyrillic = sum('\u0400' <= character <= '\u052f' for character in text)
    latin = sum(character.isalpha() and (character < '\u0250' or '\u1e00' <= character <= '\u1eff') for character in text)
    total = cyrillic + latin
    return '' if not total else 'Russian' if cyrillic >= .75 * total else 'English' if latin >= .75 * total else ''
