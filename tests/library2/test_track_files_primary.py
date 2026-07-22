"""Multi-file primary model (audit P1-07 / ADR-03).

Exactly one file per track is primary; selection follows the documented
strategy (active > lossless > bit depth > sample rate > bitrate > NEWEST id)
and the invariant survives insert / move / delete / state changes.
"""

from __future__ import annotations

from core.library2.track_files import (
    backfill_primary_flags,
    primary_file_row,
    primary_file_rows,
    set_file_state,
    set_primary_file,
)


def _seed_track(conn, title="Song"):
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('A')")
    artist_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Album')",
        (artist_id,))
    album_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_tracks(album_id, title) VALUES(?,?)",
                (album_id, title))
    return cur.lastrowid


def _add_file(conn, track_id, path, fmt="mp3", bitrate=320, bit_depth=None,
              sample_rate=None):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO lib2_track_files(track_id, path, format, bitrate, "
        "bit_depth, sample_rate) VALUES(?,?,?,?,?,?)",
        (track_id, path, fmt, bitrate, bit_depth, sample_rate))
    return cur.lastrowid


def _is_primary(conn, file_id):
    return bool(conn.execute(
        "SELECT is_primary FROM lib2_track_files WHERE id=?", (file_id,)
    ).fetchone()[0])


def test_first_file_becomes_primary_and_keeps_it(imported_conn):
    conn = imported_conn
    track = _seed_track(conn)
    first = _add_file(conn, track, "/m/a.mp3", fmt="mp3")
    assert _is_primary(conn, first)
    # A later (even better) file does not silently steal the flag — upgrades
    # replace the file through the import pipeline, not by side effect.
    second = _add_file(conn, track, "/m/a.flac", fmt="flac", bit_depth=16)
    assert _is_primary(conn, first)
    assert not _is_primary(conn, second)


def test_backfill_elects_best_file_not_oldest(imported_conn):
    conn = imported_conn
    track = _seed_track(conn)
    old_mp3 = _add_file(conn, track, "/m/b.mp3", fmt="mp3", bitrate=192)
    new_flac = _add_file(conn, track, "/m/b.flac", fmt="flac", bit_depth=24,
                         sample_rate=96000)
    # Simulate pre-ADR-03 rows: no primary flags at all.
    conn.execute("UPDATE lib2_track_files SET is_primary=0 WHERE track_id=?",
                 (track,))
    backfill_primary_flags(conn.cursor())
    assert _is_primary(conn, new_flac)
    assert not _is_primary(conn, old_mp3)


def test_backfill_demotes_extra_primaries(imported_conn):
    conn = imported_conn
    track = _seed_track(conn)
    mp3 = _add_file(conn, track, "/m/c.mp3", fmt="mp3")
    flac = _add_file(conn, track, "/m/c.flac", fmt="flac", bit_depth=16)
    conn.execute("UPDATE lib2_track_files SET is_primary=1 WHERE track_id=?",
                 (track,))
    backfill_primary_flags(conn.cursor())
    primaries = [r[0] for r in conn.execute(
        "SELECT id FROM lib2_track_files WHERE track_id=? AND is_primary=1",
        (track,))]
    assert primaries == [flac]
    assert not _is_primary(conn, mp3)


def test_backfill_repairs_deleted_primary_over_active_file(imported_conn):
    conn = imported_conn
    track = _seed_track(conn)
    deleted = _add_file(conn, track, "/m/stale.flac", fmt="flac")
    active = _add_file(conn, track, "/m/fresh.mp3", fmt="mp3")
    conn.execute(
        "UPDATE lib2_track_files SET file_state='deleted', is_primary=1 WHERE id=?",
        (deleted,),
    )
    conn.execute("UPDATE lib2_track_files SET is_primary=0 WHERE id=?", (active,))

    backfill_primary_flags(conn.cursor())

    assert not _is_primary(conn, deleted)
    assert _is_primary(conn, active)


def test_deleting_primary_promotes_best_sibling(imported_conn):
    conn = imported_conn
    track = _seed_track(conn)
    first = _add_file(conn, track, "/m/d.mp3", fmt="mp3", bitrate=192)
    better = _add_file(conn, track, "/m/d.flac", fmt="flac", bit_depth=16)
    worse = _add_file(conn, track, "/m/d2.mp3", fmt="mp3", bitrate=128)
    conn.execute("DELETE FROM lib2_track_files WHERE id=?", (first,))
    assert _is_primary(conn, better)
    assert not _is_primary(conn, worse)


def test_move_between_tracks_reassigns_primaries(imported_conn):
    conn = imported_conn
    src = _seed_track(conn, "Src")
    dst = _seed_track(conn, "Dst")
    moved = _add_file(conn, src, "/m/e.mp3", fmt="mp3")
    stays = _add_file(conn, src, "/m/e2.mp3", fmt="mp3", bitrate=256)
    assert _is_primary(conn, moved)
    conn.execute("UPDATE lib2_track_files SET track_id=? WHERE id=?",
                 (dst, moved))
    # Moved file is the target's only file → primary there; the source
    # elects its best remaining file.
    assert _is_primary(conn, moved)
    assert _is_primary(conn, stays)


