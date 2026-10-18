from types import SimpleNamespace

from core.metadata import registry as metadata_registry
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

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: {"deezer": deezer_client, "spotify": spotify_client, "itunes": None}.get(source),
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

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: {"deezer": deezer_client, "spotify": spotify_client, "itunes": None}.get(source),
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

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: {"deezer": deezer_client, "spotify": spotify_client, "itunes": None}.get(source),
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

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: {"deezer": deezer_client, "spotify": spotify_client, "itunes": itunes_client}.get(source),
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


def test_get_single_track_import_context_from_own_tags_never_searches(monkeypatch):
    """#1772: "No track picked" — the user declined every catalogue match, so
    no source may be searched at all. The clients below WOULD return an
    unrelated first result (the reported Morgan Wallen rematch); the context
    must come from the file's own tags alone, flagged as hand-tagged so
    downstream correction stands down and the embedded cover survives."""
    wrong_client = FakeClient(
        search_results=[_track_result(track_id="wrong-1", name="Whiskey Glasses", artist="Morgan Wallen")],
        details={"wrong-1": _track_details("itunes", track_id="wrong-1", name="Whiskey Glasses", artist_name="Morgan Wallen")},
    )

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "itunes")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "deezer"])
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: wrong_client,
    )

    result = resolution.get_single_track_import_context(
        "Live Mix",
        "Dj StarSunglasses",
        from_own_tags=True,
        album="Live Mix",
        track_number=1,
        disc_number=1,
        duration_ms=2_880_000,
    )

    assert wrong_client.calls == []
    assert result["success"] is False
    assert result["source"] is None
    context = result["context"]
    assert context["artist"]["name"] == "Dj StarSunglasses"
    assert context["artist"]["id"] == ""
    assert context["track_info"]["name"] == "Live Mix"
    assert context["track_info"]["id"] == ""
    assert context["track_info"]["album"] == "Live Mix"
    assert context["track_info"]["duration_ms"] == 2_880_000
    assert context["album"]["name"] == "Live Mix"
    assert context["original_search_result"]["clean_title"] == "Live Mix"
    assert context["original_search_result"]["clean_artist"] == "Dj StarSunglasses"
    assert context["_manual_metadata"] is True
    assert context["track_info"]["_manual_metadata"] is True


def test_get_single_track_import_context_from_own_tags_tolerates_garbage_tag_numbers(monkeypatch):
    """Tag numbers arrive as whatever the file/request carried; unusable
    values (including inf, whose int() is an OverflowError, and booleans) must
    fall back to defaults, never raise out of the import."""
    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: None)

    result = resolution.get_single_track_import_context(
        "Song",
        "Artist",
        from_own_tags=True,
        track_number="abc",
        disc_number=None,
        duration_ms=float("inf"),
    )

    assert result["context"]["track_info"]["track_number"] == 1
    assert result["context"]["track_info"]["disc_number"] == 1
    assert result["context"]["track_info"]["duration_ms"] == 0

    # Booleans (bool subclasses int in Python) must not masquerade as 1 or 0
    bool_result = resolution.get_single_track_import_context(
        "Song",
        "Artist",
        from_own_tags=True,
        track_number=True,
        disc_number=False,
        duration_ms=True,
    )
    assert bool_result["context"]["track_info"]["track_number"] == 1
    assert bool_result["context"]["track_info"]["disc_number"] == 1
    assert bool_result["context"]["track_info"]["duration_ms"] == 0


def test_build_single_import_fallback_context_tolerates_nonstring_args():
    """_build_single_import_fallback_context directly called with non-strings
    safely coerces to Unknown Artist and Unknown Track."""
    result = resolution._build_single_import_fallback_context(
        123,
        456,
        ["spotify"],
        album=["not", "a", "string"],
    )
    context = result["context"]
    assert context["artist"]["name"] == "Unknown Artist"
    assert context["track_info"]["name"] == "Unknown Track"
    assert context["album"]["name"] == ""


def test_get_single_track_import_context_from_own_tags_honours_tag_string_conventions(monkeypatch):
    """Tag strings follow the staging reader's conventions: "3/12" is track
    3, a float-string duration keeps its whole part, and non-positive
    positions are not real, so they take the defaults."""
    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: None)

    result = resolution.get_single_track_import_context(
        "Song",
        "Artist",
        from_own_tags=True,
        track_number="3/12",
        disc_number=-2,
        duration_ms="2880000.0",
    )

    assert result["context"]["track_info"]["track_number"] == 3
    assert result["context"]["track_info"]["disc_number"] == 1
    assert result["context"]["track_info"]["duration_ms"] == 2_880_000


