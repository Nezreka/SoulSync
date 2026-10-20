"""Run the JS tests for the download modal's watched-batch keepalive.

spectro (discord): picking candidates for the not-found rows of a finished
download died a few minutes in with "Task not found". the server dropped the
batch 5 minutes after completion even with the modal still open.

The contract tests live in `tests/static/test_watched_batch.mjs` and run via
Node's built-in runner; this shim surfaces them in the regular pytest sweep.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_FILE = _REPO_ROOT / "tests" / "static" / "test_watched_batch.mjs"


from tests._node_runner import NODE, node_available, node_path


def test_watched_batch_js():
    """Pin that an open modal tells the server it is still watching."""
    if not node_available():
        pytest.skip("Node.js >= 22 required to run the JS watched-batch tests")

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
            "JS watched-batch tests failed:\n\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}",
            pytrace=False,
        )
