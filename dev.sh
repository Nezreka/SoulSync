#!/bin/sh
# Compatibility wrapper for the Python launcher.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT_DIR/dev.py" "$@"
fi

if command -v python >/dev/null 2>&1; then
  exec python "$SCRIPT_DIR/dev.py" "$@"
fi

echo "Python is required to run the SoulSync dev launcher."
exit 1
