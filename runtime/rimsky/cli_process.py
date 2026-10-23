"""Constrained process boundary for subscription-backed analysis CLIs."""
from __future__ import annotations

import os
import signal
import subprocess
import contextlib
from pathlib import Path

from .util import ProviderUnavailableError, signal_group


SAFE_ENVIRONMENT = {
    'APPDATA', 'CODEX_HOME', 'COMSPEC', 'HOME', 'HOMEDRIVE', 'HOMEPATH',
    'LANG', 'LOCALAPPDATA', 'PATH', 'PATHEXT', 'PROGRAMDATA', 'SYSTEMDRIVE',
    'SYSTEMROOT', 'TEMP', 'TMP', 'USERPROFILE', 'WINDIR',
    'ALL_PROXY', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY',
    'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
}


def analysis_environment(source=None) -> dict[str, str]:
    """Keep login/config locations, while never forwarding API keys or tokens."""
    source = os.environ if source is None else source
    return {key: value for key, value in source.items() if key.upper() in SAFE_ENVIRONMENT}


def trusted_executable(names, *, label: str) -> str:
    """Resolve from explicit absolute PATH entries; never search the working folder."""
    names = (names,) if isinstance(names, str) else tuple(names)
    extensions = ['']
    if os.name == 'nt':
        extensions.extend(item.lower() for item in os.environ.get('PATHEXT', '.COM;.EXE;.BAT;.CMD').split(os.pathsep) if item)
    for raw in os.environ.get('PATH', '').split(os.pathsep):
        if not raw:
            continue
        directory = Path(raw).expanduser()
        if not directory.is_absolute():
            continue
        for name in names:
            base = directory / name
            candidates = [base] if base.suffix else [Path(str(base) + extension) for extension in extensions]
            for candidate in candidates:
                try:
                    if candidate.is_file():
                        return str(candidate.resolve(strict=True))
                except OSError:
                    continue
    raise ProviderUnavailableError(f'{label} is not installed or is not on a trusted absolute PATH entry.')


def _posix_descendants(parent):
    try:
        listing = subprocess.run(
            ['ps', '-eo', 'pid=,ppid='], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    children = {}
    for line in listing.splitlines():
        try:
            pid, ppid = map(int, line.split())
        except (TypeError, ValueError):
            continue
        children.setdefault(ppid, []).append(pid)
    found, pending = [], [parent]
    while pending:
        direct = children.get(pending.pop(), [])
        found.extend(direct)
        pending.extend(direct)
    return found


def terminate_process_tree(process) -> None:
    if process.poll() is not None:
        return
    if os.name == 'nt':
        system = Path(os.environ.get('SYSTEMROOT', r'C:\Windows')) / 'System32' / 'taskkill.exe'
        try:
            subprocess.run([str(system), '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        # CLI adapters run in their own sessions so their own timeout can stop
        # every helper. Kill descendants first so pausing the worker also reaches
        # those nested CLI sessions.
        for pid in reversed(_posix_descendants(process.pid)):
            with contextlib.suppress(OSError, ProcessLookupError):
                if not signal_group(pid, signal.SIGKILL):
                    os.kill(pid, signal.SIGKILL)
        if not signal_group(process.pid, signal.SIGKILL):
            process.kill()


def run_cli(command, *, input_text='', cwd=None, timeout=900, stdout=subprocess.PIPE):
    """Run without a shell, with bounded environment, tree timeout and tolerant decoding."""
    options = {'stdin': subprocess.PIPE, 'stdout': stdout, 'stderr': subprocess.PIPE,
               'cwd': cwd, 'env': analysis_environment()}
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options['start_new_session'] = True
    process = subprocess.Popen(command, **options)
    try:
        output, error = process.communicate(input_text.encode('utf-8'), timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_process_tree(process)
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode,
                                       '' if output is None else output.decode('utf-8', 'replace'),
                                       '' if error is None else error.decode('utf-8', 'replace'))
