import sys
import types
import sqlite3

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

from core.metadata import registry as metadata_registry
from core.metadata import completion as metadata_completion
from core.metadata import discography as metadata_discography
from core.metadata.lookup import MetadataLookupOptions
from database.music_database import MusicDatabase


@pytest.fixture(autouse=True)
def _clear_metadata_client_cache():
    metadata_registry.clear_cached_metadata_clients()
    yield
    metadata_registry.clear_cached_metadata_clients()


class _FakeSourceClient:
    def __init__(self, album_results=None, artist_search_results=None, discography_results=None):
        self.album_results = list(album_results or [])
        self.artist_search_results = list(artist_search_results or [])
        self.discography_results = list(discography_results or [])
        self.album_calls = []
        self.artist_search_calls = []
        self.discography_calls = []
        self.track_search_calls = []

    def get_artist_albums(self, artist_id, **kwargs):
        self.album_calls.append((artist_id, dict(kwargs)))
        return list(self.album_results)

    def search_artists(self, query, **kwargs):
        self.artist_search_calls.append((query, dict(kwargs)))
        return list(self.artist_search_results)

    def search_discography(self, query, **kwargs):
        self.discography_calls.append((query, dict(kwargs)))
        return list(self.discography_results)

    def search_tracks(self, query, **kwargs):
        self.track_search_calls.append((query, dict(kwargs)))
        return []

    def get_album_tracks(self, album_id, **kwargs):
        self.album_calls.append((album_id, dict(kwargs)))
        return {"items": list(self.album_results)}


def _album(album_id, name, release_date, album_type="album"):
    return types.SimpleNamespace(
        id=album_id,
        name=name,
        release_date=release_date,
        album_type=album_type,
        image_url=f"https://img.example/{album_id}.jpg",
        total_tracks=12,
        external_urls={"spotify": f"https://example/{album_id}"},
        artist_ids=["artist-1"],
    )


def _artist(artist_id, name):
    return types.SimpleNamespace(id=artist_id, name=name)


def test_get_artist_discography_uses_primary_then_fallback(monkeypatch):
    deezer = _FakeSourceClient()
    spotify = _FakeSourceClient(
        album_results=[
            _album("album-old", "Older Album", "2022-01-01"),
            _album("single-one", "Single One", "2024-06-01", album_type="single"),
            _album("album-new", "New Album", "2024-08-01"),
        ]
    )
    itunes = _FakeSourceClient()
    clients = {
        "deezer": deezer,
        "spotify": spotify,
        "itunes": itunes,
    }

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: clients.get(source))

    result = metadata_discography.get_artist_discography("artist-1", "Artist One", MetadataLookupOptions())

    assert result["source"] == "spotify"
    assert result["source_priority"] == ["deezer", "spotify", "itunes"]
    assert [album["id"] for album in result["albums"]] == ["album-new", "album-old"]
    assert [single["id"] for single in result["singles"]] == ["single-one"]
    assert spotify.album_calls == [(
        "artist-1",
        {
            "album_type": "album,single",
            "limit": 50,
            "allow_fallback": False,
            "skip_cache": False,
            "max_pages": 0,
        },
    )]


def test_get_artist_discography_uses_name_search_when_direct_lookup_missing(monkeypatch):
    class _SearchThenAlbumClient(_FakeSourceClient):
        def get_artist_albums(self, artist_id, **kwargs):
            self.album_calls.append((artist_id, dict(kwargs)))
            if artist_id == "deezer-artist-1":
                return [_album("deezer-album-1", "Deezer Album", "2023-05-01")]
            return []

    deezer = _SearchThenAlbumClient(
        artist_search_results=[_artist("deezer-artist-1", "Artist One")],
    )
    clients = {"deezer": deezer}

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: clients.get(source))

    result = metadata_discography.get_artist_discography("artist-1", "Artist One", MetadataLookupOptions())

    assert result["source"] == "deezer"
    assert [album["id"] for album in result["albums"]] == ["deezer-album-1"]
    assert deezer.artist_search_calls == [("Artist One", {"limit": 5})]
    assert deezer.album_calls == [
        (
            "artist-1",
            {
                "album_type": "album,single",
                "limit": 50,
            },
        ),
        (
            "deezer-artist-1",
            {
                "album_type": "album,single",
                "limit": 50,
            },
        ),
    ]


