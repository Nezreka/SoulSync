"""Run the JS tests for `webui/static/chess-engine.js` under pytest.

The contract tests live in `tests/static/test_chess_engine.mjs` and run via
Node's built-in test runner.

Arcade games are played over a Soulseek room with no server: every client
folds the same move stream through this engine, so a legality bug does not
raise an error anywhere — it just leaves two players looking at different
boards. The suite therefore runs PERFT against the standard positions, which
counts every leaf of the move tree and so catches a single missing or
spurious move at any depth.

Skipped when node isn't available or is older than 22 — same policy as
tests/test_chat_protocol_js.py.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_FILE = _REPO_ROOT / "tests" / "static" / "test_chess_engine.mjs"


from tests._node_runner import assert_node_suite, node_available


@pytest.mark.skipif(not node_available(), reason="node >= 22 not available")
def test_chess_engine_js_suite():
    assert_node_suite(_TEST_FILE, "chess engine", cwd=_REPO_ROOT)
