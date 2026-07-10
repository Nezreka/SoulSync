"""Generated collection posters — a member-poster collage with the collection
name burned in, so no collection ever renders as an art-less orb.

Layout adapts to how many member posters we can fetch (4 → 2×2 grid, 3 → one
wide + two, 2 → split, 1 → full bleed, 0 → a name-seeded duotone gradient),
with a dark bottom gradient + the collection title in the bundled Inter face
(same font stack the overlay compositor renders with, so it works in the slim
Docker image with no system fonts).

Files live beside the other poster assets on the persisted data volume:

    <data>/video_poster_assets/collections/<definition_id>.jpg

The definition's ``poster_url`` is pointed at the studio's serve route with a
content hash (``/api/video/collections/<id>/poster?v=<sha1:8>``) — the hash
busts browser caches on regenerate AND changes the sync signature, so the next
sync pushes the new art. The sync engine detects that route form and pushes the
file BYTES to Plex/Jellyfin (the server can't fetch our relative URL).

Rendering is pure (bytes in → JPEG bytes out) and poster fetching is injected,
so everything here is unit-testable without a server.
"""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.collections.poster_gen")

_W, _H = 1000, 1500
_ROUTE_PREFIX = "/api/video/collections/"

# Name-seeded duotone gradients for the no-art fallback (top rgb, bottom rgb).
_GRADIENTS = [
    ((24, 32, 54), (8, 10, 16)),     # midnight blue
    ((46, 26, 52), (10, 8, 16)),     # plum
    ((20, 44, 42), (7, 12, 12)),     # deep teal
    ((52, 34, 22), (14, 9, 7)),      # amber brown
    ((30, 30, 44), (9, 9, 14)),      # slate violet
    ((44, 22, 28), (13, 7, 9)),      # oxblood
]


# ── storage ─────────────────────────────────────────────────────────────────
def posters_root() -> Path:
    """The generated-poster directory beside the video DB (same persisted
    volume the overlay asset store uses)."""
    db = os.environ.get("VIDEO_DATABASE_PATH", "database/video_library.db")
    return Path(db).resolve().parent / "video_poster_assets" / "collections"


def poster_path(definition_id, root: Optional[Path] = None) -> Path:
    return Path(root or posters_root()) / f"{int(definition_id)}.jpg"


def read_poster(definition_id, root: Optional[Path] = None) -> Optional[bytes]:
    try:
        p = poster_path(definition_id, root)
        return p.read_bytes() if p.is_file() else None
    except OSError:
        return None


def is_generated_ref(poster_url) -> bool:
    """True when a definition's poster_url points at our own serve route (the
    sync engine then pushes file bytes instead of the unreachable relative URL)."""
    return bool(poster_url) and str(poster_url).startswith(_ROUTE_PREFIX)


def poster_route(definition_id, data: bytes) -> str:
    return f"{_ROUTE_PREFIX}{int(definition_id)}/poster?v={hashlib.sha1(data).hexdigest()[:8]}"


# ── rendering (pure) ────────────────────────────────────────────────────────
def _cover(img, w: int, h: int):
    from PIL import ImageOps
    return ImageOps.fit(img.convert("RGB"), (w, h), method=3)   # 3 = BICUBIC


def _decode(blobs: List[bytes]):
    from PIL import Image
    out = []
    for b in blobs or []:
        try:
            img = Image.open(io.BytesIO(b))
            img.load()
            out.append(img)
        except Exception:   # noqa: BLE001 - a bad poster just drops out of the collage
            continue
    return out


def _gradient(title: str):
    from PIL import Image
    top, bottom = _GRADIENTS[int(hashlib.sha1((title or "").encode("utf-8")).hexdigest(), 16)
                             % len(_GRADIENTS)]
    img = Image.new("RGB", (_W, _H))
    px = img.load()
    for y in range(_H):
        t = y / (_H - 1)
        row = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(_W):
            px[x, y] = row
    return img