def test_get_artist_discography_respects_source_override_without_fallback(monkeypatch):
    deezer = _FakeSourceClient()
    itunes = _FakeSourceClient(album_results=[_album("itunes-album-1", "iTunes Album", "2024-02-01")])
    spotify = _FakeSourceClient()
    clients = {
        "deezer": deezer,
        "itunes": itunes,
        "spotify": spotify,
    }

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: clients.get(source))

    result = metadata_discography.get_artist_discography(
        "artist-1",
        "Artist One",
        MetadataLookupOptions(source_override="itunes", allow_fallback=False),
    )

    assert result["source"] == "itunes"
    assert result["source_priority"] == ["itunes"]
    assert [album["id"] for album in result["albums"]] == ["itunes-album-1"]
    assert deezer.album_calls == []
    assert spotify.album_calls == []
    assert itunes.album_calls == [
        (
            "artist-1",
            {
                "album_type": "album,single",
                "limit": 50,
            },
        )
    ]


def test_get_artist_discography_uses_hydrabase_fast_path_when_active(monkeypatch):
    class _HydrabaseLikeClient(_FakeSourceClient):
        def get_artist_albums(self, artist_id, **kwargs):
            self.album_calls.append((artist_id, dict(kwargs)))
            if artist_id == "hydrabase-artist-1":
                return [
                    _album("hydrabase-album-1", "Hydra Album", "2024-03-01"),
                    _album("hydrabase-single-1", "Hydra Single", "2024-04-01", album_type="single"),
                ]
            return []

    hydrabase = _HydrabaseLikeClient(
        artist_search_results=[_artist("hydrabase-artist-1", "Artist One")],
    )
    clients = {"deezer": None, "spotify": None, "itunes": None}

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes", "hydrabase"])
    def fake_get_client_for_source(source):
        if source == "hydrabase":
            return hydrabase
        return clients.get(source)

    monkeypatch.setattr(metadata_registry, "get_client_for_source", fake_get_client_for_source)

    result = metadata_discography.get_artist_discography("artist-1", "Artist One", MetadataLookupOptions())

    assert result["source"] == "hydrabase"
    assert [album["id"] for album in result["albums"]] == ["hydrabase-album-1"]
    assert [single["id"] for single in result["singles"]] == ["hydrabase-single-1"]
    assert hydrabase.album_calls == [
        (
            "artist-1",
            {
                "album_type": "album,single",
                "limit": 50,
            },
        ),
        (
            "hydrabase-artist-1",
            {
                "album_type": "album,single",
                "limit": 50,
            },
        )
    ]
    assert hydrabase.artist_search_calls == [("Artist One", {"limit": 5})]


class _CompletionFakeDB:
    def __init__(self, owned_tracks=1, expected_tracks=3, is_track=False):
        self.owned_tracks = owned_tracks
        self.expected_tracks = expected_tracks
        self.is_track = is_track
        self.album_calls = []
        self.track_calls = []

    def check_album_exists_with_completeness(self, **kwargs):
        self.album_calls.append(dict(kwargs))
        return (True, 0.9, self.owned_tracks, self.expected_tracks, self.owned_tracks >= self.expected_tracks, [])

    def check_track_exists(self, **kwargs):
        self.track_calls.append(dict(kwargs))
        if self.is_track:
            return (object(), 0.9)
        return (None, 0.0)


def test_iter_artist_discography_completion_uses_primary_source_first(monkeypatch):
    deezer = _FakeSourceClient()
    spotify = _FakeSourceClient()
    itunes = _FakeSourceClient()

    deezer.album_results = [{"id": "release-1-track-1"}, {"id": "release-1-track-2"}]
    spotify.album_results = [{"id": "release-1-track-1"}, {"id": "release-1-track-2"}, {"id": "release-1-track-3"}]
    itunes.album_results = [{"id": "release-1-track-1"}]

    clients = {
        "deezer": deezer,
        "spotify": spotify,
        "itunes": itunes,
    }

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: clients.get(source))

    db = _CompletionFakeDB(owned_tracks=1, expected_tracks=2)
    events = list(metadata_completion.iter_artist_discography_completion_events(
        {
            "albums": [{"id": "release-1", "name": "Album One", "total_tracks": 0}],
            "singles": [],
        },
        artist_name="Artist One",
        db=db,
    ))

    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "complete"
    assert events[1]["expected_tracks"] == 2
    assert events[1]["status"] == "partial"
    assert deezer.album_calls == [("release-1", {})]
    assert spotify.album_calls == []
    assert itunes.album_calls == []
    assert db.album_calls and db.album_calls[0]["expected_track_count"] == 2


