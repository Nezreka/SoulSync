import sys
import types


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
            return "plex"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

if "core.matching_engine" not in sys.modules:
    matching_engine_mod = types.ModuleType("core.matching_engine")

    class _DummyMatchingEngine:
        def clean_title(self, title):
            return title

    matching_engine_mod.MusicMatchingEngine = _DummyMatchingEngine
    sys.modules["core.matching_engine"] = matching_engine_mod

import core.watchlist_scanner as watchlist_scanner_module
from core.watchlist_scanner import WatchlistScanner


class _FakeSpotifyClient:
    def __init__(self, search_results=None):
        self.search_results = list(search_results or [])
        self.search_calls = []

    def is_spotify_authenticated(self):
        return False

    def search_artists(self, query, limit=1, allow_fallback=True):
        self.search_calls.append((query, limit, allow_fallback))
        return list(self.search_results) if allow_fallback else []


class _FakeMetadataService:
    def __init__(self, album_data, spotify_client=None):
        self.spotify = spotify_client or _FakeSpotifyClient()
        self.itunes = types.SimpleNamespace()
        self._album_data = album_data

    def get_album(self, album_id):
        return self._album_data


class _FakeSourceClient:
    def __init__(self, *, artist_id: str, albums, image_url: str, album_payload=None, album_search_results=None):
        self.artist_id = artist_id
        self.albums = list(albums)
        self.image_url = image_url
        self.album_payload = album_payload
        self.album_search_results = list(album_search_results or [])
        self.search_calls = []
        self.search_album_calls = []
        self.album_calls = []
        self.artist_calls = []

    def search_artists(self, query, limit=1, **kwargs):
        self.search_calls.append((query, limit, kwargs))
        return [types.SimpleNamespace(id=self.artist_id, name=query)]

    def search_albums(self, query, limit=1, **kwargs):
        self.search_album_calls.append((query, limit, kwargs))
        return list(self.album_search_results)

    def get_artist_albums(self, artist_id, album_type='album,single', limit=50, **kwargs):
        self.album_calls.append((artist_id, album_type, limit, kwargs))
        return list(self.albums)

    def get_artist(self, artist_id, **kwargs):
        self.artist_calls.append(artist_id)
        return {
            "id": artist_id,
            "images": [{"url": self.image_url}] if self.image_url else [],
        }

    def get_album(self, album_id, **kwargs):
        self.album_calls.append((album_id, kwargs))
        if self.album_payload is not None:
            return self.album_payload
        return {
            "id": album_id,
            "name": "Album One",
            "images": [{"url": self.image_url}] if self.image_url else [],
            "tracks": {"items": []},
            "artists": [{"id": self.artist_id}],
        }


class _FakeDB:
    def __init__(self, artists):
        self.artists = artists
        self.similar_calls = []
        self.discovery_pool_calls = []
        self.discovery_pool_timestamp_calls = []
        self.db_albums = []

    def get_watchlist_artists(self, profile_id=None):
        return list(self.artists)

    def has_fresh_similar_artists(self, *args, **kwargs):
        self.similar_calls.append((args, kwargs))
        return False

    def should_populate_discovery_pool(self, hours_threshold=24, profile_id=1):
        return True

    def get_top_similar_artists(self, limit=50, profile_id=1):
        return []

    def add_to_discovery_pool(self, track_data, source, profile_id=1):
        self.discovery_pool_calls.append((track_data, source, profile_id))
        return True

    def cleanup_old_discovery_tracks(self, days_threshold=365):
        return 0

    def update_discovery_pool_timestamp(self, track_count, profile_id=1):
        self.discovery_pool_timestamp_calls.append((track_count, profile_id))
        return True

    class _Cursor:
        def __init__(self, parent):
            self.parent = parent

        def execute(self, *args, **kwargs):
            return None

        def fetchall(self):
            return list(self.parent.db_albums)

        def fetchone(self):
            return {"count": 0}

    class _Conn:
        def __init__(self, cursor):
            self._cursor = cursor

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return self._cursor

    def _get_connection(self):
        return self._Conn(self._Cursor(self))


def _build_artist(name="Artist One", profile_id=11):
    return types.SimpleNamespace(
        artist_name=name,
        spotify_artist_id="sp-artist",
        itunes_artist_id="it-artist",
        deezer_artist_id="dz-artist",
        discogs_artist_id="dg-artist",
        last_scan_timestamp=None,
        id=123,
        profile_id=profile_id,
        include_albums=True,
        include_eps=True,
        include_singles=True,
        include_live=False,
        include_remixes=False,
        include_acoustic=False,
        include_compilations=False,
        include_instrumentals=False,
        lookback_days=7,
        image_url=None,
    )


