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

#2178: the album row takes the picked edition's title, type, date and track
count too. before, the picker marked "Country Grammar" current on a row still
called "(Hot S**t) Country Grammar" and filed as an EP. and when another album
of the artist already is that edition (one copy of an album split over two
rows), picking it offers to merge the two. moving the id across left the
split where it was.

provider calls are cached for an hour so reopening the picker is free.
"""

from __future__ import annotations

import json
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
    title: Optional[str] = None,
    tracklist: Callable[[str, str], List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """what switching would change: the track count, how the files on disk line
    up against the new tracklist, whether another album holds the release, and
    which albums of this artist it would merge with (#2178). ``title`` is the
    version's title, from the list the picker showed.

    the version the album already points at previews too: applying it puts
    the edition's title, type and date on the row, and merges a split."""
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
        merge_with = merge_candidates(conn, album_id, source, release_id, title=title)
    already_current = facts["release_ids"].get(source) == release_id
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
        "already_current": already_current,
        "merge_with": merge_with,
    }


def _owned_files(conn: Any, album_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(DISTINCT t.id) FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id = t.id"
        " WHERE t.album_id = ? AND COALESCE(f.file_state, 'active') = 'active'",
        (int(album_id),)).fetchone()
    return int(row[0] or 0) if row else 0


def merge_candidates(conn: Any, album_id: int, source: str, release_id: str, *,
                     title: Optional[str] = None) -> List[Dict[str, Any]]:
    """albums of this album's artist that are the picked edition too (#2178).

    - the album that already holds the release id, when it is by the same
      artist. switching used to move the id over, so the files stayed split
      over two rows and the other album lost its match.
    - an album of the same title that holds no release of this source. it has
      nothing that says it is another edition. one holding a different
      release ("Views" next to "Views (Deluxe)") is left alone: two editions
      kept apart on purpose look exactly like that.

    ``holds`` says which one already is the edition: that one stays and the
    other folds into it. ``locked`` (#2046): a reviewed album isn't merged
    away from another album's page.
    """
    from core.library2 import locks
    from core.library2.editions import album_release_ids
    from core.library2.match_status import provider_id_owner
    from core.library2.provider_ids import provider_only

    here = conn.execute("SELECT id, primary_artist_id, title FROM lib2_albums WHERE id=?",
                        (int(album_id),)).fetchone()
    if here is None or here["primary_artist_id"] is None:
        return []
    titles = [t for t in (title, here["title"]) if str(t or "").strip()]
    owner = provider_id_owner(conn, "album", source, release_id)
    out: List[Dict[str, Any]] = []
    for row in conn.execute(
            "SELECT * FROM lib2_albums WHERE primary_artist_id=? AND id<>? ORDER BY id",
            (int(here["primary_artist_id"]), int(album_id))).fetchall():
        holds = owner is not None and int(owner) == int(row["id"])
        if not holds:
            if not any(same_title(row["title"], t) for t in titles):
                continue
            if provider_only(album_release_ids(row)).get(source):
                continue
        out.append({
            "id": int(row["id"]),
            "title": row["title"] or "",
            "holds": holds,
            "owned_count": _owned_files(conn, int(row["id"])),
            "track_count": int(row["track_count"] or 0) or None,
            "locked": locks.is_locked("album", int(row["id"]), conn=conn),
        })
    # the holder first: it is the one the picked edition already is
    out.sort(key=lambda c: (not c["holds"], c["id"]))
    return out


def _twin_track(track: Dict[str, Any], kept: List[Dict[str, Any]], album_title: Any,
                artist_name: Any) -> Optional[Dict[str, Any]]:
    """the kept album's row for the same song: the same position and the same
    song (the leftover fold's test), else the one row with its title."""
    from core.library2.dedup_repair import _same_song
    from core.library2.recording_links import normalize_title
    for row in kept:
        if (track["track_number"] is not None and row["track_number"] == track["track_number"]
                and row["disc"] == track["disc"]
                and _same_song(track, row, album_title, artist_name)):
            return row
    key = normalize_title(track["title"] or "")
    named = [row for row in kept if key and normalize_title(row["title"] or "") == key]
    return named[0] if len(named) == 1 else None


