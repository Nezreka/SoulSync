"""Where a Library-v2 album's files belong, computed from the catalogue alone.

Reorganize used to answer this by asking a metadata provider for the album's
tracklist and naming files after what came back. The library's own values were
pulled in one exception at a time — ``_keep_user_casing`` for the album name
(#1078), again for the track title (#1078), ``_keep_user_year`` for the year
(#1080). Three patches, each added after a report, each saying the same thing:
where the catalogue and the provider disagreed, the catalogue was right.

So this planner reads the catalogue and nothing else. Consequences, all
intended:

* An album with no stored source id is reorganizable. The old planner refused
  it outright (``status: 'no_source_id'``) — for an operation that moves a
  file, which needs no provider at all.
* The plan is offline. No 4.4s preview, and no ``Invalid base62 id`` 400s from
  candidate ids that were never Spotify's to begin with.
* The filename matches what the Library page shows, hand-set titles included,
  because both read through the same ``project_metadata`` override layer.
* ``total_discs`` comes from the discs the catalogue knows rather than from a
  live tracklist, which is how a half-downloaded album's layout oscillated
  between ``Disc N/`` and flat (#1080).

The cost is stated rather than hidden: a manual match no longer changes the
path by itself. Re-tag moves a provider's values into the catalogue;
reorganize applies the template to what the catalogue says.

The path is still built by ``core.imports.paths.build_final_path_for_track``
through the same context shape post-processing uses, so a reorganize
destination and a fresh download's destination cannot drift apart.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.reorganize_plan")


def _album_row(conn, album_id: int):
    return conn.execute(
        """SELECT al.id, al.title, al.year, al.release_date, al.album_type,
                  al.image_url, al.spotify_id, al.primary_artist_id,
                  ar.name AS artist_name
             FROM lib2_albums al
             LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
            WHERE al.id = ?""",
        (int(album_id),),
    ).fetchone()


def _track_rows(conn, album_id: int) -> List[Any]:
    """The album's tracks with their primary file, catalogue order.

    A track with no usable file is not part of a reorganize — there is nothing
    to move — so the join is inner on purpose.
    """
    from core.library2.track_files import primary_order

    return conn.execute(
        f"""SELECT t.id, t.title, t.track_number, t.disc_number,
                   (SELECT tf.path FROM lib2_track_files tf
                     WHERE tf.track_id = t.id AND tf.path IS NOT NULL AND tf.path <> ''
                       AND COALESCE(tf.file_state,'active')
                           NOT IN ('missing_confirmed','deleted')
                     ORDER BY {primary_order('tf')} LIMIT 1) AS file_path
              FROM lib2_tracks t
             WHERE t.album_id = ?
             ORDER BY COALESCE(t.disc_number,1), t.track_number, t.id""",
        (int(album_id),),
    ).fetchall()


def _effective(conn, entity_type: str, entity_id: Any, fields: Dict[str, Any]) -> Dict[str, Any]:
    """``fields`` with this entity's hand-set values on top.

    The same projection ``queries._serialize_track`` uses, so the name on disk
    and the name on the page are the same string by construction.
    """
    from core.library2.metadata_overrides import project_metadata

    if not entity_id:
        return dict(fields)
    try:
        effective, _overrides = project_metadata(
            conn, entity_type=entity_type, entity_id=int(entity_id),
            provider_fields=fields,
        )
        return effective
    except Exception as exc:  # noqa: BLE001 - a planner never fails on the override layer
        logger.debug("override projection skipped for %s %s: %s",
                     entity_type, entity_id, exc)
        return dict(fields)


def _credited_artists(conn, track_id: int) -> List[str]:
    rows = conn.execute(
        """SELECT ar.id, ar.name FROM lib2_track_artists ta
             JOIN lib2_artists ar ON ar.id = ta.artist_id
            WHERE ta.track_id = ? ORDER BY ta.position""",
        (int(track_id),),
    ).fetchall()
    # ARCH-04: a corrected artist name is an override on the artist, and the
    # path has to be built from the same effective value the page shows.
    from core.library2.metadata_overrides import effective_artist_names

    names = effective_artist_names(conn, [r["id"] for r in rows])
    return [n for n in (names.get(int(r["id"]), r["name"]) for r in rows) if n]


def _as_provider_album(album: Dict[str, Any], track_count: int,
                       total_discs: int) -> Dict[str, Any]:
    """Shape the catalogue album like the provider payload the context builder
    reads. Reusing that builder — rather than inventing a second context shape
    — is what keeps a reorganize destination identical to a download's."""
    return {
        "id": album.get("spotify_id") or "",
        "name": album.get("title") or "Unknown Album",
        "release_date": album.get("release_date") or (
            str(album["year"]) if album.get("year") else ""),
        "total_tracks": track_count,
        "total_discs": total_discs,
        "image_url": album.get("image_url") or "",
    }


