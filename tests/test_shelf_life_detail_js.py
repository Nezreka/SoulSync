"""Run the JS tests for the Shelf Life panel on the detail page.

an admin sees the title's stored score (or its gate), the top reasons, when it
was scored, and a Keep toggle. members never fetch it.

The contract tests live in `tests/static/test_shelf_life_detail.mjs` and run via
Node's built-in runner; this shim surfaces them in the regular pytest sweep.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_FILE = _REPO_ROOT / "tests" / "static" / "test_shelf_life_detail.mjs"


from tests._node_runner import NODE, node_available, node_path


def test_shelf_life_detail_js():
    """Pin what the detail page shows an admin for each Shelf Life state."""
    if not node_available():
        pytest.skip("Node.js >= 22 required to run the JS Shelf Life detail tests")

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
            "JS Shelf Life detail tests failed:\n\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}",
            pytrace=False,
        )
