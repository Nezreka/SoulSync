"""SeadogsBooty's artist inflation: bytes-text credits and collaboration ghosts.

- an MP4 freeform tag read as bytes became "b'Above & Beyond'", which the
  credit splitter cut into artists called "b'Above" and "Beyond'"
- "Nelly & Florida Georgia Line" picked up Nelly's iTunes id and a fetch hung
  Nelly's 22 albums on it: 0 files, unwatched, and the local split never took
  it because it owned albums
"""

from contextlib import closing

from database.music_database import MusicDatabase
from tests import lib2_seed


def test_freeform_bytes_tags_read_as_text():
    from mutagen.mp4 import MP4FreeForm

    from core.library.file_tags import _stringify

    assert _stringify([MP4FreeForm(b"Above & Beyond")]) == "Above & Beyond"
    assert _stringify(b"Above & Beyond") == "Above & Beyond"


def test_a_bytes_text_credit_is_the_name_inside_it():
    from core.library2.importer import (
        _credit_names_for_import, split_artist_credits, unwrap_bytes_repr,
    )

    assert unwrap_bytes_repr("b'Above & Beyond'") == "Above & Beyond"
    assert unwrap_bytes_repr('b"Above & Beyond"') == "Above & Beyond"
    assert unwrap_bytes_repr("bonobo") == "bonobo"
    assert all(not name.startswith("b'") and not name.endswith("'")
               for name in split_artist_credits("b'Above & Beyond'"))
    # the known band stays whole once the bytes text is unwrapped
    known = {"above & beyond"}
    assert _credit_names_for_import("b'Above & Beyond'", lambda n: n.lower() in known) == [
        "Above & Beyond"]


def _library(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = db._get_connection()
    lib2_seed.track(conn, "Nelly", "Country Grammar", "Ride Wit Me")
    lib2_seed.track(conn, "Florida Georgia Line", "Here's to the Good Times", "Cruise")
    ghost_albums = [
        lib2_seed.album(conn, "Nelly & Florida Georgia Line", f"Nelly album {n}",
                        origin="discography", monitored=0)
        for n in range(3)]
    for album_id in ghost_albums:
        # what a discography fetch writes for an unwatched artist
        conn.execute("INSERT INTO lib2_tracks(album_id, title, monitored) VALUES(?, 'Song', 0)",
                     (album_id,))
    conn.execute("UPDATE lib2_artists SET external_ids='{\"legacy_unknown\":\"351794\"}'"
                 " WHERE name='Nelly & Florida Georgia Line'")
    conn.commit()
    return db, conn, ghost_albums


def _artists(conn):
    return {r[0] for r in conn.execute("SELECT name FROM lib2_artists")}


def test_a_wrong_id_collaboration_ghost_and_its_catalogue_are_cleared(tmp_path):
    from core.library2.native_enrich import split_combined_credits_locally

    _db, conn, ghost_albums = _library(tmp_path)
    with closing(conn):
        stats = split_combined_credits_locally(conn)

        assert stats["collab_ghosts_cleared"] == 1
        assert "Nelly & Florida Georgia Line" not in _artists(conn)
        assert conn.execute(f"SELECT COUNT(*) FROM lib2_albums WHERE id IN ({','.join('?' * 3)})",
                            ghost_albums).fetchone()[0] == 0
        # the real artists and their own files are untouched
        assert {"Nelly", "Florida Georgia Line"} <= _artists(conn)
        assert conn.execute("SELECT COUNT(*) FROM lib2_track_files").fetchone()[0] == 2


def test_a_catalogue_someone_touched_keeps_the_collaboration(tmp_path):
    from core.library2.native_enrich import split_combined_credits_locally

    _db, conn, ghost_albums = _library(tmp_path)
    with closing(conn):
        conn.execute("UPDATE lib2_albums SET monitored=1 WHERE id=?", (ghost_albums[1],))
        conn.commit()
        stats = split_combined_credits_locally(conn)

        assert stats["collab_ghosts_cleared"] == 0
        assert conn.execute("SELECT COUNT(*) FROM lib2_albums WHERE primary_artist_id=("
                            "SELECT id FROM lib2_artists WHERE name='Nelly & Florida Georgia Line')"
                            ).fetchone()[0] == 3


def test_a_collaboration_whose_parts_are_not_in_the_library_stays(tmp_path):
    from core.library2.native_enrich import split_combined_credits_locally

    _db, conn, _albums = _library(tmp_path)
    with closing(conn):
        conn.execute("DELETE FROM lib2_track_files WHERE path LIKE '%Florida%'")
        conn.commit()
        assert split_combined_credits_locally(conn)["collab_ghosts_cleared"] == 0
        assert "Nelly & Florida Georgia Line" in _artists(conn)


def test_a_hand_edited_album_keeps_the_collaboration(tmp_path):
    """third #1062 review P2B-9: album edits are stored as 'release_group';
    the clear looked them up as 'album' and deleted a hand-edited album"""
    from core.library2.metadata_overrides import ensure_metadata_overrides_schema
    from core.library2.native_enrich import split_combined_credits_locally

    _db, conn, ghost_albums = _library(tmp_path)
    with closing(conn):
        ensure_metadata_overrides_schema(conn.cursor())
        conn.execute("INSERT INTO lib2_metadata_overrides(entity_type, entity_id, field_name, value_json) "
                     "VALUES('release_group', ?, 'title', '\"Nelly album (mine)\"')", (ghost_albums[0],))
        conn.commit()
        assert split_combined_credits_locally(conn)["collab_ghosts_cleared"] == 0
        assert conn.execute("SELECT COUNT(*) FROM lib2_albums WHERE id=?",
                            (ghost_albums[0],)).fetchone()[0] == 1
