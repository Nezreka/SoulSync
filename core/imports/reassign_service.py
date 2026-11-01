"""Service layer for album reassign: source lookups, preview, apply.

The flow the UI drives, in the order the user thinks about it:

  1. search for the ARTIST the album should belong to
  2. pick one of THAT artist's albums
  3. see how the local files line up against it
  4. apply

Steps 1 and 2 are what make this safe. Reassigning to a typed-in name would
produce an artist with no source id, no images and nothing for the import to
resolve against. Picking a real artist and then a real album of theirs means
the identity handed to the pipeline is one the source can actually answer for.

Everything here is a thin wrapper over clients that already exist
(``search_artists`` / ``get_artist_albums`` / ``get_album_tracks``) plus the
pure mapping in ``reassign_album``. Nothing writes tags or moves files: the
import pipeline does that when it consumes the hints, exactly as it does for a
single-track re-identify (#889).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.imports.reassign_album import apply_album_reassign, build_reassign_plan
from utils.logging_config import get_logger

logger = get_logger("imports.reassign_service")


def _client(source: str):
    from core.metadata.registry import get_client_for_source
    return get_client_for_source(source)


def _attr(obj: Any, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def search_artists(source: str, query: str, limit: int = 12) -> List[Dict[str, Any]]:
    """Artists matching ``query`` on ``source``, as display rows."""
    if not query or not query.strip():
        return []
    client = _client(source)
    if client is None or not hasattr(client, "search_artists"):
        return []
    try:
        results = client.search_artists(query.strip(), limit=limit) or []
    except Exception as exc:
        logger.debug("reassign artist search failed on %s: %s", source, exc)
        return []
    rows = []
    for artist in results:
        artist_id = _attr(artist, "id")
        if not artist_id:
            continue
        rows.append({
            "id": str(artist_id),
            "name": str(_attr(artist, "name", "") or ""),
            "image_url": _attr(artist, "image_url") or _attr(artist, "thumb_url"),
        })
    return rows


def artist_albums(source: str, artist_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """The chosen artist's releases, as display rows."""
    client = _client(source)
    if client is None or not hasattr(client, "get_artist_albums") or not artist_id:
        return []
    try:
        results = client.get_artist_albums(str(artist_id), limit=limit) or []
    except Exception as exc:
        logger.debug("reassign album list failed on %s: %s", source, exc)
        return []
    rows = []
    for album in results:
        album_id = _attr(album, "id")
        if not album_id:
            continue
        rows.append({
            "id": str(album_id),
            "name": str(_attr(album, "name", "") or _attr(album, "title", "") or ""),
            "year": _attr(album, "year") or _attr(album, "release_date"),
            "album_type": _attr(album, "album_type") or _attr(album, "record_type"),
            "total_tracks": _attr(album, "total_tracks") or _attr(album, "track_count"),
            "image_url": _attr(album, "image_url") or _attr(album, "thumb_url"),
        })
    return rows


def target_tracks(source: str, album_id: str) -> List[Dict[str, Any]]:
    """The target release's tracklist, shaped for the mapper."""
    client = _client(source)
    if client is None or not hasattr(client, "get_album_tracks") or not album_id:
        return []
    try:
        payload = client.get_album_tracks(str(album_id))
    except Exception as exc:
        logger.debug("reassign tracklist failed on %s: %s", source, exc)
        return []
    if not payload:
        return []
    # 'items' FIRST: the clients return a Spotify-compatible shape, and
    # Spotify's album-tracks payload is {'items': [...]}. Discogs, iTunes,
    # HydraBase and MusicBrainz all use it; only some use 'tracks'. Checking
    # only 'tracks' made this return nothing for most sources, which surfaced
    # as "Could not read that release's tracklist" rather than as a bug.
    if isinstance(payload, dict):
        raw = payload.get("items")
        if raw is None:
            raw = payload.get("tracks")
    else:
        raw = payload
    rows = []
    for track in raw or []:
        track_id = _attr(track, "id")
        if not track_id:
            continue
        rows.append({
            "id": str(track_id),
            "name": str(_attr(track, "name", "") or _attr(track, "title", "") or ""),
            "track_number": _attr(track, "track_number"),
            "disc_number": _attr(track, "disc_number") or 1,
        })
    return rows


LEGACY_SUBJECT_ERROR = (
    "That album is not a Library v2 album — reassign needs a lib2:<id> subject"
)


