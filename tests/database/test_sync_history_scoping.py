"""Sync history carries a SoulSync profile and a Quality Profile.

Audit finding P1-04: ``sync_history`` had no profile dimension at all, so every
profile saw (and could delete) every other profile's runs, and a "re-add to
wishlist" from that list recreated the track under the admin profile with the
global default Quality Profile — even when the original request explicitly used
neither.
"""

from __future__ import annotations

import json

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _add(db, batch_id, *, profile_id=1, quality_profile_id=None, name="Mix"):
    return db.add_sync_history_entry(
        batch_id=batch_id,
        playlist_id="pl-1",
        playlist_name=name,
        source="spotify",
        sync_type="manual",
        tracks_json=json.dumps([{"id": "t1", "name": "Song"}]),
        profile_id=profile_id,
        quality_profile_id=quality_profile_id,
    )


def test_entry_persists_both_profiles(db):
    quality_id = db.create_quality_profile("Hi-Res", {})
    assert _add(db, "b1", profile_id=7, quality_profile_id=quality_id)

    entries, total = db.get_sync_history(profile_id=7)
    assert total == 1
    row = db.get_sync_history_entry(entries[0]["id"], profile_id=7)
    assert row["profile_id"] == 7
    assert row["quality_profile_id"] == quality_id


def test_listing_is_profile_scoped(db):
    _add(db, "b-admin", profile_id=1, name="Admin Mix")
    _add(db, "b-other", profile_id=7, name="Other Mix")

    admin_entries, admin_total = db.get_sync_history(profile_id=1)
    other_entries, other_total = db.get_sync_history(profile_id=7)

    assert admin_total == other_total == 1
    assert admin_entries[0]["playlist_name"] == "Admin Mix"
    assert other_entries[0]["playlist_name"] == "Other Mix"


def test_entry_read_and_delete_are_profile_scoped(db):
    _add(db, "b-other", profile_id=7)
    entry_id = db.get_sync_history(profile_id=7)[0][0]["id"]

    assert db.get_sync_history_entry(entry_id, profile_id=1) is None
    assert db.delete_sync_history_entry(entry_id, profile_id=1) is False
    assert db.get_sync_history_entry(entry_id, profile_id=7) is not None

    assert db.delete_sync_history_entry(entry_id, profile_id=7) is True
    assert db.get_sync_history_entry(entry_id, profile_id=7) is None


def test_two_profiles_can_hold_the_same_playlist_independently(db):
    _add(db, "b1", profile_id=1, name="Shared")
    _add(db, "b2", profile_id=7, name="Shared")

    assert db.get_sync_history_playlist_names(profile_id=1) == ["Shared"]
    assert db.get_sync_history_playlist_names(profile_id=7) == ["Shared"]
    assert db.get_sync_history_stats(profile_id=7) == {"spotify": 1}


def test_legacy_rows_without_a_profile_stay_visible_to_admin(db):
    """Pre-migration rows have NULL profile_id and must not vanish from the UI."""
    _add(db, "b-legacy", profile_id=1)
    with db._get_connection() as conn:
        conn.execute("UPDATE sync_history SET profile_id = NULL")
        conn.commit()

    entries, total = db.get_sync_history(profile_id=1)
    assert total == 1
    assert db.get_sync_history_entry(entries[0]["id"], profile_id=1) is not None
    # …but they must not leak into another profile's view.
    assert db.get_sync_history(profile_id=7)[1] == 0


def test_unscoped_reads_still_see_everything(db):
    """Internal/maintenance callers keep the historic unscoped behaviour."""
    _add(db, "b1", profile_id=1)
    _add(db, "b2", profile_id=7)

    assert db.get_sync_history()[1] == 2
