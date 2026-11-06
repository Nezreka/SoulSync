"""similar artists must save image urls, never image cache links.

a cache link lives as long as its cache entry: 30 days, then the prune drops
the row and its original url with it. similar artists saved cache links, so
when 14k entries expired on Nov 5 the discover hero went blank (faces fell
back to initials) with nothing left to refetch from.
"""

from __future__ import annotations

import pytest

import core.image_cache as image_cache_module
from core.image_cache import ImageCache, cache_key_of, durable_image_url
from database.music_database import MusicDatabase

DZ = "https://cdn-images.dzcdn.net/images/artist/abc/1000x1000-000000-80-0-0.jpg"


class _Resp:
    status_code = 200
    headers = {"Content-Type": "image/jpeg", "Content-Length": "4"}

    def iter_content(self, chunk_size=65536):
        yield b"jpeg"

    def close(self):
        pass


@pytest.fixture
def cache(tmp_path, monkeypatch):
    c = ImageCache(tmp_path / "img", fetcher=lambda url, **_: _Resp())
    monkeypatch.setattr(image_cache_module, "get_image_cache", lambda: c)
    return c


def test_a_cache_link_is_saved_as_its_original(cache):
    link = cache.cache_url_for(DZ)
    assert link.startswith("/api/image-cache/")
    # still sitting in the unsent registrations
    assert durable_image_url(link) == DZ
    cache._flush_registrations()
    assert durable_image_url(link) == DZ
    assert durable_image_url(link + "?v=grid") == DZ


def test_a_forgotten_link_is_none_and_a_plain_url_passes(cache):
    assert durable_image_url("/api/image-cache/" + "0" * 64) is None
    assert durable_image_url(DZ) == DZ
    assert durable_image_url(None) is None
    assert cache_key_of("/api/library/v2/artwork/1") is None


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def test_the_writers_save_the_original(cache, db):
    link = cache.cache_url_for(DZ)
    db.add_or_update_similar_artist(
        "sp-src", "Tiago PZK", similar_artist_spotify_id="sp-tiago",
        image_url=link, source_provider="spotify")
    db.replace_similar_artists("sp-src2", [
        {"similar_artist_name": "Go Golden Junk", "image_url": link},
    ], source_provider="spotify")
    with db._get_connection() as conn:
        urls = {r[0] for r in conn.execute("SELECT image_url FROM similar_artists")}
    assert urls == {DZ}

    with db._get_connection() as conn:
        row_id = conn.execute("SELECT id FROM similar_artists LIMIT 1").fetchone()[0]
    db.update_similar_artist_metadata(row_id, image_url=link)
    with db._get_connection() as conn:
        assert conn.execute("SELECT image_url FROM similar_artists WHERE id=?",
                            (row_id,)).fetchone()[0] == DZ


def test_the_one_time_repair_restores_or_clears_saved_links(cache, db):
    live = cache.cache_url_for(DZ)
    cache._flush_registrations()
    dead = "/api/image-cache/" + "1" * 64
    with db._get_connection() as conn:
        for name, url in (("A", live), ("B", dead), ("C", DZ)):
            conn.execute(
                "INSERT INTO similar_artists(source_artist_id, similar_artist_name, "
                "similarity_rank, occurrence_count, profile_id, image_url) "
                "VALUES ('src', ?, 1, 1, 1, ?)", (name, url))
        conn.execute("DROP TABLE IF EXISTS _similar_artist_images_durable")
        conn.commit()
        db._similar_artist_images_durable(conn.cursor())
        conn.commit()
        got = dict(conn.execute("SELECT similar_artist_name, image_url FROM similar_artists"))
    assert got == {"A": DZ, "B": None, "C": DZ}
