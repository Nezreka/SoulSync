"""EXT.to release detail pages — the facts behind a Fresh Releases card.

Every row on the Fresh Releases board links to its own ext.to page, and that page
already carries what the board's cards were missing: poster art, the real title,
an IMDb id + rating, and a block of facts about the title.

WHY THIS IS LABEL-DRIVEN and not a fixed schema: ext.to serves a different fact
list per category, and the two overlap barely at all.

    movie  ->  Movie · Detected quality · IMDb link · IMDb rating · Genres ·
               Director · Cast · Release date · Release year · Runtime ·
               Country · Keywords · Budget
    tv     ->  Original name · Type · IMDb link · IMDb rating · Created by ·
               Production · Tagline · Networks · Country · Language · Schedule

Hard-coding either shape drops half the board on the floor, so ``parse_detail``
reads whatever ``<li><strong>Label:</strong> value</li>`` pairs the page happens
to carry, keeps them in page order for display, and normalises only the handful
the UI actually reasons about (poster / title / imdb / year / runtime).

The result is presentational: SoulSync does NOT resolve these to TMDB. Grabbing
still goes through the identify modal, where the user says what the release is.
The facts are here to make that call an informed one.

Fetching goes through FlareSolverr like the rest of the EXT.to code, and results
are cached in-process: a release's detail page never meaningfully changes, and
each fetch costs its own Cloudflare challenge.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from core.video.extto_search import (
    BASES,
    ExtToBlocked,
    FlareSolverrClient,
    _clean,
    _fetch,
    _int,
    _soup,
    flaresolverr_url,
)
from utils.logging_config import get_logger

logger = get_logger("video.extto_detail")

# ext.to serves this when a release has no artwork; it is a grey placeholder, so
# treating it as a poster would put a broken-looking tile on the card.
PLACEHOLDER_POSTER = "no-torrent-image"

# Stamped into every parsed payload, and checked when one is read back out of the
# cache. Matched releases are cached to make the hourly refresh cheap, which means
# an entry can outlive the parser that produced it: teaching the parser to prefer
# the TMDB poster on TV pages fixed nothing for anything already matched, because
# the refresh served the old parse straight back and never looked at the page
# again. BUMP THIS whenever parse_detail starts extracting something new — a
# mismatched stamp reads as a cache miss, so the release is re-fetched once and
# re-stored, and the improvement actually reaches the board.
PARSE_VERSION = 2

# Labels we lift into normalised fields. Everything else still reaches the UI
# through `facts`, in page order.
_TITLE_LABELS = ("movie", "original name", "show", "tv show", "name")
_RATING_RE = re.compile(r"([\d.]+)\s*(?:\(([\d,]+)\s*votes?\))?")
_IMDB_RE = re.compile(r"/title/(tt\d+)")

_CACHE_MAX = 512
_cache: "OrderedDict[str, dict]" = OrderedDict()


def is_extto_url(url: Any) -> bool:
    """Whether this is an ext.to URL we are willing to fetch.

    The detail endpoint takes a URL from the client, so without this it is an
    open proxy: anything could ask SoulSync's FlareSolverr to fetch anything and
    hand back the body. Only the hosts the search itself uses are allowed.
    """
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    for base in BASES:
        allowed = (urlparse(base).hostname or "").lower()
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def _label_pairs(soup) -> list:
    """Every ``<strong>Label:</strong> value`` row, in page order.

    The value is read by REMOVING the label node rather than slicing the text at
    the label's length — the separator is not always a single character, and a
    slice quietly shifts every value by one when it is not.
    """
    out = []
    for li in soup.select("ul.detail-page-info-list li"):
        strong = li.select_one("strong")
        if strong is None:
            continue
        label = _clean(strong.get_text(" ")).rstrip(":").strip()
        if not label:
            continue
        links = [(_clean(a.get_text(" ")), a.get("href") or "") for a in li.select("a[href]")]
        strong.extract()                       # parsed once and discarded
        value = _clean(li.get_text(" "))
        # 'Cast: A, B, C and others' — the trailing prose is not a cast member
        value = re.sub(r"\s+and others$", "", value).strip(" ,")
        if value or links:
            out.append({"label": label, "value": value, "links": links})
    return out


def _linked_names(entry: dict) -> list:
    """The anchor texts of a fact, for the list-shaped ones (genres, cast)."""
    return [t for t, _href in entry.get("links") or [] if t]


def parse_detail(html: str, base_url: str = BASES[0], url: str = "") -> dict:
    """The facts on one ext.to detail page. Pure — no network."""
    soup = _soup(html)
    pairs = _label_pairs(soup)
    by_label = {p["label"].lower(): p for p in pairs}

    # Two different poster sources, and which one exists depends on the category:
    #   TV     -> .poster-block carries a TMDB url, and detail-torrent-image is the
    #             grey 'no artwork' placeholder
    #   movies -> no .poster-block; the art is ext.to-hosted
    # TMDB is preferred wherever it exists: it is not behind Cloudflare, it is
    # already allowlisted by the image proxy, and it is a bigger image.
    img = soup.select_one("img.detail-torrent-image")
    tmdb = soup.select_one(".poster-block img[src]")
    poster = str((tmdb or {}).get("src") or "").strip() if tmdb is not None else ""
    if not poster:
        poster = str(img.get("src") or "").strip() if img is not None else ""
    if poster and PLACEHOLDER_POSTER in poster:
        poster = ""                            # ext.to's 'no artwork' grey tile

    title = None
    for key in _TITLE_LABELS:
        if key in by_label:
            entry = by_label[key]
            title = (_linked_names(entry) or [entry["value"]])[0] or None
            break

    imdb_id = None
    entry = by_label.get("imdb link")
    if entry:
        for _text, href in entry.get("links") or []:
            m = _IMDB_RE.search(str(href))
            if m:
                imdb_id = m.group(1)
                break
        if not imdb_id and entry["value"].strip().isdigit():
            imdb_id = "tt" + entry["value"].strip()

    rating = votes = None
    entry = by_label.get("imdb rating")
    if entry:
        m = _RATING_RE.search(entry["value"])
        if m:
            try:
                rating = float(m.group(1))
            except (TypeError, ValueError):
                rating = None
            votes = _int(m.group(2)) if m.group(2) else None

    year = _int((by_label.get("release year") or {}).get("value"))
    runtime = _int((by_label.get("runtime") or {}).get("value"))
    quality = (by_label.get("detected quality") or {}).get("value") or None
    genres = _linked_names(by_label.get("genres") or {})
    cast = _linked_names(by_label.get("cast") or {})

    return {
        "v": PARSE_VERSION,
        "url": url or "",
        "poster_url": urljoin(base_url, poster) if poster else None,
        "poster_title": _clean(img.get("title") or "") if img is not None else "",
        "title": title,
        "imdb_id": imdb_id,
        "imdb_rating": rating,
        "imdb_votes": votes,
        "year": year,
        "runtime_minutes": runtime,
        "quality": quality,
        "genres": genres,
        "cast": cast,
        # everything the page stated, in the order it stated it — the card shows a
        # curated few, the expander shows the lot, and a category we have never
        # seen still renders instead of coming back empty
        "facts": [{"label": p["label"], "value": p["value"]} for p in pairs if p["value"]],
    }


def _cache_get(url: str):
    hit = _cache.get(url)
    if hit is not None:
        _cache.move_to_end(url)
    return hit


def _cache_put(url: str, detail: dict) -> None:
    _cache[url] = detail
    _cache.move_to_end(url)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def cache_clear() -> None:
    _cache.clear()


def fetch_detail(url: Any, *, timeout: int = 25, flaresolverr: Optional[str] = None,
                 use_cache: bool = True) -> dict:
    """One release's detail facts. ``{ok, detail}`` or ``{ok: False, error}``.

    Cached in-process by URL: a release's page does not change, and every miss
    costs a Cloudflare challenge on the shared FlareSolverr session. Never raises.

    NO retry, and a shorter budget than the grab path, on purpose. This runs once
    per row to decorate a card that is already usable without it, so a slow
    challenge must cost the board a few seconds and then give up - retrying a
    40-second timeout just turns it into eighty. ``resolve_magnet`` is the
    opposite case (one deliberate click, nothing works without it) and keeps its
    longer budget and its retry.
    """
    target = str(url or "").strip()
    if not is_extto_url(target):
        return {"ok": False, "error": "Not an EXT.to release page."}
    if use_cache:
        hit = _cache_get(target)
        if hit is not None:
            return {"ok": True, "detail": hit, "cached": True}
    solver = str(flaresolverr if flaresolverr is not None else flaresolverr_url()).rstrip("/")
    if not solver:
        return {"ok": False, "error": "EXT.to requires FlareSolverr — set flaresolverr.url."}
    try:
        html = _fetch(FlareSolverrClient(solver, timeout=timeout), target)
    except ExtToBlocked as exc:
        logger.warning("EXT.to detail blocked: %s", exc)
        return {"ok": False, "error": str(exc)}
    except Exception as exc:   # noqa: BLE001 - network boundary; the caller reports it
        logger.warning("EXT.to detail fetch failed for %s: %s", target, exc)
        return {"ok": False, "error": "EXT.to: " + str(exc)}
    detail = parse_detail(html, BASES[0], url=target)
    _cache_put(target, detail)
    return {"ok": True, "detail": detail, "cached": False}


__all__ = ["parse_detail", "fetch_detail", "is_extto_url", "cache_clear",
           "PLACEHOLDER_POSTER", "PARSE_VERSION"]
