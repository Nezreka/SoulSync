"""Pin auto-import multi-source fallback for album identification.

Discord report (mushy, paraphrased): 16 Bandcamp albums sat in staging
because auto-import couldn't identify them. Manual search at the
bottom of the Import Music tab found the same albums fine via Tidal
or Deezer — the user's primary metadata source (Spotify) just didn't
have them.

Root cause: `_search_metadata_source` only queried the primary source.
The manual `search_import_albums` path already iterates the full
`get_source_priority(get_primary_source())` chain and breaks on first
source that returns results. This brings auto-import to parity.

Fix semantics (option C — "primary first, fall through on weak"):
  - Try primary source first
  - Score result; if best ≥ 0.4 → return with that source
  - Otherwise fall through to next source in priority order
  - First source that produces a result above threshold wins
  - Returns None only if ALL sources fail / score below threshold

Tests pin:
  - Primary success path unchanged (returns primary result, no fallback fired)
  - Primary returns nothing → fallback fires to next source
  - Primary scores below threshold → fallback fires
  - First fallback succeeds → no further sources queried
  - All sources fail → None
  - Per-source exception is contained (doesn't abort the chain)
  - Result `source` field reflects WHICH source actually matched
  - `identification_confidence` is the score from the winning source
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.auto_import_worker import AutoImportWorker, FolderCandidate


def _make_album(name: str, artist_name: str, total_tracks: int,
                album_id: str = "alb-id", artist_id: str = "art-id"):
    """Build a fake album result matching `search_albums` return shape."""
    return SimpleNamespace(
        id=album_id,
        name=name,
        artists=[{"id": artist_id, "name": artist_name}],
        total_tracks=total_tracks,
        image_url="https://img.example/cover.jpg",
        release_date="2024-01-01",
    )


def _make_worker():
    """Bare AutoImportWorker bypassing __init__ side effects."""
    return AutoImportWorker(database=MagicMock(), process_callback=lambda *a, **k: None)


def _make_candidate(file_count: int = 7, name: str = "TestAlbum"):
    """Folder candidate with N files (no actual disk reads)."""
    return FolderCandidate(
        path=f"/staging/{name}",
        name=name,
        audio_files=[f"/staging/{name}/{i:02d}.flac" for i in range(1, file_count + 1)],
    )


# ---------------------------------------------------------------------------
# Primary success path — fallback never fires
# ---------------------------------------------------------------------------


class TestPrimarySuccess:
    def test_primary_returns_strong_match_no_fallback(self):
        """Pre-fix behavior preserved: if primary scores above 0.4,
        return its result and don't touch other sources."""
        worker = _make_worker()
        candidate = _make_candidate(file_count=10)

        spotify_client = MagicMock()
        spotify_client.search_albums.return_value = [
            _make_album("Test Album", "Test Artist", total_tracks=10),
        ]
        tidal_client = MagicMock()  # should NEVER be called

        def client_dispatch(source, **kwargs):
            return {"spotify": spotify_client, "tidal": tidal_client}.get(source)

        with patch("core.metadata_service.get_primary_source", return_value="spotify"), \
             patch("core.metadata_service.get_source_priority",
                   return_value=["spotify", "tidal", "deezer"]), \
             patch("core.metadata_service.get_client_for_source",
                   side_effect=client_dispatch):
            result = worker._search_metadata_source(
                "Test Artist", "Test Album", "tags", candidate,
            )

        assert result is not None
        assert result["source"] == "spotify"
        assert result["album_name"] == "Test Album"
        spotify_client.search_albums.assert_called_once()
        tidal_client.search_albums.assert_not_called()


# ---------------------------------------------------------------------------
# Primary fails — fallback fires
# ---------------------------------------------------------------------------


class TestFallbackOnNoResults:
    def test_primary_empty_falls_through_to_next_source(self):
        """Reporter's exact case: Spotify doesn't have the Bandcamp
        indie album. Tidal does. Auto-import must find it via Tidal."""
        worker = _make_worker()
        candidate = _make_candidate(file_count=7)

        spotify_client = MagicMock()
        spotify_client.search_albums.return_value = []  # not on Spotify
        tidal_client = MagicMock()
        tidal_client.search_albums.return_value = [
            _make_album("Work in Progress", "Godly the Ruler", total_tracks=7,
                        album_id="tidal-alb-1", artist_id="tidal-art-1"),
        ]

        def client_dispatch(source, **kwargs):
            return {"spotify": spotify_client, "tidal": tidal_client}.get(source)

        with patch("core.metadata_service.get_primary_source", return_value="spotify"), \
             patch("core.metadata_service.get_source_priority",
                   return_value=["spotify", "tidal", "deezer"]), \
             patch("core.metadata_service.get_client_for_source",
                   side_effect=client_dispatch):
            result = worker._search_metadata_source(
                "Godly the Ruler", "Work in Progress", "tags", candidate,
            )

        assert result is not None
        assert result["source"] == "tidal", "Result must carry the source that actually matched"
        assert result["album_id"] == "tidal-alb-1"
        assert result["artist_id"] == "tidal-art-1"
        spotify_client.search_albums.assert_called_once()
        tidal_client.search_albums.assert_called_once()


