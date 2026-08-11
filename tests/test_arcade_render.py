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
        # ...and `hidden` has to actually hide it. .chat-composer sets
        # display:flex, which beats the browser's own [hidden] rule, so
        # setting the attribute alone left the composer on screen.
        css = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")
        assert ".chat-composer[hidden] { display: none !important; }" in css

    def test_a_stray_send_in_the_arcade_posts_nothing(self):
        # The user is looking at a board, not at a channel — a message sent
        # here would vanish from their own view the instant it went out.
        assert "if (_arcOn()) { input.value = ''; return; }" in _CHAT_JS

    def test_moves_are_never_applied_optimistically(self):
        # Our own carrier comes back through the room like everyone else's.
        # An optimistic board could disagree with the fold with no way to
        # tell which was right.
        assert "Nothing is applied optimistically" in _CHAT_JS

    def test_illegal_moves_never_reach_the_bus(self):
        # The fold owns "what does this move produce", so this holds for
        # every variant rather than just chess. The preview MUST carry the
        # actor's seat: battleship's apply() refuses to judge a move without
        # knowing who made it, so previewing without the seat rejected every
        # battleship action — the send path was dead until the seat rode along.
        assert "if (!next) return false;                 // never put an illegal move on the bus" in _CHAT_JS
        assert "window.ChatGames.previewMove(g, uci, _arcSeat(g))" in _CHAT_JS
        # Votes are cast for the ROOM's seat, and that is the actor they
        # preview as.
        assert "window.ChatGames.previewMove(g, uci, g.roomSeat)" in _CHAT_JS

    def test_move_carries_ply_and_checkpoint(self):
        # Without `n` a duplicate delivery double-applies; without `f` a
        # client that missed the opening can never join the game.
        assert "g: gid, v: g.variant, n: g.ply, m: uci, f: next.fen," in _CHAT_JS

    def test_the_room_seat_has_no_owner(self):
        # Nobody can emit gm.move for the room, so the fold commits its move
        # once enough distinct people vote for the same one.
        games_js = (_ROOT / "webui" / "static" / "chat-games.js").read_text(encoding="utf-8")
        assert "if (game.roomSeat) return;        // that seat belongs to everyone" in games_js
        assert "if (tally[vuci] < game.voteK) return;" in games_js
        # The opponent is playing against the room and gets no ballot.
        assert "if (_seatOf(game, user)) return;               // the opponent does not vote" in games_js

    def test_sync_is_stingy(self):
        # Every carrier is visible noise to vanilla Soulseek clients, so the
        # heartbeat only fires after a long quiet spell and no more than once
        # per game per cooldown.
        assert "ARC_SYNC_QUIET" in _CHAT_JS and "ARC_SYNC_EVERY" in _CHAT_JS
        assert "if (_arcMyMove(g)) return;                          // the ball is with us" in _CHAT_JS
        assert "if (!_arcSeat(g)) return;                           // only our own games" in _CHAT_JS

    def test_any_client_may_answer_a_sync(self):
        # Deliberately widened: every SoulSync client in the room folds every
        # game, so restricting answers to the two players threw away the whole
        # room's memory. Safe because answers are corroborated, not trusted.
        assert "if (_arcSeat(g) === '') return;" not in _CHAT_JS
        assert "sendProtocol('gm.state'" in _CHAT_JS
        games_js = (_ROOT / "webui" / "static" / "chat-games.js").read_text(encoding="utf-8")
        # A seated player is still the one shortcut, and only when nothing is
        # in dispute.
        assert "} else if (!quorum && !_seatOf(game, user)) {" in games_js

    def test_recovery_is_corroborated_not_trusted(self):
        # "Furthest along wins" would reward lying outright: a bad client
        # claims ply 999 and takes over every time. Answers are grouped by
        # POSITION and need independent backing.
        games_js = (_ROOT / "webui" / "static" / "chat-games.js").read_text(encoding="utf-8")
        assert "var STATE_QUORUM = 2;" in games_js
        assert "game.answers[stFen]" in games_js
        assert "var backers = Object.keys(slot.by).length;" in games_js

    def test_recovery_never_moves_backwards(self):
        # A stale majority must not delete real moves.
        games_js = (_ROOT / "webui" / "static" / "chat-games.js").read_text(encoding="utf-8")
        assert "return;                       // we are level or ahead: we answer, not adopt" in games_js
        assert "if (tn < game.ply) return;    // even then, never rewind" in games_js

    def test_answers_are_staggered(self):
        # Every client in the room can answer, so without a spread sixteen
        # people shout the same position at once.
        assert "ARC_ANSWER_SPREAD" in _CHAT_JS
        assert "_arcAnswerDelay()" in _CHAT_JS
        assert "if (backers >= window.ChatGames.STATE_QUORUM) return;" in _CHAT_JS

    def test_automatic_sync_never_settles_a_disagreement(self):
        # Otherwise whoever re-broadcast last would simply win. A frozen game
        # moves only when a human sends gm.sync with r:1.
        games_js = (_ROOT / "webui" / "static" / "chat-games.js").read_text(encoding="utf-8")
        assert "var accepted = game.syncReq && game.syncReq.reset &&" in games_js
        assert "if (!quorum && !accepted) return;" in games_js
        assert "data-chat-arc-accept" in _CHAT_JS

    def test_acknowledgement_costs_nothing(self):
        # Derived from carriers we already send -- no ack round trip.
        assert "_arcAckHtml" in _CHAT_JS
        games_js = (_ROOT / "webui" / "static" / "chat-games.js").read_text(encoding="utf-8")
        assert "game.ack[user] = Math.max(game.ack[user] || 0" in games_js

    def test_connect4_moves_are_throttled(self):
        # Every move is a real room message that vanilla Soulseek clients see
        # as noise; chess is slow enough not to care, taps are not. The floor
        # keys on the EXACT action (game + move), not on time alone — a flat
        # global floor also ate legitimate back-to-back actions (battleship's
        # auto-answer followed by the player's own shot).
        assert "if (moveKey === _arcLastMoveKey && nowMs - _arcLastMoveAt < 600) return false;" in _CHAT_JS

    def test_fold_cache_is_keyed_on_the_log_itself(self):
        # Keying on length alone served a stale fold whenever a different log
        # happened to be the same size (caught by the render harness).
        assert "_arcCache.ref === log" in _CHAT_JS

    def test_click_to_move_exists_alongside_drag(self):
        # Drag alone would make the board unusable on a phone.
        assert "data-chat-arc-sq" in _CHAT_JS
        assert "_arcSquareClick" in _CHAT_JS
        assert "Drag is an ALTERNATIVE to click" in _CHAT_JS

    def test_ratings_order_is_not_client_local(self):
        # Elo is order-dependent, so the fold order must not come from
        # anything that differs between clients. Finish times arrive from
        # each user's own slskd, so they are compared at whole-second
        # resolution with the game id as a total tiebreak.
        games_js = (_ROOT / "webui" / "static" / "chat-games.js").read_text(encoding="utf-8")
        assert "Math.floor(a.lastAt / 1000)" in games_js
        assert "a.id < b.id ? -1" in games_js

    def test_adopted_games_are_not_rated(self):
        games_js = (_ROOT / "webui" / "static" / "chat-games.js").read_text(encoding="utf-8")
        assert "!g.partial" in games_js

    def test_card_actions_are_routed_before_the_card_itself(self):
        # A lobby card carries data-chat-arc-open and CONTAINS the action
        # buttons. Checking the card first made every Join / Withdraw /
        # Take-the-seat click resolve to the card and merely open the game --
        # which is exactly what it did, unnoticed, because the tests only
        # checked the markup contained the buttons and never that a click
        # reached them.
        def at(sel):
            return _CHAT_JS.index("closest('[%s]')" % sel)

        card = at("data-chat-arc-open")
        for action in ("data-chat-arc-join", "data-chat-arc-claim",
                       "data-chat-arc-cancel", "data-chat-arc-resign",
                       "data-chat-arc-draw"):
            assert at(action) < card, action

    def test_a_card_is_not_a_button(self):
        # <button> inside <button> is invalid HTML and the parser rearranges it.
        assert '<button class="chat-arc-card' not in _CHAT_JS
        assert '\'<div class="chat-arc-card\'' in _CHAT_JS or 'chat-arc-card' in _CHAT_JS

    def test_promotion_is_asked_not_assumed(self):
        # Under-promotion is occasionally the only winning move.
        assert "state.arcade.promo = { from: from, to: to };" in _CHAT_JS
