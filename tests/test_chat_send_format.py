"""Room send format: envelope or plain (the "sent in bugs, showed up in
general" report).

"All messages" mode sends plain text so vanilla Soulseek clients can read it.
But a channel other than #general, and any thread, only exists inside the
envelope - a vanilla client is never "in" #bugs. Plain mode used to strip the
channel tag on the way out, so a message typed in #bugs landed in #general
with no warning (nanomite Sep 16, Deathwing Sep 17). The channel you are
standing in now wins over the filter. The behavioral contract lives in
tests/js/chat_send_format_harness.mjs (node, the real chat.js); this wrapper
runs it and pins the wiring.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CHAT_JS = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8", errors="replace")


def _node():
    return shutil.which("node") or shutil.which("node.exe")


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_send_format_harness_passes():
    # relative path + cwd: the WSL-interop node.exe can't open /mnt/... paths
    res = subprocess.run([_node(), "chat_send_format_harness.mjs"],
                         cwd=str(_ROOT / "tests" / "js"),
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr


class TestWiring:
    def test_plain_mode_is_general_only(self):
        body = _CHAT_JS.split("function _plainOn() {", 1)[1].split("\n    }\n", 1)[0]
        assert "state.ssOnly) return false" in body
        assert "_chanRoom()" in body and "state.thread" in body
        assert "CHAT_DEFAULT_CHANNEL" in body

    def test_every_send_path_tags_through_one_helper(self):
        # the composer, file share and GIF picker all stamp the payload here;
        # a hand-built payload is how the wrong-channel bug got in the first time
        assert _CHAT_JS.count("_tagRoomPayload(") >= 3

    def test_harness_hooks_exported(self):
        assert "_plainOn: _plainOn, _tagRoomPayload: _tagRoomPayload" in _CHAT_JS
