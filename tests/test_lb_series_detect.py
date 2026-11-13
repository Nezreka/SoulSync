"""Tests for the LB rotating-series detector that powers the
rolling-mirror collapse on the Sync page.

Pins the title patterns + canonical-name templates so accidental
regex tweaks don't silently break the auto-mirror grouping the
Auto-Sync manager + Mirrored tab rely on.
"""

from __future__ import annotations

import pytest

from core.playlists.lb_series import (
    detect_series,
    is_series_synthetic_id,
    list_series_synthetic_ids,
)


class TestDetectSeries:
    def test_weekly_jams_collapses_into_rolling_series(self):
        m = detect_series("Weekly Jams for Nezreka, week of 2026-05-25 Mon")
        assert m is not None
        assert m.series_id == "lb_weekly_jams_Nezreka"
        assert m.canonical_name == "ListenBrainz Weekly Jams"
        assert m.source_for_mirror == "listenbrainz"
        assert m.title_pattern == "Weekly Jams for Nezreka, week of %"

    def test_weekly_exploration_collapses_into_rolling_series(self):
        m = detect_series("Weekly Exploration for Nezreka, week of 2026-04-13 Mon")
        assert m is not None
        assert m.series_id == "lb_weekly_exploration_Nezreka"
        assert m.canonical_name == "ListenBrainz Weekly Exploration"
        assert m.title_pattern == "Weekly Exploration for Nezreka, week of %"

    def test_top_discoveries_collapses_per_user(self):
        m = detect_series("Top Discoveries of 2024 for Nezreka")
        assert m is not None
        assert m.series_id == "lb_top_discoveries_Nezreka"
        assert m.canonical_name == "ListenBrainz Top Discoveries (latest year)"
        assert m.title_pattern == "Top Discoveries of % for Nezreka"

    def test_top_missed_collapses_per_user(self):
        m = detect_series("Top Missed Recordings of 2025 for Nezreka")
        assert m is not None
        assert m.series_id == "lb_top_missed_Nezreka"
        assert m.canonical_name == "ListenBrainz Top Missed Recordings (latest year)"

    def test_user_with_spaces_in_name(self):
        # ListenBrainz allows usernames with spaces; the regex should
        # still match and the series id propagates the literal user
        # token. Whether SQLite LIKE works on that is the caller's
        # problem — we just preserve the captured value.
        m = detect_series("Weekly Jams for Some User, week of 2026-01-05 Mon")
        assert m is not None
        assert m.series_id == "lb_weekly_jams_Some User"

    def test_lastfm_radio_is_not_a_series(self):
        # Last.fm radios get their own per-seed MBID — they should NOT
        # be collapsed into a rolling series.
        assert detect_series("Last.fm Radio: Selfish by Madison Beer") is None

    def test_user_created_playlist_is_not_a_series(self):
        assert detect_series("My Custom Playlist") is None

    def test_empty_title_returns_none(self):
        assert detect_series("") is None
        assert detect_series(None) is None  # type: ignore[arg-type]


class TestSyntheticIdHelpers:
    def test_known_prefixes_listed(self):
        prefixes = list_series_synthetic_ids()
        assert "lb_weekly_jams_" in prefixes
        assert "lb_weekly_exploration_" in prefixes
        assert "lb_top_discoveries_" in prefixes
        assert "lb_top_missed_" in prefixes

    def test_is_series_synthetic_id_matches_known(self):
        assert is_series_synthetic_id("lb_weekly_jams_Nezreka") is True
        assert is_series_synthetic_id("lb_weekly_exploration_OtherUser") is True
        assert is_series_synthetic_id("lb_top_discoveries_X") is True

    def test_is_series_synthetic_id_rejects_mbids(self):
        # Real LB playlist MBIDs are UUID-shaped, never start with ``lb_``.
        assert is_series_synthetic_id("4badb5c9-266e-42ef-9d06-879ee311c9e0") is False
        assert is_series_synthetic_id("") is False
        assert is_series_synthetic_id("lb_") is False  # not a real series
        assert is_series_synthetic_id("lb_random_thing") is False


class TestDailyJams:
    """#2076: Daily Jams gets a new MBID every day and yesterday's is deleted,
    so it has to be one rolling mirror like the weekly ones."""

    @pytest.mark.parametrize("user", ["SomeUser", "some_user", "Some User", "Doe, John"])
    def test_a_day_collapses_into_the_users_rolling_series(self, user):
        m = detect_series(f"Daily Jams for {user}, 2026-11-13 Fri")
        assert m is not None
        assert m.series_id == f"lb_daily_jams_{user}"
        assert m.canonical_name == "ListenBrainz Daily Jams"
        assert m.source_for_mirror == "listenbrainz"
        assert m.title_pattern == f"Daily Jams for {user}, %"

    @pytest.mark.parametrize("title", [
        "Daily Jams for SomeUser",                    # no date
        "Daily Jams for SomeUser, 2026-1-3 Fri",      # not a date
        "My Daily Jams for SomeUser, 2026-11-13 Fri",
        "ListenBrainz Daily Jams",                    # the rolling mirror's own name
    ])
    def test_anything_else_is_left_alone(self, title):
        assert detect_series(title) is None

    def test_the_weekly_series_are_not_caught_by_it(self):
        assert detect_series("Weekly Jams for Nezreka, week of 2026-05-25 Mon").series_id \
            == "lb_weekly_jams_Nezreka"

    def test_its_synthetic_id_is_recognised(self):
        assert "lb_daily_jams_" in list_series_synthetic_ids()
        assert is_series_synthetic_id("lb_daily_jams_SomeUser")

    def test_the_newest_day_is_the_one_a_refresh_reads(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE p(title TEXT, last_updated TEXT)")
        conn.executemany("INSERT INTO p VALUES(?,?)", [
            ("Daily Jams for U, 2026-11-11 Wed", "2026-11-13"),   # older day, touched later
            ("Daily Jams for U, 2026-11-13 Fri", "2026-11-12"),
            ("Daily Jams for U, 2026-11-12 Thu", "2026-11-12"),
            ("Weekly Jams for U, week of 2026-11-09 Mon", "2026-11-13"),
        ])
        pattern = detect_series("Daily Jams for U, 2026-11-13 Fri").title_pattern
        row = conn.execute("SELECT title FROM p WHERE title LIKE ? "
                           "ORDER BY title DESC, last_updated DESC LIMIT 1", (pattern,)).fetchone()
        assert row[0] == "Daily Jams for U, 2026-11-13 Fri"

    def test_the_sweeper_takes_a_days_mirror_and_leaves_the_rolling_one(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE m(name TEXT)")
        conn.executemany("INSERT INTO m VALUES(?)", [
            ("Daily Jams for U, 2026-11-13 Fri",), ("ListenBrainz Daily Jams",),
            ("Daily Jams for the road",)])
        hit = [r[0] for r in conn.execute(
            "SELECT name FROM m WHERE name LIKE 'Daily Jams for %, ____-__-__%'")]
        assert hit == ["Daily Jams for U, 2026-11-13 Fri"]
