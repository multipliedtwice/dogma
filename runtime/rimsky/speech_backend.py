"""Speech backend contract. Existing unmarked packs retain legacy Qwen behavior."""
import importlib.util
from types import CodeType
from .util import AppError, digest

HIGGS = 'higgs_audio_cpp'
LEGACY = 'qwen'


def implementation_fingerprint(module_name):
    """Hash code equally in source and frozen runtimes, ignoring install paths.

    A PyInstaller module's __file__ need not exist. Code constants/bytecode are
    available through its loader; source filenames and object identities are
    excluded so the core and inference bundles agree on the same protocol.
    """
    spec = importlib.util.find_spec(module_name)
    loader = getattr(spec, 'loader', None)
    if loader is None or not hasattr(loader, 'get_code'):
        raise AppError('Speech implementation cannot be fingerprinted.')
    def encode(value):
        if isinstance(value, CodeType):
            return {'code': value.co_code.hex(), 'constants': [encode(v) for v in value.co_consts],
                    'names': value.co_names, 'variables': value.co_varnames, 'free': value.co_freevars,
                    'cells': value.co_cellvars, 'flags': value.co_flags, 'args': value.co_argcount,
                    'posonly': value.co_posonlyargcount, 'kwonly': value.co_kwonlyargcount,
                    'exceptions': getattr(value, 'co_exceptiontable', b'').hex()}
        if isinstance(value, bytes):
            return {'bytes': value.hex()}
        if isinstance(value, tuple):
            return [encode(v) for v in value]
        if isinstance(value, frozenset):
            return {'set': sorted((encode(v) for v in value), key=lambda item: repr(item))}
        if isinstance(value, complex):
            return {'complex': [value.real, value.imag]}
        if value is Ellipsis:
            return {'ellipsis': True}
        return value
    code = loader.get_code(module_name)
    if not isinstance(code, CodeType):
        raise AppError('Speech implementation code is unavailable.')
    return digest(encode(code))


def backend_name(pack):
    value = pack['speech'].get('backend', LEGACY)
    if value not in (HIGGS, LEGACY):
        raise AppError('Unsupported speech backend in the pinned voice pack.')
    return value


def is_higgs(pack):
    return backend_name(pack) == HIGGS


def backend_for(pack):
    if is_higgs(pack):
        from .higgs_backend import HiggsBackend
        return HiggsBackend()
    return None  # legacy worker path, unchanged
