"""Pin the pure helpers in `core/library/artist_image.py`.

These back the new artist-image-to-disk feature added for issue
#572 (Navidrome can't show real artist photos because Navidrome has
no API for setting them — only reads `artist.jpg` from the artist
folder on disk).

Tests are intentionally fixture-driven (tmp_path) so they actually
exercise the filesystem code (atomic replace, overwrite guard,
missing folder), not just mock interactions.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# derive_artist_folder
# ---------------------------------------------------------------------------


class TestDeriveArtistFolder:
    def test_one_level_up_from_album(self):
        from core.library.artist_image import derive_artist_folder
        # POSIX path
        assert derive_artist_folder("/music/Drake/Views") == "/music/Drake"

    def test_handles_trailing_slash(self):
        """Caller might pass an album folder with a trailing slash.
        Without trimming, `os.path.dirname` returns the input unchanged
        — silently breaks the up-one-level contract."""
        from core.library.artist_image import derive_artist_folder
        assert derive_artist_folder("/music/Drake/Views/") == "/music/Drake"

    def test_empty_string_returns_empty(self):
        from core.library.artist_image import derive_artist_folder
        assert derive_artist_folder("") == ""

    def test_none_returns_empty(self):
        from core.library.artist_image import derive_artist_folder
        assert derive_artist_folder(None) == ""

    def test_non_string_returns_empty(self):
        """Defensive — caller might hand us a Path object or similar.
        Currently we require str; return empty rather than raise."""
        from core.library.artist_image import derive_artist_folder
        assert derive_artist_folder(42) == ""


# ---------------------------------------------------------------------------
# pick_artist_image_url
# ---------------------------------------------------------------------------


class TestPickArtistImageUrl:
    def test_returns_image_url_when_set(self):
        from core.library.artist_image import pick_artist_image_url
        artist = SimpleNamespace(image_url="https://example.com/drake.jpg")
        assert pick_artist_image_url(artist) == "https://example.com/drake.jpg"

    def test_returns_none_when_empty_string(self):
        from core.library.artist_image import pick_artist_image_url
        assert pick_artist_image_url(SimpleNamespace(image_url="")) is None

    def test_returns_none_when_attribute_missing(self):
        from core.library.artist_image import pick_artist_image_url
        assert pick_artist_image_url(SimpleNamespace()) is None

    def test_returns_none_when_artist_is_none(self):
        from core.library.artist_image import pick_artist_image_url
        assert pick_artist_image_url(None) is None

    def test_strips_whitespace(self):
        from core.library.artist_image import pick_artist_image_url
        artist = SimpleNamespace(image_url="  https://example.com/drake.jpg  ")
        assert pick_artist_image_url(artist) == "https://example.com/drake.jpg"

    def test_returns_none_when_non_string(self):
        from core.library.artist_image import pick_artist_image_url
        # int / list / dict would all hit the `isinstance(..., str)` guard
        assert pick_artist_image_url(SimpleNamespace(image_url=42)) is None
        assert pick_artist_image_url(SimpleNamespace(image_url=["url"])) is None


# ---------------------------------------------------------------------------
# download_image_bytes
# ---------------------------------------------------------------------------


def _fake_response(status_code=200, content_type="image/jpeg", body=b"\x89PNG..."):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"Content-Type": content_type}
    resp.content = body
    return resp


class TestDownloadImageBytes:
    def test_returns_bytes_on_success(self):
        from core.library import artist_image as ai
        fake = _fake_response(body=b"image-data-here")
        with patch.object(ai, "requests") as r:
            r.get.return_value = fake
            result = ai.download_image_bytes("https://example.com/x.jpg")
        assert result == b"image-data-here"

    def test_returns_none_on_404(self):
        from core.library import artist_image as ai
        fake = _fake_response(status_code=404)
        with patch.object(ai, "requests") as r:
            r.get.return_value = fake
            assert ai.download_image_bytes("https://example.com/x.jpg") is None

    def test_returns_none_on_non_image_content_type(self):
        """Defensive: if a URL returns HTML or JSON (e.g. an error page),
        don't try to write it as artist.jpg."""
        from core.library import artist_image as ai
        fake = _fake_response(content_type="text/html")
        with patch.object(ai, "requests") as r:
            r.get.return_value = fake
            assert ai.download_image_bytes("https://example.com/x.jpg") is None

    def test_returns_none_on_empty_body(self):
        from core.library import artist_image as ai
        fake = _fake_response(body=b"")
        with patch.object(ai, "requests") as r:
            r.get.return_value = fake
            assert ai.download_image_bytes("https://example.com/x.jpg") is None

    def test_returns_none_on_exception(self):
        """Network timeout / DNS failure / etc shouldn't raise to
        the caller — caller just sees None and surfaces a generic
        'image fetch failed' error to the user."""
        from core.library import artist_image as ai
        with patch.object(ai, "requests") as r:
            r.get.side_effect = RuntimeError("network down")
            assert ai.download_image_bytes("https://example.com/x.jpg") is None

    def test_returns_none_for_empty_url(self):
        from core.library.artist_image import download_image_bytes
        assert download_image_bytes("") is None
        assert download_image_bytes(None) is None


