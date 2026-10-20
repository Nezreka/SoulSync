"""every service on the connections tab gets a test button.

a tile said "configured" the moment a key was typed, with nothing to prove the
key worked. most backend tests already existed, they were wired to a row of
buttons inside the legacy api group, which the tiles rebuild hides.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import core.concerts_client as cc
from core import connection_test

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")

# hydrabase's own Connect button is its test: it opens the socket and says so
HAS_OWN_CHECK = {"hydrabase"}


def _frames(attr):
    """(id, body) for each service frame, body running to the next frame."""
    starts = [(m.start(), m.group(1)) for m in re.finditer(rf'{attr}="([^"]+)"', INDEX)]
    out = []
    for i, (pos, sid) in enumerate(starts):
        nxt = INDEX.find("api-service-frame", pos)
        out.append((sid, INDEX[pos:nxt if nxt > 0 else pos + 20000]))
    return out


def _connections_frames(attr):
    # the connections tab ends where the sources tab starts
    end = INDEX.find('data-stg="sources"')
    return [(sid, body) for sid, body in _frames(attr) if INDEX.find(body) < end]


# ── the page ────────────────────────────────────────────────────────────────

def test_every_music_service_frame_has_a_test_button():
    frames = _connections_frames("data-service")
    assert len(frames) >= 12
    missing = [sid for sid, body in frames
               if sid not in HAS_OWN_CHECK
               and not re.search(r'onclick="test[A-Za-z]*Connection\(', body)]
    assert missing == []


def test_every_video_service_frame_has_a_test_button():
    frames = [(s, b) for s, b in _connections_frames("data-video-service") if s != "prefs"]
    assert len(frames) >= 11
    missing = [sid for sid, body in frames if "data-video-test-service=" not in body]
    assert missing == []


def test_every_music_button_reaches_a_real_backend_branch():
    """a button calling testConnection('x') with no branch for x answers
    "Unknown service." which is worse than no button."""
    src = (ROOT / "core" / "connection_test.py").read_text(encoding="utf-8")
    called = set(re.findall(r"onclick=\"testConnection\('([a-z_]+)'\)", INDEX))
    assert {"concerts", "itunes", "musicbrainz", "discogs"} <= called
    unknown = [svc for svc in called
               if svc != "server" and f'service == "{svc}"' not in src]
    assert unknown == []


def test_video_bundle_buttons_name_real_workers():
    from core.video.enrichment.backfill import build_backfill_workers
    workers = set(build_backfill_workers(SimpleNamespace(get_setting=lambda k: None)))
    for spec in re.findall(r'data-video-test-service="([^"]+)"', INDEX):
        for svc in spec.split(","):
            if svc in {"tmdb", "tvdb", "omdb", "subdl", "subsource", "mdblist"}:
                continue        # matcher clients / subtitle providers / list source
            assert svc in workers, svc
            # the js only tests a switched-on service, and finds the switch by id
            assert f'id="video-{svc}-enabled"' in INDEX or "," not in spec


# ── concerts ────────────────────────────────────────────────────────────────

def _resp(status, payload=None):
    return SimpleNamespace(status_code=status, ok=status < 400, json=lambda: payload or {})


@pytest.fixture()
def concert_keys(monkeypatch):
    state = {"concerts.setlistfm_api_key": "", "concerts.ticketmaster_api_key": ""}
    monkeypatch.setattr(cc, "_cfg", lambda path, default="": state.get(path, default))
    return state


def _route(monkeypatch, tm=200, sl=200):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return _resp(tm if "ticketmaster" in url else sl)
    monkeypatch.setattr(cc.requests, "get", fake_get)
    return calls


def test_concerts_with_no_keys_says_so_without_calling_out(concert_keys, monkeypatch):
    calls = _route(monkeypatch)
    ok, msg = cc.check_keys()
    assert not ok and "No concert keys" in msg and calls == []


def test_concerts_tests_only_the_key_that_is_set(concert_keys, monkeypatch):
    concert_keys["concerts.setlistfm_api_key"] = "sl"
    calls = _route(monkeypatch)
    assert cc.check_keys() == (True, "Setlist.fm key works")
    assert len(calls) == 1 and "setlist.fm" in calls[0]


def test_concerts_bad_ticketmaster_key_fails_even_when_setlist_works(concert_keys, monkeypatch):
    concert_keys.update({"concerts.setlistfm_api_key": "sl", "concerts.ticketmaster_api_key": "tm"})
    _route(monkeypatch, tm=401)
    ok, msg = cc.check_keys()
    assert not ok
    assert msg == "Ticketmaster rejected the API key · Setlist.fm key works"


def test_setlist_404_is_no_results_not_a_bad_key(concert_keys, monkeypatch):
    concert_keys["concerts.setlistfm_api_key"] = "sl"
    _route(monkeypatch, sl=404)
    assert cc.check_keys()[0] is True
    _route(monkeypatch, sl=403)
    assert cc.check_keys() == (False, "Setlist.fm rejected the API key")


def test_run_service_test_routes_concerts(monkeypatch):
    monkeypatch.setattr(cc, "check_keys", lambda: (True, "both fine"))
    with patch.object(connection_test.config_manager, "get", return_value={}), \
         patch.object(connection_test.config_manager, "set"):
        assert connection_test.run_service_test("concerts", {}) == (True, "both fine")


# ── deezer login ────────────────────────────────────────────────────────────

def _deezer(monkeypatch, token, me_payload):
    import core.deezer_throttle as throttle
    monkeypatch.setattr(throttle, "wait_for_slot", lambda **kw: True)
    urls = []

    def fake_get(url, params=None, timeout=None):
        urls.append(url)
        if url.endswith("/user/me"):
            assert params == {"access_token": token}
            return _resp(200, me_payload)
        return _resp(200, {"data": []})
    monkeypatch.setattr(connection_test.requests, "get", fake_get)
    values = {"deezer.access_token": token}
    with patch.object(connection_test.config_manager, "get",
                      side_effect=lambda k, d=None: values.get(k, d) if k != "deezer" else {}), \
         patch.object(connection_test.config_manager, "set"):
        return connection_test.run_service_test("deezer", {}), urls


def test_deezer_without_a_login_only_checks_the_public_api(monkeypatch):
    result, urls = _deezer(monkeypatch, None, None)
    assert result == (True, "Deezer Public API reachable")
    assert not any(u.endswith("/user/me") for u in urls)


def test_deezer_with_a_live_login_names_the_account(monkeypatch):
    result, _ = _deezer(monkeypatch, "tok", {"id": 5, "name": "boulder"})
    assert result == (True, "Deezer connected as boulder")


def test_deezer_expired_login_warns_but_keeps_metadata_green(monkeypatch):
    """the dashboard runs this same test for deezer as a metadata source, which
    still works with a dead login. so it passes, with the warning glyph."""
    result, _ = _deezer(monkeypatch, "tok", {"error": {"type": "OAuthException", "code": 300}})
    ok, msg = result
    assert ok is True and msg.startswith(connection_test.WARNING_MARKER)
    assert "expired" in msg


# ── mdblist ─────────────────────────────────────────────────────────────────

def test_mdblist_key_check(monkeypatch):
    import requests

    from core.video.collections.list_sources import check_mdblist_key
    assert check_mdblist_key("") == (False, "No MDBList API key set")

    answers = {"good": _resp(200, {"username": "boulder"}), "bad": _resp(403, {"error": "Invalid API key"})}
    seen = []

    def fake_get(url, params=None, timeout=None):
        seen.append(url)
        return answers[params["apikey"]]
    monkeypatch.setattr(requests, "get", fake_get)
    assert check_mdblist_key("good") == (True, "MDBList connected as boulder")
    assert check_mdblist_key("bad") == (False, "MDBList rejected the API key")
    assert seen == ["https://api.mdblist.com/user"] * 2


def test_video_test_endpoint_reaches_mdblist(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.collections.list_sources as ls
    import core.video.enrichment.engine as eng_mod
    from flask import Flask

    from database.video_database import VideoDatabase

    seen = {}
    monkeypatch.setattr(ls, "check_mdblist_key", lambda key: (seen.setdefault("key", key), (True, "fine"))[1])
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.set_setting("mdblist_api_key", "the-key")
    videoapi._video_db = db
    # mdblist must answer before the engine is ever asked for a worker
    eng_mod._engine = SimpleNamespace(worker=lambda s: pytest.fail("asked the engine"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        r = app.test_client().post("/api/video/enrichment/mdblist/test").get_json()
        assert r == {"success": True, "message": "fine", "error": None}
        assert seen["key"] == "the-key"
    finally:
        videoapi._video_db = None
        eng_mod._engine = None
