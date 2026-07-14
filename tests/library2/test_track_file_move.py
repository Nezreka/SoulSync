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
    from core.library2.monitor_rules import PROVENANCE_LEGACY, record_rule
    from core.library2.wanted import recompute_wanted
    for track_id in (single_track, album_track):
        record_rule(conn, "track", track_id, True, PROVENANCE_LEGACY)
    recompute_wanted(conn, track_ids=[single_track, album_track])
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
    projected = conn.execute(
        "SELECT wanted, reason FROM lib2_wanted_tracks WHERE track_id=?", (single,)
    ).fetchone()
    assert dict(projected) == {"wanted": 0, "reason": "track_explicit"}
    # File did NOT get duplicated: exactly one row for this path.
    assert conn.execute(
        "SELECT COUNT(*) c FROM lib2_track_files WHERE path='/m/move-single.flac'"
    ).fetchone()["c"] == 1


def test_move_rehomes_all_source_files_before_unmonitoring(imported_conn):
    conn = imported_conn
    single, album = _seed_pair(conn)
    conn.execute(
        """INSERT INTO lib2_track_files(track_id, path, format)
           VALUES(?, '/m/move-single.mp3', 'mp3')""",
        (single,),
    )
    conn.commit()

    result = move_track_file(_NoWishlistDB(), conn, single, album)

    owners = conn.execute(
        """SELECT path, track_id FROM lib2_track_files
            WHERE path LIKE '/m/move-single.%' ORDER BY path"""
    ).fetchall()
    assert [(row["path"], row["track_id"]) for row in owners] == [
        ("/m/move-single.flac", album),
        ("/m/move-single.mp3", album),
    ]
    assert result["moved_file_count"] == 2
    assert set(result["moved_file_ids"]) == {
        row["id"] for row in conn.execute(
            "SELECT id FROM lib2_track_files WHERE track_id=?", (album,)
        )
    }
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_track_files WHERE track_id=?", (single,)
    ).fetchone()[0] == 0


def test_move_from_canonical_side_reverses_direct_link(imported_conn):
    conn = imported_conn
    single, album = _seed_pair(
        conn, single_has_file=False, album_has_file=True
    )
    conn.execute(
        "UPDATE lib2_tracks SET canonical_track_id=? WHERE id=?",
        (album, single),
    )
    conn.commit()

    result = move_track_file(_NoWishlistDB(), conn, album, single)

    links = {
        row["id"]: row["canonical_track_id"]
        for row in conn.execute(
            "SELECT id, canonical_track_id FROM lib2_tracks WHERE id IN (?, ?)",
            (single, album),
        )
    }
    assert result["canonical_reversed"] is True
    assert links == {single: None, album: single}
    assert conn.execute(
        "SELECT track_id FROM lib2_track_files WHERE path='/m/move-album.flac'"
    ).fetchone()[0] == single


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("UPDATE lib2_tracks SET title='Different' WHERE id={album}", "titles"),
        ("duration", "durations"),
        ("UPDATE lib2_tracks SET isrc='AAA' WHERE id={single};", ""),
    ],
)
def test_move_rejects_incompatible_recordings(imported_conn, mutation, error):
    conn = imported_conn
    single, album = _seed_pair(conn)
    if mutation == "duration":
        conn.execute("UPDATE lib2_tracks SET duration=100000 WHERE id=?", (single,))
        conn.execute("UPDATE lib2_tracks SET duration=200000 WHERE id=?", (album,))
    elif "isrc" in mutation:
        conn.execute("UPDATE lib2_tracks SET isrc='AAA' WHERE id=?", (single,))
        conn.execute("UPDATE lib2_tracks SET isrc='BBB' WHERE id=?", (album,))
        error = "ISRC"
    else:
        conn.execute(mutation.format(single=single, album=album))
    conn.commit()
    with pytest.raises(MoveError, match=error):
        move_track_file(_NoWishlistDB(), conn, single, album)


def test_move_rejects_different_artist_and_canonical_chain(imported_conn):
    conn = imported_conn
    single, album = _seed_pair(conn)
    other_artist = conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('Other')"
    ).lastrowid
    other_album = conn.execute(
        """INSERT INTO lib2_albums(primary_artist_id, title, album_type)
           VALUES(?, 'Other release', 'album')""",
        (other_artist,),
    ).lastrowid
    conn.execute("UPDATE lib2_tracks SET album_id=? WHERE id=?", (other_album, album))
    conn.commit()
    with pytest.raises(MoveError, match="share an artist"):
        move_track_file(_NoWishlistDB(), conn, single, album)

    conn.execute("UPDATE lib2_tracks SET album_id=? WHERE id=?", (
        conn.execute("SELECT album_id FROM lib2_tracks WHERE id=?", (single,)).fetchone()[0],
        album,
    ))
    dependent = conn.execute(
        """INSERT INTO lib2_tracks(album_id, title, canonical_track_id)
           SELECT album_id, title, ? FROM lib2_tracks WHERE id=?""",
        (single, single),
    ).lastrowid
    conn.commit()
    assert dependent
    with pytest.raises(MoveError, match="canonical target"):
        move_track_file(_NoWishlistDB(), conn, single, album)


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
