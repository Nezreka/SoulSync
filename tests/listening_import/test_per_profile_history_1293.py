"""#1293 (cremonies): a navidrome admin can't read anyone else's plays, so every
profile's stats, recently played and discovery were the admin's listening.

the fix is piles. every play lands in the shared pile (owner 1) unless the
profile connected its own listenbrainz, then its listenbrainz history and its
web-player plays are its own and it reads only those. a profile without one
reads the shared pile, same as before, so nobody's page goes empty on update.

every test here runs the real MusicDatabase on a tmp file.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

import core.listening_import.listenbrainz as lb_module
from core.listening_import.dedup import insert_import_events
from core.listening_import.listenbrainz import (
    STATE_KEY,
    ListenBrainzImportWorkers,
    ListenBrainzListeningImportWorker,
)
from core.listening_scope import (
    PILE_KEY_BASES,
    SHARED_OWNER,
    listening_owner,
    listening_owners,
    owner_key,
    pile_keys,
)
from database.music_database import MusicDatabase


class _Config:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _profile(db, name, *, lb_user=None):
    pid = db.create_profile(name=name)
    if lb_user:
        assert db.set_profile_listenbrainz(pid, f"token-{lb_user}", "", lb_user)
    return pid


def _play(db, *, title, artist, played_at, owner=SHARED_OWNER, source="plex", track_id=None):
    return insert_import_events(db, [{
        "track_id": track_id or f"{source}:{title}:{played_at}",
        "title": title,
        "artist": artist,
        "album": "Album",
        "played_at": played_at,
        "duration_ms": 180000,
    }], source, profile_id=owner)


def _rows(db, sql, args=()):
    conn = db._get_connection()
    try:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


# ── the migration ────────────────────────────────────────────────────────────

def test_fresh_install_has_the_owner_column_and_owner_aware_dedup_index(db):
    cols = {r[1] for r in _rows(db, "PRAGMA table_info(listening_history)")}
    assert "profile_id" in cols
    indexes = {r[1] for r in _rows(db, "PRAGMA index_list(listening_history)")}
    assert "idx_listening_dedup_pile" in indexes
    assert "idx_listening_dedup" not in indexes
    alias_cols = {r[1] for r in _rows(db, "PRAGMA table_info(listening_import_events)")}
    assert "profile_id" in alias_cols


def test_upgrade_moves_every_existing_play_and_alias_into_the_shared_pile(tmp_path):
    """an install from before #1293: one table, no owner, old unique index, old
    alias table. every row there was the shared pile, and has to stay readable."""
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE listening_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, track_id TEXT, title TEXT NOT NULL,
            artist TEXT, album TEXT, played_at TIMESTAMP NOT NULL, duration_ms INTEGER DEFAULT 0,
            server_source TEXT, db_track_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            scrobbled_lastfm INTEGER DEFAULT 0, scrobbled_listenbrainz INTEGER DEFAULT 0);
        CREATE UNIQUE INDEX idx_listening_dedup ON listening_history (track_id, played_at, server_source);
        INSERT INTO listening_history (track_id, title, artist, album, played_at, server_source)
            VALUES ('lb-1', 'Song', 'Artist', 'Album', '2026-09-01 10:00:00', 'listenbrainz');
        CREATE TABLE listening_import_events (
            source TEXT NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL,
            listened_at INTEGER NOT NULL,
            history_id INTEGER NOT NULL REFERENCES listening_history(id) ON DELETE CASCADE,
            PRIMARY KEY (source, title, artist, listened_at), UNIQUE (history_id, source));
        INSERT INTO listening_import_events VALUES ('listenbrainz', 'song', 'artist', 1788256800, 1);
    """)
    conn.commit()
    conn.close()

    db = MusicDatabase(path)

    assert _rows(db, "SELECT profile_id FROM listening_history") == [(1,)]
    assert _rows(db, "SELECT source, history_id, profile_id FROM listening_import_events") == [
        ("listenbrainz", 1, 1)]
    indexes = {r[1] for r in _rows(db, "PRAGMA index_list(listening_history)")}
    assert "idx_listening_dedup" not in indexes
    assert db.get_listening_stats("all")["total_plays"] == 1
    # the carried-over alias still stops a re-import from doubling the play
    assert _play(db, title="Song", artist="Artist", played_at="2026-09-01 10:00:00",
                 source="listenbrainz", track_id="lb-1") == 0


