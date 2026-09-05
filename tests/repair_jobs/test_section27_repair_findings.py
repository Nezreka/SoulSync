"""Regression tests for the §27 Domain-A repair-tool findings.

dd28-18  multi-edition release groups corrupted track-number/total writes
dd28-19  unreachable storage was reported as a successful delete
dd28-20  an AcoustID retag emptied a track with no wanted recompute
dd28-27  a finding carried a TRACK id under entity_type='file'
dd28-30  in-scan mutations only synced to lib2 after the WHOLE scan
dd28-31  concurrent writers shared one fixed atomic-save staging name
dd28-32  removing a dead reference unmonitored a track that still had a file
"""

from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

import pytest

from core.repair_jobs.track_number_repair import (
    _api_tracks_for_subject,
    _edition_tracklists,
)


# --------------------------------------------------------------------------
# dd28-18 — edition-scoped tracklists
# --------------------------------------------------------------------------


def _album_with_two_editions(conn) -> tuple[int, dict]:
    conn.executescript(
        """
        CREATE TABLE lib2_tracks(
            id INTEGER PRIMARY KEY, album_id INT, title TEXT,
            track_number INT, disc_number INT);
        CREATE TABLE lib2_release_editions(
            id INTEGER PRIMARY KEY, release_group_id INT, is_default INT);
        CREATE TABLE lib2_release_tracks(
            id INTEGER PRIMARY KEY, release_edition_id INT, track_id INT,
            title_override TEXT, track_number INT, disc_number INT);
        """
    )
    album_id = 1
    conn.execute("INSERT INTO lib2_release_editions VALUES(10, 1, 1)")  # standard
    conn.execute("INSERT INTO lib2_release_editions VALUES(20, 1, 0)")  # deluxe
    ids = {}
    for number in range(1, 13):  # standard pressing: 12 tracks
        track_id = 100 + number
        conn.execute(
            "INSERT INTO lib2_tracks VALUES(?,?,?,?,1)",
            (track_id, album_id, f"Song {number}", number),
        )
        conn.execute(
            "INSERT INTO lib2_release_tracks VALUES(?,10,?,NULL,?,1)",
            (track_id, track_id, number),
        )
        ids[f"std{number}"] = track_id
    for number in range(1, 17):  # deluxe pressing: 16 tracks
        track_id = 200 + number
        conn.execute(
            "INSERT INTO lib2_tracks VALUES(?,?,?,?,1)",
            (track_id, album_id, f"Song {number}", number),
        )
        conn.execute(
            "INSERT INTO lib2_release_tracks VALUES(?,20,?,NULL,?,1)",
            (track_id, track_id, number),
        )
        ids[f"dlx{number}"] = track_id
    conn.commit()
    return album_id, ids


@pytest.fixture
def two_edition_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def test_a_files_tracklist_is_its_own_edition_not_the_union(two_edition_conn):
    """dd28-18: the union made disc_total 28 instead of 12, and every file got
    ``N/28`` written into it — with dry_run False that also renamed them."""
    album_id, ids = _album_with_two_editions(two_edition_conn)
    editions, of_track = _edition_tracklists(two_edition_conn, album_id)
    group_tracks = [dict(r) for r in two_edition_conn.execute(
        "SELECT id AS lib2_track_id, title AS name, track_number, disc_number "
        "FROM lib2_tracks WHERE album_id=?", (album_id,))]

    assert len(group_tracks) == 28, "the release GROUP really does hold both"

    standard = _api_tracks_for_subject(
        {"track_id": ids["std3"]}, group_tracks, editions, of_track,
    )
    deluxe = _api_tracks_for_subject(
        {"track_id": ids["dlx3"]}, group_tracks, editions, of_track,
    )

    assert len(standard) == 12
    assert len(deluxe) == 16


