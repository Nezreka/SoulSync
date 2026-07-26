"""The Arcade surface (arcade P3) — escaping + honesty, run for real.

Two things can go wrong on this surface and neither raises an exception.

A Soulseek username is remote input from a stranger, and it lands in a game
card, the player line, the sidebar row and several title="" attributes — the
same XSS contract as the message renderer applies. And because the board is a
fold rather than server state, what it draws has to match what the fold says:
a "your move" badge on the wrong player, or a Join button on a game you are
already sitting in, has people making moves the room silently rejects.

The behavioral contract lives in tests/js/arcade_render_harness.mjs (node,
with the real engine and fold loaded the way the page loads them); this
wrapper runs it and pins the wiring that keeps the Arcade from disturbing the
rest of chat.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CHAT_JS = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8", errors="replace")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8", errors="replace")


def _node():
    return shutil.which("node") or shutil.which("node.exe")


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_arcade_render_harness_passes():
    # relative path + cwd: the WSL-interop node.exe can't open /mnt/... paths
    res = subprocess.run([_node(), "arcade_render_harness.mjs"],
                         cwd=str(_ROOT / "tests" / "js"),
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr


class TestWiring:
    def test_engine_and_fold_load_before_chat(self):
        # chat.js reads both off window at call time, but loading them after
        # it would leave the first render with no Arcade at all.
        # Match the script tags specifically — "chat.js" also appears in a
        # comment further up the file.
        def tag(name):
            marker = f"filename='{name}'"
            assert marker in _INDEX, name
            return _INDEX.index(marker)

        assert tag("chess-engine.js") < tag("chat.js")
        assert tag("chat-games.js") < tag("chat.js")
        # The fold depends on the engine.
        assert tag("chess-engine.js") < tag("chat-games.js")

    def test_arcade_is_a_view_not_a_channel(self):
        # The channel list must stay message-only: every channel tag has to
        # resolve somewhere a message is readable ("nothing is ever
        # invisible"), and a channel rendering a chessboard would break that.
        chans = _CHAT_JS[_CHAT_JS.index("var CHAT_CHANNELS"):
                         _CHAT_JS.index("var CHAT_DEFAULT_CHANNEL")]
        assert "arcade" not in chans
        assert "state.arcade" in _CHAT_JS

    def test_switching_to_a_channel_leaves_the_arcade(self):
        assert "state.arcade = null;      // leaving for a channel leaves the arcade" in _CHAT_JS

    def test_render_delegates_through_the_existing_entry_point(self):
        # Every existing caller of renderMessages keeps working unchanged.
        assert "if (_arcOn()) { renderArcade(); return; }" in _CHAT_JS

    def test_composer_is_hidden_in_the_arcade(self):
        assert "if (_arcOn()) { form.hidden = true; return; }" in _CHAT_JS

    def test_moves_are_never_applied_optimistically(self):
        # Our own carrier comes back through the room like everyone else's.
        # An optimistic board could disagree with the fold with no way to
        # tell which was right.
        assert "Nothing is applied optimistically" in _CHAT_JS

    def test_illegal_moves_never_reach_the_bus(self):
        assert "if (!move) return;                       // never put an illegal move on the bus" in _CHAT_JS

    def test_move_carries_ply_and_checkpoint(self):
        # Without `n` a duplicate delivery double-applies; without `f` a
        # client that missed the opening can never join the game.
        assert "g: gid, v: g.variant, n: g.ply, m: uci, f: E.toFEN(after)," in _CHAT_JS

    def test_fold_cache_is_keyed_on_the_log_itself(self):
        # Keying on length alone served a stale fold whenever a different log
        # happened to be the same size (caught by the render harness).
        assert "_arcCache.ref === log" in _CHAT_JS

    def test_click_to_move_exists_alongside_drag(self):
        # Drag alone would make the board unusable on a phone.
        assert "data-chat-arc-sq" in _CHAT_JS
        assert "_arcSquareClick" in _CHAT_JS
        assert "Drag is an ALTERNATIVE to click" in _CHAT_JS

    def test_promotion_is_asked_not_assumed(self):
        # Under-promotion is occasionally the only winning move.
        assert "state.arcade.promo = { from: from, to: to };" in _CHAT_JS
