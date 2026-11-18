"""Which MusicBrainz release statuses an artist's releases may have (#2175).

MusicBrainz gives every concrete release a status: Official, Promotion,
Bootleg, Pseudo-Release, Withdrawn, Expunged or Cancelled. A watchlist can
keep the ones you don't want off the wishlist, library wide
(``watchlist.global_release_statuses``) with an override per artist
(``watchlist_artists.allowed_release_statuses``, NULL = follow the library).

The status sits on the release, not on the release group a discography
lists, so a group passes when any of its releases has a status you allow.
A group with no status to go by passes too: one whose releases carry no
status, one MusicBrainz never answered for, and every release from a source
that has no statuses at all (Spotify, Deezer, ...). The filter only ever
keeps out what MusicBrainz says is something you don't want.

Every status is allowed by default, so nothing changes until you pick.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Iterable, Mapping, Optional, Set, Tuple

from utils.logging_config import get_logger

logger = get_logger("release_statuses")

# MusicBrainz's statuses, in its own order, lower-cased
RELEASE_STATUSES: Tuple[str, ...] = (
    "official", "promotion", "bootleg", "pseudo-release",
    "withdrawn", "expunged", "cancelled",
)
STATUS_LABELS: Dict[str, str] = {
    "official": "Official",
    "promotion": "Promotion",
    "bootleg": "Bootleg",
    "pseudo-release": "Pseudo-Release",
    "withdrawn": "Withdrawn",
    "expunged": "Expunged",
    "cancelled": "Cancelled",
}
CONFIG_KEY = "watchlist.global_release_statuses"
ARTIST_COLUMN = "allowed_release_statuses"
ALBUM_COLUMN = "release_statuses"
# one browse page is 100 releases at one request a second: an artist with
# thousands of bootlegs stops here, and the groups it never reached pass
MAX_BROWSE_PAGES = 30


class ReleaseStatusError(ValueError):
    pass


def _status_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-").replace(" ", "-")


def parse_statuses(value: Any, *, strict: bool = False) -> Optional[Tuple[str, ...]]:
    """A list of statuses from a list, or as stored (a JSON list), in
    MusicBrainz's order. None for None (not set). ``strict`` is for what a
    user sent: only a real list, and every status in it a real one."""
    if value is None:
        return None
    raw = value
    if strict and not isinstance(raw, (list, tuple)):
        raise ReleaseStatusError("release statuses must be a list")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ()
        try:
            raw = json.loads(text)
        except (TypeError, ValueError):
            raw = text.split(",")
    if not isinstance(raw, (list, tuple, set, frozenset)):
        if strict:
            raise ReleaseStatusError("release statuses must be a list")
        return None
    wanted = set()
    for item in raw:
        key = _status_key(item)
        if key in RELEASE_STATUSES:
            wanted.add(key)
        elif strict:
            raise ReleaseStatusError(f"unknown release status: {item}")
    return tuple(status for status in RELEASE_STATUSES if status in wanted)


def validate_allowed(value: Any) -> Tuple[str, ...]:
    """The statuses a user picked, or ReleaseStatusError. At least one."""
    statuses = parse_statuses(value, strict=True)
    if not statuses:
        raise ReleaseStatusError("pick at least one release status")
    return statuses


def encode(statuses: Iterable[str]) -> str:
    return json.dumps(list(statuses))


def library_statuses(config_manager: Any) -> Tuple[str, ...]:
    """The library-wide statuses. Never set, or set to nothing: all of them."""
    try:
        raw = config_manager.get(CONFIG_KEY, None) if config_manager is not None else None
    except Exception:  # noqa: BLE001 - a config hiccup reads as every status
        raw = None
    statuses = parse_statuses(raw)
    return statuses if statuses else RELEASE_STATUSES


def artist_override(artist: Any) -> Optional[Tuple[str, ...]]:
    """An artist's own statuses as stored, None when it follows the library."""
    if artist is None:
        return None
    if isinstance(artist, Mapping):
        raw = artist.get(ARTIST_COLUMN)
    else:
        try:
            raw = artist[ARTIST_COLUMN]
        except (KeyError, IndexError, TypeError):
            raw = getattr(artist, ARTIST_COLUMN, None)
    statuses = parse_statuses(raw)
    return statuses if statuses else None


def effective_statuses(artist: Any, config_manager: Any) -> Tuple[str, ...]:
    """The statuses a scan lets through for this artist."""
    return artist_override(artist) or library_statuses(config_manager)


def is_restrictive(allowed: Optional[Iterable[str]]) -> bool:
    """Does this keep anything out? Only then is MusicBrainz asked."""
    if allowed is None:
        return False
    return not set(RELEASE_STATUSES) <= {_status_key(s) for s in allowed}


