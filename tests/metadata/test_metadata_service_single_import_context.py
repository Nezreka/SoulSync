from types import SimpleNamespace

from core import metadata_service
from core.imports import resolution


class FakeClient:
    def __init__(self, search_results=None, details=None, artist_details=None):
        self.search_results = search_results or []
        self.details = details or {}
        self.artist_details = artist_details or {}
        self.calls = []

    def search_tracks(self, query, limit=1, allow_fallback=True):
        self.calls.append(("search_tracks", query, limit, allow_fallback))
        return self.search_results

    def get_track_details(self, track_id):
        self.calls.append(("get_track_details", track_id))
        return self.details.get(str(track_id))

    def get_artist(self, artist_id):
        self.calls.append(("get_artist", artist_id))
        return self.artist_details.get(str(artist_id))


def _track_result(track_id="track-1", name="Song One", artist="Artist One"):
    return SimpleNamespace(
        id=track_id,
        name=name,
        artists=[artist],
        album="Album One",
        duration_ms=123000,
        track_number=1,
        disc_number=1,
        image_url="https://img.example/track.jpg",
    )


def _track_details(source, track_id="track-1", name="Song One", artist_name="Artist One", artist_id="artist-1"):
    return {
        "id": track_id,
        "name": name,
        "track_number": 7,
        "disc_number": 1,
        "duration_ms": 210000,
        "explicit": True,
        "uri": f"{source}:track:{track_id}",
        "artists": [{"name": artist_name, "id": artist_id}],
        "album": {
            "id": f"{source}-album-1",
            "name": "Album One",
            "release_date": "2024-01-01",
            "album_type": "album",
            "total_tracks": 10,
            "images": [{"url": f"https://img.example/{source}-album.jpg"}],
            "artists": [{"name": artist_name, "id": artist_id}],
        },
    }


def test_get_single_track_import_context_uses_primary_source_priority(monkeypatch):
    deezer_client = FakeClient(
        search_results=[_track_result(track_id="deezer-track-1")],
        details={"deezer-track-1": _track_details("deezer", track_id="deezer-track-1", artist_name="Artist One", artist_id="deezer-artist-1")},
        artist_details={"deezer-artist-1": {"id": "deezer-artist-1", "genres": ["electronic"]}},
    )
    spotify_client = FakeClient()

    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(metadata_service, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(
        metadata_service,
        "get_client_for_source",
        lambda source: {"deezer": deezer_client, "spotify": spotify_client, "itunes": None}.get(source),
    )

    result = resolution.get_single_track_import_context("Song One", "Artist One")

    assert result["success"] is True
    assert result["source"] == "deezer"
    assert result["source_priority"] == ["deezer", "spotify", "itunes"]
    assert result["context"]["track_info"]["name"] == "Song One"
    assert result["context"]["track_info"]["album_id"] == "deezer-album-1"
    assert result["context"]["album"]["image_url"] == "https://img.example/deezer-album.jpg"
    assert result["context"]["artist"]["genres"] == ["electronic"]
    assert result["context"]["original_search_result"]["clean_title"] == "Song One"
    assert deezer_client.calls == [
        ('search_tracks', 'artist:"Artist One" track:"Song One"', 5, True),
        ("get_track_details", "deezer-track-1"),
        ("get_artist", "deezer-artist-1"),
    ]
    assert spotify_client.calls == []


def test_get_single_track_import_context_falls_back_to_next_source(monkeypatch):
    deezer_client = FakeClient(search_results=[])
    spotify_client = FakeClient(
        search_results=[_track_result(track_id="spotify-track-1")],
        details={"spotify-track-1": _track_details("spotify", track_id="spotify-track-1", artist_name="Artist Two", artist_id="spotify-artist-1")},
        artist_details={"spotify-artist-1": {"id": "spotify-artist-1", "genres": ["indie"]}},
    )

    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(metadata_service, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(
        metadata_service,
        "get_client_for_source",
        lambda source: {"deezer": deezer_client, "spotify": spotify_client, "itunes": None}.get(source),
    )

    result = resolution.get_single_track_import_context("Song Two", "Artist Two")

    assert result["success"] is True
    assert result["source"] == "spotify"
    assert result["context"]["track_info"]["id"] == "spotify-track-1"
    assert result["context"]["album"]["name"] == "Album One"
    assert deezer_client.calls == [
        ('search_tracks', 'artist:"Artist Two" track:"Song Two"', 5, True),
        ('search_tracks', 'Song Two', 5, True),
        ('search_tracks', 'Artist Two', 5, True),
    ]
    assert spotify_client.calls == [
        ("search_tracks", "Song Two Artist Two", 5, False),
        ("get_track_details", "spotify-track-1"),
        ("get_artist", "spotify-artist-1"),
    ]


def test_get_single_track_import_context_uses_explicit_override_first(monkeypatch):
    spotify_client = FakeClient(
        details={"override-track-1": _track_details("spotify", track_id="override-track-1", artist_name="Override Artist", artist_id="spotify-artist-1")},
        artist_details={"spotify-artist-1": {"id": "spotify-artist-1", "genres": ["pop"]}},
    )
    deezer_client = FakeClient()

    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(metadata_service, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(
        metadata_service,
        "get_client_for_source",
        lambda source: {"deezer": deezer_client, "spotify": spotify_client, "itunes": None}.get(source),
    )

    result = resolution.get_single_track_import_context(
        "Ignored Title",
        "Ignored Artist",
        override_id="override-track-1",
    )

    assert result["success"] is True
    assert result["source"] == "spotify"
    assert result["context"]["track_info"]["id"] == "override-track-1"
    assert result["context"]["artist"]["genres"] == ["pop"]
    assert spotify_client.calls == [
        ("get_track_details", "override-track-1"),
        ("get_artist", "spotify-artist-1"),
    ]
    assert deezer_client.calls == []


def test_get_single_track_import_context_uses_explicit_override_source(monkeypatch):
    itunes_client = FakeClient(
        details={"override-track-2": _track_details("itunes", track_id="override-track-2", artist_name="Override Artist Two", artist_id="itunes-artist-1")},
        artist_details={"itunes-artist-1": {"id": "itunes-artist-1", "genres": ["singer-songwriter"]}},
    )
    spotify_client = FakeClient()
    deezer_client = FakeClient()

    monkeypatch.setattr(metadata_service, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(metadata_service, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(
        metadata_service,
        "get_client_for_source",
        lambda source: {"deezer": deezer_client, "spotify": spotify_client, "itunes": itunes_client}.get(source),
    )

    result = resolution.get_single_track_import_context(
        "Ignored Title",
        "Ignored Artist",
        override_id="override-track-2",
        override_source="itunes",
    )

    assert result["success"] is True
    assert result["source"] == "itunes"
    assert result["context"]["track_info"]["id"] == "override-track-2"
    assert result["context"]["artist"]["genres"] == ["singer-songwriter"]
    assert itunes_client.calls == [
        ("get_track_details", "override-track-2"),
        ("get_artist", "itunes-artist-1"),
    ]
    assert deezer_client.calls == []
    assert spotify_client.calls == []
