import sqlite3
import sys
import types

import pytest


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

if "core.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("core.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["core.settings"] = settings_mod

from core.metadata import album_tracks as metadata_album_tracks
from core.metadata import registry as metadata_registry


@pytest.fixture(autouse=True)
def _clear_metadata_client_cache():
    metadata_registry.clear_cached_metadata_clients()
    yield
    metadata_registry.clear_cached_metadata_clients()


def _album(album_id="album-1", name="Album One", album_type="album"):
    return {
        "id": album_id,
        "name": name,
        "images": [{"url": f"https://img.example/{album_id}.jpg"}],
        "release_date": "2024-01-01",
        "album_type": album_type,
        "total_tracks": 1,
    }


def _track(track_id="track-1", name="Track One"):
    return {
        "id": track_id,
        "name": name,
        "artists": [{"name": "Artist One"}],
        "duration_ms": 123456,
        "track_number": 1,
        "disc_number": 1,
        "explicit": "explicit",
        "preview_url": "https://preview.example/track-1",
        "external_urls": {"spotify": "https://example/track-1"},
        "uri": f"spotify:track:{track_id}",
    }


def test_get_artist_album_tracks_uses_primary_source_priority(monkeypatch):
    calls = []

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: object())

    def fake_get_album_for_source(source, album_id, **kwargs):
        calls.append(("album", source, album_id))
        return _album("album-1", "Album One") if source == "deezer" and album_id == "album-1" else None

    def fake_get_album_tracks_for_source(source, album_id):
        calls.append(("tracks", source, album_id))
        return {"items": [_track()]} if source == "deezer" and album_id == "album-1" else None

    monkeypatch.setattr("core.metadata.album_tracks.get_album_for_source", fake_get_album_for_source)
    monkeypatch.setattr("core.metadata.album_tracks.get_album_tracks_for_source", fake_get_album_tracks_for_source)

    result = metadata_album_tracks.get_artist_album_tracks(
        "album-1",
        artist_name="Artist One",
        album_name="Album One",
    )

    assert result["success"] is True
    assert result["source"] == "deezer"
    assert result["source_priority"] == ["deezer", "spotify", "itunes"]
    assert result["resolved_album_id"] == "album-1"
    assert result["album"]["image_url"] == "https://img.example/album-1.jpg"
    assert result["tracks"][0]["artists"] == ["Artist One"]
    assert result["tracks"][0]["explicit"] is True
    assert calls == [("album", "deezer", "album-1"), ("tracks", "deezer", "album-1")]


def test_get_artist_album_tracks_resolves_database_album_reference(monkeypatch):
    calls = []

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: object())

    def fake_get_album_for_source(source, album_id, **kwargs):
        calls.append(("album", source, album_id))
        if source == "itunes" and album_id == "itunes-123":
            return _album("itunes-123", "Resolved Album")
        return None

    def fake_get_album_tracks_for_source(source, album_id):
        calls.append(("tracks", source, album_id))
        if source == "itunes" and album_id == "itunes-123":
            return {"items": [_track("itunes-track-1", "Resolved Track")]}
        return None

    def fake_resolve_album_reference(album_id, preferred_source=None, album_name="", artist_name=""):
        assert album_id == "db-1"
        assert preferred_source == "itunes"
        return "itunes-123", "itunes"

    monkeypatch.setattr("core.metadata.album_tracks.get_album_for_source", fake_get_album_for_source)
    monkeypatch.setattr("core.metadata.album_tracks.get_album_tracks_for_source", fake_get_album_tracks_for_source)
    monkeypatch.setattr("core.metadata.album_tracks.resolve_album_reference", fake_resolve_album_reference)

    result = metadata_album_tracks.get_artist_album_tracks(
        "db-1",
        artist_name="Artist One",
        album_name="Album One",
        source_override="itunes",
    )

    assert result["success"] is True
    assert result["source"] == "itunes"
    assert result["resolved_album_id"] == "itunes-123"
    assert result["tracks"][0]["name"] == "Resolved Track"
    assert ("album", "itunes", "itunes-123") in calls
    assert ("tracks", "itunes", "itunes-123") in calls


