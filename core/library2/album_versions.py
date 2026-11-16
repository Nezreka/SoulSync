"""Other versions of an album: list a release's editions and switch to one (#2145).

picard's "other versions" menu, on the library album page. the editions come
from where they already live:

- musicbrainz: every release in the album's release group (one browse call).
  that is what an edition is there: country, label, format, track count.
- the primary metadata source: the artist's releases with the same title
  ("Good Time", "Good Time (Deluxe)"), from one artist-albums call.

switching is not a new path. it is the manual album match the Matched via
chips already do (#758): the release id is set and pinned, so the tracklist,
completeness, track-number repair and reorganize all read the picked edition.
the files are not touched here; Preview Retag and Rename / Organize do that
from the pinned release, same as after any manual match.

provider calls are cached for an hour so reopening the picker is free.
"""

from __future__ import annotations

import re
import threading
import time
from contextlib import closing
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.album_versions")

CACHE_TTL_SECONDS = 3600
_CACHE_MAX = 256
_cache: Dict[tuple, tuple] = {}
_cache_lock = threading.Lock()


def _cached(key: tuple, fetch: Callable[[], Any]) -> Any:
    """``fetch()`` once per key per hour. failures are not cached."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = fetch()
    if value is None:
        return None
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = (now + CACHE_TTL_SECONDS, value)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if value not in (None, "", [], {}):
            return value
    return default


def _title_key(text: Any) -> str:
    """title with bracketed qualifiers and edition words dropped. non latin
    titles keep their letters (the resolver's key drops them to nothing)."""
    from core.imports.reassign_album import normalize_title
    from core.metadata.canonical_resolver import _release_name_key
    key = _release_name_key(str(text or ""))
    if key:
        return key
    return normalize_title(re.sub(r"[\(\[].*?[\)\]]", " ", str(text or "")))


def same_title(a: Any, b: Any) -> bool:
    left, right = _title_key(a), _title_key(b)
    return bool(left) and left == right


def _artist_names(item: Any) -> List[str]:
    raw = _attr(item, "artists", default=None)
    names: List[str] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            name = entry.get("name") if isinstance(entry, dict) else entry
            if name:
                names.append(str(name))
    single = _attr(item, "artist", "artist_name", default=None)
    if isinstance(single, str) and single:
        names.append(single)
    return names


def _same_artist(item: Any, artist_name: str) -> bool:
    from core.imports.reassign_album import normalize_title
    want = normalize_title(artist_name)
    if not want:
        return True
    names = _artist_names(item)
    if not names:
        return True  # nothing to compare: the search was already by artist
    # a credit string is split into its people: "drake feat. wizkid" is drake,
    # "drake bell" is not
    parts = [normalize_title(p) for n in names for p in (n, *_CREDIT_SPLIT.split(n))]
    return want in parts


_CREDIT_SPLIT = re.compile(
    r"\s*(?:,|&|;|/|\+|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b|\bx\b|\band\b)\s*", re.IGNORECASE)


def version_row(source: str, item: Any, current_ids: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """one provider release as a picker row."""
    release_id = _attr(item, "id", "album_id")
    if not release_id:
        return None
    date = str(_attr(item, "release_date", "date", default="") or "")
    year = _attr(item, "year", default=None) or (date[:4] if date[:4].isdigit() else None)
    try:
        tracks = int(_attr(item, "total_tracks", "track_count", "nb_tracks", default=0) or 0)
    except (TypeError, ValueError):
        tracks = 0
    return {
        "source": source,
        "id": str(release_id),
        "title": str(_attr(item, "name", "title", default="") or ""),
        "date": date or None,
        "year": int(year) if str(year or "").isdigit() else None,
        "track_count": tracks or None,
        "label": _attr(item, "label", default=None),
        "country": _attr(item, "country", default=None),
        "format": _attr(item, "format", default=None),
        "disambiguation": _attr(item, "disambiguation", default=None),
        "status": _attr(item, "status", default=None),
        "album_type": _attr(item, "album_type", default=None),
        "image_url": _attr(item, "image_url", default=None),
        "current": str(current_ids.get(source) or "") == str(release_id),
    }


def album_facts(conn: Any, album_id: int) -> Optional[Dict[str, Any]]:
    """what the picker needs to know about the album row."""
    from core.library2.editions import album_release_ids
    from core.library2.provider_ids import provider_only, source_ids_from_values

    row = conn.execute(
        "SELECT al.*, ar.name AS artist_name, ar.spotify_id AS artist_spotify_id,"
        " ar.musicbrainz_id AS artist_musicbrainz_id, ar.external_ids AS artist_external_ids"
        " FROM lib2_albums al LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id"
        " WHERE al.id = ?", (int(album_id),)).fetchone()
    if row is None:
        return None
    keys = row.keys()
    artist_ids = provider_only(source_ids_from_values(
        spotify_id=row["artist_spotify_id"], musicbrainz_id=row["artist_musicbrainz_id"],
        external_ids=row["artist_external_ids"]))
    track_count = None
    for column in ("expected_track_count", "track_count"):
        if column in keys and row[column]:
            track_count = int(row[column])
            break
    pinned = None
    if "canonical_locked" in keys and row["canonical_locked"] and row["canonical_source"]:
        pinned = {"source": str(row["canonical_source"]).lower(),
                  "id": str(row["canonical_album_id"] or "")}
    return {
        "id": int(row["id"]),
        "title": row["title"] or "",
        "artist_name": row["artist_name"] or "",
        "artist_ids": artist_ids,
        "release_ids": provider_only(album_release_ids(row)),
        "release_group_id": (row["musicbrainz_release_group_id"]
                             if "musicbrainz_release_group_id" in keys else None) or None,
        "track_count": track_count,
        "pinned": pinned,
    }


# --- default provider calls (tests inject their own) -------------------------

def _mb_search_client():
    from core.metadata.registry import get_musicbrainz_client
    return get_musicbrainz_client()


def fetch_release_group_editions(release_group_id: str, artist_name: str) -> Optional[list]:
    client = _mb_search_client()
    if client is None or not hasattr(client, "release_group_editions"):
        return None
    return client.release_group_editions(release_group_id, artist_name) or []


def fetch_release_group_id(release_id: str) -> Optional[str]:
    from core.metadata.art_lookup import _resolve_release_group_mbid
    return _resolve_release_group_mbid(release_id)


def fetch_artist_albums(source: str, artist_id: str, artist_name: str) -> Optional[list]:
    from core.metadata.album_tracks import get_artist_albums_for_source
    return get_artist_albums_for_source(source, artist_id, artist_name,
                                        album_type="album,single", limit=50)


def search_source_albums(source: str, query: str) -> Optional[list]:
    from core.metadata.registry import get_client_for_source
    client = get_client_for_source(source)
    if client is None or not hasattr(client, "search_albums"):
        return None
    return client.search_albums(query, limit=20) or []


def primary_source() -> str:
    from core.metadata.registry import get_primary_source
    return str(get_primary_source() or "").lower()


def _source_label(source: str) -> str:
    try:
        from core.metadata.registry import get_metadata_source_label
        return get_metadata_source_label(source) or source
    except Exception as exc:  # noqa: BLE001 - a label is cosmetic
        logger.debug("no label for %s: %s", source, exc)
        return source


def _sort(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: (not r["current"], r["date"] or "9999", r["track_count"] or 9999))


def list_versions(
    database: Any,
    album_id: int,
    *,
    source: Optional[str] = None,
    editions: Callable[[str, str], Optional[list]] = None,
    group_of: Callable[[str], Optional[str]] = None,
    artist_albums: Callable[[str, str, str], Optional[list]] = None,
) -> Optional[Dict[str, Any]]:
    """the album's other versions, grouped by source. None when the album is gone."""
    editions = editions or fetch_release_group_editions
    group_of = group_of or fetch_release_group_id
    artist_albums = artist_albums or fetch_artist_albums
    with closing(database._get_connection()) as conn:
        facts = album_facts(conn, album_id)
    if facts is None:
        return None
    current = facts["release_ids"]
    groups: List[Dict[str, Any]] = []

    # musicbrainz release group: the editions picard lists
    rg = facts["release_group_id"]
    if not rg and current.get("musicbrainz"):
        rg = _cached(("mb-rg-of", current["musicbrainz"]), lambda: group_of(current["musicbrainz"]))
    mb = {"source": "musicbrainz", "label": "MusicBrainz", "versions": []}
    if rg:
        try:
            found = _cached(("mb-editions", rg), lambda: editions(rg, facts["artist_name"]))
        except Exception as exc:  # noqa: BLE001 - one source failing is not the picker failing
            logger.debug("release group %s editions failed: %s", rg, exc)
            found, mb["error"] = None, "MusicBrainz didn't answer, try again in a bit"
        rows = [version_row("musicbrainz", item, current) for item in (found or [])]
        mb["versions"] = _sort([r for r in rows if r])
    else:
        mb["note"] = "No MusicBrainz release group is known for this album yet"
    groups.append(mb)

    # the primary source: same title by the same artist
    primary = str(source or primary_source() or "").lower()
    if primary and primary != "musicbrainz":
        group = {"source": primary, "label": _source_label(primary), "versions": []}
        artist_id = facts["artist_ids"].get(primary, "")
        try:
            found = _cached(("artist-albums", primary, artist_id or facts["artist_name"].casefold()),
                            lambda: artist_albums(primary, artist_id, facts["artist_name"]))
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s artist albums failed: %s", primary, exc)
            found, group["error"] = None, f"{group['label']} didn't answer, try again in a bit"
        rows = [version_row(primary, item, current) for item in (found or [])
                if same_title(facts["title"], _attr(item, "name", "title", default=""))]
        group["versions"] = _sort([r for r in rows if r])
        groups.insert(0, group)

    return {"album": {k: facts[k] for k in ("id", "title", "artist_name", "track_count",
                                            "release_ids", "pinned")},
            "groups": groups}


def search_versions(
    database: Any,
    album_id: int,
    query: str,
    *,
    source: Optional[str] = None,
    search: Callable[[str, str], Optional[list]] = None,
) -> Optional[Dict[str, Any]]:
    """a typed search for a version the lists don't have, kept to this artist."""
    search = search or search_source_albums
    with closing(database._get_connection()) as conn:
        facts = album_facts(conn, album_id)
    if facts is None:
        return None
    text = str(query or "").strip() or facts["title"]
    artist = facts["artist_name"]
    current = facts["release_ids"]
    primary = str(source or primary_source() or "").lower()
    sources = [s for s in dict.fromkeys([primary, "musicbrainz"]) if s]
    groups = []
    for name in sources:
        # musicbrainz reads "artist - title" as a structured query
        q = f"{artist} - {text}" if name == "musicbrainz" else f"{artist} {text}"
        group = {"source": name, "label": "MusicBrainz" if name == "musicbrainz" else _source_label(name),
                 "versions": []}
        try:
            found = search(name, q) or []
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s version search failed: %s", name, exc)
            found, group["error"] = [], f"{group['label']} search failed"
        rows = [version_row(name, item, current) for item in found if _same_artist(item, artist)]
        group["versions"] = [r for r in rows if r]
        groups.append(group)
    return {"groups": groups}


def preview_version(
    database: Any,
    album_id: int,
    source: str,
    release_id: str,
    *,
    tracklist: Callable[[str, str], List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """what switching would change: the track count, how the files on disk line
    up against the new tracklist, and whether another album holds the release."""
    from core.imports.reassign_album import build_reassign_plan
    from core.imports.reassign_service import local_album_tracks, target_tracks
    from core.library2.match_status import provider_id_owner
    from core.metadata.canonical_version import CANONICAL_ALBUM_SOURCES

    source = str(source or "").strip().lower()
    release_id = str(release_id or "").strip()
    if source not in CANONICAL_ALBUM_SOURCES:
        return {"success": False, "error": f"Versions from {source or 'that source'} can't be pinned"}
    if not release_id:
        return {"success": False, "error": "Pick a version first"}
    tracklist = tracklist or target_tracks
    with closing(database._get_connection()) as conn:
        facts = album_facts(conn, album_id)
        if facts is None:
            return {"success": False, "error": "That album is gone"}
        owner = provider_id_owner(conn, "album", source, release_id)
        held_by = None
        if owner is not None and int(owner) != int(album_id):
            other = conn.execute("SELECT title FROM lib2_albums WHERE id=?", (int(owner),)).fetchone()
            held_by = {"id": int(owner), "title": other[0] if other else ""}
    if facts["release_ids"].get(source) == release_id:
        return {"success": False, "already_current": True,
                "error": "This album already points at that version"}
    targets = tracklist(source, release_id) or []
    if not targets:
        return {"success": False, "error": "Couldn't read that version's tracklist"}
    locals_ = local_album_tracks(database, f"lib2:{int(album_id)}")
    plan = build_reassign_plan(locals_, targets) if locals_ else None
    pairings = [{
        "local_title": p.local_title,
        "local_track_number": p.local_track_number,
        "target_title": p.target_title,
        "target_track_number": p.target_track_number,
        "matched_by": p.matched_by,
        "mapped": p.mapped,
    } for p in (plan.pairings if plan else [])]
    return {
        "success": True,
        "source": source,
        "release_id": release_id,
        "track_count_before": facts["track_count"],
        "track_count_after": len(targets),
        "owned_count": len(locals_),
        "mapped_count": len(plan.mapped) if plan else 0,
        "unmapped_count": len(plan.unmapped) if plan else 0,
        "pairings": pairings,
        "tracks": [{"title": t.get("name"), "track_number": t.get("track_number"),
                    "disc_number": t.get("disc_number")} for t in targets],
        "held_by": held_by,
    }


def apply_version(database: Any, album_id: int, source: str, release_id: str, *,
                  actor: str) -> Dict[str, Any]:
    """point the album at the picked version: the manual album match (#758),
    which sets the release id and pins it."""
    from core.library2.match_status import apply_manual_match
    from core.metadata.canonical_version import CANONICAL_ALBUM_SOURCES

    source = str(source or "").strip().lower()
    release_id = str(release_id or "").strip()
    if source not in CANONICAL_ALBUM_SOURCES or not release_id:
        return {"success": False, "error": "Pick a version from the list"}
    conn = database._get_connection()
    try:
        if conn.execute("SELECT 1 FROM lib2_albums WHERE id=?", (int(album_id),)).fetchone() is None:
            return {"success": False, "error": "That album is gone"}
        apply_manual_match(conn, "album", int(album_id), source, release_id, actor=actor)
        conn.commit()
    except (LookupError, ValueError) as exc:
        conn.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()
    return {"success": True, "source": source, "release_id": release_id}


__all__ = [
    "list_versions", "search_versions", "preview_version", "apply_version",
    "version_row", "same_title", "album_facts", "clear_cache",
]