def merge_album_into(conn: Any, keep_id: int, other_id: int) -> Dict[str, int]:
    """fold ``other_id`` into ``keep_id``: one album of the artist split over
    two rows (#2178). does not commit, touches no files.

    built from the folds the library already runs. a song both rows have
    becomes one track holding both files (the catalogue merge's file move and
    the twin fold's carry of plays, rules, wishlist links, edits and a
    redirect); the rest of the tracks move over as they are. the other row's
    editions, rules, mappings, findings and edits move to the kept one, then
    the row goes. the files stay where they are: Rename / Organize files them
    under the kept album, and a second copy of one song shows as that track's
    second file for the duplicate tools.
    """
    from core.library2.catalogue_duplicates import _move_references
    from core.library2.catalogue_identity import record_redirect
    from core.library2.dedup_repair import (
        _carry_folded_track, _delete_placeholder_track, _move_overrides)
    from core.library2.editions import album_release_ids, sync_default_edition
    from core.library2.entity_history import record_entity_merge
    from core.library2.provider_ids import provider_only
    from core.library2.track_files import elect_primary_file
    from core.library2.wanted import recompute_wanted_for_entity

    keep_id, other_id = int(keep_id), int(other_id)
    if keep_id == other_id:
        raise ValueError("An album can't be merged into itself")
    cursor = conn.cursor()
    keep = conn.execute(
        "SELECT al.*, ar.name AS artist_name FROM lib2_albums al"
        " LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id WHERE al.id=?",
        (keep_id,)).fetchone()
    other = conn.execute("SELECT * FROM lib2_albums WHERE id=?", (other_id,)).fetchone()
    if keep is None or other is None:
        raise LookupError("One of the two albums is gone")
    track_sql = ("SELECT id, title, track_number, COALESCE(disc_number, 1) AS disc, duration,"
                 " album_id FROM lib2_tracks WHERE album_id=? ORDER BY id")
    kept = [dict(r) for r in conn.execute(track_sql, (keep_id,))]
    profiles = {1} | {int(r[0]) for r in conn.execute(
        "SELECT profile_id FROM lib2_monitor_rules WHERE entity_type='album' AND entity_id IN (?,?)",
        (keep_id, other_id)) if r[0] is not None}
    stats = {"tracks_moved": 0, "tracks_folded": 0}
    for track in [dict(r) for r in conn.execute(track_sql, (other_id,))]:
        twin = _twin_track(track, kept, keep["title"], keep["artist_name"])
        if twin is None:
            cursor.execute("UPDATE lib2_tracks SET album_id=?, updated_at=CURRENT_TIMESTAMP"
                           " WHERE id=?", (keep_id, track["id"]))
            stats["tracks_moved"] += 1
            continue
        # the same song on both: one track, both files (the kept row's own
        # primary stays primary until the election says otherwise)
        cursor.execute("UPDATE lib2_track_files SET track_id=?, is_primary=0 WHERE track_id=?",
                       (twin["id"], track["id"]))
        _carry_folded_track(conn, int(track["id"]), int(twin["id"]))
        cursor.execute("UPDATE OR IGNORE lib2_track_artists SET track_id=? WHERE track_id=?",
                       (twin["id"], track["id"]))
        cursor.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (track["id"],))
        _delete_placeholder_track(cursor, int(track["id"]))
        elect_primary_file(conn, int(twin["id"]))
        stats["tracks_folded"] += 1

    _move_references(conn, "album", other_id, keep_id)
    _move_overrides(conn, "release_group", other_id, keep_id)
    # the other row's releases stay on record as editions of the kept album,
    # never a second default (the catalogue merge does the same). its own
    # release gets its edition first: a row nothing read an edition for yet
    # has none, and its ids would go with it
    sync_default_edition(cursor, other_id)
    cursor.execute("UPDATE lib2_release_editions SET release_group_id=?, is_default=0"
                   " WHERE release_group_id=?", (keep_id, other_id))
    cursor.execute("""INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role)
        SELECT ?, artist_id, role FROM lib2_album_artists
         WHERE album_id=? AND role<>'primary'""", (keep_id, other_id))
    record_redirect(conn, "album", other_id, keep_id, provider_only(album_release_ids(other)),
                    {"merged": "other_versions"},
                    target_provider_ids=provider_only(album_release_ids(keep)))
    record_entity_merge(conn, source_type="release_group", source_id=other_id,
                        target_type="release_group", target_id=keep_id,
                        change_source="other_versions_merge")
    cursor.execute("DELETE FROM lib2_monitor_rules WHERE entity_type='album' AND entity_id=?",
                   (other_id,))
    cursor.execute("DELETE FROM lib2_album_artists WHERE album_id=?", (other_id,))
    cursor.execute("DELETE FROM lib2_albums WHERE id=?", (other_id,))
    cursor.execute("UPDATE lib2_albums SET track_count=(SELECT COUNT(*) FROM lib2_tracks"
                   " WHERE album_id=?), updated_at=CURRENT_TIMESTAMP WHERE id=?",
                   (keep_id, keep_id))
    for profile in sorted(profiles):
        recompute_wanted_for_entity(conn, "album", keep_id, profile_id=profile)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                    " AND name='lib2_artist_rollup'").fetchone():
        cursor.execute("UPDATE lib2_artist_rollup SET computed_at=0 WHERE artist_id=?",
                       (keep["primary_artist_id"],))
    logger.info("Album %s merged into %s from Other versions: %s", other_id, keep_id, stats)
    return stats