def test_resolve_album_reference_prefers_stored_external_id(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE lib2_artists (id INTEGER PRIMARY KEY, name TEXT)")
    cursor.execute(
        """
        CREATE TABLE lib2_albums (
            id INTEGER PRIMARY KEY,
            title TEXT,
            primary_artist_id INTEGER,
            spotify_id TEXT,
            musicbrainz_id TEXT,
            soul_id TEXT,
            external_ids TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    cursor.execute("INSERT INTO lib2_artists (id, name) VALUES (1, 'Artist One')")
    cursor.execute(
        """
        INSERT INTO lib2_albums (id, title, primary_artist_id, external_ids)
        VALUES (1, 'Album One', 1, '{"deezer": "deezer-abc"}')
        """
    )
    conn.commit()

    class _FakeDatabase:
        def _get_connection(self):
            return conn

    monkeypatch.setattr("database.music_database.get_database", lambda: _FakeDatabase())
    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify"])

    resolved_id, resolved_source = metadata_album_tracks.resolve_album_reference("1", preferred_source="deezer")

    assert resolved_id == "deezer-abc"
    assert resolved_source == "deezer"


def test_resolve_album_reference_searches_by_name_when_no_external_id_exists(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE lib2_artists (id INTEGER PRIMARY KEY, name TEXT)")
    cursor.execute(
        """
        CREATE TABLE lib2_albums (
            id INTEGER PRIMARY KEY,
            title TEXT,
            primary_artist_id INTEGER,
            spotify_id TEXT,
            musicbrainz_id TEXT,
            soul_id TEXT,
            external_ids TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    cursor.execute("INSERT INTO lib2_artists (id, name) VALUES (1, 'Artist One')")
    cursor.execute("INSERT INTO lib2_albums (id, title, primary_artist_id)"
                   " VALUES (1, 'Album One', 1)")
    conn.commit()

    class _FakeDatabase:
        def _get_connection(self):
            return conn

    class _FakeSearchClient:
        def __init__(self):
            self.calls = []

        def search_albums(self, query, **kwargs):
            self.calls.append((query, dict(kwargs)))
            return [types.SimpleNamespace(id="searched-123", name="Album One")]

    fake_client = _FakeSearchClient()
    monkeypatch.setattr("database.music_database.get_database", lambda: _FakeDatabase())
    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify"])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: fake_client if source == "deezer" else None)

    resolved_id, resolved_source = metadata_album_tracks.resolve_album_reference("1", preferred_source="deezer")

    assert resolved_id == "searched-123"
    assert resolved_source == "deezer"
    assert fake_client.calls == [("Artist One Album One", {"limit": 5})]


def test_resolve_album_reference_prefers_stored_jiosaavn_id(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE lib2_artists (id INTEGER PRIMARY KEY, name TEXT)")
    cursor.execute(
        """
        CREATE TABLE lib2_albums (
            id INTEGER PRIMARY KEY,
            title TEXT,
            primary_artist_id INTEGER,
            spotify_id TEXT,
            musicbrainz_id TEXT,
            soul_id TEXT,
            external_ids TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    cursor.execute("INSERT INTO lib2_artists (id, name) VALUES (1, 'Badshah')")
    cursor.execute(
        """
        INSERT INTO lib2_albums (id, title, primary_artist_id, external_ids)
        VALUES (1, 'Jugnu', 1, '{"jiosaavn": "30471107"}')
        """
    )
    conn.commit()

    class _FakeDatabase:
        def _get_connection(self):
            return conn

    monkeypatch.setattr("database.music_database.get_database", lambda: _FakeDatabase())
    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "jiosaavn")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "deezer"])

    resolved_id, resolved_source = metadata_album_tracks.resolve_album_reference(
        "1",
        preferred_source="jiosaavn",
    )

    assert resolved_id == "30471107"
    assert resolved_source == "jiosaavn"


def test_resolve_album_reference_skips_jiosaavn_client_when_disabled(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE lib2_artists (id INTEGER PRIMARY KEY, name TEXT)")
    cursor.execute(
        """
        CREATE TABLE lib2_albums (
            id INTEGER PRIMARY KEY,
            title TEXT,
            primary_artist_id INTEGER,
            spotify_id TEXT,
            musicbrainz_id TEXT,
            soul_id TEXT,
            external_ids TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    cursor.execute("INSERT INTO lib2_artists (id, name) VALUES (1, 'Artist One')")
    cursor.execute("INSERT INTO lib2_albums (id, title, primary_artist_id)"
                   " VALUES (1, 'Album One', 1)")
    conn.commit()

    class _FakeDatabase:
        def _get_connection(self):
            return conn

    class _FakeSearchClient:
        def search_albums(self, query, **kwargs):
            return [types.SimpleNamespace(id="js-123", name="Album One")]

    fake_client = _FakeSearchClient()
    monkeypatch.setattr("database.music_database.get_database", lambda: _FakeDatabase())
    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "jiosaavn")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "deezer"])
    monkeypatch.setattr(metadata_registry, "is_source_enabled", lambda source: source != "jiosaavn")
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: fake_client if source == "deezer" else None,
    )

    resolved_id, resolved_source = metadata_album_tracks.resolve_album_reference(
        "1",
        preferred_source="jiosaavn",
    )

    assert resolved_id == "js-123"
    assert resolved_source == "deezer"


class TestGetAlbumForSourceBandcamp:
    """Bandcamp has no numeric-ID lookup API — get_album_for_source's
    bandcamp branch must resolve by name and reshape the result into the
    'Spotify-shaped' dict this module's extraction helpers expect (Bandcamp's
    own field names — title/position — don't match the alias chains
    _extract_lookup_value checks). Regression coverage for the artist-detail
    discography-grid album click 404ing even after the release was found."""

    def test_resolves_by_name_and_reshapes_tracks(self, monkeypatch):
        class _FakeBandcampClient:
            def search_album(self, artist_name, album_name):
                assert artist_name == "Radiohead"
                assert album_name == "Hail to the Thief (Live Recordings 2003-2009)"
                return {
                    "id": "365742988",
                    "title": "Hail to the Thief (Live Recordings 2003-2009)",
                    "artist": "Radiohead",
                    "release_date": "2025-08-13",
                    "image_url": "https://f4.bcbits.com/img/0454733928_3.jpg",
                    "total_tracks": 1,
                    "tracks": [
                        {"position": 1, "title": "2 + 2 = 5 (Live)", "url": "https://x/1", "duration_ms": 216000},
                    ],
                }

        monkeypatch.setattr(
            metadata_registry, "get_client_for_source",
            lambda source, **kwargs: _FakeBandcampClient() if source == "bandcamp" else None,
        )

        album_data = metadata_album_tracks.get_album_for_source(
            "bandcamp", "album-365742988",
            artist_name="Radiohead", album_name="Hail to the Thief (Live Recordings 2003-2009)",
        )

        assert album_data is not None
        assert album_data["name"] == "Hail to the Thief (Live Recordings 2003-2009)"
        assert album_data["tracks"][0]["name"] == "2 + 2 = 5 (Live)"
        assert album_data["tracks"][0]["track_number"] == 1

    def test_no_names_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            metadata_registry, "get_client_for_source",
            lambda source, **kwargs: object() if source == "bandcamp" else None,
        )
        assert metadata_album_tracks.get_album_for_source("bandcamp", "album-1") is None

    def test_no_match_returns_none(self, monkeypatch):
        class _FakeBandcampClient:
            def search_album(self, artist_name, album_name):
                return None

        monkeypatch.setattr(
            metadata_registry, "get_client_for_source",
            lambda source, **kwargs: _FakeBandcampClient() if source == "bandcamp" else None,
        )
        assert metadata_album_tracks.get_album_for_source(
            "bandcamp", "album-1", artist_name="Nobody", album_name="Nothing",
        ) is None


def test_id_collision_in_foreign_catalog_is_rejected_by_name(monkeypatch):
    """The GNX → Lady Blacktronika bug: a library DB row id (or any foreign id)
    reaching the wrong catalog returns whatever unrelated release owns that
    number there, and the chain used to stop at that first 'success'. With the
    requested album name provided, a served album whose name bears no
    resemblance must be REJECTED so the chain can continue to a source that
    has the real thing."""
    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "itunes"])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: object())

    def fake_get_album_for_source(source, album_id, **kwargs):
        if source == "deezer":
            return _album("580654", "Luv Hate Us")          # the colliding stranger
        if source == "itunes":
            return _album("580654", "GNX")                  # the real release
        return None

    def fake_get_album_tracks_for_source(source, album_id):
        return {"items": [_track()]}

    monkeypatch.setattr("core.metadata.album_tracks.get_album_for_source", fake_get_album_for_source)
    monkeypatch.setattr("core.metadata.album_tracks.get_album_tracks_for_source", fake_get_album_tracks_for_source)

    result = metadata_album_tracks.get_artist_album_tracks(
        "580654", artist_name="Kendrick Lamar", album_name="GNX",
    )

    assert result["success"] is True
    assert result["source"] == "itunes"
    assert result["album"]["name"] == "GNX"


