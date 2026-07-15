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
from typing import Any, Dict, List, Mapping, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("library2.completeness")


def _json_object(raw: Any) -> Dict[str, str]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(key).strip().lower(): str(item).strip()
        for key, item in value.items()
        if str(key).strip() and str(item).strip()
    }


def _album_tracklist_context(
    conn: Any, album_id: int,
) -> Optional[Tuple[Any, Dict[str, Any], Dict[str, str]]]:
    """Return album row, edition reference and provider IDs for cache binding."""
    row = conn.execute(
        """SELECT al.title, al.primary_artist_id, al.tracklist_json,
                  al.year AS album_year,
                  al.release_date AS album_release_date,
                  al.track_count AS album_track_count,
                  al.expected_track_count AS album_expected_track_count,
                  al.spotify_id AS album_spotify_id,
                  al.musicbrainz_id AS album_musicbrainz_id,
                  al.external_ids AS album_external_ids,
                  ed.id AS release_edition_id,
                  ed.release_date AS edition_release_date,
                  ed.track_count AS edition_track_count,
                  ed.spotify_id AS edition_spotify_id,
                  ed.musicbrainz_id AS edition_musicbrainz_id,
                  ed.external_ids AS edition_external_ids
             FROM lib2_albums al
             LEFT JOIN lib2_release_editions ed
                    ON ed.release_group_id=al.id AND ed.is_default=1
            WHERE al.id=?""",
        (album_id,),
    ).fetchone()
    if row is None:
        return None

    source_ids = _json_object(row["album_external_ids"])
    source_ids.update(_json_object(row["edition_external_ids"]))
    spotify_id = row["edition_spotify_id"] or row["album_spotify_id"]
    musicbrainz_id = row["edition_musicbrainz_id"] or row["album_musicbrainz_id"]
    if spotify_id:
        source_ids["spotify"] = str(spotify_id)
    if musicbrainz_id:
        source_ids["musicbrainz"] = str(musicbrainz_id)
    release_date = (
        row["edition_release_date"]
        or row["album_release_date"]
        or (str(row["album_year"]) if row["album_year"] else None)
    )
    track_count = (
        row["edition_track_count"]
        or row["album_expected_track_count"]
        or row["album_track_count"]
    )
    reference = {
        "release_edition_id": row["release_edition_id"],
        "spotify_id": source_ids.get("spotify"),
        "musicbrainz_id": source_ids.get("musicbrainz"),
        "external_ids": dict(sorted(source_ids.items())),
        "release_date": release_date,
        "track_count": track_count,
    }
    return row, reference, source_ids


def _snapshot_tracks(snapshot: Any, reference: Mapping[str, Any]) -> Optional[List[dict]]:
    from core.library2.provider_adapters import TRACKLIST_PARSER_VERSION

    if snapshot is None or not snapshot.is_complete:
        return None
    if snapshot.parser_version != TRACKLIST_PARSER_VERSION:
        return None
    payload = snapshot.payload
    if not isinstance(payload, dict) or payload.get("reference") != dict(reference):
        return None
    tracks = payload.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        return None
    return [track for track in tracks if isinstance(track, dict)] or None


