"""a provider id in an artist URL is a provider id. 3.6.1's artists.id was
the media server's own id; lib2_artists.id is 1..N, so a Deezer id like 13
hit catalogue row 13 and the artist page showed a stranger's releases and
top tracks (third #1062 review, G03-1)."""

from __future__ import annotations

from api.artist_detail import _library_artist_for_id
from database.music_database import MusicDatabase
from tests import lib2_seed


def _library(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    for n in range(1, 14):
        conn.execute("INSERT INTO lib2_artists(name, name_key) VALUES(?, ?)",
                     (f"Stranger {n}", f"stranger {n}"))
    eminem = conn.execute(
        "INSERT INTO lib2_artists(name, name_key, spotify_id, external_ids) "
        "VALUES('Eminem', 'eminem', 'sp-em', json_object('deezer', 13))").lastrowid
    conn.commit()
    return db, conn, eminem


def test_a_deezer_id_finds_its_artist_not_the_row_with_that_number(tmp_path):
    _db, conn, _eminem = _library(tmp_path)
    row = _library_artist_for_id(conn.cursor(), "13")
    assert row["spotify_artist_id"] == "sp-em"
    assert str(row["deezer_id"]) == "13"


def test_a_catalogue_id_is_only_taken_spelled_out(tmp_path):
    _db, conn, eminem = _library(tmp_path)
    assert _library_artist_for_id(conn.cursor(), f"lib2:{eminem}")["spotify_artist_id"] == "sp-em"
    assert _library_artist_for_id(conn.cursor(), "5") is None
