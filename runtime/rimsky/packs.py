from __future__ import annotations
import contextlib
import importlib.util
import json
import os
import re
import sys
import shutil
import threading
from pathlib import Path, PurePosixPath
from .casting import AGE_BANDS, ROLES, legacy_mechanical, role_review
from .presentation_voice import validate_presentation_profile
from .util import AppError, bounded_text, check_stop, digest, file_digest, inside, os_metadata, write_json
from .cli_process import trusted_executable
from . import __version__
from .speech_backend import is_higgs, backend_name

VERIFYING = threading.Lock()
MIN_CONTEXT = 8192
PINNED_CONTEXT = 4096
CONTEXT_ERROR = 'Text model context must be at least 8192 tokens.'
PROVIDERS = ('local', 'codex_cli', 'claude_cli')
ANALYSIS_MODES = ('legacy', 'staged', 'auto', 'codex_cli', 'claude_cli')
PRESENTATION_GAME_UI = (('channels', 'game_ui'), ('registers', 'game_system'), ('registers', 'item_or_stat_card'))

class PackChangedError(AppError):
    pass

def resolve_character_analysis(value: str) -> str:
    if value != 'auto':
        return value
    for provider, names, label in (
            ('codex_cli', ('codex.exe', 'codex.cmd', 'codex'), 'Codex CLI'),
            ('claude_cli', ('claude.exe', 'claude.cmd', 'claude'), 'Claude Code CLI')):
        try:
            trusted_executable(names, label=label)
            return provider
        except AppError:
            pass
    return 'local'

def context_floor(pinned=False, provider='local') -> int:
    if provider not in PROVIDERS:
        raise AppError('Character analysis provider must be local, codex_cli, or claude_cli.')
    return PINNED_CONTEXT if pinned or provider != 'local' else MIN_CONTEXT

def require_context(pack: dict, provider='local') -> dict:
    if pack['text_model'].get('context', MIN_CONTEXT) < context_floor(False, provider):
        raise AppError(CONTEXT_ERROR)
    return pack

def role_warnings(pack: dict) -> list:
    profile = pack.get('presentation_voices') or {}
    presented = {rule.get('voice') for group, name in PRESENTATION_GAME_UI
                 if isinstance(rule := profile.get(group, {}).get(name), dict) and rule.get('mode') == 'fixed'}
    warnings = []
    for voice in pack['voices']:
        if legacy_mechanical(voice):
            warnings.append(f'Voice {voice["id"]} is declared as a character but marked as game UI; it is cast only as a mechanical voice.')
        elif role_review(voice):
            warnings.append(f'Voice {voice["id"]} looks mechanical but is declared as a character; it is held out of casting until its role is reviewed.')
        elif voice['id'] in presented and voice.get('role', 'character') == 'character':
            warnings.append(f'Voice {voice["id"]} is the game UI presentation voice but is declared as a character; review its role.')
    return warnings

