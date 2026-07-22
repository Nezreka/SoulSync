"""Manual cover-art override (docs §49 art picker): a user-picked cover must
win over the auto-resolved embedded/provider image, and must survive a later
force-refresh (e.g. "Refresh & Scan") rather than being silently clobbered —
mirrors the legacy picker's "a manual pick pins the choice" guarantee, but via
the existing lib2_metadata_overrides store instead of a parallel pin flag.
"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from core.library2 import artwork
from core.library2.metadata_overrides import get_field_overrides, set_field_override


def _image_bytes(color=(255, 0, 0)) -> bytes:
    image = Image.new("RGB", (4, 3), color)
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _art_db(legacy_db):
    return SimpleNamespace(database_path=legacy_db.path)


@pytest.fixture
def album_id(imported_conn):
    return imported_conn.execute("SELECT id FROM lib2_albums LIMIT 1").fetchone()[0]


@pytest.fixture
def artist_id(imported_conn):
    return imported_conn.execute("SELECT id FROM lib2_artists LIMIT 1").fetchone()[0]


def test_manual_override_wins_over_embedded_art(imported_conn, legacy_db, monkeypatch, album_id):
    set_field_override(
        imported_conn, entity_type="release_group", entity_id=album_id,
        field_name="image_url", value="https://example.com/manual.jpg",
    )
    imported_conn.commit()

    override_bytes = _image_bytes((10, 20, 30))
    embedded_bytes = _image_bytes((200, 100, 50))
    monkeypatch.setattr(
        "core.library.artist_image.download_image_bytes",
        lambda url: override_bytes if url == "https://example.com/manual.jpg" else None,
    )
    monkeypatch.setattr(artwork, "_embedded_art_for_album", lambda *_args: embedded_bytes)

    database = _art_db(legacy_db)
    path = artwork.build_artwork(database, imported_conn, None, "album", album_id, force=True)

    assert path is not None
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == pytest.approx((10, 20, 30), abs=2)


def test_no_override_falls_back_to_embedded(imported_conn, legacy_db, monkeypatch, album_id):
    embedded_bytes = _image_bytes((5, 6, 7))
    monkeypatch.setattr(artwork, "_embedded_art_for_album", lambda *_args: embedded_bytes)

    database = _art_db(legacy_db)
    path = artwork.build_artwork(database, imported_conn, None, "album", album_id, force=True)

    assert path is not None
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == pytest.approx((5, 6, 7), abs=2)


def test_artist_prefers_provider_photo_over_embedded_album_art(
    imported_conn, legacy_db, monkeypatch, artist_id,
):
    provider_bytes = _image_bytes((11, 22, 33))
    embedded_bytes = _image_bytes((200, 100, 50))
    monkeypatch.setattr(
        artwork, "_provider_art_url", lambda *_args: "https://example.com/artist.jpg"
    )
    monkeypatch.setattr(
        "core.library.artist_image.download_image_bytes", lambda _url: provider_bytes
    )
    monkeypatch.setattr(artwork, "_embedded_art_for_album", lambda *_args: embedded_bytes)

    path = artwork.build_artwork(
        _art_db(legacy_db), imported_conn, None, "artist", artist_id, force=True
    )

    assert path is not None
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == pytest.approx((11, 22, 33), abs=2)


def test_artist_falls_back_to_embedded_art_when_provider_has_no_photo(
    imported_conn, legacy_db, monkeypatch, artist_id,
):
    embedded_bytes = _image_bytes((44, 55, 66))
    monkeypatch.setattr(artwork, "_provider_art_url", lambda *_args: None)
    monkeypatch.setattr(artwork, "_embedded_art_for_album", lambda *_args: embedded_bytes)

    path = artwork.build_artwork(
        _art_db(legacy_db), imported_conn, None, "artist", artist_id, force=True
    )

    assert path is not None
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == pytest.approx((44, 55, 66), abs=2)


def test_apply_manual_artwork_downloads_validates_and_caches(
    imported_conn, legacy_db, monkeypatch, album_id,
):
    chosen_bytes = _image_bytes((1, 2, 3))
    monkeypatch.setattr(
        "core.library.artist_image.download_image_bytes",
        lambda url: chosen_bytes if url == "https://example.com/cover.jpg" else None,
    )
    database = _art_db(legacy_db)

    ok = artwork.apply_manual_artwork(
        database, imported_conn, "album", album_id, "https://example.com/cover.jpg",
    )
    assert ok is True

    overrides = get_field_overrides(imported_conn, entity_type="release_group", entity_id=album_id)
    assert overrides["image_url"].value == "https://example.com/cover.jpg"

    cached = artwork.artwork_file(database, "album", album_id)
    assert artwork.is_cached_jpeg(cached)
    with Image.open(cached) as image:
        assert image.getpixel((0, 0)) == pytest.approx((1, 2, 3), abs=2)


def test_apply_manual_artwork_returns_false_for_unreachable_url(
    imported_conn, legacy_db, monkeypatch, album_id,
):
    monkeypatch.setattr("core.library.artist_image.download_image_bytes", lambda url: None)
    database = _art_db(legacy_db)

    ok = artwork.apply_manual_artwork(
        database, imported_conn, "album", album_id, "https://example.com/dead.jpg",
    )
    assert ok is False
    overrides = get_field_overrides(imported_conn, entity_type="release_group", entity_id=album_id)
    assert "image_url" not in overrides


def test_apply_manual_artwork_returns_false_for_invalid_image_bytes(
    imported_conn, legacy_db, monkeypatch, album_id,
):
    monkeypatch.setattr(
        "core.library.artist_image.download_image_bytes", lambda url: b"not-an-image",
    )
    database = _art_db(legacy_db)

    ok = artwork.apply_manual_artwork(
        database, imported_conn, "album", album_id, "https://example.com/garbage.jpg",
    )
    assert ok is False
    overrides = get_field_overrides(imported_conn, entity_type="release_group", entity_id=album_id)
    assert "image_url" not in overrides


def test_force_refresh_does_not_clobber_a_manual_pick(imported_conn, legacy_db, monkeypatch, album_id):
    """The whole point of storing the pick as an override (not just writing
    the cache file once): a later force-refresh (Refresh & Scan / precache)
    must still prefer it over the auto-resolved embedded art."""
    picked_bytes = _image_bytes((9, 9, 9))
    embedded_bytes = _image_bytes((200, 200, 200))
    monkeypatch.setattr(
        "core.library.artist_image.download_image_bytes",
        lambda url: picked_bytes if url == "https://example.com/pick.jpg" else None,
    )
    database = _art_db(legacy_db)
    assert artwork.apply_manual_artwork(
        database, imported_conn, "album", album_id, "https://example.com/pick.jpg",
    )

    # Simulate a later "Refresh & Scan" / precache force-rebuild — a real
    # embedded cover is now available (e.g. a re-scan found the file), but the
    # user's manual pick must still win.
    monkeypatch.setattr(artwork, "_embedded_art_for_album", lambda *_args: embedded_bytes)
    path = artwork.build_artwork(database, imported_conn, None, "album", album_id, force=True)

    assert path is not None
    with Image.open(path) as image:
        assert image.getpixel((0, 0)) == pytest.approx((9, 9, 9), abs=2)