# ---------------------------------------------------------------------------
# write_artist_jpg
# ---------------------------------------------------------------------------


class TestWriteArtistJpg:
    def test_writes_file_on_success(self, tmp_path):
        from core.library.artist_image import write_artist_jpg
        success, path = write_artist_jpg(str(tmp_path), b"image-bytes")
        assert success is True
        assert os.path.exists(path)
        assert open(path, "rb").read() == b"image-bytes"

    def test_returns_failure_when_folder_missing(self, tmp_path):
        from core.library.artist_image import write_artist_jpg
        missing = str(tmp_path / "does-not-exist")
        success, reason = write_artist_jpg(missing, b"image-bytes")
        assert success is False
        assert "does not exist" in reason

    def test_returns_failure_for_empty_bytes(self, tmp_path):
        from core.library.artist_image import write_artist_jpg
        success, reason = write_artist_jpg(str(tmp_path), b"")
        assert success is False
        assert "image bytes" in reason

    def test_returns_failure_for_empty_folder(self):
        from core.library.artist_image import write_artist_jpg
        success, reason = write_artist_jpg("", b"image-bytes")
        assert success is False
        assert "folder" in reason

    def test_respects_existing_file_without_overwrite(self, tmp_path):
        """Default overwrite=False protects user-supplied artist.jpg
        from being clobbered by a programmatic update. User must opt
        in to overwrite."""
        from core.library.artist_image import write_artist_jpg
        target = tmp_path / "artist.jpg"
        target.write_bytes(b"user-supplied")

        success, reason = write_artist_jpg(str(tmp_path), b"new-bytes")
        assert success is False
        assert "already exists" in reason
        # Existing file must be untouched.
        assert target.read_bytes() == b"user-supplied"

    def test_overwrites_when_flag_set(self, tmp_path):
        from core.library.artist_image import write_artist_jpg
        target = tmp_path / "artist.jpg"
        target.write_bytes(b"old-bytes")

        success, path = write_artist_jpg(str(tmp_path), b"new-bytes", overwrite=True)
        assert success is True
        assert target.read_bytes() == b"new-bytes"

    def test_atomic_write_no_temp_left_on_success(self, tmp_path):
        """`.tmp` artifact must be cleaned up by `os.replace`. Don't
        leave litter behind for the next backup / sync run to puzzle
        over."""
        from core.library.artist_image import write_artist_jpg
        success, _ = write_artist_jpg(str(tmp_path), b"image-bytes")
        assert success is True
        assert not (tmp_path / "artist.jpg.tmp").exists()

    def test_atomic_write_cleans_temp_on_failure(self, tmp_path, monkeypatch):
        """If `os.replace` fails (permission, cross-device, etc),
        the helper should remove the temp file rather than leaving
        a half-written `.tmp` on disk."""
        from core.library import artist_image as ai

        def _failing_replace(src, dst):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(os, "replace", _failing_replace)
        success, reason = ai.write_artist_jpg(str(tmp_path), b"image-bytes")
        assert success is False
        assert "write failed" in reason
        # Temp must not be left behind
        assert not (tmp_path / "artist.jpg.tmp").exists()