def load_pack(path: str, verify=False, stop=None, pinned=False, provider='local') -> dict:
    if not path:
        raise AppError('No local model pack is selected. Open Settings and choose a prepared pack.')
    root = Path(path).expanduser().resolve()
    manifest = root / 'pack.json'
    if not manifest.is_file() or manifest.stat().st_size > 1024*1024:
        raise AppError('Selected folder does not contain a valid pack.json.')
    try:
        pack = json.loads(manifest.read_text('utf-8'))
    except (ValueError,OSError) as error:
        raise AppError('Model pack manifest cannot be read.') from error
    if not isinstance(pack, dict) or pack.get('schema') != 1 or not isinstance(pack.get('voices'),list) or len(pack['voices']) < 2:
        raise AppError('The model pack needs schema 1 and at least two voices.')
    bounded_text(pack.get('name'),200)
    for field in ('text_model','speech'):
        if not isinstance(pack.get(field),dict) or not isinstance(pack[field].get('path'),str):
            raise AppError(f'Missing {field} configuration.')
        model = inside(root,pack[field]['path'])
        if not model.exists():
            raise AppError(f'Missing {field} model files.')
    if not inside(root, pack['text_model']['path']).is_file() or not inside(root, pack['speech']['path']).is_dir():
        raise AppError('The text model must be a local file and the speech model a local directory.')
    if pack['text_model'].get('analysis_mode', 'staged') not in ANALYSIS_MODES:
        raise AppError('Text analysis mode must be legacy, staged, auto, codex_cli, or claude_cli.')
    if pack['text_model'].get('dialogue_style', 'auto') not in ('auto', 'russian_dash'):
        raise AppError('Text dialogue style must be auto or russian_dash.')
    context, floor = pack['text_model'].get('context',MIN_CONTEXT), context_floor(pinned, provider)
    if type(context) is int and context < floor:
        raise AppError(CONTEXT_ERROR)
    for value, minimum, maximum in [(context,floor,131072), (pack['speech'].get('max_new_tokens',2048),128,8192)]:
        if type(value) is not int or not minimum <= value <= maximum:
            raise AppError('Invalid model context or generation limit.')
    if type(pack['speech'].get('instruction_control',False)) is not bool:
        raise AppError('Instruction control must be an explicit boolean.')
    if pack['speech'].get('language_mode','book') not in ('book','auto'):
        raise AppError('Speech language mode must be book or auto.')
    if pack['speech'].get('variant') not in ('custom','clone'):
        raise AppError('Speech variant must be custom or clone.')
    backend_name(pack)
    if not isinstance(pack.get('files'),dict) or not pack['files']:
        raise AppError('A sealed file-checksum manifest is required. Run scripts/seal_pack.py while preparing a pack.')
    voice_ids=set()
    for voice in pack['voices']:
        if not isinstance(voice, dict):
            raise AppError('Invalid voice profile.')
        vid=bounded_text(voice.get('id'),100)
        if vid != voice['id'] or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', vid):
            raise AppError('Voice IDs must be stable, whitespace-free identifiers.')
        if voice.get('role', 'character') not in ROLES:
            raise AppError('Unknown voice role.')
        if 'family' in voice and (not isinstance(voice['family'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', voice['family'])):
            raise AppError('Voice families must be stable, whitespace-free identifiers.')
        if 'group' in voice:
            bounded_text(voice['group'],64)
        tags = voice.get('tags', [])
        if not isinstance(tags, list) or len(tags) > 100 or not all(isinstance(tag, str) and len(tag) <= 200 for tag in tags):
            raise AppError('Invalid voice tags.')
        if voice.get('gender', 'female') not in ('female', 'male'):
            raise AppError('Voice gender must be female or male when present.')
        character_names = voice.get('character_names', [])
        if (not isinstance(character_names, list) or len(character_names) > 100 or
                not all(isinstance(name, str) and 0 < len(name.strip()) <= 200 for name in character_names)):
            raise AppError('Character voice bindings must be a bounded list of names.')
        ages = voice.get('age_bands', [])
        if not isinstance(ages, list) or not all(isinstance(age, str) for age in ages) or len(set(ages)) != len(ages) or not set(ages) <= set(AGE_BANDS) - {'unknown'}:
            raise AppError('Voice age bands must be distinct known age bands.')
        if vid in voice_ids:
            raise AppError('Duplicate voice ID in pack.')
        voice_ids.add(vid)
        bounded_text(voice.get('name'),200)
        if pack['speech']['variant']=='custom':
            speaker = bounded_text(voice.get('speaker'),100)
            if speaker != voice['speaker']:
                raise AppError('Custom speaker names must not contain surrounding whitespace.')
        else:
            refs=voice.get('references',{})
            if not isinstance(refs, dict) or 'neutral' not in refs:
                raise AppError(f'Voice {vid} has no neutral reference.')
            for delivery, ref in refs.items():
                from .analysis import DELIVERIES
                valid_delivery = bool(re.fullmatch(r'[a-z][a-z0-9_]{0,63}', delivery)) if is_higgs(pack) else delivery in DELIVERIES
                if not valid_delivery or not isinstance(ref, dict) or not isinstance(ref.get('audio'),str):
                    raise AppError('Invalid delivery reference.')
                if not inside(root,ref['audio']).is_file():
                    raise AppError('Voice reference file is missing.')
                bounded_text(ref.get('text'),4000)
    if sum(v.get('role') == 'narrator' for v in pack['voices']) != 1:
        raise AppError('A pack must contain exactly one narrator profile.')
    validate_presentation_profile(pack.get('presentation_voices'), voice_ids)
    if not any(v.get('role', 'character') == 'character' for v in pack['voices']):
        raise AppError('A voice pack needs at least one character performance.')
    if pack.get('aligner') is not None:
        aligner = pack['aligner']
        if not isinstance(aligner, dict) or not isinstance(aligner.get('path'), str) or not inside(root, aligner['path']).is_dir():
            raise AppError('The alignment model directory is missing or invalid.')
    if any(p.is_symlink() for p in root.rglob('*')):
        raise AppError('Symlinks are not accepted inside immutable model packs.')
    listed=set(sealed_files(pack))
    if pack_tree(root) != listed:
        raise AppError('Pack contents changed. Re-seal this pack as a new revision.')
    for relative,expected in sealed_files(pack).items():
        item=inside(root,relative)
        if not item.is_file() or not isinstance(expected,str) or re.fullmatch(r'[0-9a-f]{64}',expected) is None:
            raise AppError(f'Missing or invalid pack file: {relative}')
    pack['_root']=str(root)
    if is_higgs(pack):
        from .higgs_backend import validate_higgs_pack
        validate_higgs_pack(pack, root)
    pack['_fingerprint']=digest({k:v for k,v in pack.items() if not k.startswith('_')})
    pack['_stats']=pack_stats(pack)
    pack['_tree']=sorted(listed)
    pack['_warnings']=role_warnings(pack)
    if verify and (problem := pack_problem(pack, stop)):
        raise AppError(problem)
    return pack

def sealed_files(pack) -> dict:
    return {relative: expected for relative, expected in pack['files'].items() if not os_metadata(PurePosixPath(relative).name)}

def pack_problem(pack, stop=None) -> str:
    root = Path(pack['_root'])
    for relative, expected in sealed_files(pack).items():
        if file_digest(inside(root, relative), stop) != expected:
            return f'Pack checksum mismatch: {relative}'
    if pack['speech']['variant'] == 'clone':
        from .references import inspect_reference
        try:
            for reference in dict.fromkeys(inside(root, ref['audio']) for voice in pack['voices'] for ref in voice['references'].values()):
                inspect_reference(reference)
        except AppError as error:
            return str(error)
    return ''

def file_state(path: Path) -> list:
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns, change_time_ns(path, stat), stat.st_ino]


def change_time_ns(path: Path, stat) -> int:
    """Return metadata change time, including on Python 3.12+ for Windows.

    Python's Windows ``st_ctime`` is now the creation time, which does not
    change after an in-place write. NTFS ChangeTime is the field needed by the
    verified-pack cache so a same-size edit with restored mtime is rehashed.
    """
    if os.name != 'nt':
        return stat.st_ctime_ns
    import ctypes
    from ctypes import wintypes

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [('CreationTime', ctypes.c_longlong), ('LastAccessTime', ctypes.c_longlong),
                    ('LastWriteTime', ctypes.c_longlong), ('ChangeTime', ctypes.c_longlong),
                    ('FileAttributes', wintypes.DWORD)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    get_info = kernel.GetFileInformationByHandleEx
    get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    get_info.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80, 0x7, None, 3, 0x80, None)  # attributes; all sharing; existing file
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        return stat.st_ctime_ns
    try:
        info = FileBasicInfo()
        if not get_info(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
            return stat.st_ctime_ns
        return info.ChangeTime * 100
    finally:
        close(handle)

def pack_stats(pack) -> dict:
    root = Path(pack['_root'])
    return {relative: file_state(inside(root, relative)) for relative in sorted(sealed_files(pack))}

def pack_tree(root: Path) -> set:
    manifest = root / 'pack.json'
    return {str(p.relative_to(root)).replace(os.sep, '/') for p in root.rglob('*')
            if (p.is_symlink() or p.is_file()) and p != manifest and not os_metadata(p.name)}

def pack_unchanged(pack, *, quick=False) -> dict:
    try:
        if quick:
            cached = pack.get('_quick_stats')
            if not isinstance(cached, dict):
                cached = {relative: state for relative, state in pack['_stats'].items()
                          if state[0] <= 4 * 1024 * 1024}
                pack['_quick_stats'] = cached
            root = Path(pack['_root'])
            unchanged = all(file_state(inside(root, relative)) == state for relative, state in cached.items())
        else:
            unchanged = pack_stats(pack) == pack['_stats'] and ('_tree' not in pack or pack_tree(Path(pack['_root'])) == set(pack['_tree']))
    except (OSError, KeyError, RuntimeError, AppError):
        unchanged = False
    if not unchanged:
        raise PackChangedError('Model pack files changed after verification. Select the pack again in Settings.')
    return pack

def pack_signature(pack) -> str:
    files = [[relative, *state] for relative, state in pack_stats(pack).items()]
    return digest([__version__, pack['_fingerprint'], file_state(Path(pack['_root']) / 'pack.json'), files])

def read_verification_record(path: Path) -> dict:
    try:
        record = json.loads(Path(path).read_text('utf-8'))
        return {root: (signature, '') for root, signature in record.items() if isinstance(signature, str)}
    except (OSError, ValueError, AttributeError):
        return {}

def remember(cache: dict, record) -> None:
    if record:
        with contextlib.suppress(OSError):
            write_json(Path(record), {key: value[0] for key, value in list(cache.items()) if not value[1]})

def cached_problem(pack, cache: dict, stop=None, record=None) -> str:
    root, signature = pack['_root'], pack_signature(pack)
    while not VERIFYING.acquire(timeout=.1):
        check_stop(stop)
    try:
        entry = cache.get(root)
        if (not entry or entry[0] != signature) and (moved := next((key for key, value in list(cache.items()) if value[0] == signature), None)):
            entry = cache[root] = cache.pop(moved)
            remember(cache, record)
        if not entry or entry[0] != signature:
            problem = pack_problem(pack, stop)
            if pack_signature(pack) != signature:
                return 'Pack contents changed. Re-seal this pack as a new revision.'
            entry = cache[root] = (signature, problem)
            remember(cache, record)
        return entry[1]
    finally:
        VERIFYING.release()

def verified_pack(path, cache=None, stop=None, record=None, pinned=False, provider='local'):
    pack = load_pack(path, pinned=pinned, provider=provider)
    problem = pack_problem(pack, stop) if cache is None else cached_problem(pack, cache, stop, record)
    if problem:
        raise AppError(problem)
    return pack

def bundled_pack(resource_dir, fingerprint=None):
    manifests = sorted((Path(resource_dir) / 'packs').glob('*/pack.json'))
    if fingerprint is None:
        return str(manifests[0].parent.resolve()) if len(manifests) == 1 else None
    for manifest in manifests:
        with contextlib.suppress(AppError, OSError):
            pack = load_pack(str(manifest.parent), pinned=True)
            if pack['_fingerprint'] == fingerprint:
                return pack['_root']
    return None

def build_digest(executable):
    try:
        value = json.loads((Path(executable).parent / 'build-info.json').read_text('utf-8')).get('source_digest')
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None

def runtime_problem(binaries: Path, infer):
    if not getattr(sys, 'frozen', False):
        return ''
    if not infer:
        return 'The bundled inference runtime is missing. Reinstall the application.'
    digests = {build_digest(path) for path in (sys.executable, infer, resolve_binary('rimsky-align', binaries)) if path} - {None}
    return 'Bundled runtime components are from different builds. Reinstall the application.' if len(digests) > 1 else ''

def health(settings, binaries: Path, verify=None):
    frozen=getattr(sys,'frozen',False)
    infer=resolve_binary('rimsky-infer',binaries)
    selected=resolve_character_analysis(settings.get('character_analysis','auto'))
    external=selected in ('codex_cli','claude_cli')
    modules=('qwen_tts',) if external else ('qwen_tts','llama_cpp')
    missing=[] if infer else ['rimsky-infer'] if frozen else [module for module in modules if importlib.util.find_spec(module) is None]
    if selected in ('codex_cli', 'claude_cli'):
        names, label, missing_label = ((('codex.exe', 'codex.cmd', 'codex'), 'Codex CLI', 'codex CLI') if selected == 'codex_cli'
                                       else (('claude.exe', 'claude.cmd', 'claude'), 'Claude Code CLI', 'Claude Code CLI'))
        try:
            trusted_executable(names, label=label)
        except AppError:
            missing.append(missing_label)
    pack=None
    error=''
    try:
        pack=verify(settings['pack_path']) if verify else verified_pack(settings['pack_path'],provider=selected)
    except (AppError,KeyError,TypeError,OSError) as exc:
        error=str(exc)
    if pack and is_higgs(pack) and not infer and not frozen:
        modules = ('numpy', 'soundfile', 'torch', 'qwen_asr') + (() if external else ('llama_cpp',))
        missing = [item for item in missing if item != 'qwen_tts']
        missing.extend(module for module in modules if importlib.util.find_spec(module) is None and module not in missing)
    ffmpeg=resolve_binary('ffmpeg',binaries)
    runtime_error=runtime_problem(binaries,infer)
    return {'ready':not missing and pack is not None and not runtime_error,'missing':missing,'runtime_error':runtime_error,'pack_error':error,'pack':{'name':pack['name'],'revision':pack['_fingerprint'][:12],'voices':len(pack['voices']),'variant':pack['speech']['variant'],'warnings':pack.get('_warnings',[])} if pack else None,'ffmpeg':bool(ffmpeg),'ffprobe':bool(resolve_binary('ffprobe',binaries)),'word_alignment':bool(pack and pack.get('aligner') and (resolve_binary('rimsky-align',binaries) or not frozen and importlib.util.find_spec('qwen_asr') is not None)),'offline':not external,'version':__version__}

def resolve_binary(name: str, binaries: Path):
    folder={'rimsky-runtime':'core','rimsky-infer':'infer','rimsky-align':'align'}.get(name,'media')
    for path in (binaries/(name+'.exe'),binaries/name,binaries/folder/(name+'.exe'),binaries/folder/name):
        if path.is_file():
            return str(path)
    return None if getattr(sys, 'frozen', False) else shutil.which(name)
