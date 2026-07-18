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

    def test_emoji_picker_can_actually_close(self):
        # display:grid on the popover would override the [hidden] UA rule —
        # the explicit [hidden] restatement is what lets the toggle work
        css = (_ROOT / "webui" / "static" / "style.css").read_text(
            encoding="utf-8", errors="replace")
        assert ".chat-emoji-pop[hidden] { display: none !important; }" in css
        # and clicking anywhere else dismisses it
        assert "toggleEmojiPicker(true);" in _CHAT_JS.split("page.addEventListener('click'")[1][:400]

    def test_spoiler_reveals_on_click(self):
        assert "data-chat-spoiler" in _CHAT_JS
        assert "chat-spoiler--shown" in _CHAT_JS

    def test_own_room_echo_renders_rich(self):
        assert "rich: state.view === 'room'" in _CHAT_JS

    def test_external_clients_are_tagged_and_filterable(self):
        # a plaintext ROOM message = a non-SoulSync client; tagged + dimmed,
        # and the head toggle can hide them entirely (persisted)
        assert "state.view === 'room' && !m.rich && !self" in _CHAT_JS
        assert "chat-ext-tag" in _CHAT_JS and "via Soulseek" in _CHAT_JS
        assert "data-chat-filter" in _CHAT_JS
        assert "localStorage.setItem('chat_ss_only'" in _CHAT_JS
        assert "from other Soulseek clients hidden" in _CHAT_JS

    def test_auto_join_has_an_opt_out(self):
        ws = (_ROOT / "web_server.py").read_text(encoding="utf-8", errors="replace")
        loop = ws.split("def _emit_chat_push_loop")[1].split("\ndef ")[0]
        # without this the loop re-joins an opted-out user every 6s (un-leaveable)
        assert "soulseek.chat_auto_join" in loop

    def test_deep_links_are_path_whitelisted(self):
        # only universal shapes chip: artist source-ids + tmdb video ids;
        # 'library'-source video ids are local rows and must never travel
        assert "video-detail\\/tmdb\\/(?:movie|show)" in _CHAT_JS
        assert "chat-ss-chip" in _CHAT_JS
        assert "video-detail/library" not in _CHAT_JS.replace(
            "NEVER 'library'-source video paths", "")

    def test_embeds_are_click_to_load_and_private(self):
        # nothing fetches until the reader clicks (IP privacy), youtube goes
        # through the nocookie host, and nothing sends a referrer out
        assert "data-chat-embed-img" in _CHAT_JS
        assert "youtube-nocookie.com/embed/" in _CHAT_JS
        # every remote-media emitter carries it: yt iframe, expanded image,
        # gif auto-embed, gif picker previews
        assert _CHAT_JS.count('referrerpolicy="no-referrer"') >= 4
