"""Run the JS tests for `webui/static/chat-protocol.js` under pytest.

The contract tests live in `tests/static/test_chat_protocol.mjs` and run via
Node's built-in test runner. Determinism across clients IS the feature the
protocol library exists for, so a regression here breaks room coordination
(jukebox votes, coordinator election, the assume-SoulSync presence flip).

Skipped when node isn't available or is older than 22 — same policy as
tests/test_auto_sync_js.py.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_FILE = _REPO_ROOT / "tests" / "static" / "test_chat_protocol.mjs"


def _node_available() -> bool:
    if not shutil.which("node"):
        return False
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
    if result.returncode != 0:
        return False
    raw = (result.stdout or "").strip().lstrip("v")
    try:
        major = int(raw.split(".")[0])
    except (ValueError, IndexError):
        return False
    return major >= 22


@pytest.mark.skipif(not _node_available(), reason="node >= 22 not available")
def test_chat_protocol_js_suite():
    result = subprocess.run(
        ["node", "--test", str(_TEST_FILE)],
        capture_output=True, text=True, timeout=120, cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, (
        "chat-protocol JS tests failed:\n" + (result.stdout or "") + (result.stderr or "")
    )
