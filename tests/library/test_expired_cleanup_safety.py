"""Safety net for the Expired Download Cleaner (Cremonies' thread).

This job deletes the user's files, so the governing rule is that **every
failure must fail toward KEEP**. These tests pin the two ways it currently
fails toward DELETE.

B1 — the play-count protection never fires when SoulSync's path and the media
server's path differ (docker / NAS). ``get_origin_cleanup_candidates`` joins
``library_history.file_path`` to ``tracks.file_path`` by exact string match,
but those are two different strings on those installs — SoulSync's
post-processed path vs whatever the server reported. That mismatch is exactly
what ``core/library/path_resolver.py`` exists to bridge, and the DELETE step
already uses it; the PROTECTION query does not. Result: play_count reads 0 for
every candidate and "you played it" protects nothing.

B2 — a database rebuild makes the entire library deletable. ``clear_server_data``
drops tracks/albums/artists but leaves ``library_history`` untouched, so
``created_at`` survives (the retention clock keeps running) while ``play_count``
resets to 0 and only returns on the next stats poll — up to 30 minutes later,
and never at all on a standalone install. In that window every origin-tracked
download past its window looks "old and never played", including ones played
fifty times.

Cremonies asked for the opposite invariant: "If the DB was wiped and rebuilt
then all songs would still be safe and treated as existing."
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.library.expired_cleanup import is_expired, select_expired

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
LONG_AGO = (NOW - timedelta(days=200)).isoformat()


def _entry(**over):
    entry = {
        "id": 1,
        "origin": "playlist",
        "origin_context": "Discovery Weekly",
        "created_at": LONG_AGO,
        "file_path": "/data/music/Artist/Album/01 Track.flac",
        "title": "Track",
        "artist_name": "Artist",
        "play_count": 0,
        "protected": False,
    }
    entry.update(over)
    return entry


def _decide(entry, **over):
    kwargs = {
        "watchlist_retention": "1w",
        "playlist_retention": "1w",
        "min_plays": 2,
        "now": NOW,
    }
    kwargs.update(over)
    return is_expired(entry, **kwargs)


# ── the baseline the job already gets right ───────────────────────────────

def test_a_played_track_is_kept():
    assert _decide(_entry(play_count=5)) is False


def test_an_unplayed_expired_track_is_deletable():
    """The feature working as designed — this is the one case that SHOULD
    delete, and it must keep working after the safety changes."""
    assert _decide(_entry(play_count=0)) is True


def test_a_track_in_an_active_mirror_is_kept():
    assert _decide(_entry(protected=True)) is False


# ── B2: the rebuild hazard ────────────────────────────────────────────────

def test_a_rebuild_must_not_make_a_played_track_deletable():
    """The disaster case. After a full refresh the track row is reinserted
    with play_count 0 while library_history keeps the original created_at, so
    a track the user played fifty times reads as "old and never played".

    The fix is a grandfather marker: a track SoulSync did not itself download
    in this database's lifetime must never be deletable. Absence of the mark
    means pre-existing, which means keep.
    """
    rebuilt = _entry(play_count=0, grandfathered=True)
    assert _decide(rebuilt) is False, (
        "a re-scanned track was proposed for deletion — a database rebuild "
        "must leave every song safe"
    )


def test_an_unmarked_track_is_never_deletable():
    """Same rule stated directly: no cleanup-eligibility mark → keep.

    This is the fail-safe for every unknown, not just rebuilds — a history row
    whose track SoulSync cannot account for is not a deletion candidate.
    """
    assert _decide(_entry(grandfathered=True)) is False


def test_the_mark_does_not_rescue_a_genuinely_expired_download():
    """The marker must not become a blanket amnesty: a download SoulSync did
    make, past retention and unplayed, still goes."""
    assert _decide(_entry(grandfathered=False)) is True


# ── B1: play_count that could not be read is not proof of "never played" ──

def test_unknown_play_count_is_not_treated_as_zero():
    """When the path join fails, play_count is unknown, not zero. Reading it
    as 0 is what makes the protection silently useless on docker/NAS.

    None means "we could not tell" and must fail toward keep; a real 0 from a
    matched track row still means "never played" and stays deletable.
    """
    assert _decide(_entry(play_count=None)) is False, (
        "an unreadable play_count was treated as never-played"
    )


def test_a_real_zero_still_deletes():
    assert _decide(_entry(play_count=0)) is True


# ── select_expired honours all of the above ───────────────────────────────

def test_select_expired_keeps_the_unsafe_ones():
    entries = [
        _entry(id=1, play_count=0),                  # deletable
        _entry(id=2, play_count=None),               # unknown → keep
        _entry(id=3, grandfathered=True),            # pre-existing → keep
        _entry(id=4, play_count=9),                  # played → keep
        _entry(id=5, protected=True),                # active mirror → keep
    ]
    got = {e["id"] for e in select_expired(
        entries, watchlist_retention="1w", playlist_retention="1w",
        min_plays=2, now=NOW)}
    assert got == {1}
