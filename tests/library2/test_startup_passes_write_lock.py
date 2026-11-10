"""Every-start convergence passes must not take the write lock to change nothing.

review 5 R5-X2-1 / R5-G-4: python's sqlite3 begins a transaction before any
UPDATE/DELETE and SQLite takes the writer lock at statement start, so a
set-based no-op pass scanned its whole table with every other writer locked
out (2-3 s for the file roles, about 1 s each for the prunes, every start).
"""

import sqlite3

from core.library2.schema import ensure_library_v2_schema


def _db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn, run_backfills=False)
    conn.commit()
    conn.execute("PRAGMA journal_mode=WAL")
    ar = conn.execute("INSERT INTO lib2_artists(name, name_key) VALUES('a','a')").lastrowid
    al = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title, origin) "
                      "VALUES(?, 'b', 'library')", (ar,)).lastrowid
    for n in range(50):
        t = conn.execute("INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?, ?, ?)",
                         (al, f"t{n}", n + 1)).lastrowid
        conn.execute("INSERT INTO lib2_track_files(track_id, path, is_primary) VALUES(?, ?, 1)",
                     (t, f"/m/{n}.flac"))
    conn.commit()
    return path, conn


def _other_writer_locked_out(path):
    other = sqlite3.connect(path, timeout=0)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.rollback()
        return False
    except sqlite3.OperationalError as exc:
        return "locked" in str(exc)
    finally:
        other.close()


def test_a_no_op_file_role_pass_leaves_the_write_lock_free(tmp_path):
    from core.library2.track_files import backfill_file_roles
    path, conn = _db(tmp_path)
    assert backfill_file_roles(conn.cursor()) == 0
    assert not _other_writer_locked_out(path), "a no-op every-start pass holds the write lock"


def test_the_file_role_pass_still_classifies_companions(tmp_path):
    from core.library2.track_files import backfill_file_roles
    _path, conn = _db(tmp_path)
    track = conn.execute("SELECT track_id, id FROM lib2_track_files LIMIT 1").fetchone()
    # keep the master primary when the companion lands
    conn.execute("UPDATE lib2_track_files SET primary_manual=1 WHERE id=?", (track[1],))
    child = conn.execute("INSERT INTO lib2_track_files(track_id, path, source, is_primary) "
                         "VALUES(?, '/m/c.mp3', 'companion', 0)", (track[0],)).lastrowid
    conn.commit()
    assert backfill_file_roles(conn.cursor()) == 2
    row = conn.execute("SELECT file_role, derived_from_file_id FROM lib2_track_files WHERE id=?",
                       (child,)).fetchone()
    assert (row[0], row[1]) == ("derivative", track[1])


def test_the_file_role_reads_use_their_partial_indexes(tmp_path):
    from core.library2 import track_files
    _path, conn = _db(tmp_path)
    seen = []
    conn.set_trace_callback(seen.append)
    track_files.backfill_file_roles(conn.cursor())
    conn.set_trace_callback(None)
    reads = [s for s in seen if s.lstrip().upper().startswith("SELECT")]
    assert len(reads) == 2, reads
    for sql in reads:
        plan = " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql))
        # a walk of a partial index is a walk of just the rows to change
        assert ("idx_lib2_track_files_companion_role" in plan
                or "idx_lib2_track_files_derivative_unlinked" in plan), plan


def test_a_no_op_wanted_prune_leaves_the_write_lock_free(tmp_path):
    from core.library2.wanted import ensure_wanted_projection, recompute_wanted
    path, conn = _db(tmp_path)
    recompute_wanted(conn.cursor())
    conn.commit()
    assert ensure_wanted_projection(conn.cursor()) is None  # the prune branch
    assert not _other_writer_locked_out(path), "a no-op every-start pass holds the write lock"


def test_the_wanted_prune_does_not_lock_writers_out_mid_scan(tmp_path):
    from core.library2.wanted import (PROJECTION_VERSION, ensure_wanted_projection,
                                      ensure_wanted_schema)
    path = str(tmp_path / "w.db")
    a = sqlite3.connect(path)
    a.execute("PRAGMA journal_mode=WAL")
    a.execute("CREATE TABLE lib2_tracks(id INTEGER PRIMARY KEY)")
    ensure_wanted_schema(a.cursor())
    a.executemany("INSERT INTO lib2_tracks(id) VALUES(?)", [(i,) for i in range(1, 20001)])
    a.executemany(
        "INSERT INTO lib2_wanted_tracks(profile_id, track_id, wanted, reason, projection_version) "
        "VALUES(1,?,1,'t',?)", [(i, PROJECTION_VERSION) for i in range(1, 20001)])
    a.commit()
    b = sqlite3.connect(path, timeout=0, isolation_level=None)
    blocked = []
    current = []
    a.set_trace_callback(current.append)

    def probe():
        # another writer arriving while the prune's orphan scan runs
        if not blocked and current and "NOT IN" in current[-1]:
            try:
                b.execute("BEGIN IMMEDIATE")
                b.execute("ROLLBACK")
                blocked.append(False)
            except sqlite3.OperationalError as exc:
                blocked.append("locked" in str(exc))
        return 0

    a.set_progress_handler(probe, 2000)
    assert ensure_wanted_projection(a.cursor()) is None
    a.set_progress_handler(None, 0)
    a.set_trace_callback(None)
    assert blocked == [False], f"writer blocked during the prune scan: {blocked}"


def test_the_wanted_prune_still_drops_rows_of_deleted_tracks(tmp_path):
    from core.library2.wanted import ensure_wanted_projection, recompute_wanted
    _path, conn = _db(tmp_path)
    recompute_wanted(conn.cursor())
    conn.commit()
    gone = conn.execute("SELECT MAX(id) FROM lib2_tracks").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (gone,))
    conn.execute("DELETE FROM lib2_tracks WHERE id=?", (gone,))
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM lib2_wanted_tracks WHERE track_id=?",
                          (gone,)).fetchone()[0]
    ensure_wanted_projection(conn.cursor())
    assert conn.execute("SELECT COUNT(*) FROM lib2_wanted_tracks WHERE track_id=?",
                        (gone,)).fetchone()[0] == 0
    assert before == 1
    assert conn.execute("SELECT COUNT(*) FROM lib2_wanted_tracks").fetchone()[0] > 0


def test_a_no_op_edition_prune_leaves_the_write_lock_free(tmp_path):
    from core.library2.editions import backfill_editions, prune_orphaned_edition_rows
    path, conn = _db(tmp_path)
    backfill_editions(conn.cursor(), connection=conn)
    conn.commit()
    assert prune_orphaned_edition_rows(conn.cursor()) == 0
    assert not _other_writer_locked_out(path), "a no-op every-start pass holds the write lock"


def test_the_edition_prune_still_drops_shadow_rows_of_deleted_tracks(tmp_path):
    from core.library2.editions import backfill_editions, prune_orphaned_edition_rows
    _path, conn = _db(tmp_path)
    backfill_editions(conn.cursor(), connection=conn)
    conn.commit()
    gone = conn.execute("SELECT MAX(id) FROM lib2_tracks").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("UPDATE lib2_release_tracks SET track_id=? WHERE track_id=?", (999999, gone))
    conn.commit()
    # the release track and the recording only it referenced
    assert prune_orphaned_edition_rows(conn.cursor()) >= 2
    assert conn.execute("SELECT COUNT(*) FROM lib2_release_tracks WHERE track_id=999999"
                        ).fetchone()[0] == 0