def _build_scanner(album_data, artists):
    scanner = WatchlistScanner(metadata_service=_FakeMetadataService(album_data))
    scanner._database = _FakeDB(artists)
    scanner._wishlist_service = types.SimpleNamespace()
    scanner._matching_engine = types.SimpleNamespace()
    return scanner


def test_scan_watchlist_profile_loads_artists_and_applies_overrides(monkeypatch):
    artist = _build_artist()
    scanner = _build_scanner({"tracks": {"items": []}}, [artist])

    loaded_profiles = []
    override_calls = []
    scan_calls = []

    monkeypatch.setattr(scanner.database, "get_watchlist_artists", lambda profile_id=None: loaded_profiles.append(profile_id) or [artist])
    monkeypatch.setattr(scanner, "_apply_global_watchlist_overrides", lambda artists: override_calls.append(list(artists)))
    monkeypatch.setattr(scanner, "scan_watchlist_artists", lambda artists, **kwargs: scan_calls.append((list(artists), kwargs)) or ["ok"])

    result = scanner.scan_watchlist_profile(42)

    assert result == ["ok"]
    assert loaded_profiles == [42]
    assert override_calls and override_calls[0][0].artist_name == "Artist One"
    assert scan_calls and scan_calls[0][0][0].artist_name == "Artist One"
    assert scan_calls[0][1]["profile_id"] == 42


def test_scan_watchlist_artists_scans_tracks_and_updates_state(monkeypatch):
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ARTISTS", 0)
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ALBUMS", 0)

    artist = _build_artist()
    album = types.SimpleNamespace(id="album-1", name="Album One")
    album_data = {
        "name": "Album One",
        "images": [{"url": "https://example.com/album.jpg"}],
        "tracks": {
            "items": [
                {
                    "id": "track-1",
                    "name": "Track One",
                    "track_number": 1,
                    "disc_number": 1,
                    "artists": [{"name": "Artist One"}],
                }
            ]
        },
    }
    scanner = _build_scanner(album_data, [artist])
    scanner._database.has_fresh_similar_artists = lambda *args, **kwargs: False

    monkeypatch.setattr(scanner, "_backfill_missing_ids", lambda *args, **kwargs: None)
    monkeypatch.setattr(scanner, "get_artist_image_url", lambda *_args, **_kwargs: "https://example.com/artist.jpg")
    monkeypatch.setattr(scanner, "get_artist_discography_for_watchlist", lambda *_args, **_kwargs: [album])
    monkeypatch.setattr(scanner, "_get_lookback_period_setting", lambda: "30")
    monkeypatch.setattr(scanner, "_get_rescan_cutoff", lambda: None)
    monkeypatch.setattr(scanner, "_should_include_release", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "_should_include_track", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "is_track_missing_from_library", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "add_track_to_wishlist", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "update_artist_scan_timestamp", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "update_similar_artists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "_backfill_similar_artists_itunes_ids", lambda *_args, **_kwargs: 0)

    scan_state = {}
    results = scanner.scan_watchlist_artists([artist], scan_state=scan_state)

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].new_tracks_found == 1
    assert results[0].tracks_added_to_wishlist == 1
    assert scan_state["status"] == "completed"
    assert scan_state["summary"]["successful_scans"] == 1
    assert scan_state["summary"]["new_tracks_found"] == 1
    assert scan_state["summary"]["tracks_added_to_wishlist"] == 1
    assert scan_state["recent_wishlist_additions"][0]["track_name"] == "Track One"


