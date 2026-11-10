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


def _windows_files(monkeypatch, stored, others, resolves_to=None):
    """the module as it runs on Windows (os.path is ntpath there), over a
    stored path plus ``others`` decoys; returns (conn, resolver calls)"""
    import ntpath
    import sqlite3
    import types

    from core.library2 import paths, verification

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE lib2_track_files (id INTEGER PRIMARY KEY, path TEXT, "
                 "verification_status TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO lib2_track_files (path) VALUES (?)", (stored,))
    conn.executemany("INSERT INTO lib2_track_files (path) VALUES (?)", [(p,) for p in others])
    asked = []
    monkeypatch.setattr(paths, "resolve_lib2_path",
                        lambda raw, config_manager=None: asked.append(raw) or (
                            resolves_to if raw == stored else None))
    monkeypatch.setattr(verification, "os", types.SimpleNamespace(path=ntpath))
    return conn, asked


def test_an_exact_stored_windows_path_takes_the_index_lookup(monkeypatch):
    """review 5 R5-G-3: normcase lowercases on Windows, so `path IN` never
    matched a stored H:\\Music\\... path and the resolver walked every row"""
    stored = "H:\\Music\\Émilie Simon\\Végétal\\01 - Été.flac"
    conn, asked = _windows_files(
        monkeypatch, stored, [f"H:\\Music\\Other\\{i}.flac" for i in range(300)])

    assert mark_file_verification_status(conn, [stored], "human_verified") == 1
    assert asked == [], f"resolver ran {len(asked)} times for an exact stored path"
    assert conn.execute("SELECT verification_status FROM lib2_track_files WHERE path=?",
                        (stored,)).fetchone()[0] == "human_verified"


def test_a_non_ascii_name_narrows_the_mapped_path_fallback(monkeypatch):
    """review 5 R5-G-3: the LIKE narrowing used the lowercased name, and LIKE
    folds ASCII only, so "été.flac" missed "Été.flac" and every row was walked"""
    stored = "\\\\server\\share\\Émilie Simon\\Végétal\\01 - Été.flac"
    on_disk = "H:\\Music\\Émilie Simon\\Végétal\\01 - Été.flac"
    conn, asked = _windows_files(
        monkeypatch, stored, [f"\\\\server\\share\\Other\\{i}.flac" for i in range(300)],
        resolves_to=on_disk)

    assert mark_file_verification_status(conn, [on_disk], "human_verified") == 1
    assert asked == [stored], f"resolver ran {len(asked)} times"
