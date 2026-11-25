"""Now playing, a play sent the moment it counts, and Koito as a ListenBrainz server."""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import Mock

import pytest
import requests

import core.listenbrainz_client as lb_mod
from core.lastfm_client import LastFMClient
from core.listenbrainz_client import ListenBrainzClient
from core.listening_scope import SHARED_OWNER
from core.listening_stats_worker import ListeningStatsWorker
from core.playback import now_playing
from database.music_database import MusicDatabase


class _Cfg:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _response(data, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data).encode()
    return result


# ── who hears about it ───────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def test_shared_pile_goes_to_the_settings_accounts_when_scrobbling_is_on(db):
    cfg = _Cfg(**{"lastfm.scrobble_enabled": True, "lastfm.session_key": "sess",
                  "listenbrainz.scrobble_enabled": True, "listenbrainz.token": "tok"})
    assert now_playing.scrobble_targets(db, cfg, SHARED_OWNER) == [
        {"lb_token": "tok", "lb_base_url": None, "lastfm_session": "sess"}]


def test_nothing_set_up_means_nothing_sent(db):
    cfg = _Cfg(**{"lastfm.session_key": "sess", "listenbrainz.token": "tok"})   # scrobbling off
    assert now_playing.scrobble_targets(db, cfg, SHARED_OWNER) == []


def test_a_profile_with_its_own_pile_only_reaches_its_own_accounts(db, monkeypatch):
    pid = db.create_profile("Kid")
    monkeypatch.setattr("core.listening_scope.listening_owner", lambda _db, p: p)
    monkeypatch.setattr(db, "profiles_scrobbling", lambda: [
        {"profile_id": pid, "lastfm_session": None, "listenbrainz_token": "kidtok",
         "listenbrainz_base_url": "http://koito.lan:4110/apis/listenbrainz"}])
    cfg = _Cfg(**{"lastfm.scrobble_enabled": True, "lastfm.session_key": "admin-sess"})
    assert now_playing.scrobble_targets(db, cfg, pid) == [
        {"lb_token": "kidtok", "lb_base_url": "http://koito.lan:4110/apis/listenbrainz",
         "lastfm_session": None}]


def test_a_profile_with_its_own_pile_but_no_scrobbling_sends_nothing(db, monkeypatch):
    pid = db.create_profile("Quiet")
    monkeypatch.setattr("core.listening_scope.listening_owner", lambda _db, p: p)
    monkeypatch.setattr(db, "profiles_scrobbling", lambda: [])
    cfg = _Cfg(**{"lastfm.scrobble_enabled": True, "lastfm.session_key": "admin-sess"})
    assert now_playing.scrobble_targets(db, cfg, pid) == []


def test_send_reaches_both_services(monkeypatch):
    calls = []

    class _LB:
        def __init__(self, token=None, base_url=None):
            calls.append(("lb-init", token, base_url))

        def submit_playing_now(self, *args):
            calls.append(("lb", *args))
            return True

    class _FM:
        def __init__(self, api_key="", api_secret="", session_key=""):
            calls.append(("fm-init", session_key))

        def update_now_playing(self, *args):
            calls.append(("fm", *args))
            return True

    monkeypatch.setattr("core.listenbrainz_client.ListenBrainzClient", _LB)
    monkeypatch.setattr("core.lastfm_client.LastFMClient", _FM)
    cfg = _Cfg(**{"lastfm.api_key": "k", "lastfm.api_secret": "s"})
    sent = now_playing.send_now_playing(
        {"lb_token": "t", "lb_base_url": None, "lastfm_session": "sess"}, cfg,
        {"title": "Song", "artist": "Artist", "album": "Album", "duration_seconds": 201.4})
    assert sent == {"listenbrainz": True, "lastfm": True}
    assert ("lb", "Artist", "Song", "Album", 201) in calls
    assert ("fm", "Artist", "Song", "Album", 201) in calls


def test_queue_runs_in_the_background(monkeypatch, db):
    done = threading.Event()
    monkeypatch.setattr(now_playing, "scrobble_targets", lambda *a: [{"lb_token": "t"}])
    monkeypatch.setattr(now_playing, "send_now_playing", lambda *a: done.set())
    assert now_playing.queue_now_playing(db, _Cfg(), 1, {"title": "x", "artist": "y"}) == 1
    assert done.wait(5)


# ── the two clients ──────────────────────────────────────────────────────

def test_listenbrainz_playing_now_payload(monkeypatch):
    monkeypatch.setattr(ListenBrainzClient, "_validate_and_get_username",
                        lambda self: setattr(self, "username", "me") or True)
    client = ListenBrainzClient(token="tok", base_url="https://lb.example")
    client.session.post = Mock(return_value=_response({"status": "ok"}))
    assert client.submit_playing_now("Artist", "Song", "Album", 200) is True
    body = client.session.post.call_args.kwargs["json"]
    assert body["listen_type"] == "playing_now"
    assert "listened_at" not in body["payload"][0]
    assert body["payload"][0]["track_metadata"] == {
        "artist_name": "Artist", "track_name": "Song", "release_name": "Album",
        "additional_info": {"duration": 200}}


