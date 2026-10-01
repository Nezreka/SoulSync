"""Run the JS tests for reopening an album's download modal after its run.

#1386 (cremonies): after downloading one song from an album, closing the
modal and searching the album again, the modal replayed the previous run
until a browser refresh.

The contract tests live in `tests/static/test_album_modal_reopen.mjs` and run
via Node's built-in runner; this shim surfaces them in the regular pytest sweep.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_FILE = _REPO_ROOT / "tests" / "static" / "test_album_modal_reopen.mjs"


from tests._node_runner import NODE, node_available, node_path


def test_album_modal_reopen_js():
    """Pin that reopening an album after its run finished starts fresh (#1386)."""
    if not node_available():
        pytest.skip("Node.js >= 22 required to run the JS album-modal-reopen tests")

    if not _TEST_FILE.exists():
        pytest.skip(f"JS test file missing: {_TEST_FILE}")

    result = subprocess.run(
        [NODE, "--test", node_path(_TEST_FILE)],
        capture_output=True, text=True,
        cwd=str(_REPO_ROOT),
        timeout=60,
    )

    if result.returncode != 0:
        pytest.fail(
            "JS album-modal-reopen tests failed:\n\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}",
            pytrace=False,
        )
