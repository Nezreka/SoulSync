"""Run the JS tests for `webui/static/chat-protocol.js` under pytest.

The contract tests live in `tests/static/test_chat_protocol.mjs` and run via
Node's built-in test runner. Determinism across clients IS the feature the
protocol library exists for, so a regression here breaks room coordination
(jukebox votes, coordinator election, the assume-SoulSync presence flip).

Skipped when node isn't available or is older than 22 — same policy as
tests/test_auto_sync_js.py.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_FILE = _REPO_ROOT / "tests" / "static" / "test_chat_protocol.mjs"


from tests._node_runner import assert_node_suite, node_available


@pytest.mark.skipif(not node_available(), reason="node >= 22 not available")
def test_chat_protocol_js_suite():
    assert_node_suite(_TEST_FILE, "chat-protocol", cwd=_REPO_ROOT)
