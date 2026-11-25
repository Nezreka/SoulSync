"""soulsync's own player in server activity: its live screens normalized into
the shared session shape, polled with or without plex and jellyfin, recorded
in the play log, stopped or messaged through the screen's next heartbeat, and
still hidden from members when the profile doesn't share."""

from __future__ import annotations

import pytest
from flask import Flask

import api.player as player_api
import core.server_activity as sa
from core.player import presence
from core.player import settings as player_settings
from core.server_activity import live, store
from core.server_activity import soulsync as ss
from tests.sa_helpers import sa_env  # noqa: F401 - fixture

T = 1_800_000_000.0
MOVIE = {"session": "tab1", "item_type": "movie", "item_id": "7", "position": 600, "duration": 6000,
         "playing": True, "meta": {"title": "Heat", "artwork": "/api/video/poster/movie/7?w=342"}}


def _session(**over):
    body = dict(MOVIE, **over)
    return presence.beat(2, body, now=T, ip=over.pop("_ip", "81.2.69.160"),
                         client={"product": "SoulSync player", "device": "Firefox", "platform": "Windows"})


# ── normalize ───────────────────────────────────────────────────────────────

def test_a_transcoded_movie_says_what_is_converted_and_why():
    presence._sessions.clear()
    s = _session(stream={"via": "disk", "file_id": 31, "transcode": {
        "video": "h264", "audio": "aac", "quality": "720p", "reasons": ["HEVC video to H.264 at 720p"],
        "encoder": "NVIDIA GPU"}})
    facts = {"title": "Heat", "year": 1995, "file": {"container": "mkv", "video_codec": "hevc",
                                                      "audio_codec": "dts", "height": 2160,
                                                      "dynamic_range": "HDR10", "bitrate_kbps": 40000}}
    n = ss.normalize(s, server_name="Den", profile={"name": "Ada", "avatar_url": "/api/profiles/2/avatar?v=1"},
                     facts=facts)
    assert n["id"] == n["session_key"] == "soulsync:2:tab1"
    assert (n["server"], n["server_name"], n["kind"], n["activity"], n["state"]) == (
        "soulsync", "Den", "movie", "watching", "playing")
    assert (n["title"], n["year"], n["user"], n["user_id"]) == ("Heat", 1995, "Ada", "2")
    assert n["user_avatar"] == "/api/profiles/2/avatar?v=1"
    assert n["offset_ms"] == 600_000 and n["duration_ms"] == 6_000_000 and n["progress_pct"] == 10
    assert n["thumb"] == "/api/video/poster/movie/7?w=342"
    assert n["player"]["product"] == "SoulSync player" and n["player"]["device"] == "Firefox"
    assert n["connection"]["ip"] == "81.2.69.160" and n["connection"]["remote"] is True
    pb = n["playback"]
    assert pb["method"] == "transcode" and pb["method_label"] == "Transcode"
    assert pb["video"]["source"]["resolution"] == "4K" and pb["video"]["stream"]["resolution"] == "720p"
    assert pb["video"]["changed"] and pb["audio"]["stream"]["codec"] == "AAC"
    assert pb["container"] == {"source": "MKV", "stream": "MP4", "changed": True}
    assert n["reason"]["codes"] == ["over_quality_limit", "audio_codec"]
    assert n["transcoder"]["hw"] is True and n["transcoder"]["hw_encode"] == "NVIDIA GPU"
    assert n["controls"] == {"stop": True, "message": True, "stop_note": None}
    assert n["link"] == {"kind": "movie", "id": 7, "source": "library"}


def test_a_remux_is_direct_stream_and_a_plain_file_direct_play():
    presence._sessions.clear()
    remux = ss.normalize(_session(stream={"transcode": {"video": "copy", "audio": "copy"}}))
    assert remux["playback"]["method"] == "direct_stream" and remux["transcoder"]["hw"] is False
    plain = ss.normalize(_session(session="tab2", stream={"via": "disk", "transcode": None}))
    assert plain["playback"]["method"] == "direct_play" and plain["transcoder"] is None


def test_a_video_that_never_said_how_it_plays_has_no_method():
    """an older app sends no stream block: unknown stays unknown"""
    presence._sessions.clear()
    n = ss.normalize(_session(_ip=None))
    assert n["playback"]["method"] is None and n["playback"]["method_label"] is None
    assert n["reason"] is None and n["transcoder"] is None
    assert n["connection"]["ip"] is None and n["connection"]["remote"] is False