# ── who reads which pile ─────────────────────────────────────────────────────

def test_only_a_profile_with_its_own_listenbrainz_gets_its_own_pile(db):
    kim = _profile(db, "kim", lb_user="kim_lb")
    bob = _profile(db, "bob")
    assert listening_owner(db, SHARED_OWNER) == SHARED_OWNER
    assert listening_owner(db, None) == SHARED_OWNER
    assert listening_owner(db, bob) == SHARED_OWNER
    assert listening_owner(db, kim) == kim
    assert listening_owners(db) == [SHARED_OWNER, kim]

    db.clear_profile_listenbrainz(kim)
    assert listening_owner(db, kim) == SHARED_OWNER


def test_every_reader_keeps_the_piles_apart(db):
    """the admin's navidrome plays must never show up as kim's, and kim's own
    listening must never leak into the admin's (or bob's) stats."""
    kim = _profile(db, "kim", lb_user="kim_lb")
    bob = _profile(db, "bob")
    _play(db, title="Admin Song", artist="Admin Artist", played_at="2026-09-20 10:00:00")
    _play(db, title="Kim Song", artist="Kim Artist", played_at="2026-09-20 11:00:00",
          owner=kim, source="listenbrainz")

    def artists(pid):
        return [a["name"] for a in db.get_top_artists("all", 10, profile_id=pid)]

    assert artists(kim) == ["Kim Artist"]
    assert artists(SHARED_OWNER) == ["Admin Artist"]
    assert artists(bob) == ["Admin Artist"]
    assert artists(None) == ["Admin Artist"]

    assert db.get_listening_stats("all", profile_id=kim)["total_plays"] == 1
    assert [t["name"] for t in db.get_top_tracks("all", 10, profile_id=kim)] == ["Kim Song"]
    assert [a["name"] for a in db.get_top_albums("all", 10, profile_id=bob)] == ["Album"]
    assert db.get_listening_clock("all", profile_id=kim)["total"] == 1
    assert db.get_play_counts_by_name(["Kim Artist", "Admin Artist"], kim) == {"kim artist": 1}
    assert db.get_play_counts_by_name(["Kim Artist", "Admin Artist"], bob) == {"admin artist": 1}

    from datetime import datetime
    year = db.get_year_in_listening(now=datetime(2026, 9, 23), profile_id=kim)
    assert [a["name"] for a in year["top_artists"]] == ["Kim Artist"]
    assert [a["name"] for a in year["discoveries"]] == ["Kim Artist"]

    from core.stats import queries
    recent = queries.get_recent_tracks(db, 10, profile_id=kim)
    assert [r["title"] for r in recent] == ["Kim Song"]
    events = queries.get_listening_events(db, None, time_range="all", filter_type="hour",
                                          hour=10, profile_id=kim)
    assert events["items"] == []

    assert db.listening_history_scope(kim) == "profile"
    assert db.listening_history_scope(bob) == "shared"
    assert db.listening_history_scope(SHARED_OWNER) == "shared"


# ── the dedup key ────────────────────────────────────────────────────────────

