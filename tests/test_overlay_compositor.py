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
    assert fields.format_field("genre", "Sci-Fi") == "Sci-Fi"
    assert fields.format_field("genre", "") is None


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


def test_shape_ellipse_masks_corners():
    """An ellipse shape fills its centre but leaves the tile corners transparent."""
    base = _poster(color=(0, 0, 0), size=(600, 900))
    layer = {"type": "shape", "shapeKind": "ellipse", "anchor": "center", "x": 0.5, "y": 0.5,
             "w": 0.6, "h": 0.4, "opacity": 1, "fill": {"grad": False, "c1": "#ff0000", "a1": 1}}
    img = _open(render_overlay(base, {"layers": [layer]}, {}))
    assert img.getpixel((300, 450))[0] > 200          # centre is red
    # top-left corner of the ellipse's bounding box is outside the ellipse → still black
    assert sum(img.getpixel((int(0.5 * 600 - 0.28 * 600), int(0.5 * 900 - 0.19 * 900)))) < 40


def test_shape_line_is_thin():
    from core.video.overlays.compositor import _shape_tile
    line = _shape_tile({"type": "shape", "shapeKind": "line", "w": 0.5, "thickness": 0.006,
                        "fill": {"grad": False, "c1": "#fff", "a1": 1}}, 600, 900)
    assert line.size[1] < line.size[0]                 # a line is much wider than tall


def test_shape_border_paints_outline():
    base = _poster(color=(0, 0, 0), size=(600, 900))
    layer = {"type": "shape", "shapeKind": "rect", "anchor": "center", "x": 0.5, "y": 0.5,
             "w": 0.5, "h": 0.3, "radius": 0, "opacity": 1,
             "fill": {"grad": False, "c1": "#000000", "a1": 1},   # black fill on black poster
             "border": {"enabled": True, "color": "#00ff00", "w": 0.01}}
    img = _open(render_overlay(base, {"layers": [layer]}, {}))
    greens = [p for p in img.getdata() if p[1] > 150 and p[0] < 90 and p[2] < 90]
    assert len(greens) > 50                             # the green border painted


