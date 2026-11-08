"""a library-started download's images lead with SoulSync's own relative
artwork link. the cover fetcher took that as "no art", so those downloads
wrote no cover.jpg and embedded none (third #1062 review G09-3)."""

from __future__ import annotations

from core.metadata import artwork
from database.music_database import MusicDatabase
from tests import lib2_seed


def _album(tmp_path, image_url):
    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = lib2_seed.row_conn(str(tmp_path / "m.db"))
    lib2_seed.track(conn, "Artist", "Album", "Song")
    album_id = conn.execute("SELECT id FROM lib2_albums WHERE title='Album'").fetchone()[0]
    conn.execute("UPDATE lib2_albums SET image_url=? WHERE id=?", (image_url, album_id))
    conn.commit()
    conn.close()
    return db, album_id


def test_the_library_link_fetches_the_releases_own_cdn_cover(tmp_path, monkeypatch):
    db, album_id = _album(tmp_path, "https://cdn-images.dzcdn.net/images/cover/abc/1000x1000.jpg")
    monkeypatch.setattr("database.music_database.get_database", lambda *_a, **_k: db)
    fetched = []
    real = artwork._fetch_art_bytes

    def fake(url):
        if url.startswith("http"):
            fetched.append(url)
            return b"jpeg-bytes", "image/jpeg"
        return real(url)
    monkeypatch.setattr(artwork, "_fetch_art_bytes", fake)

    assert real(f"/api/library/v2/artwork/album/{album_id}?v=3") == (b"jpeg-bytes", "image/jpeg")
    assert fetched and "dzcdn" in fetched[0]


def test_an_unknown_relative_path_is_still_no_art(tmp_path):
    assert artwork._fetch_art_bytes("/static/whatever.png") == (None, None)