def test_two_piles_can_hold_the_same_listen(db):
    """a household on one listenbrainz account: the admin's global import and
    kim's own import see the exact same listen. before the owner was in the key
    kim's copy hit the unique index and aliased onto the admin's row."""
    kim = _profile(db, "kim", lb_user="shared_lb")
    same = dict(title="Song", artist="Artist", played_at="2026-09-20 10:00:00",
                source="listenbrainz", track_id="recording-1")
    assert _play(db, **same) == 1
    assert _play(db, owner=kim, **same) == 1
    assert db.get_listening_stats("all", profile_id=kim)["total_plays"] == 1
    assert db.get_listening_stats("all")["total_plays"] == 1
    # and re-imports stay idempotent in both piles
    assert _play(db, **same) == 0
    assert _play(db, owner=kim, **same) == 0


def test_cross_source_matching_never_links_into_another_pile(db):
    """the admin's plex play and kim's listenbrainz scrobble 3s apart are two
    people, not one play seen twice."""
    kim = _profile(db, "kim", lb_user="kim_lb")
    assert _play(db, title="Song", artist="Artist", played_at="2026-09-20 10:00:00") == 1
    assert _play(db, title="Song", artist="Artist", played_at="2026-09-20 10:00:03",
                 owner=kim, source="listenbrainz") == 1
    assert db.get_listening_stats("all", profile_id=kim)["total_plays"] == 1


def test_web_player_events_land_in_the_pile_they_carry(db):
    kim = _profile(db, "kim", lb_user="kim_lb")
    db.insert_listening_events([
        {"title": "Mine", "artist": "A", "played_at": "2026-09-20 10:00:00",
         "server_source": "soulsync", "profile_id": kim},
        {"title": "Shared", "artist": "B", "played_at": "2026-09-20 10:05:00",
         "server_source": "soulsync"},
    ])
    assert [a["name"] for a in db.get_top_artists("all", 10, profile_id=kim)] == ["A"]
    assert [a["name"] for a in db.get_top_artists("all", 10)] == ["B"]


# ── the scrobbler ────────────────────────────────────────────────────────────

def test_a_profiles_own_plays_never_go_to_the_admins_lastfm(db, monkeypatch):
    """kim's listenbrainz import marks rows scrobbled for listenbrainz but not
    last.fm, so an unscoped scrobbler posted her whole history to the admin's
    last.fm account."""
    import core.lastfm_client as lastfm_client
    from core.listening_stats_worker import ListeningStatsWorker

    kim = _profile(db, "kim", lb_user="kim_lb")
    _play(db, title="Admin Song", artist="Admin", played_at="2026-09-20 10:00:00")
    _play(db, title="Kim Song", artist="Kim", played_at="2026-09-20 11:00:00",
          owner=kim, source="listenbrainz")

    sent = []

    class FakeLastFM:
        def __init__(self, **_kw):
            pass

        def scrobble_tracks(self, tracks):
            sent.extend(t["track"] for t in tracks)
            return True

    monkeypatch.setattr(lastfm_client, "LastFMClient", FakeLastFM)
    worker = ListeningStatsWorker(db, _Config({
        "lastfm.scrobble_enabled": True, "lastfm.api_key": "k",
        "lastfm.api_secret": "s", "lastfm.session_key": "sk",
    }))
    worker._scrobble_new_events()

    assert sent == ["Admin Song"]


# ── the stats cache ──────────────────────────────────────────────────────────

def test_stats_cache_is_built_and_read_per_pile(db):
    from core.listening_stats_worker import ListeningStatsWorker
    from core.stats import queries

    kim = _profile(db, "kim", lb_user="kim_lb")
    bob = _profile(db, "bob")
    _play(db, title="Admin Song", artist="Admin Artist", played_at="2026-09-20 10:00:00")
    _play(db, title="Kim Song", artist="Kim Artist", played_at="2026-09-20 11:00:00",
          owner=kim, source="listenbrainz")

    ListeningStatsWorker(db, _Config())._build_stats_cache()

    def cached(pid):
        data = queries.get_cached_stats(db, lambda u: u, "all", profile_id=pid)
        return [a["name"] for a in data["top_artists"]], [r["title"] for r in data["recent"]]

    assert cached(kim) == (["Kim Artist"], ["Kim Song"])
    assert cached(SHARED_OWNER) == (["Admin Artist"], ["Admin Song"])
    assert cached(bob) == (["Admin Artist"], ["Admin Song"])
    # the shared pile keeps the key it always had
    assert db.get_metadata("stats_cache_all")
    assert db.get_metadata(owner_key("stats_cache_all", kim))

    year = queries.get_year_in_listening(db, lambda u: u, profile_id=kim)
    assert year["cached"] is True


