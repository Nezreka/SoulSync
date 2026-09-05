"""Run the JS tests for `webui/static/chat-games.js` under pytest.

The contract tests live in `tests/static/test_chat_games.mjs` and run via
Node's built-in test runner.

An Arcade match is protocol carriers in a Soulseek room and nothing else —
no server arbitrates it. Every client folds the same carriers into the same
game, so the failure mode of a bug here is not an exception: it is two
players looking at different boards, or a carrier moving a piece nobody
played. The suite therefore concentrates on disagreement and hostility —
seat races, moves out of turn or off-ply, checkpoints that contradict the
computed position, and seat claims judged on stream timestamps rather than a
local clock.

Skipped when node isn't available or is older than 22 — same policy as
tests/test_chat_protocol_js.py.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_FILE = _REPO_ROOT / "tests" / "static" / "test_chat_games.mjs"


from tests._node_runner import assert_node_suite, node_available


@pytest.mark.skipif(not node_available(), reason="node >= 22 not available")
def test_chat_games_js_suite():
    assert_node_suite(_TEST_FILE, "chat-games", cwd=_REPO_ROOT)
