"""``sync_history.quality_profile_id`` must never be NULL for a stamped batch.

Review-round-2 findings R2-06/R2-07: the derive-from-tracks fallback lived only
in ``web_server._record_sync_history_start``. Every other caller of
``record_sync_history_start`` — notably ``core.discovery.sync.run_sync_task`` —
therefore recorded NULL, and a later "re-add to wishlist" resolved to the global
default instead of the profile the batch actually ran under.
"""

from __future__ import annotations

import pytest

from core.downloads.history import (
    quality_profile_id_for_tracks,
    record_sync_history_start,
)
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _tracks(profile_id):
    return [
        {"id": "t1", "name": "One", "quality_profile_id": profile_id},
        {"id": "t2", "name": "Two", "quality_profile_id": profile_id},
    ]


def _stored(db, playlist_id):
    entry = db.get_latest_sync_history_by_playlist(playlist_id, profile_id=1)
    return entry["quality_profile_id"] if entry else None


def test_derivation_takes_the_first_stamp_present():
    assert quality_profile_id_for_tracks(_tracks(7)) == 7
    assert quality_profile_id_for_tracks([{"id": "t1"}, {"id": "t2", "quality_profile_id": 9}]) == 9


def test_derivation_is_none_for_an_unstamped_batch():
    assert quality_profile_id_for_tracks([{"id": "t1"}]) is None
    assert quality_profile_id_for_tracks([]) is None
    assert quality_profile_id_for_tracks(None) is None


def test_writer_derives_the_profile_when_the_caller_omits_it(db):
    """The run_sync_task path passes profile_id only (R2-06)."""
    profile_id = db.create_quality_profile("Hi-Res", {})

    record_sync_history_start(
        db,
        batch_id="sync_1",
        playlist_id="pl-1",
        playlist_name="Mix",
        tracks=_tracks(profile_id),
        is_album_download=False,
        album_context=None,
        artist_context=None,
        playlist_folder_mode=False,
        source_page="sync",
        profile_id=1,
    )

    assert _stored(db, "pl-1") == profile_id


def test_an_explicit_profile_still_wins_over_the_derivation(db):
    explicit = db.create_quality_profile("Explicit", {})
    stamped = db.create_quality_profile("Stamped", {})

    record_sync_history_start(
        db,
        batch_id="sync_2",
        playlist_id="pl-2",
        playlist_name="Mix",
        tracks=_tracks(stamped),
        is_album_download=False,
        album_context=None,
        artist_context=None,
        playlist_folder_mode=False,
        source_page="sync",
        profile_id=1,
        quality_profile_id=explicit,
    )

    assert _stored(db, "pl-2") == explicit
