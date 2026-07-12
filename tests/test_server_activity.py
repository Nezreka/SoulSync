"""Live server activity — Plex session normalization (Tautulli-style).

The raw plexapi session objects → clean activity payload is pure + defensive:
tested here with fakes shaped like plexapi's Video/Track/Player/Session/
TranscodeSession, so the transcode-decision logic, per-type titles, progress,
and summary roll-up are all covered without a live Plex.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from core.server_activity import get_activity, normalize_session


@pytest.fixture(autouse=True)
def _no_jellyfin(monkeypatch):
    """Isolate get_activity tests from any real Jellyfin config in the env —
    Jellyfin-specific tests override this."""
    import core.server_activity as sa
    monkeypatch.setattr(sa, "_jellyfin_activity", lambda db=None: ([], None))


def _media(res="1080", vcodec="hevc", acodec="eac3", bitrate=12000, container="mkv"):
    return NS(videoResolution=res, videoCodec=vcodec, audioCodec=acodec,
              bitrate=bitrate, container=container)


def _player(state="playing", product="Plex Web", device="Chrome", platform="Chrome", title="Living Room"):
    return NS(state=state, product=product, device=device, platform=platform, title=title)




def _sess(bandwidth=12000, location="lan", sid="s1"):
    return NS(bandwidth=bandwidth, location=location, id=sid)


def _movie(**kw):
    # usernames (+ _username) is the LOCAL list; never set `.user`/`.users` — those
    # would trigger a plex.tv network call in real plexapi, which the code avoids.
    base = dict(type="movie", sessionKey="10", title="Heat", year=1995, duration=600000,
                viewOffset=150000, thumb="/t/heat", art="/a/heat",
                media=[_media()], players=[_player()], usernames=["boulder"], _username="boulder",
                session=_sess(), transcodeSessions=[])
    base.update(kw)
    return NS(**base)


def _transcode(vdec="transcode", adec="copy", vcodec="h264", progress=44.0, hw=True):
    return NS(videoDecision=vdec, audioDecision=adec, videoCodec=vcodec, audioCodec="aac",
              progress=progress, throttled=False, transcodeHwEncoding=hw,
              transcodeHwRequested=hw, container="mp4")


# ── per-type titles + progress ───────────────────────────────────────────────
def test_movie_direct_play():
    s = normalize_session(_movie())
    assert s["media_type"] == "movie" and s["title"] == "Heat" and s["subtitle"] == "1995"
    assert s["progress_pct"] == 25              # 150000 / 600000
    assert s["stream"]["method"] == "Direct Play"
    assert s["stream"]["video"] == "HEVC" and s["stream"]["resolution"] == "1080P"
    assert s["user"] == "boulder" and s["player"]["device"] == "Chrome"
    assert s["bandwidth_kbps"] == 12000 and s["location"] == "lan"


def test_episode_transcode_line():
    ep = _movie(type="episode", title="Ozymandias", grandparentTitle="Breaking Bad",
                parentIndex=5, index=14, transcodeSessions=[_transcode()])
    s = normalize_session(ep)
    assert s["media_type"] == "episode"
    assert s["subtitle"] == "Breaking Bad · S05E14"
    assert s["grandparent"] == "Breaking Bad"
    assert s["stream"]["method"] == "Transcode"
    assert s["stream"]["video"] == "HEVC → H264"   # source → target on video transcode
    assert s["stream"]["audio"] == "EAC3"          # audio is 'copy' → no arrow
    assert s["stream"]["hw"] is True and s["stream"]["transcode_progress"] == 44


def test_track_titles_artist_and_album():
    tr = _movie(type="track", title="Teardrop", grandparentTitle="Massive Attack",
                parentTitle="Mezzanine", year=None)
    s = normalize_session(tr)
    assert s["media_type"] == "track"
    assert s["subtitle"] == "Massive Attack · Mezzanine"


def test_direct_stream_when_only_audio_copies_but_no_video_transcode():
    # both decisions 'copy' → Direct Stream (remux, not transcode)
    ep = _movie(transcodeSessions=[_transcode(vdec="copy", adec="copy")])
    s = normalize_session(ep)
    assert s["stream"]["method"] == "Direct Stream"
    assert "→" not in s["stream"]["video"]         # nothing transcoded


def test_paused_and_buffering_states_survive():
    assert normalize_session(_movie(players=[_player(state="paused")]))["state"] == "paused"
    assert normalize_session(_movie(players=[_player(state="buffering")]))["state"] == "buffering"
    assert normalize_session(_movie(players=[_player(state="weird")]))["state"] == "playing"


def test_missing_attributes_never_raise():
    # a bare object with almost nothing set
    s = normalize_session(NS(type="clip"))
    assert s["title"] == "" and s["user"] == "Someone" and s["progress_pct"] == 0
    assert s["stream"]["method"] == "Direct Play"


# ── the full payload + summary ───────────────────────────────────────────────
class _FakePlex:
    friendlyName = "Broque's Plex"
    version = "1.40.0"

    def __init__(self, sessions):
        self._s = sessions

    def sessions(self):
        return self._s


def test_get_activity_summarizes(monkeypatch):
    import core.server_activity as sa
    plex = _FakePlex([_movie(), _movie(type="episode", transcodeSessions=[_transcode()]),
                      _movie(session=_sess(location="wan", bandwidth=3000))])
    monkeypatch.setattr(sa, "_plex_server", lambda db=None: plex)
    out = get_activity()
    assert out["ok"] is True
    assert out["server"]["name"] == "Broque's Plex"
    sm = out["summary"]
    assert sm["streams"] == 3 and sm["transcodes"] == 1 and sm["direct_play"] == 2
    assert sm["total_bandwidth_kbps"] == 12000 + 12000 + 3000
    assert sm["lan"] == 2 and sm["wan"] == 1


def test_get_activity_no_server_is_a_state_not_an_error(monkeypatch):
    import core.server_activity as sa
    monkeypatch.setattr(sa, "_plex_server", lambda db=None: None)
    out = get_activity()
    assert out["ok"] is False and out["reason"] == "no_server"
    assert out["sessions"] == [] and out["summary"]["streams"] == 0


def test_get_activity_survives_a_bad_session(monkeypatch):
    import core.server_activity as sa

    class _Boom:
        friendlyName = "P"
        version = "1"
        def sessions(self):
            good = _movie()
            bad = property(lambda self: (_ for _ in ()).throw(RuntimeError()))  # noqa
            return [good, NS(type="movie", media=_BadMedia())]

    monkeypatch.setattr(sa, "_plex_server", lambda db=None: _Boom())
    out = sa.get_activity()
    assert out["ok"] is True and out["summary"]["streams"] >= 1   # good one survives


class _BadMedia:
    def __getitem__(self, i):
        raise RuntimeError("boom")


# ── history (Phase 2) ────────────────────────────────────────────────────────
from datetime import datetime  # noqa: E402

from core.server_activity import get_history, normalize_history  # noqa: E402


def _hist(**kw):
    base = dict(type="episode", title="Ozymandias", grandparentTitle="Breaking Bad",
                parentIndex=5, index=14, thumb="/t", grandparentThumb="/gt",
                accountID=2, deviceID=7, viewedAt=datetime(2026, 7, 12, 3, 0, 0))
    base.update(kw)
    return NS(**base)


def test_history_row_resolves_user_and_device():
    accounts, devices = {2: "boulder"}, {7: "Apple TV"}
    r = normalize_history(_hist(), accounts, devices)
    assert r["title"] == "Ozymandias" and r["subtitle"] == "Breaking Bad · S05E14"
    assert r["user"] == "boulder" and r["device"] == "Apple TV"
    assert r["viewed_epoch"] > 0


def test_history_unknown_account_falls_back():
    r = normalize_history(_hist(accountID=999), {2: "boulder"}, {})
    assert r["user"] == "Someone" and r["device"] == ""


class _HistPlex:
    machineIdentifier = "abc"

    def history(self, maxresults=None):
        return [_hist(viewedAt=datetime(2026, 7, 12, 1, 0, 0)),
                _hist(title="Newer", viewedAt=datetime(2026, 7, 12, 9, 0, 0))]

    def systemAccounts(self):
        return [NS(id=2, name="boulder")]

    def systemDevices(self):
        return [NS(id=7, name="Apple TV")]


def test_get_history_sorts_newest_first(monkeypatch):
    import core.server_activity as sa
    sa._lookup_cache.update(accounts={}, devices={}, at=0.0, key="")   # clear cache
    monkeypatch.setattr(sa, "_plex_server", lambda db=None: _HistPlex())
    out = get_history()
    assert out["ok"] is True and len(out["history"]) == 2
    assert out["history"][0]["title"] == "Newer"          # newest first
    assert out["history"][0]["user"] == "boulder"


def test_get_history_no_server(monkeypatch):
    import core.server_activity as sa
    monkeypatch.setattr(sa, "_plex_server", lambda db=None: None)
    assert get_history()["ok"] is False


# ── stream termination ───────────────────────────────────────────────────────
def test_stop_session_calls_stop_with_message(monkeypatch):
    import core.server_activity as sa
    stopped = {}

    class _Item:
        sessionKey = 42
        def stop(self, reason=None):
            stopped["reason"] = reason

    class _P:
        def sessions(self):
            return [_Item()]
    monkeypatch.setattr(sa, "_plex_server", lambda db=None: _P())
    res = sa.stop_session("42", "Go to bed")
    assert res["ok"] is True and stopped["reason"] == "Go to bed"


def test_stop_session_uses_default_message_when_blank(monkeypatch):
    import core.server_activity as sa
    seen = {}

    class _Item:
        sessionKey = 1
        def stop(self, reason=None):
            seen["r"] = reason

    monkeypatch.setattr(sa, "_plex_server", lambda db=None: type("P", (), {"sessions": lambda s: [_Item()]})())
    sa.stop_session("1", "   ")
    assert "administrator" in seen["r"]


def test_stop_session_unknown_key(monkeypatch):
    import core.server_activity as sa
    monkeypatch.setattr(sa, "_plex_server", lambda db=None: type("P", (), {"sessions": lambda s: []})())
    assert sa.stop_session("99")["ok"] is False


def test_stop_session_no_server(monkeypatch):
    import core.server_activity as sa
    monkeypatch.setattr(sa, "_plex_server", lambda db=None: None)
    assert sa.stop_session("1")["ok"] is False


# ── stats (Phase 3) ──────────────────────────────────────────────────────────
from core.server_activity import compute_stats  # noqa: E402


def _hitem(mtype, title, gp, acct, dev, when):
    return NS(type=mtype, title=title, grandparentTitle=gp, thumb="/t", grandparentThumb="/gt",
              accountID=acct, deviceID=dev, viewedAt=when)


def test_compute_stats_rolls_up_content_users_devices():
    accts, devs = {1: "boulder", 2: "guest"}, {9: "Apple TV", 8: "Chrome"}
    now = datetime(2026, 7, 12, 12, 0, 0)
    items = [
        _hitem("episode", "Ep1", "Breaking Bad", 1, 9, now),
        _hitem("episode", "Ep2", "Breaking Bad", 1, 9, now),     # same show → grouped
        _hitem("movie", "Heat", "", 2, 8, now),
        _hitem("track", "Teardrop", "Massive Attack", 1, 9, now),
    ]
    d = compute_stats(items, accts, devs, 30)
    assert d["total_plays"] == 4 and d["unique_users"] == 2
    top = {c["title"]: c["plays"] for c in d["top_content"]}
    assert top["Breaking Bad"] == 2 and top["Heat"] == 1 and top["Massive Attack"] == 1
    assert d["top_users"][0] == {"user": "boulder", "plays": 3}   # most active first
    assert d["top_devices"][0]["device"] == "Apple TV" and d["top_devices"][0]["plays"] == 3
    # series is always 14 ordered day-buckets
    assert len(d["series"]) == 14 and all("date" in p and "plays" in p for p in d["series"])


def test_compute_stats_empty():
    d = compute_stats([], {}, {}, 30)
    assert d["total_plays"] == 0 and d["top_content"] == [] and len(d["series"]) == 14


def test_get_stats_caches(monkeypatch):
    import core.server_activity as sa
    calls = {"n": 0}

    class _P:
        machineIdentifier = "m1"
        def history(self, maxresults=None, mindate=None):
            calls["n"] += 1
            return [_hitem("movie", "Heat", "", 1, 9, datetime.now())]
        def systemAccounts(self): return [NS(id=1, name="boulder")]
        def systemDevices(self): return [NS(id=9, name="TV")]
    sa._stats_cache.update(data=None, at=0.0, key="")
    sa._lookup_cache.update(accounts={}, devices={}, at=0.0, key="")
    monkeypatch.setattr(sa, "_plex_server", lambda db=None: _P())
    a = sa.get_stats(days=30)
    b = sa.get_stats(days=30)
    assert a["ok"] and a["total_plays"] == 1 and calls["n"] == 1   # second call served from cache


# ── Jellyfin (merged activity) ───────────────────────────────────────────────
from core.server_activity import normalize_jellyfin  # noqa: E402


def test_jellyfin_episode_normalizes_to_shared_shape():
    s = {"UserName": "guest", "Client": "Jellyfin Web", "DeviceName": "Firefox",
         "PlayState": {"PositionTicks": 3000000000, "IsPaused": False, "PlayMethod": "Transcode"},
         "TranscodingInfo": {"VideoCodec": "h264", "AudioCodec": "aac", "Bitrate": 4000000,
                             "CompletionPercentage": 30.0, "Container": "ts"},
         "NowPlayingItem": {"Type": "Episode", "Name": "Pilot", "SeriesName": "Severance",
                            "ParentIndexNumber": 1, "IndexNumber": 1, "RunTimeTicks": 30000000000, "Id": "abc"}}
    n = normalize_jellyfin(s, s["NowPlayingItem"])
    assert n["media_type"] == "episode" and n["title"] == "Pilot"
    assert n["subtitle"] == "Severance · S01E01"
    assert n["progress_pct"] == 10           # 3e9 / 30e9 ticks
    assert n["stream"]["method"] == "Transcode" and n["stream"]["video"] == "H264"
    assert n["user"] == "guest" and n["player"]["device"] == "Firefox"
    assert n["thumb"] == "jf:abc"            # image marker for the proxy
    assert n["session_key"] == ""            # no stop button (Plex-only terminate)


def test_jellyfin_track_uses_artist_album():
    npi = {"Type": "Audio", "Name": "Teardrop", "AlbumArtist": "Massive Attack", "Album": "Mezzanine", "Id": "x"}
    n = normalize_jellyfin({"NowPlayingItem": npi, "PlayState": {}}, npi)
    assert n["media_type"] == "track" and n["subtitle"] == "Massive Attack · Mezzanine"


def test_activity_merges_plex_and_jellyfin(monkeypatch):
    import core.server_activity as sa
    monkeypatch.setattr(sa, "_plex_server", lambda db=None: _FakePlex([_movie()]))
    npi = {"Type": "Movie", "Name": "Dune", "RunTimeTicks": 60000000000, "Id": "d1"}
    monkeypatch.setattr(sa, "_jellyfin_activity",
                        lambda db=None: ([normalize_jellyfin({"NowPlayingItem": npi, "UserName": "jf-user",
                                                              "PlayState": {}}, npi)], "Jellyfin"))
    out = get_activity()
    assert out["ok"] and out["summary"]["streams"] == 2      # 1 plex + 1 jellyfin
    assert out["server"]["platform"] == "plex+jellyfin"
    titles = {s["title"] for s in out["sessions"]}
    assert "Heat" in titles and "Dune" in titles


# ── frontend wiring ──────────────────────────────────────────────────────────
def test_ui_is_wired():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    idx = (root / "webui" / "index.html").read_text(encoding="utf-8")
    js = (root / "webui" / "static" / "server-activity.js").read_text(encoding="utf-8")
    css = (root / "webui" / "static" / "style.css").read_text(encoding="utf-8")
    # app-wide floating button next to the notif bell + the script include
    assert 'id="activity-float-btn"' in idx and "ServerActivity.toggle()" in idx
    assert "server-activity.js" in idx
    # the drawer + poll + endpoints
    assert "/api/server-activity" in js and "/api/server-activity/image" in js
    assert "function card" in js and "function refresh" in js
    assert "setInterval(refresh, 3000)" in js         # live cadence while open
    assert "startBadgePoll" in js                     # ambient badge from any page
    # never touches the network-triggering user thumb — initials avatar instead
    assert "function initials" in js
    # tabbed: Activity + History (Phase 2)
    assert 'data-sact-tab="activity"' in js and 'data-sact-tab="history"' in js
    assert "function historyRow" in js and "function ago" in js
    assert "/api/server-activity/history" in js
    # the elegant bits exist in CSS
    assert ".sact-drawer" in css and ".sact-badge--tc" in css and ".activity-live" in css
    assert ".sact-tab--on" in css and ".sact-hrow" in css
    # stream termination (Tautulli's kill move)
    assert "data-sact-stop" in js and "function openStop" in js
    assert "/api/server-activity/stop" in js and ".sact-stop-modal" in css
    # stats tab: graph + rankings (Phase 3)
    assert 'data-sact-tab="stats"' in js and "function renderStats" in js
    assert "function graph" in js and "/api/server-activity/stats" in js
    assert ".sact-graph" in css and ".sact-rank-bar" in css


def test_web_server_registers_the_routes():
    from pathlib import Path
    ws = (Path(__file__).resolve().parent.parent / "web_server.py").read_text(encoding="utf-8")
    assert "@app.route('/api/server-activity')" in ws
    assert "@app.route('/api/server-activity/image')" in ws
    assert "@app.route('/api/server-activity/history')" in ws
    assert "@app.route('/api/server-activity/stop', methods=['POST'])" in ws
    assert "@app.route('/api/server-activity/stats')" in ws
    assert "is_admin" in ws.split("stop_server_activity_stream")[1][:400]   # admin-gated