class TestFallbackOnWeakScore:
    def test_primary_below_threshold_falls_through(self):
        """Primary returns results but none score above 0.4 (e.g.
        wrong-album false-matches). Fall through to next source for
        a stronger match."""
        worker = _make_worker()
        candidate = _make_candidate(file_count=7)

        spotify_client = MagicMock()
        # Wrong album — name barely matches, no artist match, wrong track count
        spotify_client.search_albums.return_value = [
            _make_album("Different", "Wrong Artist", total_tracks=2),
        ]
        deezer_client = MagicMock()
        deezer_client.search_albums.return_value = [
            _make_album("Work in Progress", "Godly the Ruler", total_tracks=7),
        ]

        def client_dispatch(source, **kwargs):
            return {"spotify": spotify_client, "deezer": deezer_client}.get(source)

        with patch("core.metadata_service.get_primary_source", return_value="spotify"), \
             patch("core.metadata_service.get_source_priority",
                   return_value=["spotify", "deezer"]), \
             patch("core.metadata_service.get_client_for_source",
                   side_effect=client_dispatch):
            result = worker._search_metadata_source(
                "Godly the Ruler", "Work in Progress", "tags", candidate,
            )

        assert result is not None
        assert result["source"] == "deezer"
        assert result["album_name"] == "Work in Progress"
        # Both clients called — primary returned weak, fallback picked up
        spotify_client.search_albums.assert_called_once()
        deezer_client.search_albums.assert_called_once()


# ---------------------------------------------------------------------------
# Chain semantics
# ---------------------------------------------------------------------------


class TestChainSemantics:
    def test_first_fallback_success_stops_chain(self):
        """When fallback succeeds, no further sources are queried.
        Don't waste API budget on Deezer if Tidal already gave us a
        strong result."""
        worker = _make_worker()
        candidate = _make_candidate(file_count=10)

        spotify_client = MagicMock()
        spotify_client.search_albums.return_value = []
        tidal_client = MagicMock()
        tidal_client.search_albums.return_value = [
            _make_album("Test", "Artist", total_tracks=10),
        ]
        deezer_client = MagicMock()  # should NEVER be called

        def client_dispatch(source, **kwargs):
            return {"spotify": spotify_client,
                    "tidal": tidal_client,
                    "deezer": deezer_client}.get(source)

        with patch("core.metadata_service.get_primary_source", return_value="spotify"), \
             patch("core.metadata_service.get_source_priority",
                   return_value=["spotify", "tidal", "deezer"]), \
             patch("core.metadata_service.get_client_for_source",
                   side_effect=client_dispatch):
            result = worker._search_metadata_source("Artist", "Test", "tags", candidate)

        assert result is not None
        assert result["source"] == "tidal"
        deezer_client.search_albums.assert_not_called()

    def test_all_sources_fail_returns_none(self):
        """If every source returns nothing or scores below threshold,
        the whole search returns None (caller proceeds to next
        identification strategy)."""
        worker = _make_worker()
        candidate = _make_candidate(file_count=7)

        empty_client = MagicMock()
        empty_client.search_albums.return_value = []

        with patch("core.metadata_service.get_primary_source", return_value="spotify"), \
             patch("core.metadata_service.get_source_priority",
                   return_value=["spotify", "tidal", "deezer"]), \
             patch("core.metadata_service.get_client_for_source",
                   return_value=empty_client):
            result = worker._search_metadata_source(
                "Unknown Artist", "Nonexistent Album", "tags", candidate,
            )

        assert result is None
        # All 3 sources got queried
        assert empty_client.search_albums.call_count == 3

    def test_per_source_exception_does_not_abort_chain(self):
        """If one source raises (rate limit, auth, transient HTTP),
        the chain continues to the next source instead of aborting
        the whole identification attempt."""
        worker = _make_worker()
        candidate = _make_candidate(file_count=10)

        spotify_client = MagicMock()
        spotify_client.search_albums.side_effect = RuntimeError("rate limit")
        tidal_client = MagicMock()
        tidal_client.search_albums.return_value = [
            _make_album("Test", "Artist", total_tracks=10),
        ]

        def client_dispatch(source, **kwargs):
            return {"spotify": spotify_client, "tidal": tidal_client}.get(source)

        with patch("core.metadata_service.get_primary_source", return_value="spotify"), \
             patch("core.metadata_service.get_source_priority",
                   return_value=["spotify", "tidal"]), \
             patch("core.metadata_service.get_client_for_source",
                   side_effect=client_dispatch):
            result = worker._search_metadata_source("Artist", "Test", "tags", candidate)

        assert result is not None
        assert result["source"] == "tidal", (
            "Primary exception must not block the fallback chain"
        )

    def test_unconfigured_source_skipped_gracefully(self):
        """If `get_client_for_source` returns None for a source
        (user hasn't configured it), skip and continue."""
        worker = _make_worker()
        candidate = _make_candidate(file_count=10)

        tidal_client = MagicMock()
        tidal_client.search_albums.return_value = [
            _make_album("Test", "Artist", total_tracks=10),
        ]

        # Spotify returns None (no client configured); Tidal works
        def client_dispatch(source, **kwargs):
            if source == "spotify":
                return None
            return {"tidal": tidal_client}.get(source)

        with patch("core.metadata_service.get_primary_source", return_value="spotify"), \
             patch("core.metadata_service.get_source_priority",
                   return_value=["spotify", "tidal"]), \
             patch("core.metadata_service.get_client_for_source",
                   side_effect=client_dispatch):
            result = worker._search_metadata_source("Artist", "Test", "tags", candidate)

        assert result is not None
        assert result["source"] == "tidal"


