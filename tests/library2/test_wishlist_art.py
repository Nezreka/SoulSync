"""Artwork carried by Library-v2 wishlist rows.

The write path (``track_wishlist_payload``) and the read path
(``core.wishlist.routes._enrich_wishlist_images``) must agree that a Library-v2
row always has a cover: the 2026-08-22 production report found 373 of 373
Library-v2 rows with no album image in any slot, 100% correlated with that
origin and with nothing else.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2 import wishlist_art


@pytest.fixture()
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE lib2_albums (id INTEGER PRIMARY KEY, title TEXT, image_url TEXT);
        CREATE TABLE lib2_artists (id INTEGER PRIMARY KEY, name TEXT, image_url TEXT);
        """
    )
    yield connection
    connection.close()


def test_album_always_yields_at_least_the_local_artwork_endpoint(conn):
    conn.execute("INSERT INTO lib2_albums VALUES (5, 'X', NULL)")
    images = wishlist_art.album_images(conn, 5)
    assert [i["url"] for i in images] == ["/api/library/v2/artwork/album/5"]


def test_album_leads_with_the_local_endpoint_and_keeps_the_cdn_as_stand_in(conn):
    """Same order as the Library v2 pages (`_apply_artwork_urls`): the local
    copy is the long-term truth, the CDN url only covers the cold-build wait."""
    conn.execute("INSERT INTO lib2_albums VALUES (5, 'X', 'https://i.scdn.co/image/a')")
    images = wishlist_art.album_images(conn, 5)
    assert [i["url"] for i in images] == [
        "/api/library/v2/artwork/album/5", "https://i.scdn.co/image/a",
    ]


def test_a_server_side_fetcher_skips_the_relative_entry(conn):
    """cover.jpg and embedded tag art are downloaded by THIS process, which
    cannot resolve a relative URL — so they must not take images[0] blindly."""
    conn.execute("INSERT INTO lib2_albums VALUES (5, 'X', 'https://i.scdn.co/image/a')")
    images = wishlist_art.album_images(conn, 5)
    assert wishlist_art.first_fetchable_image_url(images) == "https://i.scdn.co/image/a"


def test_a_fetcher_gets_nothing_when_only_the_local_endpoint_exists(conn):
    conn.execute("INSERT INTO lib2_albums VALUES (5, 'X', NULL)")
    images = wishlist_art.album_images(conn, 5)
    assert wishlist_art.first_fetchable_image_url(images) is None


@pytest.mark.parametrize("stored", [
    "/rest/getCoverArt.view?id=al-1",          # Navidrome Subsonic path
    "/library/metadata/9/thumb/1",             # Plex path
    "http://10.10.10.10:8008/api/image-cache/x",   # private LAN host
    "http://localhost:4533/rest/getCoverArt",  # loopback
])
def test_unreachable_covers_are_not_offered_at_all(conn, stored):
    """These are precisely the URLs that 404'd for every artist in the report:
    they only load if the browser can reach the media server directly. They may
    not even be the stand-in — a broken stand-in is worse than none."""
    conn.execute("INSERT INTO lib2_albums VALUES (5, 'X', ?)", (stored,))
    images = wishlist_art.album_images(conn, 5)
    assert [i["url"] for i in images] == ["/api/library/v2/artwork/album/5"]


def test_album_images_carry_dimensions():
    """A bare {"url": ...} makes some consumers skip the entry entirely."""
    for entry in wishlist_art.album_images(None, 5):
        assert entry["width"] and entry["height"]


def test_missing_album_id_yields_nothing(conn):
    assert wishlist_art.album_images(conn, None) == []
    assert wishlist_art.album_images(conn, "not-a-number") == []


def test_artist_photo_is_the_local_endpoint(conn):
    conn.execute("INSERT INTO lib2_artists VALUES (3, 'A', 'https://i.scdn.co/image/a')")
    assert wishlist_art.artist_image_url(conn, 3) == "/api/library/v2/artwork/artist/3"
    assert wishlist_art.artist_remote_image_url(conn, 3) == "https://i.scdn.co/image/a"


def test_the_lastfm_placeholder_is_not_even_a_stand_in(conn):
    star = ("https://lastfm.freetls.fastly.net/i/u/300x300/"
            "2a96cbd8b46e442fc41c2b86b821562f.png")
    conn.execute("INSERT INTO lib2_artists VALUES (3, 'A', ?)", (star,))
    assert wishlist_art.artist_image_url(conn, 3) == "/api/library/v2/artwork/artist/3"
    assert wishlist_art.artist_remote_image_url(conn, 3) is None


def test_a_media_server_path_is_not_even_a_stand_in(conn):
    conn.execute("INSERT INTO lib2_artists VALUES (3, 'A', '/rest/getCoverArt.view?id=ar-3')")
    assert wishlist_art.artist_image_url(conn, 3) == "/api/library/v2/artwork/artist/3"
    assert wishlist_art.artist_remote_image_url(conn, 3) is None


def test_artwork_urls_are_stable_across_calls(conn):
    """Nothing here may mint a per-request URL: the image cache keys on the URL,
    so a rotating one fills the cache with a fresh entry per render."""
    conn.execute("INSERT INTO lib2_albums VALUES (5, 'X', NULL)")
    first = wishlist_art.album_images(conn, 5)
    second = wishlist_art.album_images(conn, 5)
    assert first == second
