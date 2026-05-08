"""Run the JS tests for `webui/static/discover-section-controller.js`
under the regular pytest sweep.

The actual contract tests live in
`tests/static/test_discover_section_controller.mjs` and run via
Node.js's stable built-in test runner (`node --test`). This shim
shells out to that runner and asserts a clean exit so the JS tests
fail the suite if the controller contract drifts.

Skipped when:
  - `node` isn't on PATH (e.g. Python-only dev container).
  - Node version < 22 (the built-in `--test` runner went stable in 18
    but the assert-flavor we use is 22+).

Run directly:
    node --test tests/static/test_discover_section_controller.mjs
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_FILE = _REPO_ROOT / "tests" / "static" / "test_discover_section_controller.mjs"


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
    # Output looks like "v22.21.0"
    raw = (result.stdout or "").strip().lstrip("v")
    try:
        major = int(raw.split(".")[0])
    except (ValueError, IndexError):
        return False
    return major >= 22


def test_discover_section_controller_js():
    """Pin the JS controller's lifecycle contract via `node --test`."""
    if not _node_available():
        pytest.skip("Node.js >= 22 required to run the JS controller tests")

    if not _TEST_FILE.exists():
        pytest.skip(f"JS test file missing: {_TEST_FILE}")

    result = subprocess.run(
        ["node", "--test", str(_TEST_FILE)],
        capture_output=True, text=True,
        cwd=str(_REPO_ROOT),
        timeout=60,
    )

    if result.returncode != 0:
        # Surface the node test runner output so failures are
        # debuggable from the pytest log without re-running by hand.
        pytest.fail(
            "JS controller tests failed:\n\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}",
            pytrace=False,
        )
