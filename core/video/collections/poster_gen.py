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
import threading
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
    # A 1×H strip resized to full width — the per-pixel loop took seconds.
    from PIL import Image
    top, bottom = _GRADIENTS[int(hashlib.sha1((title or "").encode("utf-8")).hexdigest(), 16)
                             % len(_GRADIENTS)]
    strip = Image.new("RGB", (1, _H))
    px = strip.load()
    for y in range(_H):
        t = y / (_H - 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return strip.resize((_W, _H))


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


def render_logo_poster(logo_bytes: bytes, title: str) -> Optional[bytes]:
    """A studio-card poster: the studio's (transparent) logo centered on a
    name-seeded gradient. Dark logos get a light card so they never vanish.
    Pure — bytes in, JPEG out; None when the logo can't be decoded."""
    from PIL import Image
    try:
        logo = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
    except Exception:   # noqa: BLE001 - bad logo → caller falls back to collage
        return None

    # Mean luminance of the visible pixels decides the card tone.
    small = logo.resize((min(64, logo.width), min(64, logo.height)))
    px = list(small.getdata())
    vis = [(r, g, b) for r, g, b, a in px if a > 40]
    lum = (sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in vis) / len(vis)) if vis else 255
    if lum < 96:   # dark logo → light card
        card = Image.new("RGB", (_W, _H), (231, 233, 238))
        strip = Image.new("RGB", (1, _H))
        sp = strip.load()
        for y in range(_H):
            t = y / (_H - 1)
            sp[0, y] = (int(231 - 14 * t), int(233 - 14 * t), int(238 - 12 * t))
        card = strip.resize((_W, _H))
    else:
        card = _gradient(title)

    # Fit the logo into a centered box (~66% wide, capped height), keep aspect.
    box_w, box_h = int(_W * 0.66), int(_H * 0.30)
    scale = min(box_w / logo.width, box_h / logo.height)
    logo = logo.resize((max(1, int(logo.width * scale)), max(1, int(logo.height * scale))))
    card = card.convert("RGBA")
    card.alpha_composite(logo, ((_W - logo.width) // 2, (_H - logo.height) // 2))

    out = io.BytesIO()
    card.convert("RGB").save(out, format="JPEG", quality=88)
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


def _default_list_fetcher(db):
    try:
        from core.video.collections.list_sources import build_list_fetcher
        return build_list_fetcher(db)
    except Exception:   # noqa: BLE001 - no fetcher → owned-only resolve
        return None


# ── context art (real artwork beats a collage where the subject HAS one) ────
def _preset_logo_hint(body: Dict[str, Any]) -> Optional[str]:
    """The preset catalog's logo hint for a union definition created BEFORE the
    hint existed — matched by keyword set, so old MCU/DCEU rows heal on a plain
    artwork refresh."""
    kws = {str(k).casefold() for k in (body.get("keywords") or [])}
    if not kws:
        return None
    try:
        from core.video.collections.presets import _UNIVERSES
        for _, _, spec, _ in _UNIVERSES:
            if spec.get("logo") and {str(k).casefold() for k in (spec.get("keywords") or [])} == kws:
                return spec["logo"]
    except Exception:   # noqa: BLE001 - a healing nicety
        pass
    return None


def _single_rule(body: Dict[str, Any], field: str):
    """The rule's scalar value when the definition is exactly one `field is X`
    rule, else None."""
    rules = body.get("rules") or []
    if len(rules) != 1 or rules[0].get("field") != field:
        return None
    val = rules[0].get("value")
    val = val[0] if isinstance(val, list) and val else val
    return val if isinstance(val, str) and val.strip() else None


def _context_art(definition: Dict[str, Any], *, engine=None,
                 http_get: Optional[Callable] = None):
    """(image_bytes, treatment) when the definition's subject carries its own
    artwork on TMDB, else None:
      · franchise / universe (with franchise ids) → the collection's title art,
        'verbatim' (it already carries the branding),
      · a single-director smart collection → the director's portrait, 'title'
        (name burned in via the standard treatment),
      · a single-studio smart collection → the studio's logo, 'logo'
        (composited onto a gradient card).
    Best-effort at every step — any miss falls back to the collage."""
    try:
        if engine is None:
            from core.video.enrichment.engine import get_video_enrichment_engine
            engine = get_video_enrichment_engine()
    except Exception:   # noqa: BLE001
        return None
    if engine is None:
        return None

    body = definition.get("definition") or {}
    url = None
    treatment = "verbatim"
    try:
        if definition.get("kind") == "list":
            source = str(body.get("source") or "").lower()
            if source in ("franchise", "tmdb_collection") and body.get("collection_id"):
                url = engine.collection_poster(body["collection_id"])
            elif source == "tmdb_union":
                for cid in body.get("collections") or []:
                    url = engine.collection_poster(cid)
                    if url:
                        break
                # Keyword-only universes (MCU/DCEU) have no collection art —
                # their 'logo' hint gives them the studio's mark instead.
                # (Older definitions predate the hint: fall back to the preset
                # catalog's hint for the same keyword set, so a plain artwork
                # refresh heals them without a delete + re-apply.)
                logo = body.get("logo") or _preset_logo_hint(body)
                if not url and logo:
                    url = engine.company_logo(logo)
                    treatment = "logo"
        else:
            director = _single_rule(body, "director")
            studio = _single_rule(body, "studio")
            if director:
                url = engine.person_photo(director)
                treatment = "title"
            elif studio:
                # Studio rules may be brand-grouped variant lists ("Hallmark
                # Channel" + "Hallmark Media") — the first variant finds the logo.
                rules = body.get("rules") or []
                if rules[0].get("op") in ("is", "in", "contains"):
                    url = engine.company_logo(studio)
                    treatment = "logo"
    except Exception:   # noqa: BLE001
        logger.debug("context art lookup failed", exc_info=True)
        return None
    if not url:
        return None
    try:
        if http_get is None:
            import requests
            http_get = lambda u: requests.get(u, timeout=20)   # noqa: E731
        r = http_get(url)
        data = getattr(r, "content", None) if getattr(r, "status_code", 200) == 200 else None
        return (data, treatment) if data else None
    except Exception:   # noqa: BLE001
        logger.debug("context art fetch failed: %s", url, exc_info=True)
        return None


def generate_for_definition(db, definition: Dict[str, Any], *,
                            fetch: Optional[Callable] = None,
                            owned: Optional[List[Dict[str, Any]]] = None,
                            list_fetcher: Optional[Callable] = None,
                            mode: str = "auto",
                            context_engine=None,
                            root: Optional[Path] = None) -> Optional[str]:
    """Render + store the poster for one definition and point its poster_url at
    the serve route. ``mode='auto'`` prefers the subject's REAL artwork when it
    has one (franchise/universe title art verbatim; a director's portrait with
    the name burned in) and collages otherwise; ``mode='collage'`` forces the
    member collage. Resolves with the real list fetcher by default; pass
    ``owned`` to skip the resolve (sync already has it). Returns the new
    poster_url, or None. Never raises."""
    did = (definition or {}).get("id")
    if did is None:
        return None
    try:
        data = None
        if mode != "collage":
            ctx = _context_art(definition, engine=context_engine)
            if ctx:
                art, treatment = ctx
                name = definition.get("name") or "Collection"
                if treatment == "title":
                    data = render_collage([art], name)
                elif treatment == "logo":
                    data = render_logo_poster(art, name)   # None → collage below
                else:
                    data = art
        if data is None:
            if owned is None:
                from core.video.collections.resolver import resolve_collection
                res = resolve_collection(db, definition,
                                         list_fetcher=list_fetcher or _default_list_fetcher(db))
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


# ── bulk artwork refresh ("get the new posters everywhere") ─────────────────
# A JobChannel like cleanup/sync-all: live 'collections:artwork' progress for
# the bell + studio, status endpoint as the polling fallback.
from core.video.collections.job_channel import JobChannel  # noqa: E402 - feature-local

_channel = JobChannel("collections:artwork",
                      {"done": 0, "total": 0, "rendered": 0, "failed": 0,
                       "name": None, "error": None})
_JOB = _channel.job


def set_artwork_progress_emitter(fn) -> None:
    _channel.set_emitter(fn)


def artwork_status() -> dict:
    """The refresh job's current state (polling fallback / bell seed)."""
    return _channel.status()


def regenerate_candidates(db) -> list:
    """Definitions whose art WE own: a generated poster ref or no poster at
    all. A hand-set external URL is the user's choice — never clobbered.
    Returns light rows (id + name for progress display)."""
    out = []
    for c in db.list_collection_definitions() or []:
        pu = c.get("poster_url")
        if not pu or is_generated_ref(pu):
            out.append({"id": c["id"], "name": c.get("name")})
    return out


def regenerate_all(db, *, mode: str = "auto", fetch: Optional[Callable] = None,
                   root: Optional[Path] = None,
                   on_progress: Optional[Callable] = None,
                   workers: int = 3) -> int:
    """Re-render every owned poster with the current art pipeline (context art
    first). Renders run on a small worker pool — each poster is I/O-bound
    (member art from the media server + TMDB lookups), so sequential rendering
    made a big refresh take ~3× longer than it needed to.
    ``on_progress(done, total, name, rendered, failed)`` fires as each finishes
    (counters computed under the lock — safe across workers). Returns how many
    were regenerated. Synchronous — callers thread it."""
    cands = regenerate_candidates(db)
    if not cands:
        return 0
    state = {"done": 0, "rendered": 0, "failed": 0}
    lock = threading.Lock()

    def one(cand):
        d = db.get_collection_definition(cand["id"])
        ok = bool(d and generate_for_definition(db, d, mode=mode, fetch=fetch, root=root))
        with lock:
            state["done"] += 1
            state["rendered" if ok else "failed"] += 1
            snap = dict(state)
        if on_progress:
            try:
                on_progress(snap["done"], len(cands), cand.get("name"),
                            snap["rendered"], snap["failed"])
            except Exception:   # noqa: BLE001 - a progress hook can't kill the run
                pass

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(one, cands))
    return state["rendered"]


def kick_regenerate_all(db) -> dict:
    """Run regenerate_all as the artwork-refresh job (at most one at a time),
    with live channel progress. Returns {ok, total}, or {ok: False} when busy."""
    total = len(regenerate_candidates(db))
    if not _channel.acquire(total=total):
        return {"ok": False, "error": "an artwork refresh is already running"}

    def run():
        try:
            _JOB.update(phase="running")

            def prog(done, tot, name, rendered, failed):
                _JOB.update(done=done, total=tot, name=name,
                            rendered=rendered, failed=failed)
                _channel.emit()

            n = regenerate_all(db, on_progress=prog)
            _JOB.update(phase="done")
            logger.info("artwork refresh regenerated %d poster(s)", n)
        except Exception as e:   # noqa: BLE001 - background nicety
            logger.exception("artwork refresh failed")
            _JOB.update(phase="error", error=str(e))
        finally:
            _channel.release()

    threading.Thread(target=run, name="collection-art-refresh", daemon=True).start()
    return {"ok": True, "total": total}


__all__ = ["render_collage", "render_logo_poster", "generate_for_definition",
           "generate_for_definitions", "regenerate_all", "kick_regenerate_all",
           "regenerate_candidates", "artwork_status", "set_artwork_progress_emitter",
           "poster_path", "read_poster", "posters_root", "is_generated_ref", "poster_route"]