def test_get_single_track_import_context_from_own_tags_failed_override_never_searches(monkeypatch):
    """The flag is an absolute never-search promise: even when an override
    id was supplied and its lookup fails, the resolver lands on the own-tags
    context instead of falling through to the catalogue search loop."""
    wrong_client = FakeClient(
        search_results=[_track_result(track_id="wrong-1", name="Whiskey Glasses", artist="Morgan Wallen")],
        details={},
    )

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: wrong_client,
    )

    result = resolution.get_single_track_import_context(
        "Live Mix",
        "Dj StarSunglasses",
        override_id="missing-track",
        override_source="deezer",
        from_own_tags=True,
    )

    assert result["source"] is None
    assert result["context"]["track_info"]["name"] == "Live Mix"
    assert result["context"]["_manual_metadata"] is True
    assert [c for c in wrong_client.calls if c[0] == "search_tracks"] == []


def test_get_single_track_import_context_from_own_tags_yields_to_override(monkeypatch):
    spotify_client = FakeClient(
        details={"override-track-1": _track_details("spotify", track_id="override-track-1", artist_name="Override Artist", artist_id="spotify-artist-1")},
        artist_details={"spotify-artist-1": {"id": "spotify-artist-1", "genres": ["pop"]}},
    )

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: {"spotify": spotify_client}.get(source),
    )

    result = resolution.get_single_track_import_context(
        "Song",
        "Artist",
        override_id="override-track-1",
        from_own_tags=True,
    )

    assert result["source"] == "spotify"
    assert result["context"]["track_info"]["id"] == "override-track-1"


def test_failed_lookup_fallback_ignores_supplied_tag_fields(monkeypatch):
    """The ordinary failed-lookup fallback keeps its HISTORICAL shape even
    when the caller supplied tag fields: those fields only shape the
    explicit from_own_tags context. Pinning this is what stops the fields
    leaking back into every no-match import (round-1 finding)."""
    deezer_client = FakeClient()
    spotify_client = FakeClient()
    itunes_client = FakeClient()

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: {"deezer": deezer_client, "spotify": spotify_client, "itunes": itunes_client}.get(source),
    )

    result = resolution.get_single_track_import_context(
        "Missing Song",
        "Missing Artist",
        album="Tag Album",
        track_number=9,
        disc_number=2,
        duration_ms=999_000,
    )

    context = result["context"]
    assert context["album"]["name"] == ""
    assert context["album"]["artists"] == []
    assert context["track_info"]["album"] == ""
    assert context["track_info"]["track_number"] == 1
    assert context["track_info"]["disc_number"] == 1
    assert context["track_info"]["duration_ms"] == 0
    assert context["original_search_result"]["clean_album"] == ""
    assert "_manual_metadata" not in context


def test_get_single_track_import_context_from_own_tags_tolerates_nonstring_fields(monkeypatch):
    """Raw request JSON can carry anything: a numeric title, a list album.
    Those are treated as absent, never stringified into the library and
    never an AttributeError out of the resolver."""
    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(metadata_registry, "get_client_for_source", lambda source, **kwargs: None)

    result = resolution.get_single_track_import_context(
        123,
        456,
        from_own_tags=True,
        album=["not", "a", "name"],
        track_number=10**100,
        duration_ms=10**100,
    )

    context = result["context"]
    assert context["track_info"]["name"] == "Unknown Track"
    assert context["artist"]["name"] == "Unknown Artist"
    assert context["album"]["name"] == ""
    assert context["track_info"]["track_number"] == 1
    assert context["track_info"]["duration_ms"] == 0


def test_get_single_track_import_context_returns_fallback_payload_when_no_source_matches(monkeypatch):
    deezer_client = FakeClient()
    spotify_client = FakeClient()
    itunes_client = FakeClient()

    monkeypatch.setattr(metadata_registry, "get_primary_source", lambda spotify_client_factory=None: "deezer")
    monkeypatch.setattr(metadata_registry, "get_source_priority", lambda primary: [primary, "spotify", "itunes"])
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: {"deezer": deezer_client, "spotify": spotify_client, "itunes": itunes_client}.get(source),
    )

    result = resolution.get_single_track_import_context("Missing Song", "Missing Artist")

    assert result["success"] is False
    assert result["source"] is None
    assert result["context"]["artist"]["name"] == "Missing Artist"
    assert result["context"]["track_info"]["name"] == "Missing Song"
    assert result["context"]["original_search_result"]["clean_title"] == "Missing Song"
    assert result["context"]["original_search_result"]["clean_artist"] == "Missing Artist"
