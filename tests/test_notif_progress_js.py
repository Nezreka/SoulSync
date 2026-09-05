"""Run the JS tests for the notification progress helpers under pytest.

#1197 (wishx): a "Library maintenance" card read 100% while the counts below
it said 2,347 / 157,122. _taskClampPct treated any value <= 1 as a 0-1
fraction, so an honest integer 1 percent became 100 — which every long job
passes through on its way up.

The contract tests live in `tests/static/test_notif_progress.mjs` and run via
Node's built-in runner; this shim surfaces them in the regular pytest sweep.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_FILE = _REPO_ROOT / "tests" / "static" / "test_notif_progress.mjs"


from tests._node_runner import NODE, node_available, node_path


def test_notif_progress_js():
    """Pin the notification progress-bar maths via `node --test` (#1197)."""
    if not node_available():
        pytest.skip("Node.js >= 22 required to run the JS notification-progress tests")

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
            "JS notification-progress tests failed:\n\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}",
            pytrace=False,
        )


def test_the_one_percent_trap_cannot_come_back_without_node():
    """A node-independent tripwire for the exact line #1197 turned on.

    The behavioural proof lives in the .mjs above, but node is not on PATH in
    every dev environment (this wrapper skips there), and this fix is one
    character wide — `<=` vs `<`. Pin the operator so a local run still catches
    a regression, and pin the reason so the next reader knows why it matters.
    """
    src = (_REPO_ROOT / "webui" / "static" / "downloads.js").read_text(
        encoding="utf-8", errors="replace")
    body = src[src.index("function _taskClampPct("):]
    body = body[:body.index("\n}")]
    # CODE only: the explanatory comment above the fix quotes the old broken
    # condition, and a naive substring check flagged its own documentation.
    code = "\n".join(ln for ln in body.splitlines()
                     if not ln.strip().startswith("//"))
    assert "pct > 0 && pct < 1" in code, "the 0-1 fraction guard is gone"
    assert "pct <= 1" not in code, (
        "an integer 1 (one percent) would be multiplied to 100 again — "
        "wishx's 'Library maintenance 100%' while the counts read 2,347/157,122"
    )
