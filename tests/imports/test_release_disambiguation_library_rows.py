"""#1299: the standalone library keeps same-named releases as separate albums.

drives record_soulsync_library_entry against the real Library v2 catalogue. before the
fix both releases of "юность в стиле панк" joined one album row by name, and a
third release of the same name died on a duplicate primary key.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.imports import side_effects

ARTIST = "кис-кис"
ALBUM = "юность в стиле панк"
ORIGINAL = "55c7242f-1b4b-485d-b5f2-d6a8feeee088"
BABY_PUNK = "3b98979b-6494-4a7c-8de6-2165902f8a87"
THIRD = "00000000-0000-4000-8000-000000000003"


@pytest.fixture()
def db(monkeypatch, tmp_path):
    """The real catalogue: Library v2 on a real MusicDatabase."""
    from database.music_database import MusicDatabase

    database = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr(side_effects, "get_database", lambda: database)
    monkeypatch.setattr(side_effects, "_get_config_manager",
                        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"))
    import core.genre_filter as genre_filter
    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)
    return database


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


def _albums(db):
    with db._get_connection() as conn:
        rows = conn.execute("SELECT id, title, musicbrainz_id AS musicbrainz_release_id,"
                            "       server_id FROM lib2_albums ORDER BY musicbrainz_id").fetchall()
    return [dict(r) for r in rows]


def _tracks_by_release(db):
    with db._get_connection() as conn:
        rows = conn.execute("""SELECT al.musicbrainz_id AS rel, t.title
                               FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id""").fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["rel"], []).append(r["title"])
    return {k: sorted(v) for k, v in out.items()}


def test_two_releases_of_one_name_are_two_albums(db, tmp_path):
    _import(tmp_path, ORIGINAL, "рэпер", 1)
    _import(tmp_path, ORIGINAL, "трахаюсь", 3)
    _import(tmp_path, BABY_PUNK, "рэпер", 1, "baby punk version")
    _import(tmp_path, BABY_PUNK, "teen love", 3, "baby punk version")

    albums = _albums(db)
    assert len(albums) == 2
    assert {a["musicbrainz_release_id"] for a in albums} == {ORIGINAL, BABY_PUNK}
    assert all(a["title"] == ALBUM for a in albums)
    assert _tracks_by_release(db) == {
        ORIGINAL: ["рэпер", "трахаюсь"],
        BABY_PUNK: ["teen love", "рэпер"],
    }


def test_import_order_does_not_matter(db, tmp_path):
    _import(tmp_path, BABY_PUNK, "рэпер", 1, "baby punk version")
    _import(tmp_path, ORIGINAL, "рэпер", 1)
    _import(tmp_path, BABY_PUNK, "кирилл", 10, "baby punk version")
    _import(tmp_path, ORIGINAL, "потрачено", 9)

    assert _tracks_by_release(db) == {
        ORIGINAL: ["потрачено", "рэпер"],
        BABY_PUNK: ["кирилл", "рэпер"],
    }


def test_a_third_release_of_the_name_gets_its_own_row(db, tmp_path):
    _import(tmp_path, ORIGINAL, "рэпер", 1)
    _import(tmp_path, BABY_PUNK, "рэпер", 1, "baby punk version")
    _import(tmp_path, THIRD, "рэпер", 1, "live")

    assert len(_albums(db)) == 3
    assert set(_tracks_by_release(db)) == {ORIGINAL, BABY_PUNK, THIRD}


def test_legacy_row_without_an_id_is_adopted_not_split(db, tmp_path):
    # an album imported before release ids were stored joins its next import
    legacy_key = str(side_effects._stable_soulsync_id(f"{ARTIST}::{ALBUM}".lower().strip()))
    from tests.support.catalogue_seed import seed_album, seed_artist
    with db._get_connection() as conn:
        artist_id = seed_artist(conn, server_id="ks", name=ARTIST, server_source="soulsync")
        legacy_id = seed_album(conn, server_id=legacy_key, title=ALBUM,
                               artist_id=artist_id, server_source="soulsync")
        conn.commit()

    _import(tmp_path, ORIGINAL, "рэпер", 1)

    albums = _albums(db)
    assert [a["id"] for a in albums] == [legacy_id]
    assert albums[0]["musicbrainz_release_id"] == ORIGINAL


def test_an_existing_release_id_is_never_overwritten(db, tmp_path):
    _import(tmp_path, ORIGINAL, "рэпер", 1)
    album_id = _albums(db)[0]["id"]
    with db._get_connection() as conn:
        side_effects._fill_external_id(conn.cursor(), "lib2_albums", album_id,
                                       "musicbrainz", BABY_PUNK)
        conn.commit()
    assert _albums(db)[0]["musicbrainz_release_id"] == ORIGINAL
