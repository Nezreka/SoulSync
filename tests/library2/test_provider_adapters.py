"""Typed Library-v2 boundaries for metadata-provider facades."""

from __future__ import annotations

import pytest

from core.library2.artwork import _provider_art_url
from core.library2.metadata_overrides import set_field_override
from core.library2.provider_adapters import (
    ArtworkProviderResult,
    fetch_album_tracklist,
    fetch_artwork_url,
    fetch_track_metadata,
)


class _DeezerTracklistClient:
    def __init__(self, metadata):
        self.metadata = metadata
        self.track_calls = []

    def search_album(self, artist_name, album_title):
        assert (artist_name, album_title) == ("Artist", "Album")
        return {"id": "dz-search"}

    def get_album_metadata(self, album_id, include_tracks=True):
        assert album_id == "dz-search"
        assert include_tracks is False
        return self.metadata

    def get_album_tracks(self, album_id):
        self.track_calls.append(album_id)
        return {"data": [{"title": "Track", "track_position": 1}]}


def _tracklist_registry(monkeypatch, deezer):
    monkeypatch.setattr("core.metadata.registry.get_spotify_client", lambda: None)
    monkeypatch.setattr("core.metadata.registry.get_deezer_client", lambda: deezer)


def test_deezer_name_search_accepts_matching_edition_facts(monkeypatch):
    deezer = _DeezerTracklistClient({
        "release_date": "2020-09-04",
        "total_tracks": 12,
        "_raw_data": {"upc": "0194398123456"},
    })
    _tracklist_registry(monkeypatch, deezer)

    result = fetch_album_tracklist(
        "Album",
        "Artist",
        source_album_ids={"upc": "0-194398-123456"},
        release_date="2020",
        expected_track_count=12,
    )

    assert result is not None
    assert result.provider_entity_id == "dz-search"
    assert deezer.track_calls == ["dz-search"]


@pytest.mark.parametrize(
    ("metadata", "source_ids", "release_date", "track_count"),
    [
        ({"release_date": "2021-01-01", "total_tracks": 12}, {}, "2020", 12),
        ({"release_date": "2020-01-01", "total_tracks": 16}, {}, "2020", 12),
        (
            {"release_date": "2020", "total_tracks": 12, "_raw_data": {"upc": "2"}},
            {"upc": "1"},
            "2020",
            12,
        ),
        ({"release_date": "2020", "total_tracks": 12}, {"upc": "1"}, "2020", 12),
    ],
)
def test_deezer_name_search_rejects_conflicting_or_missing_edition_facts(
    monkeypatch, metadata, source_ids, release_date, track_count
):
    deezer = _DeezerTracklistClient(metadata)
    _tracklist_registry(monkeypatch, deezer)

    assert fetch_album_tracklist(
        "Album",
        "Artist",
        source_album_ids=source_ids,
        release_date=release_date,
        expected_track_count=track_count,
    ) is None
    assert deezer.track_calls == []


def test_direct_deezer_identity_does_not_use_name_search_validation(monkeypatch):
    class Deezer:
        def search_album(self, *_args):
            raise AssertionError("direct identity must not search")

        def get_album_tracks(self, album_id):
            assert album_id == "dz-exact"
            return {"data": [{"title": "Track", "track_position": 1}]}

    deezer = Deezer()
    _tracklist_registry(monkeypatch, deezer)

    result = fetch_album_tracklist(
        "Album",
        "Artist",
        source_album_ids={"deezer": "dz-exact"},
        release_date="1900",
        expected_track_count=999,
    )

    assert result is not None
    assert result.provider_entity_id == "dz-exact"


def test_artist_artwork_uses_explicit_source_identity(monkeypatch):
    calls = []

    def fake_get(artist_id, source_override=None, plugin=None, artist_name=None):
        calls.append((artist_id, source_override, artist_name))
        return " https://img.test/artist.jpg "

    monkeypatch.setattr(
        "core.metadata.artist_image.get_artist_image_url", fake_get
    )
    result = fetch_artwork_url(
        "artist",
        artist_name="Artist",
        source_ids={"spotify": "sp-artist", "musicbrainz": "mb-artist"},
    )

    assert calls == [("sp-artist", "spotify", "Artist")]
    assert result is not None
    assert result.source == "spotify"
    assert result.provider_entity_id == "sp-artist"
    assert result.url == "https://img.test/artist.jpg"


def test_artist_artwork_honors_configured_provider_order(monkeypatch):
    calls = []

    def fake_get(artist_id, source_override=None, plugin=None, artist_name=None):
        calls.append((artist_id, source_override))
        return "https://img.test/itunes.jpg"

    monkeypatch.setattr(
        "core.metadata.artist_image.get_artist_image_url", fake_get
    )
    result = fetch_artwork_url(
        "artist",
        artist_name="Artist",
        source_ids={"spotify": "sp-artist", "itunes": "it-artist"},
        source_order=("itunes", "spotify"),
    )

    assert calls == [("it-artist", "itunes")]
    assert result is not None
    assert result.source == "itunes"
    assert result.provider_entity_id == "it-artist"


