"""Native catalogue subjects shared by Library-v2 maintenance tools.

Every registered catalogue-aware repair job reads through this module in the
post-legacy (P3) architecture.  The rows contain stable Library-v2 identities,
physical-file facts and provider-qualified source-id mappings for artist,
release and track.  No legacy table or legacy back-reference participates in
enumeration.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from core.library2.provider_ids import source_ids_from_values


def _enabled(config_manager: Any) -> bool:
    from core.library2.feature import library_v2_enabled

    return library_v2_enabled(config_manager)


def _table_exists(conn: Any, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _compat_provider_fields(subject: Dict[str, Any]) -> None:
    """Expose legacy-shaped read aliases only at the tool adapter boundary.

    The canonical representation remains ``*_source_ids``.  These aliases let
    mature pure repair algorithms keep their field access while ensuring the
    value always came from the correctly named provider namespace.
    """

    track_ids = subject.get("track_source_ids") or {}
    album_ids = subject.get("album_source_ids") or {}
    artist_ids = subject.get("artist_source_ids") or {}
    for provider in (
        "spotify", "musicbrainz", "deezer", "itunes", "jiosaavn",
        "discogs", "audiodb", "lastfm", "genius", "bandcamp", "tidal",
        "qobuz", "amazon",
    ):
        subject[f"{provider}_track_id"] = track_ids.get(provider)
        subject[f"{provider}_album_id"] = album_ids.get(provider)
        subject[f"{provider}_artist_id"] = artist_ids.get(provider)
    subject["musicbrainz_recording_id"] = track_ids.get("musicbrainz")
    subject["musicbrainz_album_id"] = album_ids.get("musicbrainz")
    subject["musicbrainz_artist_id"] = artist_ids.get("musicbrainz")
    subject["deezer_id"] = track_ids.get("deezer")
    subject["album_thumb"] = subject.get("album_image")
    subject["artist_thumb"] = subject.get("artist_image")
    subject["file_path"] = subject.get("path")


def active_file_subjects(
    database: Any,
    config_manager: Any,
    *,
    include_missing: bool = False,
) -> List[Dict[str, Any]]:
    """Return every indexed Library-v2 file with full entity/provider context."""

    if not _enabled(config_manager):
        return []
    conn = database._get_connection()
    try:
        if not _table_exists(conn, "lib2_track_files"):
            return []
        state_clause = (
            "COALESCE(f.file_state,'active')<>'deleted'"
            if include_missing
            else "COALESCE(f.file_state,'active')='active'"
        )
        rows = conn.execute(
            f"""SELECT f.id AS file_id, f.track_id, f.path, f.original_path,
                       f.is_primary, f.format, f.size, f.bitrate,
                       f.sample_rate, f.bit_depth, f.quality_tier, f.source,
                       f.import_status, f.processing_status,
                       f.verification_status, f.acoustid_status,
                       f.tags_json, f.missing_tags_json, f.metadata_gaps_json,
                       f.content_hash, f.file_state,
                       t.album_id, t.title, t.duration, t.track_number,
                       t.disc_number, t.isrc, t.spotify_id AS track_spotify_id,
                       t.musicbrainz_id AS track_musicbrainz_id,
                       t.external_ids AS track_external_ids,
                       t.bpm, t.explicit, t.genius_lyrics, t.copyright,
                       t.style AS track_style, t.mood AS track_mood,
                       t.play_count, t.last_played, t.monitored AS track_monitored,
                       t.canonical_track_id,
                       al.title AS album_title, al.album_type,
                       al.release_date, al.year AS album_year,
                       al.spotify_id AS album_spotify_id,
                       al.musicbrainz_id AS album_musicbrainz_id,
                       al.external_ids AS album_external_ids,
                       al.image_url AS album_image, al.genres AS album_genres,
                       al.explicit AS album_explicit, al.label AS album_label,
                       al.upc AS album_upc, al.style AS album_style,
                       al.mood AS album_mood, al.track_count AS album_track_count,
                       al.expected_track_count, al.origin AS album_origin,
                       al.monitored AS album_monitored,
                       al.primary_artist_id AS artist_id,
                       COALESCE(
                         (SELECT ar2.name
                            FROM lib2_track_artists ta2
                            JOIN lib2_artists ar2 ON ar2.id=ta2.artist_id
                           WHERE ta2.track_id=t.id
                           ORDER BY CASE ta2.role WHEN 'primary' THEN 0 ELSE 1 END,
                                    ta2.position, ar2.id LIMIT 1),
                         ar.name
                       ) AS artist_name,
                       ar.sort_name AS artist_sort_name,
                       ar.spotify_id AS artist_spotify_id,
                       ar.musicbrainz_id AS artist_musicbrainz_id,
                       ar.external_ids AS artist_external_ids,
                       ar.image_url AS artist_image, ar.genres AS artist_genres,
                       ar.summary AS artist_summary, ar.style AS artist_style,
                       ar.mood AS artist_mood, ar.label AS artist_label,
                       ar.banner_url AS artist_banner_url,
                       ar.monitored AS artist_monitored
                  FROM lib2_track_files f
                  JOIN lib2_tracks t ON t.id=f.track_id
                  JOIN lib2_albums al ON al.id=t.album_id
             LEFT JOIN lib2_artists ar ON ar.id=al.primary_artist_id
                 WHERE f.path IS NOT NULL AND f.path<>'' AND {state_clause}
              ORDER BY al.id, COALESCE(t.disc_number,1),
                       COALESCE(t.track_number,2147483647), f.id"""
        ).fetchall()
        subjects: List[Dict[str, Any]] = []
        for row in rows:
            subject = dict(row)
            subject["track_source_ids"] = source_ids_from_values(
                spotify_id=subject.pop("track_spotify_id", None),
                musicbrainz_id=subject.pop("track_musicbrainz_id", None),
                external_ids=subject.pop("track_external_ids", None),
                isrc=subject.get("isrc"),
            )
            subject["album_source_ids"] = source_ids_from_values(
                spotify_id=subject.pop("album_spotify_id", None),
                musicbrainz_id=subject.pop("album_musicbrainz_id", None),
                external_ids=subject.pop("album_external_ids", None),
                upc=subject.get("album_upc"),
            )
            subject["artist_source_ids"] = source_ids_from_values(
                spotify_id=subject.pop("artist_spotify_id", None),
                musicbrainz_id=subject.pop("artist_musicbrainz_id", None),
                external_ids=subject.pop("artist_external_ids", None),
            )
            _compat_provider_fields(subject)
            subjects.append(subject)
        return subjects
    finally:
        conn.close()


def active_album_subjects(
    database: Any,
    config_manager: Any,
    *,
    require_active_files: bool = True,
) -> List[Dict[str, Any]]:
    """Return native releases with provider IDs and an optional file anchor."""

    if not _enabled(config_manager):
        return []
    conn = database._get_connection()
    try:
        if not _table_exists(conn, "lib2_albums"):
            return []
        file_predicate = "" if not require_active_files else """
                   AND EXISTS (
                       SELECT 1 FROM lib2_tracks tx
                       JOIN lib2_track_files fx ON fx.track_id=tx.id
                      WHERE tx.album_id=al.id
                        AND COALESCE(fx.file_state,'active')='active')
        """
        rows = conn.execute(
            f"""SELECT al.id AS album_id, al.primary_artist_id AS artist_id,
                       al.title, al.album_type, al.release_date,
                       al.year AS album_year, al.spotify_id AS album_spotify_id,
                       al.musicbrainz_id AS album_musicbrainz_id,
                       al.external_ids AS album_external_ids,
                       al.image_url AS album_image, al.genres AS album_genres,
                       al.explicit AS album_explicit, al.label AS album_label,
                       al.upc AS album_upc, al.style AS album_style,
                       al.mood AS album_mood, al.track_count AS album_track_count,
                       al.expected_track_count, al.tracklist_json,
                       al.tracklist_status, al.origin AS album_origin,
                       al.monitored AS album_monitored,
                       ar.name AS artist_name, ar.sort_name AS artist_sort_name,
                       ar.spotify_id AS artist_spotify_id,
                       ar.musicbrainz_id AS artist_musicbrainz_id,
                       ar.external_ids AS artist_external_ids,
                       ar.image_url AS artist_image, ar.genres AS artist_genres,
                       ar.monitored AS artist_monitored,
                       (SELECT fx.path FROM lib2_tracks tx
                         JOIN lib2_track_files fx ON fx.track_id=tx.id
                        WHERE tx.album_id=al.id
                          AND COALESCE(fx.file_state,'active')='active'
                        ORDER BY COALESCE(tx.disc_number,1),
                                 COALESCE(tx.track_number,2147483647), fx.id
                        LIMIT 1) AS rep_path
                  FROM lib2_albums al
             LEFT JOIN lib2_artists ar ON ar.id=al.primary_artist_id
                 WHERE al.title IS NOT NULL AND al.title<>'' {file_predicate}
              ORDER BY al.id"""
        ).fetchall()
        subjects: List[Dict[str, Any]] = []
        for row in rows:
            subject = dict(row)
            subject["album_source_ids"] = source_ids_from_values(
                spotify_id=subject.pop("album_spotify_id", None),
                musicbrainz_id=subject.pop("album_musicbrainz_id", None),
                external_ids=subject.pop("album_external_ids", None),
                upc=subject.get("album_upc"),
            )
            subject["artist_source_ids"] = source_ids_from_values(
                spotify_id=subject.pop("artist_spotify_id", None),
                musicbrainz_id=subject.pop("artist_musicbrainz_id", None),
                external_ids=subject.pop("artist_external_ids", None),
            )
            subject["track_source_ids"] = {}
            _compat_provider_fields(subject)
            subjects.append(subject)
        return subjects
    finally:
        conn.close()


def subject_details(subject: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the stable finding payload for one native subject."""

    linked = {
        "artist_id": subject.get("artist_id"),
        "album_id": subject.get("album_id"),
        "track_id": subject.get("track_id"),
        "file_id": subject.get("file_id"),
    }
    for plural, singular in (
        ("artist_ids", "artist_id"), ("album_ids", "album_id"),
        ("track_ids", "track_id"), ("file_ids", "file_id"),
    ):
        value = linked.get(singular)
        linked[plural] = [int(value)] if value not in (None, "") else []
    return {
        "library_v2_native": True,
        "library_v2": linked,
        "dedup_file": {
            "id": subject.get("file_id"),
            "content_hash": subject.get("content_hash"),
            "size": subject.get("size"),
            "format": subject.get("format"),
            "bitrate": subject.get("bitrate"),
            "sample_rate": subject.get("sample_rate"),
            "bit_depth": subject.get("bit_depth"),
        } if subject.get("file_id") is not None else None,
        "provider_ids": {
            "artist": dict(subject.get("artist_source_ids") or {}),
            "album": dict(subject.get("album_source_ids") or {}),
            "track": dict(subject.get("track_source_ids") or {}),
        },
    }


def count_active_files(database: Any, config_manager: Any) -> int:
    """Cheap native scope count used by job progress estimates."""

    if not _enabled(config_manager):
        return 0
    conn = database._get_connection()
    try:
        if not _table_exists(conn, "lib2_track_files"):
            return 0
        row = conn.execute(
            "SELECT COUNT(*) FROM lib2_track_files WHERE path IS NOT NULL "
            "AND path<>'' AND COALESCE(file_state,'active')='active'"
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


__all__ = [
    "active_album_subjects",
    "active_file_subjects",
    "count_active_files",
    "subject_details",
]