def test_lastfm_update_now_playing_is_signed(monkeypatch):
    monkeypatch.setattr("core.lastfm_client.key_rejection", lambda key: None)
    client = LastFMClient(api_key="k", api_secret="s", session_key="sess")
    client.session.post = Mock(return_value=_response({"nowplaying": {}}))
    assert client.update_now_playing("Artist", "Song", "Album", 200) is True
    data = client.session.post.call_args.kwargs["data"]
    assert data["method"] == "track.updateNowPlaying"
    assert data["sk"] == "sess" and data["duration"] == "200" and data["api_sig"]


def test_lastfm_refused_key_sends_nothing(monkeypatch):
    monkeypatch.setattr("core.lastfm_client.key_rejection", lambda key: object())
    client = LastFMClient(api_key="k", api_secret="s", session_key="sess")
    client.session.post = Mock()
    assert client.update_now_playing("Artist", "Song") is False
    client.session.post.assert_not_called()


# ── a play goes out the moment it counts ─────────────────────────────────

def test_a_flush_request_wakes_the_worker_before_the_batch(tmp_path, monkeypatch):
    import core.listening_stats_worker as worker_mod
    monkeypatch.setattr(worker_mod, "interruptible_sleep",
                        lambda stop_event, seconds, step=0.5: stop_event.is_set())
    worker = ListeningStatsWorker(MusicDatabase(str(tmp_path / "w.db")),
                                  _Cfg(**{"listening_stats.enabled": False}))
    worker._build_stats_cache = lambda *a, **k: None
    flushes = []
    worker._scrobble_new_events = lambda: flushes.append(time.monotonic())
    worker.scrobble_interval = 3600     # the batch never comes round in this test
    thread = threading.Thread(target=worker._run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and len(flushes) < 1:
            time.sleep(0.05)
        assert len(flushes) == 1        # the startup flush
        time.sleep(0.3)
        assert len(flushes) == 1        # and nothing more on its own
        worker.request_scrobble_flush()
        deadline = time.time() + 3
        while time.time() < deadline and len(flushes) < 2:
            time.sleep(0.05)
        assert len(flushes) == 2, "an eligible play waited for the batch"
    finally:
        worker.should_stop = True
        worker._stop_event.set()
        thread.join(timeout=5)


# ── Koito ────────────────────────────────────────────────────────────────

@pytest.fixture
def koito(monkeypatch):
    monkeypatch.setattr(ListenBrainzClient, "_validate_and_get_username", lambda self: True)
    monkeypatch.setattr(lb_mod, "_KOITO_SERVERS", {})
    return ListenBrainzClient(token="key", base_url="http://koito.lan:4110/apis/listenbrainz")


def _koito_row(iso, title="Song", artists=("A", "B")):
    return {"time": iso, "track": {"id": 1, "title": title,
                                   "artists": [{"id": i, "name": n} for i, n in enumerate(artists)]}}


def test_koito_history_reads_its_own_listen_api(koito):
    def get(url, **kwargs):
        if url.endswith("/apis/web/v1/health"):
            return _response({})
        assert url == "http://koito.lan:4110/apis/web/v1/listens"
        assert kwargs["headers"] == {"Authorization": "Token key"}
        assert kwargs["params"]["to"] == 1700000099 and kwargs["params"]["from"] == 1
        return _response({"items": [_koito_row("2023-11-14T22:13:20Z")], "has_next_page": False})

    koito.session.get = Mock(side_effect=get)
    data = koito.get_user_listens("me", max_ts=1700000100, count=100)
    assert data["payload"]["listens"] == [{
        "listened_at": 1700000000,
        "track_metadata": {"track_name": "Song", "artist_name": "A, B",
                           "release_name": "", "additional_info": {}}}]
    assert koito._maloja_base_url() is None
    assert koito.get_user_listen_count("me") is None


def test_the_koito_probe_is_asked_once_per_server(koito):
    koito.session.get = Mock(return_value=_response({}))
    assert koito._koito_root() == "http://koito.lan:4110"
    assert koito._koito_root() == "http://koito.lan:4110"
    assert koito.session.get.call_count == 1


def test_maloja_on_the_same_path_is_still_maloja(koito):
    koito.session.get = Mock(return_value=_response({}, 404))
    assert koito._koito_root() is None
    assert koito._maloja_base_url() == "http://koito.lan:4110/apis/mlj_1"


def test_the_official_server_is_never_probed(monkeypatch):
    monkeypatch.setattr(ListenBrainzClient, "_validate_and_get_username", lambda self: True)
    client = ListenBrainzClient(token="t", base_url="https://api.listenbrainz.org")
    client.session.get = Mock()
    assert client._koito_root() is None
    client.session.get.assert_not_called()