def lib2_album_id(album_id: Any) -> Optional[int]:
    """The Library-v2 row id behind a ``lib2:<id>`` subject, else None.

    The prefix is not decoration. This flow ends in a rematch hint whose
    ``replace_track_id`` is resolved against ``lib2_track_files.track_id``, so a
    legacy ``tracks.id`` would not fail loudly — it would silently name a
    DIFFERENT track and delete that track's file. An id space you cannot tell
    apart by looking at it has to be labelled.
    """
    text = str(album_id or "").strip()
    if not text.startswith("lib2:"):
        return None
    try:
        value = int(text.split(":", 1)[1])
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def local_album_tracks(database, album_id: Any) -> List[Dict[str, Any]]:
    """The album's own files, shaped for the mapper.

    Tracks with no live file are excluded: there is nothing to stage, so
    including them would only produce pairings that can never be applied. One
    row per TRACK — a track with an MP3 next to its FLAC is still one thing to
    move, and its primary file is the one that goes.

    ``file_path`` is resolved to a real on-disk path, because staging opens it:
    what Library v2 stores is the path as the media server sees it, which this
    process may not be able to read literally.
    """
    native_id = lib2_album_id(album_id)
    if native_id is None:
        logger.warning("reassign refused non-Library-v2 album subject %r", album_id)
        return []
    conn = None
    try:
        conn = database._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT t.id, t.title, t.track_number, f.path
            FROM lib2_tracks t
            JOIN lib2_track_files f ON f.track_id = t.id
            WHERE t.album_id = ?
              AND f.path IS NOT NULL AND f.path != ''
              AND COALESCE(f.file_state, 'active') = 'active'
            ORDER BY COALESCE(t.track_number, 999999), t.title,
                     f.is_primary DESC, f.id
            """,
            (native_id,),
        )
        rows: List[Dict[str, Any]] = []
        seen = set()
        for row in cursor.fetchall():
            track_id = row[0]
            if track_id in seen:
                continue          # a second file of a track already listed
            seen.add(track_id)
            rows.append({"id": track_id, "title": row[1], "track_number": row[2],
                         "file_path": _readable_path(row[3])})
        return rows
    except Exception as exc:
        logger.error("reassign could not read local album %s: %s", album_id, exc)
        return []
    finally:
        if conn:
            conn.close()


def _readable_path(stored: Any) -> str:
    """The stored path mapped to the file this process can actually open."""
    text = str(stored or "")
    try:
        from core.library.path_resolver import resolve_library_file_path
        return resolve_library_file_path(text) or text
    except Exception:                                   # pragma: no cover - defensive
        return text


def is_same_release(database, local_album_id: Any, source: str, album_id: str) -> bool:
    """True when the target IS the release the album already claims.

    #889's cleanup relies on the re-import landing somewhere new; the same-home
    guard already refuses to delete a file the import just rewrote, so this is
    not a data-loss risk. It is a pointless-work and confusing-outcome risk:
    every file would be restaged and re-imported to produce no change. Caught
    here rather than trusted to the UI, because the API is callable directly.

    Every source is comparable in Library v2, not just Spotify: the album row
    carries its own ``spotify_id`` and ``musicbrainz_id``, and the long-tail
    providers live in ``external_ids``.
    """
    native_id = lib2_album_id(local_album_id)
    if not album_id or native_id is None:
        return False
    conn = None
    try:
        conn = database._get_connection()
        row = conn.cursor().execute(
            "SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_albums WHERE id = ?",
            (native_id,),
        ).fetchone()
    except Exception:
        return False
    finally:
        if conn:
            conn.close()
    if row is None:
        return False
    spotify_id, musicbrainz_id, external_ids = row[0], row[1], row[2]

    key = str(source or "").lower()
    if key == "spotify":
        existing = spotify_id
    elif key in ("musicbrainz", "mb"):
        existing = musicbrainz_id
    else:
        existing = None
        try:
            import json
            extra = json.loads(external_ids or "{}")
            if isinstance(extra, dict):
                existing = extra.get(key)
        except (TypeError, ValueError):
            existing = None
    return bool(existing) and str(existing) == str(album_id)


def preview_reassign(database, source: str, local_album_id: Any, album_id: str) -> Dict[str, Any]:
    """What WOULD happen, for the user to confirm before anything is staged.

    An album is many files. Showing the mapping — including why each pairing
    was proposed and which files could not be placed — is the difference
    between a reassign the user trusts and one that silently misfiles a third
    of the tracks.
    """
    if lib2_album_id(local_album_id) is None:
        # "That album has no files" would be a lie about a full album; the
        # request named the wrong id space and the message has to say so.
        return {"success": False, "error": LEGACY_SUBJECT_ERROR}

    locals_ = local_album_tracks(database, local_album_id)
    if not locals_:
        return {"success": False, "error": "That album has no files on disk to reassign"}

    if is_same_release(database, local_album_id, source, album_id):
        return {"success": False,
                "error": "This album is already assigned to that release"}

    targets = target_tracks(source, album_id)
    if not targets:
        return {"success": False, "error": "Could not read that release's tracklist"}

    plan = build_reassign_plan(locals_, targets)
    return {
        "success": True,
        "pairings": [
            {
                "local_id": p.local_id,
                "local_title": p.local_title,
                "local_track_number": p.local_track_number,
                "target_title": p.target_title,
                "target_track_number": p.target_track_number,
                "matched_by": p.matched_by,
                "mapped": p.mapped,
            }
            for p in plan.pairings
        ],
        "mapped_count": len(plan.mapped),
        "unmapped_count": len(plan.unmapped),
    }


def apply_reassign(
    database,
    *,
    source: str,
    local_album_id: Any,
    album_id: str,
    album_name: str,
    artist_id: Optional[str],
    artist_name: str,
    album_type: Optional[str],
    staging_dir: str,
    replace: bool = True,
    allow_partial: bool = False,
) -> Dict[str, Any]:
    """Stage the album's files with one hint each. The import pipeline does the
    rest — tags, folder, database rows.

    ``allow_partial`` defaults to FALSE on purpose. Moving 8 of an album's 12
    files leaves the other 4 under the old artist — an album split across two
    artists, which is the exact problem this feature exists to fix. A caller
    that has shown the user the preview and had them accept it passes True;
    a caller that has not cannot cause it by accident.
    """
    if lib2_album_id(local_album_id) is None:
        return {"success": False, "error": LEGACY_SUBJECT_ERROR}

    if is_same_release(database, local_album_id, source, album_id):
        return {"success": False,
                "error": "This album is already assigned to that release"}

    locals_ = local_album_tracks(database, local_album_id)
    targets = target_tracks(source, album_id)
    if not locals_ or not targets:
        return {"success": False, "error": "Nothing to reassign"}

    plan = build_reassign_plan(locals_, targets)
    if not plan.mapped:
        return {"success": False,
                "error": "None of this album's files line up with that release"}
    if plan.unmapped and not allow_partial:
        return {
            "success": False,
            "error": (f"{len(plan.unmapped)} of {len(plan.pairings)} files do not line up "
                      f"with that release. Reassigning only the rest would split the album "
                      f"across two artists."),
            "mapped_count": len(plan.mapped),
            "unmapped_count": len(plan.unmapped),
            "needs_confirmation": True,
        }

    conn = None
    result: Dict[str, Any] = {}
    try:
        conn = database._get_connection()
        cursor = conn.cursor()
        result = apply_album_reassign(
            plan, source=source, album_id=album_id, album_name=album_name,
            artist_id=artist_id, artist_name=artist_name, album_type=album_type,
            staging_dir=staging_dir, cursor=cursor, replace=replace,
        )
        conn.commit()
    except Exception as exc:
        # The files are already staged but their hints were never committed.
        # Left there, auto-import would pick each one up as an ordinary new
        # file and duplicate the whole album. Take them back out.
        _discard_staged(result.get("staged") or [])
        logger.error("reassign apply failed: %s", exc)
        return {"success": False, "error": str(exc)}
    finally:
        if conn:
            conn.close()

    if not result.get("staged"):
        # Nothing reached staging — every file failed. Reporting success here
        # would tell the user their album moved when not one track did.
        return {
            "success": False,
            "error": "No files could be staged for reassignment",
            **result,
        }
    return {"success": True, **result}


def _discard_staged(staged: List[Dict[str, Any]]) -> None:
    """Remove staged copies whose hints did not survive the transaction."""
    import os

    for entry in staged:
        path = entry.get("staged_path")
        if not path:
            continue
        try:
            os.remove(path)
        except OSError as exc:
            logger.warning("Could not remove orphaned staged copy %s: %s", path, exc)
