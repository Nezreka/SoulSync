"""Read queries for the Library v2 API.

All functions take an open sqlite3 connection (``row_factory = sqlite3.Row``) and
return plain dicts/lists ready to serialize. Roll-up counts go through the
``lib2_album_artists`` / ``lib2_track_artists`` junctions so a release or track that
credits multiple artists is counted under *each* of them (a song by two artists
shows under both, but is stored once).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .metadata_overrides import project_metadata, project_metadata_many
from .paths import library_relative_path
from .sql_util import (
    intent_profile_id, monitored_sql, owner_clause, pick, scoped_monitored, scope_visibility_sql,
)
from .status import compute_metadata_gaps, file_status, metadata_scan_status, quality_tier, _coerce_tags
from .track_files import primary_order

def _alpha_key(column: str) -> str:
    """Upstream's A-Z key (33da6be49): leading punctuation is skipped, so
    '"Weird Al" Yankovic' files under W and '*NSYNC' under N. One definition,
    shared with the compatibility API in ``database/music_database.py``."""
    from database.music_database import _library_sort_key_sql

    return _library_sort_key_sql(column)


def _media_server_sources_many(conn, entity_type: str, entity_ids: List[int]
                               ) -> Dict[int, List[str]]:
    """Positive Plex/Jellyfin/Navidrome recognitions for API projections."""
    ids = sorted({int(value) for value in entity_ids if value is not None})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    if entity_type == "artist":
        rows = conn.execute(
            f"""SELECT COALESCE(a.canonical_artist_id,a.id) AS entity_id,
                       m.server_source
                  FROM lib2_media_server_mappings m
                  JOIN lib2_artists a ON a.id=m.entity_id
                 WHERE m.entity_type='artist'
                   AND COALESCE(a.canonical_artist_id,a.id) IN ({marks})
                   AND m.match_status='recognized'
                 GROUP BY COALESCE(a.canonical_artist_id,a.id),m.server_source
                 ORDER BY m.server_source""",
            ids,
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT entity_id,server_source
                  FROM lib2_media_server_mappings
                 WHERE entity_type=? AND entity_id IN ({marks})
                   AND match_status='recognized'
                 GROUP BY entity_id,server_source ORDER BY server_source""",
            [entity_type, *ids],
        ).fetchall()
    result = {entity_id: [] for entity_id in ids}
    for row in rows:
        result.setdefault(int(row[0]), []).append(str(row[1]))
    return result


def _json_dict(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (ValueError, TypeError):
        return {}


def _quality_profile_dict(row: Any) -> Optional[Dict[str, Any]]:
    """Shape an app-wide ``quality_profiles`` row for the Library v2 UI."""
    if row is None:
        return None
    keys = set(row.keys())

    def _ranked(raw: Any) -> List[Any]:
        try:
            val = json.loads(raw) if isinstance(raw, str) else (raw or [])
            return val if isinstance(val, list) else []
        except (ValueError, TypeError):
            return []

    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] if "description" in keys else None,
        "upgrade_policy": row["upgrade_policy"] or "none",
        "upgrade_cutoff_index": int(row["upgrade_cutoff_index"] or 0) if "upgrade_cutoff_index" in keys else 0,
        "ranked_targets": _ranked(row["ranked_targets"] if "ranked_targets" in keys else None),
        "repair_job_id": row["repair_job_id"] if "repair_job_id" in keys else "quality_upgrade",
        "repair_settings": _json_dict(row["repair_settings"] if "repair_settings" in keys else None),
        "is_default": bool(row["is_default"]),
    }


def _quality_profile_assignment(conn: Any, entity: str, entity_id: int) -> Dict[str, Any]:
    """Shared API projection for §52.2 effective-profile provenance."""
    from core.library2.profile_lookup import effective_quality_profile

    return effective_quality_profile(conn, entity, int(entity_id))


