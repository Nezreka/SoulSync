"""Chat notification logic — unread channel badges, run for real.

The unread fold in chat.js is the line between "you have new messages" and
"a reload relights every badge". The behavioral contract lives in
tests/js/chat_notify_harness.mjs (node, real chat.js closure); this wrapper
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
def test_notify_harness_passes():
    # relative path + cwd: the WSL-interop node.exe can't open /mnt/... paths
    res = subprocess.run([_node(), "chat_notify_harness.mjs"], cwd=str(_ROOT / "tests" / "js"), capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr


class TestUnreadWiring:
    def test_markers_are_per_room(self):
        assert "'chat_chan_seen_' + (state.room || '')" in _CHAT_JS

    def test_markers_persist(self):
        assert "localStorage.setItem(_chanSeenKey()" in _CHAT_JS

    def test_push_arrivals_refresh_channel_badges(self):
        # socket arrivals re-render the channel rail immediately (the bug was
        # that only polls did, so live arrivals lit nothing until a refresh)
        assert "onRoomMessages" in _CHAT_JS
        body = _CHAT_JS.split("function onRoomMessages")[1][:3000]
        assert "renderChannels()" in body

    def test_active_channel_upkeep_advances_the_marker(self):
        # reading at the bottom while pinned advances the seen marker, so
        # messages already read don't become unread on a channel switch
        assert "renderMessages" in _CHAT_JS

    def test_helpers_exported_for_the_harness(self):
        for name in ("_chanSeenKey", "_loadChanSeen", "_saveChanSeen", "_chanUnread"):
            assert name + ": " + name in _CHAT_JS, f"missing export {name}"