def statuses_pass(release_statuses: Optional[Iterable[str]],
                  allowed: Optional[Iterable[str]]) -> bool:
    """Does a release group with these statuses get through?

    No statuses known (None or empty) passes, so does any one allowed.
    A status MusicBrainz adds later that this list doesn't know passes too."""
    if not is_restrictive(allowed):
        return True
    known = {_status_key(s) for s in (release_statuses or ()) if _status_key(s)}
    if not known:
        return True
    allowed_keys = {_status_key(s) for s in allowed or ()}
    return any(status in allowed_keys or status not in RELEASE_STATUSES for status in known)


# -- MusicBrainz ---------------------------------------------------------------

def _raw_mb_client():
    from core.metadata.registry import get_musicbrainz_client
    client = get_musicbrainz_client()
    return getattr(client, "_client", client)


_CACHE_TTL = 24 * 3600
_cache: Dict[str, Tuple[float, Optional[Dict[str, Set[str]]]]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def fetch_group_statuses(artist_mbid: str, *, client: Any = None,
                         max_pages: int = MAX_BROWSE_PAGES) -> Optional[Dict[str, Set[str]]]:
    """:func:`_fetch_group_statuses`, remembered for a day per artist: a
    watchlist scan asks for every artist, and a big one is up to 30
    rate-limited pages. only a full answer is kept."""
    mbid = str(artist_mbid or "").strip().lower()
    if not mbid:
        return None
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(mbid)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    groups = _fetch_group_statuses(mbid, client=client, max_pages=max_pages)
    if groups is not None:
        with _cache_lock:
            _cache[mbid] = (now, groups)
    return groups


def _fetch_group_statuses(artist_mbid: str, *, client: Any = None,
                          max_pages: int = MAX_BROWSE_PAGES) -> Optional[Dict[str, Set[str]]]:
    """Every release group of an artist's releases on MusicBrainz, with the
    statuses its releases have. A release with no status adds nothing, so a
    group of only those maps to an empty set. None when MusicBrainz could
    not be asked: everything then passes."""
    mbid = str(artist_mbid or "").strip()
    if not mbid:
        return None
    try:
        client = client or _raw_mb_client()
    except Exception as exc:  # noqa: BLE001
        logger.debug("no MusicBrainz client for release statuses: %s", exc)
        return None
    browse = getattr(client, "browse_artist_releases", None)
    if not callable(browse):
        return None
    groups: Dict[str, Set[str]] = {}
    offset = 0
    for _ in range(max(1, int(max_pages))):
        try:
            page = browse(mbid, limit=100, offset=offset)
        except Exception as exc:  # noqa: BLE001
            logger.debug("release status browse failed for %s: %s", mbid, exc)
            page = None
        if page is None:
            # a half answer can't be filtered by: a group's official release
            # may sit on a page we never read. everything passes instead
            return None
        for release in page:
            if not isinstance(release, Mapping):
                continue
            group = release.get("release-group") or {}
            group_id = str(group.get("id") or "").strip().lower() if isinstance(group, Mapping) else ""
            if not group_id:
                continue
            statuses = groups.setdefault(group_id, set())
            status = _status_key(release.get("status"))
            if status:
                statuses.add(status)
        if len(page) < 100:
            break
        offset += 100
    else:
        # same as an error: past the cap a group's other releases are unread
        logger.info("release statuses for %s stopped after %s pages, not filtering by status",
                    mbid, max_pages)
        return None
    return groups


# -- lib2_albums ---------------------------------------------------------------

def stored_statuses(raw: Any, group_id: Any) -> Optional[Tuple[str, ...]]:
    """The statuses kept for an album, None when they are not known for the
    release group it has now (same shape as release_group_types)."""
    current = str(group_id or "").strip().lower()
    if not raw or not current:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(data, Mapping) or str(data.get("group") or "").strip().lower() != current:
        return None
    statuses = data.get("statuses")
    if not isinstance(statuses, list):
        return None
    return tuple(_status_key(s) for s in statuses if _status_key(s))


def stamp(conn: Any, album_id: Any, group_id: Any, statuses: Optional[Iterable[str]]) -> bool:
    """Keep the statuses MusicBrainz gave for ``group_id`` on the album."""
    group = str(group_id or "").strip().lower()
    if statuses is None or not group or not album_id:
        return False
    value = json.dumps({"group": group, "statuses": sorted({_status_key(s) for s in statuses})},
                       sort_keys=True)
    cur = conn.execute(
        f"UPDATE lib2_albums SET {ALBUM_COLUMN}=? WHERE id=? AND {ALBUM_COLUMN} IS NOT ?",
        (value, int(album_id), value))
    return bool(cur.rowcount)


__all__ = [
    "RELEASE_STATUSES", "STATUS_LABELS", "CONFIG_KEY", "ARTIST_COLUMN", "ALBUM_COLUMN",
    "ReleaseStatusError", "parse_statuses", "validate_allowed", "encode",
    "library_statuses", "artist_override", "effective_statuses", "is_restrictive",
    "statuses_pass", "fetch_group_statuses", "stored_statuses", "stamp",
]
