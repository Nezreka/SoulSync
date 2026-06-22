"""Cover Art Archive helper: prefer a pinned release's OWN cover over the
release-group representative.

On the Cover Art Archive a release-group ``front`` is a single REPRESENTATIVE
cover — CAA designates one release in the group to stand for the whole thing,
which is almost always the standard / most-common edition. So when a download
has pinned a SPECIFIC release (e.g. a "Gustave Edition" the user picked), using
the release-group cover silently swaps in the standard art.

This helper tries the specific release's own ``/release/<mbid>/front`` first and
only falls back to the caller's existing URL (a release-group representative or a
provider cover) when the release has no art of its own — so it can only ever
*improve* on today's behaviour, never strip a cover that was already showing.

Pure: the network fetch is injected, so the preference logic is unit-testable.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple

COVER_ART_ARCHIVE_URL = "https://coverartarchive.org"


def caa_front_url(mbid: Optional[str], scope: str = "release", size: int = 1200) -> Optional[str]:
    """Build a Cover Art Archive front-cover URL, or None for a falsy mbid.
    ``scope`` is 'release' (a specific edition) or 'release-group' (the group's
    representative). ``size`` selects the CDN thumbnail (e.g. 250/500/1200); 0
    requests the bare ``/front`` original."""
    if not mbid:
        return None
    if scope not in ("release", "release-group"):
        scope = "release"
    suffix = f"-{size}" if size else ""
    return f"{COVER_ART_ARCHIVE_URL}/{scope}/{mbid}/front{suffix}"


def fetch_release_preferred_art(
    release_mbid: Optional[str],
    fallback_url: Optional[str],
    *,
    fetch_fn: Callable[[str], Tuple[Optional[bytes], Optional[str]]],
    size: int = 1200,
    min_bytes: int = 0,
) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Fetch the best cover, preferring the specific release's own art.

    Tries ``/release/<release_mbid>/front`` first; on any miss (no such art,
    404, or smaller than ``min_bytes``) falls back to ``fallback_url`` (a
    release-group / provider cover). ``fetch_fn(url) -> (bytes|None, mime|None)``;
    it is expected to return ``(None, None)`` on a 404, which is how a release
    with no art of its own advances to the fallback. ``min_bytes`` defaults to 0
    (accept any non-empty image) to preserve the fallback path's prior behaviour;
    callers can raise it to reject placeholder/error images. Returns
    ``(bytes|None, mime|None, url_used|None)``. Never raises for a missing cover —
    a failed candidate just advances to the next, so coverage never regresses."""
    candidates = []
    release_url = caa_front_url(release_mbid, "release", size) if release_mbid else None
    if release_url:
        candidates.append(release_url)
    if fallback_url and fallback_url not in candidates:
        candidates.append(fallback_url)

    for url in candidates:
        try:
            data, mime = fetch_fn(url)
        except Exception:
            data, mime = None, None
        if data and len(data) > min_bytes:
            return data, mime, url
    return None, None, None


__all__ = ["COVER_ART_ARCHIVE_URL", "caa_front_url", "fetch_release_preferred_art"]
