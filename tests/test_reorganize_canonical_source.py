"""_resolve_source honors a pinned canonical release (#765 Stage 3, read side).

Gated + side-effect-free: only changes behavior for albums that already carry a
canonical_source/canonical_album_id, and an explicit user source pick
(strict_source) still wins. No canonical -> byte-identical to before.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import core.library_reorganize as lr
import core.metadata.registry as metadata_registry
from core.musicbrainz_search import MusicBrainzSearchClient


def _patch_fetch(monkeypatch, tracklists):
    """tracklists: {(source, album_id): items_or_None}. Patches the album +
    tracklist fetchers and the normaliser (pass-through)."""
    def get_album(source, aid):
        return {"name": f"{source}:{aid}"} if tracklists.get((source, aid)) else None

    def get_tracks(source, aid):
        return tracklists.get((source, aid))

    monkeypatch.setattr(lr, "get_album_for_source", get_album)
    monkeypatch.setattr(lr, "get_album_tracks_for_source", get_tracks)
    monkeypatch.setattr(lr, "_normalize_album_tracks", lambda items: items or [])
    monkeypatch.setattr(lr, "get_source_priority", lambda primary: ["spotify", "itunes", "deezer"])


def test_canonical_source_preferred_over_priority(monkeypatch):
    # Album has spotify (priority winner) AND a pinned canonical = deezer.
    _patch_fetch(monkeypatch, {
        ("spotify", "sp1"): [{"name": "x"}],
        ("deezer", "dz1"): [{"name": "y"}],
    })
    album_data = {
        "spotify_album_id": "sp1", "deezer_id": "dz1",
        "canonical_source": "deezer", "canonical_album_id": "dz1",
    }
    source, api_album, items = lr._resolve_source(album_data, "spotify")
    assert source == "deezer"  # canonical beats the priority walk


def test_canonical_fetch_failure_falls_back_to_priority(monkeypatch):
    # Canonical points at musicbrainz but that fetch yields nothing -> fall back.
    _patch_fetch(monkeypatch, {
        ("spotify", "sp1"): [{"name": "x"}],
        # no entry for ('musicbrainz', 'mb1') -> get_tracks returns None
    })
    album_data = {
        "spotify_album_id": "sp1",
        "canonical_source": "musicbrainz", "canonical_album_id": "mb1",
    }
    source, _, _ = lr._resolve_source(album_data, "spotify")
    assert source == "spotify"  # fell back to priority


def test_strict_source_ignores_canonical(monkeypatch):
    # User explicitly picked spotify in the modal — their choice wins over canonical.
    _patch_fetch(monkeypatch, {
        ("spotify", "sp1"): [{"name": "x"}],
        ("deezer", "dz1"): [{"name": "y"}],
    })
    album_data = {
        "spotify_album_id": "sp1", "deezer_id": "dz1",
        "canonical_source": "deezer", "canonical_album_id": "dz1",
    }
    source, _, _ = lr._resolve_source(album_data, "spotify", strict_source=True)
    assert source == "spotify"


def test_no_canonical_unchanged(monkeypatch):
    # No canonical set -> identical to legacy priority resolution.
    _patch_fetch(monkeypatch, {("spotify", "sp1"): [{"name": "x"}]})
    album_data = {"spotify_album_id": "sp1"}
    source, _, _ = lr._resolve_source(album_data, "spotify")
    assert source == "spotify"


def test_musicbrainz_release_id_is_used_by_priority_walk(monkeypatch):
    client = MusicBrainzSearchClient()
    client._client = MagicMock()
    client._client.get_release_group.return_value = None
    client._client.get_release.return_value = {
        "id": "mb-release-1",
        "title": "Test Album",
        "date": "2024-01-01",
        "artist-credit": [{"name": "Test Artist"}],
        "release-group": {
            "id": "mb-group-1",
            "primary-type": "Album",
            "secondary-types": [],
        },
        "media": [
            {
                "position": 1,
                "tracks": [
                    {
                        "id": "track-1",
                        "number": "1",
                        "position": 1,
                        "length": 180000,
                        "recording": {
                            "id": "recording-1",
                            "title": "Test Track",
                            "artist-credit": [{"name": "Test Artist"}],
                            "length": 180000,
                        },
                    },
                ],
            },
        ],
    }

    monkeypatch.setattr(
        metadata_registry,
        "get_musicbrainz_client",
        lambda *args, **kwargs: client,
    )
    monkeypatch.setattr(
        lr,
        "get_source_priority",
        lambda primary: ["musicbrainz", "spotify"],
    )

    album_data = {
        "musicbrainz_release_id": "mb-release-1",
    }

    source, api_album, items = lr._resolve_source(
        album_data,
        "musicbrainz",
    )

    assert source == "musicbrainz"
    assert api_album["id"] == "mb-release-1"
    assert api_album["name"] == "Test Album"
    assert items == api_album["tracks"]
    assert items == [
        {
            "id": "recording-1",
            "name": "Test Track",
            "artists": [{"name": "Test Artist"}],
            "duration_ms": 180000,
            "track_number": 1,
            "disc_number": 1,
        },
    ]
    client._client.get_release_group.assert_any_call(
        "mb-release-1",
        includes=["releases", "artist-credits"],
    )
    client._client.get_release.assert_any_call(
        "mb-release-1",
        includes=["recordings", "artist-credits", "release-groups"],
    )
