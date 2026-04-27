"""Tests for `discogs_worker.count_discogs_real_tracks` — the filter
that decides which entries in a Discogs tracklist count as real songs
when caching the authoritative track count for the Album Completeness
repair job.

Reported by kettui on PR #374: the original inline filter only kept
``type_ == 'track'`` rows, but `discogs_client.get_album_tracks` itself
keeps both ``type_ == 'track'`` AND rows with an empty/missing
``type_``. The narrower filter would undercount releases whose Discogs
response left ``type_`` blank for some real tracks — and the repair
job's fallback path (`_get_expected_total`) would silently disagree
with the cached count.
"""

from core.discogs_worker import count_discogs_real_tracks


# ---------------------------------------------------------------------------
# The kettui case: empty type_ counts as a real track
# ---------------------------------------------------------------------------

def test_empty_type_counts_as_track():
    tracklist = [
        {'title': 'Track 1', 'type_': 'track'},
        {'title': 'Track 2', 'type_': ''},     # <-- the bug
        {'title': 'Track 3', 'type_': 'track'},
    ]
    assert count_discogs_real_tracks(tracklist) == 3


def test_missing_type_field_counts_as_track():
    """Discogs sometimes omits the field entirely rather than sending
    an empty string. Both shapes mean 'real track'."""
    tracklist = [
        {'title': 'Track 1'},                  # no type_ key at all
        {'title': 'Track 2', 'type_': 'track'},
    ]
    assert count_discogs_real_tracks(tracklist) == 2


def test_none_type_counts_as_track():
    """And the field may be present-but-None on some clients/versions."""
    tracklist = [
        {'title': 'Track 1', 'type_': None},
        {'title': 'Track 2', 'type_': 'track'},
    ]
    assert count_discogs_real_tracks(tracklist) == 2


# ---------------------------------------------------------------------------
# Non-track rows are excluded (Discogs's structural markers)
# ---------------------------------------------------------------------------

def test_headings_excluded():
    tracklist = [
        {'title': 'Disc 1', 'type_': 'heading'},
        {'title': 'Track 1', 'type_': 'track'},
        {'title': 'Track 2', 'type_': 'track'},
        {'title': 'Disc 2', 'type_': 'heading'},
        {'title': 'Track 3', 'type_': 'track'},
    ]
    assert count_discogs_real_tracks(tracklist) == 3


def test_indices_excluded():
    tracklist = [
        {'title': 'Side A', 'type_': 'index'},
        {'title': 'Track 1', 'type_': 'track'},
        {'title': 'Side B', 'type_': 'index'},
        {'title': 'Track 2', 'type_': 'track'},
    ]
    assert count_discogs_real_tracks(tracklist) == 2


def test_sub_tracks_excluded():
    """Sub-tracks (parts of a medley) shouldn't double-count against
    the parent track."""
    tracklist = [
        {'title': 'Medley', 'type_': 'track'},
        {'title': 'Part A', 'type_': 'sub_track'},
        {'title': 'Part B', 'type_': 'sub_track'},
        {'title': 'Encore', 'type_': 'track'},
    ]
    assert count_discogs_real_tracks(tracklist) == 2


def test_unknown_type_excluded():
    """Conservative — if Discogs adds a new structural marker we
    haven't seen, don't count it as a track until we explicitly add
    it to the allowlist."""
    tracklist = [
        {'title': 'Track 1', 'type_': 'track'},
        {'title': 'Some Future Marker', 'type_': 'experimental_thing'},
    ]
    assert count_discogs_real_tracks(tracklist) == 1


# ---------------------------------------------------------------------------
# Defensive — bad input shouldn't raise
# ---------------------------------------------------------------------------

def test_empty_tracklist_returns_zero():
    assert count_discogs_real_tracks([]) == 0


def test_none_tracklist_returns_zero():
    assert count_discogs_real_tracks(None) == 0


# ---------------------------------------------------------------------------
# Realistic mixed tracklist (the kettui case in context)
# ---------------------------------------------------------------------------

def test_realistic_multi_disc_tracklist():
    """A 2-disc release with headings, real tracks, AND a few rows
    with empty type_ — should count all real tracks but no markers."""
    tracklist = [
        {'title': 'Disc 1', 'type_': 'heading'},
        {'title': 'Track 1', 'type_': 'track'},
        {'title': 'Track 2', 'type_': 'track'},
        {'title': 'Track 3', 'type_': ''},       # the kettui case
        {'title': 'Track 4', 'type_': 'track'},
        {'title': 'Disc 2', 'type_': 'heading'},
        {'title': 'Track 5', 'type_': 'track'},
        {'title': 'Track 6'},                    # missing type_
        {'title': 'Bonus Index', 'type_': 'index'},
        {'title': 'Track 7', 'type_': 'track'},
    ]
    assert count_discogs_real_tracks(tracklist) == 7