def test_an_episode_a_track_and_a_live_channel():
    presence._sessions.clear()
    ep = presence.beat(3, {"session": "s", "item_type": "episode", "item_id": "88", "position": 1,
                           "duration": 100, "playing": False, "meta": {"title": "Pilot"}}, now=T, ip="192.168.1.4")
    n = ss.normalize(ep, facts={"title": "Pilot", "season": 1, "episode": 1, "show_id": 12, "show": "Severance"})
    assert n["subtitle"] == "Severance · S1 E1" and n["show"]["title"] == "Severance"
    assert n["state"] == "paused" and n["connection"]["remote"] is False
    assert n["keys"]["grandparent_key"] == "12"
    assert n["link"] == {"kind": "show", "id": 12, "source": "library", "season": 1, "episode": 1}
    tr = presence.beat(3, {"session": "m", "item_type": "track", "item_id": "5", "position": 1,
                           "duration": 200, "playing": True, "live": True,
                           "channel": {"id": "c1", "number": 4, "name": "Jazz"}}, now=T)
    n = ss.normalize(tr, facts={"title": "So What", "artist": "Miles Davis", "album": "Kind of Blue",
                                "album_id": 9, "artist_id": 3,
                                "file": {"format": "flac", "bitrate": 900000, "sample_rate": 96000,
                                         "bit_depth": 24}})
    assert n["activity"] == "listening" and n["music"]["artist"] == "Miles Davis"
    assert n["subtitle"] == "Miles Davis · Kind of Blue · Live on channel 4 Jazz"
    assert n["playback"]["method"] == "direct_play"
    assert n["playback"]["audio"]["source"]["quality"] == "FLAC 24-bit · 96 kHz"
    assert n["link"]["track_id"] == 5 and n["link"]["album_id"] == 9


# ── the poll ────────────────────────────────────────────────────────────────

def test_the_player_shows_with_no_plex_or_jellyfin(sa_env, monkeypatch):
    monkeypatch.setattr(ss, "_profiles", lambda: {2: {"name": "Ada"}})
    presence.beat(2, MOVIE, now=T, ip="10.0.0.5")
    out = live.poll(now=T)
    assert out["ok"] is True and [i["server"] for i in out["servers"]] == ["soulsync"]
    (s,) = out["sessions"]
    assert s["server"] == "soulsync" and s["user"] == "Ada" and s["title"] == "Heat"
    assert s["image"] is None and s["play_key"] and s["started_at"] == T
    assert out["summary"]["streams"] == 1 and out["summary"]["watching"] == 1


def test_the_player_rides_alongside_plex(sa_env):
    sa_env.use_plex()
    presence.beat(2, MOVIE)
    out = live.get_activity(max_age=0)
    assert [i["server"] for i in out["servers"]] == ["plex", "soulsync"]
    assert sum(1 for s in out["sessions"] if s["server"] == "soulsync") == 1
    assert out["summary"]["streams"] == 5


def test_player_plays_are_recorded_like_plex_ones(sa_env):
    presence.beat(2, MOVIE, now=T)
    live.poll(now=T)
    presence.beat(2, dict(MOVIE, position=630), now=T + 30)
    live.poll(now=T + 30)
    presence.stop(2, "tab1")
    live.poll(now=T + 40)
    live.writer.flush()
    (row,) = store.query("SELECT server, title, kind, user_id, played_ms, stopped_at FROM server_plays")
    assert (row["server"], row["title"], row["kind"], row["user_id"]) == ("soulsync", "Heat", "movie", "2")
    assert row["played_ms"] == 30_000 and row["stopped_at"] == T + 30
    from core.server_activity import history
    got = history.get_history(server="soulsync")
    assert [g["title"] for g in got["groups"]] == ["Heat"]


def test_the_sampler_wakes_for_the_player_alone(sa_env):
    assert live.sampler_tick(False, None) is None
    presence.beat(2, MOVIE)
    data = live.sampler_tick(False, None)
    assert data["summary"]["streams"] == 1


# ── stop and message, through the beat ──────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    from database.player_database import PlayerDatabase
    db = PlayerDatabase(str(tmp_path / "player.db"))
    monkeypatch.setattr(player_api, "player_db", lambda: db)
    player_settings._cache.clear()
    player_api._who.update({"at": 1e18, "map": {2: {"name": "Ada"}, 3: {"name": "Bo"}}})
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(player_api.create_blueprint())
    yield app.test_client()
    player_settings._cache.clear()
    player_api._who.update({"at": 0.0, "map": {}})


