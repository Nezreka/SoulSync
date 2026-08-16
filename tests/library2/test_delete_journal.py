"""One journal for every physical delete (ADR-05, step 1 of the unification).

Until now there were two ways a file left the disk. The Library-v2 dialog went
through ``delete_entity_files``: preview token, root containment, an operation
row per delete, per-file result, crash recovery. The maintenance worker went
through a bare ``os.remove`` behind its own guards — no operation row, nothing
in the History feed's delete events, and a crash mid-run left no trace to
recover from.

That asymmetry is tolerable only while a human confirms every single deletion.
It is not tolerable the moment a job deletes unattended, which is what this
work is preparing for. ``delete_files_journaled`` is the shared primitive: the
same containment rule, the same journal, the same statuses, callable with a
list of paths instead of an entity preview.
"""

from __future__ import annotations

import os

import pytest

from core.library2.file_delete import (
    delete_files_journaled,
    reconcile_incomplete_deletes,
)


class _Config:
    def __init__(self, roots):
        self.roots = roots

    def get(self, key, default=None):
        assert key == "library.music_paths"
        return self.roots


def _album_with_file(conn, path):
    """Point one imported track's only file at ``path``; return (album, file)."""
    track_id, album_id = conn.execute(
        "SELECT id, album_id FROM lib2_tracks ORDER BY id LIMIT 1"
    ).fetchone()
    conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (track_id,))
    cursor = conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, is_primary) VALUES(?,?,1)",
        (track_id, str(path)),
    )
    conn.commit()
    return int(album_id), int(cursor.lastrowid)


def _operations(conn):
    return [dict(row) for row in conn.execute(
        "SELECT * FROM lib2_file_delete_operations ORDER BY id"
    )]


def _items(conn):
    return [dict(row) for row in conn.execute(
        "SELECT * FROM lib2_file_delete_items ORDER BY id"
    )]


