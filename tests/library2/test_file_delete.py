"""ADR-05 physical-delete preview and root-safety contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.library2.file_delete import (
    FileDeleteError,
    delete_entity_files,
    preview_entity_files,
    reconcile_incomplete_deletes,
)


class _Config:
    def __init__(self, roots):
        self.roots = roots

    def get(self, key, default=None):
        assert key == "library.music_paths"
        return self.roots


def _set_track_path(conn, track_id: int, path: Path) -> int:
    conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (track_id,))
    cur = conn.execute(
        "INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
        (track_id, str(path)),
    )
    conn.commit()
    return cur.lastrowid


def test_preview_allows_only_files_inside_explicit_library_root(
        imported_conn, legacy_db, tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    inside = root / "inside.flac"
    inside.write_bytes(b"audio")
    outside = tmp_path / "outside.flac"
    outside.write_bytes(b"outside")
    tracks = imported_conn.execute(
        "SELECT id FROM lib2_tracks ORDER BY id LIMIT 2"
    ).fetchall()
    inside_id = _set_track_path(imported_conn, tracks[0][0], inside)
    outside_id = _set_track_path(imported_conn, tracks[1][0], outside)

    album_id = imported_conn.execute(
        "SELECT album_id FROM lib2_tracks WHERE id=?", (tracks[0][0],)
    ).fetchone()[0]
    # Put both rows in the same entity scope for this contract test.
    imported_conn.execute(
        "UPDATE lib2_tracks SET album_id=? WHERE id=?", (album_id, tracks[1][0])
    )
    imported_conn.commit()
    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album_id, config_manager=_Config([str(root)])
    )

    by_id = {item["file_ids"][0]: item for item in preview["files"]}
    assert by_id[inside_id]["deletable"] is True
    assert by_id[inside_id]["root"] == str(root.resolve())
    assert by_id[outside_id]["deletable"] is False
    assert by_id[outside_id]["reason"] == "outside_configured_library_roots"
    assert preview["deletable_count"] == 1
    assert preview["unsafe_count"] == 1


def test_preview_rejects_symlink_escape(imported_conn, legacy_db, tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    outside = tmp_path / "outside.flac"
    outside.write_bytes(b"outside")
    link = root / "escape.flac"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    track_id = imported_conn.execute("SELECT id FROM lib2_tracks LIMIT 1").fetchone()[0]
    file_id = _set_track_path(imported_conn, track_id, link)
    album_id = imported_conn.execute(
        "SELECT album_id FROM lib2_tracks WHERE id=?", (track_id,)
    ).fetchone()[0]

    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=album_id, config_manager=_Config([str(root)])
    )

    item = next(item for item in preview["files"] if file_id in item["file_ids"])
    assert item["deletable"] is False
    assert item["reason"] == "outside_configured_library_roots"


def test_preview_groups_duplicate_rows_for_one_physical_path(
        imported_conn, legacy_db, tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    path = root / "shared.flac"
    path.write_bytes(b"same")
    track = imported_conn.execute("SELECT id, album_id FROM lib2_tracks LIMIT 1").fetchone()
    first_id = _set_track_path(imported_conn, track["id"], path)
    second_id = imported_conn.execute(
        "INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
        (track["id"], str(path)),
    ).lastrowid
    imported_conn.commit()

    preview = preview_entity_files(
        legacy_db,
        entity="albums",
        entity_id=track["album_id"],
        config_manager=_Config([str(root)]),
    )

    item = next(item for item in preview["files"] if first_id in item["file_ids"])
    assert item["file_ids"] == [first_id, second_id]
    assert preview["file_count"] == 1


def test_preview_missing_entity_is_controlled(imported_conn, legacy_db):
    with pytest.raises(FileDeleteError) as exc:
        preview_entity_files(legacy_db, entity="albums", entity_id=999999)
    assert exc.value.status == 404


def test_execute_journals_before_unlink_and_preserves_entity(
        imported_conn, legacy_db, tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    path = root / "delete.flac"
    path.write_bytes(b"audio")
    track = imported_conn.execute("SELECT id, album_id FROM lib2_tracks LIMIT 1").fetchone()
    file_id = _set_track_path(imported_conn, track["id"], path)
    config = _Config([str(root)])
    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=track["album_id"], config_manager=config
    )
    observed = {}

    def _unlink(resolved):
        with legacy_db._get_connection() as check:
            observed["operation"] = check.execute(
                "SELECT status FROM lib2_file_delete_operations"
            ).fetchone()[0]
            observed["item"] = check.execute(
                "SELECT status FROM lib2_file_delete_items"
            ).fetchone()[0]
        Path(resolved).unlink()

    operation = delete_entity_files(
        legacy_db,
        entity="albums",
        entity_id=track["album_id"],
        preview_token=preview["preview_token"],
        config_manager=config,
        unlink=_unlink,
    )

    assert observed == {"operation": "executing", "item": "deleting"}
    assert operation["status"] == "completed"
    assert operation["items"][0]["status"] == "deleted"
    assert not path.exists()
    row = imported_conn.execute(
        "SELECT file_state FROM lib2_track_files WHERE id=?", (file_id,)
    ).fetchone()
    assert row["file_state"] == "deleted"
    assert imported_conn.execute(
        "SELECT 1 FROM lib2_albums WHERE id=?", (track["album_id"],)
    ).fetchone(), "physical delete must not remove the library entity"


def test_execute_rejects_stale_preview_without_journal(
        imported_conn, legacy_db, tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    path = root / "changed.flac"
    path.write_bytes(b"one")
    track = imported_conn.execute("SELECT id, album_id FROM lib2_tracks LIMIT 1").fetchone()
    _set_track_path(imported_conn, track["id"], path)
    config = _Config([str(root)])
    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=track["album_id"], config_manager=config
    )
    path.write_bytes(b"changed-size")

    with pytest.raises(FileDeleteError) as exc:
        delete_entity_files(
            legacy_db,
            entity="albums",
            entity_id=track["album_id"],
            preview_token=preview["preview_token"],
            config_manager=config,
        )

    assert exc.value.status == 409
    assert path.exists()
    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_file_delete_operations"
    ).fetchone()[0] == 0


def test_execute_deletes_duplicate_linked_path_once(
        imported_conn, legacy_db, tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    path = root / "shared.flac"
    path.write_bytes(b"same")
    track = imported_conn.execute("SELECT id, album_id FROM lib2_tracks LIMIT 1").fetchone()
    first_id = _set_track_path(imported_conn, track["id"], path)
    second_id = imported_conn.execute(
        "INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
        (track["id"], str(path)),
    ).lastrowid
    imported_conn.commit()
    config = _Config([str(root)])
    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=track["album_id"], config_manager=config
    )
    calls = []

    operation = delete_entity_files(
        legacy_db,
        entity="albums",
        entity_id=track["album_id"],
        preview_token=preview["preview_token"],
        config_manager=config,
        unlink=lambda resolved: (calls.append(resolved), Path(resolved).unlink())[1],
    )

    assert calls == [str(path)]
    assert operation["items"][0]["file_ids"] == [first_id, second_id]
    states = imported_conn.execute(
        "SELECT file_state FROM lib2_track_files WHERE id IN (?,?) ORDER BY id",
        (first_id, second_id),
    ).fetchall()
    assert [row["file_state"] for row in states] == ["deleted", "deleted"]


def test_reconcile_finishes_unlink_completed_before_process_crash(
        imported_conn, legacy_db, tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    path = root / "crash.flac"
    path.write_bytes(b"audio")
    track = imported_conn.execute("SELECT id, album_id FROM lib2_tracks LIMIT 1").fetchone()
    file_id = _set_track_path(imported_conn, track["id"], path)
    config = _Config([str(root)])
    preview = preview_entity_files(
        legacy_db, entity="albums", entity_id=track["album_id"], config_manager=config
    )

    def _crash_after_unlink(resolved):
        Path(resolved).unlink()
        raise KeyboardInterrupt("simulated process death")

    with pytest.raises(KeyboardInterrupt):
        delete_entity_files(
            legacy_db,
            entity="albums",
            entity_id=track["album_id"],
            preview_token=preview["preview_token"],
            config_manager=config,
            unlink=_crash_after_unlink,
        )

    assert imported_conn.execute(
        "SELECT status FROM lib2_file_delete_items"
    ).fetchone()[0] == "deleting"
    assert reconcile_incomplete_deletes(legacy_db) == 1
    assert imported_conn.execute(
        "SELECT file_state FROM lib2_track_files WHERE id=?", (file_id,)
    ).fetchone()[0] == "deleted"
    assert imported_conn.execute(
        "SELECT status FROM lib2_file_delete_operations"
    ).fetchone()[0] == "completed"
