"""Overlay compositor — render a template's layers onto a poster.

render_overlay(base_bytes, definition, values) returns JPEG bytes with the
template's text/badges/logos/shapes burned in. Positions are the same normalized
+ anchored model the editor uses, so the render matches the on-canvas preview.

Pure and testable: the only I/O is fetching remote images for logo/image layers,
and that's injected via `image_loader` (defaults to requests).
"""

from __future__ import annotations

import io
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

from utils.logging_config import get_logger

from .fields import format_field

logger = get_logger("video.overlays.compositor")

# 9-point anchors: fraction of the ELEMENT that pins to (x*W, y*H).
_ANCHOR = {
    "top-left": (0.0, 0.0), "top-center": (0.5, 0.0), "top-right": (1.0, 0.0),
    "mid-left": (0.0, 0.5), "center": (0.5, 0.5), "mid-right": (1.0, 0.5),
    "bottom-left": (0.0, 1.0), "bottom-center": (0.5, 1.0), "bottom-right": (1.0, 1.0),
}

# curated family → (regular, bold) TTF Pillow can resolve. We bundle DejaVu today
# (Pillow ships it); exact browser-matching faces are a later swap — the mapping is
# the single place to change.
_SERIF = "Georgia"
_FONT_CACHE: dict = {}


def _font(family: str, weight, px: int):
    px = max(1, int(px))
    bold = _as_int(weight, 800) >= 700
    serif = family == _SERIF
    key = (serif, bold, px)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    names = []
    if serif:
        names = ["DejaVuSerif-Bold.ttf", "DejaVuSerif.ttf"] if bold else ["DejaVuSerif.ttf"]
    names += ["DejaVuSans-Bold.ttf"] if bold else ["DejaVuSans.ttf"]
    names += ["DejaVuSans.ttf"]
    font = None
    for n in names:
        try:
            font = ImageFont.truetype(n, px)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def _as_int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _hex_rgba(hex_str, alpha=1.0):
    h = str(hex_str or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        n = int(h, 16)
    except ValueError:
        n = 0
    a = max(0, min(255, int(round(_as_float(alpha, 1.0) * 255))))
    return ((n >> 16) & 255, (n >> 8) & 255, n & 255, a)


def _place(x, y, W, H, ew, eh, anchor):
    ax, ay = _ANCHOR.get(anchor, _ANCHOR["center"])
    return int(round(x * W - ax * ew)), int(round(y * H - ay * eh))


def _rounded_mask(w, h, radius):
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    r = int(max(0, min(radius, min(w, h) / 2)))
    if r > 0:
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    else:
        d.rectangle([0, 0, w - 1, h - 1], fill=255)
    return m


def _linear_gradient(w, h, c1, c2, angle):
    """A w×h RGBA linear gradient c1→c2 at `angle` degrees (CSS convention: 180 =
    top→bottom). Built cheaply as a 1-D ramp, rotated, then centre-cropped."""
    w = max(1, w); h = max(1, h)
    diag = int(math.hypot(w, h)) + 2
    ramp = Image.new("RGBA", (1, diag))
    for i in range(diag):
        t = i / (diag - 1) if diag > 1 else 0
        ramp.putpixel((0, i), tuple(int(round(c1[k] + (c2[k] - c1[k]) * t)) for k in range(4)))
    grad = ramp.resize((diag, diag))
    grad = grad.rotate(180 - _as_float(angle, 180), resample=Image.BICUBIC, expand=False)
    left, top = (diag - w) // 2, (diag - h) // 2
    return grad.crop((left, top, left + w, top + h))


def _text_tile(layer, W, H, values):
    binding = layer.get("binding")
    if binding:
        text = format_field(binding.get("field"), (values or {}).get(binding.get("field")))
        if text is None:
            return None            # no value → don't render (matches editor placeholder)
    else:
        text = layer.get("text") or ""
    text = str(text)
    if text == "":
        return None
    if layer.get("upper"):
        text = text.upper()
    px = max(1, int(_as_float(layer.get("size"), 0.06) * H))
    font = _font(layer.get("font") or "Inter", layer.get("weight"), px)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    track_px = _as_float(layer.get("tracking"), 0) * px   # letter-spacing (fraction of font size)

    def _text_w(f, t, tp):
        if tp:
            return sum(f.getlength(ch) for ch in t) + tp * max(0, len(t) - 1)
        bb = probe.textbbox((0, 0), t, font=f, anchor="ls")
        return bb[2] - bb[0]

    # Auto-fit: cap the (tracked) glyph width at maxW·W by shrinking the font (matches
    # the editor's canvas-measured shrink). Keeps a long title on the poster.
    max_w = _as_float(layer.get("maxW"), 0)
    if max_w > 0:
        w0 = _text_w(font, text, track_px)
        cap = max_w * W
        if w0 > cap > 0:
            ratio = cap / w0
            px = max(1, int(px * ratio))
            track_px *= ratio
            font = _font(layer.get("font") or "Inter", layer.get("weight"), px)
    # Width hugs the glyphs (tight horizontal box → clean horizontal anchoring), but
    # HEIGHT comes from the font's line metrics (ascent+descent), NOT the glyph-tight
    # bbox. That makes a badge the SAME height for "1080p" and "SD" — content with or
    # without descenders/ascenders stays vertically aligned in the burn, exactly like
    # the editor (which measures the DOM line box). Measure/draw off the baseline
    # (anchor "ls") for a content-independent vertical origin.
    stroke = layer.get("stroke") or {}
    stroke_w = int(round(_as_float(stroke.get("w"), 0) * px)) if stroke.get("enabled") else 0
    stroke_fill = _hex_rgba(stroke.get("color"), 1.0) if stroke_w > 0 else None
    if track_px:
        tw = int(math.ceil(_text_w(font, text, track_px)))   # advance-based; excludes stroke
        left_bearing = 0
        extra = stroke_w                        # tracked path adds its own stroke room on each side
    else:
        l, _t, r, _b = probe.textbbox((0, 0), text, font=font, anchor="ls", stroke_width=stroke_w)
        tw = int(math.ceil(r - l))              # already includes the stroke extent
        left_bearing = l
        extra = 0
    ascent, descent = font.getmetrics()
    th = ascent + descent + 2 * stroke_w   # stroke extends past ascent/descent — keep it in the box
    bg = layer.get("bg") or {}
    pill = bool(bg.get("enabled"))
    padx = int(_as_float(bg.get("padX"), 0) * H) if pill else 0
    pady = int(_as_float(bg.get("padY"), 0) * H) if pill else 0
    shadow = bool(layer.get("shadow"))
    if shadow:
        sdx = _as_float(layer.get("shadowDx"), 0.0) * px
        sdy = _as_float(layer.get("shadowDy"), 0.12) * px
        sblur = max(0.0, _as_float(layer.get("shadowBlur"), 0.3) * px)
        scol = _hex_rgba(layer.get("shadowColor", "#000000"), _as_float(layer.get("shadowOpacity"), 0.55))
        sh = int(math.ceil(max(abs(sdx), abs(sdy)) + sblur * 2)) + 1
    else:
        sh = 0
    tile_w = max(1, tw + 2 * (padx + extra) + sh)
    tile_h = max(1, th + 2 * pady + sh)
    tile = Image.new("RGBA", (tile_w, tile_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    if pill:
        radius = int(_as_float(bg.get("radius"), 0) * H)
        fill = _hex_rgba(bg.get("color"), bg.get("opacity", 0.6))
        d.rounded_rectangle([0, 0, tw + 2 * (padx + extra) - 1, th + 2 * pady - 1],
                            radius=max(0, min(radius, (th + 2 * pady) // 2)), fill=fill)
    # Hug left (remove the left side-bearing); baseline sits `ascent` below the box top
    # (+ stroke room so the outline isn't clipped at the top).
    bx = padx + extra - left_bearing
    by = pady + ascent + stroke_w

    def _draw(dr, ox, oy, fill, sw=0, sf=None):
        if track_px:
            x = ox
            for ch in text:
                dr.text((x, oy), ch, font=font, fill=fill, anchor="ls", stroke_width=sw, stroke_fill=sf)
                x += font.getlength(ch) + track_px
        else:
            dr.text((ox, oy), text, font=font, fill=fill, anchor="ls", stroke_width=sw, stroke_fill=sf)

    if shadow:
        if sblur > 0.5:
            shl = Image.new("RGBA", tile.size, (0, 0, 0, 0))
            _draw(ImageDraw.Draw(shl), bx + sdx, by + sdy, scol)
            tile = Image.alpha_composite(tile, shl.filter(ImageFilter.GaussianBlur(sblur)))
            d = ImageDraw.Draw(tile)
        else:
            _draw(d, bx + int(round(sdx)), by + int(round(sdy)), scol)
    _draw(d, bx, by, _hex_rgba(layer.get("color"), 1.0), stroke_w, stroke_fill)
    return tile


def _image_tile(layer, W, H, values, image_loader):
    src = (values or {}).get("logo_url") if layer.get("logo") else layer.get("src")
    if not src:
        return None
    try:
        data = image_loader(src)
        if not data:
            return None
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        logger.warning("overlay image load failed for %s", src, exc_info=True)
        return None
    tw = max(1, int(_as_float(layer.get("w"), 0.4) * W))
    scale = tw / img.width if img.width else 1
    th = max(1, int(img.height * scale))
    img = img.resize((tw, th))
    if layer.get("grayscale"):
        a = img.getchannel("A")
        g = ImageOps.grayscale(img)
        img = Image.merge("RGBA", (g, g, g, a))
    radius = int(_as_float(layer.get("radius"), 0) * tw)   # corner radius as fraction of width
    if radius > 0:
        img.putalpha(ImageChops.multiply(img.getchannel("A"), _rounded_mask(tw, th, radius)))
    border = layer.get("border") or {}
    if border.get("enabled"):
        bw = max(1, int(_as_float(border.get("w"), 0.004) * H))
        off = bw / 2
        ImageDraw.Draw(img).rounded_rectangle(
            [off, off, tw - 1 - off, th - 1 - off],
            radius=max(0, radius - bw // 2), outline=_hex_rgba(border.get("color"), 1.0), width=bw)
    return img


def _shape_tile(layer, W, H):
    kind = layer.get("shapeKind") or "rect"
    tw = max(1, int(_as_float(layer.get("w"), 0.5) * W))
    if kind == "line":
        th = max(1, int(_as_float(layer.get("thickness"), 0.006) * H))
    else:
        th = max(1, int(_as_float(layer.get("h"), 0.12) * H))
    fill = layer.get("fill") or {}
    if fill.get("grad"):
        paint = _linear_gradient(tw, th, _hex_rgba(fill.get("c1"), fill.get("a1", 1)),
                                 _hex_rgba(fill.get("c2"), fill.get("a2", 0)), fill.get("dir", 180))
    else:
        paint = Image.new("RGBA", (tw, th), _hex_rgba(fill.get("c1"), fill.get("a1", 1)))
    radius = int(_as_float(layer.get("radius"), 0) * H)
    # build the shape mask by kind
    mask = Image.new("L", (tw, th), 0)
    md = ImageDraw.Draw(mask)
    if kind == "ellipse":
        md.ellipse([0, 0, tw - 1, th - 1], fill=255)
    elif kind == "line":
        md.rounded_rectangle([0, 0, tw - 1, th - 1], radius=th // 2, fill=255)   # rounded caps
    else:
        r = int(max(0, min(radius, min(tw, th) / 2)))
        (md.rounded_rectangle if r > 0 else md.rectangle)(
            [0, 0, tw - 1, th - 1], **({"radius": r, "fill": 255} if r > 0 else {"fill": 255}))
    tile = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    tile.paste(paint, (0, 0), mask)
    # optional border (not on lines)
    border = layer.get("border") or {}
    if border.get("enabled") and kind != "line":
        bw = max(1, int(_as_float(border.get("w"), 0.004) * H))
        bcol = _hex_rgba(border.get("color"), 1.0)
        bd = ImageDraw.Draw(tile)
        off = bw / 2
        box = [off, off, tw - 1 - off, th - 1 - off]
        if kind == "ellipse":
            bd.ellipse(box, outline=bcol, width=bw)
        else:
            r = int(max(0, min(radius, min(tw, th) / 2)))
            bd.rounded_rectangle(box, radius=max(0, r - bw // 2), outline=bcol, width=bw)
    return tile


def _star_points(cx, cy, r_out, r_in, n=5):
    pts = []
    for i in range(n * 2):
        r = r_out if i % 2 == 0 else r_in
        ang = -math.pi / 2 + i * math.pi / n
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    return pts


# rating field → its max (so value/max fills the stars correctly)
_RATING_MAX = {"rt": 100.0, "metacritic": 100.0, "imdb": 10.0, "tmdb": 10.0}


def _rating_tile(layer, W, H, values):
    """A row of stars filled proportionally to a bound rating (imdb/tmdb/rt/metacritic).
    Skipped when the title has no such rating."""
    field = layer.get("field") or "imdb"
    val = (values or {}).get(field)
    if val is None or val == "":
        return None
    try:
        val = float(val)
    except (TypeError, ValueError):
        return None
    frac = max(0.0, min(1.0, val / _RATING_MAX.get(field, 10.0)))
    n = max(1, int(_as_float(layer.get("stars"), 5)))
    sz = max(4, int(_as_float(layer.get("size"), 0.05) * H))
    gap = int(_as_float(layer.get("gap"), 0.2) * sz)
    r_out, r_in = sz / 2.0, sz * 0.21
    total_w = n * sz + (n - 1) * gap
    track = _hex_rgba(layer.get("emptyColor", "#ffffff"), _as_float(layer.get("emptyOpacity"), 0.28))
    fillc = _hex_rgba(layer.get("color", "#f5c518"), 1.0)   # IMDb gold default

    def _stars(color):
        im = Image.new("RGBA", (total_w, sz), (0, 0, 0, 0))
        dd = ImageDraw.Draw(im)
        for i in range(n):
            dd.polygon(_star_points(i * (sz + gap) + sz / 2.0, sz / 2.0, r_out, r_in), fill=color)
        return im

    tile = _stars(track)
    fill_layer = _stars(fillc)
    reveal = Image.new("L", (total_w, sz), 0)
    ImageDraw.Draw(reveal).rectangle([0, 0, max(0, int(round(frac * total_w)) - 1), sz], fill=255)
    tile.paste(fill_layer, (0, 0), ImageChops.multiply(fill_layer.getchannel("A"), reveal))
    return tile


def _row_tile(layer, W, H, values):
    """Auto-layout badge row: render each bound field as a badge in the row's shared
    style, flow them left-to-right with a gap, and SKIP any field with no value so the
    bar closes up (no holes). All badges share a style → equal content-independent
    heights → they're level; centre them vertically for safety."""
    style = layer.get("style") or {}
    gap = int(round(_as_float(layer.get("gap"), 0.014) * W))
    tiles = []
    for f in (layer.get("fields") or []):
        child = dict(style)
        child["type"] = "text"
        child["binding"] = {"field": f}
        t = _text_tile(child, W, H, values)
        if t is not None:
            tiles.append(t)
    if not tiles:
        return None
    total_w = sum(t.width for t in tiles) + gap * (len(tiles) - 1)
    max_h = max(t.height for t in tiles)
    out = Image.new("RGBA", (max(1, total_w), max(1, max_h)), (0, 0, 0, 0))
    x = 0
    for t in tiles:
        out.alpha_composite(t, (x, (max_h - t.height) // 2))
        x += t.width + gap
    return out


def _ribbon_tile(layer, W, H):
    """The (un-rotated) diagonal band for a corner ribbon: a coloured bar with centred
    text. render_overlay positions + rotates it onto the chosen corner."""
    m = min(W, H)
    dist = _as_float(layer.get("dist"), 0.28) * m
    thick = max(2, int(_as_float(layer.get("thickness"), 0.06) * m))
    length = max(2, int(round(2 * dist)))
    tile = Image.new("RGBA", (length, thick), _hex_rgba(layer.get("color", "#d11e2a"),
                                                        _as_float(layer.get("bandOpacity"), 1.0)))
    text = str(layer.get("text") or "")
    if layer.get("upper"):
        text = text.upper()
    if text:
        px = max(1, int(thick * _as_float(layer.get("textScale"), 0.5)))
        font = _font(layer.get("font") or "Inter", layer.get("weight"), px)
        ImageDraw.Draw(tile).text((length / 2, thick / 2), text, font=font,
                                  fill=_hex_rgba(layer.get("textColor", "#ffffff"), 1.0), anchor="mm")
    return tile


# corner → (unit centre offset from corner along the inward diagonal, CSS rotation).
_RIBBON = {
    "top-left": ((0, 0), 1, 1, -45), "top-right": ((1, 0), -1, 1, 45),
    "bottom-left": ((0, 1), 1, -1, 45), "bottom-right": ((1, 1), -1, -1, -45),
}


def _ribbon_placement(layer, W, H):
    """(cx, cy, rotation°) that seats the band across its corner, ends on the two edges."""
    (ox, oy), sx, sy, rot = _RIBBON.get(layer.get("corner") or "top-right", _RIBBON["top-right"])
    s = _as_float(layer.get("dist"), 0.28) * min(W, H) / math.sqrt(2)
    return ox * W + sx * s, oy * H + sy * s, rot


def _passes_when(layer, values):
    """Conditional visibility: a layer with a `when` rule renders only if the title's
    data satisfies it. Mirrors the editor's whenPasses() exactly."""
    w = layer.get("when")
    if not w or not w.get("field"):
        return True
    raw = (values or {}).get(w.get("field"))
    has = raw is not None and raw != ""
    op = w.get("op") or "exists"
    if op == "exists":
        return has
    if op == "neq":
        return (not has) or str(raw).lower() != str(w.get("value")).lower()
    if not has:
        return False
    if op == "eq":
        return str(raw).lower() == str(w.get("value")).lower()
    if op == "contains":
        return str(w.get("value")).lower() in str(raw).lower()
    try:
        a, b = float(raw), float(w.get("value"))
    except (TypeError, ValueError):
        return False
    return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}.get(op, True)


def _tile_for(layer, W, H, values, image_loader):
    kind = layer.get("type")
    if kind == "text":
        return _text_tile(layer, W, H, values)
    if kind == "row":
        return _row_tile(layer, W, H, values)
    if kind == "rating":
        return _rating_tile(layer, W, H, values)
    if kind == "ribbon":
        return _ribbon_tile(layer, W, H)
    if kind == "image":
        return _image_tile(layer, W, H, values, image_loader)
    if kind == "shape":
        return _shape_tile(layer, W, H)
    return None


def _default_loader(url):
    if str(url).startswith("asset://"):
        from .assets import AssetStore
        data = AssetStore.default().read_upload(str(url)[len("asset://"):])
        if data is None:
            raise FileNotFoundError(url)
        return data
    import requests
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.content


# Representative values so a gallery thumbnail's dynamic badges render something.
_THUMB_SAMPLE = {
    "resolution": "2160p", "hdr": "HDR", "video_codec": "hevc", "audio_codec": "atmos",
    "source": "bluray", "imdb": 8.4, "rt": 92, "metacritic": 81, "tmdb": 8.1,
    "content_rating": "PG-13", "status": "Returning", "year": 2021, "runtime": 148,
    "season_count": 4, "episode_count": 62, "title": "Example", "network": "HBO", "studio": "A24",
    "genre": "Sci-Fi",
}


def _neutral_base(w: int, h: int) -> bytes:
    """A dark vertical-gradient poster to preview a template against (no title art)."""
    img = Image.new("RGB", (w, h))
    top, bot = (38, 38, 49), (16, 16, 22)
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        row = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
        for x in range(w):
            px[x, y] = row
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


def _thumb_loader(url):
    """Thumbnail image loader: resolve uploaded asset:// files from disk, skip
    external http (a gallery preview shouldn't block on the network)."""
    if str(url).startswith("asset://"):
        from .assets import AssetStore
        return AssetStore.default().read_upload(str(url)[len("asset://"):])
    return None


def render_template_thumbnail(definition: dict, *, size=(300, 450), image_loader=None) -> bytes:
    """Render a template onto a neutral poster for the gallery. Dynamic badges use
    representative sample values so they show real-looking text."""
    return render_overlay(_neutral_base(size[0], size[1]), definition, _THUMB_SAMPLE,
                          image_loader=image_loader or _thumb_loader)


def render_overlay(base_bytes: bytes, definition: dict, values: dict | None = None,
                   *, image_loader=None) -> bytes:
    """Composite a template's layers onto poster art. Returns JPEG bytes at the
    base image's native resolution."""
    image_loader = image_loader or _default_loader
    canvas = Image.open(io.BytesIO(base_bytes)).convert("RGBA")
    W, H = canvas.size
    layers = (definition or {}).get("layers") or []
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("hidden"):
            continue
        if not _passes_when(layer, values):
            continue
        try:
            tile = _tile_for(layer, W, H, values, image_loader)
        except Exception:
            logger.warning("overlay layer render failed (%s)", layer.get("type"), exc_info=True)
            tile = None
        if tile is None:
            continue
        opacity = _as_float(layer.get("opacity"), 1.0)
        if opacity < 1.0:
            tile.putalpha(tile.getchannel("A").point(lambda a, o=opacity: int(a * o)))
        # Anchor the un-rotated box, then rotate around its centre (matches the
        # editor's transform-origin:center). CSS rotate() is clockwise; PIL is CCW,
        # so negate. expand=True grows the tile; re-centre it on the same point.
        ew0, eh0 = tile.size
        if layer.get("type") == "ribbon":
            cx, cy, rot = _ribbon_placement(layer, W, H)
        else:
            ax, ay = _ANCHOR.get(layer.get("anchor") or "center", _ANCHOR["center"])
            cx = _as_float(layer.get("x"), 0.5) * W - ax * ew0 + ew0 / 2
            cy = _as_float(layer.get("y"), 0.5) * H - ay * eh0 + eh0 / 2
            rot = _as_float(layer.get("rotation"), 0.0)
        if rot:
            tile = tile.rotate(-rot, resample=Image.BICUBIC, expand=True)
        ew, eh = tile.size
        left, top = int(round(cx - ew / 2)), int(round(cy - eh / 2))
        stamp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        stamp.paste(tile, (left, top), tile)   # paste clips to canvas bounds
        canvas = Image.alpha_composite(canvas, stamp)
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="JPEG", quality=92)
    return out.getvalue()