def test_name_guard_accepts_edition_variants(monkeypatch):
    """Deluxe/edition subtitles must NOT trip the guard — containment and the
    fuzzy ratio both tolerate real-world title noise."""
    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: object())

    monkeypatch.setattr(
        "core.metadata.album_tracks.get_album_for_source",
        lambda source, album_id, **kwargs: _album("a1", "GNX (Deluxe Edition)"),
    )
    monkeypatch.setattr(
        "core.metadata.album_tracks.get_album_tracks_for_source",
        lambda source, album_id: {"items": [_track()]},
    )

    result = metadata_album_tracks.get_artist_album_tracks(
        "a1", artist_name="Kendrick Lamar", album_name="GNX",
    )
    # Accepted (not rejected as a collision). The payload's display name is the
    # builder's business — the caller-provided name wins there by design.
    assert result["success"] is True
    assert len(result["tracks"]) == 1


def test_name_guard_is_inert_without_a_requested_name(monkeypatch):
    """No requested name → no basis to reject; the first hit serves as before."""
    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: object())

    monkeypatch.setattr(
        "core.metadata.album_tracks.get_album_for_source",
        lambda source, album_id, **kwargs: _album("a1", "Anything At All"),
    )
    monkeypatch.setattr(
        "core.metadata.album_tracks.get_album_tracks_for_source",
        lambda source, album_id: {"items": [_track()]},
    )

    result = metadata_album_tracks.get_artist_album_tracks("a1")
    assert result["success"] is True
    assert len(result["tracks"]) == 1
