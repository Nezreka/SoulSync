"""an import only files an id under a real provider, and only an id that can be
one (#1996). 'webui_modal' is the screen a grab came from, and a last.fm page
url ended up under deezer, so the deezer worker asked for it on every pass."""

import json

import pytest

from core.imports import side_effects
from database.music_database import MusicDatabase


@pytest.fixture
def album(tmp_path):
    from tests.support.catalogue_seed import seed_album, seed_artist
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        artist_id = seed_artist(conn, server_id="a", name="Clint Mansell", server_source="soulsync")
        album_id = seed_album(conn, server_id="b", title="Requiem for a Dream",
                              artist_id=artist_id, server_source="soulsync")
        conn.commit()
    return db, album_id


def _fill(db, album_id, source, value):
    with db._get_connection() as conn:
        side_effects._fill_external_id(conn.cursor(), "lib2_albums", album_id, source, value)
        conn.commit()
        raw = conn.execute("SELECT external_ids FROM lib2_albums WHERE id=?", (album_id,)).fetchone()[0]
    return json.loads(raw or "{}")


LASTFM_URL = "https://www.last.fm/music/Clint+Mansell/Requiem+for+a+Dream"


@pytest.mark.parametrize("source,value", [
    ("webui_modal", "496099"),
    ("unknown", "496099"),
    ("deezer", LASTFM_URL),
    ("itunes", LASTFM_URL),
    ("deezer.nested", "496099"),
])
def test_a_fake_provider_or_an_id_that_cant_be_one_is_not_filed(album, source, value):
    db, album_id = album
    assert _fill(db, album_id, source, value) == {}


@pytest.mark.parametrize("source,value", [
    ("deezer", "496099"),
    ("Deezer", "496099"),
    ("tidal", "1234"),
    ("lastfm", LASTFM_URL),
])
def test_a_real_provider_id_is_still_filed(album, source, value):
    db, album_id = album
    assert _fill(db, album_id, source, value) == {source.lower(): value}


def test_the_one_time_purge_drops_what_was_filed_wrong_and_keeps_the_rest(album):
    db, album_id = album
    before = {"deezer": LASTFM_URL, "lastfm": LASTFM_URL, "webui_modal": LASTFM_URL,
              "itunes": "723654366", "barcode": "0731454"}
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_albums SET external_ids=? WHERE id=?", (json.dumps(before), album_id))
        conn.execute("DROP TABLE IF EXISTS _fake_provider_ids_purged")
        db._purge_fake_provider_ids(conn.cursor())
        conn.commit()
        raw = conn.execute("SELECT external_ids FROM lib2_albums WHERE id=?", (album_id,)).fetchone()[0]
    assert json.loads(raw) == {"lastfm": LASTFM_URL, "itunes": "723654366", "barcode": "0731454"}
