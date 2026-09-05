"""Ownership follows the recording, not the album position (§49.6(c)).

``lib2_track_files.track_id`` points at exactly one ``lib2_tracks`` row, and
that row belongs to exactly one album. A song that sits on a single *and* on
the album it was lifted from can therefore only ever read as "present" in one
of the two places — the other renders as a gap the user is invited to
re-download. The 23 August production DB holds 21 such recordings, 7 of them
split that way, plus two where the re-download already happened and the same
audio now occupies the disk twice.

The edition model (``core/library2/editions.py``, ADR-04) already knows the
answer: recordings merge on hard identifiers only (ISRC / MusicBrainz
recording / Spotify track), and ``lib2_release_tracks`` maps every position of
every edition onto one. What was missing is a read path that uses it.

**The guard is not optional.** Providers hand different audio the same hard
id: on ``AM`` the iTunes-Festival takes of "Do I Wanna Know?", "Fireside" and
"One for the Road" carry the ISRC of the studio cut, ``Pretty. Odd.`` does the
same for "Nine In the Afternoon (Radio Mix)", and "Sleepwalker" / "Sleepwalker
- Slowed" share a Deezer-derived ISRC. Propagating ownership across those
would claim a live recording is on disk because the studio one is. Titles are
what separate them: across the 21 cross-release groups in the production DB,
normalized-title equality accepts all 20 genuine pairs and rejects the one
false pair, so it is the discriminator this module uses.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

_ACTIVE_FILE = "COALESCE(f.file_state,'active') NOT IN ('missing_confirmed','deleted')"

_IN_CHUNK = 400


def normalize_title(value: Any) -> str:
    """Casefold + collapse whitespace — the key two positions must share."""
    return " ".join(str(value or "").split()).casefold()


def ensure_sql_helpers(conn: Any) -> bool:
    """Expose :func:`normalize_title` to SQL as ``lib2_title_key``.

    The album view resolves borrowed files in Python and the wanted views do
    it inside a SQL predicate. If the two folds disagreed, one screen would
    call a track present and the other would list it as missing — the class
    of bug §44 LV2-CNT-01 was about. Registering the one function on the
    connection keeps a single definition. Returns False when the handle
    cannot take a function, so callers fall back to ``LOWER(TRIM(...))``.
    """
    try:
        conn.create_function("lib2_title_key", 1, normalize_title,
                             deterministic=True)
        return True
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        conn.create_function("lib2_title_key", 1, normalize_title)
        return True
    except Exception:  # noqa: BLE001 - any handle that cannot take one
        return False


def _title_key_sql(expr: str, registered: bool) -> str:
    return f"lib2_title_key({expr})" if registered else f"LOWER(TRIM({expr}))"


def owned_by_recording_sql(conn: Any, track_alias: str = "t") -> str:
    """SQL predicate: this position's audio is on disk under another release.

    Same recording (``lib2_release_tracks`` — merged on hard ids only) AND
    the same folded title, because providers hand live cuts the studio take's
    ISRC (see the module docstring).
    """
    key = _title_key_sql("{col}", ensure_sql_helpers(conn))
    return f"""
    EXISTS (
        SELECT 1
          FROM lib2_release_tracks mine
          JOIN lib2_release_tracks theirs
                ON theirs.recording_id = mine.recording_id
               AND theirs.track_id IS NOT NULL
               AND theirs.track_id <> {track_alias}.id
          JOIN lib2_tracks other ON other.id = theirs.track_id
          JOIN lib2_track_files f ON f.track_id = other.id
         WHERE mine.track_id = {track_alias}.id
           AND f.path IS NOT NULL AND f.path <> ''
           AND COALESCE(f.file_state,'active')
               NOT IN ('missing_confirmed','deleted')
           AND {key.format(col='other.title')}
             = {key.format(col=track_alias + '.title')}
    )
    """


def _chunks(values: List[int]) -> Iterable[List[int]]:
    for start in range(0, len(values), _IN_CHUNK):
        yield values[start:start + _IN_CHUNK]


def reference_owners(conn: Any, track_ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    """Map each fileless track id to the sibling position that owns its file.

    A sibling qualifies when it shares the recording AND the normalized title,
    and holds an active file. Tracks that own a file themselves, and tracks
    with no qualifying sibling, are absent from the result.
    """
    wanted = [int(t) for t in track_ids if t is not None]
    if not wanted:
        return {}

    key = _title_key_sql("{col}", ensure_sql_helpers(conn))
    title_match = (f"{key.format(col='other.title')}"
                   f" = {key.format(col='me.title')}")
    owners: Dict[int, Dict[str, Any]] = {}
    for chunk in _chunks(wanted):
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT me.id            AS track_id,
                   me.title         AS title,
                   other.id         AS owner_track_id,
                   other.title      AS owner_title,
                   other.album_id   AS owner_album_id,
                   al.title         AS owner_album_title,
                   al.album_type    AS owner_album_type,
                   f.id             AS file_id,
                   f.path           AS path,
                   f.is_primary     AS is_primary
              FROM lib2_tracks me
              JOIN lib2_release_tracks mine ON mine.track_id = me.id
              JOIN lib2_release_tracks theirs
                    ON theirs.recording_id = mine.recording_id
                   AND theirs.track_id IS NOT NULL
                   AND theirs.track_id <> me.id
              JOIN lib2_tracks other ON other.id = theirs.track_id
              JOIN lib2_albums al ON al.id = other.album_id
              JOIN lib2_track_files f ON f.track_id = other.id
             WHERE me.id IN ({marks})
               AND {title_match}
               AND {_ACTIVE_FILE}
               AND NOT EXISTS (
                   SELECT 1 FROM lib2_track_files own
                    WHERE own.track_id = me.id
                      AND COALESCE(own.file_state,'active')
                          NOT IN ('missing_confirmed','deleted'))
             ORDER BY me.id, f.is_primary DESC, f.id
            """,
            tuple(chunk),
        ).fetchall()
        for row in rows:
            track_id = int(row["track_id"])
            if track_id in owners:
                continue
            owners[track_id] = {
                "track_id": int(row["owner_track_id"]),
                "album_id": int(row["owner_album_id"]),
                "album_title": row["owner_album_title"],
                "album_type": row["owner_album_type"],
                "file_id": int(row["file_id"]),
                "path": row["path"],
            }
    return owners


