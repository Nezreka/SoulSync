"""Rich chat rendering (richchat P2) — the XSS contract, run for real.

Every decoded !SS1! envelope is remote input; renderRich/renderPlain are the
line between rich chat and stored XSS from a stranger on Soulseek. The
behavioral contract lives in tests/js/chat_render_harness.mjs (node, real
regex engine); this wrapper runs it and pins the wiring.
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
def test_render_harness_passes():
    # relative path + cwd: the WSL-interop node.exe can't open /mnt/... paths
    res = subprocess.run([_node(), "chat_render_harness.mjs"],
                         cwd=str(_ROOT / "tests" / "js"),
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr


class TestWiring:
    def test_rich_flag_selects_the_renderer(self):
        assert "m.rich ? renderRich(m.message) : renderPlain(m.message)" in _CHAT_JS

    def test_escape_first_then_format(self):
        # renderRich must never touch un-escaped text
        assert "esc(_preclean(text))" in _CHAT_JS
        # NUL sentinels can't be reached from user input
        assert "replace(/\\u0000/g, '')" in _CHAT_JS

    def test_toolbar_is_room_only(self):
        assert "state.view === 'room' && state.canSend" in _CHAT_JS
        html = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")
        for hook in ("data-chat-toolbar", "data-chat-emoji-pop",
                     'data-chat-fmt="bold"', 'data-chat-fmt="spoiler"'):
            assert hook in html, f"missing {hook}"

    def test_spoiler_reveals_on_click(self):
        assert "data-chat-spoiler" in _CHAT_JS
        assert "chat-spoiler--shown" in _CHAT_JS

    def test_own_room_echo_renders_rich(self):
        assert "rich: state.view === 'room'" in _CHAT_JS

    def test_embeds_are_click_to_load_and_private(self):
        # nothing fetches until the reader clicks (IP privacy), youtube goes
        # through the nocookie host, and nothing sends a referrer out
        assert "data-chat-embed-img" in _CHAT_JS
        assert "youtube-nocookie.com/embed/" in _CHAT_JS
        assert _CHAT_JS.count('referrerpolicy="no-referrer"') == 2   # img + iframe
