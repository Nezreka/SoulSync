import sys
import types


class _DummyConfigManager:
    def get(self, key, default=None):
        return default

    def get_active_media_server(self):
        return "plex"


if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")

    class _DummySpotify:
        def __init__(self, *args, **kwargs):
            pass

    oauth2 = types.ModuleType("spotipy.oauth2")

    class _DummyOAuth:
        def __init__(self, *args, **kwargs):
            pass

    spotipy.Spotify = _DummySpotify
    oauth2.SpotifyOAuth = _DummyOAuth
    oauth2.SpotifyClientCredentials = _DummyOAuth
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

if "config.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("config.settings")

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from core.repair_jobs.album_completeness import AlbumCompletenessJob
import core.repair_jobs.album_completeness as album_completeness_module


class _FakeCursor:
    def __init__(self, owned_track_numbers):
        self._owned_track_numbers = owned_track_numbers
        self._last_query = ""

    def execute(self, query, params=None):
        self._last_query = query
        return self

    def fetchall(self):
        if "SELECT track_number" in self._last_query:
            return [(track_number,) for track_number in self._owned_track_numbers]
        return []


class _FakeConnection:
    def __init__(self, owned_track_numbers):
        self._cursor = _FakeCursor(owned_track_numbers)

    def cursor(self):
        return self._cursor

    def close(self):
        return None


class _FakeDB:
    def __init__(self, owned_track_numbers):
        self._owned_track_numbers = owned_track_numbers

    def _get_connection(self):
        return _FakeConnection(self._owned_track_numbers)


class _FakeSpotifyClient:
    def __init__(self, track_count=5):
        self.track_count = track_count
        self.calls = []

    def is_spotify_authenticated(self):
        return True

    def get_album_tracks(self, album_id):
        self.calls.append(album_id)
        return {
            "items": [
                {"id": f"sp-{i}", "name": f"Spotify Track {i}", "track_number": i, "disc_number": 1, "artists": []}
                for i in range(1, self.track_count + 1)
            ]
        }


class _FakeDeezerClient:
    def __init__(self, track_count=2):
        self.track_count = track_count
        self.calls = []

    def is_authenticated(self):
        return True

    def get_album_tracks(self, album_id):
        self.calls.append(album_id)
        return {
            "items": [
                {"id": f"dz-{i}", "name": f"Deezer Track {i}", "track_number": i, "disc_number": 1, "artists": []}
                for i in range(1, self.track_count + 1)
            ]
        }


class _FakeITunesClient:
    def __init__(self):
        self.calls = []

    def is_authenticated(self):
        return True

    def get_album_tracks(self, album_id):
        self.calls.append(album_id)
        return {"items": []}


class _FakeDiscogsClient:
    def __init__(self, track_count=3):
        self.track_count = track_count
        self.calls = []

    def get_album_tracks(self, album_id):
        self.calls.append(album_id)
        return {
            "items": [
                {"id": f"dg-{i}", "name": f"Discogs Track {i}", "track_number": i, "disc_number": 1, "artists": []}
                for i in range(1, self.track_count + 1)
            ]
        }


class _FakeHydrabaseClient:
    def __init__(self, track_count=4):
        self.track_count = track_count
        self.calls = []

    def is_connected(self):
        return True

    def get_album_tracks_dict(self, album_id):
        self.calls.append(album_id)
        return {
            "items": [
                {"id": f"hy-{i}", "name": f"Hydrabase Track {i}", "track_number": i, "disc_number": 1, "artists": []}
                for i in range(1, self.track_count + 1)
            ]
        }


