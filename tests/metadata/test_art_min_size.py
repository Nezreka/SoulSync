"""Tests for the minimum-resolution guard on preferred cover art.

A source's art is only accepted when its shortest side meets the threshold, so
a low-res cover (e.g. a small Cover Art Archive upload) is skipped and the
resolver falls through to the next source instead of winning on priority alone.
Reproduces the two real cases that motivated it: Taylor Swift 599x531 and
Kendrick GNX 600x600 — both rejected at the default 1000px floor.
"""

from __future__ import annotations

from unittest.mock import patch

from core.metadata import artwork


def _run(min_px, fetch_return, dims):
    with patch.object(artwork, "_fetch_art_bytes", return_value=fetch_return), \
         patch.object(artwork, "get_image_dimensions", return_value=dims):
        validate, cache = artwork._min_size_art_validator(min_px)
        return validate("deezer", "http://x/cover.jpg"), cache


def test_rejects_small_square_art():
    ok, cache = _run(1000, (b"img", "image/jpeg"), (600, 600))  # GNX case
    assert ok is False
    # Bytes are cached even on reject (so a later accepted source reuses fetches).
    assert cache["http://x/cover.jpg"] == (b"img", "image/jpeg")


def test_rejects_small_non_square_using_shortest_side():
    ok, _ = _run(1000, (b"img", "image/jpeg"), (599, 531))  # Taylor case
    assert ok is False


def test_accepts_big_art():
    ok, cache = _run(1000, (b"img", "image/jpeg"), (1900, 1900))
    assert ok is True
    assert cache["http://x/cover.jpg"] == (b"img", "image/jpeg")


def test_accepts_unmeasurable_art_to_avoid_over_rejecting():
    ok, _ = _run(1000, (b"img", "image/jpeg"), None)
    assert ok is True


def test_rejects_when_fetch_returns_no_bytes():
    ok, _ = _run(1000, (None, None), (4000, 4000))
    assert ok is False


def test_zero_threshold_disables_the_gate():
    ok, _ = _run(0, (b"img", "image/jpeg"), (10, 10))
    assert ok is True