def plan_album_reorganize(
    conn: Any,
    album_id: int,
    *,
    build_final_path_fn: Callable,
    transfer_dir: str,
    resolve_file_path_fn: Callable[[Optional[str]], Optional[str]],
) -> Dict[str, Any]:
    """Compute every track's destination for one lib2 album. Moves nothing.

    Returns the shape the reorganize preview and the rename executor both read,
    so what the user approves is exactly what runs.
    """
    from core.library_reorganize import (
        _build_album_info, _build_post_process_context, _canonical_file_path,
        _is_in_deleted_quarantine, _same_physical_file, _trim_to_transfer,
    )

    album_row = _album_row(conn, album_id)
    if album_row is None:
        return {"success": False, "status": "no_album", "source": None,
                "album": "", "artist": "", "transfer_dir": transfer_dir,
                "tracks": []}

    album = _effective(conn, "release_group", album_row["id"], {
        "title": album_row["title"],
        "year": album_row["year"],
        "release_date": album_row["release_date"],
        "album_type": album_row["album_type"],
        "image_url": album_row["image_url"],
    })
    album["spotify_id"] = album_row["spotify_id"]
    album_title = album.get("title") or "Unknown Album"
    # ARCH-04: the same effective name the artist page shows, so a corrected
    # name reaches the folder on disk instead of only the UI.
    from core.library2.metadata_overrides import effective_artist_name
    artist_name = effective_artist_name(
        conn, album_row["primary_artist_id"], album_row["artist_name"]
    ) or "Unknown Artist"

    all_rows = _track_rows(conn, album_id)
    all_tracks = [
        _effective(conn, "track", r["id"], {
            "id": r["id"], "title": r["title"],
            "track_number": r["track_number"], "disc_number": r["disc_number"],
            "file_path": r["file_path"],
        })
        for r in all_rows
    ]
    tracks = [t for t in all_tracks if t["file_path"]]
    common = {
        "source": None,
        "album": album_title,
        "artist": artist_name,
        "transfer_dir": transfer_dir,
    }
    if not tracks:
        return {"success": False, "status": "no_tracks", **common, "tracks": []}

    # ARCH-03: the disc count is a property of the ALBUM, so it is read from
    # every catalogue position — including tracks whose file has not arrived
    # yet. Computing it after the file filter declared a known two-disc album
    # single-disc while only disc 1 was downloaded (`total_discs_declared=True`
    # suppresses the shared path builder's own disc detection), moved disc 1 out
    # of `Disc 1/`, and moved it straight back the moment disc 2's first file
    # landed — the #1080 oscillation from the other direction.
    total_discs = max((int(t["disc_number"] or 1) for t in all_tracks), default=1)

    planned: List[Dict[str, Any]] = []
    for track in tracks:
        db_path = track["file_path"]
        resolved = resolve_file_path_fn(db_path) if db_path else None
        file_ext = os.path.splitext(resolved or db_path or ".flac")[1] or ".flac"
        title = track.get("title") or ""
        item = {
            "track_id": track["id"],
            "title": title,
            "track_number": track.get("track_number") or 0,
            "disc_number": int(track.get("disc_number") or 1),
            "current_path": _trim_to_transfer(db_path, resolved, transfer_dir),
            "new_path": "",
            "current_path_abs": resolved or "",
            "new_path_abs": "",
            "file_exists": resolved is not None,
            "unchanged": False,
            "collision": False,
            "matched": resolved is not None,
            "reason": None if resolved else "File not found on disk",
        }

        # #746: files parked in the duplicate-cleaner quarantine
        # (<transfer>/deleted) are not library files and must stay put.
        if resolved and _is_in_deleted_quarantine(resolved, transfer_dir):
            item["matched"] = False
            item["reason"] = "In deleted/quarantine folder — skipped"
            planned.append(item)
            continue
        if not item["matched"]:
            planned.append(item)
            continue

        artists = _credited_artists(conn, track["id"]) or [artist_name]
        context = _build_post_process_context(
            _as_provider_album(album, len(tracks), total_discs),
            {
                "id": "",
                "name": title,
                "track_number": track.get("track_number") or 1,
                "disc_number": int(track.get("disc_number") or 1),
                "duration_ms": 0,
                "artists": [{"name": a} for a in artists],
            },
            artist_name,
            album_title,
            total_discs,
            local_title=title,
            local_year=(str(album["year"]) if album.get("year") else None),
        )
        try:
            new_full, _ok = build_final_path_fn(
                context, context["spotify_artist"], _build_album_info(context),
                file_ext, create_dirs=False,
            )
            item["new_path_abs"] = new_full or ""
            item["new_path"] = (
                os.path.relpath(new_full, transfer_dir)
                if transfer_dir and new_full and new_full.startswith(transfer_dir)
                else new_full or ""
            )
            if resolved and new_full and _same_physical_file(resolved, new_full):
                item["unchanged"] = True
        except Exception as exc:  # noqa: BLE001 - reported per track, never fatal
            item["reason"] = f"Couldn't compute destination path: {exc}"
        planned.append(item)

    # Two tracks planned onto one path would overwrite each other on apply.
    seen: Dict[str, Dict[str, Any]] = {}
    for item in planned:
        if not item["matched"] or item["unchanged"] or not item["new_path"]:
            continue
        key = _canonical_file_path(item.get("new_path_abs") or item["new_path"])
        if key in seen:
            item["collision"] = True
            seen[key]["collision"] = True
        else:
            seen[key] = item

    return {"success": True, "status": "planned", **common, "tracks": planned}


__all__ = ["plan_album_reorganize"]