_TYPE_WORDS = {"album": "album", "ep": "ep", "single": "single", "compilation": "compilation"}


def _version_type(source: str, raw: Any, track_count: Optional[int]) -> Optional[str]:
    """the edition's release type by the same rule the download filing uses:
    spotify calls EPs singles, so its 'single' splits by track count."""
    if not str(raw or "").strip() and not track_count:
        return None
    from core.imports.paths import get_album_type_display
    return _TYPE_WORDS.get(get_album_type_display(raw, track_count, source).strip().lower())


def _int(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def stamp_version(conn: Any, album_id: int, source: str, version: Dict[str, Any]) -> List[str]:
    """put the picked edition's title, type, date and track count on the album
    row (#2178). does not commit; answers the columns it wrote.

    the row kept the old release's title and type, so the picker marked the
    new edition current on a row that still read "(Hot S**t) Country Grammar",
    in the EP section. a hand edit (an override) still wins on the page, and
    a type the person set by hand is not touched at all. a compilation, live
    or other kind isn't turned into an album by a source that only says
    album/single by track count.
    """
    row = conn.execute("SELECT album_type, filed_release FROM lib2_albums WHERE id=?",
                       (int(album_id),)).fetchone()
    if row is None:
        return []
    overridden = set()
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                    " AND name='lib2_metadata_overrides'").fetchone():
        overridden = {r[0] for r in conn.execute(
            "SELECT field_name FROM lib2_metadata_overrides"
            " WHERE entity_type='release_group' AND entity_id=?", (int(album_id),))}
    sets: Dict[str, Any] = {}
    title = str(version.get("title") or "").strip()
    if title:
        sets["title"] = title
    date = str(version.get("date") or "").strip()
    if date:
        sets["release_date"] = date
        if date[:4].isdigit():
            sets["year"] = int(date[:4])
    count = _int(version.get("track_count"))
    if count:
        sets["expected_track_count"] = count
    kind = _version_type(source, version.get("album_type"), count)
    current = str(row["album_type"] or "").strip().lower()
    if (kind and "album_type" not in overridden
            and (current in ("", "album", "ep", "single") or kind == "compilation")):
        sets["album_type"] = kind
        sets["album_type_known"] = 1
        # a reorganize files the release the way its last download did: that
        # was the old edition, so it would file it back under EP/
        try:
            filed = json.loads(row["filed_release"] or "{}")
        except (TypeError, ValueError):
            filed = {}
        if isinstance(filed, dict) and filed.get("type"):
            filed.update({"type": kind, "total_tracks": count or 0, "source": source,
                          "locked": False})
            sets["filed_release"] = json.dumps(filed)
    if not sets:
        return []
    conn.execute(
        f"UPDATE lib2_albums SET {', '.join(f'{k}=?' for k in sets)},"
        " updated_at=CURRENT_TIMESTAMP WHERE id=?", (*sets.values(), int(album_id)))
    return list(sets)