def _json_list(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (ValueError, TypeError):
        return []


def _artist_provider_ids(row: Any) -> Dict[str, str]:
    """Every qualified provider id stored on an artist row (guide §2.5): the
    dedicated ``spotify_id`` column plus the ``external_ids`` namespaces."""
    from core.library2.provider_ids import parse_external_ids
    ids = parse_external_ids(row["external_ids"] if "external_ids" in row.keys() else None)
    if row["spotify_id"]:
        ids.setdefault("spotify", str(row["spotify_id"]))
    # Both promoted columns, not just Spotify: an MBID written by the importer
    # lives only in the column, and a reader that missed it (the ported
    # Concerts section asks setlist.fm by MBID) sees an artist with no
    # MusicBrainz identity at all.
    if "musicbrainz_id" in row.keys() and row["musicbrainz_id"]:
        ids.setdefault("musicbrainz", str(row["musicbrainz_id"]))
    return ids


def legacy_api_artists_page(conn, *, search_query: str = "", letter: str = "all",
                            page: int = 1, limit: int = 75,
                            watchlist_filter: str = "all", source_filter: str = "",
                            profile_id: int = 1, quality_filter: str = "",
                            sort: str = "name") -> Dict[str, Any]:
    """Historical response adapter; only this shape uses legacy back-references."""
    from .artist_reader import read_artist_page, artist_pagination
    from .provider_ids import parse_external_ids

    rows, total_count = read_artist_page(
        conn, search=search_query, letter=letter, page=page, limit=limit,
        watchlist_filter=watchlist_filter, source_filter=source_filter,
        profile_id=profile_id, quality_filter=quality_filter, sort=sort,
        include_size=False, require_legacy_id=True)
    projected = project_metadata_many(
        conn, entity_type="artist",
        provider_fields={int(row["id"]): dict(row) for row in rows})
    artists: List[Dict[str, Any]] = []
    for row in rows:
        effective, _ = projected[int(row["id"])]
        ids = parse_external_ids(row["external_ids"])
        if row["spotify_id"]:
            ids.setdefault("spotify", str(row["spotify_id"]))
        if row["musicbrainz_id"]:
            ids.setdefault("musicbrainz", str(row["musicbrainz_id"]))
        artists.append({
            "id": row["legacy_artist_id"],
            "lib2_artist_id": row["id"],
            "name": effective["name"],
            "image_url": effective["image_url"],
            "genres": _json_list(effective["genres"]),
            "musicbrainz_id": ids.get("musicbrainz"),
            "spotify_artist_id": ids.get("spotify"),
            "itunes_artist_id": ids.get("itunes"),
            "deezer_id": ids.get("deezer"),
            "audiodb_id": ids.get("audiodb"),
            "discogs_id": ids.get("discogs"),
            "lastfm_url": ids.get("lastfm"),
            "genius_url": ids.get("genius"),
            "tidal_id": ids.get("tidal"),
            "qobuz_id": ids.get("qobuz"),
            # The column is where the SoulID worker writes (docs §50.4.4.12).
            # The two external_ids keys stay as fallbacks: `soul` is what the
            # typed-metadata converter emits for an importer-supplied id, and a
            # row that only ever passed through it has nothing in the column.
            "soul_id": row["soul_id"] or ids.get("soulid") or ids.get("soul"),
            "amazon_id": ids.get("amazon"),
            "album_count": int((row["album_count"] or 0) + (row["single_count"] or 0)),
            "track_count": int(row["track_count"] or 0),
            # Not `row["monitored"]`: that is the admin's global lib2 intent,
            # and telling a guest profile it owns the admin's monitoring is the
            # regression this reproduces the legacy meaning to avoid.
            "is_watched": row["is_watched"],
        })

    return {"artists": artists, "pagination": artist_pagination(page, limit, total_count)}


def find_artists_by_name(conn, name: str, *, limit: int = 5) -> List[Dict[str, Any]]:
    """Name lookup for non-UI consumers, with the two fields they need.

    The metadata-update worker pushes genres into Plex/Jellyfin and uses a
    stored Spotify id to skip a provider search. It read those from the legacy
    ``artists`` row through ``MusicDatabase.search_artists`` /
    ``api_get_artist``; both fields exist on the lib2 row.

    Deliberately not ``list_artists``: that one carries the artist page's whole
    roll-up — album/single/track counts, quality-profile resolution and a
    window function over every file for the size column. A worker asking "do we
    know this name?" should not pay for any of it.

    The filter matches the stored name and overrides are projected afterwards,
    exactly as ``list_artists`` does. Alias members are folded away (§40) so a
    caller cannot push the same genres twice.
    """
    text = str(name or "").strip()
    if not text:
        return []
    rows = conn.execute(
        """
        SELECT id, name, genres, spotify_id, external_ids
          FROM lib2_artists
         WHERE canonical_artist_id IS NULL AND name LIKE :pattern
         ORDER BY LENGTH(name), name
         LIMIT :limit
        """,
        {"pattern": f"%{text}%", "limit": max(1, min(int(limit), 50))},
    ).fetchall()
    projected = project_metadata_many(
        conn,
        entity_type="artist",
        provider_fields={int(row["id"]): dict(row) for row in rows},
    )
    found = []
    for row in rows:
        effective, _overrides = projected[int(row["id"])]
        found.append({
            "id": row["id"],
            "name": effective["name"],
            "genres": _json_list(effective["genres"]),
            "spotify_id": _artist_provider_ids(row).get("spotify"),
        })
    return found


def _scoped_flag(conn, entity: str, row) -> bool:
    """A row's monitored flag as this library sees it (see ``monitored_sql``),
    for rows read with ``SELECT *``."""
    if not row:
        return False
    override = scoped_monitored(conn, entity, [row["id"]])
    if override is None:
        return bool(row["monitored"])
    return override.get(int(row["id"]), False)


def list_artists(conn, *, search: str = "", sort: str = "name", monitored: str = "all",
                 page: int = 1, limit: int = 75, include_size: bool = True,
                 letter: str = "all", watchlist_filter: str = "all",
                 source_filter: str = "", quality_filter: str = "",
                 profile_id: int = 1) -> Tuple[List[Dict[str, Any]], int]:
    """Library-v2 display adapter over the shared artist page reader."""
    from .artist_reader import read_artist_page

    rows, total = read_artist_page(
        conn, search=search, sort=sort, monitored=monitored, page=page,
        limit=limit, include_size=include_size, letter=letter,
        watchlist_filter=watchlist_filter, source_filter=source_filter,
        quality_filter=quality_filter, profile_id=profile_id)
    projected = project_metadata_many(
        conn,
        entity_type="artist",
        provider_fields={int(row["id"]): dict(row) for row in rows},
    )
    media_sources = _media_server_sources_many(
        conn, "artist", [int(row["id"]) for row in rows])
    artists = []
    for r in rows:
        effective, overrides = projected[int(r["id"])]
        track_count = r["track_count"] or 0
        present = r["track_files_present"] or 0
        artists.append({
            "id": r["id"],
            "name": effective["name"],
            "image_url": effective["image_url"],
            "genres": _json_list(effective["genres"]),
            "monitored": bool(r["monitored"]),
            "monitor_new_items": r["monitor_new_items"],
            "quality_profile_id": r["quality_profile_id"],
            "quality_profile_source": (
                "artist" if bool(r["quality_profile_explicit"]) else "global"
            ),
            "quality_profile_source_id": (
                r["id"] if bool(r["quality_profile_explicit"]) else None
            ),
            "quality_profile_explicit": bool(r["quality_profile_explicit"]),
            "added_at": r["added_at"],
            "album_count": r["album_count"] or 0,
            "single_count": r["single_count"] or 0,
            "track_count": track_count,
            "tracks_present": present,
            "tracks_missing": max(0, track_count - present),
            "total_size_bytes": r["total_size_bytes"] or 0,
            "media_server_sources": media_sources.get(int(r["id"]), []),
            "user_overrides": overrides,
        })
    return artists, total


_ALBUM_SORTS = {
    "title": _alpha_key("al.title") + ", al.title COLLATE NOCASE",
    "year_desc": "COALESCE(al.year, 0) = 0, al.year DESC, al.title COLLATE NOCASE",
    "year_asc": "COALESCE(al.year, 0) = 0, al.year ASC, al.title COLLATE NOCASE",
    "added": "al.added_at DESC",
}


def list_albums(conn, *, search: str = "", sort: str = "title", monitored: str = "all",
                page: int = 1, limit: int = 75) -> Tuple[List[Dict[str, Any]], int]:
    """The whole library by release (upstream's album browse, A07).

    The releases the artist list counts -- a library row, or one this library
    monitors -- in the caller's library, newest-first or by title or year.
    Unknown years sort last either way.
    """
    page = max(1, int(page))
    limit = max(1, min(int(limit), 500))
    album_monitored = monitored_sql("album", "al")
    # Same membership as the artist list: a live file, or monitoring intent on
    # the release or one of its tracks. An empty release a cleared wish left
    # behind is not part of the library any more.
    from core.library2.sql_util import intent_profile_id, owned_sql
    clauses = [f"({owned_sql('album', 'al')} OR {album_monitored}=1 OR EXISTS ("
               "SELECT 1 FROM lib2_tracks wt JOIN lib2_wanted_tracks ww ON ww.track_id=wt.id "
               f"AND ww.profile_id={intent_profile_id()} AND ww.wanted=1 WHERE wt.album_id=al.id))"]
    visible = scope_visibility_sql("album", "al")
    if visible:
        clauses.append(visible)
    params: Dict[str, Any] = {}
    if search:
        escaped = (str(search).replace("\\", "\\\\")
                   .replace("%", "\\%").replace("_", "\\_"))
        params["like"] = f"%{escaped}%"
        clauses.append("(al.title LIKE :like ESCAPE '\\'"
                       " OR ar.name LIKE :like ESCAPE '\\')")
    if monitored == "monitored":
        clauses.append(f"{album_monitored} = 1")
    elif monitored == "unmonitored":
        clauses.append(f"{album_monitored} = 0")
    where = " AND ".join(clauses)
    base = ("FROM lib2_albums al LEFT JOIN lib2_artists ar"
            " ON ar.id = al.primary_artist_id WHERE " + where)
    total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    track_monitored = monitored_sql("track", "t")
    tf_owner = owner_clause(column="tf.owner_profile_id")
    rows = conn.execute(
        f"""
        WITH page_albums AS MATERIALIZED (
            SELECT al.id, al.title, al.album_type, al.year, al.image_url, al.added_at,
                   {album_monitored} AS monitored,
                   COALESCE(ar.canonical_artist_id, ar.id) AS artist_id,
                   ar.name AS artist_name
              {base}
             ORDER BY {_ALBUM_SORTS.get(sort, _ALBUM_SORTS["title"])}, al.id
             LIMIT :limit OFFSET :offset
        ),
        stats AS (
            SELECT t.album_id,
                   COUNT(DISTINCT CASE
                       WHEN COALESCE(w.wanted, {track_monitored})=1 OR tf.id IS NOT NULL
                       THEN t.id END) AS track_count,
                   COUNT(DISTINCT CASE
                       WHEN tf.id IS NOT NULL
                        AND COALESCE(tf.file_state, 'active')
                            NOT IN ('missing_confirmed','deleted')
                       THEN t.id END) AS tracks_present
              FROM page_albums pa
              CROSS JOIN lib2_tracks t ON t.album_id = pa.id
              LEFT JOIN lib2_wanted_tracks w
                     ON w.track_id = t.id AND w.profile_id = {intent_profile_id()}
              LEFT JOIN lib2_track_files tf ON tf.track_id = t.id{tf_owner}
             GROUP BY t.album_id
        )
        SELECT pa.*, COALESCE(s.track_count, 0) AS track_count,
               COALESCE(s.tracks_present, 0) AS tracks_present
          FROM page_albums pa LEFT JOIN stats s ON s.album_id = pa.id
        """,
        {**params, "limit": limit, "offset": (page - 1) * limit},
    ).fetchall()
    albums = []
    for r in rows:
        count, present = int(r["track_count"] or 0), int(r["tracks_present"] or 0)
        albums.append({
            "id": r["id"], "title": r["title"], "album_type": r["album_type"],
            "year": r["year"], "image_url": r["image_url"], "added_at": r["added_at"],
            "monitored": bool(r["monitored"]), "artist_id": r["artist_id"],
            "artist_name": r["artist_name"], "track_count": count,
            "tracks_present": present, "tracks_missing": max(0, count - present),
        })
    return albums, int(total)


def list_artist_track_files(conn, artist_id: int, *, search: str = "",
                            page: int = 1, limit: int = 100
                            ) -> Tuple[List[Dict[str, Any]], int]:
    """Paginated flat file list for one artist (C2: Lidarr "Manage Track
    Files"). Mirrors ``core.library2.file_delete._scope_snapshot``'s artist
    scope exactly (alias-group ``primary_artist_id``, non-deleted files) so a selection
    made from this list lines up with what the ADR-05 preview/execute
    endpoints will actually see for the same file ids.
    """
    page = max(1, int(page))
    limit = max(1, min(int(limit), 500))
    offset = (page - 1) * limit
    from core.library2.artist_aliases import resolve_alias_group

    artist_ids = resolve_alias_group(conn, artist_id)
    artist_marks = ",".join(f":artist_id_{i}" for i in range(len(artist_ids)))
    # The file ids this returns are exactly what the ADR-05 preview/execute
    # endpoints accept, so an unscoped list is a way to select -- and destroy --
    # another library's files. The clause has to match _scope_snapshot's.
    clauses = [f"al.primary_artist_id IN ({artist_marks})", "tf.file_state <> 'deleted'"]
    _owner = owner_clause(column="tf.owner_profile_id")
    if _owner:
        clauses.append(_owner.replace(" AND ", "", 1))
    params: Dict[str, Any] = {
        f"artist_id_{i}": value for i, value in enumerate(artist_ids)
    }
    if search:
        clauses.append("(t.title LIKE :like OR al.title LIKE :like)")
        params["like"] = f"%{search}%"
    where = "WHERE " + " AND ".join(clauses)

    total = conn.execute(
        f"""SELECT COUNT(*) AS c FROM lib2_track_files tf
             JOIN lib2_tracks t ON t.id = tf.track_id
             JOIN lib2_albums al ON al.id = t.album_id
            {where}""",
        params,
    ).fetchone()["c"]

    rows = conn.execute(
        f"""SELECT tf.id AS file_id, tf.track_id, tf.path, tf.size, tf.format,
                   tf.bitrate, tf.sample_rate, tf.bit_depth, tf.quality_tier,
                   tf.file_state, tf.is_primary, tf.primary_manual,
                   tf.file_role, tf.derived_from_file_id,
                   tf.acquired_quality_json, tf.retention_json, tf.added_at,
                   t.title AS track_title, t.track_number, t.disc_number,
                   al.id AS album_id, al.title AS album_title
              FROM lib2_track_files tf
              JOIN lib2_tracks t ON t.id = tf.track_id
              JOIN lib2_albums al ON al.id = t.album_id
             {where}
             ORDER BY al.title, t.disc_number, t.track_number, tf.id
             LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset},
    ).fetchall()

    files = [
        {
            **pick(r, "file_id", "track_id", "track_title", "track_number", "disc_number",
                   "album_id", "album_title", "path", "size", "format", "bitrate", "sample_rate",
                   "bit_depth", "quality_tier", "file_state"),
            "is_primary": bool(r["is_primary"]),
            "primary_manual": bool(r["primary_manual"]),
            "file_role": r["file_role"] or "master",
            **pick(r, "derived_from_file_id", "acquired_quality_json", "retention_json",
                   "added_at"),
        }
        for r in rows
    ]
    return files, total


def get_artist(conn, artist_id: int) -> Optional[Dict[str, Any]]:
    """Artist detail: header + albums and singles grouped separately.

    §40: resolves ``artist_id``'s alias group first — works whether it is the
    canonical row or one of its linked aliases, so an old deep link to an
    alias id still resolves. Albums/EPs/singles are the UNION of every group
    member's own releases (each keeps its own ``lib2_albums`` rows, nothing
    is reassigned); the header fields (bio/image/genres/...) always come from
    the CANONICAL row.
    """
    # whose library this page is (#1199). Built per call: the scope belongs
    # to whoever is asking, and while SCOPE_PARKED is true it is empty, so
    # every query here is byte for byte the one that ran before.
    tf_owner = owner_clause(column="tf.owner_profile_id")
    f_owner = owner_clause(column="f.owner_profile_id")
    from core.library2.artist_aliases import resolve_alias_group
    group = resolve_alias_group(conn, artist_id)
    canonical_id = group[0]
    a = conn.execute("SELECT * FROM lib2_artists WHERE id = ?", (canonical_id,)).fetchone()
    if a is None:
        return None
    artist_effective, artist_overrides = project_metadata(
        conn,
        entity_type="artist",
        entity_id=a["id"],
        provider_fields=dict(a),
    )
    artist_profile = _quality_profile_assignment(conn, "artists", a["id"])
    qp = conn.execute(
        "SELECT * FROM quality_profiles WHERE id = ?", (artist_profile["id"],)
    ).fetchone()

    group_marks = ",".join("?" for _ in group)
    from core.library2.recording_links import owned_by_recording_sql
    owned_elsewhere = owned_by_recording_sql(conn, "t")
    album_rows = conn.execute(
        f"""
        WITH artist_albums AS (
            SELECT aa.album_id
              FROM lib2_album_artists aa
             WHERE aa.artist_id IN ({group_marks})
            UNION
            SELECT t.album_id
              FROM lib2_track_artists ta
              JOIN lib2_tracks t ON t.id=ta.track_id
             WHERE ta.artist_id IN ({group_marks})
        ),
        -- Scoped to THIS artist's albums. Neither this CTE nor album_size
        -- below used to be, so opening any artist page ranked every file row
        -- in the library and grouped every album in the database: 491 ms for a
        -- ONE-album artist at 320k tracks, and the same 491 ms for a
        -- 105-album artist -- the cost was entirely library-wide (PERF-03).
        -- `list_artists` had the identical defect and it was fixed there
        -- (perf25-03); this one was missed.
        track_primary_files AS (
            SELECT tf.track_id, tf.size,
                   ROW_NUMBER() OVER (
                       PARTITION BY tf.track_id ORDER BY {primary_order('tf')}
                   ) AS rank
              FROM artist_albums aa2
              JOIN lib2_tracks t2 ON t2.album_id=aa2.album_id
              JOIN lib2_track_files tf ON tf.track_id=t2.id
             WHERE COALESCE(tf.file_state, 'active') <> 'deleted'{tf_owner}
        ),
        -- I8: disk-space roll-up per album, computed separately from the
        -- files_present fan-out below (that join isn't restricted to one row
        -- per track, so a SUM(size) sharing it would double-count).
        album_size AS (
            SELECT t.album_id, COALESCE(SUM(pf.size), 0) AS total_size_bytes
              FROM artist_albums aa3
              JOIN lib2_tracks t ON t.album_id=aa3.album_id
              JOIN track_primary_files pf ON pf.track_id=t.id AND pf.rank=1
             GROUP BY t.album_id
        )
        SELECT al.id, al.title, al.album_type, al.release_date, al.year,
               al.image_url, {monitored_sql("album", "al")} AS monitored, al.quality_profile_id,
               al.quality_profile_explicit, al.track_count,
               al.expected_track_count, al.origin, al.spotify_id,
               al.primary_artist_id,
               pa.quality_profile_id AS artist_quality_profile_id,
               pa.quality_profile_explicit AS artist_quality_profile_explicit,
               al.explicit, al.label, al.style, al.mood,
               COUNT(DISTINCT t.id) AS db_track_count,
               COUNT(DISTINCT CASE
                   WHEN (tf.id IS NOT NULL
                         AND COALESCE(tf.file_state, 'active')
                             NOT IN ('missing_confirmed','deleted'))
                     OR ({owned_elsewhere})
                   THEN t.id END) AS files_present,
               -- Guide §5: "My Library" means `origin='library' OR monitored`.
               -- A wanted TRACK on an otherwise unowned release satisfies that
               -- just as much as a monitored album does — without this count
               -- bookmarking a single top track wrote a wishlist row the user
               -- could then not find anywhere in their library.
               COUNT(DISTINCT CASE WHEN {monitored_sql("track", "t")}=1 THEN t.id END) AS monitored_tracks,
               -- What "missing" is allowed to mean: a track you still want and
               -- do not have. Unmonitoring the two interludes you never intend
               -- to own used to leave the album reading "2 missing" forever,
               -- with no way to reach zero except downloading music you had
               -- explicitly said no to.
               -- `wanted` first, `monitored` as the fallback: that is exactly
               -- what the album detail projects per row, and the two views
               -- disagreeing about the same number would be its own bug.
               COUNT(DISTINCT CASE
                   WHEN COALESCE(w.wanted, {monitored_sql("track", "t")})=1 AND NOT EXISTS (
                       SELECT 1 FROM lib2_track_files f
                        WHERE f.track_id=t.id
                          AND COALESCE(f.file_state,'active')
                              NOT IN ('missing_confirmed','deleted'){f_owner})
                    -- §49.6(c): and it is not already on disk under another
                    -- release. The album detail draws that row as present, so
                    -- counting it as a gap here would be the same disagreement
                    -- the comment above exists to prevent.
                    AND NOT ({owned_elsewhere})
                   THEN t.id END) AS monitored_missing,
               COALESCE(asz.total_size_bytes, 0) AS total_size_bytes
        FROM artist_albums aa
        JOIN lib2_albums al ON al.id = aa.album_id
        JOIN lib2_artists pa ON pa.id=al.primary_artist_id
        LEFT JOIN lib2_tracks t ON t.album_id=al.id
        LEFT JOIN lib2_track_files tf ON tf.track_id=t.id{tf_owner}
        LEFT JOIN lib2_wanted_tracks w ON w.track_id=t.id AND w.profile_id={intent_profile_id()}
        LEFT JOIN album_size asz ON asz.album_id=al.id
        GROUP BY al.id
        ORDER BY al.year DESC, {_alpha_key("al.title")}
        """,
        (*tuple(group), *tuple(group)),
    ).fetchall()

    projected_albums = project_metadata_many(
        conn,
        entity_type="release_group",
        provider_fields={int(row["id"]): dict(row) for row in album_rows},
    )
    albums, eps, singles = [], [], []
    for r in album_rows:
        effective, overrides = projected_albums[int(r["id"])]
        album_owns_profile = bool(r["quality_profile_explicit"])
        artist_owns_profile = bool(r["artist_quality_profile_explicit"])
        album_profile = {
            "source": "album" if album_owns_profile else (
                "artist" if artist_owns_profile else "global"
            ),
            "source_id": r["id"] if album_owns_profile else (
                r["primary_artist_id"] if artist_owns_profile else None
            ),
            "explicit": album_owns_profile,
        }
        present = r["files_present"] or 0
        # Total = the metadata's true track count when known, so partial albums
        # show "have / total" and the missing count is visible (Lidarr-style).
        total = max(r["expected_track_count"] or 0, r["db_track_count"] or 0,
                    r["track_count"] or 0, present)
        entry = {
            "id": r["id"],
            **pick(effective, "title", "album_type", "release_date", "year", "image_url"),
            "monitored": bool(r["monitored"]),
            "quality_profile_id": r["quality_profile_id"],
            "quality_profile_source": album_profile["source"],
            "quality_profile_source_id": album_profile["source_id"],
            "quality_profile_explicit": album_profile["explicit"],
            "origin": r["origin"] or "library",
            "spotify_id": r["spotify_id"],
            "explicit": (bool(effective["explicit"]) if effective["explicit"] is not None else None),
            "label": effective["label"],
            "style": effective["style"],
            "mood": effective["mood"],
            "track_count": total,
            "tracks_present": present,
            # Rows the provider promised but that do not exist yet always
            # count: a slot with no row is not a track anyone said no to, and
            # `lib2_albums.monitored` cannot stand in for intent here — the
            # importer clears it precisely BECAUSE a release is incomplete, so
            # gating on it would hide the gaps on exactly the albums that have
            # them.
            "tracks_missing": (r["monitored_missing"] or 0)
            + max(0, total - (r["db_track_count"] or 0)),
            "monitored_tracks": r["monitored_tracks"] or 0,
            "total_size_bytes": r["total_size_bytes"] or 0,
            "user_overrides": overrides,
        }
        if effective["album_type"] == "single":
            singles.append(entry)
        elif effective["album_type"] == "ep":
            eps.append(entry)
        else:
            albums.append(entry)

    def _in_library(entries):
        return sum(1 for e in entries
                   if e["origin"] == "library" or e["monitored"] or e["monitored_tracks"])

    return {
        "id": a["id"],
        **pick(artist_effective, "name", "image_url", "summary", "style", "mood", "label"),
        "genres": _json_list(artist_effective["genres"]),
        # ldp-05: the rich artist header asks the shared provider endpoints
        # (top tracks) for this artist — those key off a provider id, and a
        # lib2 row id is not one. Expose what the row already stores instead
        # of making the client take a second round trip to /match-status.
        "provider_ids": _artist_provider_ids(a),
        "media_server_sources": _media_server_sources_many(
            conn, "artist", [int(a["id"])]).get(int(a["id"]), []),
        # iss32-E02: "both show as matched so you can't tell them apart."
        # An artist with a legacy counterpart is walked by the twelve metadata
        # workers and can carry provider bios; one born inside lib2 is served
        # by the native path, which resolves provider ids, artwork, genres and
        # the descriptive columns but not the Last.fm/Genius/Discogs bios
        # (those workers still write legacy rows — Stufe 2). Say which, instead
        # of letting the chips imply parity.
        "enrichment_depth": "full" if a["legacy_artist_id"] is not None else "native",
        "monitored": _scoped_flag(conn, "artist", a),
        "monitor_new_items": a["monitor_new_items"],
        "quality_profile": _quality_profile_dict(qp),
        "quality_profile_source": artist_profile["source"],
        "quality_profile_source_id": artist_profile["source_id"],
        "quality_profile_explicit": artist_profile["explicit"],
        "albums": albums,
        "eps": eps,
        "singles": singles,
        "album_count": _in_library(albums) + _in_library(eps),
        "single_count": _in_library(singles),
        "discography_count": sum(1 for e in albums + eps + singles if e["origin"] == "discography"),
        # I8: sum of each release's own total_size_bytes above — one source
        # of truth, no separate artist-wide aggregate query needed.
        "total_size_bytes": sum(e["total_size_bytes"] for e in albums + eps + singles),
        "user_overrides": artist_overrides,
    }


def _track_artists(conn, track_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ar.id, ar.name, ta.role, ta.position
        FROM lib2_track_artists ta
        JOIN lib2_artists ar ON ar.id = ta.artist_id
        WHERE ta.track_id = ?
        ORDER BY ta.position
        """,
        (track_id,),
    ).fetchall()
    result = []
    for r in rows:
        effective, overrides = project_metadata(
            conn,
            entity_type="artist",
            entity_id=r["id"],
            provider_fields=dict(r),
        )
        result.append({
            "id": r["id"],
            "name": effective["name"],
            "role": r["role"],
            "user_overrides": overrides,
        })
    return result


