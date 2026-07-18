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
from .status import compute_metadata_gaps, file_status, quality_tier
from .track_files import primary_order

_SORTS = {
    "name": "a.sort_name COLLATE NOCASE, a.name COLLATE NOCASE",
    "added": "a.added_at DESC",
    "albums": "album_count DESC, a.name COLLATE NOCASE",
    "tracks": "track_count DESC, a.name COLLATE NOCASE",
}

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
        "upgrade_policy": row["upgrade_policy"] or "acceptable",
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


def list_artists(conn, *, search: str = "", sort: str = "name", monitored: str = "all",
                 page: int = 1, limit: int = 75) -> Tuple[List[Dict[str, Any]], int]:
    """Paginated artist overview with per-artist roll-up stats.

    ``monitored`` filters the list: ``'all'`` (default), ``'monitored'``, or
    ``'unmonitored'``.
    """
    order = _SORTS.get(sort, _SORTS["name"])
    page = max(1, int(page))
    limit = max(1, min(int(limit), 500))
    offset = (page - 1) * limit
    # §40: alias-member rows are folded into their canonical artist's entry
    # (get_artist merges their albums in) and never listed on their own.
    clauses, params = ["a.canonical_artist_id IS NULL"], {}
    if search:
        clauses.append("a.name LIKE :like")
        params["like"] = f"%{search}%"
    if monitored == "monitored":
        clauses.append("a.monitored = 1")
    elif monitored == "unmonitored":
        clauses.append("a.monitored = 0")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM lib2_artists a {where}", params
    ).fetchone()["c"]

    rows = conn.execute(
        f"""
        WITH artist_albums AS (
            SELECT aa.artist_id, aa.album_id
              FROM lib2_album_artists aa
            UNION
            SELECT ta.artist_id, t.album_id
              FROM lib2_track_artists ta
              JOIN lib2_tracks t ON t.id=ta.track_id
        ),
        album_stats AS (
            SELECT aa.artist_id,
                   COUNT(DISTINCT CASE
                       WHEN al.album_type <> 'single'
                        AND (al.origin='library' OR al.monitored=1)
                       THEN al.id END) AS album_count,
                   COUNT(DISTINCT CASE
                       WHEN al.album_type = 'single'
                        AND (al.origin='library' OR al.monitored=1)
                       THEN al.id END) AS single_count
              FROM artist_albums aa
              JOIN lib2_albums al ON al.id=aa.album_id
             GROUP BY aa.artist_id
        ),
        track_stats AS (
            SELECT ta.artist_id,
                   COUNT(DISTINCT CASE
                       WHEN COALESCE(w.wanted, t.monitored)=1 OR tf.id IS NOT NULL
                       THEN t.id END) AS track_count,
                   COUNT(DISTINCT CASE
                       WHEN tf.id IS NOT NULL
                        AND COALESCE(tf.file_state, 'active')
                            NOT IN ('missing_confirmed','deleted')
                       THEN t.id END) AS track_files_present
              FROM lib2_track_artists ta
              JOIN lib2_tracks t ON t.id=ta.track_id
              LEFT JOIN lib2_wanted_tracks w
                     ON w.track_id=t.id AND w.profile_id=1
              LEFT JOIN lib2_track_files tf ON tf.track_id=t.id
             GROUP BY ta.artist_id
        ),
        track_primary_files AS (
            SELECT tf.track_id, tf.size,
                   ROW_NUMBER() OVER (
                       PARTITION BY tf.track_id ORDER BY {primary_order('tf')}
                   ) AS rank
              FROM lib2_track_files tf
             WHERE COALESCE(tf.file_state, 'active') <> 'deleted'
        ),
        -- I8: disk-space roll-up, kept separate from track_stats above —
        -- that CTE's plain (unranked) tf join fans out per historical file
        -- row, which would inflate a SUM(size) sharing the same join. This
        -- one joins each track's single ADR-03 primary file exactly once.
        artist_size AS (
            SELECT ta.artist_id, COALESCE(SUM(pf.size), 0) AS total_size_bytes
              FROM lib2_track_artists ta
              JOIN track_primary_files pf ON pf.track_id=ta.track_id AND pf.rank=1
             GROUP BY ta.artist_id
        )
        SELECT a.id, a.name, a.sort_name, a.image_url, a.genres,
               a.monitored, a.monitor_new_items, a.quality_profile_id,
               a.quality_profile_explicit, a.added_at,
               COALESCE(als.album_count, 0) AS album_count,
               COALESCE(als.single_count, 0) AS single_count,
               COALESCE(ts.track_count, 0) AS track_count,
               COALESCE(ts.track_files_present, 0) AS track_files_present,
               COALESCE(asz.total_size_bytes, 0) AS total_size_bytes
        FROM lib2_artists a
        LEFT JOIN album_stats als ON als.artist_id=a.id
        LEFT JOIN track_stats ts ON ts.artist_id=a.id
        LEFT JOIN artist_size asz ON asz.artist_id=a.id
        {where}
        ORDER BY {order}
        LIMIT :limit OFFSET :offset
        """,
        {**params, "limit": limit, "offset": offset},
    ).fetchall()

    projected = project_metadata_many(
        conn,
        entity_type="artist",
        provider_fields={int(row["id"]): dict(row) for row in rows},
    )
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
            "user_overrides": overrides,
        })
    return artists, total


