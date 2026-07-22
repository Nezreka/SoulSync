"""Tests for typed provider-owned artist-discography outcomes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.metadata import registry as metadata_registry
from core.metadata.discography_providers import (
    MusicBrainzDiscographyProviderAdapter,
    StandardDiscographyProviderAdapter,
)
from core.metadata.discography_result import (
    DiscographyOutcome,
    DiscographyRequest,
    DiscographyStatus,
)
from core.metadata.discography_strict import get_artist_discography
from core.metadata.lookup import MetadataLookupOptions


def _album(album_id: str = "album-1"):
    return SimpleNamespace(
        id=album_id,
        name="Album One",
        release_date="2024-01-01",
        album_type="album",
        image_url=None,
        total_tracks=10,
        external_urls={},
        artist_ids=["artist-1"],
    )


class _StaticClient:
    def __init__(self, albums):
        self.albums = list(albums)
        self.calls = []

    def get_artist_albums(self, artist_id, **kwargs):
        self.calls.append((artist_id, dict(kwargs)))
        return list(self.albums)


class _MusicBrainzClient:
    def __init__(self, albums):
        self.albums = list(albums)
        self.calls = []

    def search_albums(self, query, limit=10):
        self.calls.append((query, limit))
        return list(self.albums)


def _configure_sources(monkeypatch, clients, priority):
    monkeypatch.setattr(
        metadata_registry,
        "get_primary_source",
        lambda spotify_client_factory=None: priority[0],
    )
    monkeypatch.setattr(
        metadata_registry,
        "get_source_priority",
        lambda primary_source: list(priority),
    )
    monkeypatch.setattr(
        metadata_registry,
        "get_client_for_source",
        lambda source, **kwargs: clients.get(source),
    )


def test_results_outcome_requires_releases():
    with pytest.raises(ValueError):
        DiscographyOutcome(
            status=DiscographyStatus.RESULTS,
            source="deezer",
        )


def test_empty_outcome_cannot_contain_releases():
    with pytest.raises(ValueError):
        DiscographyOutcome(
            status=DiscographyStatus.EMPTY,
            source="deezer",
            releases=(_album(),),
            status_code=404,
        )


def test_access_error_requires_error_status():
    with pytest.raises(ValueError):
        DiscographyOutcome.access_error(
            "deezer",
            "failure",
            status_code=200,
        )


def test_standard_adapter_returns_results():
    client = _StaticClient([_album()])
    outcome = StandardDiscographyProviderAdapter("deezer", client).load(
        DiscographyRequest(artist_id="artist-1")
    )

    assert outcome.status is DiscographyStatus.RESULTS
    assert [album.id for album in outcome.releases] == ["album-1"]


def test_standard_adapter_returns_confirmed_empty():
    client = _StaticClient([])
    outcome = StandardDiscographyProviderAdapter("deezer", client).load(
        DiscographyRequest(artist_id="artist-1")
    )

    assert outcome.status is DiscographyStatus.EMPTY
    assert outcome.releases == ()


def test_musicbrainz_uses_same_contract():
    client = _MusicBrainzClient([_album("mb-album")])
    outcome = MusicBrainzDiscographyProviderAdapter(
        "musicbrainz",
        client,
    ).load(
        DiscographyRequest(
            artist_id="mbid",
            artist_name="Artist One",
            limit=25,
        )
    )

    assert outcome.status is DiscographyStatus.RESULTS
    assert [album.id for album in outcome.releases] == ["mb-album"]
    assert client.calls == [("Artist One", 25)]


def test_explicit_provider_is_exclusive_even_when_fallback_allowed(monkeypatch):
    selected = _StaticClient([])
    fallback = _StaticClient([_album("fallback-album")])
    _configure_sources(
        monkeypatch,
        {"itunes": selected, "deezer": fallback},
        ["deezer", "itunes"],
    )

    result = get_artist_discography(
        "artist-1",
        artist_name="Artist One",
        options=MetadataLookupOptions(
            source_override="itunes",
            allow_fallback=True,
        ),
    )

    assert result["state"] == "empty"
    assert result["source"] == "itunes"
    assert result["source_priority"] == ["itunes"]
    assert len(selected.calls) == 1
    assert fallback.calls == []


def test_automatic_mode_continues_after_confirmed_empty(monkeypatch):
    primary = _StaticClient([])
    fallback = _StaticClient([_album("fallback-album")])
    _configure_sources(
        monkeypatch,
        {"itunes": primary, "deezer": fallback},
        ["itunes", "deezer"],
    )

    result = get_artist_discography(
        "artist-1",
        artist_name="Artist One",
        options=MetadataLookupOptions(),
    )

    assert result["state"] == "results"
    assert result["source"] == "deezer"
    assert [album["id"] for album in result["albums"]] == ["fallback-album"]


def test_unavailable_explicit_provider_is_error(monkeypatch):
    _configure_sources(monkeypatch, {}, ["deezer"])

    result = get_artist_discography(
        "artist-1",
        artist_name="Artist One",
        options=MetadataLookupOptions(source_override="musicbrainz"),
    )

    assert result["state"] == "error"
    assert result["source"] == "musicbrainz"
    assert result["status_code"] == 503
