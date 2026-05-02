#!/usr/bin/env python3
"""SoulSync development launcher.

Starts the backend and Vite dev server together, restarts the backend when
backend source files change, and handles shutdown cleanly across platforms.
"""

from __future__ import annotations

import atexit
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
LOG_DIR = ROOT_DIR / 'logs'
GUNICORN_CONFIG = ROOT_DIR / 'gunicorn.dev.conf.py'
VITE_URL = os.environ.get('SOULSYNC_WEBUI_VITE_URL', 'http://127.0.0.1:5173').rstrip('/')
VITE_LOG_FILE = Path(os.environ.get('SOULSYNC_WEBUI_VITE_LOG', str(LOG_DIR / 'webui-vite.log')))

INCLUDED_SUFFIXES = {'.py', '.html', '.jinja', '.jinja2'}
SHUTDOWN_GRACE_SECONDS = int(os.environ.get('SOULSYNC_SHUTDOWN_GRACE_SECONDS', '10'))
FORCE_KILL_ON_SHUTDOWN = os.environ.get('SOULSYNC_FORCE_KILL_ON_SHUTDOWN', '1').lower() in {
    '1',
    'true',
    'yes',
    'on',
}

shutdown_requested = False
managed_processes: list[tuple[str, subprocess.Popen, object | None]] = []


def resolve_command(*candidates: str) -> str | None:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def is_excluded(path: Path) -> bool:
    try:
        relative = path.relative_to(ROOT_DIR)
    except ValueError:
        return False

    parts = relative.parts
    if not parts:
        return False

    if any(part == '__pycache__' for part in parts):
        return True
    if parts[0] in {'.git', 'logs'}:
        return True
    if len(parts) >= 2 and parts[0] == 'webui' and parts[1] == 'node_modules':
        return True
    if len(parts) >= 3 and parts[0] == 'webui' and parts[1] == 'static' and parts[2] == 'dist':
        return True
    return False


def build_backend_env(direct_mode: bool) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault('SOULSYNC_WEB_DEV_NO_CACHE', '1')
    env.setdefault('SOULSYNC_WEBUI_VITE_DEV', '1')
    env.setdefault('SOULSYNC_WEBUI_VITE_URL', VITE_URL)
    env.setdefault('SOULSYNC_WEBUI_VITE_LOG', str(VITE_LOG_FILE))
    env.setdefault('SOULSYNC_CONFIG_PATH', str(ROOT_DIR / 'config' / 'config.json'))

    if direct_mode:
        env.setdefault('SOULSYNC_WEB_BIND_HOST', '127.0.0.1')
        env.setdefault('SOULSYNC_WEB_BIND_PORT', '8008')

    return env


def start_process(label: str, cmd: list[str], *, log_file: Path | None = None, env: dict[str, str] | None = None) -> tuple[subprocess.Popen, object | None]:
    log_handle = None
    stdout = None
    stderr = None
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_file.open('ab')
        stdout = log_handle
        stderr = log_handle

    creationflags = 0
    start_new_session = False
    if os.name == 'nt':
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        start_new_session = True

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
    except Exception:
        if log_handle is not None:
            log_handle.close()
        raise

    managed_processes.append((label, proc, log_handle))
    return proc, log_handle


def wait_for_exit(proc: subprocess.Popen, seconds: int) -> bool:
    checks = max(1, int(seconds * 10))
    for _ in range(checks):
        if proc.poll() is not None:
            return True
        time.sleep(0.1)
    return proc.poll() is not None