# ── the importer ─────────────────────────────────────────────────────────────

class _FakeLB:
    """one page of listens for whoever asks, never the network."""
    seen = []

    def __init__(self, token=None, base_url=None):
        self.token = token

    def get_user_listen_count(self, username):
        return 1

    def get_user_listens(self, username, min_ts=None, max_ts=None, count=100):
        _FakeLB.seen.append((self.token, username))
        return {"payload": {"listens": [{
            "listened_at": 1788256800,
            "track_metadata": {"track_name": f"{username} song", "artist_name": username},
        }]}}

    def get_authenticated_username(self):
        return None


def test_a_profile_worker_imports_its_own_account_into_its_own_pile(db, monkeypatch):
    monkeypatch.setattr(lb_module, "ListenBrainzClient", _FakeLB)
    _FakeLB.seen = []
    kim = _profile(db, "kim", lb_user="kim_lb")
    config = _Config({"listenbrainz.token": "admin-token", "listenbrainz.username": "admin_lb"})

    worker = ListenBrainzListeningImportWorker(db, config, profile_id=kim)
    # a typed-in name is ignored: a profile imports the account it connected
    state = worker.run_once(username="someone_else")

    assert state["status"] == "complete"
    assert _FakeLB.seen == [("token-kim_lb", "kim_lb")]
    assert [a["name"] for a in db.get_top_artists("all", 10, profile_id=kim)] == ["kim_lb"]
    assert db.get_top_artists("all", 10) == []
    # its own resume state, the admin's untouched
    assert json.loads(db.get_metadata(owner_key(STATE_KEY, kim)))["username"] == "kim_lb"
    assert db.get_metadata(STATE_KEY) is None
    assert config.values["listenbrainz.username"] == "admin_lb"


def test_the_shared_worker_still_uses_the_account_in_settings(db, monkeypatch):
    monkeypatch.setattr(lb_module, "ListenBrainzClient", _FakeLB)
    _FakeLB.seen = []
    config = _Config({"listenbrainz.token": "admin-token", "listenbrainz.username": "admin_lb"})

    ListenBrainzListeningImportWorker(db, config).run_once()

    assert _FakeLB.seen == [("admin-token", "admin_lb")]
    assert [a["name"] for a in db.get_top_artists("all", 10)] == ["admin_lb"]


def test_the_registry_runs_every_profile_pile_and_skips_plain_profiles(db, monkeypatch):
    monkeypatch.setattr(lb_module, "ListenBrainzClient", _FakeLB)
    _FakeLB.seen = []
    kim = _profile(db, "kim", lb_user="kim_lb")
    _profile(db, "bob")
    workers = ListenBrainzImportWorkers(db, _Config())

    results = workers.run_profiles()

    assert list(results) == [kim]
    assert results[kim]["status"] == "complete"
    assert _FakeLB.seen == [("token-kim_lb", "kim_lb")]


