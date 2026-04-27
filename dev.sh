#!/bin/bash
# SoulSync Development Launcher Script
# Starts the Python backend and Vite dev server together for local work.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
mkdir -p "$SCRIPT_DIR/logs"

DEV_GUNICORN_CONFIG="$SCRIPT_DIR/gunicorn.dev.conf.py"
GUNICORN_CONFIG="$DEV_GUNICORN_CONFIG"

VITE_URL="${SOULSYNC_WEBUI_VITE_URL:-http://127.0.0.1:5173}"
VITE_LOG_FILE="${SOULSYNC_WEBUI_VITE_LOG:-$SCRIPT_DIR/logs/webui-vite.log}"

VITE_PID=""
SERVER_PID=""
SHUTTING_DOWN="0"
SHUTDOWN_GRACE_SECONDS="${SOULSYNC_SHUTDOWN_GRACE_SECONDS:-10}"
FORCE_KILL_ON_SHUTDOWN="${SOULSYNC_FORCE_KILL_ON_SHUTDOWN:-1}"

stop_process_group() {
  local pid="$1"
  local label="$2"

  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    return
  fi

  echo "Stopping ${label}..."
  kill -TERM -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true

  local max_checks=$((SHUTDOWN_GRACE_SECONDS * 10))
  for _ in $(seq 1 "$max_checks"); do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done

  if kill -0 "$pid" 2>/dev/null; then
    if [[ "$FORCE_KILL_ON_SHUTDOWN" != "1" ]]; then
      echo "${label} did not exit in time; skipping forced kill for this test run."
      wait "$pid" 2>/dev/null || true
      return
    fi
    echo "${label} did not exit in time; forcing shutdown..."
    kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
  fi

  wait "$pid" 2>/dev/null || true
}

cleanup() {
  if [[ "$SHUTTING_DOWN" == "1" ]]; then
    return
  fi
  SHUTTING_DOWN="1"

  stop_process_group "${SERVER_PID}" "SoulSync web server"
  stop_process_group "${VITE_PID}" "Vite dev server"
}

trap cleanup EXIT INT TERM

start_in_own_session() {
  local pid_file="$1"
  shift

  local log_file=""
  if [[ "${1:-}" == "--log-file" ]]; then
    log_file="$2"
    shift 2
  fi

  python3 - "$pid_file" "$log_file" "$@" <<'PY'
import subprocess
import sys

pid_file = sys.argv[1]
log_file = sys.argv[2]
cmd = sys.argv[3:]
stdout = None
stderr = None
log_handle = None

if log_file:
    log_handle = open(log_file, "ab")
    stdout = log_handle
    stderr = log_handle

try:
    process = subprocess.Popen(cmd, start_new_session=True, stdout=stdout, stderr=stderr)
    with open(pid_file, "w", encoding="utf-8") as pid_handle:
        pid_handle.write(str(process.pid))
finally:
    if log_handle is not None:
        log_handle.close()
PY
}

start_server() {
  echo "Starting SoulSync web server..."
  echo "Using Gunicorn config: ${GUNICORN_CONFIG}"
  local pid_file
  pid_file="$(mktemp "$SCRIPT_DIR/logs/.gunicorn-pid.XXXXXX")"
  start_in_own_session "$pid_file" gunicorn -c "${GUNICORN_CONFIG}" wsgi:application
  SERVER_PID="$(<"$pid_file")"
  rm -f "$pid_file"
}

stop_server() {
  stop_process_group "${SERVER_PID}" "SoulSync web server"
  SERVER_PID=""
}

compute_backend_watch_state() {
  python3 - "$SCRIPT_DIR" <<'PY'
import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
excluded_dirs = {
    root / '.git',
    root / 'logs',
    root / 'webui' / 'node_modules',
    root / 'webui' / 'static' / 'dist',
}
included_suffixes = {'.py', '.html', '.jinja', '.jinja2'}
rows = []

for dirpath, dirnames, filenames in os.walk(root):
    current_dir = Path(dirpath)
    if current_dir in excluded_dirs:
        dirnames[:] = []
        continue
    if any(part == '__pycache__' for part in current_dir.parts):
        dirnames[:] = []
        continue

    dirnames[:] = [
        name
        for name in dirnames
        if (current_dir / name) not in excluded_dirs and name != '__pycache__'
    ]

    for filename in filenames:
        path = current_dir / filename
        if any(part == '__pycache__' for part in path.parts):
            continue
        if path.suffix not in included_suffixes:
            continue
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        rows.append(f'{stat.st_mtime_ns} {path}')

for row in sorted(rows):
    print(row)
PY
}

watch_and_run_server() {
  local last_state=""
  local current_state=""

  last_state="$(compute_backend_watch_state)"
  start_server

  while true; do
    sleep 1

    if [[ "$SHUTTING_DOWN" == "1" ]]; then
      return
    fi

    if [[ -n "${SERVER_PID}" ]] && ! kill -0 "${SERVER_PID}" 2>/dev/null; then
      echo "SoulSync web server exited. Restarting..."
      start_server
      last_state="$(compute_backend_watch_state)"
      continue
    fi

    current_state="$(compute_backend_watch_state)"
    if [[ "$current_state" != "$last_state" ]]; then
      echo "Detected backend file changes. Restarting SoulSync web server..."
      last_state="$current_state"
      stop_server
      start_server
    fi
  done
}

if [[ ! -d "$SCRIPT_DIR/webui/node_modules" ]]; then
  echo "webui/node_modules is missing."
  echo "Run: cd webui && npm install"
  exit 1
fi

echo "Starting Vite dev server at ${VITE_URL}..."
mkdir -p "$(dirname "$VITE_LOG_FILE")"
VITE_PID_FILE="$(mktemp "$SCRIPT_DIR/logs/.vite-pid.XXXXXX")"
start_in_own_session "$VITE_PID_FILE" --log-file "$VITE_LOG_FILE" npm --prefix "$SCRIPT_DIR/webui" run dev -- --host 127.0.0.1 --port 5173
VITE_PID="$(<"$VITE_PID_FILE")"
rm -f "$VITE_PID_FILE"

if command -v curl >/dev/null 2>&1; then
  READY_URL="${VITE_URL}/static/dist/@vite/client"
  vite_ready="0"
  for _ in {1..50}; do
    if ! kill -0 "${VITE_PID}" 2>/dev/null; then
      echo "Warning: Vite dev server exited before it became ready."
      break
    fi
    if curl -fsS "$READY_URL" >/dev/null 2>&1; then
      vite_ready="1"
      break
    fi
    sleep 0.2
  done
  if [[ "$vite_ready" == "1" ]]; then
    echo "Vite dev server is ready."
  else
    echo "Warning: timed out waiting for the Vite dev server."
    echo "The backend will still start, but the frontend may not hot-reload yet."
  fi
else
  sleep 2
fi

echo "Vite log: $VITE_LOG_FILE"
echo "Backend file watching is enabled."
watch_and_run_server
