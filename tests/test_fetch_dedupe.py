"""API GET dedupe (request-flood P1).

One page load fired the same GET many times from components that don't know
about each other. fetch-dedupe.js wraps window.fetch so identical same-origin
/api GET bursts share one wire request, with clone-per-consumer semantics.
The behavioral contract lives in tests/js/fetch_dedupe_harness.mjs (real
Response objects under node); this wrapper runs it and pins the wiring.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HTML = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")
_JS = (_ROOT / "webui" / "static" / "fetch-dedupe.js").read_text(encoding="utf-8", errors="replace")


def _node():
    return shutil.which("node") or shutil.which("node.exe")


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_behavioral_harness_passes():
    # relative path + cwd: under WSL the interop node.exe can't open
    # /mnt/... absolute paths, but resolves relative ones from the mapped cwd
    res = subprocess.run(
        [_node(), "tests/js/fetch_dedupe_harness.mjs"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, f"harness failed:\n{res.stdout}\n{res.stderr}"
    assert "all assertions passed" in res.stdout


def test_loaded_before_every_other_script():
    # must wrap fetch before the React bundle and all split modules run
    dedupe_at = _HTML.index("fetch-dedupe.js")
    assert dedupe_at < _HTML.index("vite_assets('body')")
    assert dedupe_at < _HTML.index("filename='core.js'")


def test_conservative_bypass_rules_present():
    # the rules that make a global fetch patch safe
    assert "'/stream'" in _JS or '"/stream"' in _JS      # GET SSE (similar artists)
    assert "socket.io" in _JS
    assert "text/event-stream" in _JS
    assert "signal" in _JS                               # abortable → bypass
    assert ".clone()" in _JS                             # single-use bodies
    assert "entries.delete(key)" in _JS                  # failures not cached
