"""a redownload on a path-mapped install: the old row stores the media
server's path, SoulSync read the file at its local path. the old row has to
go and the new file become primary (third #1062 review G06-3)."""

from __future__ import annotations

from core.imports import pipeline
from database.music_database import MusicDatabase
from tests import lib2_seed


def test_the_moved_away_file_never_stays_primary(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    stored = "/data/music/A/B/01 X.mp3"            # what the server reported
    track = lib2_seed.track(conn, "A", "B", "X", path=stored)
    new = str(tmp_path / "music" / "A" / "B" / "01 X.flac")
    conn.execute("INSERT INTO lib2_track_files(track_id, path, is_primary) VALUES(?, ?, 0)", (track, new))
    conn.commit()
    monkeypatch.setattr("database.music_database.get_database", lambda *_a, **_k: db)

    pipeline._retire_lib2_path_after_redownload(
        "/music/A/B/01 X.mp3", new, track_id=track, stored_path=stored)

    rows = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT path, COALESCE(file_state,'active'), is_primary FROM lib2_track_files WHERE track_id=?",
        (track,))}
    assert rows[stored][0] == "deleted"
    assert rows[new] == ("active", 1)
