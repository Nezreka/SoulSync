"""Wishlist art enrichment (read-path).

Two production bugs are pinned here, both from the 2026-08-22 wishlist report:

* every one of the 373 Library-v2 rows had NO album image at all (the mirror
  payload never carried one), so the UI drew an empty tile for each; and
* all 218 distinct artist photo URLs were media-server-backed
  `/api/image-cache/<hash>` URLs that 404'd immediately, plus 129 further rows
  pointing at the generic Last.fm placeholder star.

Both are fixed on READ, so rows already sitting in the wishlist are repaired
without rewriting a single stored payload.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.wishlist import routes


class _KeepAlive:
    """A connection whose close() is a no-op, so one in-memory db survives the
    enrichment's `finally: conn.close()`."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def close(self):
        pass


class _DB:
    """A minimal but REALISTIC lib2 catalogue — the enrichment resolves through
    ids and relations now, so a two-column stand-in would not exercise it."""

    def __init__(self, artists=(), albums=(), track_artists=()):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE lib2_artists (
                id INTEGER PRIMARY KEY, name TEXT, name_key TEXT, image_url TEXT);
            CREATE TABLE lib2_albums (
                id INTEGER PRIMARY KEY, title TEXT, image_url TEXT);
            CREATE TABLE lib2_track_artists (
                track_id INTEGER, artist_id INTEGER, role TEXT, position INTEGER);
            """
        )
        self._conn.executemany(
            "INSERT INTO lib2_artists (id, name, name_key, image_url) VALUES (?,?,?,?)",
            [(a[0], a[1], a[1].lower(), a[2]) for a in artists],
        )
        self._conn.executemany(
            "INSERT INTO lib2_albums (id, title, image_url) VALUES (?,?,?)", albums,
        )
        self._conn.executemany(
            "INSERT INTO lib2_track_artists (track_id, artist_id, role, position) "
            "VALUES (?,?, 'primary', ?)", track_artists,
        )
        self._conn.commit()

    # The enrichment closes what it opens; keep the in-memory db alive.
    def _get_connection(self):
        return _KeepAlive(self._conn)

    @property
    def database_path(self):
        return ":memory:"


@pytest.fixture(autouse=True)
def _stub_normalize(monkeypatch):
    # Deterministic stand-in for the real Plex/Jellyfin URL rebuild.
    monkeypatch.setattr(routes, "normalize_image_url", lambda u: f"PROXY({u})")


def _lib2_row(*, artist="A", lib2_track_id=None, lib2_album_id=None, images=None):
    source_info = {"source": "library_v2"}
    if lib2_track_id is not None:
        source_info["lib2_track_id"] = lib2_track_id
    if lib2_album_id is not None:
        source_info["lib2_album_id"] = lib2_album_id
    return {
        "artist_name": artist,
        "source_info": source_info,
        "spotify_data": {"album": {"images": list(images or [])}},
    }


# --- the predicate ---------------------------------------------------------

def test_needs_image_fix_predicate():
    assert routes._needs_image_fix("/library/metadata/1/thumb/2") is True
    assert routes._needs_image_fix("/Items/x/Images/Primary") is True
    assert routes._needs_image_fix("http://localhost:32400/library/x") is True
    assert routes._needs_image_fix("https://i.scdn.co/image/ab") is False
    assert routes._needs_image_fix("https://is1.mzstatic.com/600x600bb.jpg") is False
    assert routes._needs_image_fix("") is False
    assert routes._needs_image_fix(None) is False


def test_soulsync_own_urls_are_never_a_fix_target():
    """`/api/library/v2/artwork/..` starts with `/api/`, which normalize_image_url
    reads as a legacy Navidrome path — rewriting our own working artwork URL
    into an unreachable media-server one."""
    assert routes._needs_image_fix("/api/library/v2/artwork/album/12?v=3") is False
    assert routes._needs_image_fix("/api/image-cache/" + "a" * 64) is False


# --- album covers ----------------------------------------------------------

def test_relative_album_image_is_normalized():
    tracks = [{"artist_name": "A",
               "spotify_data": {"album": {"images": [{"url": "/library/metadata/9/thumb/1"}]}}}]
    routes._enrich_wishlist_images(tracks, _DB())
    assert tracks[0]["spotify_data"]["album"]["images"][0]["url"] == "PROXY(/library/metadata/9/thumb/1)"


def test_cdn_album_image_is_left_untouched():
    """Items that already render must not change — guards against regressing normal wishlist art."""
    url = "https://i.scdn.co/image/ab67616d"
    tracks = [{"artist_name": "A", "spotify_data": {"album": {"images": [{"url": url}]}}}]
    routes._enrich_wishlist_images(tracks, _DB())
    assert tracks[0]["spotify_data"]["album"]["images"][0]["url"] == url


def test_library_v2_row_without_any_image_gets_a_cover():
    """The report's headline finding: 373/373 Library-v2 rows had no cover."""
    tracks = [_lib2_row(lib2_album_id=4158)]
    routes._enrich_wishlist_images(tracks, _DB(albums=[(4158, "METAMORPHOSIS 2", None)]))
    images = tracks[0]["spotify_data"]["album"]["images"]
    assert images, "a Library-v2 row must never come back image-less"
    assert images[0]["url"] == "/api/library/v2/artwork/album/4158"


def test_library_v2_cover_leads_with_the_local_endpoint():
    """Same precedence as the Library v2 pages: the locally cached copy is the
    primary url, the CDN cover stands in while a cold build runs."""
    cdn = "https://i.scdn.co/image/ab67616d0000b273"
    tracks = [_lib2_row(lib2_album_id=7)]
    routes._enrich_wishlist_images(tracks, _DB(albums=[(7, "X", cdn)]))
    images = tracks[0]["spotify_data"]["album"]["images"]
    assert [i["url"] for i in images] == ["/api/library/v2/artwork/album/7", cdn]