def _track_artists_many(
    conn, track_ids: List[int],
) -> Dict[int, List[Dict[str, Any]]]:
    """Load and project track credits once for an album result set."""
    if not track_ids:
        return {}
    marks = ",".join("?" for _ in track_ids)
    rows = conn.execute(
        f"""SELECT ta.track_id, ar.id, ar.name, ta.role, ta.position
              FROM lib2_track_artists ta
              JOIN lib2_artists ar ON ar.id=ta.artist_id
             WHERE ta.track_id IN ({marks})
             ORDER BY ta.track_id, ta.position""",
        track_ids,
    ).fetchall()
    projected = project_metadata_many(
        conn,
        entity_type="artist",
        provider_fields={int(row["id"]): dict(row) for row in rows},
    )
    result: Dict[int, List[Dict[str, Any]]] = {
        int(track_id): [] for track_id in track_ids
    }
    for row in rows:
        effective, overrides = projected[int(row["id"])]
        result[int(row["track_id"])].append({
            "id": row["id"],
            "name": effective["name"],
            "role": row["role"],
            "user_overrides": overrides,
        })
    return result


def _download_provenance_for_path(conn, path: Optional[str], *,
                                  track: Any = None,
                                  album: Optional[Dict[str, Any]] = None,
                                  artists: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Most recent quality/provenance row for a file path, if the old table exists."""
    try:
        row = None
        if path:
            row = conn.execute(
                "SELECT * FROM track_downloads WHERE file_path = ? ORDER BY id DESC LIMIT 1",
                (path,),
            ).fetchone()
            fname = str(path).replace("\\", "/").rsplit("/", 1)[-1]
            if row is None and fname:
                row = conn.execute(
                    "SELECT * FROM track_downloads WHERE file_path LIKE ? OR file_path LIKE ? "
                    "ORDER BY id DESC LIMIT 1",
                    (f"%/{fname}", f"%\\{fname}"),
                ).fetchone()
        if row is not None:
            return dict(row)

        if track is not None:
            for column, value in (
                ("spotify_track_id", track["spotify_id"] if "spotify_id" in track.keys() else None),
                ("musicbrainz_recording_id",
                 track["musicbrainz_id"] if "musicbrainz_id" in track.keys() else None),
                ("isrc", track["isrc"] if "isrc" in track.keys() else None),
            ):
                if not value:
                    continue
                row = conn.execute(
                    f"SELECT * FROM track_downloads WHERE {column} = ? ORDER BY id DESC LIMIT 1",
                    (value,),
                ).fetchone()
                if row is not None:
                    return dict(row)

        title = track["title"] if track is not None and "title" in track.keys() else None
        album_title = album.get("title") if album else None
        artist_names = [a.get("name") for a in (artists or []) if a.get("name")]
        if album and album.get("primary_artist_name"):
            artist_names.append(album["primary_artist_name"])
        unique_artist_names = []
        seen_artists = set()
        for name in artist_names:
            folded = name.casefold()
            if folded not in seen_artists:
                seen_artists.add(folded)
                unique_artist_names.append(name)
        if title:
            candidates: List[List[Tuple[str, Any]]] = []
            for artist_name in unique_artist_names:
                if album_title:
                    candidates.append([
                        ("lower(track_title) = lower(?)", title),
                        ("lower(track_artist) = lower(?)", artist_name),
                        ("lower(track_album) = lower(?)", album_title),
                    ])
                candidates.append([
                    ("lower(track_title) = lower(?)", title),
                    ("lower(track_artist) = lower(?)", artist_name),
                ])
            if album_title:
                candidates.append([
                    ("lower(track_title) = lower(?)", title),
                    ("lower(track_album) = lower(?)", album_title),
                ])
            for candidate in candidates:
                clauses = [part[0] for part in candidate]
                params = [part[1] for part in candidate]
                row = conn.execute(
                    "SELECT * FROM track_downloads WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY id DESC LIMIT 1",
                    params,
                ).fetchone()
                if row is not None:
                    return dict(row)

        return dict(row) if row else {}
    except Exception:
        return {}


def _download_provenance_many(
    conn,
    tracks: List[Any],
    files: Mapping[int, Dict[str, Any]],
    album: Optional[Dict[str, Any]],
    artists: Mapping[int, List[Dict[str, Any]]],
) -> Dict[int, Dict[str, Any]]:
    """Resolve legacy provenance candidates once for an album track set."""
    if not tracks:
        return {}

    def _values(column: str) -> List[str]:
        values = []
        for track in tracks:
            if column in track.keys() and track[column] not in (None, ""):
                values.append(str(track[column]))
        return sorted(set(values))

    paths = sorted({
        str(file_row["path"])
        for file_row in files.values()
        if file_row.get("path")
    })
    filenames = sorted({
        path.replace("\\", "/").rsplit("/", 1)[-1]
        for path in paths
        if path.replace("\\", "/").rsplit("/", 1)[-1]
    })
    predicates: List[str] = []
    params: List[Any] = []

    def _in(column: str, values: List[str], *, lower: bool = False) -> None:
        if not values:
            return
        marks = ",".join("?" for _ in values)
        predicates.append(
            f"lower({column}) IN ({marks})" if lower else f"{column} IN ({marks})"
        )
        params.extend(value.lower() if lower else value for value in values)

    _in("file_path", paths)
    for filename in filenames:
        predicates.append("(file_path LIKE ? OR file_path LIKE ?)")
        params.extend((f"%/{filename}", f"%\\{filename}"))
    _in("spotify_track_id", _values("spotify_id"))
    _in("musicbrainz_recording_id", _values("musicbrainz_id"))
    _in("isrc", _values("isrc"))
    _in("track_title", _values("title"), lower=True)
    if album and album.get("title"):
        predicates.append("lower(track_album)=lower(?)")
        params.append(album["title"])
    if not predicates:
        return {}
    try:
        candidates = [dict(row) for row in conn.execute(
            "SELECT * FROM track_downloads WHERE "
            + " OR ".join(predicates)
            + " ORDER BY id DESC",
            params,
        ).fetchall()]
    except Exception:
        return {}

    def _fold(value: Any) -> str:
        return str(value or "").casefold()

    result: Dict[int, Dict[str, Any]] = {}
    for track in tracks:
        track_id = int(track["id"])
        file_row = files.get(track_id) or {}
        path = str(file_row.get("path") or "")
        filename = path.replace("\\", "/").rsplit("/", 1)[-1]
        match = next(
            (row for row in candidates if path and row.get("file_path") == path),
            None,
        )
        if match is None and filename:
            match = next(
                (
                    row for row in candidates
                    if str(row.get("file_path") or "").replace("\\", "/").endswith(
                        f"/{filename}"
                    )
                ),
                None,
            )
        if match is None:
            for track_column, download_column in (
                ("spotify_id", "spotify_track_id"),
                ("musicbrainz_id", "musicbrainz_recording_id"),
                ("isrc", "isrc"),
            ):
                value = track[track_column] if track_column in track.keys() else None
                if value:
                    match = next(
                        (
                            row for row in candidates
                            if str(row.get(download_column) or "") == str(value)
                        ),
                        None,
                    )
                if match is not None:
                    break
        if match is None:
            title = _fold(track["title"] if "title" in track.keys() else None)
            album_title = _fold(album.get("title") if album else None)
            artist_names = []
            for artist in artists.get(track_id, []):
                if artist.get("name"):
                    artist_names.append(_fold(artist["name"]))
            if album and album.get("primary_artist_name"):
                artist_names.append(_fold(album["primary_artist_name"]))
            artist_names = list(dict.fromkeys(artist_names))
            for artist_name in artist_names:
                match = next(
                    (
                        row for row in candidates
                        if _fold(row.get("track_title")) == title
                        and _fold(row.get("track_artist")) == artist_name
                        and _fold(row.get("track_album")) == album_title
                    ),
                    None,
                ) if album_title else None
                if match is None:
                    match = next(
                        (
                            row for row in candidates
                            if _fold(row.get("track_title")) == title
                            and _fold(row.get("track_artist")) == artist_name
                        ),
                        None,
                    )
                if match is not None:
                    break
            if match is None and album_title:
                match = next(
                    (
                        row for row in candidates
                        if _fold(row.get("track_title")) == title
                        and _fold(row.get("track_album")) == album_title
                    ),
                    None,
                )
        if match is not None:
            result[track_id] = match
    return result


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", 0):
            return value
    return None


def _bitrate_kbps(value: Any) -> Any:
    if value in (None, "", 0):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value
    if numeric > 10000:
        numeric = numeric / 1000
    return int(round(numeric))


_NOT_LOADED = object()


def _serialize_track(
    conn,
    t,
    album=None,
    *,
    file_row: Any = _NOT_LOADED,
    artists: Optional[List[Dict[str, Any]]] = None,
    projection: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None,
    provenance: Optional[Dict[str, Any]] = None,
    media_server_sources: Optional[List[str]] = None,
    linked_from: Any = _NOT_LOADED,
    findings: Any = None,
) -> Dict[str, Any]:
    """Build a track dict with linked artists, primary file, and computed status."""
    if file_row is _NOT_LOADED:
        from core.library2.track_files import primary_file_row
        file_row = primary_file_row(conn, t["id"])
    # §49.6(c): a position with no file of its own may still be on disk — under
    # another release that carries the same recording. Borrow that file rather
    # than drawing a gap the user is invited to re-download.
    if linked_from is _NOT_LOADED:
        linked_from = None
        if not file_row:
            from core.library2.recording_links import reference_owner
            linked_from = reference_owner(conn, t["id"])
            if linked_from:
                borrowed = conn.execute(
                    "SELECT * FROM lib2_track_files WHERE id=?",
                    (linked_from["file_id"],),
                ).fetchone()
                file_row = dict(borrowed) if borrowed else None
    if artists is None:
        artists = _track_artists(conn, t["id"])
    if projection is None:
        projection = project_metadata(
            conn,
            entity_type="track",
            entity_id=t["id"],
            provider_fields=dict(t),
        )
    effective, overrides = projection
    keys = set(t.keys())
    if "effective_wanted" in keys:
        wanted = bool(t["effective_wanted"])
    else:
        wanted_row = conn.execute(
            "SELECT wanted FROM lib2_wanted_tracks "
            f"WHERE profile_id={intent_profile_id()} AND track_id=?",
            (t["id"],),
        ).fetchone()
        wanted = bool(wanted_row["wanted"]) if wanted_row else _scoped_flag(conn, "track", t)
    gaps = compute_metadata_gaps(file_row)
    from core.library2.validation import finding_summary
    findings = finding_summary(conn, file_row, findings)
    validation = _coerce_tags(file_row.get('tags_json')).get('_validation', {}) if file_row else {}
    gaps = list(dict.fromkeys(gaps + [f['title'] or f['finding_type'] for f in findings
                                    if f['category'] == 'metadata' and (f['finding_type'] != 'library_retag' or not gaps)]))
    scan_status = metadata_scan_status(file_row)
    fstat = file_status(file_row, t["canonical_track_id"])
    if linked_from and fstat == "present":
        fstat = "linked"
    file_info = None
    if file_row:
        prov = provenance or {}
        if provenance is None and (
            not file_row["bitrate"]
            or not file_row["sample_rate"]
            or not file_row["bit_depth"]
        ):
            prov = _download_provenance_for_path(
                conn, file_row["path"], track=t, album=album, artists=artists
            )
        bitrate = _bitrate_kbps(file_row["bitrate"])
        sample_rate = file_row["sample_rate"]
        bit_depth = file_row["bit_depth"]
        source = _first_present(file_row["source"], prov.get("source_service"))
        has_rg = False
        has_lyrics = False
        if file_row.get("tags_json"):
            try:
                tags_data = json.loads(file_row["tags_json"]) or {}
                has_rg = any(
                    k in tags_data
                    for k in (
                        "replaygain_track_gain",
                        "replaygain_track_peak",
                        "replaygain_album_gain",
                        "replaygain_album_peak",
                    )
                )
                has_lyrics = bool(tags_data.get("lyrics") or tags_data.get("unsyncedlyrics"))
            except (AttributeError, TypeError, ValueError):
                has_rg = False
                has_lyrics = False
        pipeline_result = {}
        if file_row.get("pipeline_result_json"):
            try:
                pipeline_result = json.loads(file_row["pipeline_result_json"]) or {}
            except Exception:
                pipeline_result = {}
        file_info = {
            "file_id": file_row["id"],
            "path": file_row["path"],
            # Where the file sits IN THE LIBRARY. `path` stays the authority —
            # it is what the tooltip and the copy button hand back — but the
            # column leading with a root the user configured themselves says
            # nothing, and the same root reaches this table written three ways
            # ('./Transfer', 'Transfer', '/app/Transfer'), so the noise was not
            # even consistent between rows of one album.
            "display_path": library_relative_path(file_row["path"]),
            "format": file_row["format"],
            "bitrate": bitrate,
            "sample_rate": sample_rate,
            "bit_depth": bit_depth,
            "size": file_row["size"],
            "quality_tier": quality_tier(file_row["format"], bitrate, bit_depth),
            "import_status": file_row["import_status"],
            "verification_status": file_row["verification_status"],
            # Deep-dive A7/C4: AcoustID outcome + compact pipeline detail
            # (AcoustID reason, quality-profile fallback) for the Info-tab
            # lifecycle section — populated by the autolink import callback.
            "acoustid_status": file_row["acoustid_status"],
            "check_findings": [f for f in findings if f['category'] == 'check'],
            "pipeline_result": pipeline_result,
            "source": source,
            "file_state": file_row["file_state"],
            "is_primary": bool(file_row.get("is_primary")),
            "primary_manual": bool(file_row.get("primary_manual")),
            "file_role": file_row.get("file_role") or "master",
            "derived_from_file_id": file_row.get("derived_from_file_id"),
            "acquired_quality_json": file_row.get("acquired_quality_json"),
            "retention_json": file_row.get("retention_json"),
            "has_replaygain": has_rg,
            "has_lyrics": has_lyrics,
        }
    return {
        "id": t["id"],
        "lib2_track_id": t["id"],
        "legacy_track_id": t["legacy_track_id"] if "legacy_track_id" in keys else None,
        # Legacy ids originate from the media-server-backed tracks table and
        # are therefore also the only safe server stream id available here.
        "server_track_id": t["legacy_track_id"] if "legacy_track_id" in keys else None,
        **pick(effective, "title", "track_number", "disc_number", "duration", "bpm"),
        "explicit": (bool(effective["explicit"]) if effective["explicit"] is not None else None),
        "style": effective["style"],
        "mood": effective["mood"],
        "isrc": t["isrc"],
        "monitored": wanted,
        "quality_profile_id": (
            t["quality_profile_id"] if bool(t["quality_profile_explicit"])
            else (album or {}).get("quality_profile_id")
        ),
        "quality_profile_source": (
            "track" if bool(t["quality_profile_explicit"])
            else (album or {}).get("quality_profile_source", "global")
        ),
        "quality_profile_source_id": (
            t["id"] if bool(t["quality_profile_explicit"])
            else (album or {}).get("quality_profile_source_id")
        ),
        "quality_profile_explicit": bool(t["quality_profile_explicit"]),
        "canonical_track_id": t["canonical_track_id"],
        "artists": artists,
        "file": file_info,
        "file_status": fstat,
        "linked_from": linked_from or None,
        "metadata_gaps": gaps,
        "metadata_scan_status": scan_status,
        "metadata_validation": validation,
        "metadata_findings": [f for f in findings if f['category'] == 'metadata'],
        "media_server_sources": media_server_sources or [],
        "user_overrides": overrides,
    }


def _borrowed_row(links: Dict[int, Dict[str, Any]],
                  borrowed_files: Dict[int, Dict[str, Any]],
                  track_id: int) -> Optional[Dict[str, Any]]:
    """The file row a fileless position borrows, if it borrows one."""
    link = links.get(track_id)
    return borrowed_files.get(int(link["file_id"])) if link else None


def _serialize_tracks(conn, tracks: List[Any], album=None) -> List[Dict[str, Any]]:
    """Serialize an album track set with bounded shared reads."""
    if not tracks:
        return []
    track_ids = [int(track["id"]) for track in tracks]
    from core.library2.track_files import primary_file_rows
    files = primary_file_rows(conn, track_ids)
    # One resolve for the whole table instead of one per fileless row.
    from core.library2.recording_links import reference_owners
    links = reference_owners(conn, [tid for tid in track_ids if not files.get(tid)])
    borrowed_files: Dict[int, Dict[str, Any]] = {}
    if links:
        file_ids = sorted({int(link["file_id"]) for link in links.values()})
        marks = ",".join("?" for _ in file_ids)
        borrowed_files = {
            int(row["id"]): dict(row)
            for row in conn.execute(
                f"SELECT * FROM lib2_track_files WHERE id IN ({marks})",
                tuple(file_ids),
            ).fetchall()
        }
    artists = _track_artists_many(conn, track_ids)
    projections = project_metadata_many(
        conn,
        entity_type="track",
        provider_fields={int(track["id"]): dict(track) for track in tracks},
    )
    needs_provenance = [
        track for track in tracks
        if (file_row := files.get(int(track["id"])))
        and (
            not file_row.get("bitrate")
            or not file_row.get("sample_rate")
            or not file_row.get("bit_depth")
        )
    ]
    provenance = _download_provenance_many(
        conn,
        needs_provenance,
        files,
        album,
        artists,
    )
    media_sources = _media_server_sources_many(conn, "track", track_ids)
    from core.library2.validation import linked_findings_many
    findings_by_file = linked_findings_many(conn, [*files.values(), *borrowed_files.values()])
    serialized = [
        _serialize_track(
            conn,
            track,
            album,
            file_row=(files.get(int(track["id"]))
                      or _borrowed_row(links, borrowed_files, int(track["id"]))),
            linked_from=(None if files.get(int(track["id"]))
                         else links.get(int(track["id"]))),
            artists=artists.get(int(track["id"]), []),
            projection=projections[int(track["id"])],
            provenance=provenance.get(int(track["id"]), {}),
            media_server_sources=media_sources.get(int(track["id"]), []),
            findings=findings_by_file.get((files.get(int(track['id'])) or _borrowed_row(links, borrowed_files, int(track['id'])) or {}).get('id'), []),
        )
        for track in tracks
    ]
    marks = ",".join("?" for _ in track_ids)
    file_counts = {
        int(row["track_id"]): int(row["file_count"])
        for row in conn.execute(
            f"""SELECT track_id, COUNT(*) AS file_count
                  FROM lib2_track_files
                 WHERE track_id IN ({marks})
                   AND COALESCE(file_state,'active')<>'deleted'{{owner}}
                 GROUP BY track_id""".replace(
                     "{owner}", owner_clause(column="owner_profile_id")),
            tuple(track_ids),
        ).fetchall()
    }
    for item in serialized:
        item["file_count"] = file_counts.get(int(item["id"]), 0) if item.get("id") else 0
    return serialized


def _missing_track_placeholder(track_number: int, *, disc_number: int = 1,
                               album=None, title: Optional[str] = None) -> Dict[str, Any]:
    """Expected-but-not-owned track row, mirroring Lidarr's missing rows."""
    artists = []
    if album and album.get("primary_artist_id") and album.get("primary_artist_name"):
        artists.append({
            "id": album["primary_artist_id"],
            "name": album["primary_artist_name"],
            "role": "primary",
        })
    return {
        "id": None,
        "title": title,
        "track_number": track_number,
        "disc_number": disc_number,
        "duration": None,
        "bpm": None,
        "explicit": None,
        "style": None,
        "mood": None,
        "isrc": None,
        "monitored": bool(album["monitored"]) if album and "monitored" in album else False,
        "quality_profile_id": album["quality_profile_id"] if album and "quality_profile_id" in album else None,
        "quality_profile_source": (
            album.get("quality_profile_source", "global") if album else "global"
        ),
        "quality_profile_source_id": (
            album.get("quality_profile_source_id") if album else None
        ),
        "quality_profile_explicit": False,
        "canonical_track_id": None,
        "artists": artists,
        "file": None,
        "file_status": "missing",
        "metadata_gaps": [],
        "media_server_sources": [],
        "is_missing": True,
    }


def get_album(conn, album_id: int) -> Optional[Dict[str, Any]]:
    """Album/single detail: header + track table with per-track status."""
    al = conn.execute("SELECT * FROM lib2_albums WHERE id = ?", (album_id,)).fetchone()
    if al is None:
        return None
    album_effective, album_overrides = project_metadata(
        conn,
        entity_type="release_group",
        entity_id=al["id"],
        provider_fields=dict(al),
    )
    album_profile = _quality_profile_assignment(conn, "albums", al["id"])
    qp = conn.execute(
        "SELECT * FROM quality_profiles WHERE id = ?", (album_profile["id"],)
    ).fetchone()
    artist = conn.execute(
        "SELECT id, name FROM lib2_artists WHERE id = ?", (al["primary_artist_id"],)
    ).fetchone()
    track_rows = conn.execute(
        f"""SELECT t.*, COALESCE(w.wanted, {monitored_sql("track", "t")}) AS effective_wanted
             FROM lib2_tracks t
             LEFT JOIN lib2_wanted_tracks w
                    ON w.track_id=t.id AND w.profile_id={intent_profile_id()}
            WHERE t.album_id = ?
            ORDER BY t.disc_number, t.track_number, t.id""",
        (album_id,),
    ).fetchall()
    album_for_tracks = album_effective
    album_for_tracks["quality_profile_id"] = album_profile["id"]
    album_for_tracks["quality_profile_source"] = album_profile["source"]
    album_for_tracks["quality_profile_source_id"] = album_profile["source_id"]
    if artist:
        artist_effective, _artist_overrides = project_metadata(
            conn,
            entity_type="artist",
            entity_id=artist["id"],
            provider_fields=dict(artist),
        )
        album_for_tracks["primary_artist_name"] = artist_effective["name"]
        album_for_tracks["primary_artist_id"] = artist["id"]
    tracks = _serialize_tracks(conn, track_rows, album_for_tracks)
    present_count = sum(1 for t in tracks if t["file_status"] != "missing")

    # Evaluate each present file against its effective Track→Album→Artist→
    # Global profile.  A track override must affect both the profile shown in
    # the row and its upgrade badge; evaluating the entire album against the
    # album profile made those two UI statements contradict each other.
    from core.library2.quality_eval import evaluate_file, profile_targets, quality_issue
    profile_ids = sorted({
        int(t["quality_profile_id"])
        for t in tracks if t.get("quality_profile_id") is not None
    })
    profile_rows: Dict[int, Dict[str, Any]] = {}
    if profile_ids:
        marks = ",".join("?" for _ in profile_ids)
        profile_rows = {
            int(row["id"]): dict(row)
            for row in conn.execute(
                f"SELECT * FROM quality_profiles WHERE id IN ({marks})",
                tuple(profile_ids),
            ).fetchall()
        }
    upgrades_available = 0
    for t in tracks:
        if t.get("file") and t["file_status"] != "missing":
            targets, upgrade_policy, cutoff_index = profile_targets(
                profile_rows.get(int(t["quality_profile_id"]))
                if t.get("quality_profile_id") is not None else None
            )
            ev = evaluate_file(t["file"], targets, upgrade_policy, cutoff_index)
            t["quality_issue"] = quality_issue(
                t["file"], profile_rows.get(int(t["quality_profile_id"]))
                if t.get("quality_profile_id") is not None else {},
            )
            t["meets_profile"] = ev["meets_profile"]
            candidate = ev["upgrade_candidate"]
            t["upgrade_candidate"] = (
                None if candidate is None
                else bool(t["monitored"] and candidate)
            )
            if t["upgrade_candidate"] is True:
                upgrades_available += 1
        else:
            t["meets_profile"] = None
            t["upgrade_candidate"] = False
            t["quality_issue"] = None

    # Lidarr keeps expected missing recordings visible in the track table. When
    # we only know the album's expected size, expose those slots as missing rows
    # without pretending we know their title or tag gaps.
    expected = al["expected_track_count"] or 0
    known_count = len(tracks)
    total = max(expected, known_count, present_count)
    known_numbers = {
        (t.get("disc_number") or 1, t.get("track_number"))
        for t in tracks
        if t.get("track_number") is not None
    }
    # Slots for the missing tracks. When the album's canonical tracklist is
    # cached (core/library2/completeness.py) the slots come from it — with the
    # real title AND disc number, so multi-disc albums don't get colliding
    # disc-1 placeholders. Without a tracklist, fall back to a numeric loop.
    tl_entries: List[Dict[str, Any]] = []
    try:
        tl_raw = al["tracklist_json"] if "tracklist_json" in al.keys() else None
        for entry in (json.loads(tl_raw) if tl_raw else []):
            num = entry.get("track_number")
            if num:
                tl_entries.append({
                    "track_number": int(num),
                    "disc_number": int(entry.get("disc_number") or 1),
                    "title": entry.get("title"),
                })
    except (ValueError, TypeError):
        tl_entries = []
    if total > known_count:
        if tl_entries:
            for entry in tl_entries:
                key = (entry["disc_number"], entry["track_number"])
                if key not in known_numbers:
                    tracks.append(_missing_track_placeholder(
                        entry["track_number"], disc_number=entry["disc_number"],
                        album=album_for_tracks, title=entry.get("title")))
        else:
            for number in range(1, total + 1):
                if (1, number) not in known_numbers:
                    tracks.append(_missing_track_placeholder(number, album=album_for_tracks))
    tracks.sort(key=lambda t: (t.get("disc_number") or 1, t.get("track_number") or 0,
                              t.get("id") or 0))

    origin = "library"
    try:
        origin = al["origin"] or "library"
    except (IndexError, KeyError):
        pass

    return {
        "id": al["id"],
        **pick(album_effective, "title", "album_type", "release_date", "year", "image_url"),
        "genres": _json_list(album_effective["genres"]),
        "explicit": (
            bool(album_effective["explicit"]) if album_effective["explicit"] is not None else None
        ),
        "label": album_effective["label"],
        "style": album_effective["style"],
        "mood": album_effective["mood"],
        "monitored": _scoped_flag(conn, "album", al),
        "origin": origin,
        "quality_profile": _quality_profile_dict(qp),
        "quality_profile_source": album_profile["source"],
        "quality_profile_source_id": album_profile["source_id"],
        "quality_profile_explicit": album_profile["source"] == "album",
        "primary_artist": {
            "id": artist["id"],
            "name": album_for_tracks["primary_artist_name"],
        } if artist else None,
        "tracks": tracks,
        "track_count": total,
        "tracks_present": present_count,
        # Same rule as the album list: only a track you still want counts as
        # missing, plus the slots the provider promised that have no row yet
        # (those have no monitored flag of their own — the album's answers).
        #
        # ARCH-02: the placeholders for those promised slots were appended to
        # `tracks` above and inherit the album's monitored flag, so on a
        # monitored album the sum already counted them and `total - known_count`
        # counted them a second time — two expected tracks with no rows yet
        # reported four missing, more missing than the album has. `id is None`
        # is what makes a row a placeholder; only real rows belong in the sum.
        "tracks_missing": sum(
            1 for t in tracks
            if t.get("id") is not None
            and t["file_status"] == "missing" and t.get("monitored")
        ) + max(0, total - known_count),
        # I8: disk-space roll-up — sum of each present track's primary file.
        "total_size_bytes": sum(
            t["file"]["size"] or 0 for t in tracks if t.get("file") and t["file"].get("size")
        ),
        "upgrades_available": upgrades_available,
        "tracklist_sync": {
            "status": al["tracklist_status"],
            "attempts": al["tracklist_attempts"],
            "error": al["tracklist_error"],
            "retry_at": al["tracklist_retry_at"],
        },
        "user_overrides": album_overrides,
    }


def get_track(conn, track_id: int) -> Optional[Dict[str, Any]]:
    """Single-track detail incl. linked album + artists + file + status."""
    t = conn.execute(
        f"""SELECT t.*, COALESCE(w.wanted, {monitored_sql("track", "t")}) AS effective_wanted
             FROM lib2_tracks t
             LEFT JOIN lib2_wanted_tracks w
                    ON w.track_id=t.id AND w.profile_id={intent_profile_id()}
            WHERE t.id = ?""",
        (track_id,),
    ).fetchone()
    if t is None:
        return None
    album = conn.execute("SELECT * FROM lib2_albums WHERE id = ?", (t["album_id"],)).fetchone()
    album_effective = None
    album_overrides: Dict[str, Any] = {}
    if album:
        album_effective, album_overrides = project_metadata(
            conn,
            entity_type="release_group",
            entity_id=album["id"],
            provider_fields=dict(album),
        )
    data = _serialize_track(conn, t, album_effective)
    data["album"] = {
        "id": album["id"],
        "title": album_effective["title"],
        "album_type": album_effective["album_type"],
        "user_overrides": album_overrides,
    } if album else None
    return data


def list_quality_profiles(conn) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM quality_profiles ORDER BY is_default DESC, id"
    ).fetchall()
    return [_quality_profile_dict(row) for row in rows if row is not None]


__all__ = ["legacy_api_artists_page", "list_artists", "list_artist_track_files",
           "get_artist", "get_album", "get_track", "list_quality_profiles"]
