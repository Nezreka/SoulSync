"""Regression tests for the §27 Domain-E monitoring/wanted findings.

dd28-11  the upgrade scan judged by a DENORMALIZED profile id, so switching
         the app-wide default profile left it filtering by the old policy
dd28-12  the Wishlist→lib2 edge existed for removals only, so an addition was
         pruned again by the hourly reconciler within the hour
dd28-41  a monitored track with a satisfying file was never withdrawn from a
         Wishlist it was already on
dd28-42  a profile reassignment without monitor_existing never reprojected
dd28-43  'new' auto-monitored undated/year-only back-catalog releases
dd28-51  a late-delivered back-catalog release could mask a genuinely new one
"""

from __future__ import annotations

import pytest

from core.library2.discography import _should_auto_monitor


# --------------------------------------------------------------------------
# dd28-43 / dd28-51 — monitor_new_items='new'
# --------------------------------------------------------------------------


def _decide(release_date, year=None, newest=(2018, 6, 29), synced=None):
    return _should_auto_monitor(
        "new",
        eligible_reexpansion=True,
        release_date=release_date,
        year=year,
        newest_existing=newest,
        synced_before=synced,
    )


def test_a_genuinely_newer_dated_release_is_monitored():
    assert _decide("2023-10-06") is True


def test_an_undated_release_is_never_monitored():
    """dd28-43: guide §5 — 'new' takes only unambiguously new, DATED releases."""
    assert _decide(None) is False
    assert _decide("") is False


def test_a_year_only_release_is_not_treated_as_a_date():
    """dd28-43: the lenient key filled the gaps with 1, making 2020 -> 2020-01-01."""
    assert _decide("2020") is False
    assert _decide(None, year=2020) is False


def test_a_month_only_release_is_not_treated_as_a_date():
    assert _decide("2020-05") is False


def test_an_older_dated_release_is_not_monitored():
    assert _decide("2017-12-01") is False


def test_a_release_published_since_the_last_sync_beats_an_inflated_bar():
    """dd28-51: a pre-announced or late-delivered release inflates the bar.

    ``newest_existing`` here is a future-dated release already in the catalog,
    which used to mask every genuinely new release below it.
    """
    assert _decide("2026-07-15", newest=(2026, 12, 1), synced=(2026, 7, 1)) is True


def test_the_sync_stamp_path_still_requires_a_full_date():
    assert _decide("2026", newest=(2026, 12, 1), synced=(2026, 7, 1)) is False


def test_the_sync_stamp_path_does_not_admit_older_back_catalog():
    assert _decide("2015-03-02", newest=(2026, 12, 1), synced=(2026, 7, 1)) is False


def test_policies_other_than_new_are_untouched():
    assert _should_auto_monitor(
        "all", eligible_reexpansion=True, release_date=None, year=None,
        newest_existing=None,
    ) is True
    assert _should_auto_monitor(
        "none", eligible_reexpansion=True, release_date="2030-01-01", year=None,
        newest_existing=(2018, 1, 1),
    ) is False


# --------------------------------------------------------------------------
# dd28-11 — upgrade scan resolves the profile live
# --------------------------------------------------------------------------


def test_upgrade_scan_resolves_live_profile_when_projection_is_stale(imported_conn, tmp_path):
    """The scan and review path share the live cascade, not stale projections."""
    from core.library2.wishlist_mirror import upgrade_candidate_track_ids
    from core.library2.wanted import PROJECTION_VERSION

    conn = imported_conn
    track_id = conn.execute("SELECT id FROM lib2_tracks LIMIT 1").fetchone()[0]
    path = str(tmp_path / "Song.mp3")
    open(path, "wb").close()
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, format, import_status) "
        "VALUES(?,?, 'mp3', 'imported')",
        (track_id, path),
    )

    stale = conn.execute(
        """INSERT INTO quality_profiles(name, upgrade_policy, upgrade_cutoff_index,
               ranked_targets, is_default)
           VALUES('Stale', 'acceptable', 0, '[]', 0)"""
    ).lastrowid
    upgrading = conn.execute(
        """INSERT INTO quality_profiles(name, upgrade_policy, upgrade_cutoff_index,
               ranked_targets, is_default)
           VALUES('Upgrade until top', 'until_top', 0, '[]', 0)"""
    ).lastrowid

    conn.execute("UPDATE quality_profiles SET is_default=0")
    conn.execute(
        "UPDATE quality_profiles SET is_default=1 WHERE id=?", (upgrading,)
    )
    # Both compatibility projections deliberately point at the old profile;
    # the live global assignment is authoritative.
    conn.execute(
        "UPDATE lib2_tracks SET quality_profile_id=?, quality_profile_explicit=0 "
        "WHERE id=?", (stale, track_id)
    )
    conn.execute(
        """INSERT INTO lib2_wanted_tracks(track_id, profile_id, wanted, reason,
               effective_profile_id, projection_version)
           VALUES(?,?,1,'missing',?,?)
           ON CONFLICT(track_id, profile_id) DO UPDATE SET
               wanted=1, effective_profile_id=excluded.effective_profile_id,
               projection_version=excluded.projection_version""",
        (track_id, 1, stale, PROJECTION_VERSION),
    )
    conn.commit()

    assert track_id in upgrade_candidate_track_ids(conn, profile_id=1)


