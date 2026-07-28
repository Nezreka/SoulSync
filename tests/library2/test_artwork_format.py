"""P2-04: Library-v2 artwork cache bytes, suffix and HTTP MIME agree."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from core.library2 import artwork


def _image_bytes(format_name: str, mode: str = "RGBA") -> bytes:
    image = Image.new(mode, (4, 3), (255, 0, 0, 128) if mode == "RGBA" else "red")
    output = BytesIO()
    image.save(output, format_name)
    return output.getvalue()


def _art_db(legacy_db):
    return SimpleNamespace(database_path=legacy_db.path)


def test_normalizes_png_with_alpha_to_real_jpeg():
    normalized = artwork._normalize_jpeg(_image_bytes("PNG"))

    assert normalized is not None
    assert normalized.startswith(b"\xff\xd8\xff")
    with Image.open(BytesIO(normalized)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"


def test_normalization_builds_full_and_thumbnail_from_one_decode():
    source = _image_bytes("PNG", "RGB")

    variants = artwork._normalize_jpeg_variants(source, thumb_height=2)

    assert variants is not None
    full, thumbnail = variants
    with Image.open(BytesIO(full)) as image:
        assert image.format == "JPEG"
        assert image.size == (4, 3)
    with Image.open(BytesIO(thumbnail)) as image:
        assert image.format == "JPEG"
        assert image.size == (2, 2)


def test_build_artwork_writes_only_valid_jpeg(
        imported_conn, legacy_db, monkeypatch):
    album_id = imported_conn.execute("SELECT id FROM lib2_albums LIMIT 1").fetchone()[0]
    monkeypatch.setattr(
        artwork, "_embedded_art_for_album", lambda *_args: _image_bytes("PNG")
    )
    database = _art_db(legacy_db)

    path = artwork.build_artwork(
        database, imported_conn, None, "album", album_id, force=True
    )

    assert path is not None
    cached = artwork.artwork_file(database, "album", album_id)
    assert cached.suffix == ".jpg"
    assert artwork.is_cached_jpeg(cached)
    with Image.open(cached) as image:
        assert image.format == "JPEG"


def test_invalid_provider_bytes_are_not_cached(
        imported_conn, legacy_db, monkeypatch):
    album_id = imported_conn.execute("SELECT id FROM lib2_albums LIMIT 1").fetchone()[0]
    monkeypatch.setattr(artwork, "_embedded_art_for_album", lambda *_args: b"not-image")
    monkeypatch.setattr(artwork, "_provider_art_url", lambda *_args, **_kwargs: None)
    database = _art_db(legacy_db)

    path = artwork.build_artwork(
        database, imported_conn, None, "album", album_id, force=True
    )

    assert path is None
    assert not artwork.artwork_file(database, "album", album_id).exists()


def test_old_png_named_jpg_is_rebuilt(imported_conn, legacy_db, monkeypatch):
    album_id = imported_conn.execute("SELECT id FROM lib2_albums LIMIT 1").fetchone()[0]
    database = _art_db(legacy_db)
    cached = artwork.artwork_file(database, "album", album_id)
    cached.write_bytes(_image_bytes("PNG"))
    monkeypatch.setattr(
        artwork, "_embedded_art_for_album", lambda *_args: _image_bytes("WEBP", "RGB")
    )

    path = artwork.build_artwork(database, imported_conn, None, "album", album_id)

    assert path == str(cached)
    assert artwork.is_cached_jpeg(cached)


def test_both_variants_take_the_extra_huffman_optimize_pass(monkeypatch):
    """rev25-13: the full variant is not list-view-only — the artist/album
    detail headers and the match dialogs all request it, on every visit, so
    the entropy pass earns back its one-time background-thread cost in bytes
    saved on every subsequent serve plus a smaller permanent cache footprint.

    Uses the shared ``monkeypatch`` fixture (not a manual save/restore of the
    class attribute) so the patch cannot survive a thread that's still
    mid-build from another test — ``Image.Image.save`` is a shared class
    attribute and the module keeps a process-global background executor."""
    source = _image_bytes("PNG", "RGB")
    saves: list[dict] = []
    real_save = Image.Image.save

    def recording_save(self, fp, *args, **kwargs):
        saves.append({"size": self.size, **kwargs})
        return real_save(self, fp, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", recording_save)

    assert artwork._normalize_jpeg_variants(source, thumb_height=2) is not None

    full, thumbnail = saves
    assert full["size"] == (4, 3)
    assert full.get("optimize") is True
    assert thumbnail["size"] == (2, 2)
    assert thumbnail.get("optimize") is True