def test_a_single_edition_album_is_unchanged(two_edition_conn):
    two_edition_conn.executescript(
        """
        CREATE TABLE lib2_tracks(
            id INTEGER PRIMARY KEY, album_id INT, title TEXT,
            track_number INT, disc_number INT);
        CREATE TABLE lib2_release_editions(
            id INTEGER PRIMARY KEY, release_group_id INT, is_default INT);
        CREATE TABLE lib2_release_tracks(
            id INTEGER PRIMARY KEY, release_edition_id INT, track_id INT,
            title_override TEXT, track_number INT, disc_number INT);
        INSERT INTO lib2_release_editions VALUES(10, 1, 1);
        INSERT INTO lib2_tracks VALUES(101, 1, 'Song 1', 1, 1);
        INSERT INTO lib2_release_tracks VALUES(1, 10, 101, NULL, 1, 1);
        """
    )
    editions, of_track = _edition_tracklists(two_edition_conn, 1)
    group_tracks = [{"lib2_track_id": 101, "name": "Song 1", "track_number": 1}]

    assert _api_tracks_for_subject(
        {"track_id": 101}, group_tracks, editions, of_track,
    ) == group_tracks


def test_an_album_without_edition_rows_falls_back_to_the_group():
    group_tracks = [{"lib2_track_id": 1, "name": "x", "track_number": 1}]
    assert _api_tracks_for_subject({"track_id": 1}, group_tracks, {}, {}) == group_tracks


def test_an_ambiguous_edition_yields_no_tracklist(two_edition_conn):
    """Writing a plausible-looking WRONG number is worse than no finding."""
    album_id, ids = _album_with_two_editions(two_edition_conn)
    # Same track row shared by both editions — the ambiguous case.
    two_edition_conn.execute(
        "INSERT INTO lib2_release_tracks VALUES(999, 20, ?, NULL, 5, 1)",
        (ids["std3"],),
    )
    two_edition_conn.commit()
    editions, of_track = _edition_tracklists(two_edition_conn, album_id)
    group_tracks = [{"lib2_track_id": ids["std3"], "name": "x", "track_number": 3}]

    assert _api_tracks_for_subject(
        {"track_id": ids["std3"]}, group_tracks, editions, of_track,
    ) == []


# --------------------------------------------------------------------------
# dd28-19 — deletion vs unreachable storage
# --------------------------------------------------------------------------


class _Worker:
    """Just the methods under test, with no worker bootstrap."""

    from core.repair_worker import RepairWorker

    _remove_native_repair_file = RepairWorker._remove_native_repair_file
    _other_usable_lib2_files = RepairWorker._other_usable_lib2_files
    _delete_journal_subject = RepairWorker._delete_journal_subject

    def __init__(self, config_manager=None, db=None):
        self._config_manager = config_manager
        self.db = db


@pytest.fixture
def journal_db(tmp_path):
    """A database the ADR-05 delete journal can be written to.

    Every physical delete is journalled now, including the maintenance ones,
    so a worker without a database can no longer delete — deliberately: a
    delete nobody recorded is the thing this work exists to remove.
    """
    path = str(tmp_path / "journal.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE lib2_track_files(
               id INTEGER PRIMARY KEY, track_id INT, path TEXT,
               file_state TEXT DEFAULT 'active')"""
    )
    conn.execute("CREATE TABLE lib2_tracks(id INTEGER PRIMARY KEY, album_id INT)")
    conn.commit()
    conn.close()

    class _DB:
        def _get_connection(self):
            c = sqlite3.connect(path)
            c.row_factory = sqlite3.Row
            return c

    return _DB()


def test_a_deleted_file_reports_a_real_delete(tmp_path, journal_db):
    target = tmp_path / "song.flac"
    target.write_bytes(b"x")
    worker = _Worker(config_manager=SimpleNamespace(get=lambda *a, **k: []),
                     db=journal_db)

    result = worker._remove_native_repair_file(str(target), {})

    assert result['success'] is True
    assert result['deleted_file'] is True
    assert result['resolved_path'] == str(target)
    assert result['delete_operation_id'], 'the delete must be on the record'
    assert not target.exists()


def test_an_already_absent_file_under_a_healthy_root_is_not_an_error(
        tmp_path, journal_db):
    worker = _Worker(config_manager=SimpleNamespace(get=lambda *a, **k: []),
                     db=journal_db)

    result = worker._remove_native_repair_file(str(tmp_path / "gone.flac"), {})

    assert result == {'success': True, 'deleted_file': False}


def test_unreachable_storage_is_reported_as_a_failure(tmp_path, journal_db):
    """dd28-19: this used to return success, so sync_repair_change set
    file_state='deleted' and flipped monitoring — deleting a file from the
    catalog that still exists on an unmounted NAS, and queueing a redownload."""
    worker = _Worker(config_manager=SimpleNamespace(get=lambda *a, **k: []),
                     db=journal_db)
    unreachable = str(tmp_path / "not-mounted" / "song.flac")

    result = worker._remove_native_repair_file(unreachable, {})

    assert result['success'] is False
    assert 'not reachable' in result['error']


# --------------------------------------------------------------------------
# dd28-32 — a dead reference is about the FILE
# --------------------------------------------------------------------------


@pytest.fixture
def files_db(tmp_path):
    path = str(tmp_path / "files.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE lib2_track_files(
               id INTEGER PRIMARY KEY, track_id INT, path TEXT,
               file_state TEXT DEFAULT 'active')"""
    )
    conn.commit()
    conn.close()

    class _DB:
        def _get_connection(self):
            c = sqlite3.connect(path)
            c.row_factory = sqlite3.Row
            return c

    return _DB(), path