def _delete_track_row(conn, track_id: int) -> None:
    """Remove one track row and its dependent rows (not the edition prune)."""
    conn.execute(
        "DELETE FROM lib2_monitor_rules WHERE entity_type='track' AND entity_id=?",
        (track_id,),
    )
    conn.execute("DELETE FROM lib2_wanted_tracks WHERE track_id=?", (track_id,))
    conn.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (track_id,))
    conn.execute("DELETE FROM lib2_tracks WHERE id=?", (track_id,))


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
        """SELECT t.id, t.legacy_track_id, t.monitored,
                  EXISTS(SELECT 1 FROM lib2_track_files f WHERE f.track_id = t.id) AS has_file,
                  EXISTS(
                      SELECT 1 FROM lib2_monitor_rules r
                       WHERE r.entity_type='track' AND r.entity_id=t.id
                         AND r.monitored=1
                  ) AS has_positive_rule,
                  EXISTS(
                      SELECT 1 FROM lib2_wanted_tracks w
                       WHERE w.track_id=t.id AND w.wanted=1
                  ) AS is_wanted
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
        if (
            row["legacy_track_id"] is not None
            or row["has_file"]
            or row["monitored"]
            or row["has_positive_rule"]
            or row["is_wanted"]
        ):
            continue
        _delete_track_row(conn, row["id"])
        deleted += 1
    if deleted:
        from core.library2.editions import prune_orphaned_edition_rows
        prune_orphaned_edition_rows(conn.cursor())
    return deleted


def _norm_title(value: Any) -> str:
    """Casefold + collapse whitespace — a forgiving key for title matching."""
    return " ".join(str(value or "").split()).casefold()


def _unique_untouched_title_match(conn, album_id: int, title: str,
                                  touched_ids: set) -> Optional[int]:
    """A single not-yet-touched local track of this album with the same title.

    Returns its id only when the title unambiguously identifies ONE track to
    heal, so a duplicate title (remix/intro/outro name reused) never triggers
    a wrong heal. Used to repair corrupted track NUMBERS (§16.3): the title is
    the stable identity, the number is the field that got collapsed/
    duplicated, so matching on it re-keys the right row instead of confirming
    the corruption or inserting a duplicate.

    A title can also collide between a real (has-file) row and one or more
    fileless placeholders: an earlier resolve created the placeholder at the
    correct number before the file existed, then the file's own row got its
    number corrupted into colliding with something else (§17.2 — "DAISIES at
    number 1 AND 2"). Plain uniqueness would refuse to heal here (ambiguous),
    leaving the real row corrupted and the placeholder as a visible duplicate.
    When exactly one candidate has a file and the rest are safe-to-drop
    placeholders (no legacy link, not monitored, no positive monitor rule, not
    wanted), the real row is the one to heal and the redundant placeholder(s)
    are removed.
    """
    norm = _norm_title(title)
    if not norm:
        return None
    rows = [
        r for r in conn.execute(
            """SELECT t.id, t.title, t.legacy_track_id, t.monitored,
                      EXISTS(SELECT 1 FROM lib2_track_files f WHERE f.track_id=t.id) AS has_file,
                      EXISTS(
                          SELECT 1 FROM lib2_monitor_rules r
                           WHERE r.entity_type='track' AND r.entity_id=t.id
                             AND r.monitored=1
                      ) AS has_positive_rule,
                      EXISTS(
                          SELECT 1 FROM lib2_wanted_tracks w
                           WHERE w.track_id=t.id AND w.wanted=1
                      ) AS is_wanted
                 FROM lib2_tracks t WHERE t.album_id=?""",
            (album_id,),
        ).fetchall()
        if _norm_title(r["title"]) == norm and r["id"] not in touched_ids
    ]
    if len(rows) == 1:
        return rows[0]["id"]
    if len(rows) < 2:
        return None
    with_file = [r for r in rows if r["has_file"]]
    without_file = [r for r in rows if not r["has_file"]]
    if len(with_file) != 1 or not without_file:
        return None
    if any(
        r["legacy_track_id"] is not None or r["monitored"]
        or r["has_positive_rule"] or r["is_wanted"]
        for r in without_file
    ):
        return None
    for r in without_file:
        _delete_track_row(conn, r["id"])
    from core.library2.editions import prune_orphaned_edition_rows
    prune_orphaned_edition_rows(conn.cursor())
    return with_file[0]["id"]


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
    # A provider-confirmed complete list wins over an old undercount. Never
    # slice real entries to a stale expected_track_count (P1-26).
    if len(entries) > expected:
        expected = len(entries)
        conn.execute(
            """UPDATE lib2_albums
                  SET expected_track_count=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (expected, album_id),
        )
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

        # §16.3 heal: prefer a unique, not-yet-touched local row with the SAME
        # title over the (disc, number) key. When track numbers got corrupted
        # (e.g. a whole album collapsed onto number 1, or duplicated), that key
        # IS the corrupt field, so it could only re-confirm the collapse or add
        # duplicate rows — which is exactly why "Update Discography" never
        # repaired it. Matching on the stable title lets a correctly-fetched
        # tracklist rewrite the numbers IN PLACE.
        heal_id = _unique_untouched_title_match(conn, album_id, title, touched_ids)
        existing = None if heal_id is not None else conn.execute(
            """SELECT id FROM lib2_tracks
               WHERE album_id=? AND COALESCE(disc_number, 1)=? AND track_number=?""",
            (album_id, disc, number),
        ).fetchone()
        if heal_id is not None:
            conn.execute(
                """UPDATE lib2_tracks
                      SET track_number=?, disc_number=?,
                          spotify_id=COALESCE(NULLIF(spotify_id, ''), ?),
                          duration=COALESCE(duration, ?),
                          updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (number, disc, spotify_id, duration, heal_id),
            )
            track_id = heal_id
            touched_ids.add(track_id)
        elif existing:
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
            from core.library2.profile_lookup import default_quality_profile_id
            conn.execute(
                """INSERT INTO lib2_tracks(album_id, title, track_number, disc_number,
                          duration, spotify_id, monitored, quality_profile_id)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (album_id, title, number, disc, duration, spotify_id,
                 1 if al["monitored"] else 0,
                 al["quality_profile_id"] or default_quality_profile_id(conn)),
            )
            track_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            created += 1
            touched_ids.add(track_id)

        conn.execute(
            """INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position)
               VALUES(?,?, 'primary', 0)""",
            (track_id, al["primary_artist_id"]),
        )
    changed = created + _trim_excess_fileless_tracks(
        conn, album_id, expected, protect_ids=touched_ids
    )
    # Protected local/wanted rows can legitimately extend beyond the provider
    # count. Converge the stored expectation so precache does not retry the
    # same intentional mismatch forever.
    remaining_count = conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?", (album_id,)
    ).fetchone()[0]
    if remaining_count > expected:
        conn.execute(
            """UPDATE lib2_albums
                  SET expected_track_count=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (remaining_count, album_id),
        )
    # Every newly materialized provider row enters the authoritative wanted
    # projection immediately, even when browsing (not monitoring) created it.
    if touched_ids:
        from core.library2.wanted import recompute_wanted
        recompute_wanted(conn, track_ids=sorted(touched_ids))
    return changed


def resolve_tracklist(config_manager, conn, album_id: int) -> Optional[List[dict]]:
    """Return + cache the album's canonical tracklist. None when unavailable."""
    context = _album_tracklist_context(conn, album_id)
    if context is None:
        return None
    al, reference, source_ids = context

    from core.library2.provider_snapshots import (
        get_latest_provider_snapshot, record_provider_snapshot)
    snapshot = get_latest_provider_snapshot(
        conn, entity_type="album", entity_id=album_id, scope="tracklist")
    durable_tracks = _snapshot_tracks(snapshot, reference)
    cached: Optional[List[dict]] = None
    if al["tracklist_json"]:
        try:
            parsed = json.loads(al["tracklist_json"])
            if isinstance(parsed, list) and parsed:
                cached = [track for track in parsed if isinstance(track, dict)]
        except (ValueError, TypeError):
            pass
    if cached and snapshot is None:
        # Upgrade path: preserve an existing cache once, but bind it to the
        # current edition reference so a later edition switch invalidates it.
        from core.library2.provider_adapters import TRACKLIST_PARSER_VERSION
        record_provider_snapshot(
            conn,
            provider="legacy-cache",
            entity_type="album",
            entity_id=album_id,
            scope="tracklist",
            parser_version=TRACKLIST_PARSER_VERSION,
            payload={"reference": reference, "tracks": cached},
            is_complete=True,
        )
        durable_tracks = cached
    elif snapshot is not None and durable_tracks is None and cached:
        logger.info(
            "Invalidating tracklist cache for album %s after edition/provider change",
            album_id,
        )
        cached = None
        conn.execute(
            """UPDATE lib2_albums
                  SET tracklist_json=NULL, tracklist_status='idle',
                      tracklist_error=NULL, tracklist_retry_at=NULL
                WHERE id=?""",
            (album_id,),
        )
        conn.commit()

    reusable = durable_tracks or cached
    if reusable:
        _persist_tracklist_tracks(conn, album_id, reusable)
        conn.execute(
            """UPDATE lib2_albums
                  SET tracklist_json=?, tracklist_status='ready',
                      tracklist_attempts=0, tracklist_error=NULL,
                      tracklist_retry_at=NULL
                WHERE id=?""",
            (json.dumps(reusable), album_id),
        )
        conn.commit()
        return reusable

    artist = conn.execute(
        "SELECT name FROM lib2_artists WHERE id=?", (al["primary_artist_id"],)
    ).fetchone()
    artist_name = artist["name"] if artist else ""
    from core.library2.provider_adapters import fetch_album_tracklist
    provider_result = fetch_album_tracklist(
        al["title"],
        artist_name,
        source_album_ids=source_ids,
        release_date=reference["release_date"],
        expected_track_count=reference["track_count"],
    )
    if provider_result:
        tracks = provider_result.track_payloads()
        try:
            record_provider_snapshot(
                conn,
                provider=provider_result.provider,
                entity_type="album",
                entity_id=album_id,
                scope="tracklist",
                provider_entity_id=provider_result.provider_entity_id,
                parser_version=provider_result.parser_version,
                payload=provider_result.snapshot_payload(reference),
                is_complete=provider_result.is_complete,
            )
            conn.execute(
                """UPDATE lib2_albums
                      SET tracklist_json=?, tracklist_status='ready',
                          tracklist_attempts=0, tracklist_error=NULL,
                          tracklist_retry_at=NULL
                    WHERE id=?""",
                (json.dumps(tracks), album_id),
            )
            _persist_tracklist_tracks(conn, album_id, tracks)
            conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("tracklist cache write failed (%s): %s", album_id, e)
        return tracks
    return None


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