def _compose_collage(imgs) -> "object":
    from PIL import Image
    canvas = Image.new("RGB", (_W, _H), (10, 11, 15))
    n = len(imgs)
    if n >= 4:
        cells = [(0, 0, _W // 2, _H // 2), (_W // 2, 0, _W // 2, _H // 2),
                 (0, _H // 2, _W // 2, _H // 2), (_W // 2, _H // 2, _W // 2, _H // 2)]
        for img, (x, y, w, h) in zip(imgs[:4], cells, strict=False):
            canvas.paste(_cover(img, w, h), (x, y))
    elif n == 3:
        canvas.paste(_cover(imgs[0], _W, _H // 2), (0, 0))
        canvas.paste(_cover(imgs[1], _W // 2, _H // 2), (0, _H // 2))
        canvas.paste(_cover(imgs[2], _W // 2, _H // 2), (_W // 2, _H // 2))
    elif n == 2:
        canvas.paste(_cover(imgs[0], _W // 2, _H), (0, 0))
        canvas.paste(_cover(imgs[1], _W // 2, _H), (_W // 2, 0))
    else:
        canvas.paste(_cover(imgs[0], _W, _H), (0, 0))
    return canvas


def _fit_title(draw, title: str, max_w: int):
    """(lines, font) — shrink to fit one line; below the floor size, split into
    two lines at the space nearest the middle and refit."""
    from core.video.overlays.compositor import _font

    def width(text, f):
        box = draw.textbbox((0, 0), text, font=f)
        return box[2] - box[0]

    for px in range(110, 53, -4):
        f = _font("Inter", 800, px)
        if width(title, f) <= max_w:
            return [title], f

    words = title.split()
    if len(words) > 1:
        # Split nearest the middle of the string.
        best, best_diff = 1, None
        for i in range(1, len(words)):
            diff = abs(len(" ".join(words[:i])) - len(" ".join(words[i:])))
            if best_diff is None or diff < best_diff:
                best, best_diff = i, diff
        lines = [" ".join(words[:best]), " ".join(words[best:])]
        for px in range(84, 41, -4):
            f = _font("Inter", 800, px)
            if all(width(ln, f) <= max_w for ln in lines):
                return lines, f
        return lines, _font("Inter", 800, 42)
    return [title], _font("Inter", 800, 54)


def render_collage(member_posters: List[bytes], title: str) -> bytes:
    """Render the collection poster: collage (or gradient fallback) + dark
    bottom gradient + accent bar + fitted title. Pure — bytes in, JPEG out."""
    from PIL import Image, ImageDraw

    imgs = _decode(member_posters)
    canvas = _compose_collage(imgs) if imgs else _gradient(title)

    # Unifying dark wash + bottom gradient for text legibility.
    overlay = Image.new("RGBA", (_W, _H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle([0, 0, _W, _H], fill=(4, 5, 8, 42))
    grad_top = int(_H * 0.58)
    for y in range(grad_top, _H):
        a = int(235 * ((y - grad_top) / (_H - grad_top)) ** 1.35)
        od.line([(0, y), (_W, y)], fill=(6, 7, 11, a))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)

    draw = ImageDraw.Draw(canvas)
    title = " ".join((title or "Collection").split())
    lines, font = _fit_title(draw, title, max_w=int(_W * 0.86))

    line_h = int(font.size * 1.12)
    block_h = line_h * len(lines)
    y = _H - 96 - block_h
    # Accent bar above the title block.
    bar_w = 120
    draw.rectangle([(_W - bar_w) // 2, y - 34, (_W + bar_w) // 2, y - 26],
                   fill=(140, 190, 255, 255))
    for ln in lines:
        box = draw.textbbox((0, 0), ln, font=font)
        draw.text(((_W - (box[2] - box[0])) // 2 - box[0], y), ln,
                  font=font, fill=(255, 255, 255, 255))
        y += line_h

    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="JPEG", quality=88)
    return out.getvalue()


# ── generation (resolve members → fetch art → render → store) ──────────────
def _default_fetch(db) -> Callable:
    from core.video.overlays.service import fetch_poster_bytes
    return lambda media_type, item_id: fetch_poster_bytes(db, media_type, item_id)


def _collage_members(owned: List[Dict[str, Any]], limit: int = 4) -> List[Dict[str, Any]]:
    """The most poster-worthy members: highest-rated first (nulls last), stable."""
    def rank(m):
        r = m.get("rating")
        return -(r if isinstance(r, (int, float)) else -1)
    return sorted(owned, key=rank)[:limit]


def generate_for_definition(db, definition: Dict[str, Any], *,
                            fetch: Optional[Callable] = None,
                            root: Optional[Path] = None) -> Optional[str]:
    """Render + store the poster for one definition and point its poster_url at
    the serve route. Owned members only (no list fetcher — art needs nothing
    remote). Returns the new poster_url, or None on failure. Never raises."""
    did = (definition or {}).get("id")
    if did is None:
        return None
    try:
        from core.video.collections.resolver import resolve_collection
        res = resolve_collection(db, definition)
        owned = res.owned if res.ok else []
        fetch = fetch or _default_fetch(db)
        media_type = definition.get("media_type") or "movie"
        blobs = []
        for m in _collage_members(owned):
            if len(blobs) >= 4:
                break
            try:
                b = fetch(media_type, m.get("id"))
            except Exception:   # noqa: BLE001 - one bad fetch shouldn't kill the poster
                b = None
            if b:
                blobs.append(b)
        data = render_collage(blobs, definition.get("name") or "Collection")

        path = poster_path(did, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

        url = poster_route(did, data)
        db.update_collection_definition(did, poster_url=url)
        return url
    except Exception:   # noqa: BLE001 - poster art is always best-effort
        logger.exception("poster generation failed for definition %s", did)
        return None


def generate_for_definitions(db, definition_ids, *, fetch: Optional[Callable] = None,
                             root: Optional[Path] = None) -> int:
    """Generate posters for several definitions (preset apply); returns how many
    succeeded. One failure never stops the rest."""
    n = 0
    for did in definition_ids or []:
        d = db.get_collection_definition(did)
        if d and generate_for_definition(db, d, fetch=fetch, root=root):
            n += 1
    return n


__all__ = ["render_collage", "generate_for_definition", "generate_for_definitions",
           "poster_path", "read_poster", "posters_root", "is_generated_ref", "poster_route"]