def test_a_surviving_sibling_file_is_detected(files_db):
    db, path = files_db
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(1, '/m/a.flac')")
    conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(1, '/m/a.mp3')")
    conn.commit()
    conn.close()

    worker = _Worker(db=db)
    assert worker._other_usable_lib2_files(1, '/m/a.flac')


def test_the_last_file_leaves_no_sibling(files_db):
    db, path = files_db
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(1, '/m/a.flac')")
    conn.commit()
    conn.close()

    worker = _Worker(db=db)
    assert worker._other_usable_lib2_files(1, '/m/a.flac') == []


def test_a_deleted_sibling_does_not_count(files_db):
    db, path = files_db
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(1, '/m/a.flac')")
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, file_state) "
        "VALUES(1, '/m/a.mp3', 'deleted')"
    )
    conn.commit()
    conn.close()

    worker = _Worker(db=db)
    assert worker._other_usable_lib2_files(1, '/m/a.flac') == []


# --------------------------------------------------------------------------
# dd28-30 / dd28-31 — durability and concurrency of the fix paths
# --------------------------------------------------------------------------


def test_the_repair_worker_flushes_changes_in_batches():
    """dd28-30: a process death mid-run lost the WHOLE run's lib2 convergence."""
    from core.repair_worker import _CHANGE_SYNC_BATCH

    assert 0 < _CHANGE_SYNC_BATCH <= 100


def test_atomic_save_stages_under_a_per_writer_name(tmp_path, monkeypatch):
    """dd28-31: a fixed `<path>.sstmp` collided between a bulk fix and a scan's
    own auto-fix; the loser fell back to the non-atomic in-place save."""
    from core.metadata import common

    target = tmp_path / "song.flac"
    target.write_bytes(b"ORIGINAL")

    seen: list = []

    def _capture(audio_file, symbols, target=None):
        if target:
            seen.append(target)
            with open(target, "ab") as handle:
                handle.write(b"+T")

    monkeypatch.setattr(common, "_raw_audio_save", _capture)
    monkeypatch.setattr(common, "_audio_intact", lambda *a, **k: True)
    monkeypatch.setattr(common, "_flac_audio_identical", lambda *a, **k: True)

    audio = SimpleNamespace(filename=str(target))
    symbols = SimpleNamespace(File=lambda p: SimpleNamespace(info=None), FLAC=None)
    common.save_audio_file(audio, symbols)
    common.save_audio_file(audio, symbols)

    assert len(seen) == 2
    assert seen[0] != seen[1], "two writers must not share one staging name"
    assert all(name.endswith(".sstmp") for name in seen)
    assert not list(tmp_path.glob("*.sstmp"))


# --------------------------------------------------------------------------
# dd28-27 — identity of a file-typed finding
# --------------------------------------------------------------------------


def test_fake_lossless_reports_its_own_file_id():
    """The id under entity_type='file' must be a FILE id: the id spaces
    overlap, so a track id silently resolved to an unrelated file."""
    import inspect

    from core.repair_jobs import fake_lossless_detector

    source = inspect.getsource(fake_lossless_detector)
    assert "entity_id=f\"lib2:{subject['file_id']}\"" in source
    assert "entity_id=f\"lib2:{subject['track_id']}\"" not in source


# --------------------------------------------------------------------------
# dd28-20 — an emptied track must be reprojected
# --------------------------------------------------------------------------


def test_the_sync_bridge_honours_a_forced_wanted_recompute():
    import inspect

    from core.library2 import maintenance_sync

    source = inspect.getsource(maintenance_sync)
    assert 'result.get("library_v2_recompute_wanted")' in source
