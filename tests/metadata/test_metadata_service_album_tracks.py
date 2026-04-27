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

if "config.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("config.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from core import metadata_service


@pytest.fixture(autouse=True)
def _clear_metadata_client_cache():
    metadata_service.clear_cached_metadata_clients()
    yield
    metadata_service.clear_cached_metadata_clients()


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

    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(metadata_service, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(metadata_service, "get_client_for_source", lambda source: object())

    def fake_get_album_for_source(source, album_id):
        calls.append(("album", source, album_id))
        return _album("album-1", "Album One") if source == "deezer" and album_id == "album-1" else None

    def fake_get_album_tracks_for_source(source, album_id):
        calls.append(("tracks", source, album_id))
        return {"items": [_track()]} if source == "deezer" and album_id == "album-1" else None

    monkeypatch.setattr(metadata_service, "get_album_for_source", fake_get_album_for_source)
    monkeypatch.setattr(metadata_service, "get_album_tracks_for_source", fake_get_album_tracks_for_source)

    result = metadata_service.get_artist_album_tracks(
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

    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(metadata_service, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(metadata_service, "get_client_for_source", lambda source: object())

    def fake_get_album_for_source(source, album_id):
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

    monkeypatch.setattr(metadata_service, "get_album_for_source", fake_get_album_for_source)
    monkeypatch.setattr(metadata_service, "get_album_tracks_for_source", fake_get_album_tracks_for_source)
    monkeypatch.setattr(metadata_service, "resolve_album_reference", fake_resolve_album_reference)

    result = metadata_service.get_artist_album_tracks(
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
    cursor.execute("CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT)")
    cursor.execute(
        """
        CREATE TABLE albums (
            id INTEGER PRIMARY KEY,
            title TEXT,
            artist_id INTEGER,
            spotify_album_id TEXT,
            itunes_album_id TEXT,
            deezer_id TEXT,
            deezer_album_id TEXT,
            discogs_id TEXT,
            soul_id TEXT,
            hydrabase_album_id TEXT
        )
        """
    )
    cursor.execute("INSERT INTO artists (id, name) VALUES (1, 'Artist One')")
    cursor.execute(
        """
        INSERT INTO albums (id, title, artist_id, deezer_id)
        VALUES (1, 'Album One', 1, 'deezer-abc')
        """
    )
    conn.commit()

    class _FakeDatabase:
        def _get_connection(self):
            return conn

    monkeypatch.setattr("database.music_database.get_database", lambda: _FakeDatabase())
    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(metadata_service, "get_source_priority", lambda primary: [primary, "spotify"])

    resolved_id, resolved_source = metadata_service.resolve_album_reference("1", preferred_source="deezer")

    assert resolved_id == "deezer-abc"
    assert resolved_source == "deezer"


def test_resolve_album_reference_searches_by_name_when_no_external_id_exists(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT)")
    cursor.execute(
        """
        CREATE TABLE albums (
            id INTEGER PRIMARY KEY,
            title TEXT,
            artist_id INTEGER,
            spotify_album_id TEXT,
            itunes_album_id TEXT,
            deezer_id TEXT,
            deezer_album_id TEXT,
            discogs_id TEXT,
            soul_id TEXT,
            hydrabase_album_id TEXT
        )
        """
    )
    cursor.execute("INSERT INTO artists (id, name) VALUES (1, 'Artist One')")
    cursor.execute("INSERT INTO albums (id, title, artist_id) VALUES (1, 'Album One', 1)")
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
    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(metadata_service, "get_source_priority", lambda primary: [primary, "spotify"])
    monkeypatch.setattr(metadata_service, "get_client_for_source", lambda source: fake_client if source == "deezer" else None)

    resolved_id, resolved_source = metadata_service.resolve_album_reference("1", preferred_source="deezer")

    assert resolved_id == "searched-123"
    assert resolved_source == "deezer"
    assert fake_client.calls == [("Artist One Album One", {"limit": 5})]
