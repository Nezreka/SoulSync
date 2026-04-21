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

import types as pytypes

from core import metadata_service


class _FakeMusicMapResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class _FakeSourceClient:
    def __init__(self, results_by_query):
        self.results_by_query = results_by_query
        self.search_calls = []

    def search_artists(self, query, **kwargs):
        self.search_calls.append((query, dict(kwargs)))
        return list(self.results_by_query.get(query, []))


def test_iter_musicmap_similar_artist_events_uses_source_priority(monkeypatch):
    html = """
    <html>
      <body>
        <div id="gnodMap">
          <a href="/artist/seed">Artist One</a>
          <a href="/artist/similar">Similar Artist</a>
        </div>
      </body>
    </html>
    """

    deezer = _FakeSourceClient({
        "Artist One": [pytypes.SimpleNamespace(id="dz-seed", name="Artist One")],
        "Similar Artist": [],
    })
    itunes = _FakeSourceClient({
        "Artist One": [pytypes.SimpleNamespace(id="it-seed", name="Artist One")],
        "Similar Artist": [
            pytypes.SimpleNamespace(
                id="it-match",
                name="iTunes Canonical",
                image_url="https://itunes.example/it-match.jpg",
                genres=["indie", "alt"],
                popularity=77,
            )
        ],
    })
    spotify = _FakeSourceClient({})

    monkeypatch.setattr(metadata_service.requests, "get", lambda *args, **kwargs: _FakeMusicMapResponse(html))
    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(metadata_service, "get_source_priority", lambda primary: [primary, "itunes", "spotify"])
    monkeypatch.setattr(
        metadata_service,
        "get_client_for_source",
        lambda source: {"deezer": deezer, "itunes": itunes, "spotify": spotify}.get(source),
    )

    events = list(metadata_service.iter_musicmap_similar_artist_events("Artist One", limit=5))

    assert events[0]["type"] == "start"
    assert events[0]["source_priority"] == ["deezer", "itunes", "spotify"]
    assert events[1]["type"] == "artist"
    assert events[1]["artist"]["id"] == "it-match"
    assert events[1]["artist"]["name"] == "iTunes Canonical"
    assert events[1]["artist"]["image_url"] == "https://itunes.example/it-match.jpg"
    assert events[1]["artist"]["genres"] == ["indie", "alt"]
    assert events[1]["artist"]["popularity"] == 77
    assert events[1]["artist"]["source"] == "itunes"
    assert events[-1]["type"] == "complete"
    assert events[-1]["complete"] is True
    assert events[-1]["total"] == 1
    assert events[-1]["total_found"] == 1

    assert [call[0] for call in deezer.search_calls] == ["Artist One", "Similar Artist"]
    assert [call[0] for call in itunes.search_calls] == ["Artist One", "Similar Artist"]
    assert spotify.search_calls == [("Artist One", {"limit": 1, "allow_fallback": False})]


def test_iter_musicmap_similar_artist_events_enriches_itunes_images(monkeypatch):
    html = """
    <html>
      <body>
        <div id="gnodMap">
          <a href="/artist/similar">Similar Artist</a>
        </div>
      </body>
    </html>
    """

    class _ItunesClient(_FakeSourceClient):
        def __init__(self):
            super().__init__({
                "Artist One": [pytypes.SimpleNamespace(id="it-seed", name="Artist One")],
                "Similar Artist": [pytypes.SimpleNamespace(id="it-match", name="iTunes Canonical")],
            })
            self.get_artist_calls = []

        def get_artist(self, artist_id):
            self.get_artist_calls.append(artist_id)
            return {
                "id": artist_id,
                "name": "iTunes Canonical",
                "images": [{"url": "https://itunes.example/full-art.jpg"}],
                "genres": ["indie"],
                "popularity": 0,
            }

    itunes = _ItunesClient()
    monkeypatch.setattr(metadata_service.requests, "get", lambda *args, **kwargs: _FakeMusicMapResponse(html))
    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: "itunes")
    monkeypatch.setattr(metadata_service, "get_source_priority", lambda primary: [primary])
    monkeypatch.setattr(metadata_service, "get_client_for_source", lambda source: itunes if source == "itunes" else None)

    events = list(metadata_service.iter_musicmap_similar_artist_events("Artist One", limit=5))

    artist_events = [event for event in events if event.get("type") == "artist"]
    assert len(artist_events) == 1
    assert artist_events[0]["artist"]["image_url"] == "https://itunes.example/full-art.jpg"
    assert itunes.get_artist_calls == ["it-match"]


def test_iter_musicmap_similar_artist_events_falls_back_to_itunes_album_art(monkeypatch):
    html = """
    <html>
      <body>
        <div id="gnodMap">
          <a href="/artist/similar">Similar Artist</a>
        </div>
      </body>
    </html>
    """

    class _ItunesClient(_FakeSourceClient):
        def __init__(self):
            super().__init__({
                "Artist One": [pytypes.SimpleNamespace(id="it-seed", name="Artist One")],
                "Similar Artist": [pytypes.SimpleNamespace(id="it-match", name="iTunes Canonical")],
            })
            self.get_artist_calls = []
            self.album_art_calls = []

        def get_artist(self, artist_id):
            self.get_artist_calls.append(artist_id)
            return {
                "id": artist_id,
                "name": "iTunes Canonical",
                "images": [],
                "genres": ["indie"],
                "popularity": 0,
            }

        def _get_artist_image_from_albums(self, artist_id):
            self.album_art_calls.append(artist_id)
            return "https://itunes.example/album-art.jpg"

    itunes = _ItunesClient()
    monkeypatch.setattr(metadata_service.requests, "get", lambda *args, **kwargs: _FakeMusicMapResponse(html))
    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: "itunes")
    monkeypatch.setattr(metadata_service, "get_source_priority", lambda primary: [primary])
    monkeypatch.setattr(metadata_service, "get_client_for_source", lambda source: itunes if source == "itunes" else None)

    events = list(metadata_service.iter_musicmap_similar_artist_events("Artist One", limit=5))

    artist_events = [event for event in events if event.get("type") == "artist"]
    assert len(artist_events) == 1
    assert artist_events[0]["artist"]["image_url"] == "https://itunes.example/album-art.jpg"
    assert itunes.get_artist_calls == ["it-match"]
    assert itunes.album_art_calls == ["it-match"]


def test_get_musicmap_similar_artists_returns_not_found_when_musicmap_missing(monkeypatch):
    html = """
    <html>
      <body>
        <div class="no-map">Nothing here</div>
      </body>
    </html>
    """

    monkeypatch.setattr(metadata_service.requests, "get", lambda *args, **kwargs: _FakeMusicMapResponse(html))
    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(metadata_service, "get_source_priority", lambda primary: [primary, "itunes"])
    monkeypatch.setattr(metadata_service, "get_client_for_source", lambda source: object())

    result = metadata_service.get_musicmap_similar_artists("Artist One", limit=5)

    assert result["success"] is False
    assert result["status_code"] == 404
    assert "Could not find artist map" in result["error"]
    assert result["similar_artists"] == []
