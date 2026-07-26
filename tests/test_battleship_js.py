"""Run the JS suites for the Arcade's commitment layer under pytest.

`chat-hash.js` is a synchronous SHA-256. It exists because commit-reveal has
to be verified INSIDE the pure fold and crypto.subtle is async, and because a
cheap 32-bit hash would let a player commit to one Battleship board and reveal
a different one that collides — the exact cheat the commitment prevents. The
suite checks it against the published NIST vectors and against node's own
crypto, never against itself.

`test_battleship.mjs` covers the first Arcade game with hidden information.
The fold cannot see a board, so the owner answers shots and an answer is a
claim; the tests are largely about whether a lie survives the reveal.

Skipped when node isn't available or is older than 22 — same policy as
tests/test_chat_protocol_js.py.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SUITES = (
    _REPO_ROOT / "tests" / "static" / "test_chat_hash.mjs",
    _REPO_ROOT / "tests" / "static" / "test_battleship.mjs",
)


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
@pytest.mark.parametrize("suite", _SUITES, ids=lambda p: p.stem)
def test_js_suite(suite):
    result = subprocess.run(
        ["node", "--test", str(suite)],
        capture_output=True, text=True, timeout=180, cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"{suite.name} failed:\n{result.stdout}\n{result.stderr}"
    )
