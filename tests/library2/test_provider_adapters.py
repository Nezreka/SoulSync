"""Typed Library-v2 boundaries for metadata-provider facades."""

from __future__ import annotations

import pytest

from core.library2.artwork import _provider_art_url
from core.library2.metadata_overrides import set_field_override
from core.library2.provider_adapters import ArtworkProviderResult, fetch_artwork_url


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


def test_album_artwork_reuses_shared_resolver_and_normalizes_result(monkeypatch):
    captured = {}

    def fake_select(artist, album, metadata, order, validate=None):
        captured.update(
            artist=artist, album=album, metadata=metadata, order=order
        )
        return "https://img.test/album.jpg", "caa"

    monkeypatch.setattr("core.metadata.art_lookup.select_preferred_art", fake_select)
    result = fetch_artwork_url(
        "album",
        artist_name="Artist",
        album_title="Album",
        source_ids={"MusicBrainz": " mb-release ", "spotify": "sp-release"},
        source_order=("caa", "spotify"),
    )

    assert captured == {
        "artist": "Artist",
        "album": "Album",
        "metadata": {"musicbrainz_release_id": "mb-release"},
        "order": ("caa", "spotify"),
    }
    assert result is not None
    assert result.source == "caa"
    assert result.provider_entity_id is None
    assert result.url == "https://img.test/album.jpg"


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
