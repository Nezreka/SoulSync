"""#1299: the standalone library keeps same-named releases as separate albums.

drives record_soulsync_library_entry against a real sqlite schema. before the
fix both releases of "юность в стиле панк" joined one album row by name, and a
third release of the same name died on a duplicate primary key.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from core.imports import side_effects

ARTIST = "кис-кис"
ALBUM = "юность в стиле панк"
ORIGINAL = "55c7242f-1b4b-485d-b5f2-d6a8feeee088"
BABY_PUNK = "3b98979b-6494-4a7c-8de6-2165902f8a87"
THIRD = "00000000-0000-4000-8000-000000000003"


class _FakeDB:
    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        return self._conn


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE artists (
        id TEXT PRIMARY KEY, name TEXT, genres TEXT, thumb_url TEXT, server_source TEXT,
        created_at TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE albums (
        id TEXT PRIMARY KEY, artist_id TEXT, title TEXT, year INTEGER, thumb_url TEXT,
        genres TEXT, track_count INTEGER, duration INTEGER, server_source TEXT,
        created_at TEXT, updated_at TEXT, spotify_album_id TEXT,
        musicbrainz_release_id TEXT, musicbrainz_match_status TEXT)""")
    conn.execute("""CREATE TABLE tracks (
        id TEXT PRIMARY KEY, album_id TEXT, artist_id TEXT, title TEXT,
        track_number INTEGER, duration INTEGER, file_path TEXT, bitrate INTEGER,
        file_size INTEGER, track_artist TEXT, musicbrainz_recording_id TEXT, isrc TEXT,
        quality_profile_id INTEGER, server_source TEXT, created_at TEXT, updated_at TEXT)""")
    return conn


@pytest.fixture()
def conn(monkeypatch):
    c = _db()
    monkeypatch.setattr(side_effects, "get_database", lambda: _FakeDB(c))
    monkeypatch.setattr(side_effects, "_get_config_manager",
                        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"))
    import core.genre_filter as genre_filter
    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)
    yield c
    c.close()


def _import(tmp_path, release_id, title, number, disambiguation=""):
    folder = tmp_path / (release_id[:8])
    folder.mkdir(exist_ok=True)
    final_path = folder / f"{number:02d} - {title}.flac"
    final_path.write_bytes(b"audio")
    album = {"id": release_id, "musicbrainz_release_id": release_id, "name": ALBUM,
             "release_date": "2019-03-22", "total_tracks": 9}
    if disambiguation:
        album["disambiguation"] = disambiguation
    context = {
        "source": "musicbrainz",
        "artist": {"id": "mb-artist", "name": ARTIST},
        "album": album,
        "track_info": {"id": f"{release_id}-{number}", "name": title, "track_number": number,
                       "duration_ms": 180000, "artists": [{"name": ARTIST}]},
        "original_search_result": {"title": title},
        "_final_processed_path": str(final_path),
    }
    side_effects.record_soulsync_library_entry(
        context, {"name": ARTIST, "genres": []},
        {"is_album": True, "album_name": ALBUM, "track_number": number})


def _albums(conn):
    rows = conn.execute("SELECT id, title, musicbrainz_release_id, musicbrainz_match_status "
                        "FROM albums ORDER BY musicbrainz_release_id").fetchall()
    return [dict(r) for r in rows]


def _tracks_by_release(conn):
    rows = conn.execute("""SELECT al.musicbrainz_release_id AS rel, t.title
                           FROM tracks t JOIN albums al ON al.id = t.album_id""").fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["rel"], []).append(r["title"])
    return {k: sorted(v) for k, v in out.items()}


def test_two_releases_of_one_name_are_two_albums(conn, tmp_path):
    _import(tmp_path, ORIGINAL, "рэпер", 1)
    _import(tmp_path, ORIGINAL, "трахаюсь", 3)
    _import(tmp_path, BABY_PUNK, "рэпер", 1, "baby punk version")
    _import(tmp_path, BABY_PUNK, "teen love", 3, "baby punk version")

    albums = _albums(conn)
    assert len(albums) == 2
    assert {a["musicbrainz_release_id"] for a in albums} == {ORIGINAL, BABY_PUNK}
    assert all(a["title"] == ALBUM for a in albums)
    assert all(a["musicbrainz_match_status"] == "matched" for a in albums)
    assert _tracks_by_release(conn) == {
        ORIGINAL: ["рэпер", "трахаюсь"],
        BABY_PUNK: ["teen love", "рэпер"],
    }


def test_import_order_does_not_matter(conn, tmp_path):
    _import(tmp_path, BABY_PUNK, "рэпер", 1, "baby punk version")
    _import(tmp_path, ORIGINAL, "рэпер", 1)
    _import(tmp_path, BABY_PUNK, "кирилл", 10, "baby punk version")
    _import(tmp_path, ORIGINAL, "потрачено", 9)

    assert _tracks_by_release(conn) == {
        ORIGINAL: ["потрачено", "рэпер"],
        BABY_PUNK: ["кирилл", "рэпер"],
    }


def test_a_third_release_of_the_name_gets_its_own_row(conn, tmp_path):
    _import(tmp_path, ORIGINAL, "рэпер", 1)
    _import(tmp_path, BABY_PUNK, "рэпер", 1, "baby punk version")
    _import(tmp_path, THIRD, "рэпер", 1, "live")

    assert len(_albums(conn)) == 3
    assert set(_tracks_by_release(conn)) == {ORIGINAL, BABY_PUNK, THIRD}


def test_legacy_row_without_an_id_is_adopted_not_split(conn, tmp_path):
    # an album imported before release ids were stored joins its next import
    legacy_id = side_effects._stable_soulsync_id(f"{ARTIST}::{ALBUM}".lower().strip())
    artist_id = side_effects._stable_soulsync_id(ARTIST.lower().strip())
    conn.execute("INSERT INTO artists (id, name, server_source) VALUES (?, ?, 'soulsync')",
                 (artist_id, ARTIST))
    conn.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, 'soulsync')",
                 (legacy_id, artist_id, ALBUM))

    _import(tmp_path, ORIGINAL, "рэпер", 1)

    albums = _albums(conn)
    assert [a["id"] for a in albums] == [legacy_id]
    assert albums[0]["musicbrainz_release_id"] == ORIGINAL


def test_an_existing_release_id_is_never_overwritten(conn, tmp_path):
    _import(tmp_path, ORIGINAL, "рэпер", 1)
    album_id = _albums(conn)[0]["id"]
    side_effects._fill_empty_mb_release_id(conn.cursor(), album_id, BABY_PUNK)
    assert _albums(conn)[0]["musicbrainz_release_id"] == ORIGINAL
