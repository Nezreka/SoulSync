"""Single -> parent-album resolution.

When a track is matched to a SINGLE release (album_type 'single', the single's
name usually equal to the track title), it carries the single's name + the
single's source album id. The canonical grouping in
[core/imports/album_grouping.py] then files it under a different album row than
its album-mates, and the album-grouped repair jobs dress that row in the
single's art — songs of one album end up with different covers (Sokhi).

This module re-homes such a track onto the ALBUM it actually belongs to, so it
carries the album's name/id and groups with the rest of the album.

Design: the SELECTION is a pure, conservative function (no I/O), and the lookup
loop takes INJECTED fetchers, so both are unit-testable without a live metadata
client. CONSERVATIVE by intent — it only re-homes a track when a real
``album``-type release's tracklist *contains that exact track*. It never
promotes a genuine standalone single and never guesses, because a wrong
promotion would mis-home a real single onto an album (the inverse bug).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

_WS = re.compile(r"\s+")
# Trailing version qualifiers that differ between a single and its album cut but
# don't change track identity (kept conservative — only the obvious ones).
_QUALIFIER = re.compile(
    r"\s*[\(\[]\s*(album version|single version|radio edit|remaster(ed)?( \d{4})?)\s*[\)\]]\s*$",
    re.IGNORECASE,
)


def _norm(s: Any) -> str:
    """Lowercase, strip a trailing '(Album Version)'-style qualifier, collapse
    whitespace — so 'Song' matches 'Song (Album Version)'."""
    t = str(s or "").strip().lower()
    t = _QUALIFIER.sub("", t)
    return _WS.sub(" ", t).strip()


def _get(obj: Any, *keys: str, default=None):
    for k in keys:
        if isinstance(obj, dict):
            if obj.get(k) is not None:
                return obj.get(k)
        else:
            v = getattr(obj, k, None)
            if v is not None:
                return v
    return default


def select_parent_album(track_title: str, candidate_albums: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the parent ALBUM for ``track_title`` from normalized candidates, or
    None. Each candidate is ``{name, album_type, tracks: [title, ...], ...}``.

    Conservative rules — a candidate qualifies ONLY when:
      * it is an ``album`` release (never single / ep / compilation), and
      * its name is not just the track title (that IS the single), and
      * its tracklist contains the track by exact normalized title.
    Returns the FIRST qualifying candidate (caller passes them in priority
    order, so the result is deterministic).
    """
    tgt = _norm(track_title)
    if not tgt:
        return None
    for alb in candidate_albums or []:
        if str(_get(alb, "album_type", default="album")).lower() != "album":
            continue
        if _norm(_get(alb, "name", "title", default="")) == tgt:
            continue
        tracks = _get(alb, "tracks", default=[]) or []
        if any(_norm(t) == tgt for t in tracks):
            return alb
    return None


def resolve_single_to_album(
    track_title: str,
    *,
    fetch_album_candidates: Callable[[], List[Dict[str, Any]]],
    fetch_album_tracks: Callable[[Dict[str, Any]], List[str]],
    max_albums: int = 8,
) -> Optional[Dict[str, Any]]:
    """Find the parent album for a single-matched track. I/O is INJECTED so this
    is testable without a live client:
      * ``fetch_album_candidates()`` -> the artist's ALBUM-type releases (dicts
        with name/album_type/id/source), in priority order.
      * ``fetch_album_tracks(album)`` -> that album's track titles.
    Probes at most ``max_albums`` albums, lazily (stops at the first that
    contains the track). Fail-safe: any error / no confident match -> None
    (the track stays as it was matched). Returns the normalized winning album
    ``{name, album_type, album_id, source, tracks}`` or None.
    """
    if not _norm(track_title):
        return None
    try:
        albums = fetch_album_candidates() or []
    except Exception:
        return None

    probed = 0
    for alb in albums:
        if str(_get(alb, "album_type", default="album")).lower() != "album":
            continue
        if probed >= max_albums:
            break
        probed += 1
        try:
            tracks = fetch_album_tracks(alb) or []
        except Exception:
            continue
        normalized = {
            "name": _get(alb, "name", "title", default=""),
            "album_type": "album",
            "album_id": _get(alb, "id", "album_id"),
            "source": _get(alb, "source"),
            "tracks": list(tracks),
        }
        if select_parent_album(track_title, [normalized]):
            return normalized
    return None