def test_upgrade_scan_honours_explicit_track_profile(imported_conn, tmp_path):
    from core.library2.wishlist_mirror import upgrade_candidate_track_ids
    from core.library2.wanted import PROJECTION_VERSION

    conn = imported_conn
    track_id = conn.execute("SELECT id FROM lib2_tracks LIMIT 1").fetchone()[0]
    path = str(tmp_path / "Song2.mp3")
    open(path, "wb").close()
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, format, import_status) "
        "VALUES(?,?, 'mp3', 'imported')",
        (track_id, path),
    )
    upgrading = conn.execute(
        """INSERT INTO quality_profiles(name, upgrade_policy, upgrade_cutoff_index,
               ranked_targets, is_default)
           VALUES('Upgrade fallback', 'until_cutoff', 0, '[]', 0)"""
    ).lastrowid
    conn.execute(
        "UPDATE lib2_tracks SET quality_profile_id=?, quality_profile_explicit=1 "
        "WHERE id=?", (upgrading, track_id)
    )
    conn.execute(
        """INSERT INTO lib2_wanted_tracks(track_id, profile_id, wanted, reason,
               effective_profile_id, projection_version)
           VALUES(?,?,1,'missing',NULL,?)
           ON CONFLICT(track_id, profile_id) DO UPDATE SET
               wanted=1, effective_profile_id=NULL,
               projection_version=excluded.projection_version""",
        (track_id, 1, PROJECTION_VERSION),
    )
    conn.commit()

    assert track_id in upgrade_candidate_track_ids(conn, profile_id=1)


# --------------------------------------------------------------------------
# dd28-12 — the Wishlist → lib2 forward edge
# --------------------------------------------------------------------------


def test_a_wishlist_addition_monitors_the_matching_lib2_track(imported_conn, legacy_db):
    """dd28-12: without this the hourly reconciler pruned the entry again."""
    from core.library2.monitor_sync import monitor_lib2_tracks_for_added_wishlist

    conn = imported_conn
    track_id = conn.execute("SELECT id FROM lib2_tracks LIMIT 1").fetchone()[0]
    conn.execute("UPDATE lib2_tracks SET monitored=0 WHERE id=?", (track_id,))
    conn.commit()

    stats = monitor_lib2_tracks_for_added_wishlist(
        legacy_db, [{"source_info": {"lib2_track_id": track_id}}], profile_id=1,
    )

    assert stats["matched"] == 1
    assert stats["monitored"] == 1
    assert conn.execute(
        "SELECT monitored FROM lib2_tracks WHERE id=?", (track_id,)
    ).fetchone()[0] == 1


def test_a_wishlist_addition_that_matches_nothing_is_a_no_op(imported_conn, legacy_db):
    from core.library2.monitor_sync import monitor_lib2_tracks_for_added_wishlist

    stats = monitor_lib2_tracks_for_added_wishlist(
        legacy_db, [{"source_info": {"lib2_track_id": 999_999}}], profile_id=1,
    )
    assert stats == {"matched": 0, "monitored": 0}


def test_the_forward_edge_is_admin_only(imported_conn, legacy_db):
    from core.settings import config_manager
    from core.library2.monitor_sync import sync_wishlist_addition

    conn = imported_conn
    track_id = conn.execute("SELECT id FROM lib2_tracks LIMIT 1").fetchone()[0]
    conn.execute("UPDATE lib2_tracks SET monitored=0 WHERE id=?", (track_id,))
    conn.commit()

    stats = sync_wishlist_addition(
        legacy_db, config_manager,
        [{"source_info": {"lib2_track_id": track_id}}], profile_id=42,
    )

    assert stats == {"matched": 0, "monitored": 0}
    assert conn.execute(
        "SELECT monitored FROM lib2_tracks WHERE id=?", (track_id,)
    ).fetchone()[0] == 0