def test_album_artwork_prefers_exact_musicbrainz_release_identity(monkeypatch):
    class Spotify:
        def get_album(self, *_args, **_kwargs):
            raise AssertionError("CAA priority must win before Spotify lookup")

    monkeypatch.setattr(
        "core.metadata.registry.get_client_for_source",
        lambda source: Spotify() if source == "spotify" else None,
    )
    result = fetch_artwork_url(
        "album",
        artist_name="Artist",
        album_title="Album",
        source_ids={"MusicBrainz": " mb-release ", "spotify": "sp-release"},
        source_order=("caa", "spotify"),
    )

    assert result is not None
    assert result.source == "caa"
    assert result.provider_entity_id == "mb-release"
    assert result.url == "https://coverartarchive.org/release/mb-release/front-1200"


def test_album_artwork_uses_exact_deezer_metadata_endpoint(monkeypatch):
    calls = []

    class Deezer:
        def get_album_metadata(self, album_id, include_tracks=True):
            calls.append((album_id, include_tracks))
            return {"images": [{"url": "https://img.test/deezer.jpg"}]}

    monkeypatch.setattr(
        "core.metadata.registry.get_client_for_source",
        lambda source: Deezer() if source == "deezer" else None,
    )
    result = fetch_artwork_url(
        "album",
        artist_name="Artist",
        album_title="Album",
        source_ids={"deezer": "dz-album"},
        source_order=("deezer",),
    )

    assert calls == [("dz-album", False)]
    assert result is not None
    assert result.source == "deezer"
    assert result.provider_entity_id == "dz-album"
    assert result.url == "https://img.test/deezer.jpg"


def test_direct_itunes_tracklist_keeps_itunes_track_identity(monkeypatch):
    class ITunes:
        def get_album_tracks(self, album_id):
            assert album_id == "it-album"
            return {"items": [{
                "id": "it-track", "name": "Track", "track_number": 1,
                "disc_number": 1, "duration_ms": 123000,
            }]}

    monkeypatch.setattr(
        "core.metadata.registry.get_client_for_source",
        lambda source: ITunes() if source == "itunes" else None,
    )
    result = fetch_album_tracklist(
        "Album", "Artist", source_album_ids={"itunes": "it-album"},
    )

    assert result is not None and result.provider == "itunes"
    assert result.track_payloads()[0]["external_ids"] == {"itunes": "it-track"}
    assert "spotify_id" not in result.track_payloads()[0]


def test_track_metadata_records_provider_that_actually_answered(monkeypatch):
    class EmptySpotify:
        def get_track_details(self, _track_id, **_kwargs):
            return None

    class Deezer:
        def get_track_details(self, track_id):
            assert track_id == "dz-track"
            return {"duration": 201, "album": {"cover_big": "https://img/dz.jpg"}}

    clients = {"spotify": EmptySpotify(), "deezer": Deezer()}
    monkeypatch.setattr(
        "core.metadata.registry.get_client_for_source", clients.get,
    )
    result = fetch_track_metadata(
        {"spotify": "sp-track", "deezer": "dz-track"},
        source_order=("spotify", "deezer"),
    )

    assert result is not None
    assert result.provider == "deezer"
    assert result.provider_entity_id == "dz-track"
    assert result.duration_ms == 201000


def test_artwork_adapter_rejects_unknown_kind():
    with pytest.raises(ValueError, match="artist or album"):
        fetch_artwork_url("track", artist_name="Artist")


def test_artwork_adapter_returns_none_for_incomplete_reference(monkeypatch):
    monkeypatch.setattr(
        "core.metadata.artist_image.get_artist_image_url",
        lambda *args, **kwargs: None,
    )
    assert fetch_artwork_url("artist", artist_name="Artist") is None
    assert fetch_artwork_url("album", artist_name="", album_title="Album") is None


def test_library_artwork_crosses_typed_boundary_with_effective_metadata(
    imported_conn, monkeypatch,
):
    artist_id = imported_conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    album_id = imported_conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]
    imported_conn.execute(
        "UPDATE lib2_artists SET external_ids=? WHERE id=?",
        ('{"deezer":"dz-artist"}', artist_id),
    )
    imported_conn.execute(
        "UPDATE lib2_albums SET external_ids=? WHERE id=?",
        ('{"itunes":"it-album"}', album_id),
    )
    set_field_override(
        imported_conn,
        entity_type="artist",
        entity_id=artist_id,
        field_name="name",
        value="Artist Corrected",
    )
    set_field_override(
        imported_conn,
        entity_type="release_group",
        entity_id=album_id,
        field_name="title",
        value="Album Corrected",
    )
    calls = []

    def fake_fetch(kind, **kwargs):
        calls.append((kind, kwargs))
        return ArtworkProviderResult(
            kind=kind,
            source="test",
            provider_entity_id=None,
            url=f"https://img.test/{kind}.jpg",
        )

    monkeypatch.setattr(
        "core.library2.provider_adapters.fetch_artwork_url", fake_fetch
    )

    assert _provider_art_url(imported_conn, "artist", artist_id).endswith(
        "/artist.jpg"
    )
    assert _provider_art_url(imported_conn, "album", album_id).endswith(
        "/album.jpg"
    )
    assert calls[0][1]["artist_name"] == "Artist Corrected"
    assert calls[0][1]["source_ids"]["deezer"] == "dz-artist"
    assert calls[1][1]["artist_name"] == "Artist Corrected"
    assert calls[1][1]["album_title"] == "Album Corrected"
    assert calls[1][1]["source_ids"]["itunes"] == "it-album"