def stop_process(label: str, proc: subprocess.Popen, log_handle: object | None) -> None:
    if proc.poll() is not None:
        if log_handle is not None:
            log_handle.close()
        return

    print(f'Stopping {label}...')

    try:
        if os.name == 'nt':
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    if not wait_for_exit(proc, SHUTDOWN_GRACE_SECONDS):
        if not FORCE_KILL_ON_SHUTDOWN:
            print(f'{label} did not exit in time; skipping forced kill for this test run.')
        else:
            print(f'{label} did not exit in time; forcing shutdown...')
            if os.name == 'nt':
                subprocess.run(
                    ['taskkill', '/T', '/F', '/PID', str(proc.pid)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            wait_for_exit(proc, 5)

    if log_handle is not None:
        log_handle.close()


def cleanup() -> None:
    global shutdown_requested

    if shutdown_requested:
        return
    shutdown_requested = True

    for label, proc, log_handle in reversed(managed_processes):
        stop_process(label, proc, log_handle)


def compute_backend_watch_state() -> str:
    rows: list[str] = []

    for dirpath, dirnames, filenames in os.walk(ROOT_DIR):
        current_dir = Path(dirpath)
        if is_excluded(current_dir):
            dirnames[:] = []
            continue

        dirnames[:] = [
            name
            for name in dirnames
            if not is_excluded(current_dir / name) and name != '__pycache__'
        ]

        for filename in filenames:
            path = current_dir / filename
            if path.suffix not in INCLUDED_SUFFIXES:
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            rows.append(f'{stat.st_mtime_ns} {path}')

    return '\n'.join(sorted(rows))


def start_vite() -> subprocess.Popen:
    npm = resolve_command('npm', 'npm.cmd')
    if npm is None:
        raise SystemExit('npm is required to run the Vite dev server.')

    print(f'Starting Vite dev server at {VITE_URL}...')
    vite_cmd = [
        npm,
        '--prefix',
        str(ROOT_DIR / 'webui'),
        'run',
        'dev',
        '--',
        '--host',
        '127.0.0.1',
        '--port',
        '5173',
    ]
    proc, _ = start_process('Vite dev server', vite_cmd, log_file=VITE_LOG_FILE, env=os.environ.copy())
    return proc


def wait_for_vite_ready(vite_proc: subprocess.Popen) -> None:
    ready_url = f'{VITE_URL}/static/dist/@vite/client'
    vite_ready = False

    for _ in range(50):
        if vite_proc.poll() is not None:
            print('Warning: Vite dev server exited before it became ready.')
            break
        try:
            with urllib.request.urlopen(ready_url, timeout=1) as response:
                if response.status < 400:
                    vite_ready = True
                    break
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(0.2)

    if vite_ready:
        print('Vite dev server is ready.')
    else:
        print('Warning: timed out waiting for the Vite dev server.')
        print('The backend will still start, but the frontend may not hot-reload yet.')


def start_backend() -> tuple[subprocess.Popen, object | None]:
    backend_mode = os.environ.get('SOULSYNC_DEV_BACKEND', '').strip().lower()
    direct_mode = backend_mode == 'direct'
    gunicorn_mode = backend_mode == 'gunicorn'

    if not backend_mode:
        if os.name == 'nt':
            direct_mode = True
        elif resolve_command('gunicorn') is None:
            print('gunicorn not found; falling back to direct Python server.')
            direct_mode = True
        else:
            gunicorn_mode = True

    print('Starting SoulSync web server...')

    if gunicorn_mode:
        gunicorn = resolve_command('gunicorn')
        if gunicorn is None:
            raise SystemExit('gunicorn is not available but SOULSYNC_DEV_BACKEND=gunicorn was requested.')
        print(f'Using Gunicorn config: {GUNICORN_CONFIG}')
        cmd = [gunicorn, '-c', str(GUNICORN_CONFIG), 'wsgi:application']
    else:
        print('Using direct Python server for backend.')
        cmd = [sys.executable, str(ROOT_DIR / 'web_server.py')]

    proc, log_handle = start_process(
        'SoulSync web server',
        cmd,
        env=build_backend_env(direct_mode),
    )
    return proc, log_handle


def watch_and_run_backend() -> None:
    last_state = compute_backend_watch_state()
    backend_proc, backend_log = start_backend()

    try:
        while not shutdown_requested:
            time.sleep(1)

            if backend_proc.poll() is not None:
                print('SoulSync web server exited. Restarting...')
                stop_process('SoulSync web server', backend_proc, backend_log)
                managed_processes.pop()
                backend_proc, backend_log = start_backend()
                last_state = compute_backend_watch_state()
                continue

            current_state = compute_backend_watch_state()
            if current_state != last_state:
                print('Detected backend file changes. Restarting SoulSync web server...')
                last_state = current_state
                stop_process('SoulSync web server', backend_proc, backend_log)
                managed_processes.pop()
                backend_proc, backend_log = start_backend()
    finally:
        if backend_proc.poll() is None:
            stop_process('SoulSync web server', backend_proc, backend_log)
            if managed_processes:
                managed_processes.pop()


def main() -> int:
    if not (ROOT_DIR / 'webui' / 'node_modules').is_dir():
        print('webui/node_modules is missing.')
        print('Run: cd webui && npm install')
        return 1

    vite_proc = start_vite()
    try:
        wait_for_vite_ready(vite_proc)
        print(f'Vite log: {VITE_LOG_FILE}')
        print('Backend file watching is enabled.')
        watch_and_run_backend()
    finally:
        cleanup()
    return 0


def _handle_signal(signum: int, _frame) -> None:
    raise SystemExit(130 if signum == signal.SIGINT else 143)


signal.signal(signal.SIGINT, _handle_signal)
if hasattr(signal, 'SIGTERM'):
    signal.signal(signal.SIGTERM, _handle_signal)
if hasattr(signal, 'SIGBREAK'):
    signal.signal(signal.SIGBREAK, _handle_signal)

atexit.register(cleanup)

if __name__ == '__main__':
    raise SystemExit(main())
