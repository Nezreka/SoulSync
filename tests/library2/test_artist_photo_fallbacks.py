"""An artist photo must depict THAT artist.

Two fallbacks were handing out pictures of somebody else:

* the album-cover fallback picked the first album the artist is credited on —
  for a guest that is the host's record, so 2 Chainz, DJ Snake and Dillon
  Francis all wore the cover of "Peace Is The Mission (Extended)";
* Last.fm's generic "no artist image" star was accepted as a real photo, so
  seven artists (40 Thevz, E-40, Kam, L.V., …) wore the same grey placeholder.
"""

from __future__ import annotations

from core.library2 import artwork
from core.metadata.artist_image import is_placeholder_artist_image

LASTFM_STAR = (
    "https://lastfm-img.freetls.fastly.net/i/u/300x300/"
    "2a96cbd8b46e442fc41c2b86b821562f.png"
)


def _artist(conn, name):
    return int(conn.execute(
        "INSERT INTO lib2_artists(name, name_key, sort_name) VALUES(?,?,?)",
        (name, name.lower(), name),
    ).lastrowid)


def test_lastfm_placeholder_star_is_not_an_artist_photo():
    assert is_placeholder_artist_image(LASTFM_STAR) is True
    assert is_placeholder_artist_image(LASTFM_STAR.replace(".png", ".webp")) is True
    assert is_placeholder_artist_image("https://i.scdn.co/image/ab676161000.jpg") is False
    assert is_placeholder_artist_image("") is False


def test_album_cover_fallback_only_uses_a_release_the_artist_fronts(
    imported_conn, monkeypatch, tmp_path,
):
    host = _artist(imported_conn, "Major Lazer")
    guest = _artist(imported_conn, "DJ Snake")
    album_id = int(imported_conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin) "
        "VALUES(?, 'Peace Is The Mission (Extended)', 'library')",
        (host,),
    ).lastrowid)
    for artist_id, role in ((host, "primary"), (guest, "featured")):
        imported_conn.execute(
            "INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,?,?)",
            (album_id, artist_id, role),
        )

    embedded_for = []
    monkeypatch.setattr(artwork, "_provider_art_url", lambda *a, **k: None)
    monkeypatch.setattr(
        artwork, "_embedded_art_for_album",
        lambda _conn, _cfg, aid: embedded_for.append(aid) or None,
    )

    class _DB:
        def _get_connection(self):  # pragma: no cover - unused here
            raise AssertionError
    db = _DB()
    monkeypatch.setattr(artwork, "artwork_dir", lambda _db: tmp_path)

    artwork._build_artwork_unlocked(db, imported_conn, None, "artist", guest)
    assert embedded_for == [], "a guest must not borrow the host's cover"

    artwork._build_artwork_unlocked(db, imported_conn, None, "artist", host)
    assert embedded_for == [album_id]


def test_stored_placeholder_photos_are_cleared_but_a_locked_one_is_kept(imported_conn):
    from core.library2 import native_enrich as NE

    starred = _artist(imported_conn, "40 Thevz")
    imported_conn.execute(
        "UPDATE lib2_artists SET image_url=? WHERE id=?", (LASTFM_STAR, starred))
    chosen = _artist(imported_conn, "Hand Picked")
    imported_conn.execute(
        "UPDATE lib2_artists SET image_url=?, art_locked=1 WHERE id=?",
        (LASTFM_STAR, chosen))
    real = _artist(imported_conn, "Has A Photo")
    imported_conn.execute(
        "UPDATE lib2_artists SET image_url='https://i.scdn.co/image/real.jpg' WHERE id=?",
        (real,))

    stats = NE.clear_placeholder_artist_images(imported_conn)

    assert stats["artist_ids"] == [starred]
    assert imported_conn.execute(
        "SELECT image_url FROM lib2_artists WHERE id=?", (starred,)
    ).fetchone()["image_url"] is None
    assert imported_conn.execute(
        "SELECT image_url FROM lib2_artists WHERE id=?", (chosen,)
    ).fetchone()["image_url"] == LASTFM_STAR
    assert imported_conn.execute(
        "SELECT image_url FROM lib2_artists WHERE id=?", (real,)
    ).fetchone()["image_url"] == "https://i.scdn.co/image/real.jpg"
