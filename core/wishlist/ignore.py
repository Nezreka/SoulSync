"""Wishlist ignore-list — a TTL'd skip-gate for the wishlist (#874).

When a user removes a track from the wishlist or cancels an in-flight
wishlist download, SoulSync would otherwise re-add it on the next
automatic cycle (watchlist scan, failed-track capture, or the cancel
handler's own re-add), so the same release downloads → fails/cancels →
re-queues forever. The ignore list records the user's "stop
auto-grabbing this" intent, and the wishlist *add* path checks it,
skipping automatic re-adds until the entry ages out.

It is deliberately softer than the blocklist:
  - it **expires** after ``IGNORE_TTL_DAYS`` so the track is re-attempted
    again later rather than banned forever, and
  - it **never blocks a manual force-download** — only the automatic
    re-queue. (Manual downloads don't go through ``add_to_wishlist`` at
    all, and an explicit manual *add* both bypasses the gate and clears
    any existing ignore for the track.)

This module is pure decision logic — no database handle and no clock of
its own; the caller passes ``now``. The SQL lives in ``MusicDatabase``
as a thin wrapper around these helpers, which keeps the TTL / id-matching
rules unit-testable without a database.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

IGNORE_TTL_DAYS = 30

# Recognised reasons (free-text tolerated; these are the canonical two).
REASON_REMOVED = "removed"
REASON_CANCELLED = "cancelled"


def normalize_ignore_id(track_id: Any) -> str:
    """Canonical key for a wishlist track id.

    The wishlist stores some ids as a composite ``<track_id>::<album_id>``
    (when ``wishlist.allow_duplicate_tracks`` is on). The add-path gate
    keys on the bare track id (``spotify_track_data['id']``), so we strip
    the ``::album`` suffix here so an ignore recorded from a composite-id
    wishlist row still matches the bare-id add attempt, and vice-versa.
    Returns ``''`` for falsy/blank input.
    """
    s = str(track_id or "").strip()
    if not s:
        return ""
    return s.split("::", 1)[0]


def _parse_ts(value: Any) -> Optional[datetime]:
    """Best-effort parse of a sqlite TIMESTAMP / ISO string / datetime."""
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    s = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def is_expired(created_at: Any, now: datetime, ttl_days: int = IGNORE_TTL_DAYS) -> bool:
    """True when an ignore entry created at ``created_at`` has aged past TTL.

    Fail-SAFE in the gate's favour: an unparseable/blank timestamp is
    treated as **expired** (returns True) so a corrupt row can never wedge
    a track out of the wishlist permanently — the worst case is the
    ignore silently lapses and the track becomes eligible again.
    """
    created = _parse_ts(created_at)
    if created is None:
        return True
    return now >= created + timedelta(days=ttl_days)


def active_ignored_ids(
    rows: Iterable[Dict[str, Any]], now: datetime, ttl_days: int = IGNORE_TTL_DAYS
) -> Set[str]:
    """Set of normalized track ids whose ignore entry is still within TTL."""
    out: Set[str] = set()
    for row in rows or []:
        tid = normalize_ignore_id(row.get("track_id"))
        if tid and not is_expired(row.get("created_at"), now, ttl_days):
            out.add(tid)
    return out


def is_ignored(
    rows: Iterable[Dict[str, Any]], track_id: Any, now: datetime, ttl_days: int = IGNORE_TTL_DAYS
) -> bool:
    """Whether ``track_id`` matches an in-TTL entry among ``rows``."""
    key = normalize_ignore_id(track_id)
    if not key:
        return False
    return key in active_ignored_ids(rows, now, ttl_days)


def extract_display(data: Any) -> Tuple[str, str]:
    """Pull a (track_name, artist_name) pair from a Spotify-shaped dict.

    Tolerates ``artists`` as a list of dicts or bare strings, and missing
    fields. Used to give ignore-list rows a human label for the UI.
    Returns ``('', '')`` when nothing usable is present.
    """
    if not isinstance(data, dict):
        return "", ""
    name = str(data.get("name") or "").strip()
    artist = ""
    artists = data.get("artists") or []
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict):
            artist = str(first.get("name") or "").strip()
        else:
            artist = str(first or "").strip()
    return name, artist


def ignore_wishlist_track(
    database: Any,
    profile_id: int,
    track_id: Any,
    reason: str,
    spotify_data: Any = None,
) -> bool:
    """Record an ignore entry for a wishlist track. Best-effort; never raises.

    Copies the track's display name/artist from ``spotify_data`` when
    provided (callers should capture it BEFORE removing the wishlist row,
    since the row may be gone afterwards); otherwise leaves them blank.
    Returns True when an entry was written.
    """
    key = normalize_ignore_id(track_id)
    if not key or database is None:
        return False
    name, artist = extract_display(spotify_data or {})
    try:
        return bool(
            database.add_to_wishlist_ignore(
                key,
                track_name=name,
                artist_name=artist,
                reason=reason or REASON_REMOVED,
                profile_id=profile_id,
            )
        )
    except Exception:
        return False