def _as(client, profile_id):
    with client.session_transaction() as sess:
        sess["profile_id"] = profile_id


def test_a_stop_rides_back_on_the_next_beat(sa_env, client, monkeypatch):
    killed = []
    from core.player import transcode
    monkeypatch.setattr(transcode, "stop_owner", lambda owner: killed.append(owner) or True)
    _as(client, 2)
    first = client.post("/api/player/presence", json=MOVIE).get_json()
    assert first["success"] and "stop" not in first and "message" not in first
    assert sa.stop_session("soulsync:2:tab1", "Bedtime, see you tomorrow.") == {"ok": True, "message_shown": None}
    assert killed == ["2:tab1"]
    # off the live view at once, and the screen hears it on its next beat
    assert [s for s in live.get_activity(max_age=0)["sessions"] if s["server"] == "soulsync"] == []
    answer = client.post("/api/player/presence", json=MOVIE).get_json()
    assert answer["stop"] == {"message": "Bedtime, see you tomorrow."}
    # its beats aren't kept while it's held, and its item isn't served again
    assert presence.everyone() == []
    assert presence.refused(2, "movie", "7") == "Bedtime, see you tomorrow."
    assert presence.refused(2, "movie", "8") is None and presence.refused(3, "movie", "7") is None
    assert presence.refused(2, "movie", "7", now=presence._stops[(2, "tab1")]["until"] + 1) is None


def test_a_stop_with_no_words_still_says_something(sa_env, client):
    _as(client, 2)
    client.post("/api/player/presence", json=MOVIE)
    sa.stop_session("soulsync:2:tab1", "")
    assert client.post("/api/player/presence", json=MOVIE).get_json()["stop"]["message"]


def test_a_message_is_shown_once_without_stopping(sa_env, client):
    _as(client, 2)
    client.post("/api/player/presence", json=MOVIE)
    assert sa.message_session("soulsync:2:tab1", "Restarting in 10 minutes", header="Heads up") == {"ok": True}
    answer = client.post("/api/player/presence", json=MOVIE).get_json()
    assert answer["message"] == {"header": "Heads up", "text": "Restarting in 10 minutes"} and "stop" not in answer
    assert "message" not in client.post("/api/player/presence", json=MOVIE).get_json()
    assert len(presence.everyone()) == 1


def test_controls_refuse_a_gone_or_odd_screen(sa_env):
    for key in ("soulsync:2:nope", "soulsync:x:tab1", "soulsync:2:", "soulsync:"):
        assert sa.stop_session(key, "x")["ok"] is False
        assert sa.message_session(key, "x")["ok"] is False
    presence.beat(2, MOVIE)
    assert sa.message_session("soulsync:2:tab1", "  ")["ok"] is False


# ── privacy ─────────────────────────────────────────────────────────────────

def test_a_profile_that_does_not_share_is_hidden_from_members_but_not_the_admin(sa_env, client, monkeypatch):
    monkeypatch.setattr(player_api, "_viewer_may_see", lambda s: True)
    _as(client, 2)
    client.put("/api/player/settings", json={"share_activity": False})
    answer = client.post("/api/player/presence", json=MOVIE).get_json()
    assert answer["shared"] is False
    # bo, a member, sees nobody
    _as(client, 3)
    assert client.get("/api/player/presence").get_json()["sessions"] == []
    # ada sees herself, without the admin's details
    _as(client, 2)
    (mine,) = client.get("/api/player/presence").get_json()["sessions"]
    assert mine["you"] and not {"ip", "client", "stream", "shared", "session"} & set(mine)
    # the admin's server activity sees her stream
    keys = [s["session_key"] for s in live.get_activity(max_age=0)["sessions"]]
    assert keys == ["soulsync:2:tab1"]
    # sharing back on: members see her at once
    client.put("/api/player/settings", json={"share_activity": True})
    _as(client, 3)
    assert [r["profile_id"] for r in client.get("/api/player/presence").get_json()["sessions"]] == [2]


def test_members_never_see_where_a_screen_is():
    presence._sessions.clear()
    presence.beat(2, dict(MOVIE, stream={"via": "disk"}), ip="81.2.69.160", client={"name": "Pixel"})
    (row,) = presence.visible_to(3, presence.everyone(), shares=lambda p: True, may_see=lambda s: True)
    assert not {"ip", "client", "stream", "shared"} & set(row)
    presence._sessions.clear()
