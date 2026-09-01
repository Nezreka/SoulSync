"""End-to-end check for the same-run orphan-cleanup fix (Nezreka/SoulSync#1216,
PR #1217) — exercises the REAL DatabaseUpdateWorker code path
(_process_artist_with_content) against a real on-disk MusicDatabase schema,
not just cleanup_orphaned_records() in isolation
(see tests/database/test_cleanup_orphaned_records_exclusion.py for that).

Reproduces the exact failure shape traced live on Rare Funk & Disco 24: an
artist whose album fetch comes back with the album but an EMPTY track list
on one run (simulating an incomplete upstream response), then a full track
list on a later run once the fetch behaves.
"""

from __future__ import annotations

import pytest

from core.database_update_worker import DatabaseUpdateWorker
from database.music_database import MusicDatabase


class _Track:
    def __init__(self, rating_key, title, track_number):
        self.ratingKey = rating_key
        self.title = title
        self.trackNumber = track_number


class _Album:
    def __init__(self, rating_key, title, tracks):
        self.ratingKey = rating_key
        self.title = title
        self._tracks = tracks

    def tracks(self):
        return self._tracks


class _Artist:
    def __init__(self, rating_key, title, albums):
        self.ratingKey = rating_key
        self.title = title
        self._albums = albums

    def albums(self):
        return self._albums


@pytest.fixture()
def dbpath(tmp_path):
    return str(tmp_path / "music.db")


def _worker(dbpath):
    worker = DatabaseUpdateWorker(
        media_client=None,  # unused directly by _process_artist_with_content
        database_path=dbpath,
        server_type="navidrome",
        full_refresh=False,
        force_sequential=True,
    )
    # run() normally sets this via get_database(); _process_artist_with_content
    # is called directly here (bypassing run()), so wire it the same way
    # the real web-server call site does (api/database_admin.py:
    # ``worker.database = database``).
    worker.database = MusicDatabase(dbpath)
    return worker


def test_incomplete_fetch_survives_same_run_cleanup_then_completes_next_run(dbpath):
    album_id = "album-rare-funk"
    artist_id = "artist-various"

    # ── Run 1: the album is inserted, but its own track fetch came back
    # empty on this call (the exact condition that used to be fatal) ──
    empty_album = _Album(album_id, "Rare Funk & Disco 24", tracks=[])
    artist_run1 = _Artist(artist_id, "Various Artists", albums=[empty_album])

    worker1 = _worker(dbpath)
    success, details, album_count, track_count = worker1._process_artist_with_content(artist_run1)
    assert success is True
    assert album_count == 1
    assert track_count == 0
    assert album_id in worker1._touched_album_ids
    assert artist_id in worker1._touched_artist_ids

    # Simulate the real end-of-run cleanup call, passing this run's touched ids
    # exactly like run() does.
    cleanup1 = worker1.database.cleanup_orphaned_records(
        exclude_artist_ids=worker1._touched_artist_ids,
        exclude_album_ids=worker1._touched_album_ids,
    )
    assert cleanup1["orphaned_albums_removed"] == 0
    assert cleanup1["orphaned_artists_removed"] == 0

    row = worker1.database._get_connection().execute(
        "SELECT id FROM albums WHERE id = ?", (album_id,)
    ).fetchone()
    assert row is not None, "album must survive the same run's cleanup despite having 0 tracks"

    track_row_count = worker1.database._get_connection().execute(
        "SELECT COUNT(*) FROM tracks WHERE album_id = ?", (album_id,)
    ).fetchone()[0]
    assert track_row_count == 0

    # ── Run 2 (fresh worker — new process/scan, like the real hourly
    # automation instantiating a new DatabaseUpdateWorker each time): the
    # upstream fetch now behaves and returns all 16 tracks. Confirm the
    # surviving album row gets completed, not treated as a duplicate/stuck. ──
    full_tracks = [_Track(f"track-{i}", f"Song {i}", i) for i in range(1, 17)]
    full_album = _Album(album_id, "Rare Funk & Disco 24", tracks=full_tracks)
    artist_run2 = _Artist(artist_id, "Various Artists", albums=[full_album])

    worker2 = _worker(dbpath)
    success2, _, album_count2, track_count2 = worker2._process_artist_with_content(artist_run2)
    assert success2 is True
    assert album_count2 == 1
    assert track_count2 == 16

    cleanup2 = worker2.database.cleanup_orphaned_records(
        exclude_artist_ids=worker2._touched_artist_ids,
        exclude_album_ids=worker2._touched_album_ids,
    )
    assert cleanup2["orphaned_albums_removed"] == 0

    final_track_count = worker2.database._get_connection().execute(
        "SELECT COUNT(*) FROM tracks WHERE album_id = ?", (album_id,)
    ).fetchone()[0]
    assert final_track_count == 16


def test_album_still_trackless_after_a_later_untouched_run_is_genuinely_cleaned(dbpath):
    """The fix protects a row for the run that touched it — it must NOT
    become permanently immune. If a later run doesn't touch this artist at
    all (e.g. it dropped out of the incremental scan's recent-albums window)
    and the album is still trackless, that run's cleanup should remove it
    like any other real orphan."""
    album_id = "album-stuck-forever"
    artist_id = "artist-stuck"

    empty_album = _Album(album_id, "Never Gets Tracks", tracks=[])
    artist = _Artist(artist_id, "Some Artist", albums=[empty_album])

    worker1 = _worker(dbpath)
    worker1._process_artist_with_content(artist)
    worker1.database.cleanup_orphaned_records(
        exclude_artist_ids=worker1._touched_artist_ids,
        exclude_album_ids=worker1._touched_album_ids,
    )
    still_there = worker1.database._get_connection().execute(
        "SELECT id FROM albums WHERE id = ?", (album_id,)
    ).fetchone()
    assert still_there is not None

    # A later run's cleanup, with nothing touched this time (this artist
    # wasn't in scope for this scan at all) — must remove the still-trackless row.
    worker2 = _worker(dbpath)
    cleanup2 = worker2.database.cleanup_orphaned_records(
        exclude_artist_ids=worker2._touched_artist_ids,  # empty
        exclude_album_ids=worker2._touched_album_ids,    # empty
    )
    assert cleanup2["orphaned_albums_removed"] == 1
    assert cleanup2["orphaned_artists_removed"] == 1

    gone = worker2.database._get_connection().execute(
        "SELECT id FROM albums WHERE id = ?", (album_id,)
    ).fetchone()
    assert gone is None