def test_iter_artist_discography_completion_respects_source_override(monkeypatch):
    deezer = _FakeSourceClient()
    spotify = _FakeSourceClient()
    itunes = _FakeSourceClient()

    deezer.album_results = [{"id": "release-2-track-1"}]
    spotify.album_results = [{"id": "release-2-track-1"}, {"id": "release-2-track-2"}]
    itunes.album_results = [{"id": "release-2-track-1"}, {"id": "release-2-track-2"}, {"id": "release-2-track-3"}]

    clients = {
        "deezer": deezer,
        "spotify": spotify,
        "itunes": itunes,
    }

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: clients.get(source))

    db = _CompletionFakeDB(owned_tracks=1, expected_tracks=3)
    events = list(metadata_completion.iter_artist_discography_completion_events(
        {
            "albums": [{"id": "release-2", "name": "Album Two", "total_tracks": 0}],
            "singles": [],
        },
        artist_name="Artist Two",
        source_override="itunes",
        db=db,
    ))

    assert events[1]["expected_tracks"] == 3
    assert itunes.album_calls == [("release-2", {})]
    assert deezer.album_calls == []
    assert spotify.album_calls == []


def test_iter_artist_discography_completion_uses_release_artist_metadata(monkeypatch):
    source = _FakeSourceClient()
    clients = {"deezer": source}

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source_name, **kwargs: clients.get(source_name))

    db = _CompletionFakeDB(owned_tracks=1, expected_tracks=2)
    events = list(metadata_completion.iter_artist_discography_completion_events(
        {
            "albums": [{
                "id": "release-3",
                "name": "Album Three",
                "artist_name": "Explicit Artist",
                "total_tracks": 2,
            }],
            "singles": [],
        },
        artist_name="Unknown Artist",
        db=db,
    ))

    assert events[0]["artist_name"] == "Explicit Artist"
    assert events[1]["name"] == "Album Three"
    assert db.album_calls[0]["artist"] == "Explicit Artist"
    assert source.track_search_calls == []


def test_get_artist_detail_discography_classifies_release_types(monkeypatch):
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_discography",
        lambda artist_id, artist_name='', options=None: {
            "albums": [
                {
                    "id": "album-1",
                    "name": "Album One",
                    "album_type": "album",
                    "image_url": "https://img.example/album-1.jpg",
                    "release_date": "2024-01-05",
                    "total_tracks": 10,
                }
            ],
            "singles": [
                {
                    "id": "ep-1",
                    "name": "EP One",
                    "album_type": "ep",
                    "image_url": "https://img.example/ep-1.jpg",
                    "release_date": "2023-06-01",
                    "total_tracks": 6,
                },
                {
                    "id": "single-1",
                    "name": "Single One",
                    "album_type": "single",
                    "image_url": "https://img.example/single-1.jpg",
                    "release_date": "2022-03-10",
                    "total_tracks": 1,
                },
            ],
            "source": "deezer",
            "source_priority": ["deezer", "spotify"],
        },
    )

    result = metadata_discography.get_artist_detail_discography("artist-1", "Artist One", MetadataLookupOptions())

    assert result["success"] is True
    assert result["source"] == "deezer"
    assert result["source_priority"] == ["deezer", "spotify"]
    assert [album["id"] for album in result["albums"]] == ["album-1"]
    assert [ep["id"] for ep in result["eps"]] == ["ep-1"]
    assert [single["id"] for single in result["singles"]] == ["single-1"]
    assert result["albums"][0]["title"] == "Album One"
    assert result["albums"][0]["owned"] is None
    assert result["albums"][0]["track_completion"] == "checking"


def test_get_artist_detail_discography_dedups_variant_releases(monkeypatch):
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_discography",
        lambda artist_id, artist_name='', options=None: {
            "albums": [
                {
                    "id": "album-standard",
                    "name": "Variant Album",
                    "album_type": "album",
                    "image_url": "https://img.example/standard.jpg",
                    "release_date": "2024-01-05",
                    "total_tracks": 10,
                },
                {
                    "id": "album-swedish",
                    "name": "Variant Album (Swedish Edition)",
                    "album_type": "album",
                    "image_url": "https://img.example/swedish.jpg",
                    "release_date": "2024-01-05",
                    "total_tracks": 12,
                },
                {
                    "id": "album-remaster",
                    "name": "Variant Album (2023 Abbey Road Remaster)",
                    "album_type": "album",
                    "image_url": "https://img.example/remaster.jpg",
                    "release_date": "2024-01-05",
                    "total_tracks": 10,
                },
            ],
            "singles": [],
            "source": "deezer",
            "source_priority": ["deezer", "spotify"],
        },
    )

    result = metadata_discography.get_artist_detail_discography("artist-1", "Artist One", MetadataLookupOptions())

    assert result["success"] is True
    assert [album["id"] for album in result["albums"]] == ["album-standard"]
    assert result["albums"][0]["title"] == "Variant Album"
    assert result["albums"][0]["track_count"] == 10