def test_switching_listenbrainz_accounts_drops_only_the_old_accounts_plays(db, monkeypatch):
    """the old account's history isn't this person's anymore. their web-player
    plays are, so those stay."""
    monkeypatch.setattr(lb_module, "ListenBrainzClient", _FakeLB)
    kim = _profile(db, "kim", lb_user="old_lb")
    _play(db, title="Old LB", artist="X", played_at="2026-09-20 10:00:00",
          owner=kim, source="listenbrainz")
    _play(db, title="Web Play", artist="Y", played_at="2026-09-20 12:00:00",
          owner=kim, source="soulsync")
    db.set_metadata(owner_key("stats_cache_all", kim), "{}")
    workers = ListenBrainzImportWorkers(db, _Config())
    started = threading.Event()
    monkeypatch.setattr(workers, "start_profile", lambda owner, full=False: started.set() or {})

    workers.on_connected(kim, "old_lb", "old_lb")
    assert _rows(db, "SELECT COUNT(*) FROM listening_history WHERE profile_id = ?", (kim,)) == [(2,)]

    db.set_profile_listenbrainz(kim, "token-new", "", "new_lb")
    workers.on_connected(kim, "old_lb", "new_lb")

    assert _rows(db, "SELECT title FROM listening_history WHERE profile_id = ?", (kim,)) == [("Web Play",)]
    assert db.get_metadata(owner_key("stats_cache_all", kim)) is None
    assert started.is_set()


def test_the_admin_connecting_listenbrainz_changes_nothing(db):
    _play(db, title="Admin Song", artist="A", played_at="2026-09-20 10:00:00", source="listenbrainz")
    workers = ListenBrainzImportWorkers(db, _Config())
    assert workers.on_connected(SHARED_OWNER, "old", "new")["status"] == "skipped"
    assert db.reset_listening_pile(SHARED_OWNER) == 0
    assert db.get_listening_stats("all")["total_plays"] == 1


def test_a_profile_import_only_rebuilds_its_own_pile_and_only_tells_its_room():
    built, emitted = [], []
    workers = ListenBrainzImportWorkers(
        SimpleNamespace(get_metadata=lambda _k: None),
        _Config(),
        cache_builder=lambda owners=None: built.append(owners),
        progress_callback=lambda state, owner=None: emitted.append(owner),
    )
    kim_worker = workers.for_owner(7)
    kim_worker.cache_builder()
    kim_worker.progress_callback({})
    workers.shared.cache_builder()
    workers.shared.progress_callback({})
    assert built == [[7], None]
    assert emitted == [7, None]


# ── deleting a profile ───────────────────────────────────────────────────────

def test_deleting_a_profile_takes_its_pile_and_its_caches(db):
    kim = _profile(db, "kim", lb_user="kim_lb")
    _play(db, title="Kim Song", artist="Kim", played_at="2026-09-20 11:00:00",
          owner=kim, source="listenbrainz")
    for key in pile_keys(kim):
        db.set_metadata(key, "{}")

    assert db.delete_profile(kim)

    assert _rows(db, "SELECT COUNT(*) FROM listening_history WHERE profile_id = ?", (kim,)) == [(0,)]
    assert _rows(db, "SELECT COUNT(*) FROM listening_import_events WHERE profile_id = ?", (kim,)) == [(0,)]
    assert all(db.get_metadata(k) is None for k in pile_keys(kim))


def test_pile_keys_cover_the_import_state_and_never_the_shared_pile():
    assert STATE_KEY in PILE_KEY_BASES
    assert pile_keys(SHARED_OWNER) == []
    assert owner_key(STATE_KEY, 5) in pile_keys(5)


# ── the hourly automation ────────────────────────────────────────────────────

def test_the_hourly_sync_runs_profile_piles_even_with_the_admins_sync_off():
    """connecting their own listenbrainz was the opt-in. the admin's switch is
    about the admin's account."""
    from core.automation.handlers.listenbrainz_import import auto_import_listenbrainz_listening

    ran = []
    deps = SimpleNamespace(
        config_manager=_Config({"listenbrainz.listening_sync_enabled": False}),
        listenbrainz_import_worker=SimpleNamespace(run_once=lambda **_kw: ran.append("shared")),
        listenbrainz_import_workers=SimpleNamespace(
            run_profiles=lambda full=False: ran.append("profiles") or {7: {"status": "complete"}}),
    )

    result = auto_import_listenbrainz_listening({}, deps)

    assert ran == ["profiles"]
    assert result["status"] == "skipped"
    assert result["profiles"] == {7: {"status": "complete"}}