def list_artist_track_files(conn, artist_id: int, *, search: str = "",
                            page: int = 1, limit: int = 100
                            ) -> Tuple[List[Dict[str, Any]], int]:
    """Paginated flat file list for one artist (C2: Lidarr "Manage Track
    Files"). Mirrors ``core.library2.file_delete._scope_snapshot``'s artist
    scope exactly (``primary_artist_id``, non-deleted files) so a selection
    made from this list lines up with what the ADR-05 preview/execute
    endpoints will actually see for the same file ids.
    """
    page = max(1, int(page))
    limit = max(1, min(int(limit), 500))
    offset = (page - 1) * limit
    clauses = ["al.primary_artist_id = :artist_id", "tf.file_state <> 'deleted'"]
    params: Dict[str, Any] = {"artist_id": int(artist_id)}
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
                   tf.file_state, tf.is_primary, tf.added_at,
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
            "file_id": r["file_id"],
            "track_id": r["track_id"],
            "track_title": r["track_title"],
            "track_number": r["track_number"],
            "disc_number": r["disc_number"],
            "album_id": r["album_id"],
            "album_title": r["album_title"],
            "path": r["path"],
            "size": r["size"],
            "format": r["format"],
            "bitrate": r["bitrate"],
            "sample_rate": r["sample_rate"],
            "bit_depth": r["bit_depth"],
            "quality_tier": r["quality_tier"],
            "file_state": r["file_state"],
            "is_primary": bool(r["is_primary"]),
            "added_at": r["added_at"],
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
        track_primary_files AS (
            SELECT tf.track_id, tf.size,
                   ROW_NUMBER() OVER (
                       PARTITION BY tf.track_id ORDER BY {primary_order('tf')}
                   ) AS rank
              FROM lib2_track_files tf
             WHERE COALESCE(tf.file_state, 'active') <> 'deleted'
        ),
        -- I8: disk-space roll-up per album, computed separately from the
        -- files_present fan-out below (that join isn't restricted to one row
        -- per track, so a SUM(size) sharing it would double-count).
        album_size AS (
            SELECT t.album_id, COALESCE(SUM(pf.size), 0) AS total_size_bytes
              FROM lib2_tracks t
              JOIN track_primary_files pf ON pf.track_id=t.id AND pf.rank=1
             GROUP BY t.album_id
        )
        SELECT al.id, al.title, al.album_type, al.release_date, al.year,
               al.image_url, al.monitored, al.quality_profile_id,
               al.quality_profile_explicit, al.track_count,
               al.expected_track_count, al.origin, al.spotify_id,
               al.primary_artist_id,
               pa.quality_profile_id AS artist_quality_profile_id,
               pa.quality_profile_explicit AS artist_quality_profile_explicit,
               al.explicit, al.label, al.style, al.mood,
               COUNT(DISTINCT t.id) AS db_track_count,
               COUNT(DISTINCT CASE
                   WHEN tf.id IS NOT NULL
                    AND COALESCE(tf.file_state, 'active')
                        NOT IN ('missing_confirmed','deleted')
                   THEN t.id END) AS files_present,
               COALESCE(asz.total_size_bytes, 0) AS total_size_bytes
        FROM artist_albums aa
        JOIN lib2_albums al ON al.id = aa.album_id
        JOIN lib2_artists pa ON pa.id=al.primary_artist_id
        LEFT JOIN lib2_tracks t ON t.album_id=al.id
        LEFT JOIN lib2_track_files tf ON tf.track_id=t.id
        LEFT JOIN album_size asz ON asz.album_id=al.id
        GROUP BY al.id
        ORDER BY al.year DESC, al.title COLLATE NOCASE
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
            "title": effective["title"],
            "album_type": effective["album_type"],
            "release_date": effective["release_date"],
            "year": effective["year"],
            "image_url": effective["image_url"],
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
            "tracks_missing": max(0, total - present),
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
        return sum(1 for e in entries if e["origin"] == "library" or e["monitored"])

    return {
        "id": a["id"],
        "name": artist_effective["name"],
        "image_url": artist_effective["image_url"],
        "summary": artist_effective["summary"],
        "style": artist_effective["style"],
        "mood": artist_effective["mood"],
        "label": artist_effective["label"],
        "genres": _json_list(artist_effective["genres"]),
        "monitored": bool(a["monitored"]),
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
) -> Dict[str, Any]:
    """Build a track dict with linked artists, primary file, and computed status."""
    if file_row is _NOT_LOADED:
        from core.library2.track_files import primary_file_row
        file_row = primary_file_row(conn, t["id"])
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
            "WHERE profile_id=1 AND track_id=?",
            (t["id"],),
        ).fetchone()
        wanted = bool(wanted_row["wanted"]) if wanted_row else bool(t["monitored"])
    track_meta = {
        "title": effective["title"],
        "track_number": effective["track_number"],
        "disc_number": effective["disc_number"],
        "isrc": t["isrc"],
        "album_title": album["title"] if album else None,
        "album_artist_name": album["primary_artist_name"] if album and "primary_artist_name" in album.keys() else None,
        "album_year": album["year"] if album else None,
        "album_image_url": album["image_url"] if album else None,
        "album_genres": album["genres"] if album else None,
    }
    gaps = compute_metadata_gaps(track_meta, file_row, artist_count=len(artists))
    fstat = file_status(file_row, t["canonical_track_id"])
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
        bitrate = _bitrate_kbps(_first_present(file_row["bitrate"], prov.get("bitrate")))
        sample_rate = _first_present(file_row["sample_rate"], prov.get("sample_rate"))
        bit_depth = _first_present(file_row["bit_depth"], prov.get("bit_depth"))
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
            "pipeline_result": pipeline_result,
            "source": source,
            "file_state": file_row["file_state"],
            "has_replaygain": has_rg,
            "has_lyrics": has_lyrics,
        }
    return {
        "id": t["id"],
        "title": effective["title"],
        "track_number": effective["track_number"],
        "disc_number": effective["disc_number"],
        "duration": effective["duration"],
        "bpm": effective["bpm"],
        "explicit": (bool(effective["explicit"]) if effective["explicit"] is not None else None),
        "style": effective["style"],
        "mood": effective["mood"],
        "isrc": t["isrc"],
        "monitored": wanted,
        "quality_profile_id": t["quality_profile_id"],
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
        "metadata_gaps": gaps,
        "user_overrides": overrides,
    }


