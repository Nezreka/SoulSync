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
    assert "getDuration()" in wd
    assert "playerEnded" in wd
