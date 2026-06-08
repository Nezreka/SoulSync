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


def test_best_fit_mode_picks_best_regardless_of_priority():
    files = list(STD)  # user owns the 11-track standard
    table = {
        ("spotify", "sp_deluxe"): DLX,     # spotify (primary) linked to deluxe (17)
        ("musicbrainz", "mb_std"): STD,    # musicbrainz has standard (11)
    }
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "sp_deluxe", "musicbrainz": "mb_std"},
        file_tracks=files,
        fetch_tracklist=_fetcher(table),
        source_priority=PRIORITY,
        mode="best_fit",
    )
    # best_fit: standard matches the files, deluxe doesn't — fit beats priority.
    assert out["source"] == "musicbrainz" and out["album_id"] == "mb_std"
    assert out["score"] > 0.9


# ── source-selection modes ────────────────────────────────────────────────

def test_active_preferred_uses_primary_when_it_fits():
    files = list(STD)
    table = {("spotify", "sp1"): STD, ("musicbrainz", "mb1"): STD}  # both fit
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "sp1", "musicbrainz": "mb1"},
        file_tracks=files, fetch_tracklist=_fetcher(table),
        source_priority=PRIORITY,  # primary = spotify
    )  # default mode = active_preferred
    assert out["source"] == "spotify"


def test_active_preferred_falls_back_when_primary_clearly_misfits():
    files = list(STD)  # 11 tracks
    table = {
        ("spotify", "sp_bad"): [{"duration_ms": 60_000, "title": "X"}] * 3,  # 3-track, <floor
        ("musicbrainz", "mb_std"): STD,
    }
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "sp_bad", "musicbrainz": "mb_std"},
        file_tracks=files, fetch_tracklist=_fetcher(table),
        source_priority=PRIORITY, mode="active_preferred",
    )
    # primary spotify scores below floor -> fall back to the fitting source.
    assert out["source"] == "musicbrainz"


def test_active_preferred_keeps_primary_even_if_another_fits_better():
    files = list(STD)
    # primary spotify is a deluxe (decent fit, above floor); musicbrainz is exact.
    table = {("spotify", "sp_dlx"): DLX, ("musicbrainz", "mb_std"): STD}
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "sp_dlx", "musicbrainz": "mb_std"},
        file_tracks=files, fetch_tracklist=_fetcher(table),
        source_priority=PRIORITY, mode="active_preferred",
    )
    # active_preferred respects the active source as long as it clears the floor,
    # even though musicbrainz would fit better (use best_fit for that).
    assert out["source"] == "spotify"


def test_active_only_pins_primary_and_never_falls_back():
    files = list(STD)
    # primary spotify is below floor; a perfect musicbrainz exists but is ignored.
    table = {
        ("spotify", "sp_bad"): [{"duration_ms": 60_000, "title": "X"}] * 3,
        ("musicbrainz", "mb_std"): STD,
    }
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "sp_bad", "musicbrainz": "mb_std"},
        file_tracks=files, fetch_tracklist=_fetcher(table),
        source_priority=PRIORITY, mode="active_only",
    )
    assert out is None  # primary didn't fit, and active_only won't consider others


def test_result_includes_breakdown_and_candidate_comparison():
    files = list(STD)
    table = {("spotify", "sp1"): DLX, ("deezer", "dz1"): STD}
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "sp1", "deezer": "dz1"},
        file_tracks=files, fetch_tracklist=_fetcher(table),
        source_priority=["spotify", "deezer", "itunes", "musicbrainz"],
        mode="best_fit",
    )
    assert out["source"] == "deezer"
    assert out["file_track_count"] == 11
    assert out["release_track_count"] == 11
    assert out["count_fit"] == 1.0 and out["duration_fit"] == 1.0 and out["title_fit"] == 1.0
    by_src = {c["source"]: c for c in out["candidates"]}
    assert by_src["deezer"]["track_count"] == 11 and by_src["deezer"]["score"] > 0.9
    assert by_src["spotify"]["track_count"] == 17 and by_src["spotify"]["score"] < 0.8


def test_active_only_pins_primary_when_it_fits():
    files = list(STD)
    table = {("spotify", "sp1"): STD, ("musicbrainz", "mb1"): STD}
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "sp1", "musicbrainz": "mb1"},
        file_tracks=files, fetch_tracklist=_fetcher(table),
        source_priority=PRIORITY, mode="active_only",
    )
    assert out["source"] == "spotify"


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