# ── the stats page's import card ─────────────────────────────────────────────

def test_the_import_card_shows_a_profile_its_own_account(db, monkeypatch):
    from flask import Flask

    import api.stats as stats

    kim = _profile(db, "kim", lb_user="kim_lb")
    config = _Config({"listenbrainz.token": "admin-token", "listenbrainz.username": "admin_lb"})
    workers = ListenBrainzImportWorkers(db, config)
    started = []
    monkeypatch.setattr(workers.for_owner(kim), "start_import",
                        lambda username=None, full=False: started.append(username) or {"status": "started"})
    monkeypatch.setattr(stats, "get_database", lambda: db)
    monkeypatch.setattr(stats, "config_manager", config)
    monkeypatch.setattr(stats, "_listenbrainz_import_worker", lambda: workers.shared)
    monkeypatch.setattr(stats, "_listenbrainz_import_workers", lambda: workers)
    monkeypatch.setattr(stats, "_automation_engine", lambda: None)
    monkeypatch.setattr(stats, "get_current_profile_id", lambda: kim)
    app = Flask(__name__)
    app.register_blueprint(stats.bp)
    client = app.test_client()

    status = client.get("/api/listenbrainz/listening-import/status").get_json()
    assert status["username"] == "kim_lb"
    assert status["history_scope"] == "profile"
    assert status["token_configured"] is True

    ran = client.post("/api/listenbrainz/listening-import/run", json={"username": "admin_lb2"})
    assert ran.status_code == 200
    assert started == [None]
    assert config.values["listenbrainz.username"] == "admin_lb"

    monkeypatch.setattr(stats, "get_current_profile_id", lambda: SHARED_OWNER)
    assert client.get("/api/listenbrainz/listening-import/status").get_json()["history_scope"] == "shared"


# ── last.fm per profile ──────────────────────────────────────────────────────
#
# a non-admin types their last.fm username in My Accounts. scrobbles are
# public, so the app's api key reads them, no login, nothing secret stored.

import core.listening_import.lastfm as fm_module  # noqa: E402
from core.listening_import.lastfm import LastFMImportWorkers, LastFMListeningImportWorker  # noqa: E402
from core.listening_scope import has_own_account, profiles_with_account  # noqa: E402

FM_STATE_KEY = fm_module.STATE_KEY


def _fm_profile(db, name, username):
    pid = db.create_profile(name=name)
    assert db.set_profile_lastfm(pid, username)
    return pid


class _FakeFM:
    """one page of scrobbles for whoever asks, never the network."""
    made = []
    seen = []

    def __init__(self, api_key="", api_secret="", session_key=""):
        _FakeFM.made.append((api_key, api_secret, session_key))

    def get_user_recent_tracks(self, username, page=1, limit=200, from_ts=None, to_ts=None, extended=False):
        _FakeFM.seen.append(username)
        return {"recenttracks": {
            "@attr": {"page": "1", "totalPages": "1", "total": "1", "user": username},
            "track": [{"name": f"{username} song", "artist": {"#text": username},
                       "album": {"#text": "Album"}, "date": {"uts": "1788256800"}}],
        }}

    def get_authenticated_username(self):
        return None


@pytest.fixture()
def fake_fm(monkeypatch):
    monkeypatch.setattr(fm_module, "LastFMClient", _FakeFM)
    _FakeFM.made, _FakeFM.seen = [], []
    return _FakeFM


