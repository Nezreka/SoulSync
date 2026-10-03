"""Deleting a mirrored playlist must cascade to its Auto-Sync schedule automations.

Issue #1455: the dashboard Sync band renders schedule rows from pipeline
automations (not mirrors), so a deleted mirror's orphaned automations showed up
as ghost rows named "Playlist #<id>" and kept firing error runs.

The cascade must ONLY remove automations the Auto-Sync board owns
(``owned_by='auto_sync'``) that are scoped to the deleted playlist id:
user-created automations and 'all' schedules (covering every playlist) survive.
"""

from __future__ import annotations

import json

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _mirror(db, *, profile_id: int = 1, source_playlist_id: str = "p1", name: str = "Mix"):
    return db.mirror_playlist(
        source="spotify",
        source_playlist_id=source_playlist_id,
        name=name,
        tracks=[{"track_name": "Song", "artist_name": "Artist"}],
        profile_id=profile_id,
    )


def _auto_sync_schedule(db, playlist_id, *, profile_id: int = 1, all_playlists: bool = False):
    cfg = {"all": True} if all_playlists else {"playlist_id": str(playlist_id), "all": False}
    return db.create_automation(
        name=f"Auto-Sync: mix {playlist_id}",
        trigger_type="schedule",
        trigger_config=json.dumps({"hours": [8]}),
        action_type="playlist_pipeline",
        action_config=json.dumps(cfg),
        profile_id=profile_id,
        group_name="Playlist Auto-Sync",
        owned_by="auto_sync",
    )


def _user_automation(db, *, profile_id: int = 1):
    return db.create_automation(
        name="My nightly cleanup",
        trigger_type="schedule",
        trigger_config=json.dumps({"hours": [22]}),
        action_type="library_cleanup",
        action_config=json.dumps({"dry_run": True}),
        profile_id=profile_id,
    )


def _automation_ids(db):
    return {a["id"] for a in db.get_all_automations()}


def test_delete_removes_board_owned_schedule_for_that_playlist(db):
    pk = _mirror(db)
    scoped = _auto_sync_schedule(db, pk)
    other = _auto_sync_schedule(db, pk + 999)

    assert db.delete_mirrored_playlist(pk, profile_id=1) is True

    remaining = _automation_ids(db)
    assert scoped not in remaining
    assert other in remaining


def test_delete_keeps_user_created_automations(db):
    pk = _mirror(db)
    user_auto = _user_automation(db)

    assert db.delete_mirrored_playlist(pk, profile_id=1) is True

    assert user_auto in _automation_ids(db)


def test_delete_keeps_all_playlists_schedules(db):
    pk = _mirror(db)
    all_auto = _auto_sync_schedule(db, pk, all_playlists=True)

    assert db.delete_mirrored_playlist(pk, profile_id=1) is True

    assert all_auto in _automation_ids(db)


def test_delete_cascade_is_profile_scoped(db):
    pk = _mirror(db, profile_id=2)
    foreign_schedule = _auto_sync_schedule(db, pk, profile_id=2)

    # A foreign delete must not remove anything — mirror or automation.
    assert db.delete_mirrored_playlist(pk, profile_id=1) is False
    assert foreign_schedule in _automation_ids(db)

    # The owning profile's delete cascades as usual.
    assert db.delete_mirrored_playlist(pk, profile_id=2) is True
    assert foreign_schedule not in _automation_ids(db)


def test_delete_with_no_automations_still_succeeds(db):
    pk = _mirror(db)

    assert db.delete_mirrored_playlist(pk, profile_id=1) is True
    assert db.get_mirrored_playlist(pk) is None