def _serialize_tracks(conn, tracks: List[Any], album=None) -> List[Dict[str, Any]]:
    """Serialize an album track set with bounded shared reads."""
    if not tracks:
        return []
    track_ids = [int(track["id"]) for track in tracks]
    from core.library2.track_files import primary_file_rows
    files = primary_file_rows(conn, track_ids)
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
    return [
        _serialize_track(
            conn,
            track,
            album,
            file_row=files.get(int(track["id"])),
            artists=artists.get(int(track["id"]), []),
            projection=projections[int(track["id"])],
            provenance=provenance.get(int(track["id"]), {}),
        )
        for track in tracks
    ]


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
        """SELECT t.*, COALESCE(w.wanted, t.monitored) AS effective_wanted
             FROM lib2_tracks t
             LEFT JOIN lib2_wanted_tracks w
                    ON w.track_id=t.id AND w.profile_id=1
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

    # Evaluate each present file against the album's quality profile (meets /
    # upgrade-available), reusing core/quality. Missing rows stay neutral.
    from core.library2.quality_eval import evaluate_file, profile_targets
    targets, upgrade_policy, cutoff_index = profile_targets(dict(qp) if qp else None)
    upgrades_available = 0
    for t in tracks:
        if t.get("file") and t["file_status"] != "missing":
            ev = evaluate_file(t["file"], targets, upgrade_policy, cutoff_index)
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
        "title": album_effective["title"],
        "album_type": album_effective["album_type"],
        "release_date": album_effective["release_date"],
        "year": album_effective["year"],
        "image_url": album_effective["image_url"],
        "genres": _json_list(album_effective["genres"]),
        "explicit": (
            bool(album_effective["explicit"]) if album_effective["explicit"] is not None else None
        ),
        "label": album_effective["label"],
        "style": album_effective["style"],
        "mood": album_effective["mood"],
        "monitored": bool(al["monitored"]),
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
        "tracks_missing": max(0, total - present_count),
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
        """SELECT t.*, COALESCE(w.wanted, t.monitored) AS effective_wanted
             FROM lib2_tracks t
             LEFT JOIN lib2_wanted_tracks w
                    ON w.track_id=t.id AND w.profile_id=1
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


__all__ = ["list_artists", "list_artist_track_files", "get_artist", "get_album", "get_track",
           "list_quality_profiles"]
