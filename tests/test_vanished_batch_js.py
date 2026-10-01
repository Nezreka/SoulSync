"""Run the JS tests for the download modal's vanished-batch handling.

#1384 (cremonies): a cancelled playlist download kept reading "running" when
the modal was reopened, and the batched status poll logged "0 batches"
forever. The server had dropped the batch and nothing told the modal.

The contract tests live in `tests/static/test_vanished_batch.mjs` and run via
Node's built-in runner; this shim surfaces them in the regular pytest sweep.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_FILE = _REPO_ROOT / "tests" / "static" / "test_vanished_batch.mjs"


from tests._node_runner import NODE, node_available, node_path


def test_vanished_batch_js():
    """Pin how the modal ends a batch the server dropped (#1384)."""
    if not node_available():
        pytest.skip("Node.js >= 22 required to run the JS vanished-batch tests")

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
            "JS vanished-batch tests failed:\n\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}",
            pytrace=False,
        )
