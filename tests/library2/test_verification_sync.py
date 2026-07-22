from __future__ import annotations

from core.library2.verification import mark_file_verification_status


def test_human_approve_updates_library_v2_file(imported_conn):
    row = imported_conn.execute(
        "SELECT id, path FROM lib2_track_files ORDER BY id LIMIT 1"
    ).fetchone()
    imported_conn.execute(
        "UPDATE lib2_track_files SET verification_status='unverified' WHERE id=?",
        (row["id"],),
    )

    updated = mark_file_verification_status(
        imported_conn, [row["path"]], "human_verified"
    )

    assert updated == 1
    assert imported_conn.execute(
        "SELECT verification_status FROM lib2_track_files WHERE id=?", (row["id"],)
    ).fetchone()["verification_status"] == "human_verified"


def test_human_approve_matches_resolved_mapped_path(imported_conn, monkeypatch):
    row = imported_conn.execute(
        "SELECT id, path FROM lib2_track_files ORDER BY id LIMIT 1"
    ).fetchone()
    monkeypatch.setattr(
        "core.library2.paths.resolve_lib2_path",
        lambda path, config_manager=None: (
            "/host/music/resolved.flac" if path == row["path"] else None
        ),
    )

    updated = mark_file_verification_status(
        imported_conn, ["/host/music/resolved.flac"], "human_verified"
    )

    assert updated == 1


def test_verification_sync_is_noop_without_library_v2_schema(tmp_path):
    import sqlite3

    conn = sqlite3.connect(tmp_path / "plain.sqlite")
    try:
        assert mark_file_verification_status(
            conn, ["/music/song.flac"], "human_verified"
        ) == 0
    finally:
        conn.close()
