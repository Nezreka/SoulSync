"""identify a track by its isrc instead of searching for it.

an isrc names one recording. beatport hands one over with every track, and
spotify (``isrc:`` search) and deezer (``/track/isrc:<code>``) can look it up
directly, so the exact recording comes back: the extended mix stays the
extended mix instead of whatever version a title search ranks first.

isrcs do get reused and mis-assigned now and then, so a hit only counts when
one of its artists agrees with the source's. anything that doesn't pan out is
None and the normal search runs, so this can never do worse than before.

same contract as direct_match: returns the provider's own Track object, the
one a search would have returned, so the caller stores it unchanged.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("discovery.isrc_match")

_ISRC_RE = re.compile(r'^[A-Z]{2}[A-Z0-9]{3}\d{7}$')


def clean_isrc(value: Any) -> str:
    """an isrc in its canonical form, or '' when it isn't one."""
    code = re.sub(r'[\s-]', '', str(value or '')).upper()
    return code if _ISRC_RE.match(code) else ''


def _norm(name: Any) -> str:
    return re.sub(r'[^0-9a-z]+', '', str(name or '').casefold())


def artists_agree(wanted: Iterable[Any], got: Iterable[Any]) -> bool:
    """true when one of the source's artists is one of the hit's. a name
    contained in the other counts ("Armin van Buuren" vs "Armin van Buuren &
    Funk Tribu"). no source artists means nothing to check against."""
    want = [n for n in (_norm(w) for w in wanted or []) if len(n) >= 2]
    if not want:
        return True
    have = [n for n in (_norm(g) for g in got or []) if n]
    return any(w in h or h in w for w in want for h in have)


def _spotify_isrc(client: Any, code: str) -> Optional[Any]:
    from core.spotify_client import Track as SpotifyTrack

    if client is None or not getattr(client, 'sp', None):
        return None
    # the isrc: field needs the real api. the no-auth path can't do it, and a
    # free-text search for the code would match nothing useful
    try:
        if not client.is_spotify_authenticated() or client._free_active():
            return None
    except Exception:
        return None
    results = client.sp.search(q=f"isrc:{code}", type="track", limit=5)
    items = ((results or {}).get('tracks') or {}).get('items') or []
    for raw in items:
        if raw and raw.get('id'):
            try:
                from core.metadata.cache import get_metadata_cache
                # the discovery workers read the full album from here
                get_metadata_cache().store_entity('spotify', 'track', raw['id'], raw)
            except Exception as e:  # noqa: BLE001
                logger.debug("spotify isrc cache write failed: %s", e)
            return SpotifyTrack.from_spotify_track(raw)
    return None


def _deezer_isrc(client: Any, code: str) -> Optional[Any]:
    from core.deezer_client import DeezerClient, Track as DeezerTrack

    if not isinstance(client, DeezerClient):
        return None
    # public endpoint: a stale oauth token would fail it outright
    raw = client._api_get(f"/track/isrc:{code}", use_token=False)
    if not isinstance(raw, dict) or raw.get('error') or not raw.get('id'):
        return None
    return DeezerTrack.from_deezer_track(raw)


def isrc_match(
    isrc: Any,
    discovery_source: str,
    use_spotify: bool,
    spotify_client: Any = None,
    fallback_client: Any = None,
    artists: Iterable[Any] = (),
) -> Optional[Any]:
    """the provider's track for this isrc, or None to fall back to search."""
    code = clean_isrc(isrc)
    if not code:
        return None
    source = (discovery_source or '').lower()
    try:
        if use_spotify and source == 'spotify':
            track = _spotify_isrc(spotify_client, code)
        elif source == 'deezer' and not use_spotify:
            track = _deezer_isrc(fallback_client, code)
        else:
            return None
    except Exception as e:  # noqa: BLE001 - a failed lookup just means search
        logger.debug("isrc lookup failed for %s on %s: %s", code, source, e)
        return None
    if track is None:
        return None
    wanted = list(artists or [])
    if not artists_agree(wanted, getattr(track, 'artists', None) or []):
        logger.info("isrc %s on %s came back as %s by %s, not %s; searching instead",
                    code, source, getattr(track, 'name', '?'),
                    ', '.join(getattr(track, 'artists', None) or []), ', '.join(map(str, wanted)))
        return None
    return track