def test_move_onto_track_with_primary_does_not_steal(imported_conn):
    conn = imported_conn
    src = _seed_track(conn, "Src")
    dst = _seed_track(conn, "Dst")
    incoming = _add_file(conn, src, "/m/f.flac", fmt="flac", bit_depth=24)
    existing = _add_file(conn, dst, "/m/f2.mp3", fmt="mp3")
    conn.execute("UPDATE lib2_track_files SET track_id=? WHERE id=?",
                 (dst, incoming))
    assert _is_primary(conn, existing)
    assert not _is_primary(conn, incoming)


def test_primary_leaving_active_hands_flag_to_active_sibling(imported_conn):
    conn = imported_conn
    track = _seed_track(conn)
    flac = _add_file(conn, track, "/m/g.flac", fmt="flac", bit_depth=16)
    mp3 = _add_file(conn, track, "/m/g.mp3", fmt="mp3")
    assert _is_primary(conn, flac)
    assert set_file_state(conn, flac, "missing_confirmed")
    assert _is_primary(conn, mp3)
    assert not _is_primary(conn, flac)
    state = conn.execute(
        "SELECT file_state FROM lib2_track_files WHERE id=?", (flac,)
    ).fetchone()[0]
    assert state == "missing_confirmed"


def test_deleted_file_is_not_primary_and_new_import_replaces_its_flag(imported_conn):
    conn = imported_conn
    track = _seed_track(conn)
    deleted = _add_file(conn, track, "/m/deleted.flac", fmt="flac")

    assert set_file_state(conn, deleted, "deleted")
    assert primary_file_row(conn, track) is None

    replacement = _add_file(conn, track, "/m/replacement.flac", fmt="flac")
    assert _is_primary(conn, replacement)
    assert not _is_primary(conn, deleted)
    assert primary_file_row(conn, track)["id"] == replacement


def test_set_file_state_rejects_unknown_state(imported_conn):
    conn = imported_conn
    track = _seed_track(conn)
    f = _add_file(conn, track, "/m/h.mp3")
    assert not set_file_state(conn, f, "vanished")


def test_explicit_set_primary_overrides_strategy(imported_conn):
    conn = imported_conn
    track = _seed_track(conn)
    flac = _add_file(conn, track, "/m/i.flac", fmt="flac", bit_depth=16)
    mp3 = _add_file(conn, track, "/m/i.mp3", fmt="mp3")
    assert set_primary_file(conn, track, mp3)
    assert _is_primary(conn, mp3)
    assert not _is_primary(conn, flac)


def test_set_primary_rejects_foreign_file(imported_conn):
    conn = imported_conn
    track_a = _seed_track(conn, "A")
    track_b = _seed_track(conn, "B")
    file_b = _add_file(conn, track_b, "/m/j.mp3")
    assert not set_primary_file(conn, track_a, file_b)
    assert _is_primary(conn, file_b)


def test_primary_file_row_returns_primary_not_oldest(imported_conn):
    conn = imported_conn
    track = _seed_track(conn)
    _add_file(conn, track, "/m/k.mp3", fmt="mp3")
    flac = _add_file(conn, track, "/m/k.flac", fmt="flac", bit_depth=16)
    assert set_primary_file(conn, track, flac)
    row = primary_file_row(conn, track)
    assert row is not None and row["id"] == flac


def test_primary_file_rows_preserves_primary_strategy_for_batch(imported_conn):
    first_track = _seed_track(imported_conn, "First")
    second_track = _seed_track(imported_conn, "Second")
    first_primary = _add_file(imported_conn, first_track, "/m/first.mp3")
    _add_file(imported_conn, first_track, "/m/first.flac", fmt="flac")
    _add_file(imported_conn, second_track, "/m/second.mp3")
    second_primary = _add_file(
        imported_conn,
        second_track,
        "/m/second.flac",
        fmt="flac",
        bit_depth=24,
    )
    assert set_primary_file(imported_conn, second_track, second_primary)

    rows = primary_file_rows(imported_conn, [first_track, second_track])

    assert rows[first_track]["id"] == first_primary
    assert rows[second_track]["id"] == second_primary


def test_importer_backfill_marks_legacy_single_files(imported_conn):
    """Files seeded by the legacy importer end up primary (one file each)."""
    conn = imported_conn
    rows = conn.execute(
        "SELECT track_id, COUNT(*) AS n, SUM(is_primary) AS p "
        "FROM lib2_track_files WHERE track_id IS NOT NULL GROUP BY track_id"
    ).fetchall()
    assert rows, "importer should have seeded files"
    for r in rows:
        assert r["p"] == 1
