"""Run the JS tests for the Library-maintenance card title under pytest.

#1211 (wishx): four maintenance jobs running at once showed four identical
"Library maintenance" cards, so there was no way to tell which was which, or
which one to stop when a container got bogged down.

Nothing was missing from the data. `_repair_job_start` (web_server.py) has
always put the job's ``display_name`` in the progress state and the
``repair:progress`` emit sends that state verbatim — the card just read
``t.name`` / ``t.job_name``, neither of which exists, so it fell through to
the generic label every time.

The contract tests live in `tests/static/test_repair_card_names.mjs`; this
shim surfaces them in the regular pytest sweep.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_FILE = _REPO_ROOT / "tests" / "static" / "test_repair_card_names.mjs"


def _node_available() -> bool:
    if not shutil.which("node"):
        return False
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=10)
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


def test_repair_card_names_js():
    """Pin the maintenance card title via `node --test` (#1211)."""
    if not _node_available():
        pytest.skip("Node.js >= 22 required to run the JS card-title tests")
    if not _TEST_FILE.exists():
        pytest.skip(f"JS test file missing: {_TEST_FILE}")

    result = subprocess.run(
        ["node", "--test", str(_TEST_FILE)],
        capture_output=True, text=True, cwd=str(_REPO_ROOT), timeout=60)

    if result.returncode != 0:
        pytest.fail(
            "JS maintenance-card-title tests failed:\n\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}",
            pytrace=False,
        )


def test_the_card_reads_display_name_without_node():
    """Node-independent tripwire: the card must read the key the server sends.

    The behavioural proof is in the .mjs above, but node isn't on PATH
    everywhere (that wrapper skips there) and this fix is one key name wide.
    """
    src = (_REPO_ROOT / "webui" / "static" / "downloads.js").read_text(
        encoding="utf-8", errors="replace")
    body = src[src.index("function _musicRepairActiveHTML("):]
    body = body[:body.index("\n}")]
    # CODE only — the comment above the fix names the old broken keys, and a
    # naive substring check would happily match its own documentation.
    code = "\n".join(ln for ln in body.splitlines()
                     if not ln.strip().startswith("//"))
    assert "t.display_name" in code, (
        "the card stopped reading display_name, so every running job renders "
        "as the same generic 'Library maintenance' card again (#1211)"
    )
