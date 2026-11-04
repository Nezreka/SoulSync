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


DEEZER_BLANK = (
    "https://cdn-images.dzcdn.net/images/artist//1000x1000-000000-80-0-0.jpg"
)
DEEZER_REAL = (
    "https://cdn-images.dzcdn.net/images/artist/"
    "e8a10a0d4addf3eff1d8295d9a066c28/1000x1000-000000-80-0-0.jpg"
)


def test_deezer_blank_avatar_is_recognised_by_its_empty_asset_hash():
    """Deezer answers an artist with no photo with a URL whose asset hash is
    simply MISSING — `/images/artist//…` — and serves a grey silhouette for it.
    Verified live: api.deezer.com/artist/5541359 (40 Thevz) returns exactly this
    as `picture_xl`, which is how that silhouette became his portrait again the
    moment the Last.fm star was cleared.
    """
    assert is_placeholder_artist_image(DEEZER_BLANK) is True
    assert is_placeholder_artist_image(DEEZER_REAL) is False
    # The same shape for the other entities Deezer serves this way.
    assert is_placeholder_artist_image(
        "https://cdn-images.dzcdn.net/images/cover//500x500-000000-80-0-0.jpg"
    ) is True


def test_a_portrait_borrowed_from_a_foreign_album_cover_is_dropped(
    imported_conn, tmp_path,
):
    """Restricting the fallback does not un-cache what it already produced.

    2 Chainz's cached portrait stayed byte-identical to the cover of "Peace Is
    The Mission (Extended)" — a release he guests on — because nothing in the
    repair pass had a reason to touch his row: his provider id is his own.
    """
    host = _artist(imported_conn, "Major Lazer")
    guest = _artist(imported_conn, "2 Chainz")
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

    class _DB:
        database_path = ":memory:"
    db = _DB()
    cover = b"\xff\xd8cover-bytes"
    for name in (f"album_{album_id}.jpg", f"artist_{host}.jpg", f"artist_{guest}.jpg"):
        (tmp_path / name).write_bytes(cover)
    (tmp_path / f"artist_{guest}_t.jpg").write_bytes(cover)
    import core.library2.artwork as A
    original_dir = A.artwork_dir
    A.artwork_dir = lambda _db: tmp_path
    try:
        dropped = A.drop_borrowed_album_cover_portraits(db, imported_conn)
    finally:
        A.artwork_dir = original_dir

    assert dropped == [guest]
    assert not (tmp_path / f"artist_{guest}.jpg").exists()
    # The host fronts the release — a cover is a weak portrait but it is HIS.
    assert (tmp_path / f"artist_{host}.jpg").exists()


DEEZER_SILHOUETTE = (
    "https://cdn-images.dzcdn.net/images/artist/"
    "e23066163f21176a822b54001ee648a2/1000x1000-000000-80-0-0.jpg"
)


def test_deezers_silhouette_under_its_own_hash_is_a_placeholder():
    """the grey silhouette also comes under real-looking asset ids, shared by
    dozens of unrelated artists; it showed on the library grid as their photo"""
    assert is_placeholder_artist_image(DEEZER_SILHOUETTE) is True
    assert is_placeholder_artist_image(DEEZER_SILHOUETTE.replace(
        "e23066163f21176a822b54001ee648a2", "ebcf22e5c3a0eb3178b8a6f6bc1a5a6c")) is True
    assert is_placeholder_artist_image(DEEZER_REAL) is False


def test_a_stored_silhouette_is_no_stored_photo(imported_conn):
    artist = _artist(imported_conn, "Faceless")
    imported_conn.execute(
        "UPDATE lib2_artists SET image_url=? WHERE id=?", (DEEZER_SILHOUETTE, artist))

    assert artwork._stored_image(imported_conn, "artist", artist) is None


def test_the_first_build_drops_cached_silhouettes_once(imported_conn, monkeypatch, tmp_path):
    from contextlib import contextmanager

    faceless = _artist(imported_conn, "Faceless")
    imported_conn.execute(
        "UPDATE lib2_artists SET image_url=? WHERE id=?", (DEEZER_SILHOUETTE, faceless))
    imported_conn.commit()
    monkeypatch.setattr(artwork, "artwork_dir", lambda _db: tmp_path)

    class _DB:
        database_path = str(tmp_path / "x.db")

        def _get_connection(self):
            return _Shared(imported_conn)

    class _Shared:
        def __init__(self, c):
            self.c = c

        def __getattr__(self, k):
            return getattr(self.c, k)

        def close(self):
            pass

    db = _DB()
    cached = artwork.artwork_file(db, "artist", faceless)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"silhouette")
    artwork._placeholder_sweep_done.clear()

    artwork._forget_placeholder_artist_art_once(db, imported_conn)

    assert not cached.exists()
    assert imported_conn.execute(
        "SELECT image_url FROM lib2_artists WHERE id=?", (faceless,)).fetchone()["image_url"] is None
    cached.write_bytes(b"rebuilt")
    artwork._forget_placeholder_artist_art_once(db, imported_conn)
    assert cached.exists(), "once per process: a rebuilt picture is left alone"


def test_a_picture_three_unrelated_artists_share_is_rebuilt(imported_conn, monkeypatch, tmp_path):
    """a server's 'no picture' image cached before a real photo was stored"""
    monkeypatch.setattr(artwork, "artwork_dir", lambda _db: tmp_path)

    class _DB:
        database_path = str(tmp_path / "x.db")

    ids = [_artist(imported_conn, f"Faceless {n}") for n in range(3)]
    own = _artist(imported_conn, "Has Its Own")
    picked = _artist(imported_conn, "Hand Picked")
    imported_conn.execute("UPDATE lib2_artists SET art_locked=1 WHERE id=?", (picked,))
    for artist_id in ids + [picked]:
        artwork.thumb_file(_DB(), "artist", artist_id).write_bytes(b"server silhouette")
    artwork.thumb_file(_DB(), "artist", own).write_bytes(b"a real photo")

    shared = artwork._shared_artist_pictures(_DB(), imported_conn)

    assert sorted(shared) == sorted(ids)


def test_two_artists_sharing_a_picture_are_left_alone(imported_conn, monkeypatch, tmp_path):
    monkeypatch.setattr(artwork, "artwork_dir", lambda _db: tmp_path)

    class _DB:
        database_path = str(tmp_path / "x.db")

    for name in ("Duo", "Duo (alias)"):
        artwork.thumb_file(_DB(), "artist", _artist(imported_conn, name)).write_bytes(b"same photo")

    assert artwork._shared_artist_pictures(_DB(), imported_conn) == []
