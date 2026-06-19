"""#889 Phase 3: search a metadata source for the releases a track appears on.

The Re-identify modal lets the user search ANY configured source (tabs, defaulting
to the active one) and shows the SAME song across its different collections —
single / EP / album — so they can pick which release the track should be filed
under. Two steps, deliberately split:

  * ``search_release_candidates(source, query)`` — lightweight DISPLAY rows from the
    normal typed ``search_tracks`` (title, artist, release name, type badge, year,
    track count, art, ISRC, track_id). No album_id needed to draw the list.
  * ``resolve_hint_fields(source, track_id)`` — runs ONCE, on the row the user
    picks: ``get_track_details`` yields the album_id / isrc / track#/disc the hint
    needs. We don't pay that lookup for every search result, only the chosen one.

Pure normalization + injected client factory, so the search/normalize/resolve seam
is unit-tested with a fake client and no network.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def _get(obj: Any, key: str, default=None):
    """Read ``key`` from either an object (attr) or a mapping (item)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _year(release_date: Any) -> Optional[str]:
    s = str(release_date or "").strip()
    return s[:4] if len(s) >= 4 and s[:4].isdigit() else None


def infer_release_type(album_type: Any, total_tracks: Any) -> str:
    """Normalize a source's release type to one of album / ep / single / compilation.

    Sources disagree: Spotify has no 'EP' — EPs come back as ``album_type='single'``
    with several tracks; MusicBrainz/Deezer label EPs properly. So when a 'single'
    carries more than a handful of tracks, call it an EP for the badge. The actual
    filing is unaffected — that's driven by the real album_id, not this label."""
    t = str(album_type or "").strip().lower()
    try:
        n = int(total_tracks) if total_tracks is not None else 0
    except (TypeError, ValueError):
        n = 0
    if t in ("compilation", "comp"):
        return "compilation"
    if t == "ep":
        return "ep"
    if t == "album":
        return "album"   # an explicit album stays an album; only 'single' gets promoted to EP
    if t == "single":
        # 1–3 tracks → single; 4+ → almost always an EP in practice.
        return "ep" if n >= 4 else "single"
    # Unknown type: infer purely from track count.
    if n >= 7:
        return "album"
    if n >= 4:
        return "ep"
    if n >= 1:
        return "single"
    return t or "album"


def normalize_search_result(result: Any, source: str) -> Optional[Dict[str, Any]]:
    """One typed search Track (or raw dict) → a display row, or ``None`` if it has
    no usable id/title. ``album`` is just a name at search time; album_id is
    resolved later for the picked row only."""
    track_id = _get(result, "id") or _get(result, "track_id")
    title = _get(result, "name") or _get(result, "title")
    if not track_id or not title:
        return None

    artists = _get(result, "artists")
    if isinstance(artists, list):
        artist_name = ", ".join(str(_get(a, "name", a) if not isinstance(a, str) else a) for a in artists)
    else:
        artist_name = str(artists or _get(result, "artist") or "")

    album = _get(result, "album")
    album_name = album if isinstance(album, str) else (_get(album, "name") or "")
    raw_type = _get(result, "album_type")
    total = _get(result, "total_tracks")
    ext = _get(result, "external_ids") or {}
    isrc = _get(result, "isrc") or (ext.get("isrc") if isinstance(ext, dict) else None)

    return {
        "source": source,
        "track_id": str(track_id),
        "track_title": str(title),
        "artist_name": artist_name,
        "album_name": str(album_name or ""),
        "album_type": infer_release_type(raw_type, total),
        "raw_album_type": str(raw_type or ""),
        "total_tracks": int(total) if isinstance(total, int) else None,
        "year": _year(_get(result, "release_date")),
        "image_url": _get(result, "image_url") or "",
        "isrc": isrc or None,
    }


