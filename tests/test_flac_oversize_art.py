"""A cover too big for a FLAC picture block must not fail the whole tag write.

From Boulder's log, Aug 2026, on `1-09 capaz (merengueton).flac`:

    Album art successfully embedded.
    [Atomic Save] atomic path failed (block is too long to write) — in-place fallback
    Error enhancing metadata ...: block is too long to write

A FLAC METADATA_BLOCK carries its length in 24 bits, so nothing over 16MB can
ever be written. mutagen only discovers that at save time, so the art was
reported as embedded, the atomic save failed, and the fallback then ran the
SAME impossible write against the real file — a second identical traceback, and
the track ended up with neither art nor tags.

Two fixes, pinned here: oversized art is re-encoded (or dropped) before it can
poison the save, and a tag-encoding failure no longer falls back to writing in
place.
"""

from __future__ import annotations

import io as _io

import pytest

from core.metadata import artwork
from core.metadata.common import _is_tag_write_error


def _png(side: int) -> bytes:
    from PIL import Image
    buf = _io.BytesIO()
    Image.new("RGB", (side, side), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


class TestTheBlockCeiling:
    def test_the_cap_is_under_what_24_bits_can_express(self):
        assert artwork.FLAC_MAX_PICTURE_BYTES < (1 << 24)

    def test_a_huge_cover_is_shrunk_to_fit(self):
        # Incompressible noise, so it is genuinely oversized rather than a PNG
        # that happens to deflate well.
        from PIL import Image
        import os
        buf = _io.BytesIO()
        Image.frombytes("RGB", (3000, 3000), os.urandom(3000 * 3000 * 3)).save(buf, format="PNG")
        raw = buf.getvalue()
        assert len(raw) > artwork.FLAC_MAX_PICTURE_BYTES, "fixture is not actually oversized"

        out = artwork._shrink_for_flac(raw, "image/png")
        assert out is not None
        assert len(out) <= artwork.FLAC_MAX_PICTURE_BYTES

    def test_a_normal_cover_survives_the_helper_intact_enough(self):
        out = artwork._shrink_for_flac(_png(600), "image/png")
        assert out is not None and len(out) <= artwork.FLAC_MAX_PICTURE_BYTES

    def test_unreadable_bytes_give_up_rather_than_raising(self):
        assert artwork._shrink_for_flac(b"not an image at all", "image/png") is None

    def test_no_pillow_gives_up_rather_than_raising(self, monkeypatch):
        # sys.modules rather than patching __import__: the latter is global for
        # the whole test and pytest's own machinery imports things too.
        import sys
        data = _png(100)                     # build it BEFORE hiding Pillow
        monkeypatch.setitem(sys.modules, "PIL", None)
        monkeypatch.setitem(sys.modules, "PIL.Image", None)
        assert artwork._shrink_for_flac(data, "image/png") is None


class TestTheFallbackStopsRetryingImpossibleWrites:
    def test_a_mutagen_error_is_a_tag_write_error(self):
        import mutagen
        assert _is_tag_write_error(mutagen.MutagenError("block is too long to write"))

    def test_the_real_flac_error_is_recognised(self):
        from mutagen.flac import error as flac_error
        assert _is_tag_write_error(flac_error("block is too long to write"))

    def test_a_disk_problem_is_NOT_a_tag_write_error(self):
        """That is exactly what the in-place fallback exists for."""
        assert not _is_tag_write_error(OSError("No space left on device"))
        assert not _is_tag_write_error(PermissionError("denied"))

    def test_an_unrelated_failure_is_NOT_a_tag_write_error(self):
        assert not _is_tag_write_error(ValueError("something else entirely"))


class TestWiring:
    """The call sites, not the helpers."""

    def test_save_audio_file_bails_out_on_a_tag_write_error(self):
        import ast
        with open("core/metadata/common.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "save_audio_file")
        called = {n.func.id for n in ast.walk(fn)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_is_tag_write_error" in called

    def test_the_embed_path_checks_the_size(self):
        import ast
        with open("core/metadata/artwork.py", encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "FLAC_MAX_PICTURE_BYTES" in names
        assert "_shrink_for_flac" in names
