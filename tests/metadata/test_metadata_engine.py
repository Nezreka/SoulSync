import sys
import types
from types import SimpleNamespace


if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")
    oauth2 = types.ModuleType("spotipy.oauth2")

    class _DummySpotify:
        def __init__(self, *args, **kwargs):
            pass

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

from core.metadata.engine import MetadataEngine, MetadataSourceFacade
from core.metadata.models import MetadataArtist, MetadataRecord
from core.metadata.registry import get_client_for_source
from core.metadata.service import MetadataService


def test_get_client_for_source_returns_source_facade():
    client = get_client_for_source("itunes")

    assert isinstance(client, MetadataSourceFacade)
    assert client.source == "itunes"
    assert client.is_connected() is True
    assert hasattr(client, "_get_artist_image_from_albums")


def test_metadata_service_searches_via_engine(monkeypatch):
    calls = []

    class _FakeOutcome:
        def __init__(self, items):
            self.items = items

    class _FakeEngine:
        def search_artists(self, query, **kwargs):
            calls.append(("artists", query, dict(kwargs)))
            return _FakeOutcome([SimpleNamespace(id="artist-1", name="Artist One")])

        def search_tracks(self, query, **kwargs):
            calls.append(("tracks", query, dict(kwargs)))
            return _FakeOutcome([])

        def search_albums(self, query, **kwargs):
            calls.append(("albums", query, dict(kwargs)))
            return _FakeOutcome([])

    monkeypatch.setattr("core.metadata.service.get_spotify_client", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.metadata.service.get_primary_source", lambda *args, **kwargs: "deezer")
    monkeypatch.setattr("core.metadata.service.get_client_for_source", lambda source: SimpleNamespace(source=source))
    monkeypatch.setattr("core.metadata.service.get_metadata_engine", lambda: _FakeEngine())

    service = MetadataService()
    results = service.search_artists("Artist One", limit=5)

    assert [result.name for result in results] == ["Artist One"]
    assert calls[0][0] == "artists"
    assert calls[0][1] == "Artist One"
    assert calls[0][2]["limit"] == 5
    assert calls[0][2]["source_override"] == "deezer"


def test_metadata_record_mirrors_source_id_and_id():
    record = MetadataRecord({"id": "provider-1"})
    assert record["source_id"] == "provider-1"
    assert record["id"] == "provider-1"

    record = MetadataRecord({"source_id": "provider-2"})
    assert record["source_id"] == "provider-2"
    assert record["id"] == "provider-2"

    record["id"] = "different-id"
    assert record["source_id"] == "provider-2"
    assert record["id"] == "provider-2"


def test_metadata_artist_to_dict_prefers_source_id():
    artist = MetadataArtist(
        id="legacy-id",
        name="Artist One",
        popularity=0,
        genres=[],
        followers=0,
        source_id="provider-id",
    )

    payload = artist.to_dict()

    assert payload["source_id"] == "provider-id"
    assert payload["id"] == "provider-id"


def test_metadata_engine_caches_search_results_by_source_id(monkeypatch):
    stored_rows = []

    class _FakeAdapter:
        def search_artists_raw(self, query, limit=20):
            return [{"id": "provider-1", "name": "Artist One"}]

    class _FakeCache:
        def store_entities_bulk(self, source, entity_kind, rows, skip_if_exists=True):
            stored_rows.append((source, entity_kind, rows, skip_if_exists))

    engine = MetadataEngine()
    monkeypatch.setattr(engine, "_get_adapter", lambda source: _FakeAdapter())
    monkeypatch.setattr("core.metadata.engine.get_metadata_cache", lambda: _FakeCache())

    outcome = engine.search_artists("Artist One", source_override="deezer", allow_fallback=False)

    assert outcome.items[0]["source_id"] == "provider-1"
    assert stored_rows[0][0] == "deezer"
    assert stored_rows[0][1] == "artist"
    assert stored_rows[0][2][0][0] == "provider-1"
