"""Fix 11: specials (season 0) are included in the automation refresh scope."""
from core.video.enrichment.engine import _latest_seasons
from core.video.monitor_policy import latest_season_numbers


def test_latest_seasons_includes_specials():
    # a show with seasons 0-3: refresh scopes to [0, 3, 2] (specials + latest 2)
    assert _latest_seasons([0, 1, 2, 3]) == [0, 3, 2]


def test_latest_seasons_without_specials_unchanged():
    assert _latest_seasons([1, 2, 3]) == [3, 2]


def test_latest_seasons_specials_only_fallback():
    # specials-only show: falls back to what it has
    assert _latest_seasons([0]) == [0]


def test_latest_season_numbers_includes_specials():
    detail = {"seasons": [{"season_number": n} for n in [0, 1, 2, 3]]}
    assert latest_season_numbers(detail) == [0, 2, 3]


def test_latest_season_numbers_without_specials_unchanged():
    detail = {"seasons": [{"season_number": n} for n in [1, 2, 3]]}
    assert latest_season_numbers(detail) == [2, 3]