def test_image_grayscale_and_radius(tmp_path):
    from core.video.overlays.compositor import _image_tile
    logo = io.BytesIO()
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(logo, format="PNG")
    load = lambda url: logo.getvalue()  # noqa: E731
    # grayscale: a pure-red image becomes gray (r == g == b)
    g = _image_tile({"type": "image", "src": "x", "w": 0.4, "grayscale": True}, 600, 900, {}, load)
    px = g.getpixel((g.size[0] // 2, g.size[1] // 2))
    assert px[0] == px[1] == px[2]
    # corner radius punches the corners transparent
    rimg = _image_tile({"type": "image", "src": "x", "w": 0.4, "radius": 0.4}, 600, 900, {}, load)
    assert rimg.getpixel((0, 0))[3] == 0          # top-left corner cut out
    assert rimg.getpixel((rimg.size[0] // 2, rimg.size[1] // 2))[3] > 0   # centre solid


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


def test_template_thumbnail_renders_valid_jpeg_with_sample_badges():
    from core.video.overlays.compositor import render_template_thumbnail
    definition = {"layers": [{"type": "text", "binding": {"field": "resolution"}, "anchor": "top-right",
                              "x": 0.95, "y": 0.05, "size": 0.06, "color": "#ffffff",
                              "bg": {"enabled": True, "color": "#000000", "opacity": 1, "radius": 0.02, "padX": 0.03, "padY": 0.02}}]}
    data = render_template_thumbnail(definition, size=(200, 300))
    img = Image.open(io.BytesIO(data))
    assert img.format == "JPEG" and img.size == (200, 300)
    # the sample resolution badge (2160p → "4K") paints a bright pill top-right
    top_right = img.convert("RGB").crop((120, 0, 200, 60))
    assert max(sum(p) for p in top_right.getdata()) > 500


def test_rating_stars_fill_proportional():
    from core.video.overlays.compositor import _rating_tile
    def gold(t):
        return sum(1 for p in t.getdata() if p[0] > 200 and p[1] > 160 and p[2] < 90 and p[3] > 0)
    base = {"type": "rating", "field": "imdb", "stars": 5, "size": 0.06, "color": "#f5c518"}
    full = _rating_tile(dict(base), 600, 900, {"imdb": 10})
    half = _rating_tile(dict(base), 600, 900, {"imdb": 5})
    empty = _rating_tile(dict(base), 600, 900, {"imdb": 0})
    assert gold(full) > gold(half) > gold(empty)          # more rating → more gold
    assert gold(empty) == 0                                # 0 → nothing filled
    assert _rating_tile(dict(base), 600, 900, {}) is None  # no value → skipped
    # rt is out of 100
    assert gold(_rating_tile({**base, "field": "rt"}, 600, 900, {"rt": 100})) > 0


def test_rating_stars_optional_background():
    from core.video.overlays.compositor import _rating_tile
    base = {"type": "rating", "field": "imdb", "stars": 5, "size": 0.06, "color": "#f5c518"}
    plain = _rating_tile(dict(base), 600, 900, {"imdb": 8})
    withbg = _rating_tile({**base, "bg": {"enabled": True, "color": "#000000",
                                          "opacity": 0.6, "radius": 0.02, "padX": 0.02, "padY": 0.012}},
                          600, 900, {"imdb": 8})
    # padding grows the tile on both axes
    assert withbg.width > plain.width and withbg.height > plain.height
    # a semi-transparent black pill sits behind the stars (sample a mid-left edge pixel)
    px = withbg.getpixel((3, withbg.height // 2))
    assert px[3] > 0 and px[0] < 40 and px[1] < 40 and px[2] < 40
    # disabled bg is a no-op (same size as plain)
    off = _rating_tile({**base, "bg": {"enabled": False}}, 600, 900, {"imdb": 8})
    assert off.size == plain.size


def test_bg_box_fixed_size_and_align():
    from core.video.overlays.compositor import _bg_wrap, _bg_align
    from PIL import Image
    c = Image.new("RGBA", (100, 40), (255, 255, 255, 255))
    base = {"enabled": True, "color": "#000000", "opacity": 0.6, "padX": 0.02, "padY": 0.012}
    # hug-content when no fixed size (padX*H=30, padY*H=18 on a 1500-tall poster)
    assert _bg_wrap(c, {"bg": base}, 1000, 1500).size == (160, 76)
    # back_width/back_height override the box size
    assert _bg_wrap(c, {"bg": dict(base, w=0.3, h=0.06)}, 1000, 1500).size == (300, 90)
    # back_align maps 'h v' strings to fractions
    assert _bg_align("left top") == (0.0, 0.0)
    assert _bg_align("right bottom") == (1.0, 1.0)
    assert _bg_align(None) == (0.5, 0.5)


def test_trakt_is_a_rating_field():
    from core.video.overlays.fields import format_field
    from core.video.overlays.compositor import _RATING_MAX, _rating_tile
    assert format_field("trakt", 8.34) == "Trakt 8.3"      # text badge
    assert _RATING_MAX["trakt"] == 10.0                    # scored out of 10 like imdb/tmdb
    assert _rating_tile({"field": "trakt", "stars": 5, "size": 0.06}, 600, 900, {"trakt": 9}) is not None
    assert _rating_tile({"field": "trakt", "stars": 5, "size": 0.06}, 600, 900, {}) is None  # no value → skip


def test_bg_box_optional_border():
    from core.video.overlays.compositor import _bg_wrap, _text_tile
    from PIL import Image
    def gold(t):
        return sum(1 for p in t.getdata() if p[0] > 200 and 140 < p[1] < 190 and p[2] < 90 and p[3] > 200)
    content = Image.new("RGBA", (100, 40), (255, 255, 255, 255))
    box = {"enabled": True, "color": "#000000", "opacity": 0.6, "radius": 0.02,
           "padX": 0.02, "padY": 0.012, "line": True, "lineColor": "#e3a63b", "lineW": 0.01}
    assert gold(_bg_wrap(content, {"bg": box}, 1000, 1500)) > 0               # border drawn on the pill
    assert gold(_bg_wrap(content, {"bg": dict(box, line=False)}, 1000, 1500)) == 0  # off = no border
    # a text pill gets the same back_line border
    assert gold(_text_tile({"text": "4K", "color": "#fff", "size": 0.05, "bg": box}, 1000, 1500, {})) > 0


def test_badge_row_reflows_and_stays_valid():
    """A badge row renders its fields as a bar; a field with no value is SKIPPED so
    the bar closes up (no hole). Fewer values → a narrower row tile."""
    from core.video.overlays.compositor import _row_tile
    style = {"size": 0.05, "color": "#fff", "font": "Inter", "weight": 800,
             "bg": {"enabled": True, "color": "#000", "opacity": 1, "radius": 0.02, "padX": 0.03, "padY": 0.016}}
    layer = {"type": "row", "gap": 0.02, "fields": ["resolution", "hdr", "audio_codec"], "style": style}
    full = _row_tile(layer, 600, 900, {"resolution": "2160p", "hdr": "HDR", "audio_codec": "atmos"})
    partial = _row_tile(layer, 600, 900, {"resolution": "2160p", "audio_codec": "atmos"})  # no HDR
    assert full is not None and partial is not None
    assert partial.width < full.width                    # the missing badge closed up
    # all-empty → nothing to draw
    assert _row_tile(layer, 600, 900, {}) is None


def test_badge_row_children_are_level():
    """Every badge in a row shares the style → equal (content-independent) heights →
    the row tile height equals a single badge's height (they sit on one line)."""
    from core.video.overlays.compositor import _row_tile, _text_tile
    style = {"size": 0.05, "color": "#fff", "font": "Inter", "weight": 800}
    layer = {"type": "row", "gap": 0.02, "fields": ["resolution", "audio_codec"], "style": style}
    row = _row_tile(layer, 600, 900, {"resolution": "1080p", "audio_codec": "atmos"})
    one = _text_tile({**style, "type": "text", "binding": {"field": "resolution"}}, 600, 900, {"resolution": "1080p"})
    assert row.height == one.height


def test_badge_row_renders_in_full_composite():
    base = _poster(color=(0, 0, 0), size=(600, 900))
    layer = {"type": "row", "anchor": "bottom-left", "x": 0.06, "y": 0.94, "opacity": 1, "gap": 0.02,
             "fields": ["resolution"], "style": {"size": 0.06, "color": "#ffffff", "font": "Inter", "weight": 800,
             "bg": {"enabled": True, "color": "#ffffff", "opacity": 1, "radius": 0.02, "padX": 0.03, "padY": 0.02}}}
    img = _open(render_overlay(base, {"layers": [layer]}, {"resolution": "2160p"}))
    # bottom-left white pill painted
    assert max(sum(p) for p in img.crop((0, 800, 200, 900)).getdata()) > 600


def test_maxw_shrinks_long_text_to_fit():
    """A long title must shrink to stay within maxW·W (no overflow off the poster).
    Without maxW it renders full width; with maxW its tile is capped."""
    from core.video.overlays.compositor import _text_tile
    long = "The Lord of the Rings: The Return of the King"
    layer = {"type": "text", "text": long, "size": 0.09, "color": "#ffffff", "font": "Inter", "weight": 800}
    unbounded = _text_tile(dict(layer), 600, 900, {})
    bounded = _text_tile(dict(layer, maxW=0.6), 600, 900, {})
    assert bounded.size[0] < unbounded.size[0]
    assert bounded.size[0] <= int(0.6 * 600) + 6          # within the cap (+rounding)
    # short text under the cap is untouched
    short = _text_tile({"type": "text", "text": "Hi", "size": 0.09, "maxW": 0.6,
                        "color": "#fff", "font": "Inter", "weight": 800}, 600, 900, {})
    assert short.size[0] < int(0.6 * 600)


def test_soft_shadow_spreads_beyond_hard():
    """A blurred drop-shadow paints a softer, wider footprint than a hard one — the
    blurred tile is larger and has semi-transparent shadow pixels around the text."""
    from core.video.overlays.compositor import _text_tile
    base = {"type": "text", "text": "Yg", "size": 0.12, "color": "#ffffff", "font": "Inter", "weight": 800,
            "shadow": True, "shadowColor": "#000000", "shadowOpacity": 0.6, "shadowDy": 0.1}
    hard = _text_tile(dict(base, shadowBlur=0), 600, 900, {})
    soft = _text_tile(dict(base, shadowBlur=0.5), 600, 900, {})
    assert soft.size[0] >= hard.size[0] and soft.size[1] >= hard.size[1]   # blur reserves more room
    # soft shadow has partial-alpha pixels (feathered edge); a hard shadow is binary
    alphas = {p[3] for p in soft.getdata()}
    assert any(0 < a < 255 for a in alphas)


def test_text_uppercase_matches_manual_upper():
    from core.video.overlays.compositor import _text_tile
    a = _text_tile({"type": "text", "text": "hello", "upper": True, "size": 0.06,
                    "color": "#fff", "font": "Inter", "weight": 800}, 600, 900, {})
    b = _text_tile({"type": "text", "text": "HELLO", "size": 0.06,
                    "color": "#fff", "font": "Inter", "weight": 800}, 600, 900, {})
    assert a.size == b.size          # uppercasing "hello" renders the same box as "HELLO"


def test_letter_spacing_widens_text_same_height():
    from core.video.overlays.compositor import _text_tile
    base = {"type": "text", "text": "WIDE", "size": 0.06, "color": "#fff", "font": "Inter", "weight": 800}
    tight = _text_tile(dict(base), 600, 900, {})
    tracked = _text_tile(dict(base, tracking=0.2), 600, 900, {})
    assert tracked.size[0] > tight.size[0]        # tracking widens the box
    assert tracked.size[1] == tight.size[1]       # but height is unchanged


def test_text_stroke_paints_an_outline():
    """A text outline must paint its stroke colour. Fill the glyph with the same
    colour as the background so ONLY the red stroke can show up."""
    base = _poster(color=(0, 0, 0), size=(600, 900))
    layer = {"type": "text", "text": "O", "anchor": "center", "x": 0.5, "y": 0.5,
             "size": 0.2, "color": "#000000", "font": "Inter", "weight": 800, "opacity": 1,
             "stroke": {"enabled": True, "color": "#ff0000", "w": 0.2}}
    img = _open(render_overlay(base, {"layers": [layer]}, {}))
    crop = img.crop((200, 350, 400, 550))
    reds = [p for p in crop.getdata() if p[0] > 150 and p[1] < 90 and p[2] < 90]
    assert len(reds) > 20, "the red outline did not paint"
    # disabled stroke → no red
    layer["stroke"]["enabled"] = False
    img2 = _open(render_overlay(base, {"layers": [layer]}, {}))
    reds2 = [p for p in img2.crop((200, 350, 400, 550)).getdata() if p[0] > 150 and p[1] < 90 and p[2] < 90]
    assert len(reds2) == 0


def test_text_tile_height_is_content_independent():
    """The heart of "1080p vs SD sitting correctly": a badge's box height must not
    depend on the specific glyphs (descenders/ascenders). "1080p" (has a descender)
    and "SD" (none) must produce the SAME tile height, so they stay vertically
    aligned when anchored — width still hugs the content."""
    from core.video.overlays.compositor import _text_tile
    base = {"type": "text", "size": 0.06, "color": "#ffffff", "font": "Inter", "weight": 800}
    tall = _text_tile({**base, "text": "1080p"}, 600, 900, {})
    short = _text_tile({**base, "text": "SD"}, 600, 900, {})
    assert tall.size[1] == short.size[1]         # same height regardless of glyphs
    assert tall.size[0] > short.size[0]          # but width hugs the content
    # and it holds with a pill background (equal padding either way)
    pill = {**base, "bg": {"enabled": True, "color": "#000", "opacity": 1, "radius": 0.02, "padX": 0.03, "padY": 0.02}}
    assert _text_tile({**pill, "text": "1080p"}, 600, 900, {}).size[1] == \
           _text_tile({**pill, "text": "SD"}, 600, 900, {}).size[1]


def test_varying_badges_align_vertically_in_burn():
    """Two bottom-anchored badges with different content must sit on the same line in
    the actual composite — the property that keeps a row of badges level. "1080p" and
    "SD" have equal cap/digit heights on a shared baseline, so the TOP of their ink
    must line up (the p-descender differs, but that's ink, not placement)."""
    base = _poster(color=(0, 0, 0), size=(600, 900))
    def badge(text):
        return {"type": "text", "text": text, "anchor": "bottom-left", "x": 0.1, "y": 0.9,
                "size": 0.06, "color": "#ffffff", "font": "Inter", "weight": 800}
    def top_white_row(img):
        px = img.load()
        for y in range(img.size[1]):
            for x in range(img.size[0]):
                if px[x, y][0] > 180:
                    return y
        return -1
    a = top_white_row(_open(render_overlay(base, {"layers": [badge("1080p")]}, {})))
    b = top_white_row(_open(render_overlay(base, {"layers": [badge("SD")]}, {})))
    assert a >= 0 and b >= 0
    assert abs(a - b) <= 2      # glyph tops line up → the badges are level


def test_corner_ribbon_fills_its_corner():
    """A top-right ribbon is a filled flag seated at the top-right corner: the
    corner TIP is painted (the whole point of the flag model), and the other
    corners are clear."""
    base = _poster(color=(0, 0, 0), size=(600, 900))
    layer = {"type": "ribbon", "corner": "top-right", "size": 0.3,
             "color": "#ff0000", "text": "NEW", "textColor": "#ffffff", "opacity": 1}
    img = _open(render_overlay(base, {"layers": [layer]}, {}))
    def red(box):
        return sum(1 for p in img.crop(box).getdata() if p[0] > 150 and p[1] < 90 and p[2] < 90)
    p = img.getpixel((598, 1))                   # the corner tip itself is filled now
    assert p[0] > 150 and p[1] < 90 and p[2] < 90
    assert red((450, 0, 600, 150)) > 100         # top-right corner has the red flag
    assert red((0, 750, 150, 900)) == 0          # bottom-left corner is clear


def test_ribbon_placement_seats_flag_flush_in_corner():
    from core.video.overlays.compositor import _ribbon_placement
    L = 0.28 * 600                               # leg length = size * min(W, H)
    tl = _ribbon_placement({"corner": "top-left", "size": 0.28}, 600, 900)
    br = _ribbon_placement({"corner": "bottom-right", "size": 0.28}, 600, 900)
    assert tl[:2] == (L / 2, L / 2)              # flag flush against the top-left corner
    assert br[:2] == (600 - L / 2, 900 - L / 2)  # flush against the bottom-right corner
    assert tl[2] == 0 and br[2] == 0             # no rotation — the tile is pre-oriented


def test_conditional_visibility_gates_a_layer():
    from core.video.overlays.compositor import _passes_when
    only4k = {"when": {"field": "resolution", "op": "eq", "value": "2160p"}}
    assert _passes_when(only4k, {"resolution": "2160p"}) is True
    assert _passes_when(only4k, {"resolution": "1080p"}) is False
    assert _passes_when(only4k, {}) is False                       # missing → hide
    hi = {"when": {"field": "imdb", "op": "gte", "value": "8"}}
    assert _passes_when(hi, {"imdb": 8.4}) is True
    assert _passes_when(hi, {"imdb": 6.1}) is False
    assert _passes_when({"when": {"field": "season_count", "op": "exists"}}, {"season_count": 4}) is True
    assert _passes_when({"when": {"field": "season_count", "op": "exists"}}, {}) is False
    assert _passes_when({}, {}) is True                            # no rule → always show


def test_conditional_layer_skipped_in_full_render():
    base = _poster(color=(0, 0, 0), size=(600, 900))
    layer = {"type": "shape", "anchor": "top-left", "x": 0, "y": 0, "w": 1, "h": 1, "opacity": 1,
             "fill": {"grad": False, "c1": "#ffffff", "a1": 1},
             "when": {"field": "resolution", "op": "eq", "value": "2160p"}}
    # 1080p title → rule fails → nothing painted (stays black)
    assert max(sum(p) for p in _open(render_overlay(base, {"layers": [layer]}, {"resolution": "1080p"})).getdata()) < 30
    # 2160p title → rule passes → white paints
    assert max(sum(p) for p in _open(render_overlay(base, {"layers": [layer]}, {"resolution": "2160p"})).getdata()) > 600


def test_broken_layer_does_not_sink_the_render():
    base = _poster()
    # a garbage layer shouldn't crash the whole composite
    out = render_overlay(base, {"layers": [{"type": "text"}, None, {"type": "shape", "w": "oops"}]}, {})
    assert Image.open(io.BytesIO(out)).format == "JPEG"
