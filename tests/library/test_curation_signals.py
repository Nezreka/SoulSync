"""Keep what people actually like (Cremonies).

    "It would check if in others playlist, favorited, or rated above 2 stars.
    Favorited trumped rating since some user liked songs but didn't want them
    mixed in their mixes."

Play count is a weak proxy for wanting a song — it measures what happened, not
what anyone chose. A favorite, a star rating, or putting a track in a playlist
are deliberate acts. These pin the decision rules; reading the signals off the
media servers is a separate layer.

The governing rule from the rest of this job still applies: unknown fails
toward KEEP. No signals at all means we cannot tell, which means keep.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.library.expired_cleanup import is_curated, is_expired

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


def _sig(**over):
    signal = {"user": "alice", "favorite": False, "rating": None, "in_playlist": False}
    signal.update(over)
    return signal


# ── the three keep signals ────────────────────────────────────────────────

def test_a_favorite_keeps_it():
    assert is_curated([_sig(favorite=True)]) is True


def test_a_rating_above_two_keeps_it():
    assert is_curated([_sig(rating=3)]) is True


def test_membership_of_any_playlist_keeps_it():
    assert is_curated([_sig(in_playlist=True)]) is True


def test_nothing_expressed_is_not_curated():
    assert is_curated([_sig()]) is False


def test_no_signals_at_all_is_not_curated():
    """Distinct from 'kept': an empty list means nobody expressed anything.
    The caller decides what to do about not having synced yet."""
    assert is_curated([]) is False


# ── the thresholds ────────────────────────────────────────────────────────

def test_two_stars_is_not_enough():
    """'rated above 2 stars' — two is not above two."""
    assert is_curated([_sig(rating=2)]) is False


def test_one_star_is_not_enough():
    assert is_curated([_sig(rating=1)]) is False


def test_the_threshold_is_configurable():
    assert is_curated([_sig(rating=3)], min_rating=4) is False
    assert is_curated([_sig(rating=4)], min_rating=4) is True


def test_a_zero_rating_is_treated_as_unrated_not_as_a_low_score():
    """Subsonic and Jellyfin both report 0 for 'no rating set'."""
    assert is_curated([_sig(rating=0)]) is False


# ── Cremonies' rule: favorite trumps rating ───────────────────────────────

def test_a_favorite_with_a_low_rating_is_still_kept():
    """His exact case: people star a song they like but rate it low (or not at
    all) to keep it out of their mixes. The low rating must not override the
    favorite."""
    assert is_curated([_sig(favorite=True, rating=1)]) is True


def test_a_favorite_with_a_zero_rating_is_still_kept():
    assert is_curated([_sig(favorite=True, rating=0)]) is True


# ── any user counts ───────────────────────────────────────────────────────

def test_one_users_favorite_protects_the_track_for_everyone():
    """A shared library: if anyone on the server wants it, it stays."""
    signals = [_sig(user="alice"), _sig(user="bob", favorite=True)]
    assert is_curated(signals) is True


def test_nobody_wanting_it_is_not_curated():
    signals = [_sig(user="alice"), _sig(user="bob", rating=2)]
    assert is_curated(signals) is False


# ── malformed input fails toward keep ─────────────────────────────────────

def test_a_junk_rating_does_not_crash_and_does_not_delete():
    assert is_curated([_sig(rating="not a number")]) is True


def test_a_junk_signal_row_does_not_crash():
    assert is_curated([None, "nonsense"]) is True


# ── how it reaches the expiry decision ────────────────────────────────────

def _entry(**over):
    entry = {
        "origin": "playlist",
        "created_at": (NOW - timedelta(days=200)).isoformat(),
        "play_count": 0,
        "protected": False,
        "grandfathered": False,
        "curated": False,
    }
    entry.update(over)
    return entry


def _decide(entry):
    return is_expired(entry, watchlist_retention="1w", playlist_retention="1w",
                      min_plays=2, now=NOW)


def test_a_curated_track_is_never_proposed():
    assert _decide(_entry(curated=True)) is False


def test_an_uncurated_expired_track_is_still_proposed():
    """The feature must still do its job — this is the whole point of it."""
    assert _decide(_entry(curated=False)) is True