def test_album_completeness_uses_primary_provider_first(monkeypatch):
    job = AlbumCompletenessJob()
    context = types.SimpleNamespace(
        db=_FakeDB(owned_track_numbers={1}),
        spotify_client=_FakeSpotifyClient(track_count=5),
        is_spotify_rate_limited=lambda: False,
    )

    deezer_client = _FakeDeezerClient(track_count=2)
    itunes_client = _FakeITunesClient()
    calls = []
    album_ids = {
        "spotify": "spotify-album",
        "itunes": "itunes-album",
        "deezer": "deezer-album",
        "discogs": "",
        "hydrabase": "",
    }

    monkeypatch.setattr(album_completeness_module, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(
        album_completeness_module,
        "get_album_tracks_for_source",
        lambda source, album_id: (
            calls.append((source, album_id)) or
            (
                deezer_client.get_album_tracks(album_id) if source == "deezer" else
                itunes_client.get_album_tracks(album_id) if source == "itunes" else
                context.spotify_client.get_album_tracks(album_id) if source == "spotify" else
                {"items": []}
            )
        )
    )

    expected_total = job._get_expected_total(context, "deezer", album_ids)
    missing_tracks = job._find_missing_tracks(context, "deezer", 42, album_ids)

    assert expected_total == 2
    assert calls == [("deezer", "deezer-album"), ("deezer", "deezer-album")]
    assert deezer_client.calls == ["deezer-album", "deezer-album"]
    assert context.spotify_client.calls == []
    assert itunes_client.calls == []

    assert [track["track_number"] for track in missing_tracks] == [2]
    assert missing_tracks[0]["source"] == "deezer"
    assert missing_tracks[0]["source_track_id"] == "dz-2"
    assert missing_tracks[0]["spotify_track_id"] == "dz-2"


def test_album_completeness_supports_discogs_primary(monkeypatch):
    job = AlbumCompletenessJob()
    context = types.SimpleNamespace(
        db=_FakeDB(owned_track_numbers={1}),
        spotify_client=_FakeSpotifyClient(track_count=5),
        is_spotify_rate_limited=lambda: False,
    )

    discogs_client = _FakeDiscogsClient(track_count=3)
    itunes_client = _FakeITunesClient()
    deezer_client = _FakeDeezerClient(track_count=2)
    calls = []
    album_ids = {
        "spotify": "spotify-album",
        "itunes": "itunes-album",
        "deezer": "deezer-album",
        "discogs": "discogs-release",
        "hydrabase": "",
    }

    monkeypatch.setattr(album_completeness_module, "get_primary_source", lambda: "discogs")
    monkeypatch.setattr(
        album_completeness_module,
        "get_album_tracks_for_source",
        lambda source, album_id: (
            calls.append((source, album_id)) or
            (
                discogs_client.get_album_tracks(album_id) if source == "discogs" else
                itunes_client.get_album_tracks(album_id) if source == "itunes" else
                deezer_client.get_album_tracks(album_id) if source == "deezer" else
                context.spotify_client.get_album_tracks(album_id) if source == "spotify" else
                {"items": []}
            )
        )
    )

    expected_total = job._get_expected_total(context, "discogs", album_ids)
    missing_tracks = job._find_missing_tracks(context, "discogs", 42, album_ids)

    assert expected_total == 3
    assert calls == [("discogs", "discogs-release"), ("discogs", "discogs-release")]
    assert discogs_client.calls == ["discogs-release", "discogs-release"]
    assert context.spotify_client.calls == []
    assert itunes_client.calls == []
    assert deezer_client.calls == []

    assert [track["track_number"] for track in missing_tracks] == [2, 3]
    assert missing_tracks[0]["source"] == "discogs"
    assert missing_tracks[0]["source_track_id"] == "dg-2"


def test_album_completeness_supports_hydrabase_primary(monkeypatch):
    job = AlbumCompletenessJob()
    context = types.SimpleNamespace(
        db=_FakeDB(owned_track_numbers={1}),
        spotify_client=_FakeSpotifyClient(track_count=5),
        is_spotify_rate_limited=lambda: False,
    )

    hydrabase_client = _FakeHydrabaseClient(track_count=4)
    itunes_client = _FakeITunesClient()
    deezer_client = _FakeDeezerClient(track_count=2)
    calls = []
    album_ids = {
        "spotify": "spotify-album",
        "itunes": "itunes-album",
        "deezer": "deezer-album",
        "discogs": "",
        "hydrabase": "soul-album",
    }

    monkeypatch.setattr(album_completeness_module, "get_primary_source", lambda: "hydrabase")
    monkeypatch.setattr(
        album_completeness_module,
        "get_album_tracks_for_source",
        lambda source, album_id: (
            calls.append((source, album_id)) or
            (
                hydrabase_client.get_album_tracks_dict(album_id) if source == "hydrabase" else
                itunes_client.get_album_tracks(album_id) if source == "itunes" else
                deezer_client.get_album_tracks(album_id) if source == "deezer" else
                context.spotify_client.get_album_tracks(album_id) if source == "spotify" else
                {"items": []}
            )
        )
    )

    expected_total = job._get_expected_total(context, "hydrabase", album_ids)
    missing_tracks = job._find_missing_tracks(context, "hydrabase", 42, album_ids)

    assert expected_total == 4
    assert calls == [("hydrabase", "soul-album"), ("hydrabase", "soul-album")]
    assert hydrabase_client.calls == ["soul-album", "soul-album"]
    assert context.spotify_client.calls == []
    assert itunes_client.calls == []
    assert deezer_client.calls == []

    assert [track["track_number"] for track in missing_tracks] == [2, 3, 4]
    assert missing_tracks[0]["source"] == "hydrabase"
    assert missing_tracks[0]["source_track_id"] == "hy-2"


# ---------------------------------------------------------------------------
# api_track_count caching — the fix for the "0.1s / 0 findings" bug
# ---------------------------------------------------------------------------

class _ApiCountCursor:
    """Records UPDATE statements so we can verify the cache write."""

    def __init__(self):
        self.updates = []

    def execute(self, query, params=None):
        if query.strip().startswith("UPDATE albums"):
            self.updates.append((query, params))
        return self

    def fetchall(self):
        return []


class _ApiCountConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def close(self):
        return None


class _ApiCountDB:
    def __init__(self):
        self.cursor = _ApiCountCursor()

    def _get_connection(self):
        return _ApiCountConnection(self.cursor)


def test_save_api_track_count_writes_update_to_db():
    """The helper persists the resolved count so subsequent scans don't
    refetch the expected total from the API."""
    job = AlbumCompletenessJob()
    db = _ApiCountDB()
    context = types.SimpleNamespace(db=db)

    job._save_api_track_count(context, "album-42", 12)

    assert len(db.cursor.updates) == 1
    query, params = db.cursor.updates[0]
    assert "UPDATE albums" in query
    assert "api_track_count = ?" in query
    assert params == (12, "album-42")


def test_save_api_track_count_swallows_errors():
    """A cache-write failure must not break the scan — the job falls back
    to the pre-cache behavior (API call next time)."""
    job = AlbumCompletenessJob()

    class _Boom:
        def _get_connection(self):
            raise RuntimeError("db is gone")

    context = types.SimpleNamespace(db=_Boom())
    # Should not raise.
    job._save_api_track_count(context, "album-x", 10)


# ---------------------------------------------------------------------------
# Integration tests — run the full scan loop against a real sqlite in-memory
# DB so SELECT/PRAGMA/UPDATE go through actual SQL. Catches wiring mistakes
# between the SELECT, column_index, loop, and finding creation that the
# isolated helper tests wouldn't surface.
# ---------------------------------------------------------------------------

import sqlite3
import uuid


class _SharedMemoryDB:
    """Tiny shim matching MusicDatabase's `_get_connection()` contract,
    backed by a shared-cache sqlite in-memory DB so `close()` on a
    per-call connection doesn't destroy the data."""

    def __init__(self):
        self.uri = f"file:testdb_{uuid.uuid4().hex}?mode=memory&cache=shared"
        # Keepalive conn holds the in-memory DB alive while other conns
        # open/close. Without this, the DB is garbage-collected when the
        # last conn closes.
        self._keepalive = sqlite3.connect(self.uri, uri=True)
        self._keepalive.executescript(
            """
            CREATE TABLE artists (
                id TEXT PRIMARY KEY,
                name TEXT,
                thumb_url TEXT
            );
            CREATE TABLE albums (
                id TEXT PRIMARY KEY,
                artist_id TEXT,
                title TEXT,
                thumb_url TEXT,
                spotify_album_id TEXT,
                itunes_album_id TEXT,
                deezer_id TEXT,
                discogs_id TEXT,
                soul_id TEXT,
                track_count INTEGER,
                api_track_count INTEGER
            );
            CREATE TABLE tracks (
                id TEXT PRIMARY KEY,
                album_id TEXT,
                track_number INTEGER
            );
            """
        )
        self._keepalive.commit()

    def _get_connection(self):
        return sqlite3.connect(self.uri, uri=True)

    def insert_artist(self, artist_id, name, thumb=None):
        self._keepalive.execute(
            "INSERT INTO artists (id, name, thumb_url) VALUES (?, ?, ?)",
            (artist_id, name, thumb),
        )
        self._keepalive.commit()

    def insert_album(self, album_id, artist_id, title, *, spotify_id=None,
                      track_count=None, api_track_count=None):
        self._keepalive.execute(
            """INSERT INTO albums
               (id, artist_id, title, spotify_album_id, track_count, api_track_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (album_id, artist_id, title, spotify_id, track_count, api_track_count),
        )
        self._keepalive.commit()

    def insert_tracks(self, album_id, count):
        rows = [(f"{album_id}-t{i}", album_id, i) for i in range(1, count + 1)]
        self._keepalive.executemany(
            "INSERT INTO tracks (id, album_id, track_number) VALUES (?, ?, ?)",
            rows,
        )
        self._keepalive.commit()

    def fetch_api_track_count(self, album_id):
        row = self._keepalive.execute(
            "SELECT api_track_count FROM albums WHERE id = ?", (album_id,)
        ).fetchone()
        return row[0] if row else None


def _make_job_context(db, *, create_finding):
    """Minimal JobContext stand-in covering the fields scan() touches."""
    return types.SimpleNamespace(
        db=db,
        transfer_folder='',
        config_manager=_DummyConfigManager(),
        spotify_client=None,
        is_spotify_rate_limited=lambda: False,
        stop_event=None,
        create_finding=create_finding,
        should_stop=None,
        is_paused=None,
        update_progress=None,
        report_progress=None,
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
    )


def test_scan_uses_cached_api_track_count_without_expected_total_lookup(monkeypatch):
    """Integration: when api_track_count is populated, the scan reads the
    expected total from the cache. `_get_expected_total` must NOT be called
    for albums with a cached value. (The missing-tracks lookup may still
    hit the API for incomplete albums — that's a separate call path used
    only after we've decided the album is incomplete.)"""
    db = _SharedMemoryDB()
    db.insert_artist('a1', 'Test Artist')
    db.insert_album('alb-incomplete', 'a1', 'Incomplete Album',
                    spotify_id='sp-1', track_count=10, api_track_count=12)
    db.insert_tracks('alb-incomplete', 10)
    db.insert_album('alb-complete', 'a1', 'Complete Album',
                    spotify_id='sp-2', track_count=8, api_track_count=8)
    db.insert_tracks('alb-complete', 8)

    # Stub the track-lookup used by _find_missing_tracks (needed for the
    # incomplete album's finding details). We're not asserting on it.
    monkeypatch.setattr(
        album_completeness_module,
        "get_album_tracks_for_source",
        lambda source, album_id: {"items": [
            {"track_number": i, "name": f"T{i}", "artists": []} for i in range(1, 13)
        ]},
    )
    monkeypatch.setattr(album_completeness_module, "get_primary_source", lambda: "spotify")
    monkeypatch.setattr(
        album_completeness_module, "get_source_priority",
        lambda primary: ["spotify", "itunes", "deezer", "discogs", "hydrabase"],
    )

    # Spy on _get_expected_total specifically — that's the call path the
    # cache is supposed to short-circuit.
    job = AlbumCompletenessJob()
    expected_total_calls = []
    original_get_expected = job._get_expected_total

    def spy(context_, primary_, album_ids_):
        expected_total_calls.append(album_ids_.get('spotify'))
        return original_get_expected(context_, primary_, album_ids_)
    job._get_expected_total = spy

    findings = []
    context = _make_job_context(db, create_finding=lambda **kwargs: (findings.append(kwargs) or True))

    result = job.scan(context)

    # _get_expected_total was NOT called — both albums had cached counts.
    assert expected_total_calls == []
    # Exactly one finding for the incomplete album.
    assert result.findings_created == 1
    assert len(findings) == 1
    finding = findings[0]
    assert finding['entity_id'] == 'alb-incomplete'
    assert finding['details']['expected_tracks'] == 12
    assert finding['details']['actual_tracks'] == 10


def test_scan_falls_back_to_api_and_persists_count_on_cache_miss(monkeypatch):
    """Integration: when api_track_count is NULL, scan calls the API,
    gets the expected total, caches it, and creates the finding."""
    db = _SharedMemoryDB()
    db.insert_artist('a1', 'Test Artist')
    # api_track_count is NULL — will need API lookup
    db.insert_album('alb-fresh', 'a1', 'Fresh Album',
                    spotify_id='sp-fresh', track_count=8, api_track_count=None)
    db.insert_tracks('alb-fresh', 8)

    # API returns 14 tracks (so 8 owned out of 14 → finding)
    monkeypatch.setattr(
        album_completeness_module,
        "get_album_tracks_for_source",
        lambda source, album_id: {
            "items": [{"track_number": i, "name": f"T{i}", "artists": []} for i in range(1, 15)],
        } if source == "spotify" and album_id == "sp-fresh" else None,
    )
    monkeypatch.setattr(album_completeness_module, "get_primary_source", lambda: "spotify")
    monkeypatch.setattr(
        album_completeness_module, "get_source_priority",
        lambda primary: ["spotify"],
    )

    findings = []
    context = _make_job_context(db, create_finding=lambda **kwargs: (findings.append(kwargs) or True))

    job = AlbumCompletenessJob()
    result = job.scan(context)

    # Finding for 8/14.
    assert result.findings_created == 1
    finding = findings[0]
    assert finding['details']['expected_tracks'] == 14
    assert finding['details']['actual_tracks'] == 8
    # Crucially: the scan persisted the count so the next scan won't refetch.
    assert db.fetch_api_track_count('alb-fresh') == 14


def test_scan_ignores_track_count_completely(monkeypatch):
    """Regression: the observed `track_count` (Plex's leafCount) must
    NOT influence the expected-total comparison. Before the fix, an
    album with track_count=actual_count was skipped as 'complete' even
    when the metadata source said it had more tracks."""
    db = _SharedMemoryDB()
    db.insert_artist('a1', 'Test Artist')
    # track_count=10 matches actual=10 (sassmastawillis's bug scenario).
    # api_track_count=15 says the album actually has 15 tracks.
    db.insert_album('alb-bug', 'a1', 'Bug Reproduction',
                    spotify_id='sp-bug', track_count=10, api_track_count=15)
    db.insert_tracks('alb-bug', 10)

    monkeypatch.setattr(
        album_completeness_module, "get_album_tracks_for_source",
        lambda source, album_id: None,  # should NOT be called
    )
    monkeypatch.setattr(album_completeness_module, "get_primary_source", lambda: "spotify")
    monkeypatch.setattr(
        album_completeness_module, "get_source_priority",
        lambda primary: ["spotify"],
    )

    findings = []
    context = _make_job_context(db, create_finding=lambda **kwargs: (findings.append(kwargs) or True))

    job = AlbumCompletenessJob()
    result = job.scan(context)

    # The album MUST be flagged (10/15), not silently skipped.
    assert result.findings_created == 1
    assert findings[0]['details']['expected_tracks'] == 15
    assert findings[0]['details']['actual_tracks'] == 10
