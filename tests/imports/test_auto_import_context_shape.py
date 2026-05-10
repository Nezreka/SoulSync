"""Pin the post-process context dict the auto-import worker hands to
``_post_process_matched_download``.

Background
----------

Auto-import doesn't write to the SoulSync standalone DB itself —
it routes every matched track through the same
``_post_process_matched_download`` callback the regular download
flow uses. The pipeline downstream (``record_soulsync_library_entry``,
``record_library_history_download``, ``record_download_provenance``)
reads:

- ``context["source"]`` — picks the right source-id columns
  (``spotify_track_id`` / ``deezer_id`` / ``itunes_track_id`` / etc.)
- ``context["_download_username"]`` — labels the row in library
  history + provenance ("Auto-Import" instead of falling back to the
  Soulseek default).
- ``context["track_info"]["musicbrainz_recording_id"]`` and
  ``context["track_info"]["isrc"]`` — per-recording IDs that land on
  the dedicated ``musicbrainz_recording_id`` / ``isrc`` track columns.

If the worker drops any of these, the soulsync library row gets
written but with NULL on every source-id column, and library history
mislabels every imported file as a Soulseek download. SoulSync
standalone is meant to be a full server replacement so it must reach
parity with what a Plex / Jellyfin / Navidrome scan would write. These
tests pin that contract at the worker boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest


@dataclass
class _FakeCandidate:
    path: str
    name: str
    audio_files: List[str] = field(default_factory=list)
    disc_structure: Dict[int, List[str]] = field(default_factory=dict)
    folder_hash: str = "fake-hash"
    is_single: bool = False


@pytest.fixture
def worker_with_capture(tmp_path):
    """Worker whose ``process_callback`` captures the per-track context
    dict so the test can assert on its shape directly."""
    from core.auto_import_worker import AutoImportWorker

    captured: List[Dict[str, Any]] = []
    fake_db = MagicMock()
    fake_cfg = MagicMock()
    fake_cfg.get.side_effect = lambda key, default=None: default

    def _capture(_key, ctx, _path):
        captured.append(ctx)

    worker = AutoImportWorker(
        database=fake_db,
        staging_path=str(tmp_path),
        transfer_path=str(tmp_path / "transfer"),
        process_callback=_capture,
        config_manager=fake_cfg,
        automation_engine=None,
    )
    worker._captured = captured
    return worker


def _make_match_result(source: str, track_count: int = 1) -> Dict[str, Any]:
    return {
        "matches": [],  # filled by tests
        "unmatched_files": [],
        "total_tracks": track_count,
        "matched_count": track_count,
        "confidence": 0.95,
        "album_data": {
            "id": "ALBUM-ID-FROM-SOURCE",
            "total_tracks": track_count,
            "album_type": "album",
            "release_date": "2024-01-01",
            "images": [{"url": "https://img.example/cover.jpg"}],
            "artists": [{"name": "A", "id": "ARTIST-ID-FROM-SOURCE"}],
        },
    }


def _make_identification(source: str = "deezer") -> Dict[str, Any]:
    return {
        "source": source,
        "artist_name": "A",
        "artist_id": "ARTIST-ID-FROM-SOURCE",
        "album_name": "Album",
        "album_id": "ALBUM-ID-FROM-SOURCE",
        "image_url": "https://img.example/cover.jpg",
        "release_date": "2024-01-01",
        "method": "tags",
    }


# ---------------------------------------------------------------------------
# context["source"] propagation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["spotify", "deezer", "itunes", "discogs"])
def test_context_carries_source(worker_with_capture, tmp_path, source):
    """Worker must propagate ``identification['source']`` onto the
    top-level context. Without it, ``record_soulsync_library_entry``
    can't pick a source column and writes the row with NULL on every
    source-id field."""
    f = tmp_path / "01.flac"
    f.write_bytes(b"audio")
    cand = _FakeCandidate(path=str(tmp_path), name="Album")
    ident = _make_identification(source=source)
    mr = _make_match_result(source, 1)
    mr["matches"] = [{
        "track": {"id": "t1", "name": "Track", "track_number": 1,
                  "disc_number": 1, "duration_ms": 200000,
                  "artists": [{"name": "A"}]},
        "file": str(f), "confidence": 0.95,
    }]

    worker_with_capture._process_matches(cand, ident, mr)

    ctx = worker_with_capture._captured[0]
    assert ctx["source"] == source, (
        f"Expected context['source']={source!r}, got {ctx.get('source')!r}. "
        f"Without this, soulsync library writes the row with NULL on "
        f"{source}_track_id."
    )


# ---------------------------------------------------------------------------
# Auto-import labels: history + provenance must NOT default to Soulseek
# ---------------------------------------------------------------------------


def test_context_marks_download_username_as_auto_import(worker_with_capture, tmp_path):
    """``_download_username='auto_import'`` is what triggers the
    "Auto-Import" / "auto_import" branch in side_effects.py source maps.
    Without it, every imported file is labelled as a Soulseek download
    in library history + provenance — false signal in the UI."""
    f = tmp_path / "01.flac"
    f.write_bytes(b"audio")
    cand = _FakeCandidate(path=str(tmp_path), name="Album")
    ident = _make_identification("spotify")
    mr = _make_match_result("spotify", 1)
    mr["matches"] = [{
        "track": {"id": "t1", "name": "Track", "track_number": 1,
                  "disc_number": 1, "duration_ms": 200000,
                  "artists": [{"name": "A"}]},
        "file": str(f), "confidence": 0.95,
    }]

    worker_with_capture._process_matches(cand, ident, mr)

    ctx = worker_with_capture._captured[0]
    assert ctx.get("_download_username") == "auto_import"


# ---------------------------------------------------------------------------
# Per-recording IDs flow through to track_info
# ---------------------------------------------------------------------------


def test_context_propagates_isrc_and_mbid_when_present(worker_with_capture, tmp_path):
    """When the metadata-source response carries per-recording IDs
    (Picard-tagged libraries always have MBID, MusicBrainz-enriched
    Spotify carries ISRC), they must end up on
    context['track_info']['isrc'] / ['musicbrainz_recording_id'] so
    the soulsync library row writes them onto dedicated columns."""
    f = tmp_path / "01.flac"
    f.write_bytes(b"audio")
    cand = _FakeCandidate(path=str(tmp_path), name="Album")
    ident = _make_identification("spotify")
    mr = _make_match_result("spotify", 1)
    mr["matches"] = [{
        "track": {
            "id": "spotify-track-id",
            "name": "Track",
            "track_number": 1,
            "disc_number": 1,
            "duration_ms": 200000,
            "artists": [{"name": "A"}],
            "isrc": "USABC1234567",
            "musicbrainz_recording_id": "abcd1234-mbid-uuid-form",
        },
        "file": str(f), "confidence": 0.95,
    }]

    worker_with_capture._process_matches(cand, ident, mr)

    ti = worker_with_capture._captured[0]["track_info"]
    assert ti["isrc"] == "USABC1234567"
    assert ti["musicbrainz_recording_id"] == "abcd1234-mbid-uuid-form"


def test_context_per_recording_ids_default_empty_when_missing(worker_with_capture, tmp_path):
    """Missing IDs default to empty string, NOT to None — the side-
    effects layer normalises to None at write time. Empty string keeps
    the field present in the dict so downstream code that does
    `track_info.get("isrc")` doesn't have to special-case missing keys."""
    f = tmp_path / "01.flac"
    f.write_bytes(b"audio")
    cand = _FakeCandidate(path=str(tmp_path), name="Album")
    ident = _make_identification("deezer")
    mr = _make_match_result("deezer", 1)
    mr["matches"] = [{
        "track": {"id": "111", "name": "Track", "track_number": 1,
                  "disc_number": 1, "duration_ms": 200000,
                  "artists": [{"name": "A"}]},  # no isrc / mbid
        "file": str(f), "confidence": 0.95,
    }]

    worker_with_capture._process_matches(cand, ident, mr)

    ti = worker_with_capture._captured[0]["track_info"]
    assert ti.get("isrc") == ""
    assert ti.get("musicbrainz_recording_id") == ""


# ---------------------------------------------------------------------------
# Album back-reference on track_info
# ---------------------------------------------------------------------------


def test_track_info_includes_album_id_back_reference(worker_with_capture, tmp_path):
    """`get_import_source_ids` reads `track_info.album_id` as one of the
    fallback paths for resolving the album-source-id. Without the back
    reference, sources whose API response nests album under
    `track.album.id` fall through and the soulsync row writes NULL on
    the album-source-id column."""
    f = tmp_path / "01.flac"
    f.write_bytes(b"audio")
    cand = _FakeCandidate(path=str(tmp_path), name="Album")
    ident = _make_identification("spotify")
    mr = _make_match_result("spotify", 1)
    mr["matches"] = [{
        "track": {"id": "t1", "name": "Track", "track_number": 1,
                  "disc_number": 1, "duration_ms": 200000,
                  "artists": [{"name": "A"}]},
        "file": str(f), "confidence": 0.95,
    }]

    worker_with_capture._process_matches(cand, ident, mr)

    ti = worker_with_capture._captured[0]["track_info"]
    assert ti.get("album_id") == "ALBUM-ID-FROM-SOURCE"
