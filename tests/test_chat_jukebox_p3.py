"""Chat next-level P3 — the room jukebox.

Queue/votes/now-playing live entirely in the protocol stream (jbx.sub /
jbx.vote / jbx.now — reduced client-side by chat-protocol.js, covered in
tests/static/test_chat_protocol.mjs). The server's only new surface is the
resolve endpoint: YouTube URL/id → keyless oEmbed lookup, free text →
yt-dlp search through an injected seam. Hermetic — both lookups are
monkeypatched, no network.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask, g

import api.chat as chat_api

_ROOT = Path(__file__).resolve().parent.parent


# ── URL/id parsing ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("q,vid", [
    ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?list=PL123&v=dQw4w9WgXcQ&t=42", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=xyz", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
])
def test_parse_youtube_id_accepts(q, vid):
    assert chat_api._parse_youtube_id(q) == vid


@pytest.mark.parametrize("q", [
    "", "daft punk around the world", "https://vimeo.com/12345",
    "shortid", "x" * 11 + "!",   # 11 chars but bad alphabet
])
def test_parse_youtube_id_rejects(q):
    assert chat_api._parse_youtube_id(q) is None


# ── resolve endpoint ────────────────────────────────────────────────────────

@pytest.fixture()
def jbx_app(monkeypatch):
    state = {"admin": True, "config": {}, "search_calls": [], "oembed_calls": [],
             "search_results": [], "oembed_fail": False}

    def fake_search(query, max_results):
        state["search_calls"].append((query, max_results))
        return state["search_results"]

    def fake_oembed(video_id):
        state["oembed_calls"].append(video_id)
        if state["oembed_fail"]:
            raise RuntimeError("404")
        return {"title": "Never Gonna Give You Up", "author_name": "Rick Astley"}

    monkeypatch.setattr(chat_api, "_oembed_fetch", fake_oembed)
    chat_api.configure(
        client_getter=lambda: None,
        run_async=lambda v: v,
        config_get=lambda key, default=None: state["config"].get(key, default),
        youtube_search=fake_search,
    )
    app = Flask(__name__)

    @app.before_request
    def _fake_profile():
        g.is_admin = state["admin"]

    app.register_blueprint(chat_api.create_blueprint())
    yield app.test_client(), state
    chat_api.configure(client_getter=lambda: None, run_async=lambda v: v,
                       config_get=lambda k, d=None: d)


def test_url_resolves_via_oembed_exactly_once(jbx_app):
    http, state = jbx_app
    body = http.post("/api/chat/jukebox/resolve",
                     json={"q": "https://youtu.be/dQw4w9WgXcQ"}).get_json()
    assert body["results"] == [{"id": "dQw4w9WgXcQ",
                                "title": "Never Gonna Give You Up",
                                "channel": "Rick Astley"}]
    assert state["oembed_calls"] == ["dQw4w9WgXcQ"]
    assert state["search_calls"] == []            # a link never hits yt-dlp


def test_free_text_goes_through_search_seam(jbx_app):
    http, state = jbx_app
    state["search_results"] = [
        SimpleNamespace(video_id="aaaaaaaaaaa", title="Around the World",
                        channel="Daft Punk", duration=429),
        SimpleNamespace(video_id="not a vid!!", title="hostile", channel="x",
                        duration=1),              # bad id → filtered
    ]
    body = http.post("/api/chat/jukebox/resolve",
                     json={"q": "daft punk around the world"}).get_json()
    assert state["search_calls"] == [("daft punk around the world", 5)]
    assert body["results"] == [{"id": "aaaaaaaaaaa", "title": "Around the World",
                                "channel": "Daft Punk", "duration": 429}]


def test_unresolvable_video_404s(jbx_app):
    http, state = jbx_app
    state["oembed_fail"] = True
    r = http.post("/api/chat/jukebox/resolve", json={"q": "dQw4w9WgXcQ"})
    assert r.status_code == 404


def test_resolve_respects_the_send_gate(jbx_app):
    """Submitting to the queue is sending — a read-only profile must not be
    able to make the server fetch YouTube on its behalf either."""
    http, state = jbx_app
    state["admin"] = False
    r = http.post("/api/chat/jukebox/resolve", json={"q": "dQw4w9WgXcQ"})
    assert r.status_code == 403
    assert state["oembed_calls"] == [] and state["search_calls"] == []


def test_garbage_input_400s_without_lookups(jbx_app):
    http, state = jbx_app
    assert http.post("/api/chat/jukebox/resolve", json={}).status_code == 400
    assert http.post("/api/chat/jukebox/resolve",
                     json={"q": "x" * 300}).status_code == 400
    assert state["oembed_calls"] == [] and state["search_calls"] == []


def test_no_search_seam_means_paste_only(jbx_app):
    http, state = jbx_app
    chat_api.configure(client_getter=lambda: None, run_async=lambda v: v,
                       config_get=lambda k, d=None: d)   # no youtube_search
    r = http.post("/api/chat/jukebox/resolve", json={"q": "some song"})
    assert r.status_code == 503
    # links still work without the seam
    body = http.post("/api/chat/jukebox/resolve", json={"q": "dQw4w9WgXcQ"})
    assert body.get_json()["results"][0]["id"] == "dQw4w9WgXcQ"


# ── frontend pins ───────────────────────────────────────────────────────────

def test_frontend_jukebox_wiring():
    js = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
    # the reducer is the single source of truth — no shadow queue state
    assert "reduceJukebox" in js and "nextTrack" in js
    # room-tagged protocol log: jukebox events must never mix rooms
    assert "e.room === room" in js
    assert "room: room, p: p" in js
    # DJ pool = PROTOCOL-CAPABLE clients only (live-test catch: envelope
    # messages also come from pre-jukebox versions — electing one of those
    # gets a DJ that can never press play, and the queue starves forever)
    assert "emitters[e.username] = 1" in js
    assert "cls[n] === 'soulsync'" not in js
    # starvation fallback: any capable client kicks a DJ-less queue
    assert "starvedAt" in js and "45000" in js
    # playback is opt-in (autoplay-with-sound needs the tune-in gesture)
    assert "data-chat-jbx-tunein" in js and "_jbxTuneOut" in js
    # DJ double-fire guard + watchdog for stale/absent now-playing
    assert "lastAdvanceAt" in js and "_jbxWatchdog" in js
    assert "'/api/chat/jukebox/resolve'" in js

    proto = (_ROOT / "webui" / "static" / "chat-protocol.js").read_text(encoding="utf-8")
    assert "reduceJukebox" in proto and "_saneDuration" in proto

    html = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert "data-chat-jukebox" in html and "data-chat-jbx-form" in html
    assert "data-chat-jbx-player" in html

    css = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".chat-jukebox" in css and ".chat-jbx-vote" in css


def test_switching_rooms_tunes_out():
    """Playing room A's track while looking at room B would be chaos — the
    room switch must destroy the player."""
    js = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
    open_room = js[js.index("function openRoom"):js.index("function loadRooms")]
    assert "_jbxTuneOut()" in open_room


def test_review_catches_pinned():
    """P3 adversarial review — the four catches must stay fixed:
    (1) the player follows the ROOM, not the panel (a tuned-in listener in
    PM view must still hear DJ advances); (2) a transient null now-playing
    between tracks must never destroy the player; (3) onReady re-syncs so a
    now-change during iframe boot isn't skipped; (4) the DJ double-fire
    guard outlasts a slow slskd roundtrip (a duplicate jbx.now restarts the
    track for the whole room)."""
    js = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
    render = js[js.index("function renderJukebox"):js.index("function toggleJukebox")]
    # (1) sync happens BEFORE the panel-visibility early return
    assert render.index("_jbxSyncPlayer") < render.index("if (!show) return;")
    # (2) between tracks: keep the player
    sync = js[js.index("function _jbxSyncPlayer"):js.index("function _jbxOnPlayerState")]
    assert "if (!now) return;" in sync
    assert "_jbxTuneOut(); return;" not in sync
    # (3) onReady catches a now-change during boot
    assert "_jbxSyncPlayer(_jbxState().now)" in sync
    # (4) roundtrip-proof guard
    assert "15000" in js[js.index("function _jbxAdvance"):js.index("function _jbxLoadYT")]


def test_watchdog_polls_the_player_not_just_the_event():
    """Live-test catch #2 (Boulder): a pasted-link track has NO duration
    (oEmbed doesn't provide one), so when the iframe's ENDED event goes
    missing the queue sat until the 15-minute unknown-length cap. The
    watchdog must ask a tuned-in player directly — getPlayerState() for
    ended, getDuration() for the real length."""
    js = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
    wd = js[js.index("function _jbxWatchdog"):js.index("function _jbxAdvance")]
    assert "getPlayerState() === 0" in wd
    assert "playerEnded" in wd
    # the player-truth duration lives in the shared helper the watchdog calls
    assert "_jbxEffDuration(st.now)" in wd
    eff = js[js.index("function _jbxEffDuration"):js.index("function _jbxSkipNeeded")]
    assert "getDuration()" in eff


def test_jukebox_level_up_wiring():
    """The polish pass: thumbnails, progress bar, skip votes, pull-your-own,
    history with replay, audio-only, local volume — all reduced from the
    same stream (three new verbs: jbx.skip / jbx.unsub, history from
    jbx.now handoffs), still zero server surface."""
    js = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
    # free YouTube thumbnails, no API
    assert "i.ytimg.com/vi/" in js
    # skip is majority-of-listeners, deterministic from the tuned set
    assert "_jbxSkipNeeded" in js and "Math.ceil(n / 2)" in js
    assert "'jbx.skip'" in js and "'jbx.unsub'" in js
    # the watchdog treats a skip majority as end-of-track
    wd = js[js.index("function _jbxWatchdog"):js.index("function _jbxAdvance")]
    assert "st.skips >= _jbxSkipNeeded()" in wd
    # audio-only collapses the container but never kills the iframe
    assert "chat-jbx-player--audio" in js
    css = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")
    assert "height: 0 !important" in css.split(".chat-jbx-player--audio", 1)[1][:120]
    # volume is local-only (player.setVolume, persisted, never a protocol event)
    assert "chat_jbx_vol" in js and "setVolume" in js
    proto = (_ROOT / "webui" / "static" / "chat-protocol.js").read_text(encoding="utf-8")
    assert "skipVotes" in proto and "history" in proto


def test_auto_dj_radio_wiring():
    """Radio mode: shared jbx.radio toggle (latest wins, reduced like all
    bus state); when the queue runs dry the DJ searches something related
    to the last-heard track and queues it marked auto — vote/skippable
    like any human pick, 25s cooldown, never fights the paste-only 503."""
    js = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
    assert "_jbxAutoQueue" in js and "'jbx.radio'" in js
    wd = js[js.index("function _jbxWatchdog"):js.index("function _jbxAutoQueue")]
    assert "st.radio && _jbxIsDj()" in wd            # only the DJ tops up
    auto = js[js.index("function _jbxAutoQueue"):js.index("function _jbxAdvance")]
    assert "25000" in auto                            # cooldown
    assert "avoid[h.id] = 1" in auto                  # history never replays
    assert "a: 1" in auto                             # marked auto
    proto = (_ROOT / "webui" / "static" / "chat-protocol.js").read_text(encoding="utf-8")
    assert "jbx.radio" in proto and "entry.auto = true" in proto
    html = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert "data-chat-jbx-radio" in html


def test_watchdog_drives_queue_with_panel_closed():
    """Correctness pass (Boulder): advancing must not depend on the DJ having
    the jukebox PANEL open. The watchdog gates on being in a room (not
    panel-open) and is ticked from the 4s room refresh, so the elected DJ
    starts/advances the queue even with the panel closed — otherwise the
    queue stalled until a 45s starvation fallback, or froze entirely."""
    js = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
    wd = js[js.index("function _jbxWatchdog"):js.index("function _jbxAutoQueue")]
    assert "if (state.view !== 'room') return;" in wd     # room-view, not panel-open
    assert "if (!state.jukebox.open) return;" not in wd    # the old gate is gone
    # ticked from the room refresh (which itself is behind pageVisible + room)
    refresh_room = js[js.index("_sendJoinBeacon();"):js.index("_sendJoinBeacon();") + 200]
    assert "_jbxWatchdog();" in refresh_room


def test_radio_refill_requires_a_listener():
    """Auto-DJ must not generate an endless stream in an unwatched room —
    radio refill is gated on someone being tuned in. (Advancing an existing
    finite queue stays unconditional; it just drains.)"""
    js = (_ROOT / "webui" / "static" / "chat.js").read_text(encoding="utf-8")
    wd = js[js.index("function _jbxWatchdog"):js.index("function _jbxAutoQueue")]
    assert "st.radio && _jbxIsDj() && _jbxHasListeners()" in wd
    assert "function _jbxHasListeners" in js
    fn = js[js.index("function _jbxHasListeners"):js.index("function _jbxHasListeners") + 600]
    assert "state.jukebox.tunedIn" in fn and "reduceTuned(_roomEvents())" in fn
