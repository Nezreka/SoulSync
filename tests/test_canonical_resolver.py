"""Tests for resolve_canonical_for_album (#765 Stage 2 — injectable core)."""

from __future__ import annotations

from core.metadata.canonical_resolver import resolve_canonical_for_album

STD = [{"duration_ms": 180_000 + i * 10_000, "title": f"Song {i+1}"} for i in range(11)]
DLX = STD + [{"duration_ms": 300_000 + i * 10_000, "title": f"Bonus {i+1}"} for i in range(6)]

PRIORITY = ["spotify", "itunes", "deezer", "musicbrainz"]


def _fetcher(table):
    """fetch_tracklist backed by a {(source, album_id): tracks} table."""
    def fetch(source, album_id):
        return table.get((source, album_id))
    return fetch


def test_picks_source_whose_release_fits_the_files():
    files = list(STD)  # user owns the 11-track standard
    table = {
        ("spotify", "sp_deluxe"): DLX,     # spotify linked to deluxe (17)
        ("musicbrainz", "mb_std"): STD,    # musicbrainz has standard (11)
    }
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "sp_deluxe", "musicbrainz": "mb_std"},
        file_tracks=files,
        fetch_tracklist=_fetcher(table),
        source_priority=PRIORITY,
    )
    # Best FIT wins over priority — standard matches the files, deluxe doesn't.
    assert out == {"source": "musicbrainz", "album_id": "mb_std", "score": out["score"]}
    assert out["score"] > 0.9


def test_priority_breaks_tie_between_equal_fits():
    files = list(STD)
    table = {("spotify", "a"): STD, ("itunes", "b"): STD}  # identical fit
    out = resolve_canonical_for_album(
        album_source_ids={"itunes": "b", "spotify": "a"},
        file_tracks=files,
        fetch_tracklist=_fetcher(table),
        source_priority=PRIORITY,  # spotify before itunes
    )
    assert out["source"] == "spotify"


def test_skips_sources_without_ids_or_failed_fetch():
    files = list(STD)

    def fetch(source, album_id):
        if source == "spotify":
            raise RuntimeError("API down")
        if source == "deezer":
            return STD
        return None

    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "x", "deezer": "y"},  # no itunes id
        file_tracks=files,
        fetch_tracklist=fetch,
        source_priority=PRIORITY,
    )
    assert out["source"] == "deezer"


def test_none_when_no_candidates():
    out = resolve_canonical_for_album(
        album_source_ids={},
        file_tracks=list(STD),
        fetch_tracklist=_fetcher({}),
        source_priority=PRIORITY,
    )
    assert out is None


def test_none_when_no_files():
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "a"},
        file_tracks=[],
        fetch_tracklist=_fetcher({("spotify", "a"): STD}),
        source_priority=PRIORITY,
    )
    assert out is None


def test_none_when_below_floor():
    files = list(STD)  # 11 tracks
    # Only candidate is a wildly-wrong 3-track release.
    table = {("spotify", "a"): [{"duration_ms": 60_000, "title": "X"}] * 3}
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "a"},
        file_tracks=files,
        fetch_tracklist=_fetcher(table),
        source_priority=PRIORITY,
    )
    assert out is None


def test_score_is_rounded():
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "a"},
        file_tracks=list(STD),
        fetch_tracklist=_fetcher({("spotify", "a"): STD}),
        source_priority=PRIORITY,
    )
    assert out["score"] == round(out["score"], 4)
