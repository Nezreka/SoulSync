"""Resolve an album's canonical tracklist so missing tracks show real titles.

Lidarr shows the full tracklist of an album (from metadata) and marks which tracks
are present vs missing. We fetch the canonical tracklist from a metadata provider
(Spotify by id, else Deezer by search — both reusing SoulSync's existing clients)
and cache it on ``lib2_albums.tracklist_json``. The read path (``queries.get_album``)
then fills missing-track placeholders with the real title instead of "Track N".

Resolution is best-effort and never raises — when no provider yields a tracklist,
the UI falls back to numbered missing slots.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.completeness")


def _extract_tracks(payload: Any, *, source: str = "") -> List[dict]:
    """Pull ``[{track_number, title}]`` out of a provider get_album_tracks payload,
    tolerant of the various container shapes (items / tracks / data)."""
    if not payload:
        return []
    items: Optional[list] = None
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("items", "tracks", "data"):
            v = payload.get(key)
            if isinstance(v, dict):
                v = v.get("items") or v.get("data")
            if isinstance(v, list):
                items = v
                break
    out: List[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        title = it.get("name") or it.get("title")
        num = it.get("track_number") or it.get("track_position") or it.get("position")
        disc = it.get("disc_number") or 1
        duration = it.get("duration_ms")
        if duration is None and source == "deezer" and it.get("duration"):
            try:
                duration = int(it.get("duration")) * 1000
            except (TypeError, ValueError):
                duration = None
        if title:
            entry = {
                "track_number": int(num) if num else None,
                "disc_number": int(disc) if disc else 1,
                "title": str(title),
            }
            if duration:
                entry["duration_ms"] = duration
            if source == "spotify" and it.get("id"):
                entry["spotify_id"] = str(it.get("id"))
            out.append(entry)
    return out


def _trim_excess_fileless_tracks(conn, album_id: int, expected: int,
                                  protect_ids: Optional[set] = None) -> int:
    """Drop surplus provider-only rows when an old import over-materialized them.

    ``protect_ids`` (rows the current call's entries matched or inserted) are
    never dropped — the tracklist just reaffirmed those positions are real,
    even when the album's stored ``expected_track_count`` predates that
    knowledge and is now an undercount.
    """
    if expected <= 0:
        return 0
    protect_ids = protect_ids or set()
    rows = conn.execute(
        """SELECT t.id, t.legacy_track_id,
                  EXISTS(SELECT 1 FROM lib2_track_files f WHERE f.track_id = t.id) AS has_file
             FROM lib2_tracks t
            WHERE t.album_id=?
            ORDER BY COALESCE(t.disc_number, 1), t.track_number, t.id""",
        (album_id,),
    ).fetchall()
    if len(rows) <= expected:
        return 0

    deleted = 0
    for idx, row in enumerate(rows):
        if idx < expected:
            continue
        if row["id"] in protect_ids:
            continue
        if row["legacy_track_id"] is not None or row["has_file"]:
            continue
        conn.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (row["id"],))
        conn.execute("DELETE FROM lib2_tracks WHERE id=?", (row["id"],))
        deleted += 1
    return deleted


def _persist_tracklist_tracks(conn, album_id: int, tracks: List[dict]) -> int:
    """Persist provider tracklist entries as fileless lib2 track rows.

    Missing rows must have real DB ids so they can be monitored individually,
    just like Lidarr's wanted track rows. Existing local/downloaded tracks are
    matched by disc+track number and left in place.
    """
    al = conn.execute(
        "SELECT primary_artist_id, monitored, quality_profile_id, expected_track_count FROM lib2_albums WHERE id=?",
        (album_id,),
    ).fetchone()
    if not al:
        return 0

    entries = [t for t in tracks if isinstance(t, dict)]
    try:
        expected = int(al["expected_track_count"] or 0)
    except (TypeError, ValueError):
        expected = 0
    if expected and len(entries) > expected:
        entries = entries[:expected]
    has_explicit_disc = any(e.get("disc_number") not in (None, "", 1, "1") for e in entries)
    inferred_disc = 1
    previous_number: Optional[int] = None

    created = 0
    touched_ids: set = set()
    for idx, entry in enumerate(entries):
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        try:
            number = int(entry.get("track_number") or idx + 1)
        except (TypeError, ValueError):
            number = idx + 1
        if has_explicit_disc:
            try:
                disc = int(entry.get("disc_number") or 1)
            except (TypeError, ValueError):
                disc = 1
        else:
            if previous_number is not None and number <= previous_number:
                inferred_disc += 1
            disc = inferred_disc
            previous_number = number
        duration = entry.get("duration_ms")
        spotify_id = entry.get("spotify_id")

        existing = conn.execute(
            """SELECT id FROM lib2_tracks
               WHERE album_id=? AND COALESCE(disc_number, 1)=? AND track_number=?""",
            (album_id, disc, number),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE lib2_tracks
                      SET title=COALESCE(NULLIF(title, ''), ?),
                          spotify_id=COALESCE(NULLIF(spotify_id, ''), ?),
                          duration=COALESCE(duration, ?),
                          updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (title, spotify_id, duration, existing["id"]),
            )
            track_id = existing["id"]
            touched_ids.add(track_id)
        else:
            conn.execute(
                """INSERT INTO lib2_tracks(album_id, title, track_number, disc_number,
                          duration, spotify_id, monitored, quality_profile_id)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (album_id, title, number, disc, duration, spotify_id,
                 1 if al["monitored"] else 0, al["quality_profile_id"] or 1),
            )
            track_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            created += 1
            touched_ids.add(track_id)

        conn.execute(
            """INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position)
               VALUES(?,?, 'primary', 0)""",
            (track_id, al["primary_artist_id"]),
        )
    return created + _trim_excess_fileless_tracks(conn, album_id, expected, protect_ids=touched_ids)