# ---------------------------------------------------------------------------
# Result shape preservation
# ---------------------------------------------------------------------------


class TestResultShape:
    def test_result_carries_correct_source_for_downstream_match(self):
        """`_match_tracks` reads `identification['source']` to know
        which client to ask for the album's tracklist. Result MUST
        carry the source that actually matched, not the primary
        source name."""
        worker = _make_worker()
        candidate = _make_candidate(file_count=8)

        spotify_client = MagicMock()
        spotify_client.search_albums.return_value = []
        deezer_client = MagicMock()
        deezer_client.search_albums.return_value = [
            _make_album("Test", "Artist", total_tracks=8, album_id="dz-123"),
        ]

        def client_dispatch(source, **kwargs):
            return {"spotify": spotify_client, "deezer": deezer_client}.get(source)

        with patch("core.metadata_service.get_primary_source", return_value="spotify"), \
             patch("core.metadata_service.get_source_priority",
                   return_value=["spotify", "deezer"]), \
             patch("core.metadata_service.get_client_for_source",
                   side_effect=client_dispatch):
            result = worker._search_metadata_source("Artist", "Test", "tags", candidate)

        assert result["source"] == "deezer"
        assert result["album_id"] == "dz-123", (
            "Album ID must be the Deezer ID so _match_tracks queries "
            "Deezer's get_album with the right ID format"
        )

    def test_identification_confidence_reflects_winning_source(self):
        """`identification_confidence` is used in the overall-confidence
        formula and the 0.9 / 0.7 cascade thresholds. It must be the
        score from the source that actually matched."""
        worker = _make_worker()
        candidate = _make_candidate(file_count=10)

        spotify_client = MagicMock()
        spotify_client.search_albums.return_value = []
        # Perfect match on Tidal — all 3 weights at max → score = 1.0
        tidal_client = MagicMock()
        tidal_client.search_albums.return_value = [
            _make_album("Test Album", "Test Artist", total_tracks=10),
        ]

        def client_dispatch(source, **kwargs):
            return {"spotify": spotify_client, "tidal": tidal_client}.get(source)

        with patch("core.metadata_service.get_primary_source", return_value="spotify"), \
             patch("core.metadata_service.get_source_priority",
                   return_value=["spotify", "tidal"]), \
             patch("core.metadata_service.get_client_for_source",
                   side_effect=client_dispatch):
            result = worker._search_metadata_source(
                "Test Artist", "Test Album", "tags", candidate,
            )

        assert result["identification_confidence"] == pytest.approx(1.0, abs=0.01)