def search_release_candidates(
    source: str,
    query: str,
    *,
    limit: int = 25,
    client_factory: Optional[Callable[[str], Any]] = None,
) -> List[Dict[str, Any]]:
    """Search ``source`` for tracks matching ``query`` → normalized display rows.

    Returns ``[]`` (never raises) when the source has no client or errors — the UI
    just shows an empty tab. Rows keep duplicate releases; the UI groups them."""
    query = (query or "").strip()
    if not query:
        return []
    factory = client_factory or _default_client_factory
    try:
        client = factory(source)
    except Exception:
        client = None
    if client is None or not hasattr(client, "search_tracks"):
        return []
    try:
        results = client.search_tracks(query, limit=limit)
    except TypeError:
        results = client.search_tracks(query)   # clients with no limit kwarg
    except Exception:
        return []

    rows: List[Dict[str, Any]] = []
    for r in results or []:
        row = normalize_search_result(r, source)
        if row is not None:
            rows.append(row)
    return rows


def resolve_hint_fields(
    source: str,
    track_id: str,
    *,
    client_factory: Optional[Callable[[str], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve the picked track to the fields a hint needs (album_id critically,
    plus isrc / track# / disc# / album name+type). One lookup for one chosen row.
    Returns ``None`` if it can't be resolved (caller surfaces an error)."""
    factory = client_factory or _default_client_factory
    try:
        client = factory(source)
    except Exception:
        client = None
    if client is None or not hasattr(client, "get_track_details"):
        return None
    try:
        details = client.get_track_details(track_id)
    except Exception:
        return None
    if not details:
        return None

    album = _get(details, "album") or {}
    album_id = _get(album, "id") if not isinstance(album, str) else None
    album_name = _get(album, "name") if not isinstance(album, str) else album
    album_type = _get(album, "album_type") or _get(details, "album_type")
    total = _get(album, "total_tracks") or _get(details, "total_tracks")

    artists = _get(details, "artists") or []
    artist_id = None
    artist_name = ""
    if isinstance(artists, list) and artists:
        artist_id = _get(artists[0], "id")
        artist_name = ", ".join(str(_get(a, "name", a) if not isinstance(a, str) else a) for a in artists)

    ext = _get(details, "external_ids") or {}
    isrc = _get(details, "isrc") or (ext.get("isrc") if isinstance(ext, dict) else None)

    if not album_id:
        return None   # without an album_id the import can't fetch the tracklist

    return {
        "source": source,
        "track_id": str(track_id),
        "album_id": str(album_id),
        "artist_id": str(artist_id) if artist_id else None,
        "track_title": _get(details, "name") or _get(details, "title") or "",
        "album_name": str(album_name or ""),
        "artist_name": artist_name,
        "album_type": infer_release_type(album_type, total),
        "track_number": _get(details, "track_number"),
        "disc_number": _get(details, "disc_number") or 1,
        "isrc": isrc or None,
    }


def _default_client_factory(source: str):
    from core.metadata.registry import get_client_for_source
    return get_client_for_source(source)


def available_sources() -> List[Dict[str, Any]]:
    """The source tabs for the modal: every metadata source with a live client,
    the primary one flagged ``active`` so the UI selects it by default."""
    from core.metadata.registry import (
        METADATA_SOURCE_PRIORITY,
        get_client_for_source,
        get_primary_source,
    )

    try:
        primary = get_primary_source()
    except Exception:
        primary = None

    out: List[Dict[str, Any]] = []
    seen = set()
    for src in METADATA_SOURCE_PRIORITY:
        if src in seen:
            continue
        seen.add(src)
        try:
            client = get_client_for_source(src)
        except Exception:
            client = None
        if client is None or not hasattr(client, "search_tracks"):
            continue
        out.append({
            "source": src,
            "label": src.replace("_", " ").title(),
            "active": src == primary,
        })
    # Guarantee the primary is selectable + first even if priority ordering missed it.
    if primary and not any(s["active"] for s in out):
        out.insert(0, {"source": primary, "label": primary.replace("_", " ").title(), "active": True})
    return out


__all__ = [
    "infer_release_type",
    "normalize_search_result",
    "search_release_candidates",
    "resolve_hint_fields",
    "available_sources",
]
