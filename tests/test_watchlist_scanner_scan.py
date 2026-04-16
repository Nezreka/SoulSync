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

import core.watchlist_scanner as watchlist_scanner_module
from core.watchlist_scanner import WatchlistScanner


class _FakeSpotifyClient:
    def is_spotify_authenticated(self):
        return False


class _FakeMetadataService:
    def __init__(self, album_data):
        self.spotify = _FakeSpotifyClient()
        self.itunes = types.SimpleNamespace()
        self._album_data = album_data

    def get_album(self, album_id):
        return self._album_data


class _FakeDB:
    def __init__(self, artists):
        self.artists = artists
        self.similar_calls = []

    def get_watchlist_artists(self, profile_id=None):
        return list(self.artists)

    def has_fresh_similar_artists(self, *args, **kwargs):
        self.similar_calls.append((args, kwargs))
        return False


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
