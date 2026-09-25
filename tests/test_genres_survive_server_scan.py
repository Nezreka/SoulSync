"""Genres SoulSync's genre jobs settled survive a media server scan (Cremonies).

Upstream's catalogue let a scan write whatever genres the server read from the
files, so a whitelist cleanup quietly undid itself on the next scan, and it
added a ``genres_locked`` flag to stop that. Library v2 never had the bug: a
media-server scan is not an ownership path and only stamps its mapping on a
catalogue row it already knows -- it does not write genres at all. These pin
that, since it is the whole reason the lock does not exist here.

Real upserts, real database.
"""

from __future__ import annotations

import json

import pytest

from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_library_track


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture()
def db(tmp_path):
    database = MusicDatabase(str(tmp_path / "genres.db"))
    with database._get_connection() as conn:
        seed_library_track(conn, artist="Radiohead", album="OK Computer", title="Airbag",
                           server_source="navidrome", artist_server_id="ar-1",
                           album_server_id="al-1", track_server_id="t-1",
                           file_path="/music/Radiohead/OK Computer/01 - Airbag.flac")
        conn.execute("UPDATE lib2_artists SET genres=? ", (json.dumps(["Alternative"]),))
        conn.execute("UPDATE lib2_albums SET genres=? ", (json.dumps(["Alternative"]),))
        conn.commit()
    return database


def _genres(db, table):
    with db._get_connection() as conn:
        return json.loads(conn.execute(f"SELECT genres FROM {table}").fetchone()[0])


def test_a_scan_never_puts_the_files_genres_back(db):
    artist = _Obj(ratingKey="ar-1", title="Radiohead", thumb=None,
                  genres=["Alternative", "Britpop", "seen live"], summary="")
    album = _Obj(ratingKey="al-1", title="OK Computer", year=1997, thumb=None,
                 genres=["Alternative", "Britpop", "seen live"], leafCount=12, duration=3200)
    db.insert_or_update_media_artist(artist, server_source="navidrome")
    db.insert_or_update_media_album(album, "ar-1", server_source="navidrome")
    assert _genres(db, "lib2_artists") == ["Alternative"]
    assert _genres(db, "lib2_albums") == ["Alternative"]
