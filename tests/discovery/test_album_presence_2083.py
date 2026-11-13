"""#2083: "Albums You're Missing" listed albums already in the library.

both sides were compared with lower().strip(), so an apostrophe style, a
separator or a pressing tag made an owned album look missing.
"""

from __future__ import annotations

from contextlib import closing

import pytest

from core.wishlist.presence import album_presence_key, artist_presence_key
from database.music_database import MusicDatabase


@pytest.mark.parametrize("suggested, owned", [
    ("Results May Vary", "Results May Vary (explicit)"),
    ("Comme elle vient - Live 2002", "Comme elle vient : Live 2002"),
    ("1993 J'appuie sur la g\u00e2chette...", "1993... J'appuie sur la g\u00e2chette"),
    ("It's A Mother", "It\u2019s a Mother"),
    ("Love Power Peace (Live At The Olympia, Paris, 1971)",
     "Love Power Peace: Live at The Olympia, Paris, 1971"),
    ("Sliver - The Best Of The Box", "Sliver: The Best of the Box"),
    ("Dial 'M' for Monkey", "Dial \u2018M\u2019 for Monkey"),
    ("G-Sides", "G Sides"),
    ("Nevermind (30th Anniversary Super Deluxe)", "Nevermind"),
    ("Led Zeppelin IV (Remaster)", "[Led Zeppelin IV]"),
    ("I Exist Inside This Machine - EP", "I Exist Inside This Machine (EP)"),
    ("Abbey Road (Remastered 2009)", "Abbey Road"),
    ("X (Deluxe) (2011 Remaster)", "X"),
    ("Love [Explicit Version]", "Love"),
    ("Kindred EP", "Kindred"),
    ("Hello - Single", "Hello"),
    ("Revolver (Mono)", "Revolver [2009 Stereo Remaster]"),
])
def test_the_same_album_keys_the_same(suggested, owned):
    assert album_presence_key(suggested, "Artist") == album_presence_key(owned, "Artist")


@pytest.mark.parametrize("one, other", [
    ("ABBA Gold", "ABBA"),
    ("Something Special", "Something"),
    ("Mezzanine (Live)", "Mezzanine"),
    ("Inhuman (Remixes)", "Inhuman"),
    ("1989 (Taylor's Version)", "1989"),
    ("Led Zeppelin (Remaster)", "Led Zeppelin II"),
    ("Deep", "De"),
    ("!!!", "..."),
    ("(Explicit)", "(Deluxe)"),
])
def test_a_different_album_keys_differently(one, other):
    assert album_presence_key(one, "Artist") != album_presence_key(other, "Artist")


def test_a_title_that_folds_to_nothing_stays_literal():
    assert album_presence_key("!!!", "!!!").startswith("!!!|||")
    assert album_presence_key("(Explicit)", "A") == "explicit|||a"
    assert album_presence_key("EP", "A") == "ep|||a"
    for title in ("\u6771\u4eac", "\u041a\u0438\u043d\u043e"):
        assert not album_presence_key(title, "A").startswith("|||")


def test_a_title_that_repeats_its_artist_is_the_title_alone():
    assert (album_presence_key("Max Cooper - Traum Collection", "Max Cooper")
            == album_presence_key("Traum Collection", "Max Cooper"))
    # a self-titled album, and a title that only starts with the name, are left whole
    assert album_presence_key("Weezer", "Weezer") == "weezer|||weezer"
    assert album_presence_key("ABBA Gold", "ABBA") == "abbagold|||abba"
    assert album_presence_key("Blur - ", "Blur") == "blur|||blur"


def test_an_artist_is_one_artist_however_it_is_accented():
    assert artist_presence_key("BJ\u00d6RK ") == artist_presence_key("Bjork") == "bjork"
    assert artist_presence_key("!!!") == "!!!"


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _own(conn, artist, title):
    aid = conn.execute("SELECT id FROM lib2_artists WHERE name=?", (artist,)).fetchone()
    aid = aid[0] if aid else conn.execute(
        "INSERT INTO lib2_artists(name) VALUES(?)", (artist,)).lastrowid
    al = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?,?)",
                      (aid, title)).lastrowid
    t = conn.execute("INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'x')", (al,)).lastrowid
    conn.execute("INSERT INTO lib2_track_files(track_id, path, file_state) VALUES(?,?,'active')",
                 (t, f"/m/{artist}/{title}.flac"))


def _cache(conn, artist, title, popularity):
    conn.execute(
        "INSERT INTO metadata_cache_entities(source, entity_type, entity_id, name, artist_name,"
        " popularity, raw_json) VALUES('spotify', 'album', ?, ?, ?, ?, '{}')",
        (f"id-{title}", title, artist, popularity))


def test_the_shelf_drops_owned_albums_and_lists_a_missing_one_once(db):
    from api.discover_routes import _owned_album_keys_for
    from core.metadata.cache import MetadataCache

    with closing(db._get_connection()) as conn:
        _own(conn, "Limp Bizkit", "Results May Vary (explicit)")
        _own(conn, "Bj\u00f6rk", "It\u2019s a Mother")
        _cache(conn, "Limp Bizkit", "Results May Vary", 90)
        _cache(conn, "Bjork", "It's A Mother", 80)
        _cache(conn, "Limp Bizkit", "Significant Other (Deluxe)", 70)
        _cache(conn, "Limp Bizkit", "Significant Other", 60)
        _cache(conn, "Limp Bizkit", "Gold Cobra", 50)
        conn.commit()
        names = ["Limp Bizkit", "Bjork"]
        keys = _owned_album_keys_for(conn, names)

    cache = MetadataCache.__new__(MetadataCache)
    cache._get_db = lambda: db
    cache._with_art = lambda cursor, rows, kind: rows
    albums = cache.get_undiscovered_albums(names, keys, source="spotify", limit=20)
    assert [a["name"] for a in albums] == ["Significant Other (Deluxe)", "Gold Cobra"]


@pytest.mark.parametrize("other", [
    "Mezzanine (Live - Special Edition)", "Mezzanine (Acoustic Edition)",
    "Mezzanine (Extended Mixes)", "Mezzanine (Clean Bandit Remix)", "Mezzanine (Deluxe Demos)",
])
def test_a_pressing_word_next_to_another_recording_does_not_make_it_the_album(other):
    assert album_presence_key(other, "Artist") != album_presence_key("Mezzanine", "Artist")


def test_a_symbol_title_keys_the_same_with_its_pressing_tag():
    assert album_presence_key("÷ (Deluxe)", "Ed") == album_presence_key("÷", "Ed")