def test_scan_watchlist_artists_skips_placeholder_tracklists(monkeypatch):
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ARTISTS", 0)
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ALBUMS", 0)

    artist = _build_artist()
    album = types.SimpleNamespace(id="album-1", name="Album One")
    album_data = {
        "name": "Album One",
        "images": [{"url": "https://example.com/album.jpg"}],
        "tracks": {
            "items": [
                {
                    "id": "track-1",
                    "name": "Track 1",
                    "track_number": 1,
                    "disc_number": 1,
                    "artists": [{"name": "Artist One"}],
                },
                {
                    "id": "track-2",
                    "name": "Track 2",
                    "track_number": 2,
                    "disc_number": 1,
                    "artists": [{"name": "Artist One"}],
                },
            ]
        },
    }
    scanner = _build_scanner(album_data, [artist])
    scanner._database.has_fresh_similar_artists = lambda *args, **kwargs: False

    monkeypatch.setattr(scanner, "_backfill_missing_ids", lambda *args, **kwargs: None)
    monkeypatch.setattr(scanner, "get_artist_image_url", lambda *_args, **_kwargs: "https://example.com/artist.jpg")
    monkeypatch.setattr(scanner, "get_artist_discography_for_watchlist", lambda *_args, **_kwargs: [album])
    monkeypatch.setattr(scanner, "_get_lookback_period_setting", lambda: "30")
    monkeypatch.setattr(scanner, "_get_rescan_cutoff", lambda: None)
    monkeypatch.setattr(scanner, "_should_include_release", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "_should_include_track", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "is_track_missing_from_library", lambda *_args, **_kwargs: True)

    add_calls = []
    monkeypatch.setattr(scanner, "add_track_to_wishlist", lambda *args, **kwargs: add_calls.append((args, kwargs)) or True)
    monkeypatch.setattr(scanner, "update_artist_scan_timestamp", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "update_similar_artists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "_backfill_similar_artists_itunes_ids", lambda *_args, **_kwargs: 0)

    scan_state = {}
    results = scanner.scan_watchlist_artists([artist], scan_state=scan_state)

    assert len(results) == 1
    assert results[0].success is True
    assert results[0].new_tracks_found == 0
    assert results[0].tracks_added_to_wishlist == 0
    assert add_calls == []
    assert scan_state["summary"]["new_tracks_found"] == 0
    assert scan_state["summary"]["tracks_added_to_wishlist"] == 0


def test_scan_watchlist_artists_honors_cancel_check(monkeypatch):
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ARTISTS", 0)
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ALBUMS", 0)

    artist_a = _build_artist("Artist One")
    artist_b = _build_artist("Artist Two")
    album = types.SimpleNamespace(id="album-1", name="Album One")
    album_data = {
        "name": "Album One",
        "tracks": {"items": []},
    }
    scanner = _build_scanner(album_data, [artist_a, artist_b])
    scanner._database.has_fresh_similar_artists = lambda *args, **kwargs: False

    monkeypatch.setattr(scanner, "_backfill_missing_ids", lambda *args, **kwargs: None)
    monkeypatch.setattr(scanner, "get_artist_image_url", lambda *_args, **_kwargs: "https://example.com/artist.jpg")
    monkeypatch.setattr(scanner, "get_artist_discography_for_watchlist", lambda *_args, **_kwargs: [album])
    monkeypatch.setattr(scanner, "_get_lookback_period_setting", lambda: "30")
    monkeypatch.setattr(scanner, "_get_rescan_cutoff", lambda: None)
    monkeypatch.setattr(scanner, "_should_include_release", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "_should_include_track", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "is_track_missing_from_library", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(scanner, "add_track_to_wishlist", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(scanner, "update_artist_scan_timestamp", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "update_similar_artists", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(scanner, "_backfill_similar_artists_itunes_ids", lambda *_args, **_kwargs: 0)

    cancels = iter([False, True])
    scan_state = {}
    results = scanner.scan_watchlist_artists(
        [artist_a, artist_b],
        scan_state=scan_state,
        cancel_check=lambda: next(cancels),
    )

    assert len(results) == 1
    assert results[0].artist_name == "Artist One"
    assert scan_state["status"] == "cancelled"
    assert scan_state["summary"]["cancelled"] is True
    assert scan_state["summary"]["successful_scans"] == 1


def test_get_artist_discography_for_watchlist_prefers_primary_source(monkeypatch):
    monkeypatch.setattr(watchlist_scanner_module, "time", types.SimpleNamespace(sleep=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(watchlist_scanner_module, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(watchlist_scanner_module, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])

    deezer_album = types.SimpleNamespace(id="dz-album", name="Deezer Album", release_date=None)
    spotify_album = types.SimpleNamespace(id="sp-album", name="Spotify Album", release_date=None)

    deezer_client = _FakeSourceClient(artist_id="dz-artist", albums=[deezer_album], image_url="https://example.com/deezer.jpg")
    spotify_client = _FakeSourceClient(artist_id="sp-artist", albums=[spotify_album], image_url="https://example.com/spotify.jpg")

    def fake_get_client_for_source(source):
        return {"deezer": deezer_client, "spotify": spotify_client}.get(source)

    monkeypatch.setattr(watchlist_scanner_module, "get_client_for_source", fake_get_client_for_source)

    artist = _build_artist()
    artist.spotify_artist_id = "sp-artist"
    artist.deezer_artist_id = "dz-artist"

    scanner = _build_scanner({"tracks": {"items": []}}, [artist])
    scanner._database.has_fresh_similar_artists = lambda *args, **kwargs: False
    scanner._get_lookback_period_setting = lambda: "30"
    scanner._get_rescan_cutoff = lambda: None

    result = scanner.get_artist_discography_for_watchlist(artist, None)

    assert result is not None
    assert result.source == "deezer"
    assert result.artist_id == "dz-artist"
    assert result.albums and result.albums[0].id == "dz-album"
    assert deezer_client.album_calls
    assert spotify_client.album_calls == []


def test_get_artist_discography_for_watchlist_falls_back_when_primary_empty(monkeypatch):
    monkeypatch.setattr(watchlist_scanner_module, "time", types.SimpleNamespace(sleep=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(watchlist_scanner_module, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(watchlist_scanner_module, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])

    deezer_client = _FakeSourceClient(artist_id="dz-artist", albums=[], image_url="https://example.com/deezer.jpg")
    spotify_album = types.SimpleNamespace(id="sp-album", name="Spotify Album", release_date=None)
    spotify_client = _FakeSourceClient(artist_id="sp-artist", albums=[spotify_album], image_url="https://example.com/spotify.jpg")

    def fake_get_client_for_source(source):
        return {"deezer": deezer_client, "spotify": spotify_client}.get(source)

    monkeypatch.setattr(watchlist_scanner_module, "get_client_for_source", fake_get_client_for_source)

    artist = _build_artist()
    artist.spotify_artist_id = "sp-artist"
    artist.deezer_artist_id = "dz-artist"

    scanner = _build_scanner({"tracks": {"items": []}}, [artist])
    scanner._database.has_fresh_similar_artists = lambda *args, **kwargs: False
    scanner._get_lookback_period_setting = lambda: "30"
    scanner._get_rescan_cutoff = lambda: None

    result = scanner.get_artist_discography_for_watchlist(artist, None)

    assert result is not None
    assert result.source == "spotify"
    assert result.artist_id == "sp-artist"
    assert result.albums and result.albums[0].id == "sp-album"
    assert deezer_client.album_calls
    assert spotify_client.album_calls


def test_populate_discovery_pool_uses_primary_source_first(monkeypatch):
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ARTISTS", 0)
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ALBUMS", 0)
    monkeypatch.setattr(watchlist_scanner_module, "time", types.SimpleNamespace(sleep=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(watchlist_scanner_module, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(watchlist_scanner_module, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])

    similar_artist = types.SimpleNamespace(
        id=501,
        similar_artist_name="Similar Artist",
        occurrence_count=3,
        similar_artist_spotify_id="sp-artist",
        similar_artist_itunes_id="it-artist",
        similar_artist_deezer_id="dz-artist",
    )

    album = types.SimpleNamespace(id="dz-album-1", name="Deezer Album", album_type="album")
    deezer_album_payload = {
        "id": "dz-album-1",
        "name": "Deezer Album",
        "images": [{"url": "https://example.com/deezer-album.jpg"}],
        "release_date": "2026-04-01",
        "popularity": 0,
        "tracks": {
            "items": [
                {
                    "id": "dz-track-1",
                    "name": "Deezer Track",
                    "duration_ms": 123456,
                    "artists": [{"name": "Similar Artist"}],
                }
            ]
        },
        "artists": [{"id": "dz-artist"}],
    }

    deezer_client = _FakeSourceClient(
        artist_id="dz-artist",
        albums=[album],
        image_url="https://example.com/deezer-artist.jpg",
        album_payload=deezer_album_payload,
    )
    spotify_client = _FakeSourceClient(
        artist_id="sp-artist",
        albums=[types.SimpleNamespace(id="sp-album-1", name="Spotify Album", album_type="album")],
        image_url="https://example.com/spotify-artist.jpg",
        album_payload={
            "id": "sp-album-1",
            "name": "Spotify Album",
            "images": [{"url": "https://example.com/spotify-album.jpg"}],
            "release_date": "2026-04-01",
            "popularity": 50,
            "tracks": {"items": []},
            "artists": [{"id": "sp-artist"}],
        },
    )

    def fake_get_client_for_source(source):
        return {
            "deezer": deezer_client,
            "spotify": spotify_client,
        }.get(source)

    monkeypatch.setattr(watchlist_scanner_module, "get_client_for_source", fake_get_client_for_source)

    scanner = _build_scanner({"tracks": {"items": []}}, [])
    scanner._database.has_fresh_similar_artists = lambda *args, **kwargs: False
    scanner.database.should_populate_discovery_pool = lambda hours_threshold=24, profile_id=1: True
    scanner.database.get_top_similar_artists = lambda limit=50, profile_id=1: [similar_artist]
    scanner.database.db_albums = []
    scanner.cache_discovery_recent_albums = lambda *args, **kwargs: None
    scanner.curate_discovery_playlists = lambda *args, **kwargs: None
    scanner.database.update_discovery_pool_timestamp = lambda *args, **kwargs: True
    scanner.database.cleanup_old_discovery_tracks = lambda *args, **kwargs: 0

    scanner.populate_discovery_pool(top_artists_limit=1, albums_per_artist=1, profile_id=1)

    assert scanner.database.discovery_pool_calls
    assert scanner.database.discovery_pool_calls[0][1] == "deezer"
    assert deezer_client.album_calls
    assert spotify_client.search_calls == []
    assert spotify_client.artist_calls == []


def test_populate_discovery_pool_falls_back_to_spotify_when_primary_has_no_albums(monkeypatch):
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ARTISTS", 0)
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ALBUMS", 0)
    monkeypatch.setattr(watchlist_scanner_module, "time", types.SimpleNamespace(sleep=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(watchlist_scanner_module, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(watchlist_scanner_module, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])

    similar_artist = types.SimpleNamespace(
        id=502,
        similar_artist_name="Fallback Artist",
        occurrence_count=1,
        similar_artist_spotify_id="sp-artist",
        similar_artist_itunes_id="it-artist",
        similar_artist_deezer_id="dz-artist",
    )

    deezer_client = _FakeSourceClient(
        artist_id="dz-artist",
        albums=[],
        image_url="https://example.com/deezer-artist.jpg",
    )
    spotify_album = types.SimpleNamespace(id="sp-album-1", name="Spotify Album", album_type="album")
    spotify_client = _FakeSourceClient(
        artist_id="sp-artist",
        albums=[spotify_album],
        image_url="https://example.com/spotify-artist.jpg",
        album_payload={
            "id": "sp-album-1",
            "name": "Spotify Album",
            "images": [{"url": "https://example.com/spotify-album.jpg"}],
            "release_date": "2026-04-01",
            "popularity": 50,
            "tracks": {
                "items": [
                    {
                        "id": "sp-track-1",
                        "name": "Spotify Track",
                        "duration_ms": 234567,
                        "artists": [{"name": "Fallback Artist"}],
                    }
                ]
            },
            "artists": [{"id": "sp-artist"}],
        },
    )

    def fake_get_client_for_source(source):
        return {
            "deezer": deezer_client,
            "spotify": spotify_client,
        }.get(source)

    monkeypatch.setattr(watchlist_scanner_module, "get_client_for_source", fake_get_client_for_source)

    scanner = _build_scanner({"tracks": {"items": []}}, [])
    scanner._database.has_fresh_similar_artists = lambda *args, **kwargs: False
    scanner.database.should_populate_discovery_pool = lambda hours_threshold=24, profile_id=1: True
    scanner.database.get_top_similar_artists = lambda limit=50, profile_id=1: [similar_artist]
    scanner.database.db_albums = []
    scanner.cache_discovery_recent_albums = lambda *args, **kwargs: None
    scanner.curate_discovery_playlists = lambda *args, **kwargs: None
    scanner.database.update_discovery_pool_timestamp = lambda *args, **kwargs: True
    scanner.database.cleanup_old_discovery_tracks = lambda *args, **kwargs: 0

    scanner.populate_discovery_pool(top_artists_limit=1, albums_per_artist=1, profile_id=1)

    assert scanner.database.discovery_pool_calls
    assert scanner.database.discovery_pool_calls[0][1] == "spotify"
    assert deezer_client.album_calls
    assert spotify_client.search_calls == [("Fallback Artist", 1, {"allow_fallback": False})]
    assert spotify_client.album_calls
    assert any(
        isinstance(call, tuple)
        and len(call) == 4
        and call[0] == "sp-artist"
        and call[3].get("skip_cache") is False
        and call[3].get("allow_fallback") is False
        and call[3].get("max_pages") == 2
        for call in spotify_client.album_calls
    )
    assert any(
        isinstance(call, tuple)
        and len(call) == 4
        and call[3].get("allow_fallback") is False
        for call in spotify_client.album_calls
    )


def test_populate_discovery_pool_uses_strict_spotify_for_database_album_search(monkeypatch):
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ARTISTS", 0)
    monkeypatch.setattr(watchlist_scanner_module, "DELAY_BETWEEN_ALBUMS", 0)
    monkeypatch.setattr(watchlist_scanner_module, "time", types.SimpleNamespace(sleep=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(watchlist_scanner_module, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(watchlist_scanner_module, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])

    similar_artist = types.SimpleNamespace(
        id=503,
        similar_artist_name="No Album Artist",
        occurrence_count=1,
        similar_artist_spotify_id="sp-artist",
        similar_artist_itunes_id="it-artist",
        similar_artist_deezer_id="dz-artist",
    )

    deezer_client = _FakeSourceClient(
        artist_id="dz-artist",
        albums=[],
        image_url="https://example.com/deezer-artist.jpg",
    )
    spotify_client = _FakeSourceClient(
        artist_id="sp-artist",
        albums=[],
        image_url="https://example.com/spotify-artist.jpg",
        album_search_results=[types.SimpleNamespace(id="sp-db-album", name="DB Album")],
        album_payload={
            "id": "sp-db-album",
            "name": "DB Album",
            "images": [{"url": "https://example.com/db-album.jpg"}],
            "release_date": "2026-04-01",
            "popularity": 75,
            "tracks": {
                "items": [
                    {
                        "id": "sp-db-track-1",
                        "name": "DB Track",
                        "duration_ms": 345678,
                        "artists": [{"name": "DB Artist"}],
                    }
                ]
            },
            "artists": [{"id": "sp-artist"}],
        },
    )

    def fake_get_client_for_source(source):
        return {
            "deezer": deezer_client,
            "spotify": spotify_client,
        }.get(source)

    monkeypatch.setattr(watchlist_scanner_module, "get_client_for_source", fake_get_client_for_source)

    scanner = _build_scanner({"tracks": {"items": []}}, [])
    scanner._database.has_fresh_similar_artists = lambda *args, **kwargs: False
    scanner.database.should_populate_discovery_pool = lambda hours_threshold=24, profile_id=1: True
    scanner.database.get_top_similar_artists = lambda limit=50, profile_id=1: [similar_artist]
    scanner.database.db_albums = [{"title": "DB Album", "artist_name": "DB Artist"}]
    scanner.cache_discovery_recent_albums = lambda *args, **kwargs: None
    scanner.curate_discovery_playlists = lambda *args, **kwargs: None
    scanner.database.update_discovery_pool_timestamp = lambda *args, **kwargs: True
    scanner.database.cleanup_old_discovery_tracks = lambda *args, **kwargs: 0

    scanner.populate_discovery_pool(top_artists_limit=1, albums_per_artist=1, profile_id=1)

    assert scanner.database.discovery_pool_calls
    assert scanner.database.discovery_pool_calls[0][1] == "spotify"
    assert spotify_client.search_album_calls
    assert any(
        kwargs.get("allow_fallback") is False
        for _, _, kwargs in spotify_client.search_album_calls
    )
    assert any(
        isinstance(call, tuple)
        and len(call) == 4
        and call[0] == "sp-artist"
        and call[3].get("skip_cache") is False
        and call[3].get("allow_fallback") is False
        and call[3].get("max_pages") == 2
        for call in spotify_client.album_calls
    )
    assert any(
        isinstance(call, tuple)
        and len(call) == 2
        and call[1].get("allow_fallback") is False
        for call in spotify_client.album_calls
        if len(call) == 2
    )


def test_match_to_spotify_uses_strict_lookup():
    spotify_client = _FakeSpotifyClient(
        search_results=[types.SimpleNamespace(id="fallback-id", name="Artist One")]
    )
    scanner = WatchlistScanner(metadata_service=_FakeMetadataService(None, spotify_client=spotify_client))
    original_get_client_for_source = watchlist_scanner_module.get_client_for_source
    watchlist_scanner_module.get_client_for_source = lambda source: spotify_client if source == "spotify" else None

    try:
        result = scanner._match_to_spotify("Artist One")
    finally:
        watchlist_scanner_module.get_client_for_source = original_get_client_for_source

    assert result is None
    assert spotify_client.search_calls == [("Artist One", 5, False)]
