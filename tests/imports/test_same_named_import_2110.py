"""#2110: the standalone import writer and weezer's self-titled albums.

the grouping turned the green album away from the blue one (another provider
album id), and the writer then found the blue row by the name hash both share
and filed the green album on it anyway.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.imports import side_effects


@pytest.fixture()
def db(monkeypatch, tmp_path):
    from database.music_database import MusicDatabase

    database = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr(side_effects, "get_database", lambda: database)
    monkeypatch.setattr(side_effects, "_get_config_manager",
                        lambda: SimpleNamespace(get_active_media_server=lambda: "soulsync"))
    import core.genre_filter as genre_filter
    monkeypatch.setattr(genre_filter, "filter_genres", lambda genres, _cfg: genres)
    return database


def _import(tmp_path, album_id, date, title, number):
    folder = tmp_path / "Weezer" / f"{date[:4]} - Weezer"
    folder.mkdir(parents=True, exist_ok=True)
    final_path = folder / f"{number:02d} - {title}.flac"
    final_path.write_bytes(b"audio")
    context = {
        "source": "spotify",
        "artist": {"id": "sp-weezer", "name": "Weezer"},
        "album": {"id": album_id, "name": "Weezer", "release_date": date, "total_tracks": 10},
        "track_info": {"id": f"{album_id}-{number}", "name": title, "track_number": number,
                       "duration_ms": 180000, "artists": [{"name": "Weezer"}]},
        "original_search_result": {"title": title},
        "_final_processed_path": str(final_path),
    }
    side_effects.record_soulsync_library_entry(
        context, {"name": "Weezer", "genres": []},
        {"is_album": True, "album_name": "Weezer", "track_number": number})


def _albums(db):
    with db._get_connection() as conn:
        return sorted(
            (a["year"], sorted(r["title"] for r in conn.execute(
                "SELECT title FROM lib2_tracks WHERE album_id=?", (a["id"],))))
            for a in conn.execute("SELECT id, year FROM lib2_albums WHERE title='Weezer'"))


def test_three_self_titled_downloads_are_three_albums(db, tmp_path):
    _import(tmp_path, "sp-blue", "1994-05-10", "My Name Is Jonas", 1)
    _import(tmp_path, "sp-green", "2001-05-15", "Don't Let Go", 1)
    _import(tmp_path, "sp-red", "2008-06-03", "Troublemaker", 1)
    _import(tmp_path, "sp-green", "2001-05-15", "Photograph", 2)
    _import(tmp_path, "sp-blue", "1994-05-10", "No One Else", 2)
    assert _albums(db) == [(1994, ["My Name Is Jonas", "No One Else"]),
                           (2001, ["Don't Let Go", "Photograph"]),
                           (2008, ["Troublemaker"])]


def test_one_album_under_two_provider_ids_stays_one_album(db, tmp_path):
    # a clean and an explicit listing, one per region: same name, same year
    _import(tmp_path, "sp-blue", "1994-05-10", "My Name Is Jonas", 1)
    _import(tmp_path, "sp-blue-us", "1994-05-10", "No One Else", 2)
    assert _albums(db) == [(1994, ["My Name Is Jonas", "No One Else"])]
