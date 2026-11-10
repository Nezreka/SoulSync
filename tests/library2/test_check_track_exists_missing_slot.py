"""a missing album slot is never "the track you already have".

completeness, discography and wanted rows put fileless tracks on an album.
check_track_exists' album fallback read every lib2_tracks row on the album
with no ownership filter, so with no media server named (the watchlist
scan, music requests, playlist materialize) the missing track 9 of a
partly owned album came back owned and was never wishlisted.
"""

from __future__ import annotations

from database.music_database import MusicDatabase
from tests import lib2_seed


def test_a_fileless_slot_on_an_owned_album_is_not_owned(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    for n in range(1, 8):
        lib2_seed.track(conn, "Bonobo", "Migration", f"Owned Song {n}", track_number=n)
    lib2_seed.track(conn, "Bonobo", "Migration", "Kerala", owned=False, track_number=9)
    conn.commit()
    conn.close()

    track, confidence = db.check_track_exists(
        "Kerala", "Bonobo", confidence_threshold=0.7, server_source=None, album="Migration")
    assert track is None, f"a fileless slot matched at {confidence}"

    owned, _ = db.check_track_exists(
        "Owned Song 3", "Bonobo", confidence_threshold=0.7, server_source=None, album="Migration")
    assert owned is not None
