"""Cover-art source selection.

Picks album cover art from a user-ordered list of sources, falling back down
the list and finally to the download's own art when nothing in the list
resolves. This generalizes the legacy single ``prefer_caa_art`` toggle into an
ordered, mix-and-match preference (Sokhi's request) while preserving today's
behavior byte-for-byte when no order is configured.

The module is deliberately pure and import-light: the ordering + fallback
contract is unit-testable without network, config, or a DB. The actual
per-source art lookups are injected as callables (a registry the caller
builds), so this module never imports a metadata client — that keeps the
selection logic fast to test and impossible to break with a client-side
network change.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence, Tuple

# Sources we can reliably pull album cover art from, in a sensible default
# priority. This is the set the UI offers and that ``effective_art_order``
# accepts — anything else in a saved order is filtered out.
#
# Genius (lyrics) and Last.fm (deprecated/unreliable images) are excluded — no
# dependable album covers. Tidal/Qobuz/HiFi are deferred: their album lookups
# return IDs that need cover-URL construction and they lack a clean core-side
# client accessor, so rather than ship extraction that silently yields nothing
# we add them once that's verified. The current set covers the universally
# available free sources plus Spotify (the common connected account source).
ART_CAPABLE_SOURCES: Tuple[str, ...] = (
    "caa", "deezer", "itunes", "spotify", "audiodb",
)

# Minimum byte size for a fetched image to count as a real cover. Mirrors the
# existing Cover Art Archive guard in ``artwork.py`` (a 1x1 pixel, a
# placeholder, or a truncated download is a miss, not a hit).
MIN_VALID_ART_BYTES = 1000


def effective_art_order(
    order,
    *,
    prefer_caa_art: bool = False,
) -> list:
    """Resolve the configured art-source order into a concrete priority list.

    Rules (in priority):
    - A configured non-empty list wins. It's lower-cased, trimmed, filtered to
      known art-capable sources, and de-duplicated (first occurrence kept).
    - An empty / missing / all-invalid list preserves **legacy behavior**:
      ``['caa']`` when ``prefer_caa_art`` is on (Cover Art Archive first, then
      the download's own art), else ``[]`` (use the download's own art only).

    The empty-list case is what makes the feature non-breaking: an install that
    has never touched the new setting resolves to exactly today's logic.
    """
    if isinstance(order, (list, tuple)):
        seen = set()
        deduped = []
        for raw in order:
            name = str(raw).strip().lower()
            if not name or name not in ART_CAPABLE_SOURCES or name in seen:
                continue
            seen.add(name)
            deduped.append(name)
        if deduped:
            return deduped
    return ["caa"] if prefer_caa_art else []


def resolve_cover_art(
    order: Sequence[str],
    lookup: Callable[[str], Optional[str]],
    *,
    validate: Optional[Callable[[str, str], bool]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Walk ``order`` and return ``(art_url, source_name)`` for the first source
    whose ``lookup(source)`` yields a URL that passes ``validate`` (if given).

    Returns ``(None, None)`` when nothing in the list resolves — the caller then
    falls back to the download's own art (today's default), so art is never
    *worse* than before.

    Robustness contract:
    - ``lookup`` is ``source_name -> url | None``. A source that returns a
      falsy value is skipped.
    - An exception raised by ``lookup`` or ``validate`` for one source is
      swallowed and treated as a miss, so a single flaky source can never
      abort the whole chain (or the download).
    """
    for source in order:
        try:
            url = lookup(source)
        except Exception:
            url = None
        if not url:
            continue
        if validate is not None:
            try:
                if not validate(source, url):
                    continue
            except Exception:
                continue
        return url, source
    return None, None
