from __future__ import annotations

import pytest

from core.playback.prefetch import (
    deduplicate_prefetch_tracks,
    normalize_prefetch_track,
    track_identity,
)


def test_normalizes_provider_row_for_existing_download_worker():
    track = normalize_prefetch_track(
        {
            "title": "Teardrop",
            "artist": "Massive Attack",
            "album_title": "Mezzanine",
            "duration": 330,
            "source": "tidal",
            "source_track_id": "123",
            "_queue_request_id": "row-1",
        }
    )
    assert track["name"] == "Teardrop"
    assert track["artists"] == [{"name": "Massive Attack"}]
    assert track["album"] == "Mezzanine"
    assert track["duration_ms"] == 330_000
    assert track["id"] == "123"
    assert track["_queue_request_id"] == "row-1"


def test_provider_identity_is_stable_and_different_sources_do_not_collide():
    tidal = {"source": "tidal", "source_track_id": "42", "title": "X", "artist": "Y"}
    spotify = {"source": "spotify", "source_track_id": "42", "title": "X", "artist": "Y"}
    assert track_identity(tidal) == track_identity(dict(tidal))
    assert track_identity(tidal) != track_identity(spotify)


def test_deduplicates_repeated_queue_entries_but_keeps_all_request_ids():
    unique, request_ids = deduplicate_prefetch_tracks(
        [
            {"title": "Genesis", "artist": "Justice", "album": "Cross", "_queue_request_id": "a"},
            {"name": "Genesis", "artists": ["Justice"], "album": "Cross", "_queue_request_id": "b"},
        ]
    )
    assert len(unique) == 1
    key = unique[0]["_playback_queue_key"]
    assert request_ids[key] == ["a", "b"]


def test_rejects_missing_identity_metadata():
    with pytest.raises(ValueError, match="title and artist"):
        normalize_prefetch_track({"title": "Untitled"})
