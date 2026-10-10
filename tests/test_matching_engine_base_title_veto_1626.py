"""Base-title veto in the Soulseek scorer (#1626).

A shared version qualifier inflates whole-path similarity enough to pass the
title gate: wanted "Crystalline (Orchestral Version)" vs a DIFFERENT song's
"Damnation Flame (Orchestral Version)" in the right artist folder scored
~0.69 and cleared the 0.58 accept threshold — the "(Orchestral Version)"
overlap pushed the title score past the 0.30 gate while the artist folder
contributed 0.40. ``base_title_of`` already reduces both sides to the bare
song name; the veto compares those and rejects when they are nowhere near
each other.

The veto is deliberately fail-open — it fires only when ALL of these hold:
  (a) the artist component matched cleanly (word-boundary hit, >= 0.99);
  (b) both base titles are non-empty;
  (c) base-title similarity is under a floor (0.30), not exact inequality,
      so a typo'd filename never gets vetoed.
"""

from __future__ import annotations

import pytest

from core.matching_engine import MusicMatchingEngine
from core.download_plugins.types import TrackResult
from core.spotify_client import Track as SpotifyTrack

# 0.58 is the accept threshold in find_best_slskd_matches_enhanced.
ACCEPT_THRESHOLD = 0.58


@pytest.fixture
def engine() -> MusicMatchingEngine:
    return MusicMatchingEngine()


def _track(name: str, artists: list[str]) -> SpotifyTrack:
    return SpotifyTrack(
        id='1', name=name, artists=artists, album='Some Album',
        duration_ms=200000, popularity=0, preview_url=None, external_urls={},
    )


def _result(filename: str, duration_ms: int = 200000) -> TrackResult:
    return TrackResult(
        username='peer', filename=filename, size=30_000_000, bitrate=1000,
        duration=duration_ms, quality='flac',
        free_upload_slots=1, upload_speed=100000, queue_length=0,
    )


def _enhanced(engine, track, result):
    return engine.calculate_slskd_match_confidence_enhanced(track, result)


# ---------------------------------------------------------------------------
# The reported scenario.
# ---------------------------------------------------------------------------


def test_wrong_song_sharing_version_qualifier_is_vetoed(engine):
    """The exact repro: wanted "Crystalline (Orchestral Version)", candidate
    "Damnation Flame (Orchestral Version)" in the right artist folder with
    unknown duration. Pre-fix this scored ~0.69 ('original') and passed."""
    track = _track('Crystalline (Orchestral Version)', ['Dark Sanctuary'])
    candidate = _result(
        'Dark Sanctuary/Exaudi Nos/07 - Damnation Flame (Orchestral Version).flac',
        duration_ms=0,
    )

    confidence, version_type = _enhanced(engine, track, candidate)

    assert confidence == 0.0
    # the veto, not a version rejection ('rejected_version_mismatch'); the
    # veto carries its own distinct label because the caller re-stamps the
    # attribute and never consumes this tuple element.
    assert version_type == 'rejected_base_title_mismatch'


def test_second_pair_different_base_same_qualifier_is_vetoed(engine):
    """A second wrong-song/same-qualifier pair, to pin that this is about the
    base titles and not one specific song."""
    track = _track('Nemo (Orchestral Version)', ['Nightwish'])
    candidate = _result(
        'Nightwish/Dark Passion Play/02 - The Poet and the Pendulum '
        '(Orchestral Version).flac',
    )

    confidence, _ = _enhanced(engine, track, candidate)

    assert confidence == 0.0


def test_correct_file_analogue_still_accepted(engine):
    """The right file under identical conditions must keep passing — the
    veto must not nuke genuine matches."""
    track = _track('Crystalline (Orchestral Version)', ['Dark Sanctuary'])
    candidate = _result(
        'Dark Sanctuary/Exaudi Nos/03 - Crystalline (Orchestral Version).flac',
    )

    confidence, version_type = _enhanced(engine, track, candidate)

    assert confidence > ACCEPT_THRESHOLD
    assert version_type == 'original'


