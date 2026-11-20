"""listenbrainz wants a token for an artist's top recordings since nov 2026.

soulsync sends a saved listenbrainz token on its own api reads (global first,
then admins, then profiles by id; only tokens for the official server), skips
the gated read up front with none, never caches a 401 as an empty answer,
puts a refused token aside for a while (not forever), and never logs it.
"""

from __future__ import annotations

import pytest

from core import lastfm_fallback as fb
from core import listenbrainz_auth as lba
from core import listenbrainz_public as lbp
from core import provider_cache

# the conftest stubs these per test; the real ones are kept here
REAL_CANDIDATES = lba._candidates
REAL_GET = lbp._get

TOKEN = "secret-token-abc123"


class _Resp:
    def __init__(self, payload=None, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {}

    def json(self):
        return self._payload


class _Session:
    """records the auth header each read went out with."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, dict(headers or {})))
        return self.answer(url) if callable(self.answer) else self.answer


class _Log:
    def __init__(self):
        self.lines = []

    def _add(self, level, msg, *args, **_kw):
        self.lines.append((level, msg % args if args else msg))

    def debug(self, msg, *a, **k):
        self._add("debug", msg, *a)

    def info(self, msg, *a, **k):
        self._add("info", msg, *a)

    def warning(self, msg, *a, **k):
        self._add("warning", msg, *a)


@pytest.fixture
def logs(monkeypatch):
    log = _Log()
    monkeypatch.setattr(lba, "logger", log)
    monkeypatch.setattr(lbp, "logger", log)
    return log


@pytest.fixture
def real_get(monkeypatch):
    monkeypatch.setattr(lbp, "_get", REAL_GET)
    monkeypatch.setattr(lbp, "MIN_GAP_SECONDS", 0)


def _tokens(monkeypatch, tokens):
    monkeypatch.setattr(lba, "_candidates", lambda: list(tokens))
    lba.reset()


# ── which token ──

class _Db:
    def __init__(self, profiles):
        self.profiles = profiles

    def get_profiles_with_listenbrainz(self):
        return list(self.profiles)


def _sources(monkeypatch, config, profiles):
    from core.settings import config_manager
    import database.music_database as mdb
    monkeypatch.setattr(lba, "_candidates", REAL_CANDIDATES)
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: config.get(key, default))
    monkeypatch.setattr(mdb, "get_database", lambda: _Db(profiles))
    lba.reset()


def test_the_global_token_wins_then_admins_then_profiles_by_id(monkeypatch):
    profiles = [{"id": 3, "token": "p3", "base_url": ""},
                {"id": 2, "token": "p2-admin", "base_url": "", "is_admin": True},
                {"id": 1, "token": "p1", "base_url": ""}]
    _sources(monkeypatch, {"listenbrainz.token": "global"}, profiles)
    assert lba._candidates() == ["global", "p2-admin", "p1", "p3"]
    assert lba.token() == "global"


def test_without_a_global_token_the_admin_profile_is_used(monkeypatch):
    profiles = [{"id": 4, "token": "p4", "base_url": ""},
                {"id": 1, "token": "p1", "base_url": "", "is_admin": True}]
    _sources(monkeypatch, {"listenbrainz.token": ""}, profiles)
    assert lba.token() == "p1"


def test_tokens_for_another_server_are_never_sent_to_listenbrainz(monkeypatch):
    profiles = [{"id": 1, "token": "koito", "base_url": "http://koito.local:4110/apis/listenbrainz"},
                {"id": 2, "token": "official", "base_url": "https://api.listenbrainz.org"}]
    _sources(monkeypatch, {"listenbrainz.token": "maloja",
                           "listenbrainz.base_url": "http://maloja:42010/apis/listenbrainz"}, profiles)
    assert lba._candidates() == ["official"]


def test_no_token_anywhere(monkeypatch):
    _sources(monkeypatch, {}, [])
    assert lba.token() is None
    assert lba.available() is False


def test_a_refused_token_steps_aside_for_hours_then_is_tried_again(monkeypatch, logs):
    _tokens(monkeypatch, ["first", "second"])
    now = [1000.0]
    monkeypatch.setattr(lba.time, "time", lambda: now[0])
    assert lba.token() == "first"
    lba.reject("first")
    assert lba.token() == "second"
    lba.reject("second")
    assert lba.token() is None
    now[0] += lba.REJECT_TTL + 1
    assert lba.token() == "first"
    assert all("first" not in line and "second" not in line for _lvl, line in logs.lines)


# ── listenbrainz_public ──

def test_the_token_goes_on_api_reads_and_not_to_labs(monkeypatch, real_get):
    _tokens(monkeypatch, [TOKEN])
    session = _Session(_Resp([]))
    monkeypatch.setattr(lbp, "_session", session)
    lbp._get(f"{lbp.API}/stats/user/u/artists")
    lbp._get(f"{lbp.LABS}/similar-artists/json")
    assert session.calls[0][1] == {"Authorization": f"Token {TOKEN}"}
    assert "Authorization" not in session.calls[1][1]


def test_open_reads_still_work_with_no_token(monkeypatch, real_get):
    session = _Session(_Resp({"payload": {"artists": [{"artist_name": "Low", "listen_count": 3}]}}))
    monkeypatch.setattr(lbp, "_session", session)
    assert lbp.user_top_artists("someone")[0]["name"] == "Low"
    assert "Authorization" not in session.calls[0][1]


def test_top_recordings_are_skipped_up_front_without_a_token(monkeypatch, real_get, logs):
    session = _Session(_Resp([{"recording_name": "Words", "total_listen_count": 5}]))
    monkeypatch.setattr(lbp, "_session", session)
    assert lbp.top_recordings_for_artist("mbid-1") == []
    assert lbp.top_recordings_for_artist("mbid-2") == []
    assert session.calls == []
    infos = [line for lvl, line in logs.lines if lvl == "info"]
    assert len(infos) == 1 and "connect ListenBrainz" in infos[0]


def test_top_recordings_send_the_token(monkeypatch, real_get):
    _tokens(monkeypatch, [TOKEN])
    session = _Session(_Resp([{"recording_name": "Words", "total_listen_count": 5}]))
    monkeypatch.setattr(lbp, "_session", session)
    assert lbp.top_recordings_for_artist("mbid-1")[0]["name"] == "Words"
    assert session.calls[0][1]["Authorization"] == f"Token {TOKEN}"


def test_a_401_is_not_cached_and_puts_the_token_aside(monkeypatch, real_get, logs):
    _tokens(monkeypatch, [TOKEN])
    session = _Session(_Resp({"code": 401, "error": "need an auth token"}, status=401))
    monkeypatch.setattr(lbp, "_session", session)
    assert lbp.top_recordings_for_artist("mbid-1") == []
    assert provider_cache.lookup("listenbrainz", "top-recordings-for-artist", {"mbid": "mbid-1"}) is None
    assert lba.token() is None
    # a new working token: asked again straight away, not served a cached []
    _tokens(monkeypatch, ["fresh"])
    session.answer = _Resp([{"recording_name": "Words", "total_listen_count": 5}])
    assert lbp.top_recordings_for_artist("mbid-1")[0]["name"] == "Words"
    assert all(TOKEN not in line for _lvl, line in logs.lines)


def test_a_401_without_a_token_puts_nothing_aside(monkeypatch, real_get):
    session = _Session(_Resp({}, status=401))
    monkeypatch.setattr(lbp, "_session", session)
    assert lbp._get(f"{lbp.API}/popularity/artist") is None
    _tokens(monkeypatch, ["later"])
    assert lba.token() == "later"


# ── the top-tracks fallback ──

def _deezer(monkeypatch):
    answers = {"search/artist": {"data": [{"id": 7, "name": "Low"}]},
               "artist/7/top": {"data": [{"title": "Sunflower"}]}}
    monkeypatch.setattr(fb, "_deezer_get", lambda endpoint, params=None: answers.get(endpoint))


def test_top_tracks_fall_to_deezer_without_asking_musicbrainz_when_no_token(monkeypatch):
    _deezer(monkeypatch)
    asked = []
    monkeypatch.setattr(lbp, "artist_mbid", lambda name: asked.append(name) or "MBID")
    rows, source = fb.artist_top_tracks("Low", lastfm=None)
    assert source == "deezer" and rows[0]["name"] == "Sunflower"
    assert asked == []


def test_top_tracks_come_from_listenbrainz_with_a_token(monkeypatch):
    _deezer(monkeypatch)
    _tokens(monkeypatch, [TOKEN])
    monkeypatch.setattr(lbp, "artist_mbid", lambda name: "MBID")
    monkeypatch.setattr(lbp, "_get", lambda url, params=None, timeout=15: [
        {"recording_name": "Lullaby", "total_listen_count": 9}])
    rows, source = fb.artist_top_tracks("Low", lastfm=None)
    assert source == "listenbrainz" and rows[0]["name"] == "Lullaby"


def test_a_refused_token_falls_through_to_deezer(monkeypatch, real_get):
    _deezer(monkeypatch)
    _tokens(monkeypatch, [TOKEN])
    monkeypatch.setattr(lbp, "artist_mbid", lambda name: "MBID")
    monkeypatch.setattr(lbp, "_session", _Session(_Resp({}, status=401)))
    rows, source = fb.artist_top_tracks("Low", lastfm=None)
    assert source == "deezer"


def test_the_database_lists_profile_tokens_by_id_with_the_admin_flag(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO profiles (id, name, is_admin) VALUES (5, 'five', 0)")
        conn.execute("INSERT INTO profiles (id, name, is_admin) VALUES (3, 'three', 1)")
        conn.commit()
    db.set_profile_listenbrainz(5, "t5")
    db.set_profile_listenbrainz(3, "t3", base_url="")
    rows = [(p["id"], p["token"], p["is_admin"]) for p in db.get_profiles_with_listenbrainz()]
    assert rows == [(3, "t3", True), (5, "t5", False)]
