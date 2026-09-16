"""the sync's match count is not overwritten by the download batch that follows.

storm: the dashboard sync card read "0/240 in library" and the details
modal's footer "0 matched · 0 downloaded" while the modal's own rows
showed 236 matched. two writers share the sync_history row: the sync
writes how many tracks matched, then the download batch for the missing
ones writes what downloaded. a batch that ran no analysis of its own
computed tracks_found from an empty list and wrote 0 over the sync's
number; its (empty) track results were skipped, which is why the rows
still said 236.
"""

from __future__ import annotations

import json

import pytest

from core.downloads.history import record_sync_history_completion
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _entry(db, batch_id="b1"):
    db.add_sync_history_entry(batch_id, "pl1", "Miku my beloved", "spotify", "playlist",
                              json.dumps([]), total_tracks=240)
    return db.get_latest_sync_history_by_playlist("pl1")


def _counts(db):
    row = db.get_latest_sync_history_by_playlist("pl1")
    return row["tracks_found"], row["tracks_downloaded"], row["tracks_failed"]


def test_a_batch_with_no_analysis_keeps_the_syncs_match_count(db):
    _entry(db)
    db.update_sync_history_completion("b1", tracks_found=236, tracks_downloaded=0, tracks_failed=4)
    assert _counts(db) == (236, 0, 4)

    # the download batch for the four missing tracks: one landed, no analysis
    record_sync_history_completion(db, "b1", {
        "analysis_results": [],
        "queue": [],
        "permanently_failed_tracks": ["x", "y", "z"],
    })
    assert _counts(db) == (236, 0, 3)


def test_a_batch_with_its_own_analysis_still_writes_what_it_found(db):
    _entry(db)
    db.update_sync_history_completion("b1", tracks_found=236, tracks_downloaded=0, tracks_failed=4)
    record_sync_history_completion(db, "b1", {
        "analysis_results": [
            {"found": True, "track": {"name": "a", "artists": [{"name": "A"}], "album": {"name": "X"}}, "track_index": 0},
            {"found": False, "track": {"name": "b", "artists": [{"name": "B"}], "album": {"name": "Y"}}, "track_index": 1},
        ],
        "queue": [],
        "permanently_failed_tracks": [],
    })
    assert _counts(db)[0] == 1


def test_none_keeps_and_a_number_replaces(db):
    _entry(db)
    db.update_sync_history_completion("b1", tracks_found=10, tracks_downloaded=1, tracks_failed=0)
    db.update_sync_history_completion("b1", tracks_found=None, tracks_downloaded=2, tracks_failed=0)
    assert _counts(db) == (10, 2, 0)
    db.update_sync_history_completion("b1", tracks_found=0, tracks_downloaded=2, tracks_failed=0)
    assert _counts(db) == (0, 2, 0)