def fetch_release_facts(source: str, release_id: str) -> Optional[Dict[str, Any]]:
    """title, date, type and track count of one release, from its source."""
    from core.metadata.album_tracks import _build_album_info, get_album_for_source
    raw = get_album_for_source(source, release_id)
    if not raw:
        return None
    info = _build_album_info(raw, release_id, source=source)
    return {"title": info.get("name"), "date": info.get("release_date"),
            "album_type": info.get("album_type"), "track_count": info.get("total_tracks")}


def apply_version(database: Any, album_id: int, source: str, release_id: str, *,
                  actor: str, version: Optional[Dict[str, Any]] = None,
                  merge_album_id: Any = None,
                  release_facts: Callable[[str, str], Optional[Dict[str, Any]]] = None,
                  ) -> Dict[str, Any]:
    """point the album at the picked version: the manual album match (#758),
    which sets the release id and pins it, and the edition's title, type and
    date on the row (#2178).

    ``version`` is the row the picker showed (title, date, album_type,
    track_count); without one the source is asked. ``merge_album_id`` is one
    of :func:`merge_candidates`: the album that already is the edition keeps
    it and the other folds in. answers ``album_id``, the album left standing.
    """
    from core.library2.match_status import apply_manual_match
    from core.metadata.canonical_version import CANONICAL_ALBUM_SOURCES

    source = str(source or "").strip().lower()
    release_id = str(release_id or "").strip()
    if source not in CANONICAL_ALBUM_SOURCES or not release_id:
        return {"success": False, "error": "Pick a version from the list"}
    facts = {k: (version or {}).get(k) for k in ("title", "date", "album_type", "track_count")}
    if not any(facts.values()):
        try:
            facts = (release_facts or fetch_release_facts)(source, release_id) or {}
        except Exception as exc:  # noqa: BLE001 - the switch stands without them
            logger.debug("no facts for %s release %s: %s", source, release_id, exc)
            facts = {}
    keep, merged = int(album_id), None
    conn = database._get_connection()
    try:
        if conn.execute("SELECT 1 FROM lib2_albums WHERE id=?", (keep,)).fetchone() is None:
            return {"success": False, "error": "That album is gone"}
        if merge_album_id not in (None, ""):
            candidates = merge_candidates(conn, keep, source, release_id, title=facts.get("title"))
            pick = next((c for c in candidates if str(c["id"]) == str(merge_album_id)), None)
            if pick is None:
                return {"success": False, "error": "That album isn't this version, so it can't be merged here"}
            if pick["locked"]:
                return {"success": False,
                        "error": f"{pick['title'] or 'That album'} is locked. Unlock it to merge"}
            keep, merged = (pick["id"], keep) if pick["holds"] else (keep, pick["id"])
        apply_manual_match(conn, "album", keep, source, release_id, actor=actor)
        stats = merge_album_into(conn, keep, merged) if merged is not None else {}
        stamped = stamp_version(conn, keep, source, facts)
        conn.commit()
    except (LookupError, ValueError) as exc:
        conn.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()
    return {"success": True, "source": source, "release_id": release_id, "album_id": keep,
            "merged_album_id": merged, "stamped": stamped, **stats}


__all__ = [
    "list_versions", "search_versions", "preview_version", "apply_version",
    "merge_candidates", "merge_album_into", "stamp_version",
    "version_row", "same_title", "album_facts", "clear_cache",
]