# ── #767-2: expand to alternate editions when the linked one clearly misfits ──
#
# The reported bug: a 1-track single was enriched against the *deluxe* album, so
# every linked source ID points at the 10-track deluxe. The single is never a
# candidate, so it can never be chosen — and the deluxe scores so badly (0.1,
# below the 0.5 floor) that nothing gets pinned and the organizer falls back to
# the stored deluxe ID. The fix: when no LINKED edition clears the floor, fetch
# the source's other editions of the same release and score those too.

SINGLE_FILE = [{"duration_ms": 129_000, "title": "Scatterbrain"}]  # owns the 2:09 single
# 10-track deluxe; "Scatterbrain" sits at track 2 (2:10, within ±3s of the file).
DELUXE_10 = (
    [{"duration_ms": 200_000, "title": "Intro"}]
    + [{"duration_ms": 130_000, "title": "Scatterbrain"}]
    + [{"duration_ms": 180_000 + i * 5_000, "title": f"Deluxe {i+1}"} for i in range(8)]
)
SINGLE_RELEASE = [{"duration_ms": 129_000, "title": "Scatterbrain", "track_number": 1}]


def _alternates(table):
    """fetch_alternates backed by a {(source, album_id): [release, ...]} table.
    Each release is {'album_id', 'tracks'} — a sibling edition from that source."""
    def fetch(source, album_id):
        return table.get((source, album_id)) or []
    return fetch


def test_expands_to_alternate_edition_when_linked_misfits():
    # Only linked id is the deluxe; user actually owns the single.
    alt_table = {
        ("spotify", "sp_deluxe"): [
            {"album_id": "sp_single", "tracks": SINGLE_RELEASE},
            {"album_id": "sp_deluxe", "tracks": DELUXE_10},  # the edition we already have
        ],
    }
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "sp_deluxe"},
        file_tracks=list(SINGLE_FILE),
        fetch_tracklist=_fetcher({("spotify", "sp_deluxe"): DELUXE_10}),
        fetch_alternates=_alternates(alt_table),
        source_priority=PRIORITY,
    )
    assert out is not None, "deluxe alone scores 0.1 (below floor) — must expand"
    assert out["source"] == "spotify" and out["album_id"] == "sp_single"
    assert out["score"] > 0.9


def test_does_not_expand_when_linked_edition_fits():
    # Linked edition fits the files (score ~1.0) -> NEVER fetch alternates (cost
    # guard + zero behaviour change for the 95% common case).
    calls = []

    def spy(source, album_id):
        calls.append((source, album_id))
        return [{"album_id": "sp_other", "tracks": DELUXE_10}]

    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "sp_std"},
        file_tracks=list(STD),
        fetch_tracklist=_fetcher({("spotify", "sp_std"): STD}),
        fetch_alternates=spy,
        source_priority=PRIORITY,
    )
    assert out["source"] == "spotify" and out["album_id"] == "sp_std"
    assert calls == [], "must not fetch alternates when the linked edition already fits"


def test_expansion_dedupes_alternates_against_linked():
    # The alternates list re-offers the linked deluxe; it must not be double-scored
    # and must not crash. The exact-fit single still wins.
    alt_table = {
        ("spotify", "sp_deluxe"): [
            {"album_id": "sp_deluxe", "tracks": DELUXE_10},   # dup of the linked one
            {"album_id": "sp_single", "tracks": SINGLE_RELEASE},
        ],
    }
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "sp_deluxe"},
        file_tracks=list(SINGLE_FILE),
        fetch_tracklist=_fetcher({("spotify", "sp_deluxe"): DELUXE_10}),
        fetch_alternates=_alternates(alt_table),
        source_priority=PRIORITY,
    )
    assert out["album_id"] == "sp_single"
    ids = [c["album_id"] for c in out["candidates"]]
    assert ids.count("sp_deluxe") == 1, "linked edition must be scored exactly once"


def test_no_expansion_when_alternates_not_provided():
    # Backward-compat: without a fetch_alternates callable, behaviour is exactly
    # as before — the misfit deluxe stays unresolved (None).
    out = resolve_canonical_for_album(
        album_source_ids={"spotify": "sp_deluxe"},
        file_tracks=list(SINGLE_FILE),
        fetch_tracklist=_fetcher({("spotify", "sp_deluxe"): DELUXE_10}),
        source_priority=PRIORITY,
    )
    assert out is None
