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

from PIL import Image, ImageDraw, ImageFont

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
    px = max(1, int(_as_float(layer.get("size"), 0.06) * H))
    font = _font(layer.get("font") or "Inter", layer.get("weight"), px)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    # Auto-fit: cap the glyph width at maxW·W by shrinking the font (matches the
    # editor's canvas-measured shrink). Keeps a long title on the poster.
    max_w = _as_float(layer.get("maxW"), 0)
    if max_w > 0:
        bb0 = probe.textbbox((0, 0), text, font=font, anchor="ls")
        tw0 = bb0[2] - bb0[0]
        cap = max_w * W
        if tw0 > cap > 0:
            px = max(1, int(px * (cap / tw0)))
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
    l, _t, r, _b = probe.textbbox((0, 0), text, font=font, anchor="ls", stroke_width=stroke_w)
    tw = int(math.ceil(r - l))
    ascent, descent = font.getmetrics()
    th = ascent + descent + 2 * stroke_w   # stroke extends past ascent/descent — keep it in the box
    bg = layer.get("bg") or {}
    pill = bool(bg.get("enabled"))
    padx = int(_as_float(bg.get("padX"), 0) * H) if pill else 0
    pady = int(_as_float(bg.get("padY"), 0) * H) if pill else 0
    shadow = bool(layer.get("shadow"))
    sh = int(px * 0.12) if shadow else 0
    tile_w = max(1, tw + 2 * padx + sh)
    tile_h = max(1, th + 2 * pady + sh)
    tile = Image.new("RGBA", (tile_w, tile_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    if pill:
        radius = int(_as_float(bg.get("radius"), 0) * H)
        fill = _hex_rgba(bg.get("color"), bg.get("opacity", 0.6))
        d.rounded_rectangle([0, 0, tw + 2 * padx - 1, th + 2 * pady - 1],
                            radius=max(0, min(radius, (th + 2 * pady) // 2)), fill=fill)
    # Hug left (remove the left side-bearing); baseline sits `ascent` below the box top
    # (+ stroke room so the outline isn't clipped at the top).
    bx = padx - l
    by = pady + ascent + stroke_w
    if shadow:
        d.text((bx + sh, by + sh), text, font=font, fill=(0, 0, 0, 140), anchor="ls")
    d.text((bx, by), text, font=font, fill=_hex_rgba(layer.get("color"), 1.0), anchor="ls",
           stroke_width=stroke_w, stroke_fill=stroke_fill)
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
    return img.resize((tw, th))


def _shape_tile(layer, W, H):
    tw = max(1, int(_as_float(layer.get("w"), 0.5) * W))
    th = max(1, int(_as_float(layer.get("h"), 0.12) * H))
    fill = layer.get("fill") or {}
    if fill.get("grad"):
        tile = _linear_gradient(tw, th, _hex_rgba(fill.get("c1"), fill.get("a1", 1)),
                                _hex_rgba(fill.get("c2"), fill.get("a2", 0)), fill.get("dir", 180))
    else:
        tile = Image.new("RGBA", (tw, th), _hex_rgba(fill.get("c1"), fill.get("a1", 1)))
    radius = int(_as_float(layer.get("radius"), 0) * H)
    if radius > 0:
        mask = _rounded_mask(tw, th, radius)
        out = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        out.paste(tile, (0, 0), mask)
        tile = out
    return tile


def _tile_for(layer, W, H, values, image_loader):
    kind = layer.get("type")
    if kind == "text":
        return _text_tile(layer, W, H, values)
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
