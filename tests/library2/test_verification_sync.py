from __future__ import annotations

from contextlib import closing

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

    with closing(sqlite3.connect(tmp_path / "plain.sqlite")) as conn:
        assert mark_file_verification_status(
            conn, ["/music/song.flac"], "human_verified"
        ) == 0


def test_the_mapped_path_fallback_only_resolves_rows_with_the_files_name(monkeypatch):
    """Approve on a mapped-path setup ran the resolver (a stat and a suffix
    walk) on every file row: 300k network stats per click. it narrows to rows
    ending in the file's name first, and still finds the mapped row."""
    import sqlite3
    from core.library2 import paths, verification

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE lib2_track_files (id INTEGER PRIMARY KEY, path TEXT, "
                 "verification_status TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO lib2_track_files (path) VALUES ('/data/media/Artist/Album/01 - Song.flac')")
    conn.executemany("INSERT INTO lib2_track_files (path) VALUES (?)",
                     [(f"/data/media/Other/{i}.flac",) for i in range(200)])
    asked = []

    def resolve(raw, config_manager=None):
        asked.append(raw)
        return raw.replace("/data/media", "/music")
    monkeypatch.setattr(paths, "resolve_lib2_path", resolve)
    updated = verification.mark_file_verification_status(
        conn, ["/music/Artist/Album/01 - Song.flac"], "verified")
    assert updated == 1
    assert asked == ["/data/media/Artist/Album/01 - Song.flac"]