def resolve_tracklist(config_manager, conn, album_id: int) -> Optional[List[dict]]:
    """Return + cache the album's canonical tracklist. None when unavailable."""
    al = conn.execute(
        "SELECT title, spotify_id, primary_artist_id, tracklist_json FROM lib2_albums WHERE id=?",
        (album_id,),
    ).fetchone()
    if not al:
        return None
    if al["tracklist_json"]:
        try:
            cached = json.loads(al["tracklist_json"])
            if cached:
                if _persist_tracklist_tracks(conn, album_id, cached):
                    conn.commit()
                return cached
        except (ValueError, TypeError):
            pass

    artist = conn.execute(
        "SELECT name FROM lib2_artists WHERE id=?", (al["primary_artist_id"],)
    ).fetchone()
    artist_name = artist["name"] if artist else ""
    tracks: List[dict] = []

    # 1) Spotify by stored album id (works when Spotify is authenticated).
    if al["spotify_id"]:
        try:
            from core.metadata.registry import get_spotify_client
            sp = get_spotify_client()
            if sp:
                tracks = _extract_tracks(sp.get_album_tracks(al["spotify_id"]), source="spotify")
        except Exception as e:  # noqa: BLE001
            logger.debug("spotify tracklist failed (%s): %s", album_id, e)

    # 2) Deezer by search (free, no auth) as a fallback.
    if not tracks and artist_name and al["title"]:
        try:
            from core.metadata.registry import get_deezer_client
            dz = get_deezer_client()
            if dz:
                album = dz.search_album(artist_name, al["title"])
                aid = album.get("id") if isinstance(album, dict) else None
                if aid:
                    tracks = _extract_tracks(dz.get_album_tracks(str(aid)), source="deezer")
        except Exception as e:  # noqa: BLE001
            logger.debug("deezer tracklist failed (%s): %s", album_id, e)

    if tracks:
        try:
            conn.execute(
                "UPDATE lib2_albums SET tracklist_json=? WHERE id=?",
                (json.dumps(tracks), album_id),
            )
            _persist_tracklist_tracks(conn, album_id, tracks)
            conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("tracklist cache write failed (%s): %s", album_id, e)
    return tracks or None


def _partial_album_rows(conn, *, cached: Optional[bool] = None) -> List[Any]:
    """Albums whose expected provider track count is larger than known track rows."""
    count_sql = "(SELECT COUNT(*) FROM lib2_tracks t WHERE t.album_id = al.id)"
    clauses = []
    if cached is True:
        clauses.append(f"al.expected_track_count IS NOT NULL AND al.expected_track_count <> {count_sql}")
        clauses.append("al.tracklist_json IS NOT NULL AND al.tracklist_json <> ''")
    else:
        clauses.append(f"al.expected_track_count > {count_sql}")
    if cached is False:
        clauses.append("(al.tracklist_json IS NULL OR al.tracklist_json = '')")
    return conn.execute(
        "SELECT al.id FROM lib2_albums al WHERE " + " AND ".join(clauses) + " ORDER BY al.id"
    ).fetchall()


def precache_tracklists(database, config_manager, *, progress=None) -> int:
    """Resolve tracklists for every partial album (expected > present). Background.

    Cached tracklists are materialized first and without provider calls, so rows
    that already have canonical titles immediately become real, monitorable
    missing tracks in Library v2.
    """
    resolved = 0
    try:
        conn = database._get_connection()
    except Exception:  # noqa: BLE001
        return 0
    try:
        cached_rows = _partial_album_rows(conn, cached=True)
        for i, r in enumerate(cached_rows):
            if resolve_tracklist(config_manager, conn, r[0]):
                resolved += 1
            if progress and i % 20 == 0:
                progress("tracklists", i, len(cached_rows))

        rows = _partial_album_rows(conn, cached=False)
        for i, r in enumerate(rows):
            if resolve_tracklist(config_manager, conn, r[0]):
                resolved += 1
            if progress and i % 20 == 0:
                progress("tracklists", i, len(rows))
    except Exception as e:  # noqa: BLE001
        logger.debug("tracklist precache error: %s", e)
    finally:
        conn.close()
    logger.info("Library v2 tracklist precache: %d resolved", resolved)
    return resolved


__all__ = ["resolve_tracklist", "precache_tracklists"]
