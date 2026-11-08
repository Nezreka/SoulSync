"""3.6.1's spotify/deezer artist credits reach Library v2 (#6ec2ece64).

the upgrade copied neither credit table and records every 3.6.1 match as
done, so nothing fetched them again: 'watch the throne' left kanye's page.
"""

from __future__ import annotations

from core.library2.legacy_credits import carry_legacy_artist_credits
from database.music_database import MusicDatabase
from tests import lib2_seed

LEGACY_DDL = [
    """CREATE TABLE IF NOT EXISTS track_artist_credits (
        track_id TEXT NOT NULL, source TEXT NOT NULL, position INTEGER NOT NULL,
        name TEXT NOT NULL, source_artist_id TEXT,
        PRIMARY KEY (track_id, source, position)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS album_artist_credits (
        album_id TEXT NOT NULL, source TEXT NOT NULL, position INTEGER NOT NULL,
        name TEXT NOT NULL, source_artist_id TEXT,
        PRIMARY KEY (album_id, source, position)) WITHOUT ROWID""",
]


def _library(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    for ddl in LEGACY_DDL:
        conn.execute(ddl)
    jay = lib2_seed.artist(conn, "JAY-Z")
    ye = lib2_seed.artist(conn, "Kanye West")
    conn.execute("UPDATE lib2_artists SET spotify_id='sp-jay' WHERE id=?", (jay,))
    conn.execute("UPDATE lib2_artists SET spotify_id='sp-ye' WHERE id=?", (ye,))
    album = lib2_seed.album(conn, "JAY-Z", "Watch the Throne", legacy_album_id=55)
    otis = lib2_seed.track(conn, "JAY-Z", "Watch the Throne", "Otis", legacy_track_id=101)
    solo = lib2_seed.track(conn, "JAY-Z", "Watch the Throne", "Solo", legacy_track_id=102)
    kept = lib2_seed.track(conn, "JAY-Z", "Watch the Throne", "Kept", legacy_track_id=103)
    conn.executemany("INSERT INTO album_artist_credits VALUES (?,?,?,?,?)", [
        ("55", "spotify", 0, "JAY-Z", "sp-jay"), ("55", "spotify", 1, "Kanye West", "sp-ye")])
    conn.executemany("INSERT INTO track_artist_credits VALUES (?,?,?,?,?)", [
        ("101", "spotify", 0, "JAY-Z", "sp-jay"), ("101", "spotify", 1, "Kanye West", "sp-ye"),
        ("102", "spotify", 0, "JAY-Z", "sp-jay"),
        ("103", "spotify", 0, "JAY-Z", "sp-jay"), ("103", "spotify", 1, "Old Guest", "sp-old")])
    # a match the v2 workers made since: its snapshot stays
    conn.execute("INSERT INTO lib2_provider_credits VALUES ('track', ?, 'spotify', 0, 'JAY-Z', 'sp-jay')",
                 (kept,))
    conn.commit()
    return db, conn, {"album": album, "otis": otis, "solo": solo, "kept": kept, "ye": ye}


def test_3_6_1_credits_land_on_the_v2_catalogue(tmp_path):
    _db, conn, ids = _library(tmp_path)

    stats = carry_legacy_artist_credits(conn)

    assert stats["album_credits"] == 2 and stats["track_credits"] == 2
    # watch the throne is on kanye's page again, and otis credits him
    assert conn.execute("SELECT 1 FROM lib2_album_artists WHERE album_id=? AND artist_id=?",
                        (ids["album"], ids["ye"])).fetchone()
    assert conn.execute("SELECT 1 FROM lib2_track_artists WHERE track_id=? AND artist_id=?",
                        (ids["otis"], ids["ye"])).fetchone()
    # a lone credit adds nothing, and the newer v2 snapshot is untouched
    assert not conn.execute("SELECT 1 FROM lib2_provider_credits WHERE entity_id=?",
                            (ids["solo"],)).fetchone()
    assert [r[0] for r in conn.execute(
        "SELECT name FROM lib2_provider_credits WHERE entity_type='track' AND entity_id=?",
        (ids["kept"],))] == ["JAY-Z"]


def test_it_runs_once(tmp_path):
    _db, conn, _ids = _library(tmp_path)
    carry_legacy_artist_credits(conn)
    conn.execute("DELETE FROM lib2_provider_credits")
    conn.commit()
    assert sum(carry_legacy_artist_credits(conn).values()) == 0
    assert conn.execute("SELECT COUNT(*) FROM lib2_provider_credits").fetchone()[0] == 0
