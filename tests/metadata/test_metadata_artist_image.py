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
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from core.metadata import artist_image as metadata_artist_image
from core.metadata import registry as metadata_registry


class _FakeSpotifyClient:
    def __init__(self, image_url="https://spotify.example/artist.jpg"):
        self.image_url = image_url
        self.calls = []

    def is_spotify_authenticated(self):
        return True

    def get_artist(self, artist_id, allow_fallback=True):
        self.calls.append((artist_id, allow_fallback))
        return {
            "id": artist_id,
            "name": "Spotify Artist",
            "images": [{"url": self.image_url}],
            "genres": ["rock"],
            "popularity": 80,
        }


class _FakeDeezerClient:
    def __init__(self, image_url="https://deezer.example/artist.jpg"):
        self.image_url = image_url
        self.calls = []

    def get_artist(self, artist_id):
        self.calls.append(artist_id)
        return types.SimpleNamespace(
            id=artist_id,
            name="Deezer Artist",
            image_url=self.image_url,
            genres=["indie"],
            popularity=0,
        )


class _FakeItunesClient:
    def __init__(self, album_art_url="https://itunes.example/artist.jpg"):
        self.album_art_url = album_art_url
        self.calls = []
        self.album_art_calls = []

    def get_artist(self, artist_id):
        self.calls.append(artist_id)
        return {
            "id": artist_id,
            "name": "iTunes Artist",
            "images": [],
            "genres": ["alt"],
            "popularity": 0,
        }

    def _get_artist_image_from_albums(self, artist_id):
        self.album_art_calls.append(artist_id)
        return self.album_art_url


def test_get_artist_image_url_uses_primary_source_priority(monkeypatch):
    deezer = _FakeDeezerClient()
    spotify = _FakeSpotifyClient()

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify"])
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: {"deezer": deezer, "spotify": spotify}.get(source),
    )

    image_url = metadata_artist_image.get_artist_image_url("artist-1")

    assert image_url == "https://deezer.example/artist.jpg"
    assert deezer.calls == ["artist-1"]
    assert spotify.calls == []


def test_get_artist_image_url_uses_itunes_album_art_for_explicit_override(monkeypatch):
    itunes = _FakeItunesClient()
    spotify = _FakeSpotifyClient()

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "spotify")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "itunes"])
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: {"itunes": itunes, "spotify": spotify}.get(source),
    )

    image_url = metadata_artist_image.get_artist_image_url("12345", source_override="itunes")

    assert image_url == "https://itunes.example/artist.jpg"
    assert itunes.calls == ["12345"]
    assert itunes.album_art_calls == ["12345"]
    assert spotify.calls == []


def test_get_artist_image_url_handles_hydrabase_plugin(monkeypatch):
    deezer = _FakeDeezerClient("https://deezer.example/hydra.jpg")
    spotify = _FakeSpotifyClient()

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "spotify")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "deezer"])
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: {"deezer": deezer, "spotify": spotify}.get(source),
    )

    image_url = metadata_artist_image.get_artist_image_url("artist-1", source_override="hydrabase", plugin="deezer")

    assert image_url == "https://deezer.example/hydra.jpg"
    assert deezer.calls == ["artist-1"]
    assert spotify.calls == []
