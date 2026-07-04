"""Overlay compositor — render templates onto poster art with real Pillow.

Covers the field formatters (which must mirror the editor) and the compositor:
that it returns a valid same-size JPEG, that layers actually paint pixels, that a
bound badge with no value is skipped, and that images load through the injected
loader (no network in tests).
"""

from __future__ import annotations

import io

import pytest

from PIL import Image

from core.video.overlays import fields
from core.video.overlays.compositor import render_overlay


# ── field formatters (parity with the editor) ─────────────────────────────────
def test_formatters_mirror_editor():
    assert fields.format_field("resolution", "2160p") == "4K"
    assert fields.format_field("resolution", "1080p") == "1080p"
    assert fields.format_field("video_codec", "hevc") == "HEVC"
    assert fields.format_field("audio_codec", "truehd") == "TrueHD"
    assert fields.format_field("source", "web-dl") == "WEB-DL"
    assert fields.format_field("imdb", 8.36) == "IMDb 8.4"
    assert fields.format_field("rt", 92) == "RT 92%"
    assert fields.format_field("status", "continuing") == "Returning"
    assert fields.format_field("runtime", 148) == "2h 28m"
    assert fields.format_field("season_count", 1) == "1 Season"
    assert fields.format_field("season_count", 4) == "4 Seasons"


def test_formatter_none_when_no_value():
    assert fields.format_field("resolution", None) is None
    assert fields.format_field("imdb", None) is None
    assert fields.format_field("hdr", "") is None
    assert fields.format_field("unknown_field", "x") is None


# ── compositor ────────────────────────────────────────────────────────────────
def _poster(color=(20, 20, 30), size=(600, 900)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _open(b):
    return Image.open(io.BytesIO(b)).convert("RGB")


def test_render_returns_same_size_jpeg():
    base = _poster()
    out = render_overlay(base, {"layers": []}, {})
    img = _open(out)
    assert img.size == (600, 900) and img.format is None  # decoded; format check below
    # a valid JPEG round-trips
    assert Image.open(io.BytesIO(out)).format == "JPEG"


def test_text_layer_paints_pixels():
    base = _poster(color=(0, 0, 0))
    definition = {"layers": [{
        "type": "text", "text": "HELLO", "anchor": "center", "x": 0.5, "y": 0.5,
        "size": 0.1, "color": "#ffffff", "font": "Inter", "weight": 800, "opacity": 1,
    }]}
    out = render_overlay(base, definition, {})
    img = _open(out)
    # centre region should now contain near-white pixels from the text
    crop = img.crop((150, 400, 450, 500))
    assert max(crop.getdata(), key=lambda p: sum(p))[0] > 200


def test_bound_badge_with_no_value_is_skipped():
    base = _poster(color=(0, 0, 0))
    definition = {"layers": [{
        "type": "text", "binding": {"field": "resolution"}, "anchor": "center",
        "x": 0.5, "y": 0.5, "size": 0.1, "color": "#ffffff",
        "bg": {"enabled": True, "color": "#ffffff", "opacity": 1, "radius": 0.02, "padX": 0.03, "padY": 0.02},
    }]}
    # no 'resolution' in values → badge skipped → image stays black
    out = render_overlay(base, definition, {})
    img = _open(out)
    assert max(sum(p) for p in img.getdata()) < 30      # essentially untouched black
    # with a value → the pill paints white
    out2 = render_overlay(base, definition, {"resolution": "2160p"})
    assert max(sum(p) for p in _open(out2).getdata()) > 600


def test_shape_layer_solid_fill():
    base = _poster(color=(0, 0, 0))
    definition = {"layers": [{
        "type": "shape", "anchor": "top-left", "x": 0, "y": 0, "w": 1, "h": 0.5, "radius": 0, "opacity": 1,
        "fill": {"grad": False, "c1": "#ff0000", "a1": 1},
    }]}
    img = _open(render_overlay(base, definition, {}))
    assert img.getpixel((300, 100))[0] > 200            # top half is red
    assert sum(img.getpixel((300, 800))) < 30           # bottom half untouched


def test_image_layer_uses_injected_loader():
    base = _poster(color=(0, 0, 0))
    logo = io.BytesIO()
    Image.new("RGBA", (100, 40), (0, 255, 0, 255)).save(logo, format="PNG")
    calls = []

    def loader(url):
        calls.append(url)
        return logo.getvalue()

    definition = {"layers": [{
        "type": "image", "logo": True, "anchor": "center", "x": 0.5, "y": 0.5, "w": 0.5, "opacity": 1,
    }]}
    img = _open(render_overlay(base, definition, {"logo_url": "http://x/logo.png"}, image_loader=loader))
    assert calls == ["http://x/logo.png"]
    assert img.getpixel((300, 450))[1] > 200            # green logo painted centre


def test_hidden_layer_not_rendered():
    base = _poster(color=(0, 0, 0))
    definition = {"layers": [{
        "type": "shape", "hidden": True, "anchor": "top-left", "x": 0, "y": 0, "w": 1, "h": 1,
        "fill": {"grad": False, "c1": "#ffffff", "a1": 1},
    }]}
    assert max(sum(p) for p in _open(render_overlay(base, definition, {})).getdata()) < 30


def test_rotation_changes_the_render_and_stays_valid():
    base = _poster(color=(0, 0, 0))
    layer = {"type": "shape", "anchor": "center", "x": 0.5, "y": 0.5, "w": 0.6, "h": 0.1, "radius": 0,
             "opacity": 1, "fill": {"grad": False, "c1": "#ff0000", "a1": 1}}
    flat = render_overlay(base, {"layers": [dict(layer, rotation=0)]}, {})
    tilted = render_overlay(base, {"layers": [dict(layer, rotation=45)]}, {})
    assert Image.open(io.BytesIO(tilted)).format == "JPEG"
    assert flat != tilted                                    # rotation actually changed the pixels
    # a flat bar spans y≈405–495 at the centre column; the 45°-tilted bar reaches
    # higher. at (300, 400) the tilted bar is red but the flat one is untouched.
    assert _open(tilted).getpixel((300, 400))[0] > 150
    assert sum(_open(flat).getpixel((300, 400))) < 30


def test_broken_layer_does_not_sink_the_render():
    base = _poster()
    # a garbage layer shouldn't crash the whole composite
    out = render_overlay(base, {"layers": [{"type": "text"}, None, {"type": "shape", "w": "oops"}]}, {})
    assert Image.open(io.BytesIO(out)).format == "JPEG"
