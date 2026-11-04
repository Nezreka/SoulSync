"""A file that is already gone must not veto deleting the ones that are there.

Reported, second half: "Permanent deletion is blocked for 1 unsafe or
unresolved file … und kann immer noch nicht permanently delete files". The
album in question has 13 file rows; twelve are on disk, one points at a file
that no longer exists (``file_state='missing_confirmed'``). ``unsafe_count``
counted that row, and ``delete_entity_files`` refuses outright while
``unsafe_count`` is non-zero — so one already-deleted file made the other
twelve undeletable, permanently.

"Unsafe" is meant to mean: this path exists and lies OUTSIDE your library, so
deleting it could destroy something that is not yours to delete. A file that is
simply gone is not that — there is nothing to unlink, only a row to retire.

The distinction that still matters is dd28-19: a path can also look absent
because the storage is unreachable (unmounted NAS, broken path mapping). That
one keeps blocking, because "delete" would then retire rows for files that are
alive and well on a disk we cannot see.
"""

from __future__ import annotations

import pytest

from core.library2.file_delete import (
    FileDeleteError,
    delete_entity_files,
    preview_entity_files,
)


class _Config:
    def __init__(self, *, music_paths=None, transfer=None):
        self.values = {
            "library.music_paths": music_paths if music_paths is not None else [],
            "soulseek.transfer_path": transfer or "",
        }

    def get(self, key, default=None):
        return self.values.get(key, default)


def _two_files(conn, present, absent):
    """One track with a live file, one with a row whose file is gone."""
    rows = conn.execute("SELECT id, album_id FROM lib2_tracks ORDER BY id LIMIT 2").fetchall()
    album_id = int(rows[0][1])
    conn.execute("UPDATE lib2_tracks SET album_id=? WHERE id=?", (album_id, rows[1][0]))
    conn.execute("DELETE FROM lib2_track_files")
    live = conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, is_primary) VALUES(?,?,1)",
        (rows[0][0], str(present)),
    ).lastrowid
    ghost = conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, is_primary, file_state) "
        "VALUES(?,?,1,'missing_confirmed')",
        (rows[1][0], str(absent)),
    ).lastrowid
    conn.commit()
    return album_id, int(live), int(ghost)


@pytest.fixture()
def album(imported_conn, tmp_path):
    transfer = tmp_path / "Transfer"
    present = transfer / "Artist" / "Album" / "01 - Here.flac"
    present.parent.mkdir(parents=True)
    present.write_bytes(b"audio")
    absent = transfer / "Artist" / "Album" / "02 - Gone.flac"
    album_id, live, ghost = _two_files(imported_conn, present, absent)
    return {
        "id": album_id, "live_id": live, "ghost_id": ghost,
        "present": present, "absent": absent,
        "config": _Config(transfer=str(transfer)),
    }


def test_a_file_that_is_gone_is_not_counted_as_unsafe(legacy_db, album):
    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album["id"],
        config_manager=album["config"],
    )

    assert preview["unsafe_count"] == 0, preview["files"]
    assert preview["missing_count"] == 1
    assert preview["deletable_count"] == 1
    gone = [f for f in preview["files"] if f["reason"] == "already_gone"]
    assert len(gone) == 1


def test_the_delete_goes_ahead_and_retires_the_gone_row(legacy_db, imported_conn, album):
    """The reported failure, end to end: twelve files held hostage by one."""
    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album["id"],
        config_manager=album["config"],
    )

    operation = delete_entity_files(
        legacy_db, entity="albums", entity_id=album["id"],
        preview_token=preview["preview_token"], config_manager=album["config"],
    )

    assert not album["present"].exists(), "the file that WAS there must be deleted"
    assert operation["status"] == "completed"
    states = dict(imported_conn.execute(
        "SELECT id, file_state FROM lib2_track_files WHERE id IN (?,?)",
        (album["live_id"], album["ghost_id"]),
    ).fetchall())
    assert states[album["live_id"]] == "deleted"
    assert states[album["ghost_id"]] == "deleted", "the row of a gone file is retired too"


def test_the_gone_file_is_still_on_the_record(legacy_db, imported_conn, album):
    """It is part of what the user asked to remove, so the History has to show
    it — otherwise the album's timeline claims one file left when two rows did."""
    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album["id"],
        config_manager=album["config"],
    )
    operation = delete_entity_files(
        legacy_db, entity="albums", entity_id=album["id"],
        preview_token=preview["preview_token"], config_manager=album["config"],
    )

    statuses = sorted(item["status"] for item in operation["items"])
    assert statuses == ["deleted", "missing"]


def test_unreachable_storage_still_blocks(legacy_db, imported_conn, tmp_path):
    """dd28-19 stands. The parent folder does not exist at all here, which is
    what an unmounted share looks like — the files may be perfectly alive."""
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    gone_mount = tmp_path / "nas" / "Artist" / "Album" / "01 - Song.flac"
    rows = imported_conn.execute("SELECT id, album_id FROM lib2_tracks ORDER BY id LIMIT 1").fetchone()
    imported_conn.execute("DELETE FROM lib2_track_files")
    imported_conn.execute(
        "INSERT INTO lib2_track_files(track_id, path, is_primary) VALUES(?,?,1)",
        (rows[0], str(gone_mount)),
    )
    imported_conn.commit()
    config = _Config(transfer=str(transfer))

    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=int(rows[1]), config_manager=config,
    )

    assert preview["unsafe_count"] == 1
    assert preview["missing_count"] == 0
    with pytest.raises(FileDeleteError):
        delete_entity_files(
            legacy_db, entity="albums", entity_id=int(rows[1]),
            preview_token=preview["preview_token"], config_manager=config,
        )
