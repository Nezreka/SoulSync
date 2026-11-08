"""a play of a 3.6.1 playlist-folder copy (its row joined the library track
as a file) is linked to that track (third #1062 review P2A-3)."""

from __future__ import annotations

from database.music_database import MusicDatabase
from tests import lib2_seed


def test_a_copy_play_is_linked_at_start(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        track = lib2_seed.track(conn, "Artist", "Album", "Song", path="/m/song.flac")
        conn.execute("UPDATE lib2_tracks SET legacy_track_id=100 WHERE id=?", (track,))
        # the playlist-folder copy's 3.6.1 row joined the track as a file
        conn.execute("INSERT INTO lib2_track_files(track_id, path, legacy_track_id) "
                     "VALUES(?, '/playlists/Mix/song.flac', 555)", (track,))
        conn.execute("INSERT INTO listening_history(db_track_id, title, artist, played_at) "
                     "VALUES('555', 'Song', 'Artist', '2026-09-01 10:00:00')")
        conn.commit()

    # the next start's listening-history pass
    with db._get_connection() as conn:
        db._add_listening_history_table(conn.cursor())
        conn.commit()
        assert conn.execute("SELECT lib2_track_id FROM listening_history").fetchone()[0] == track