def test_a_lastfm_username_alone_gives_a_profile_its_own_pile(db):
    kim = _fm_profile(db, "kim", "kim_fm")
    lb = _profile(db, "lee", lb_user="lee_lb")
    assert listening_owner(db, kim) == kim
    assert listening_owners(db) == [SHARED_OWNER, kim, lb]
    assert profiles_with_account(db, "lastfm") == [kim]
    assert profiles_with_account(db, "listenbrainz") == [lb]
    assert has_own_account(db, kim, "lastfm") and not has_own_account(db, kim, "listenbrainz")

    db.clear_profile_lastfm(kim)
    assert listening_owner(db, kim) == SHARED_OWNER


def test_a_profile_lastfm_worker_reads_its_own_name_with_the_apps_key_only(db, fake_fm):
    """never the admin's session: a profile's pile is read from public
    scrobbles, and a typed-in name is ignored."""
    kim = _fm_profile(db, "kim", "kim_fm")
    config = _Config({"lastfm.api_key": "app-key", "lastfm.api_secret": "admin-secret",
                      "lastfm.session_key": "admin-session", "lastfm.username": "admin_fm"})

    state = LastFMListeningImportWorker(db, config, profile_id=kim).run_once(username="someone_else")

    assert state["status"] == "complete"
    assert fake_fm.seen == ["kim_fm"]
    assert all(made == ("app-key", "", "") for made in fake_fm.made)
    assert [a["name"] for a in db.get_top_artists("all", 10, profile_id=kim)] == ["kim_fm"]
    assert db.get_top_artists("all", 10) == []
    assert json.loads(db.get_metadata(owner_key(FM_STATE_KEY, kim)))["username"] == "kim_fm"
    assert db.get_metadata(FM_STATE_KEY) is None
    assert config.values["lastfm.username"] == "admin_fm"


def test_each_registry_only_runs_profiles_with_its_own_account(db, fake_fm, monkeypatch):
    monkeypatch.setattr(lb_module, "ListenBrainzClient", _FakeLB)
    _FakeLB.seen = []
    kim = _fm_profile(db, "kim", "kim_fm")
    lee = _profile(db, "lee", lb_user="lee_lb")
    config = _Config({"lastfm.api_key": "app-key"})

    fm = LastFMImportWorkers(db, config).run_profiles()
    lb = ListenBrainzImportWorkers(db, config).run_profiles()

    assert list(fm) == [kim] and fm[kim]["status"] == "complete"
    assert list(lb) == [lee] and lb[lee]["status"] == "complete"
    assert fake_fm.seen == ["kim_fm"]
    assert _FakeLB.seen == [("token-lee_lb", "lee_lb")]


def test_switching_lastfm_accounts_keeps_the_listenbrainz_side(db):
    """both connected: a new last.fm name drops the old last.fm plays and only
    last.fm's resume state. the listenbrainz plays and crawl are untouched."""
    kim = _profile(db, "kim", lb_user="kim_lb")
    db.set_profile_lastfm(kim, "old_fm")
    _play(db, title="Old FM", artist="X", played_at="2026-09-20 10:00:00", owner=kim, source="lastfm")
    _play(db, title="LB Play", artist="Y", played_at="2026-09-20 12:00:00", owner=kim, source="listenbrainz")
    db.set_metadata(owner_key(FM_STATE_KEY, kim), "{}")
    db.set_metadata(owner_key(STATE_KEY, kim), '{"username": "kim_lb"}')
    workers = LastFMImportWorkers(db, _Config())
    workers.start_profile = lambda owner, full=False: {}

    db.set_profile_lastfm(kim, "new_fm")
    workers.on_connected(kim, "old_fm", "new_fm")

    assert _rows(db, "SELECT title FROM listening_history WHERE profile_id = ?", (kim,)) == [("LB Play",)]
    assert db.get_metadata(owner_key(FM_STATE_KEY, kim)) is None
    assert db.get_metadata(owner_key(STATE_KEY, kim)) == '{"username": "kim_lb"}'


