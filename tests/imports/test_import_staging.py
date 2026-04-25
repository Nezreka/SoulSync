from types import SimpleNamespace

import core.imports.staging as import_staging


class FakeClient:
    def __init__(self, results=None):
        self.results = results or []
        self.calls = []

    def search_albums(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return self.results

    def search_tracks(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return self.results


def _album_result(album_id, name, artist, release_date="2024-01-01", total_tracks=10, image_url="https://img.example/album.jpg", album_type="album"):
    return SimpleNamespace(
        id=album_id,
        name=name,
        artists=[artist],
        release_date=release_date,
        total_tracks=total_tracks,
        image_url=image_url,
        album_type=album_type,
    )


def test_search_import_albums_prefers_primary_source(monkeypatch):
    deezer_client = FakeClient([
        _album_result("deezer-1", "Album One", "Artist One"),
    ])
    spotify_client = FakeClient([
        _album_result("spotify-1", "Album One", "Artist One"),
    ])

    monkeypatch.setattr(import_staging, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(import_staging, "get_source_priority", lambda primary: [primary, "spotify"])
    monkeypatch.setattr(
        import_staging,
        "get_client_for_source",
        lambda source: {"deezer": deezer_client, "spotify": spotify_client}.get(source),
    )
    monkeypatch.setattr(
        import_staging,
        "_search_albums_for_source",
        lambda source, client, query, limit=5: client.search_albums(query, limit=limit, allow_fallback=False) if source == "spotify" else client.search_albums(query, limit=limit),
    )

    results = import_staging.search_import_albums("Album One", limit=2)

    assert results == [
        {
            "id": "deezer-1",
            "name": "Album One",
            "artist": "Artist One",
            "release_date": "2024-01-01",
            "total_tracks": 10,
            "image_url": "https://img.example/album.jpg",
            "album_type": "album",
            "source": "deezer",
        }
    ]
    assert deezer_client.calls == [("Album One", {"limit": 2})]
    assert spotify_client.calls == []


def test_search_import_albums_falls_back_when_primary_has_no_results(monkeypatch):
    deezer_client = FakeClient([])
    spotify_client = FakeClient([
        _album_result("spotify-1", "Album Two", "Artist Two"),
    ])

    monkeypatch.setattr(import_staging, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(import_staging, "get_source_priority", lambda primary: [primary, "spotify"])
    monkeypatch.setattr(
        import_staging,
        "get_client_for_source",
        lambda source: {"deezer": deezer_client, "spotify": spotify_client}.get(source),
    )
    monkeypatch.setattr(
        import_staging,
        "_search_albums_for_source",
        lambda source, client, query, limit=5: client.search_albums(query, limit=limit, allow_fallback=False) if source == "spotify" else client.search_albums(query, limit=limit),
    )

    results = import_staging.search_import_albums("Album Two", limit=2)

    assert results == [
        {
            "id": "spotify-1",
            "name": "Album Two",
            "artist": "Artist Two",
            "release_date": "2024-01-01",
            "total_tracks": 10,
            "image_url": "https://img.example/album.jpg",
            "album_type": "album",
            "source": "spotify",
        }
    ]
    assert deezer_client.calls == [("Album Two", {"limit": 2})]
    assert spotify_client.calls == [("Album Two", {"limit": 2, "allow_fallback": False})]


def test_search_import_tracks_prefers_primary_source(monkeypatch):
    deezer_client = FakeClient([
        SimpleNamespace(
            id="deezer-track-1",
            name="Song One",
            artists=[{"name": "Artist One"}],
            album={"id": "deezer-album-1", "name": "Album One"},
            duration_ms=210000,
            image_url="https://img.example/track.jpg",
            track_number=7,
        ),
    ])
    spotify_client = FakeClient([
        SimpleNamespace(
            id="spotify-track-1",
            name="Song One",
            artists=["Artist One"],
            album="Album One",
            duration_ms=210000,
            image_url="https://img.example/track.jpg",
            track_number=7,
        ),
    ])

    monkeypatch.setattr(import_staging, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(import_staging, "get_source_priority", lambda primary: [primary, "spotify"])
    monkeypatch.setattr(
        import_staging,
        "get_client_for_source",
        lambda source: {"deezer": deezer_client, "spotify": spotify_client}.get(source),
    )
    monkeypatch.setattr(
        import_staging,
        "_search_tracks_for_source",
        lambda source, client, query, limit=5: client.search_tracks(query, limit=limit, allow_fallback=False) if source == "spotify" else client.search_tracks(query, limit=limit),
    )

    results = import_staging.search_import_tracks("Song One", limit=2)

    assert results == [
        {
            "id": "deezer-track-1",
            "name": "Song One",
            "artist": "Artist One",
            "album": "Album One",
            "album_id": "deezer-album-1",
            "duration_ms": 210000,
            "image_url": "https://img.example/track.jpg",
            "track_number": 7,
            "source": "deezer",
        }
    ]
    assert deezer_client.calls == [("Song One", {"limit": 2})]
    assert spotify_client.calls == []


def test_search_import_tracks_falls_back_when_primary_has_no_results(monkeypatch):
    deezer_client = FakeClient([])
    spotify_client = FakeClient([
        SimpleNamespace(
            id="spotify-track-1",
            name="Song Two",
            artists=["Artist Two"],
            album="Album Two",
            duration_ms=180000,
            image_url="",
            track_number=3,
        ),
    ])

    monkeypatch.setattr(import_staging, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(import_staging, "get_source_priority", lambda primary: [primary, "spotify"])
    monkeypatch.setattr(
        import_staging,
        "get_client_for_source",
        lambda source: {"deezer": deezer_client, "spotify": spotify_client}.get(source),
    )
    monkeypatch.setattr(
        import_staging,
        "_search_tracks_for_source",
        lambda source, client, query, limit=5: client.search_tracks(query, limit=limit, allow_fallback=False) if source == "spotify" else client.search_tracks(query, limit=limit),
    )

    results = import_staging.search_import_tracks("Song Two", limit=2)

    assert results == [
        {
            "id": "spotify-track-1",
            "name": "Song Two",
            "artist": "Artist Two",
            "album": "Album Two",
            "album_id": "",
            "duration_ms": 180000,
            "image_url": "",
            "track_number": 3,
            "source": "spotify",
        }
    ]
    assert deezer_client.calls == [("Song Two", {"limit": 2})]
    assert spotify_client.calls == [("Song Two", {"limit": 2, "allow_fallback": False})]


def test_build_import_suggestions_background_uses_collected_queries(monkeypatch, tmp_path):
    staging_root = tmp_path / "Staging"
    staging_root.mkdir()

    cache = import_staging.get_import_suggestions_cache()
    original_cache = dict(cache)
    cache["suggestions"] = []
    cache["building"] = False
    cache["built"] = False

    monkeypatch.setattr(import_staging, "get_staging_path", lambda: str(staging_root))
    monkeypatch.setattr(import_staging, "_collect_import_suggestion_queries", lambda staging_path: ["Album One", "Folder Hint"])
    monkeypatch.setattr(
        import_staging,
        "search_import_albums",
        lambda query, limit=2: [
            {
                "id": query.lower().replace(" ", "-"),
                "name": query,
                "artist": f"{query} Artist",
                "release_date": "2024-02-01",
                "total_tracks": 12,
                "image_url": "",
                "album_type": "album",
            }
        ],
    )

    try:
        import_staging._build_import_suggestions_background()

        assert cache["built"] is True
        assert cache["building"] is False
        assert cache["suggestions"] == [
            {
                "id": "album-one",
                "name": "Album One",
                "artist": "Album One Artist",
                "release_date": "2024-02-01",
                "total_tracks": 12,
                "image_url": "",
                "album_type": "album",
            },
            {
                "id": "folder-hint",
                "name": "Folder Hint",
                "artist": "Folder Hint Artist",
                "release_date": "2024-02-01",
                "total_tracks": 12,
                "image_url": "",
                "album_type": "album",
            },
        ]
    finally:
        cache.clear()
        cache.update(original_cache)
