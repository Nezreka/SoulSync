"""the public, keyless listenbrainz reads the last.fm fallbacks lean on.

- an artist's most played recordings (popularity)
- similar artists (the labs dataset)
- a user's top artists (stats)

no token: these are open endpoints on the official server. listenbrainz
sends X-RateLimit-* headers (about 30 calls a 10s window); every call here
goes through one gate that keeps a small gap and waits out an empty window.
results go through core.provider_cache.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

import requests

from core import provider_cache
from utils.logging_config import get_logger

logger = get_logger("listenbrainz_public")

API = "https://api.listenbrainz.org/1"
LABS = "https://labs.api.listenbrainz.org"
SIMILAR_ARTISTS_ALGORITHM = (
    "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30")

CATALOGUE_TTL = 30 * 86400
USER_TTL = 6 * 3600
MIN_GAP_SECONDS = 0.35
MAX_WINDOW_WAIT = 10.0

_gate = threading.Lock()
_next_at = [0.0]
_session = requests.Session()
_session.headers.update({"User-Agent": "SoulSync/1.0", "Accept": "application/json"})


def _get(url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 15) -> Any:
    """one paced GET. None on anything but a 200 with json."""
    with _gate:
        wait = _next_at[0] - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _next_at[0] = time.monotonic() + MIN_GAP_SECONDS
    try:
        response = _session.get(url, params=params, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.debug("listenbrainz %s failed: %s", url, exc)
        return None
    headers = getattr(response, "headers", None) or {}
    try:
        remaining = int(headers.get("X-RateLimit-Remaining", 1))
        reset_in = float(headers.get("X-RateLimit-Reset-In", 0))
    except (TypeError, ValueError):
        remaining, reset_in = 1, 0.0
    if remaining <= 0 or response.status_code == 429:
        with _gate:
            _next_at[0] = max(_next_at[0], time.monotonic() + min(MAX_WINDOW_WAIT, max(reset_in, 1.0)))
    if response.status_code != 200:
        logger.debug("listenbrainz %s answered %s", url, response.status_code)
        return None
    try:
        return response.json()
    except ValueError:
        return None


def artist_mbid(artist_name: str) -> Optional[str]:
    """the musicbrainz id for a name, through the shared musicbrainz service
    (its own cache + the 1 req/s musicbrainz gate)."""
    name = str(artist_name or "").strip()
    if not name:
        return None
    # an artist in the library usually has its id already (indexed name_key)
    conn = None
    try:
        from core.library2.importer import normalize_name
        from database.music_database import get_database
        conn = get_database()._get_connection()
        row = conn.execute(
            "SELECT musicbrainz_id FROM lib2_artists WHERE name_key = ? "
            "AND musicbrainz_id IS NOT NULL AND musicbrainz_id <> '' LIMIT 1",
            (normalize_name(name),)).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception as exc:  # noqa: BLE001
        logger.debug("library artist id lookup failed for %s: %s", name, exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 - cleanup only
                pass
    try:
        from core.musicbrainz_service import get_musicbrainz_service
        match = get_musicbrainz_service().match_artist(name) or {}
        return match.get("mbid") or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("musicbrainz artist lookup failed for %s: %s", name, exc)
        return None


def _top_recordings_rows(raw: Any, limit: int) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(raw, list):
        return None
    rows: List[Dict[str, Any]] = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("recording_name") or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        rows.append({
            "name": name,
            "playcount": int(item.get("total_listen_count") or 0),
            "listeners": int(item.get("total_user_count") or 0),
            "mbid": item.get("recording_mbid") or "",
            "album": item.get("release_name") or "",
            "url": f"https://listenbrainz.org/track/{item['recording_mbid']}/" if item.get("recording_mbid") else "",
        })
        if len(rows) >= limit:
            break
    return rows


def top_recordings_for_artist(mbid: str, limit: int = 50) -> List[Dict[str, Any]]:
    """the artist's most listened recordings, one row per title."""
    if not mbid:
        return []
    keep_n = max(limit, 50)

    def fetch():
        return _top_recordings_rows(_get(f"{API}/popularity/top-recordings-for-artist/{mbid}"), keep_n)

    rows, _how = provider_cache.cached("listenbrainz", "top-recordings-for-artist", {"mbid": mbid},
                                       CATALOGUE_TTL, fetch)
    return list(rows or [])[:limit]


def similar_artists(mbid: str, limit: int = 30) -> List[Dict[str, Any]]:
    """[{name, mbid, match}] from the labs similar-artists dataset."""
    if not mbid:
        return []

    def fetch():
        raw = _get(f"{LABS}/similar-artists/json",
                   {"artist_mbids": mbid, "algorithm": SIMILAR_ARTISTS_ALGORITHM}, timeout=20)
        if not isinstance(raw, list):
            return None
        top = max((float(r.get("score") or 0) for r in raw if isinstance(r, dict)), default=0.0) or 1.0
        return [{"name": r.get("name") or "", "mbid": r.get("artist_mbid") or "",
                 "match": round(float(r.get("score") or 0) / top, 4)}
                for r in raw if isinstance(r, dict) and r.get("name")]

    rows, _how = provider_cache.cached("listenbrainz", "similar-artists", {"mbid": mbid},
                                       CATALOGUE_TTL, fetch)
    return list(rows or [])[:limit]


def user_top_artists(username: str, limit: int = 200, range_: str = "all_time") -> List[Dict[str, Any]]:
    """[{name, playcount, mbid}] from the user's listenbrainz stats."""
    user = str(username or "").strip()
    if not user:
        return []
    count = max(1, min(int(limit or 100), 1000))

    def fetch():
        raw = _get(f"{API}/stats/user/{requests.utils.quote(user, safe='')}/artists",
                   {"range": range_, "count": count})
        if not isinstance(raw, dict):
            return None
        artists = ((raw.get("payload") or {}).get("artists")) or []
        return [{"name": a.get("artist_name") or "", "playcount": int(a.get("listen_count") or 0),
                 "mbid": a.get("artist_mbid") or ""}
                for a in artists if isinstance(a, dict) and a.get("artist_name")]

    rows, _how = provider_cache.cached("listenbrainz", "user-top-artists",
                                       {"user": user, "range": range_, "count": count}, USER_TTL, fetch)
    return list(rows or [])[:limit]