def test_get_artist_detail_discography_keeps_variants_when_dedup_disabled(monkeypatch):
    """MetadataLookupOptions.dedup_variants=False is the source-only artist
    detail code path — used so the standalone /artist-detail page can show
    every release the source returns (matching the retired inline Artists
    page behaviour)."""
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_discography",
        lambda artist_id, artist_name='', options=None: {
            "albums": [
                {
                    "id": "album-standard",
                    "name": "Variant Album",
                    "album_type": "album",
                    "image_url": "https://img.example/standard.jpg",
                    "release_date": "2024-01-05",
                    "total_tracks": 10,
                },
                {
                    "id": "album-swedish",
                    "name": "Variant Album (Swedish Edition)",
                    "album_type": "album",
                    "image_url": "https://img.example/swedish.jpg",
                    "release_date": "2024-01-05",
                    "total_tracks": 12,
                },
                {
                    "id": "album-remaster",
                    "name": "Variant Album (2023 Abbey Road Remaster)",
                    "album_type": "album",
                    "image_url": "https://img.example/remaster.jpg",
                    "release_date": "2024-01-05",
                    "total_tracks": 10,
                },
            ],
            "singles": [],
            "source": "deezer",
            "source_priority": ["deezer", "spotify"],
        },
    )

    result = metadata_discography.get_artist_detail_discography(
        "artist-1",
        "Artist One",
        MetadataLookupOptions(dedup_variants=False),
    )

    assert result["success"] is True
    assert [album["id"] for album in result["albums"]] == [
        "album-standard",
        "album-swedish",
        "album-remaster",
    ]


def test_get_artist_discography_keeps_provider_artist_ids(monkeypatch):
    class _SpotifyArtistIdClient(_FakeSourceClient):
        def get_artist_albums(self, artist_id, **kwargs):
            self.album_calls.append((artist_id, dict(kwargs)))
            return [
                types.SimpleNamespace(
                    id="spotify-release-1",
                    name="Spotify Album",
                    release_date="2024-01-01",
                    album_type="album",
                    image_url="https://img.example/spotify-release-1.jpg",
                    total_tracks=9,
                    external_urls={"spotify": "https://example/spotify-release-1"},
                    artist_ids=["7wzRaLHNSWIG8ZHK2hQljt"],
                )
            ]

    spotify = _SpotifyArtistIdClient()
    clients = {"spotify": spotify}

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "spotify")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: clients.get(source))

    result = metadata_discography.get_artist_discography("364555966", "Amarok", MetadataLookupOptions())

    assert result["source"] == "spotify"
    assert [album["id"] for album in result["albums"]] == ["spotify-release-1"]
    assert spotify.album_calls == [
        (
            "364555966",
            {
                "album_type": "album,single",
                "limit": 50,
                "allow_fallback": False,
                "skip_cache": False,
                "max_pages": 0,
            },
        ),
    ]


def test_get_artist_discography_prefers_source_specific_artist_ids(monkeypatch):
    class _SourceIdClient(_FakeSourceClient):
        def __init__(self, source_id, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.source_id = source_id

        def get_artist_albums(self, artist_id, **kwargs):
            self.album_calls.append((artist_id, dict(kwargs)))
            if artist_id == self.source_id:
                return [
                    _album(f"{self.source_id}-album-1", f"{self.source_id} Album", "2024-01-01")
                ]
            return []

    spotify = _SourceIdClient("spotify-artist-1")
    deezer = _SourceIdClient("deezer-artist-1")
    clients = {
        "spotify": spotify,
        "deezer": deezer,
    }

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "spotify")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "deezer"])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: clients.get(source))

    result = metadata_discography.get_artist_discography(
        "artist-1",
        "Artist One",
        MetadataLookupOptions(
            artist_source_ids={
                "spotify": "spotify-artist-1",
                "deezer": "deezer-artist-1",
            }
        ),
    )

    assert result["source"] == "spotify"
    assert [album["id"] for album in result["albums"]] == ["spotify-artist-1-album-1"]
    assert spotify.album_calls == [
        (
            "spotify-artist-1",
            {
                "album_type": "album,single",
                "limit": 50,
                "allow_fallback": False,
                "skip_cache": False,
                "max_pages": 0,
            },
        )
    ]
    assert deezer.album_calls == []