def test_media_server_cover_is_not_offered_at_all():
    """A `/rest/..` path is what turns into an authenticated media-server URL
    that 404s whenever the server is unreachable from the browser. It may not
    even be the stand-in — a broken stand-in is worse than none."""
    tracks = [_lib2_row(lib2_album_id=7)]
    routes._enrich_wishlist_images(
        tracks, _DB(albums=[(7, "X", "/rest/getCoverArt.view?id=al-1")]),
    )
    assert [i["url"] for i in tracks[0]["spotify_data"]["album"]["images"]] == [
        "/api/library/v2/artwork/album/7",
    ]


def test_existing_album_image_is_not_replaced():
    url = "https://i.scdn.co/image/keepme"
    tracks = [_lib2_row(lib2_album_id=7, images=[{"url": url}])]
    routes._enrich_wishlist_images(tracks, _DB(albums=[(7, "X", "https://other/cdn.jpg")]))
    assert tracks[0]["spotify_data"]["album"]["images"][0]["url"] == url


# --- artist photos ---------------------------------------------------------

def test_artist_photo_resolves_through_the_library_v2_track_relation():
    """Not by exact name: the payload's spelling and the catalogue's can differ,
    and the report showed 40 artist names that simply never matched."""
    tracks = [_lib2_row(artist="INTERWORLD", lib2_track_id=10675)]
    db = _DB(artists=[(3, "InterWorld", "https://cdn-images.dzcdn.net/x.jpg")],
             track_artists=[(10675, 3, 0)])
    photos, fallbacks = routes._enrich_wishlist_images(tracks, db)
    assert photos == {"interworld": "/api/library/v2/artwork/artist/3"}
    assert fallbacks == {"interworld": "https://cdn-images.dzcdn.net/x.jpg"}


def test_artist_photo_falls_back_to_a_folded_name_match():
    tracks = [{"artist_name": "Modest Mouse", "spotify_data": {"album": {"images": []}}}]
    db = _DB(artists=[(1, "Modest Mouse", "https://i.scdn.co/image/mm"),
                      (2, "Other Band", "https://i.scdn.co/image/ob")])
    photos, fallbacks = routes._enrich_wishlist_images(tracks, db)
    assert photos == {"modest mouse": "/api/library/v2/artwork/artist/1"}
    assert fallbacks == {"modest mouse": "https://i.scdn.co/image/mm"}


def test_media_server_artist_photo_is_replaced_and_not_kept_as_a_stand_in():
    """The 218 permanently-404ing photos: a Plex/Navidrome path normalized into
    an authenticated media-server URL nobody could load."""
    tracks = [{"artist_name": "B", "spotify_data": {"album": {"images": []}}}]
    db = _DB(artists=[(11, "B", "/library/metadata/222/thumb/9")])
    photos, fallbacks = routes._enrich_wishlist_images(tracks, db)
    assert photos == {"b": "/api/library/v2/artwork/artist/11"}
    assert fallbacks == {}


def test_lastfm_placeholder_star_does_not_count_as_a_photo():
    """129 rows / 99 artists shared this one URL. It loads fine — which is why
    every `if url:` check passed — but it is a grey star, not an artist."""
    star = ("https://lastfm.freetls.fastly.net/i/u/300x300/"
            "2a96cbd8b46e442fc41c2b86b821562f.png")
    tracks = [{"artist_name": "C", "spotify_data": {"album": {"images": []}}}]
    db = _DB(artists=[(12, "C", star)])
    photos, fallbacks = routes._enrich_wishlist_images(tracks, db)
    assert photos == {"c": "/api/library/v2/artwork/artist/12"}
    assert fallbacks == {}, "a grey star is not worth painting even as a stand-in"


def test_artist_without_a_catalogue_row_is_omitted():
    tracks = [{"artist_name": "Nobody", "spotify_data": {"album": {"images": []}}}]
    assert routes._enrich_wishlist_images(tracks, _DB())[0] == {}


def test_unknown_artist_is_skipped():
    tracks = [{"artist_name": "Unknown Artist", "spotify_data": {}}]
    assert routes._enrich_wishlist_images(tracks, _DB())[0] == {}


def test_source_info_arriving_as_json_text_is_still_understood():
    """`source_info` reaches the enrichment as a dict from the service and as
    JSON text from some callers; both must resolve the same row."""
    tracks = [{
        "artist_name": "A",
        "source_info": '{"source": "library_v2", "lib2_album_id": 99}',
        "spotify_data": {"album": {"images": []}},
    }]
    routes._enrich_wishlist_images(tracks, _DB(albums=[(99, "X", None)]))
    assert tracks[0]["spotify_data"]["album"]["images"][0]["url"] == \
        "/api/library/v2/artwork/album/99"


def test_artist_lookup_survives_a_catalogue_without_name_key():
    """`name_key` is additive. A database from before it existed must still get
    photos, not silently get none."""
    db = _DB()
    db._conn.executescript(
        "DROP TABLE lib2_artists;"
        "CREATE TABLE lib2_artists (id INTEGER PRIMARY KEY, name TEXT, image_url TEXT);"
        "INSERT INTO lib2_artists VALUES (5, 'Boards of Canada', 'https://i.scdn.co/image/boc');"
    )
    tracks = [{"artist_name": "Boards of Canada", "spotify_data": {"album": {"images": []}}}]
    photos, fallbacks = routes._enrich_wishlist_images(tracks, db)
    assert photos == {"boards of canada": "/api/library/v2/artwork/artist/5"}
    assert fallbacks == {"boards of canada": "https://i.scdn.co/image/boc"}