def test_a_journalled_delete_removes_the_file_and_records_the_operation(
        imported_conn, legacy_db, tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    target = root / "track.flac"
    target.write_bytes(b"audio")
    album_id, file_id = _album_with_file(imported_conn, target)

    result = delete_files_journaled(
        legacy_db,
        targets=[str(target)],
        entity_type="albums",
        entity_id=album_id,
        actor="repair:corrupt_audio",
        config_manager=_Config([str(root)]),
    )

    assert not target.exists(), "the file was supposed to be deleted"
    assert result["deleted"] == [str(target.resolve())]
    assert result["failed"] == []

    operation = _operations(imported_conn)[0]
    assert operation["status"] == "completed"
    assert operation["entity_type"] == "albums"
    assert operation["entity_id"] == album_id
    assert operation["mode"] == "permanent"
    # The actor is the whole point of journalling an unattended delete: the
    # History feed prints it, so "who deleted my album" has an answer that is
    # not "someone, at some point".
    assert operation["actor"] == "repair:corrupt_audio"

    item = _items(imported_conn)[0]
    assert item["status"] == "deleted"
    assert item["resolved_path"] == str(target.resolve())
    assert item["root_path"] == str(root.resolve())


def test_the_catalogue_row_follows_the_file(imported_conn, legacy_db, tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    target = root / "track.flac"
    target.write_bytes(b"audio")
    album_id, file_id = _album_with_file(imported_conn, target)

    delete_files_journaled(
        legacy_db,
        targets=[str(target)],
        entity_type="albums",
        entity_id=album_id,
        actor="repair:corrupt_audio",
        config_manager=_Config([str(root)]),
    )

    state = imported_conn.execute(
        "SELECT file_state FROM lib2_track_files WHERE id=?", (file_id,)
    ).fetchone()[0]
    assert state == "deleted"


def test_a_path_outside_every_configured_root_is_refused(
        imported_conn, legacy_db, tmp_path):
    """Fail closed, per file. The containment rule is the whole safety story
    for an unattended delete — a job that resolved a path wrongly must not be
    able to reach outside the library."""
    root = tmp_path / "music"
    root.mkdir()
    outside = tmp_path / "elsewhere.flac"
    outside.write_bytes(b"audio")
    album_id, _ = _album_with_file(imported_conn, outside)

    result = delete_files_journaled(
        legacy_db,
        targets=[str(outside)],
        entity_type="albums",
        entity_id=album_id,
        actor="repair:corrupt_audio",
        config_manager=_Config([str(root)]),
    )

    assert outside.exists(), "a file outside the library roots must survive"
    assert result["deleted"] == []
    assert result["failed"][0]["error"] == "outside_configured_library_roots"
    assert _operations(imported_conn)[0]["status"] == "partial"


def test_no_configured_roots_means_nothing_is_deletable(
        imported_conn, legacy_db, tmp_path):
    """With nothing to validate against there is no safe answer, so the answer
    is no — the same rule ``fuzzy_resolved_path_is_deletable`` already uses."""
    target = tmp_path / "track.flac"
    target.write_bytes(b"audio")
    album_id, _ = _album_with_file(imported_conn, target)

    result = delete_files_journaled(
        legacy_db,
        targets=[str(target)],
        entity_type="albums",
        entity_id=album_id,
        actor="repair:corrupt_audio",
        config_manager=_Config([]),
    )

    assert target.exists()
    assert result["deleted"] == []


def test_one_bad_path_does_not_sink_the_rest(imported_conn, legacy_db, tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    good = root / "good.flac"
    good.write_bytes(b"audio")
    outside = tmp_path / "bad.flac"
    outside.write_bytes(b"audio")
    album_id, _ = _album_with_file(imported_conn, good)

    result = delete_files_journaled(
        legacy_db,
        targets=[str(good), str(outside)],
        entity_type="albums",
        entity_id=album_id,
        actor="repair:unwanted_content",
        config_manager=_Config([str(root)]),
    )

    assert not good.exists()
    assert outside.exists()
    assert result["deleted"] == [str(good.resolve())]
    assert len(result["failed"]) == 1


def test_a_crash_between_journal_and_unlink_is_recoverable(
        imported_conn, legacy_db, tmp_path):
    """The state is written before the unlink, so a process that dies in
    between leaves evidence. Recovery finishes the bookkeeping for a file that
    did go, and refuses to delete one that is still there."""
    root = tmp_path / "music"
    root.mkdir()
    target = root / "track.flac"
    target.write_bytes(b"audio")
    album_id, file_id = _album_with_file(imported_conn, target)

    def _die(path):
        raise KeyboardInterrupt("process died mid-delete")

    with pytest.raises(KeyboardInterrupt):
        delete_files_journaled(
            legacy_db,
            targets=[str(target)],
            entity_type="albums",
            entity_id=album_id,
            actor="repair:corrupt_audio",
            config_manager=_Config([str(root)]),
            unlink=_die,
        )

    assert _items(imported_conn)[0]["status"] == "deleting"
    os.remove(target)                      # the unlink had in fact happened

    assert reconcile_incomplete_deletes(legacy_db) == 1
    assert _items(imported_conn)[0]["status"] == "deleted"
    assert imported_conn.execute(
        "SELECT file_state FROM lib2_track_files WHERE id=?", (file_id,)
    ).fetchone()[0] == "deleted"


class _WorkerConfig:
    """Permissive config for the worker path, which reads more than roots."""

    def __init__(self, roots):
        self.roots = roots

    def get(self, key, default=None):
        if key == "library.music_paths":
            return self.roots
        return default


def _worker(database, config):
    from core.repair_worker import RepairWorker

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = database
    worker._config_manager = config
    return worker


def test_a_maintenance_delete_lands_in_the_same_journal(
        imported_conn, legacy_db, tmp_path):
    """The point of the whole exercise. Before this, a job deleted with a bare
    ``os.remove`` and the album's History had nothing to show for it."""
    root = tmp_path / "music"
    root.mkdir()
    target = root / "clip.flac"
    target.write_bytes(b"audio")
    album_id, _ = _album_with_file(imported_conn, target)

    result = _worker(legacy_db, _WorkerConfig([str(root)]))._remove_native_repair_file(
        str(target), {}, reason="corrupt_audio",
    )

    assert result["success"] is True
    assert result["deleted_file"] is True
    assert not target.exists()

    operation = _operations(imported_conn)[0]
    assert operation["status"] == "completed"
    assert operation["actor"] == "repair:corrupt_audio"
    # Filed against the album, because that is what the History feed queries —
    # an operation recorded against nothing is a journal entry nobody can find.
    assert operation["entity_type"] == "albums"
    assert operation["entity_id"] == album_id


def test_a_maintenance_delete_of_an_unknown_file_still_journals(
        imported_conn, legacy_db, tmp_path):
    """An orphan file has no catalogue row to hang the operation on. It is
    still a file that left the disk, so it is still journalled."""
    root = tmp_path / "music"
    root.mkdir()
    stray = root / "stray.flac"
    stray.write_bytes(b"audio")

    result = _worker(legacy_db, _WorkerConfig([str(root)]))._remove_native_repair_file(
        str(stray), {}, reason="orphan_file",
    )

    assert result["success"] is True
    assert not stray.exists()
    assert _operations(imported_conn)[0]["actor"] == "repair:orphan_file"


def test_the_maintenance_path_deletes_exactly_what_it_deleted_before(
        imported_conn, legacy_db, tmp_path):
    """Journalling must not change WHICH files a job may delete.

    A path the catalogue names exactly is deletable even when it sits outside
    ``library.music_paths`` — plenty of libraries never listed their folders
    there, and the maintenance fixes have always worked for them. The stricter
    containment rule belongs to switching unattended deletion on, not to
    writing deletions down. What the worker still refuses is a path the
    resolver GUESSED (``fuzzy_resolved_path_is_deletable``); that rule is
    unchanged and covered by tests/library2/test_fuzzy_delete_containment.py.
    """
    outside = tmp_path / "outside.flac"
    outside.write_bytes(b"audio")
    _album_with_file(imported_conn, outside)

    result = _worker(legacy_db, _WorkerConfig([]))._remove_native_repair_file(
        str(outside), {}, reason="corrupt_audio",
    )

    assert result["success"] is True
    assert not outside.exists()
    # …and it is on the record, which is the part that IS new.
    assert _operations(imported_conn)[0]["actor"] == "repair:corrupt_audio"


def test_deleting_an_orphan_file_is_journalled_too(
        imported_conn, legacy_db, tmp_path):
    """An orphan is a file the catalogue does not know — which is exactly why
    its deletion needs a record somewhere."""
    root = tmp_path / "music"
    root.mkdir()
    orphan = root / "nobody-knows-me.flac"
    orphan.write_bytes(b"audio")
    worker = _worker(legacy_db, _WorkerConfig([str(root)]))
    worker.transfer_folder = str(root)

    result = worker._fix_orphan_file(
        "file", None, str(orphan), {"_fix_action": "delete"},
    )

    assert result["success"] is True
    assert not orphan.exists()
    assert _operations(imported_conn)[0]["actor"] == "repair:orphan_file"


def test_the_lossy_converters_seam_journals_the_original_it_replaces(
        imported_conn, legacy_db, tmp_path):
    """"Blasphemy mode" deletes a lossless original after transcoding it — the
    single most consequential delete the worker performs, and until now the
    least visible. Driving the converter itself needs ffmpeg, so this covers
    the seam it now calls (``_remove_native_repair_file`` with its own reason)
    rather than the transcode."""
    root = tmp_path / "music"
    root.mkdir()
    original = root / "track.flac"
    original.write_bytes(b"audio")
    album_id, _ = _album_with_file(imported_conn, original)

    result = _worker(legacy_db, _WorkerConfig([str(root)]))._remove_native_repair_file(
        str(original), {}, reason="lossy_converter",
    )

    assert result["success"] is True
    assert not original.exists()
    operation = _operations(imported_conn)[0]
    assert operation["actor"] == "repair:lossy_converter"
    assert operation["entity_id"] == album_id


def test_a_file_that_is_already_gone_is_not_an_error(
        imported_conn, legacy_db, tmp_path):
    """Two jobs can flag the same file. The second one to run should record a
    no-op, not a failure that reads like the delete went wrong."""
    root = tmp_path / "music"
    root.mkdir()
    target = root / "ghost.flac"
    album_id, _ = _album_with_file(imported_conn, target)

    result = delete_files_journaled(
        legacy_db,
        targets=[str(target)],
        entity_type="albums",
        entity_id=album_id,
        actor="repair:corrupt_audio",
        config_manager=_Config([str(root)]),
    )

    assert result["deleted"] == []
    assert result["failed"][0]["error"] == "file_not_found"