#: How much of a home a release type is. A song that appears on an album
#: belongs to the album (user decision, docs §49.11): that is the position the
#: user reasons about, and the one quality checks, upgrades and deletion should
#: act from. Equal ranks never move — there would be no reason to prefer one.
_HOME_RANK = {"album": 3, "compilation": 3, "ep": 2, "single": 1}


def _home_rank(album_type: Any) -> int:
    return _HOME_RANK.get(str(album_type or "").strip().lower(), 2)


def prefer_album_home(conn: Any) -> Dict[str, int]:
    """Re-point files whose recording also sits on a higher-ranked release.

    The file does not move on disk — only the catalogue's idea of which
    position owns it. A position that already holds a file of its own is
    never touched: two files for one recording are two real copies, which is
    a duplicate finding, not a re-home. Idempotent; does not commit.
    """
    key = _title_key_sql("{col}", ensure_sql_helpers(conn))
    rows = conn.execute(
        f"""
        SELECT f.id            AS file_id,
               f.track_id      AS from_track_id,
               src_al.album_type AS from_type,
               dst.id          AS to_track_id,
               dst_al.album_type AS to_type
          FROM lib2_track_files f
          JOIN lib2_tracks src ON src.id = f.track_id
          JOIN lib2_albums src_al ON src_al.id = src.album_id
          JOIN lib2_release_tracks mine ON mine.track_id = src.id
          JOIN lib2_release_tracks theirs
                ON theirs.recording_id = mine.recording_id
               AND theirs.track_id IS NOT NULL
               AND theirs.track_id <> src.id
          JOIN lib2_tracks dst ON dst.id = theirs.track_id
          JOIN lib2_albums dst_al ON dst_al.id = dst.album_id
         WHERE COALESCE(f.file_state,'active')
               NOT IN ('missing_confirmed','deleted')
           AND {key.format(col='dst.title')} = {key.format(col='src.title')}
           AND NOT EXISTS (
               SELECT 1 FROM lib2_track_files own
                WHERE own.track_id = dst.id
                  AND COALESCE(own.file_state,'active')
                      NOT IN ('missing_confirmed','deleted'))
         ORDER BY f.id
        """
    ).fetchall()

    moves: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        gain = _home_rank(row["to_type"]) - _home_rank(row["from_type"])
        if gain <= 0:
            continue
        best = moves.get(int(row["file_id"]))
        if best is None or gain > best["gain"] or (
            gain == best["gain"] and int(row["to_track_id"]) < best["to_track_id"]
        ):
            moves[int(row["file_id"])] = {
                "gain": gain, "to_track_id": int(row["to_track_id"])}

    for file_id, move in moves.items():
        conn.execute(
            "UPDATE lib2_track_files"
            "   SET track_id=?, updated_at=CURRENT_TIMESTAMP"
            " WHERE id=?",
            (move["to_track_id"], file_id),
        )
    return {"rehomed": len(moves), "considered": len(rows)}


def reference_owner(conn: Any, track_id: int) -> Optional[Dict[str, Any]]:
    """``reference_owners`` for a single track."""
    return reference_owners(conn, [track_id]).get(int(track_id))


__all__ = [
    "ensure_sql_helpers",
    "normalize_title",
    "owned_by_recording_sql",
    "prefer_album_home",
    "reference_owner",
    "reference_owners",
]
