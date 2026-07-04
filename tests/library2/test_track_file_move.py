"""Single↔album move: re-home a file link between two track rows."""

from __future__ import annotations

import pytest

from core.library2.track_file_move import MoveError, move_track_file


class _NoWishlistDB:
    """mirror_tracks_wishlist needs db.add/remove_from_wishlist; for these
    sqlite-only tests a stub that reports nothing-removed is enough."""

    def remove_from_wishlist(self, *_a, **_k):
        return False

    def add_to_wishlist(self, *_a, **_k):
        return False


def _seed_pair(conn, *, single_has_file: bool = True, album_has_file: bool = False):
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('Drake')")
    artist_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
                "VALUES(?, 'One Dance', 'single')", (artist_id,))
    single_album = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title, album_type) "
                "VALUES(?, 'Views', 'album')", (artist_id,))
    full_album = cur.lastrowid
    for aid in (single_album, full_album):
        cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                    (aid, artist_id))
    cur.execute("INSERT INTO lib2_tracks(album_id, title, monitored) "
                "VALUES(?, 'One Dance', 1)", (single_album,))
    single_track = cur.lastrowid
    cur.execute("INSERT INTO lib2_tracks(album_id, title, track_number, monitored, "
                "canonical_track_id) VALUES(?, 'One Dance', 4, 1, NULL)", (full_album,))
    album_track = cur.lastrowid
    if single_has_file:
        cur.execute("INSERT INTO lib2_track_files(track_id, path, format) "
                    "VALUES(?, '/m/move-single.flac', 'flac')", (single_track,))
    if album_has_file:
        cur.execute("INSERT INTO lib2_track_files(track_id, path, format) "
                    "VALUES(?, '/m/move-album.flac', 'flac')", (album_track,))
    conn.commit()
    return single_track, album_track


def test_move_rehomes_file_and_unmonitors_source(imported_conn):
    conn = imported_conn
    single, album = _seed_pair(conn)
    result = move_track_file(_NoWishlistDB(), conn, single, album)
    assert result["to_track_id"] == album
    assert result["source_unmonitored"] is True

    owner = conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE path='/m/move-single.flac'"
    ).fetchone()["track_id"]
    assert owner == album
    assert conn.execute(
        "SELECT monitored FROM lib2_tracks WHERE id=?", (single,)
    ).fetchone()["monitored"] == 0
    # File did NOT get duplicated: exactly one row for this path.
    assert conn.execute(
        "SELECT COUNT(*) c FROM lib2_track_files WHERE path='/m/move-single.flac'"
    ).fetchone()["c"] == 1


def test_move_rejects_when_target_has_file(imported_conn):
    conn = imported_conn
    single, album = _seed_pair(conn, album_has_file=True)
    with pytest.raises(MoveError) as exc:
        move_track_file(_NoWishlistDB(), conn, single, album)
    assert exc.value.status == 409


def test_move_rejects_fileless_source(imported_conn):
    conn = imported_conn
    single, album = _seed_pair(conn, single_has_file=False)
    with pytest.raises(MoveError) as exc:
        move_track_file(_NoWishlistDB(), conn, single, album)
    assert exc.value.status == 409


def test_move_rejects_self_and_unknown(imported_conn):
    conn = imported_conn
    single, album = _seed_pair(conn)
    with pytest.raises(MoveError):
        move_track_file(_NoWishlistDB(), conn, single, single)
    with pytest.raises(MoveError) as exc:
        move_track_file(_NoWishlistDB(), conn, single, 999999)
    assert exc.value.status == 404
