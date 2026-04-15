import sys
import types
from types import SimpleNamespace

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

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from core.repair_jobs.unknown_artist_fixer import UnknownArtistFixerJob
import core.repair_jobs.unknown_artist_fixer as unknown_artist_fixer_module


class _FakeClient:
    def __init__(self, track_details=None, search_results=None):
        self.track_details = track_details or {}
        self.search_results = search_results or {}
        self.get_calls = []
        self.search_calls = []

    def get_track_details(self, track_id):
        self.get_calls.append(track_id)
        return self.track_details.get(track_id)

    def search_tracks(self, query, limit=5):
        self.search_calls.append((query, limit))
        return self.search_results.get(query, [])


def _install_tag_reader(monkeypatch, tags=None):
    fake_module = types.ModuleType("core.tag_writer")
    fake_module.read_file_tags = lambda path: tags or {}
    monkeypatch.setitem(sys.modules, "core.tag_writer", fake_module)


def test_unknown_artist_fixer_uses_primary_source_track_id_first(monkeypatch):
    job = UnknownArtistFixerJob()
    _install_tag_reader(monkeypatch)

    deezer_client = _FakeClient(
        track_details={
            "dz-1": {
                "primary_artist": "Deezer Artist",
                "album": {
                    "name": "Deezer Album",
                    "release_date": "2024-02-01",
                    "images": [{"url": "https://img/deezer"}],
                },
                "name": "Deezer Song",
                "track_number": 7,
                "disc_number": 1,
            }
        }
    )
    spotify_client = _FakeClient(
        track_details={
            "sp-1": {
                "primary_artist": "Spotify Artist",
                "album": {
                    "name": "Spotify Album",
                    "release_date": "2023-01-01",
                    "images": [{"url": "https://img/spotify"}],
                },
                "name": "Spotify Song",
                "track_number": 1,
                "disc_number": 1,
            }
        }
    )

    monkeypatch.setattr(unknown_artist_fixer_module, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(
        unknown_artist_fixer_module,
        "get_client_for_source",
        lambda source: {"deezer": deezer_client, "spotify": spotify_client}.get(source),
    )

    track = {
        "title": "Unknown Title",
        "album_title": "Unknown Album",
        "spotify_track_id": "sp-1",
        "deezer_track_id": "dz-1",
        "itunes_track_id": "",
    }

    result = job._resolve_metadata(SimpleNamespace(), track, "/tmp/track.flac")

    assert result["artist"] == "Deezer Artist"
    assert result["album"] == "Deezer Album"
    assert result["source"] == "deezer_track_id_lookup"
    assert deezer_client.get_calls == ["dz-1"]
    assert spotify_client.get_calls == []


def test_unknown_artist_fixer_searches_primary_source_first(monkeypatch):
    job = UnknownArtistFixerJob()
    _install_tag_reader(monkeypatch)

    candidate = SimpleNamespace(
        id="dz-song-1",
        name="Matching Title",
        album="Matching Album",
        artists=["Deezer Artist"],
        image_url="https://img/deezer-search",
    )

    deezer_client = _FakeClient(
        track_details={
            "dz-song-1": {
                "primary_artist": "Deezer Artist",
                "album": {
                    "name": "Matching Album",
                    "release_date": "2024-03-02",
                    "images": [{"url": "https://img/deezer-full"}],
                },
                "name": "Matching Title",
                "track_number": 4,
                "disc_number": 1,
            }
        },
        search_results={"Matching Title": [candidate]},
    )
    spotify_client = _FakeClient(
        search_results={
            "Matching Title": [
                SimpleNamespace(
                    id="sp-song-1",
                    name="Matching Title",
                    album="Matching Album",
                    artists=["Spotify Artist"],
                    image_url="https://img/spotify-search",
                )
            ]
        }
    )

    monkeypatch.setattr(unknown_artist_fixer_module, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(
        unknown_artist_fixer_module,
        "get_client_for_source",
        lambda source: {"deezer": deezer_client, "spotify": spotify_client}.get(source),
    )

    track = {
        "title": "Matching Title",
        "album_title": "Matching Album",
        "spotify_track_id": "",
        "deezer_track_id": "",
        "itunes_track_id": "",
    }

    result = job._resolve_metadata(SimpleNamespace(sleep_or_stop=lambda seconds: False), track, "/tmp/track.flac")

    assert result["artist"] == "Deezer Artist"
    assert result["album"] == "Matching Album"
    assert result["source"] == "deezer_title_search"
    assert deezer_client.search_calls == [("Matching Title", 5)]
    assert spotify_client.search_calls == []


def test_unknown_artist_fixer_supports_hydrabase_title_search(monkeypatch):
    job = UnknownArtistFixerJob()
    _install_tag_reader(monkeypatch)

    hydrabase_candidate = SimpleNamespace(
        id="hy-song-1",
        name="Hydra Match",
        album="Hydra Album",
        artists=["Hydra Artist"],
        image_url="https://img/hydra-search",
    )
    hydrabase_client = _FakeClient(
        track_details={
            "hy-song-1": {
                "primary_artist": "Hydra Artist",
                "album": {
                    "name": "Hydra Album",
                    "release_date": "2024-04-03",
                    "images": [{"url": "https://img/hydra-full"}],
                },
                "name": "Hydra Match",
                "track_number": 2,
                "disc_number": 1,
            }
        },
        search_results={"Hydra Match": [hydrabase_candidate]},
    )
    spotify_client = _FakeClient()

    monkeypatch.setattr(unknown_artist_fixer_module, "get_primary_source", lambda: "hydrabase")
    monkeypatch.setattr(
        unknown_artist_fixer_module,
        "get_client_for_source",
        lambda source: {"hydrabase": hydrabase_client, "spotify": spotify_client}.get(source),
    )

    track = {
        "title": "Hydra Match",
        "album_title": "Hydra Album",
        "spotify_track_id": "",
        "deezer_track_id": "",
        "itunes_track_id": "",
    }

    result = job._resolve_metadata(SimpleNamespace(sleep_or_stop=lambda seconds: False), track, "/tmp/track.flac")

    assert result["artist"] == "Hydra Artist"
    assert result["source"] == "hydrabase_title_search"
    assert hydrabase_client.search_calls == [("Hydra Match", 5)]
    assert spotify_client.search_calls == []