# ---------------------------------------------------------------------------
# Fail-open: the veto must NOT fire here.
# ---------------------------------------------------------------------------


def test_same_base_different_qualifier_not_vetoed(engine):
    """Same song, different version: base titles are equal, so no veto.
    "X" vs "X (Orchestral Version)" must stay acceptable."""
    track = _track('Crystalline', ['Dark Sanctuary'])
    candidate = _result(
        'Dark Sanctuary/Exaudi Nos/03 - Crystalline (Orchestral Version).flac',
    )

    confidence, _ = _enhanced(engine, track, candidate)

    assert confidence > ACCEPT_THRESHOLD


def test_wanted_live_vs_candidate_live_at_venue_not_vetoed(engine):
    """Wanted "X (Live)" vs candidate "X (Live at Y)": equal base titles, so
    no veto — and the strict version check sees the live indicator too."""
    track = _track('Crystalline (Live)', ['Dark Sanctuary'])
    candidate = _result(
        'Dark Sanctuary/Live/01 - Crystalline (Live at Wembley).flac',
    )

    confidence, version_type = _enhanced(engine, track, candidate)

    assert confidence > ACCEPT_THRESHOLD
    assert version_type == 'live'


def test_typo_tolerance_no_veto(engine):
    """A one-character typo in the candidate title must not be vetoed: the
    base-title comparison is a similarity floor, not exact equality. The
    flat filename keeps the base scorer's title ratio above its gate so the
    file genuinely passes on its own merits."""
    track = _track('Crystalline', ['Dark Sanctuary'])
    candidate = _result(
        'Dark Sanctuary - Crystallin (Orchestral Version).flac',
    )

    confidence, _ = _enhanced(engine, track, candidate)

    assert confidence > ACCEPT_THRESHOLD


def test_no_veto_without_strong_artist_evidence(engine):
    """Condition (a): with only a fuzzy artist-folder match the veto stays
    silent (fail open). The file is still rejected, but by the confidence
    threshold — not by an exact 0.0 veto."""
    track = _track('Crystalline (Orchestral Version)', ['Dark Sanctuary'])
    candidate = _result(
        'Sanctuary/Exaudi Nos/07 - Damnation Flame (Orchestral Version).flac',
        duration_ms=0,
    )

    confidence, _ = _enhanced(engine, track, candidate)

    assert 0.0 < confidence <= ACCEPT_THRESHOLD


def test_flat_filename_short_title_not_vetoed(engine):
    """Flat "Artist - Title" filenames with a short title must not be vetoed
    (MAJOR-1, #1626 review finding). Pre-fix, the veto reduced the wanted
    side to "up" but the candidate side to "pink floyd up" — the artist name
    never got stripped, diluting the similarity and wrongly killing the match
    to 0.0 for every short title by a long-named artist. Passing the wanted
    artist through base_title_of on both sides reduces the candidate to "up"
    and the veto stays silent."""
    track = _track('Up', ['Pink Floyd'])
    candidate = _result(
        'Pink Floyd - Up (Orchestral Version).flac',
    )

    confidence, _ = _enhanced(engine, track, candidate)

    assert confidence > 0.0  # never vetoed
    assert confidence > ACCEPT_THRESHOLD  # and genuinely accepted


def test_flat_filename_wrong_song_still_vetoed(engine):
    """The artist fix must not blind the veto: a flat "Artist - Title"
    filename whose base title is nowhere near the wanted one is still
    vetoed after the artist prefix is stripped from both sides."""
    track = _track('Crystalline (Orchestral Version)', ['Dark Sanctuary'])
    candidate = _result(
        'Dark Sanctuary - Damnation Flame (Orchestral Version).flac',
        duration_ms=0,
    )

    confidence, version_type = _enhanced(engine, track, candidate)

    assert confidence == 0.0
    assert version_type == 'rejected_base_title_mismatch'
