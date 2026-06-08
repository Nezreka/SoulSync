"""Pure expiry decision for the Expired Download Cleaner."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.library.expired_cleanup import (
    retention_cutoff,
    is_expired,
    select_expired,
)

NOW = datetime(2026, 6, 7, tzinfo=timezone.utc)


def _entry(origin="playlist", days_old=100, play_count=0, protected=False, eid=1):
    return {
        "id": eid, "origin": origin, "play_count": play_count, "protected": protected,
        "created_at": (NOW - timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _check(entry, wl="off", pl="2mo", min_plays=2):
    return is_expired(entry, watchlist_retention=wl, playlist_retention=pl,
                      min_plays=min_plays, now=NOW)


# ── retention windows ────────────────────────────────────────────────────────

def test_retention_cutoff_maps_durations():
    assert retention_cutoff("2mo", NOW) == NOW - timedelta(days=60)
    assert retention_cutoff("1w", NOW) == NOW - timedelta(days=7)
    assert retention_cutoff("off", NOW) is None
    assert retention_cutoff(None, NOW) is None
    assert retention_cutoff("bogus", NOW) is None


def test_expired_only_past_window():
    assert _check(_entry(days_old=70), pl="2mo") is True     # 70 > 60d
    assert _check(_entry(days_old=50), pl="2mo") is False    # 50 < 60d


def test_off_retention_never_expires():
    assert _check(_entry(origin="watchlist", days_old=999), wl="off") is False


def test_origin_uses_its_own_window():
    wl = _entry(origin="watchlist", days_old=30)
    # watchlist=1w (expired at 30d), playlist=off
    assert is_expired(wl, watchlist_retention="1w", playlist_retention="off",
                      min_plays=2, now=NOW) is True
    pl = _entry(origin="playlist", days_old=30)
    assert is_expired(pl, watchlist_retention="1w", playlist_retention="off",
                      min_plays=2, now=NOW) is False   # playlist off


# ── the keep guards ──────────────────────────────────────────────────────────

def test_protected_kept_even_if_old():
    assert _check(_entry(days_old=999, protected=True), pl="1w") is False


def test_played_more_than_once_kept():
    assert _check(_entry(days_old=999, play_count=2), pl="1w", min_plays=2) is False
    assert _check(_entry(days_old=999, play_count=1), pl="1w", min_plays=2) is True   # one play = deletable
    assert _check(_entry(days_old=999, play_count=0), pl="1w", min_plays=2) is True


def test_min_plays_threshold_configurable():
    e = _entry(days_old=999, play_count=1)
    assert _check(e, pl="1w", min_plays=1) is False   # keep-if-played-at-least-1
    assert _check(e, pl="1w", min_plays=3) is True    # needs 3 plays to keep


def test_unknown_age_never_deleted():
    e = _entry(days_old=999)
    e["created_at"] = "garbage"
    assert _check(e, pl="1w") is False


# ── select_expired ───────────────────────────────────────────────────────────

def test_select_expired_filters():
    entries = [
        _entry(eid=1, days_old=70, play_count=0),            # expired
        _entry(eid=2, days_old=70, play_count=5),            # listened → keep
        _entry(eid=3, days_old=70, protected=True),          # mirrored → keep
        _entry(eid=4, days_old=10),                          # too new → keep
    ]
    out = select_expired(entries, watchlist_retention="off", playlist_retention="2mo")
    assert [e["id"] for e in out] == [1]
