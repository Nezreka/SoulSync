"""Resolve Library-v2 quality profiles and their inheritance provenance.

Used by acquisition paths that predate Library v2 (the watchlist scanner's
new-release queueing) so a per-artist profile assignment still reaches the
wishlist row — and therefore the download/import pipeline — for releases lib2
itself didn't queue.

§52.2 adds one important invariant: the stored profile id is the effective
compatibility projection, while ``quality_profile_explicit`` records whether
that entity actually owns the choice.  All pipeline consumers resolve through
this module so Track > Album > Artist > Global cannot drift between callers.

Fail-open: returns ``None`` (→ app-wide default profile) when the feature is
off, the artist isn't in lib2, or anything errors. Never raises.
"""

from __future__ import annotations

from collections import defaultdict
import sqlite3
from typing import Any, Dict, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.profile_lookup")


def _missing_playlist_schema(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "no such table" in message or "no such column" in message

_ENTITY_ALIASES = {
    "artist": "artists",
    "artists": "artists",
    "album": "albums",
    "albums": "albums",
    "track": "tracks",
    "tracks": "tracks",
}


def default_quality_profile_id(conn) -> int:
    """The app-wide default profile's id, for fallbacks.

    Profile ids must never be hardcoded to 1 — the starter profiles are fully
    user-manageable (incl. deleting id 1), so a literal 1 can dangle. Falls
    back to the lowest existing profile id, then 1 (empty table = seed order).
    """
    try:
        row = conn.execute(
            "SELECT id FROM quality_profiles WHERE is_default=1 ORDER BY id LIMIT 1"
        ).fetchone()
        if row:
            return int(row[0])
        row = conn.execute("SELECT id FROM quality_profiles ORDER BY id LIMIT 1").fetchone()
        if row:
            return int(row[0])
    except Exception as e:  # noqa: BLE001
        logger.debug("default profile lookup failed: %s", e)
    return 1


def resolve_profile_cascade(levels, default_id: int) -> Dict[str, Any]:
    """Pick the effective profile from ordered cascade levels.

    ``levels`` is an ordered iterable of ``(source, source_id, profile_id,
    explicit)`` tuples (most specific first, e.g. track → album → artist).
    The first level that explicitly owns a profile wins; otherwise the
    app-wide ``default_id``. This is the SINGLE cascade implementation shared
    by both the per-entity :func:`effective_quality_profile` lookup and the
    batched wanted-projection recompute, so the two can never drift.
    """
    for source, source_id, profile_id, explicit in levels:
        if explicit and profile_id is not None:
            return {
                "id": int(profile_id),
                "source": source,
                "source_id": int(source_id),
                "explicit": True,
            }
    return {
        "id": default_id,
        "source": "global",
        "source_id": None,
        "explicit": False,
    }


def playlist_quality_profile_states(
    conn,
    track_ids: Iterable[int],
    *,
    default_id: Optional[int] = None,
) -> Dict[int, Dict[str, Any]]:
    """Resolve playlist defaults for tracks already linked to mirror rows.

    A playlist profile is weaker than every explicit entity assignment.  This
    helper therefore only describes the playlist tier; callers decide whether
    the Track/Album/Artist cascade has already won.  Multiple playlists using
    the same profile are unambiguous.  Multiple *different* profiles produce
    an explicit conflict result and fall back to the app default only as a
    display value -- acquisition callers must honor ``conflict`` and stop.

    Older databases may not have the additive mirror-link columns yet.  Reads
    stay compatible and simply report no playlist tier until startup migration
    installs them.
    """
    normalized = sorted({int(track_id) for track_id in track_ids})
    if not normalized:
        return {}
    marks = ",".join("?" for _ in normalized)
    try:
        rows = conn.execute(
            f"""SELECT mpt.lib2_track_id AS track_id,
                       mp.id AS playlist_id, mp.name AS playlist_name,
                       mp.quality_profile_id AS profile_id,
                       qp.name AS profile_name
                  FROM mirrored_playlist_tracks mpt
                  JOIN mirrored_playlists mp ON mp.id=mpt.playlist_id
                  JOIN quality_profiles qp ON qp.id=mp.quality_profile_id
                 WHERE mpt.lib2_track_id IN ({marks})
                   AND mp.quality_profile_id IS NOT NULL
                 ORDER BY mpt.lib2_track_id, mp.id""",
            normalized,
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if _missing_playlist_schema(exc):
            return {}
        raise

    grouped: Dict[int, list[Dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[int, int]] = set()
    for row in rows:
        track_id = int(row["track_id"])
        playlist_id = int(row["playlist_id"])
        if (track_id, playlist_id) in seen:
            continue
        seen.add((track_id, playlist_id))
        grouped[track_id].append({
            "playlist_id": playlist_id,
            "playlist_name": str(row["playlist_name"] or ""),
            "profile_id": int(row["profile_id"]),
            "profile_name": str(row["profile_name"] or ""),
        })

    fallback = default_id if default_id is not None else default_quality_profile_id(conn)
    states: Dict[int, Dict[str, Any]] = {}
    for track_id, memberships in grouped.items():
        profile_ids = sorted({item["profile_id"] for item in memberships})
        playlist_ids = [item["playlist_id"] for item in memberships]
        if len(profile_ids) == 1:
            states[track_id] = {
                "id": profile_ids[0],
                "source": "playlist",
                "source_id": playlist_ids[0] if len(playlist_ids) == 1 else None,
                "explicit": False,
                "conflict": False,
                "playlist_ids": playlist_ids,
                "playlist_profiles": memberships,
            }
        else:
            states[track_id] = {
                "id": fallback,
                "source": "playlist",
                "source_id": None,
                "explicit": False,
                "conflict": True,
                "playlist_ids": playlist_ids,
                "playlist_profiles": memberships,
            }
    return states


def effective_quality_profiles(conn, track_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    """Batch Track > Album > Artist > Playlist > Global resolution."""
    normalized = sorted({int(track_id) for track_id in track_ids})
    if not normalized:
        return {}
    marks = ",".join("?" for _ in normalized)
    rows = conn.execute(
        f"""SELECT t.id AS track_id, t.quality_profile_id AS track_profile,
                   COALESCE(t.quality_profile_explicit, 0) AS track_explicit,
                   al.id AS album_id, al.quality_profile_id AS album_profile,
                   COALESCE(al.quality_profile_explicit, 0) AS album_explicit,
                   a.id AS artist_id, a.quality_profile_id AS artist_profile,
                   COALESCE(a.quality_profile_explicit, 0) AS artist_explicit
              FROM lib2_tracks t
              LEFT JOIN lib2_albums al ON al.id=t.album_id
              LEFT JOIN lib2_artists a ON a.id=al.primary_artist_id
             WHERE t.id IN ({marks})""",
        normalized,
    ).fetchall()
    default_id = default_quality_profile_id(conn)
    playlist_states = playlist_quality_profile_states(
        conn, normalized, default_id=default_id,
    )
    resolved: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        track_id = int(row["track_id"])
        result = resolve_profile_cascade(
            (
                ("track", track_id, row["track_profile"], row["track_explicit"]),
                ("album", row["album_id"], row["album_profile"], row["album_explicit"]),
                ("artist", row["artist_id"], row["artist_profile"], row["artist_explicit"]),
            ),
            default_id,
        )
        if result["source"] == "global" and track_id in playlist_states:
            result = playlist_states[track_id]
        resolved[track_id] = result
    return resolved


def playlist_quality_conflicts(
    conn,
    *,
    playlist_id: Optional[int] = None,
) -> list[Dict[str, Any]]:
    """Return unresolved equal-priority playlist defaults, grouped by track."""
    try:
        if playlist_id is None:
            rows = conn.execute(
                "SELECT DISTINCT lib2_track_id FROM mirrored_playlist_tracks "
                "WHERE lib2_track_id IS NOT NULL"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT lib2_track_id FROM mirrored_playlist_tracks "
                "WHERE playlist_id=? AND lib2_track_id IS NOT NULL",
                (int(playlist_id),),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        if _missing_playlist_schema(exc):
            return []
        raise
    track_ids = [int(row[0]) for row in rows]
    effective = effective_quality_profiles(conn, track_ids)
    conflicts = []
    for track_id in track_ids:
        state = effective.get(track_id) or {}
        if not state.get("conflict"):
            continue
        track = conn.execute(
            """SELECT t.title, al.title AS album_title, ar.name AS artist_name
                 FROM lib2_tracks t
                 LEFT JOIN lib2_albums al ON al.id=t.album_id
                 LEFT JOIN lib2_artists ar ON ar.id=al.primary_artist_id
                WHERE t.id=?""",
            (track_id,),
        ).fetchone()
        conflicts.append({
            "track_id": track_id,
            "title": str(track["title"] or "") if track else "",
            "album": str(track["album_title"] or "") if track else "",
            "artist": str(track["artist_name"] or "") if track else "",
            "playlists": state.get("playlist_profiles", []),
        })
    return conflicts


def effective_quality_profile(conn, entity: str, entity_id: int) -> Dict[str, Any]:
    """Return the effective profile id plus the level that owns the choice.

    Track resolution includes the playlist tier. Different equal-priority
    playlist defaults are returned as ``conflict=True``; callers must not
    silently acquire with the display fallback id.
    """
    normalized = _ENTITY_ALIASES.get(str(entity).strip().lower())
    if normalized == "tracks":
        result = effective_quality_profiles(conn, [int(entity_id)]).get(int(entity_id))
        if result is None:
            raise LookupError("Track not found")
        return result
    elif normalized == "albums":
        # LEFT JOIN for the same reason as the tracks branch above — an
        # album with a dangling primary_artist_id must resolve to its own
        # explicit choice or the default, not a spurious "Album not found".
        row = conn.execute(
            """SELECT al.id AS album_id, al.quality_profile_id AS album_profile,
                      COALESCE(al.quality_profile_explicit, 0) AS album_explicit,
                      a.id AS artist_id, a.quality_profile_id AS artist_profile,
                      COALESCE(a.quality_profile_explicit, 0) AS artist_explicit
                 FROM lib2_albums al
                 LEFT JOIN lib2_artists a ON a.id=al.primary_artist_id
                WHERE al.id=?""",
            (int(entity_id),),
        ).fetchone()
        if row is None:
            raise LookupError("Album not found")
        levels = (
            ("album", row["album_id"], row["album_profile"], row["album_explicit"]),
            ("artist", row["artist_id"], row["artist_profile"], row["artist_explicit"]),
        )
    elif normalized == "artists":
        row = conn.execute(
            """SELECT id AS artist_id, quality_profile_id AS artist_profile,
                      COALESCE(quality_profile_explicit, 0) AS artist_explicit
                 FROM lib2_artists WHERE id=?""",
            (int(entity_id),),
        ).fetchone()
        if row is None:
            raise LookupError("Artist not found")
        levels = ((
            "artist", row["artist_id"], row["artist_profile"], row["artist_explicit"]
        ),)
    else:
        raise ValueError("Unknown Library-v2 profile entity")

    return resolve_profile_cascade(levels, default_quality_profile_id(conn))


def assign_quality_profile(
    conn, entity: str, entity_id: int, profile_id: Optional[int]
) -> Dict[str, Any]:
    """Set/clear one explicit assignment and refresh inherited projections.

    ``profile_id=None`` means "inherit".  Descendant rows that do not own an
    explicit choice are updated for compatibility with older readers; their
    provenance stays inherited.  Explicit descendants are never overwritten.
    The caller owns the transaction.
    """
    normalized = _ENTITY_ALIASES.get(str(entity).strip().lower())
    tables = {"artists": "lib2_artists", "albums": "lib2_albums", "tracks": "lib2_tracks"}
    table = tables.get(normalized or "")
    if table is None:
        raise ValueError("Unknown Library-v2 profile entity")

    exists = conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (int(entity_id),)).fetchone()
    if exists is None:
        raise LookupError("Not found")

    if profile_id is None:
        if normalized == "artists":
            inherited_id = default_quality_profile_id(conn)
        elif normalized == "albums":
            parent = conn.execute(
                "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (int(entity_id),)
            ).fetchone()
            inherited_id = effective_quality_profile(
                conn, "artists", int(parent["primary_artist_id"])
            )["id"]
        else:
            parent = conn.execute(
                "SELECT album_id FROM lib2_tracks WHERE id=?", (int(entity_id),)
            ).fetchone()
            inherited_id = effective_quality_profile(
                conn, "albums", int(parent["album_id"])
            )["id"]
        conn.execute(
            f"UPDATE {table} SET quality_profile_id=?, quality_profile_explicit=0 "
            "WHERE id=?",
            (int(inherited_id), int(entity_id)),
        )
    else:
        conn.execute(
            f"UPDATE {table} SET quality_profile_id=?, quality_profile_explicit=1 "
            "WHERE id=?",
            (int(profile_id), int(entity_id)),
        )

    updated = 1
    if normalized == "artists":
        cur = conn.execute(
            """UPDATE lib2_albums
                  SET quality_profile_id=(
                      SELECT a.quality_profile_id FROM lib2_artists a
                       WHERE a.id=lib2_albums.primary_artist_id)
                WHERE primary_artist_id=?
                  AND COALESCE(quality_profile_explicit, 0)=0""",
            (int(entity_id),),
        )
        updated += max(0, cur.rowcount)
        cur = conn.execute(
            """UPDATE lib2_tracks
                  SET quality_profile_id=(
                      SELECT al.quality_profile_id FROM lib2_albums al
                       WHERE al.id=lib2_tracks.album_id)
                WHERE album_id IN (
                      SELECT id FROM lib2_albums WHERE primary_artist_id=?)
                  AND COALESCE(quality_profile_explicit, 0)=0""",
            (int(entity_id),),
        )
        updated += max(0, cur.rowcount)
    elif normalized == "albums":
        cur = conn.execute(
            """UPDATE lib2_tracks
                  SET quality_profile_id=(
                      SELECT al.quality_profile_id FROM lib2_albums al
                       WHERE al.id=lib2_tracks.album_id)
                WHERE album_id=?
                  AND COALESCE(quality_profile_explicit, 0)=0""",
            (int(entity_id),),
        )
        updated += max(0, cur.rowcount)

    result = effective_quality_profile(conn, normalized, int(entity_id))
    current_source = {
        "artists": "artist",
        "albums": "album",
        "tracks": "track",
    }[normalized]
    result["entity_explicit"] = result["source"] == current_source
    result["updated"] = updated
    return result


def lib2_quality_profile_for_artist(database, artist_name: str) -> Optional[int]:
    """The app-wide ``quality_profiles`` id assigned to this artist in
    Library v2, or ``None`` when unavailable."""
    if not artist_name:
        return None
    try:
        from config.settings import config_manager
        from core.library2.feature import library_v2_enabled
        library_v2_enabled(config_manager)
        from .importer import normalize_name
        key = normalize_name(artist_name)
        conn = database._get_connection()
        try:
            # Fast path: SQL case-insensitive match (avoids a full-table
            # python scan on every watchlist queue decision).
            row = conn.execute(
                "SELECT id FROM lib2_artists "
                "WHERE lower(name) = ? AND quality_profile_id IS NOT NULL LIMIT 1",
                (key,),
            ).fetchone()
            if row:
                return int(effective_quality_profile(conn, "artists", row["id"])["id"])
            for row in conn.execute(
                "SELECT id, name FROM lib2_artists "
                "WHERE quality_profile_id IS NOT NULL"
            ):
                if normalize_name(row["name"]) == key:
                    return int(effective_quality_profile(conn, "artists", row["id"])["id"])
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("lib2 profile lookup failed (%s): %s", artist_name, e)
    return None


__all__ = [
    "assign_quality_profile",
    "default_quality_profile_id",
    "effective_quality_profiles",
    "effective_quality_profile",
    "lib2_quality_profile_for_artist",
    "playlist_quality_conflicts",
    "playlist_quality_profile_states",
]
