"""When is a movie downloadable — the Radarr-style 'minimum availability = released' date
computed from TMDB per-country release-date data."""

from __future__ import annotations

import datetime

from core.video.release_availability import available_date


def _country(code, *types):
    return {"iso_3166_1": code, "release_dates": [
        {"type": t, "release_date": d + "T00:00:00.000Z"} for t, d in types]}


def test_prefers_earliest_digital_or_physical():
    # digital (4) 2025-03-01 in US, physical (5) 2025-02-01 in GB → earliest home = 2025-02-01
    results = [_country("US", (3, "2024-11-27"), (4, "2025-03-01")),
               _country("GB", (5, "2025-02-01"))]
    assert available_date(results) == "2025-02-01"


def test_estimates_from_theatrical_when_no_home_release():
    # only theatrical known (the Moana-in-cinemas case) → theatrical + 90 days
    results = [_country("US", (3, "2026-07-10"))]
    got = available_date(results, delay_days=90)
    assert got == (datetime.date(2026, 7, 10) + datetime.timedelta(days=90)).isoformat()


def test_none_when_tmdb_has_no_dates():
    assert available_date([]) is None
    assert available_date([_country("US")]) is None
    assert available_date(None) is None


def test_ignores_malformed_entries():
    results = [{"release_dates": [{"type": 4, "release_date": None},
                                  {"type": None, "release_date": "2025-01-01"},
                                  {"type": 4, "release_date": "2025-06-06T00:00:00Z"}]}]
    assert available_date(results) == "2025-06-06"


def test_premiere_type1_is_ignored_uses_wide_theatrical():
    # The Invite case: a Jan premiere (type 1) months before the July wide theatrical (type 3).
    # The estimate must anchor on the WIDE date, not the premiere.
    results = [_country("US", (1, "2026-01-24"), (2, "2026-06-26"), (3, "2026-07-10"))]
    assert available_date(results, delay_days=90) == \
        (datetime.date(2026, 7, 10) + datetime.timedelta(days=90)).isoformat()


def test_limited_theatrical_used_when_no_wide():
    results = [_country("US", (1, "2026-01-24"), (2, "2026-06-26"))]   # premiere + limited, no wide
    assert available_date(results, delay_days=90) == \
        (datetime.date(2026, 6, 26) + datetime.timedelta(days=90)).isoformat()
