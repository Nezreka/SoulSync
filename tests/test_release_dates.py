"""#705 release-date gate: unreleased tracks stay out of hot paths.

Watchlist scans add announced albums on purpose; the gate keeps their
future-dated tracks out of the wishlist search cycle and the Fresh Tape
radar until release day. Conservative by design: only a CONFIDENTLY
future date gates; bad/missing dates never block anything.
"""

from __future__ import annotations

from datetime import date

from core.metadata.release_dates import (
    is_future_release,
    split_released_unreleased,
    track_release_date,
)

TODAY = date(2026, 6, 7)


def test_full_dates():
    assert is_future_release('2026-06-08', today=TODAY) is True
    assert is_future_release('2026-06-07', today=TODAY) is False  # release DAY = released
    assert is_future_release('2026-06-06', today=TODAY) is False
    assert is_future_release('2027-01-01', today=TODAY) is True


def test_partial_dates_are_conservative():
    # Year-only: future only when the YEAR is future.
    assert is_future_release('2027', today=TODAY) is True
    assert is_future_release('2026', today=TODAY) is False
    # Year-month: future only when the MONTH is future.
    assert is_future_release('2026-07', today=TODAY) is True
    assert is_future_release('2026-06', today=TODAY) is False
    assert is_future_release('2026-05', today=TODAY) is False


def test_garbage_never_blocks():
    for bad in ('', None, 'unknown', 'soon', '20xx-01-01', '2026-13-45', 123, {}):
        assert is_future_release(bad, today=TODAY) is False


def test_invalid_day_falls_back_to_month_precision():
    # 2026-06-99 is unparseable as a date but month precision says "not future".
    assert is_future_release('2026-06-99', today=TODAY) is False
    assert is_future_release('2026-07-99', today=TODAY) is True


def test_track_release_date_shapes():
    assert track_release_date({'album': {'release_date': '2026-10-03'}}) == '2026-10-03'
    assert track_release_date({'release_date': '2026'}) == '2026'
    assert track_release_date({'album': 'a-string'}) == ''
    assert track_release_date({}) == ''
    assert track_release_date(None) == ''


def test_split_partitions_and_preserves_order():
    tracks = [
        {'name': 'out',      'album': {'release_date': '2026-01-01'}},
        {'name': 'tomorrow', 'album': {'release_date': '2026-06-08'}},
        {'name': 'no-date',  'album': {}},
        {'name': 'next-year', 'release_date': '2027'},
    ]
    released, unreleased = split_released_unreleased(tracks, today=TODAY)
    assert [t['name'] for t in released] == ['out', 'no-date']
    assert [t['name'] for t in unreleased] == ['tomorrow', 'next-year']
