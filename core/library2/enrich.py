"""Resync a lib2 entity's provider-sourced fields from its legacy counterpart
row right after ``core.metadata`` enrichment workers (see ``web_server.py``'s
``_run_single_enrichment``) re-query a provider and write fresh data into it.

lib2 rows are a point-in-time mirror of the legacy library (see
``core.library2.importer``): enrichment only ever updates the LEGACY row, so
without this the refreshed data would be invisible in the lib2 UI until a
full re-import. Unlike the bulk importer's upsert (which never regresses a
richer existing value across incremental imports — see
``_ArtistResolver.upsert_legacy``), a user-triggered Enrich is an explicit
"pull fresh data now" action for ONE entity, so its provider-owned fields are
safe to overwrite outright — except we still guard against clobbering good
existing data with a legacy column that some OTHER, untouched provider left
NULL, hence ``COALESCE``. Identity fields (name/title) are intentionally left
alone; Enrich only refreshes descriptive metadata. User overrides
(``core.library2.metadata_overrides``) are layered on top at read time
regardless of the base row, so overwriting the base row here is always safe.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional


def _row_get(row: Any, col: str) -> Optional[Any]:
    return row[col] if col in row.keys() else None


def _normalize_genres(raw: Any) -> str:
    """Mirror legacy genre storage (JSON array OR comma string) → JSON array
    string. Duplicated from ``importer._normalize_genres`` (module-private,
    same tiny-helper-duplication precedent as ``_precache_max_workers`` in
    ``artwork.py``/``completeness.py``)."""
    if not raw:
        return "[]"
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return json.dumps([str(g).strip() for g in parsed if str(g).strip()])
    except (ValueError, TypeError):
        pass
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    return json.dumps(parts)


def _provider_ids(legacy_row: Any, entity_type: str) -> Dict[str, Any]:
    """Every provider id the legacy row carries for this entity type.

    Same ``match_status.SERVICES`` mapping the importer and the match chips
    use, so a provider added there is mirrored here without a second edit.
    """
    from core.library2.match_status import SERVICES

    out: Dict[str, Any] = {}
    for service, _label, id_columns in SERVICES:
        column = id_columns.get(entity_type)
        if not column:
            continue
        value = _row_get(legacy_row, column)
        if value not in (None, ""):
            out[service] = str(value).strip()
    return out


def _merge_json_column(conn, table: str, entity_id: int, column: str,
                       incoming: Dict[str, Any]) -> None:
    """Merge ``incoming`` into a JSON object column, legacy winning per key.

    Deliberately different from the importer's ``_merge_external_ids``, which
    uses ``setdefault`` so a thinner re-import can never regress a richer row.
    Here the trigger fired *because* an enrichment worker just wrote that very
    value, so legacy is the fresher source by construction — keeping the old
    value would mean a corrected provider id could never reach lib2. Keys the
    legacy row has nothing for are left untouched either way.
    """
    if not incoming:
        return
    row = conn.execute(
        f"SELECT {column} FROM {table} WHERE id=?", (entity_id,)).fetchone()
    try:
        current = json.loads((_row_get(row, column) if row else None) or "{}")
        if not isinstance(current, dict):
            current = {}
    except (TypeError, ValueError):
        current = {}
    merged = dict(current)
    for key, value in incoming.items():
        if isinstance(value, dict):
            bucket = dict(merged.get(key) or {}) if isinstance(merged.get(key), dict) else {}
            bucket.update(value)
            merged[key] = bucket
        else:
            merged[key] = value
    if merged != current:
        conn.execute(
            f"UPDATE {table} SET {column}=? WHERE id=?",
            (json.dumps(merged, sort_keys=True, separators=(",", ":")), entity_id))


def _artist_enrichment(legacy_row: Any) -> Dict[str, Dict[str, Any]]:
    """Provider bios/stats, keyed by source — the ``bios`` Nezreka named.

    These live in ``lib2_artists.enrichment`` rather than in columns because a
    Last.fm bio and a Genius description are different text, not one field
    from two sources (same reasoning as ``importer._artist_enrichment_payload``).
    """
    def _list(raw):
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else None
        except (TypeError, ValueError):
            parts = [p.strip() for p in str(raw).split(",") if p.strip()]
            return parts or None

    candidates = {
        "lastfm": {
            "bio": _row_get(legacy_row, "lastfm_bio"),
            "listeners": _row_get(legacy_row, "lastfm_listeners"),
            "tags": _list(_row_get(legacy_row, "lastfm_tags")),
            "similar": _list(_row_get(legacy_row, "lastfm_similar")),
            "url": _row_get(legacy_row, "lastfm_url"),
        },
        "genius": {
            "description": _row_get(legacy_row, "genius_description"),
            "alt_names": _list(_row_get(legacy_row, "genius_alt_names")),
            "url": _row_get(legacy_row, "genius_url"),
        },
        "discogs": {
            "bio": _row_get(legacy_row, "discogs_bio"),
            "members": _list(_row_get(legacy_row, "discogs_members")),
            "urls": _list(_row_get(legacy_row, "discogs_urls")),
        },
    }
    out: Dict[str, Dict[str, Any]] = {}
    for source, fields in candidates.items():
        cleaned = {k: v for k, v in fields.items() if v not in (None, "", [])}
        if cleaned:
            out[source] = cleaned
    return out


def resync_artist_from_legacy(conn, lib2_artist_id: int, legacy_row: Any) -> bool:
    genres = _row_get(legacy_row, "genres")
    aliases = _row_get(legacy_row, "aliases")
    conn.execute(
        "UPDATE lib2_artists SET "
        "image_url=COALESCE(?, image_url), "
        "genres=COALESCE(?, genres), "
        "summary=COALESCE(?, summary), style=COALESCE(?, style), "
        "mood=COALESCE(?, mood), label=COALESCE(?, label), "
        "aliases=COALESCE(?, aliases), "
        "banner_url=COALESCE(?, banner_url), updated_at=CURRENT_TIMESTAMP "
        "WHERE id=?",
        (
            _row_get(legacy_row, "thumb_url"),
            _normalize_genres(genres) if genres else None,
            _row_get(legacy_row, "summary"),
            _row_get(legacy_row, "style"),
            _row_get(legacy_row, "mood"),
            _row_get(legacy_row, "label"),
            _normalize_genres(aliases) if aliases else None,
            _row_get(legacy_row, "banner_url"),
            lib2_artist_id,
        ),
    )
    # iss32-E01: the review asks for "artwork, genres, bios, provider ids".
    # The UPDATE above covers artwork and genres; the two below are the half
    # that was missing entirely — a mirrored artist kept lib2's stale provider
    # ids and never received a Last.fm/Genius/Discogs bio at all.
    _merge_json_column(conn, "lib2_artists", lib2_artist_id, "external_ids",
                       _provider_ids(legacy_row, "artist"))
    _merge_json_column(conn, "lib2_artists", lib2_artist_id, "enrichment",
                       _artist_enrichment(legacy_row))
    _sync_dedicated_id_columns(conn, "lib2_artists", lib2_artist_id, legacy_row,
                               {"spotify_id": ("spotify_artist_id",),
                                "musicbrainz_id": ("musicbrainz_id",)})
    return True


def _sync_dedicated_id_columns(conn, table: str, entity_id: int, legacy_row: Any,
                               mapping: Dict[str, tuple]) -> None:
    """Keep the promoted id columns in step with ``external_ids``.

    ``lib2_*`` stores Spotify/MusicBrainz twice: once as a first-class column
    (indexed, joined on) and once inside ``external_ids``. Mirroring only the
    JSON would leave the columns the read paths actually use behind.
    """
    for column, legacy_columns in mapping.items():
        value = _row_get(legacy_row, legacy_columns[0])
        for extra in legacy_columns[1:]:
            if value in (None, ""):
                value = _row_get(legacy_row, extra)
        if value in (None, ""):
            continue
        conn.execute(
            f"UPDATE {table} SET {column}=? WHERE id=? AND COALESCE({column},'')<>?",
            (str(value).strip(), entity_id, str(value).strip()))


def resync_album_from_legacy(conn, lib2_album_id: int, legacy_row: Any) -> bool:
    genres = _row_get(legacy_row, "genres")
    conn.execute(
        "UPDATE lib2_albums SET "
        "image_url=COALESCE(?, image_url), "
        "genres=COALESCE(?, genres), "
        "label=COALESCE(?, label), explicit=COALESCE(?, explicit), "
        "upc=COALESCE(?, upc), "
        "style=COALESCE(?, style), mood=COALESCE(?, mood), "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (
            _row_get(legacy_row, "thumb_url"),
            _normalize_genres(genres) if genres else None,
            _row_get(legacy_row, "label"),
            _row_get(legacy_row, "explicit"),
            _row_get(legacy_row, "upc"),
            _row_get(legacy_row, "style"),
            _row_get(legacy_row, "mood"),
            lib2_album_id,
        ),
    )
    _merge_json_column(conn, "lib2_albums", lib2_album_id, "external_ids",
                       _provider_ids(legacy_row, "album"))
    _sync_dedicated_id_columns(conn, "lib2_albums", lib2_album_id, legacy_row,
                               {"spotify_id": ("spotify_album_id",),
                                "musicbrainz_id": ("musicbrainz_release_id",)})
    return True


def resync_track_from_legacy(conn, lib2_track_id: int, legacy_row: Any) -> bool:
    conn.execute(
        "UPDATE lib2_tracks SET "
        "bpm=COALESCE(?, bpm), explicit=COALESCE(?, explicit), "
        "genius_lyrics=COALESCE(?, genius_lyrics), "
        "copyright=COALESCE(?, copyright), "
        "style=COALESCE(?, style), mood=COALESCE(?, mood), "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (
            _row_get(legacy_row, "bpm"),
            _row_get(legacy_row, "explicit"),
            _row_get(legacy_row, "genius_lyrics"),
            _row_get(legacy_row, "copyright"),
            _row_get(legacy_row, "style"),
            _row_get(legacy_row, "mood"),
            lib2_track_id,
        ),
    )
    _merge_json_column(conn, "lib2_tracks", lib2_track_id, "external_ids",
                       _provider_ids(legacy_row, "track"))
    _sync_dedicated_id_columns(conn, "lib2_tracks", lib2_track_id, legacy_row,
                               {"spotify_id": ("spotify_track_id",),
                                "musicbrainz_id": ("musicbrainz_recording_id",),
                                "isrc": ("isrc",)})
    return True


_RESYNC: Dict[str, tuple] = {
    "artist": ("artists", resync_artist_from_legacy),
    "album": ("albums", resync_album_from_legacy),
    "track": ("tracks", resync_track_from_legacy),
}


def resync_entity_from_legacy(conn, entity_type: str, lib2_id: int, legacy_id: Any) -> bool:
    """Re-read the legacy row and overwrite the lib2 row's provider fields.

    Returns False (no-op) if the legacy row is gone or ``entity_type`` is
    unrecognized — the caller's enrichment result is unaffected either way.
    """
    spec = _RESYNC.get(entity_type)
    if spec is None:
        return False
    legacy_table, fn = spec
    row = conn.execute(f"SELECT * FROM {legacy_table} WHERE id=?", (legacy_id,)).fetchone()
    if row is None:
        return False
    return fn(conn, lib2_id, row)


__all__ = [
    "resync_artist_from_legacy",
    "resync_album_from_legacy",
    "resync_track_from_legacy",
    "resync_entity_from_legacy",
]
