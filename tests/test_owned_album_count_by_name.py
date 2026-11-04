"""the discover hero's ownership meter: albums of an artist that have a file,
matched on the name without case. it rides the lower(name) index now; the
plain NOCASE compare walked every library album (a second per artist)."""

from database.music_database import MusicDatabase
from tests import lib2_seed


def test_counts_owned_albums_whatever_the_case(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        lib2_seed.track(conn, "Radiohead", "OK Computer", "Airbag", path="/m/a.flac")
        lib2_seed.track(conn, "Radiohead", "Kid A", "Idioteque", path="/m/b.flac")
        lib2_seed.track(conn, "Radiohead", "Wanted Only", "Nope", owned=False)
        lib2_seed.track(conn, "Portishead", "Dummy", "Roads", path="/m/c.flac")
        conn.commit()

    assert db.get_owned_album_count_by_artist_name("radiohead") == 2
    assert db.get_owned_album_count_by_artist_name("RADIOHEAD") == 2
    assert db.get_owned_album_count_by_artist_name("Radio") == 0
    assert db.get_owned_album_count_by_artist_name("  ") == 0
