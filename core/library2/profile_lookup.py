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

from typing import Any, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.profile_lookup")

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


def effective_quality_profile(conn, entity: str, entity_id: int) -> Dict[str, Any]:
    """Return the effective profile id plus the level that owns the choice.

    Playlist defaults deliberately are not guessed here: §52.12 still needs a
    deterministic same-track/multiple-playlist conflict rule.  Until that is
    decided, this resolver implements every unambiguous level and finishes at
    the app-wide default.
    """
    normalized = _ENTITY_ALIASES.get(str(entity).strip().lower())
    if normalized == "tracks":
        # LEFT JOINs: a track whose album has a NULL/dangling
        # primary_artist_id must still resolve (falling through to the
        # default profile at that level), not raise "Track not found" — an
        # INNER JOIN would drop the row entirely and make a real track
        # indistinguishable from a nonexistent one (matches the batched
        # resolver in core.library2.wanted.recompute_wanted).
        row = conn.execute(
            """SELECT t.id AS track_id, t.quality_profile_id AS track_profile,
                      COALESCE(t.quality_profile_explicit, 0) AS track_explicit,
                      al.id AS album_id, al.quality_profile_id AS album_profile,
                      COALESCE(al.quality_profile_explicit, 0) AS album_explicit,
                      a.id AS artist_id, a.quality_profile_id AS artist_profile,
                      COALESCE(a.quality_profile_explicit, 0) AS artist_explicit
                 FROM lib2_tracks t
                 LEFT JOIN lib2_albums al ON al.id=t.album_id
                 LEFT JOIN lib2_artists a ON a.id=al.primary_artist_id
                WHERE t.id=?""",
            (int(entity_id),),
        ).fetchone()
        if row is None:
            raise LookupError("Track not found")
        levels = (
            ("track", row["track_id"], row["track_profile"], row["track_explicit"]),
            ("album", row["album_id"], row["album_profile"], row["album_explicit"]),
            ("artist", row["artist_id"], row["artist_profile"], row["artist_explicit"]),
        )
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
    "effective_quality_profile",
    "lib2_quality_profile_for_artist",
]