def test_the_hourly_lastfm_sync_runs_profile_piles_even_with_the_admins_sync_off():
    from core.automation.handlers.lastfm_import import auto_import_lastfm_listening

    ran = []
    deps = SimpleNamespace(
        config_manager=_Config({"lastfm.listening_sync_enabled": False}),
        lastfm_import_worker=SimpleNamespace(run_once=lambda **_kw: ran.append("shared")),
        lastfm_import_workers=SimpleNamespace(
            run_profiles=lambda full=False: ran.append("profiles") or {7: {"status": "complete"}}),
    )

    result = auto_import_lastfm_listening({}, deps)

    assert ran == ["profiles"]
    assert result["profiles"] == {7: {"status": "complete"}}


def _stats_client(db, monkeypatch, pid, config):
    from flask import Flask

    import api.stats as stats

    lb_workers = ListenBrainzImportWorkers(db, config)
    fm_workers = LastFMImportWorkers(db, config)
    monkeypatch.setattr(stats, "get_database", lambda: db)
    monkeypatch.setattr(stats, "config_manager", config)
    monkeypatch.setattr(stats, "_listenbrainz_import_worker", lambda: lb_workers.shared)
    monkeypatch.setattr(stats, "_listenbrainz_import_workers", lambda: lb_workers)
    monkeypatch.setattr(stats, "_lastfm_import_worker", lambda: fm_workers.shared)
    monkeypatch.setattr(stats, "_lastfm_import_workers", lambda: fm_workers)
    monkeypatch.setattr(stats, "_automation_engine", lambda: None)
    monkeypatch.setattr(stats, "get_current_profile_id", lambda: pid)
    app = Flask(__name__)
    app.register_blueprint(stats.bp)
    return app.test_client(), fm_workers


def test_a_lastfm_only_profile_gets_its_own_lastfm_card_and_no_listenbrainz_card(db, monkeypatch):
    kim = _fm_profile(db, "kim", "kim_fm")
    config = _Config({"lastfm.api_key": "app-key", "lastfm.username": "admin_fm",
                      "listenbrainz.token": "admin-token", "listenbrainz.username": "admin_lb"})
    client, fm_workers = _stats_client(db, monkeypatch, kim, config)
    started = []
    monkeypatch.setattr(fm_workers.for_owner(kim), "start_import",
                        lambda username=None, full=False: started.append(username) or {"status": "started"})

    fm = client.get("/api/lastfm/listening-import/status").get_json()
    assert (fm["username"], fm["history_scope"], fm["own_account"]) == ("kim_fm", "profile", True)
    lb = client.get("/api/listenbrainz/listening-import/status").get_json()
    assert (lb["history_scope"], lb["own_account"]) == ("profile", False)
    assert "username" not in lb

    assert client.post("/api/lastfm/listening-import/run", json={"username": "x"}).status_code == 200
    assert started == [None]
    assert config.values["lastfm.username"] == "admin_fm"
    # the admin's listenbrainz import isn't hers to kick off
    assert client.post("/api/listenbrainz/listening-import/run", json={}).status_code == 400
    assert config.values["listenbrainz.username"] == "admin_lb"


def test_the_shared_lastfm_card_is_unchanged_for_the_admin(db, monkeypatch):
    config = _Config({"lastfm.api_key": "app-key", "lastfm.username": "admin_fm"})
    client, _ = _stats_client(db, monkeypatch, SHARED_OWNER, config)
    fm = client.get("/api/lastfm/listening-import/status").get_json()
    assert (fm["username"], fm["history_scope"], fm["own_account"]) == ("admin_fm", "shared", False)


def test_deleting_a_profile_takes_its_lastfm_import_state_too(db):
    kim = _fm_profile(db, "kim", "kim_fm")
    db.set_metadata(owner_key(FM_STATE_KEY, kim), "{}")
    assert db.delete_profile(kim)
    assert db.get_metadata(owner_key(FM_STATE_KEY, kim)) is None
